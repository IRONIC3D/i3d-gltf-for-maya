"""Import options: one table of known keys with types and defaults.

The translator receives an options string of the form ``key=value;key=value``.
Maya prepends the translator's default options string to whatever the caller passes,
so a key may appear several times: the last occurrence wins.

To add an option, add an ``OptionSpec`` to ``IMPORT_OPTION_SPECS``. The import options
window (maya/options_ui.py) is generated from this table: ``group``, ``label`` and ``help``
are its section, label and one-line annotation, ``widget`` picks a special control, and
``presets`` are the common values offered for a free-text option. The window reads and
writes the same ``key=value`` string through ``dialog_values`` / ``dialog_string``.
"""

from .diagnostics import Reporter

#: Sections of the options window, in display order.
OPTION_GROUPS = ("Geometry", "Materials", "Animation")


class OptionSpec(object):
    __slots__ = ("name", "kind", "default", "choices", "minimum", "doc", "group", "label", "help", "widget",
                 "presets")

    def __init__(self, name, kind, default, choices=None, minimum=None, doc="", group="", label="", help="",
                 widget=None, presets=None):
        self.name = name
        self.kind = kind  # "bool" | "float" | "int" | "str" | "enum"
        self.default = default
        self.choices = choices
        self.minimum = minimum
        self.doc = doc
        self.group = group      # one of OPTION_GROUPS
        self.label = label or name
        self.help = help        # one line, shown as the control's annotation (tooltip)
        self.widget = widget    # None (by kind) | "folder" (text field + browse button)
        self.presets = presets  # (value, description) pairs offered for a free-text option


IMPORT_OPTION_SPECS = (
    OptionSpec("scale", "float", 1.0, minimum=0.0, group="Geometry", label="Scale",
               help="Size multiplier. glTF metres always become Maya centimetres; 1.0 keeps real-world size.",
               doc="Extra scale multiplier. glTF metres are always converted to Maya centimetres (x100)."),
    OptionSpec("importNormals", "bool", True, group="Geometry", label="Import normals",
               help="Use the file's normals as locked normals (off: Maya computes them).",
               doc="Import NORMAL as locked user normals."),
    OptionSpec("importColors", "bool", True, group="Geometry", label="Import vertex colours",
               help="Import COLOR_n attributes as colour sets.",
               doc="Import COLOR_n as colour sets."),
    OptionSpec("shader", "enum", "aiStandardSurface",
               choices=("aiStandardSurface", "aiOpenPBRSurface", "openPBRSurface", "standardSurface", "PxrSurface",
                        "dlPrincipled", "none"),
               group="Materials", label="Shader",
               help="Shader type built for glTF materials. An unavailable type falls back, with a warning.",
               doc="Shader node type for glTF materials ('none': everything on initialShadingGroup). "
                   "An unavailable type falls back (see maya/shading.py) with a warning."),
    OptionSpec("textureDir", "str", "", group="Materials", label="Texture folder", widget="folder",
               help="Where images embedded in the file are written. Empty: <project>/sourceimages/i3dGltf.",
               doc="Folder for textures extracted from GLB / data URIs (a per-file subfolder is added). "
                   "Default: <project>/sourceimages/i3dGltf."),
    OptionSpec("animation", "str", "first", group="Animation", label="Animation",
               presets=(("first", "the file's first animation"), ("none", "no animation")),
               help="Which animation to import: first, none, an index (0, 1, ...) or an animation name.",
               doc="Which glTF animation to import: 'first', 'none', an index (0, 1, ...) or an animation name. "
                   "An index or name that does not exist gives a warning and imports the first."),
    OptionSpec("animSampling", "enum", "keys", choices=("keys", "bake"), group="Animation", label="Sampling",
               help="keys: keys where the file has them. bake: one key per frame (exact for every curve).",
               doc="'keys': keys exactly where the file has them. 'bake': one key per frame at the scene "
                   "frame rate (for curves Maya cannot represent exactly, e.g. CUBICSPLINE rotations)."),
    OptionSpec("timeRange", "bool", True, group="Animation", label="Set time range",
               help="Set the playback range to the imported animation (imports only, never references).",
               doc="Set the playback range to the imported animation on import (never for references)."),
)

_TRUE = ("1", "true", "yes", "on")
_FALSE = ("0", "false", "no", "off")


