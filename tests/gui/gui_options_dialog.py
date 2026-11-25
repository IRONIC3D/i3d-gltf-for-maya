"""Inside GUI Maya (run by tests/gui/run_gui_tests.py): the import options window.

1. Loads the plug-in (module on MAYA_MODULE_PATH).
2. Posts the options UI into a test window through the MEL procedure exactly as Maya's
   file dialog does, reads it back with "query", edits controls and reads again.
3. Opens Maya's own File > Import / Create Reference / Open option boxes with our file type,
   photographs them, changes a value, presses Save and checks that Maya stored the string
   and shows it again when the box is reopened (remembered settings).
4. Opens the real File > Import file browser (fileDialog2, modal), switches its file type to
   ours and photographs the options pane, then cancels it.
Screenshots are of Maya's own windows only.
"""

import os
import traceback

import maya.cmds as cmds
import maya.mel as mel
import maya.utils

import gui_common as gc

QtCore, QtGui, QtWidgets, shiboken = gc.qt()
T = "IRONIC3D glTF"
R = gc.RESULT
R["checks"] = {}


def check(name, ok, detail=None):
    R["checks"][name] = {"ok": bool(ok), "detail": detail}
    return ok


def find_window(title_part):
    for w in QtWidgets.QApplication.topLevelWidgets():
        try:
            if w.isVisible() and title_part.lower() in w.windowTitle().lower():
                return w
        except RuntimeError:
            pass
    return None


def click_button(window, text):
    for b in window.findChildren(QtWidgets.QAbstractButton):
        if b.isVisible() and b.text().replace("&", "").strip().lower() == text.lower():
            b.click()
            return True
    return False


def show_ours(window):
    """Scroll every scroll area of a Maya window to the bottom, where the translator options are."""
    for sa in window.findChildren(QtWidgets.QScrollArea):
        bar = sa.verticalScrollBar()
        bar.setValue(bar.maximum())


def process(seconds=0.5):
    app = QtWidgets.QApplication.instance()
    end = QtCore.QTime.currentTime().addMSecs(int(seconds * 1000))
    while QtCore.QTime.currentTime() < end:
        app.processEvents(QtCore.QEventLoop.AllEvents, 50)


def step_load():
    cmds.loadPlugin("i3dGltf")
    import i3d_gltf
    check("plugin loaded", cmds.pluginInfo("i3dGltf", q=True, loaded=True), i3d_gltf.VERSION)
    check("options script registered", mel.eval('translator -q -optionsScript "%s"' % T) == "i3dGltfImportOptions")
    R["maya"] = cmds.about(version=True)
    R["qt"] = QtCore.qVersion()


def step_test_window():
    from i3d_gltf.core.options import dialog_string, dialog_values
    from i3d_gltf.maya import options_ui
    mel.eval('global string $i3dGuiOpts; global proc i3dGuiCallback(string $o) '
             '{ global string $i3dGuiOpts; $i3dGuiOpts = $o; }')
    if cmds.window("i3dOptTest", exists=True):
        cmds.deleteUI("i3dOptTest")
    win = cmds.window("i3dOptTest", title="i3dGltf options test", widthHeight=(420, 460))
    cmds.scrollLayout(childResizable=True)
    parent = cmds.columnLayout("i3dOptTestParent", adjustableColumn=True)
    R["mtoaBefore"] = cmds.pluginInfo("mtoa", q=True, loaded=True)
    settings = ("scale=0.0254;importNormals=0;importColors=1;shader=openPBRSurface;textureDir=D:/tex dir/é;"
                "animation=Walk Cycle;animSampling=bake;timeRange=0;futureKey=7")
    mel.eval('i3dGltfImportOptions "%s" "post" "%s" "i3dGuiCallback"' % (parent, settings))
    cmds.showWindow(win)
    process(1.0)
    gc.grab_control("test_window", win)
    mel.eval('i3dGltfImportOptions "%s" "query" "" "i3dGuiCallback"' % parent)
    got = mel.eval("$tmp = $i3dGuiOpts")
    expected = dialog_string(dialog_values(settings))
    check("post then query round-trips", got == expected, {"got": got, "expected": expected})
    controls = options_ui._state["controls"]
    labels = {}
    for name, (spec, ctrl) in controls.items():
        labels[name] = spec.label
    check("every option has a control", sorted(controls) == sorted(dialog_values("")), sorted(controls))
    shader_ctrl = controls["shader"][1]
    items = cmds.optionMenu(shader_ctrl, q=True, itemListLong=True) or []
    menu_labels = [cmds.menuItem(i, q=True, label=True) for i in items]
    R["shaderMenu"] = menu_labels
    check("shader menu lists all types", len(menu_labels) == 5, menu_labels)
    check("mtoa not loaded by the dialog", cmds.pluginInfo("mtoa", q=True, loaded=True) == R["mtoaBefore"],
          R["mtoaBefore"])
    annotations = {n: cmds.control(c, q=True, annotation=True) for n, (s, c) in controls.items()}
    check("annotations", all(annotations.values()), annotations)
    # edit like a user and read back
    cmds.floatField(controls["scale"][1], e=True, value=2.5)
    cmds.checkBox(controls["importNormals"][1], e=True, value=True)
    cmds.optionMenu(shader_ctrl, e=True, select=4)  # standardSurface
    cmds.textField(controls["textureDir"][1], e=True, text="C:\\tex;dir")
    cmds.textField(controls["animation"][1], e=True, text="none")
    mel.eval('i3dGltfImportOptions "%s" "query" "" "i3dGuiCallback"' % parent)
    got = mel.eval("$tmp = $i3dGuiOpts")
    expected = ("scale=2.5;importNormals=1;importColors=1;shader=standardSurface;textureDir=C:/texdir;"
                "animation=none;animSampling=bake;timeRange=0")
    check("edited controls read back", got == expected, got)
    cmds.deleteUI(win)


