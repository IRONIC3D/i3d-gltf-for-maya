"""Import options: one table of known keys with types and defaults.

The translator receives an options string of the form ``key=value;key=value``.
Maya prepends the translator's default options string to whatever the caller passes,
so a key may appear several times: the last occurrence wins.

To add an option in a later milestone, add an ``OptionSpec`` to ``IMPORT_OPTION_SPECS``.
An options UI (optionsScriptName) reads/writes the same string via ``format_options``.
"""

from .diagnostics import Reporter


class OptionSpec(object):
    __slots__ = ("name", "kind", "default", "choices", "minimum", "doc")

    def __init__(self, name, kind, default, choices=None, minimum=None, doc=""):
        self.name = name
        self.kind = kind  # "bool" | "float" | "int" | "str" | "enum"
        self.default = default
        self.choices = choices
        self.minimum = minimum
        self.doc = doc


IMPORT_OPTION_SPECS = (
    OptionSpec("scale", "float", 1.0, minimum=0.0,
               doc="Extra scale multiplier. glTF metres are always converted to Maya centimetres (x100)."),
    OptionSpec("importNormals", "bool", True,
               doc="Import NORMAL as locked user normals."),
    OptionSpec("importColors", "bool", True,
               doc="Import COLOR_n as colour sets."),
    OptionSpec("shader", "enum", "aiStandardSurface",
               choices=("aiStandardSurface", "aiOpenPBRSurface", "openPBRSurface", "standardSurface", "none"),
               doc="Shader node type for glTF materials ('none': everything on initialShadingGroup). "
                   "An unavailable type falls back (see maya/shading.py) with a warning."),
    OptionSpec("textureDir", "str", "",
               doc="Folder for textures extracted from GLB / data URIs (a per-file subfolder is added). "
                   "Default: <project>/sourceimages/i3dGltf."),
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
