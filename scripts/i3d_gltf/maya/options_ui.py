"""The import options window shown by Maya's file dialogs (File > Import / Create Reference /
Open, option box and the options pane of the file browser).

Wiring: the translator is registered with the options script ``i3dGltfImportOptions``, a
MEL procedure (MEL_SOURCE, defined when the plug-in loads) that Maya's file dialog calls as

    i3dGltfImportOptions <parent layout> "post"  <initialSettings> <resultCallback>
    i3dGltfImportOptions <parent layout> "query" ""                <resultCallback>

"post" builds the controls under the parent (post()), "query" reads them back and passes
the ``key=value;...`` string to the callback (query()). Maya itself stores the queried
string in the optionVar "IRONIC3D glTFOptions" and passes it back as initialSettings next
time, which is how the last used settings are remembered (as for Maya's own translators).

The controls are generated from core.options.IMPORT_OPTION_SPECS (group, label, help,
widget, presets); the string is read and written with core.options.dialog_values /
dialog_string, the same functions the tests use. Built only with maya.cmds, so the same
code runs on every Maya version (PySide2 on 2024, PySide6 on 2025+).
"""

import maya.cmds as cmds

from ..core.options import (IMPORT_OPTION_SPECS, OPTION_GROUPS, clean_text_value, dialog_string,
                            dialog_values)
from . import shading

MEL_PROC = "i3dGltfImportOptions"

MEL_SOURCE = r'''
global proc int i3dGltfImportOptions(string $parent, string $action, string $initialSettings,
                                     string $resultCallback)
{
    if ($action == "post") {
        python("import i3d_gltf.maya.options_ui");
        python("i3d_gltf.maya.options_ui.post(\"" + encodeString($parent) + "\", \""
               + encodeString($initialSettings) + "\")");
        return 1;
    }
    if ($action == "query") {
        python("import i3d_gltf.maya.options_ui");
        string $options = python("i3d_gltf.maya.options_ui.query()");
        eval($resultCallback + " \"" + encodeString($options) + "\"");
        return 1;
    }
    return 0;
}
'''

RENDERER = {"aiStandardSurface": "Arnold", "aiOpenPBRSurface": "Arnold", "standardSurface": "Maya",
            "openPBRSurface": "Maya"}

LABEL_WIDTH = 110
FIELD_WIDTH = 130  # minimum; the field column stretches with the pane (the file browser pane is narrow)
BROWSE_ICON = "navButtonBrowse.png"

#: controls of the most recent post(): {option name: (kind, control)}, plus the shader menu
#: values in menu order and the settings that were posted.
_state = {"controls": {}, "shader_values": [], "posted": ""}


def define_mel_procedure():
    """Define the MEL options procedure (called when the plug-in loads)."""
    import maya.mel as mel
    mel.eval(MEL_SOURCE)


# --- pure helpers (tested without a UI) -------------------------------------------------

def shader_label(value, status, fallback):
    """Menu label for one shader value and its shading.shader_availability() status."""
    if value == "none":
        return "none (no materials)"
    renderer = RENDERER.get(value, "")
    if status == shading.AVAILABLE:
        return "%s (%s)" % (value, renderer)
    if status == shading.ARNOLD_ON_IMPORT:
        return "%s (Arnold, loaded on import)" % value
    return "%s - not available, uses %s" % (value, fallback or "standardSurface")


def shader_menu(choices, availability=None):
    """[(value, label), ...] for the shader menu. Every value is listed, including the
    ones this session cannot build (the import falls back with a warning)."""
    availability = availability if availability is not None else shading.shader_availability(choices)
    return [(v, shader_label(v, status, fallback)) for v, status, fallback in availability]


def grouped_specs(specs=IMPORT_OPTION_SPECS):
    """[(group, [spec, ...]), ...] in display order; options without a known group last."""
    out = []
    for g in OPTION_GROUPS:
        members = [s for s in specs if s.group == g]
        if members:
            out.append((g, members))
    rest = [s for s in specs if s.group not in OPTION_GROUPS]
    if rest:
        out.append(("Other", rest))
    return out


# --- UI ----------------------------------------------------------------------------------

def _row(spec, columns, widths):
    row = cmds.rowLayout(numberOfColumns=columns, columnWidth=list(enumerate(widths, 1)),
                         adjustableColumn=2, columnAttach=[(1, "right", 4)] + [(i, "left", 2)
                                                                              for i in range(2, columns + 1)])
    cmds.text(label="" if spec.kind == "bool" else spec.label, align="right", width=widths[0],
              annotation=spec.help)
    return row