class ImportOptions(object):
    """Typed, attribute-style access to option values (``opts.scale``)."""

    def __init__(self, values=None, specs=IMPORT_OPTION_SPECS):
        self._specs = dict((s.name, s) for s in specs)
        self._values = dict((s.name, s.default) for s in specs)
        if values:
            for k, v in values.items():
                if k not in self._specs:
                    raise KeyError("Unknown import option '%s'" % k)
                self._values[k] = v

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        try:
            return self._values[name]
        except KeyError:
            raise AttributeError(name)

    def get(self, name, default=None):
        return self._values.get(name, default)

    def as_dict(self):
        return dict(self._values)

    def __repr__(self):
        return "ImportOptions(%s)" % format_options(self)


def _convert(spec, raw):
    raw = raw.strip()
    if spec.kind == "bool":
        low = raw.lower()
        if low in _TRUE:
            return True
        if low in _FALSE:
            return False
        raise ValueError("expected a boolean (1/0/true/false)")
    if spec.kind == "float":
        value = float(raw)
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("expected a finite number")
        if spec.minimum is not None and value <= spec.minimum:
            raise ValueError("must be greater than %s" % spec.minimum)
        return value
    if spec.kind == "int":
        value = int(raw)
        if spec.minimum is not None and value < spec.minimum:
            raise ValueError("must be at least %s" % spec.minimum)
        return value
    if spec.kind == "enum":
        if raw not in spec.choices:
            raise ValueError("expected one of %s" % ", ".join(spec.choices))
        return raw
    return raw


def parse_options(text, reporter=None, specs=IMPORT_OPTION_SPECS):
    """Parse ``key=value;key=value`` into ImportOptions. Never raises on bad input:
    unknown keys and bad values produce a warning and fall back to the default."""
    reporter = reporter or Reporter()
    by_name = dict((s.name, s) for s in specs)
    values = {}
    for item in (text or "").split(";"):
        item = item.strip()
        if not item:
            continue
        if "=" not in item:
            reporter.warning("Ignoring malformed option '%s' (expected key=value)" % item)
            continue
        key, raw = item.split("=", 1)
        key = key.strip()
        spec = by_name.get(key)
        if spec is None:
            reporter.warning("Ignoring unknown option '%s'" % key)
            continue
        try:
            values[key] = _convert(spec, raw)
        except ValueError as exc:
            reporter.warning("Option '%s=%s' is invalid (%s); using default %r"
                             % (key, raw.strip(), exc, spec.default))
            values.pop(key, None)
    return ImportOptions(values, specs)


def format_options(options, specs=IMPORT_OPTION_SPECS):
    """Inverse of parse_options (used for the translator default options string)."""
    parts = []
    for spec in specs:
        value = options.get(spec.name, spec.default) if options is not None else spec.default
        if spec.kind == "bool":
            value = 1 if value else 0
        parts.append("%s=%s" % (spec.name, value))
    return ";".join(parts)


def default_options_string(specs=IMPORT_OPTION_SPECS):
    return format_options(None, specs)


def dialog_values(text, specs=IMPORT_OPTION_SPECS):
    """Values the options window shows for an ``initialSettings`` string: every option,
    typed, with defaults for keys that are missing or invalid. Unknown keys (for example
    from a newer version) are ignored silently."""
    return parse_options(text, Reporter(), specs).as_dict()


def clean_text_value(value):
    """A free-text value as it can travel in the options string: stripped, without ';'
    (the separator), and with forward slashes (Maya's file dialog splices the string into
    MEL source unescaped, where backslashes would be read as escapes)."""
    return (value or "").replace(";", "").replace("\\", "/").strip()


def dialog_string(values, specs=IMPORT_OPTION_SPECS):
    """The ``key=value;...`` string the options window posts back for ``values`` (a dict
    as returned by dialog_values, possibly edited). Invalid values become the default, so
    the result always parses without warnings."""
    clean = {}
    for spec in specs:
        value = values.get(spec.name, spec.default)
        if spec.kind == "str":
            value = clean_text_value(value)
        clean[spec.name] = value
    text = format_options(clean, specs)
    # round-trip through the parser: anything invalid falls back to the default
    return format_options(dialog_values(text, specs), specs)