def option_box(action, type_var):
    from i3d_gltf.maya import options_ui
    cmds.optionVar(stringValue=(type_var, T))
    mel.eval('fileOptions "%s" "projectViewer %s"' % (action, action))
    process(1.5)
    title = {"Import": "Import Options", "Reference": "Reference", "Open": "Open Options"}[action]
    win = find_window(title)
    shot = None
    if win is not None:
        win.resize(win.width(), 980)
        process(0.5)
        show_ours(win)
        process(0.3)
        shot = gc.grab_widget(win, "optionbox_%s" % action.lower())
    controls = options_ui._state["controls"]
    alive = bool(controls) and all(cmds.control(c, exists=True) for _, c in controls.values())
    check("option box %s shows our controls" % action, alive and win is not None,
          {"window": win.windowTitle() if win else None, "shot": shot})
    return win


def step_option_boxes():
    from i3d_gltf.maya import options_ui
    stored_before = cmds.optionVar(q=T + "Options") if cmds.optionVar(exists=T + "Options") else None
    R["storedBefore"] = stored_before
    win = option_box("Import", "defaultFileImportType")
    if win is None:
        return
    controls = options_ui._state["controls"]
    cmds.floatField(controls["scale"][1], e=True, value=3.25)
    cmds.textField(controls["animation"][1], e=True, text="1")
    pressed = click_button(win, "Save and Close")
    process(1.0)
    stored = cmds.optionVar(q=T + "Options")
    check("Save stores our string in the optionVar", pressed and "scale=3.25" in stored and "animation=1" in stored,
          stored)
    if find_window("Import Options"):
        click_button(find_window("Import Options"), "Close") or click_button(find_window("Import Options"),
                                                                             "Cancel")
    process(0.5)
    win = option_box("Import", "defaultFileImportType")
    if win is not None:
        controls = options_ui._state["controls"]
        check("reopened box remembers settings",
              abs(cmds.floatField(controls["scale"][1], q=True, value=True) - 3.25) < 1e-9
              and cmds.textField(controls["animation"][1], q=True, text=True) == "1")
        click_button(win, "Close") or click_button(win, "Cancel")
    process(0.5)
    for action, var in (("Reference", "defaultFileReferenceType"), ("Open", "defaultFileOpenType")):
        try:
            w = option_box(action, var)
            if w is not None:
                click_button(w, "Close") or click_button(w, "Cancel")
            process(0.5)
        except Exception:
            check("option box %s" % action, False, traceback.format_exc())


def step_file_browser():
    """The real File > Import file browser with its options pane (modal)."""
    state = {"tries": 0}

    def poke():
        state["tries"] += 1
        dlg = QtWidgets.QApplication.activeModalWidget()
        if dlg is None:
            if state["tries"] < 40:
                QtCore.QTimer.singleShot(250, poke)
            return
        try:
            combos = [c for c in dlg.findChildren(QtWidgets.QComboBox) if c.isVisible()]
            chosen = None
            for c in combos:
                for i in range(c.count()):
                    if c.itemText(i).startswith(T):
                        c.setCurrentIndex(i)
                        chosen = c.itemText(i)
            R["fileBrowserType"] = chosen
            R["fileBrowserTitle"] = dlg.windowTitle()
            # make sure the options pane is shown
            for b in dlg.findChildren(QtWidgets.QAbstractButton):
                if b.isVisible() and b.text().replace("&", "").strip().lower() in ("options", "show options"):
                    if not (b.isCheckable() and b.isChecked()):
                        b.click()

            for sp in dlg.findChildren(QtWidgets.QSplitter):
                sizes = sp.sizes()
                if len(sizes) >= 2 and sp.orientation() == QtCore.Qt.Horizontal:
                    sp.setSizes(sizes[:-2] + [max(sizes[-2] - 200, 100), sizes[-1] + 200])
            dlg.resize(max(dlg.width(), 1250), max(dlg.height(), 850))

            def shoot():
                try:
                    show_ours(dlg)
                    process(0.3)
                    gc.grab_widget(dlg, "file_browser_import")
                    texts = gc.widget_texts(dlg)
                    R["fileBrowserTexts"] = texts
                    check("file browser shows our options pane", chosen is not None and any(
                        "Texture folder" in t or "Scale" in t for t in texts), chosen)
                finally:
                    dlg.reject()
            QtCore.QTimer.singleShot(2500, shoot)
        except Exception:
            R["fileBrowserError"] = traceback.format_exc()
            dlg.reject()

    QtCore.QTimer.singleShot(500, poke)
    cmds.optionVar(stringValue=("defaultFileImportType", T))
    try:
        mel.eval("Import")
    except Exception:
        R["fileBrowserError"] = traceback.format_exc()


def main():
    for name, fn in (("load", step_load), ("test window", step_test_window), ("option boxes", step_option_boxes),
                     ("file browser", step_file_browser)):
        gc.step(name)
        try:
            fn()
        except Exception:
            check(name, False, traceback.format_exc())
    R["ok"] = all(c["ok"] for c in R["checks"].values())
    gc.finish()


maya.utils.executeDeferred(main)