def _browse(field):
    current = cmds.textField(field, q=True, text=True)
    start = current or cmds.workspace(q=True, rootDirectory=True)
    picked = cmds.fileDialog2(fileMode=3, caption="Texture folder", okCaption="Select",
                              startingDirectory=start)
    if picked:
        cmds.textField(field, e=True, text=clean_text_value(picked[0]))


def _set_text(field, value, *_):
    cmds.textField(field, e=True, text=value)


def build(parent, values):
    """Build the controls under ``parent`` showing ``values`` (dict from dialog_values).
    Returns the top layout."""
    controls = {}
    cmds.setParent(parent)
    top = cmds.columnLayout(adjustableColumn=True, rowSpacing=4)
    for group, specs in grouped_specs():
        cmds.setParent(top)
        cmds.frameLayout(label=group, collapsable=True, collapse=False, marginWidth=4, marginHeight=4)
        cmds.columnLayout(adjustableColumn=True, rowSpacing=3)
        for spec in specs:
            value = values.get(spec.name, spec.default)
            if spec.kind == "bool":
                _row(spec, 2, (LABEL_WIDTH, FIELD_WIDTH))
                ctrl = cmds.checkBox(label=spec.label, value=bool(value), annotation=spec.help)
            elif spec.kind == "float":
                _row(spec, 2, (LABEL_WIDTH, 90))
                ctrl = cmds.floatField(value=float(value), precision=4, minValue=1e-6, width=90,
                                       annotation=spec.help)
            elif spec.kind == "int":
                _row(spec, 2, (LABEL_WIDTH, 90))
                ctrl = cmds.intField(value=int(value), width=90, annotation=spec.help)
            elif spec.kind == "enum":
                _row(spec, 2, (LABEL_WIDTH, FIELD_WIDTH))
                ctrl = cmds.optionMenu(annotation=spec.help)
                if spec.name == "shader":
                    items = shader_menu(spec.choices)
                    _state["shader_values"] = [v for v, _ in items]
                else:
                    items = [(c, c) for c in spec.choices]
                for _, label in items:
                    cmds.menuItem(label=label)
                cmds.optionMenu(ctrl, e=True, select=[v for v, _ in items].index(value) + 1)
            elif spec.widget == "folder":
                _row(spec, 3, (LABEL_WIDTH, FIELD_WIDTH, 26))
                ctrl = cmds.textField(text=str(value), annotation=spec.help,
                                      placeholderText="<project>/sourceimages/i3dGltf")
                cmds.symbolButton(image=BROWSE_ICON, annotation="Choose a folder",
                                  command=lambda *_a, f=ctrl: _browse(f))
            elif spec.presets:
                _row(spec, 3, (LABEL_WIDTH, FIELD_WIDTH, 26))
                ctrl = cmds.textField(text=str(value), annotation=spec.help)
                button = cmds.button(label="...", width=26, annotation="Common values")
                cmds.popupMenu(parent=button, button=1)
                for preset, description in spec.presets:
                    cmds.menuItem(label="%s  (%s)" % (preset, description),
                                  command=lambda *_a, f=ctrl, p=preset: _set_text(f, p))
            else:
                _row(spec, 2, (LABEL_WIDTH, FIELD_WIDTH))
                ctrl = cmds.textField(text=str(value), annotation=spec.help)
            controls[spec.name] = (spec, ctrl)
            cmds.setParent("..")  # out of the row
    cmds.setParent(top)
    _state["controls"] = controls
    return top


def read():
    """Current values of the controls of the last build() (None when they no longer exist)."""
    controls = _state["controls"]
    if not controls or not all(cmds.control(c, exists=True) for _, c in controls.values()):
        return None
    values = {}
    for name, (spec, ctrl) in controls.items():
        if spec.kind == "bool":
            values[name] = cmds.checkBox(ctrl, q=True, value=True)
        elif spec.kind == "float":
            values[name] = cmds.floatField(ctrl, q=True, value=True)
        elif spec.kind == "int":
            values[name] = cmds.intField(ctrl, q=True, value=True)
        elif spec.kind == "enum":
            index = cmds.optionMenu(ctrl, q=True, select=True) - 1
            choices = _state["shader_values"] if name == "shader" else list(spec.choices)
            values[name] = choices[index]
        else:
            values[name] = cmds.textField(ctrl, q=True, text=True)
    return values


def post(parent, initial_settings):
    """'post' action: build the controls from the initialSettings string."""
    _state["posted"] = initial_settings or ""
    build(parent, dialog_values(initial_settings))


def query():
    """'query' action: the options string for the current controls (or, if they are gone,
    for the settings that were posted)."""
    values = read()
    if values is None:
        values = dialog_values(_state["posted"])
    return dialog_string(values)
