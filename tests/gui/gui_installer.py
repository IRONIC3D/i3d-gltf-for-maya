"""Inside GUI Maya (run by tests/gui/run_gui_tests.py): the drag-and-drop installer.

Session 1: calls Maya's own drop handler (maya.app.general.executeDroppedPythonFile) on
install_i3dGltf.py and answers every dialog like a user: Install, Maya's 'Untrusted Plugin
Loading' warning (Allow, or Deny with deny=true), auto-load Yes, OK. Then imports a file.
Session 2 (same MAYA_APP_DIR, Maya restarted with -noAutoloadPlugins): checks the auto-load
entry in pluginPrefs.mel and that loading the plug-in by name shows no security warning, imports a file, then drops the installed
uninstall_i3dGltf.py and checks that everything the installer created is gone.
"""

import json
import os
import sys
import tempfile
import traceback

import maya.cmds as cmds
import maya.utils

import gui_common as gc

QtCore, QtGui, QtWidgets, shiboken = gc.qt()
ARGS = json.loads(os.environ["I3D_GUI_ARGS"])
R = gc.RESULT
R["checks"] = {}
R["session"] = ARGS["session"]
seen = {}


def check(name, ok, detail=None):
    R["checks"][name] = {"ok": bool(ok), "detail": detail}


def answer():
    for w in gc.top_dialogs():
        try:
            title = w.windowTitle()
            texts = gc.widget_texts(w)
        except RuntimeError:
            continue
        key = (title, tuple(texts[:3]))
        if key not in seen:
            seen[key] = 0
            n = len(R["dialogs"])
            R["dialogs"].append({"title": title, "texts": texts, "shot": gc.grab_widget(w, "dialog_%02d" % n),
                                 "during": R["steps"][-1]["step"] if R["steps"] else None})
        seen[key] += 1
        if seen[key] < 2:
            continue  # let it paint once for the screenshot
        security = "security" in title.lower() or any("non-trusted" in t for t in texts)
        prefs = (["Deny"] if ARGS.get("deny") else ["Allow"]) if security else \
            ["Install", "Yes", "Uninstall", "OK"]
        for p in prefs:
            for b in w.findChildren(QtWidgets.QAbstractButton):
                if b.isVisible() and b.text().replace("&", "").strip() == p:
                    R["dialogs"][-1].setdefault("clicked", p)
                    b.click()
                    return


timer = QtCore.QTimer()
timer.timeout.connect(answer)
timer.start(500)
R["timerObj"] = str(timer)


def state():
    app = cmds.internalVar(userAppDir=True)
    mods = os.path.join(app, "modules")
    s = {"appDir": app, "modulesDir": sorted(os.listdir(mods)) if os.path.isdir(mods) else None,
         "moduleListed": "i3dGltf" in (cmds.moduleInfo(listModules=True) or []),
         "loaded": cmds.pluginInfo("i3dGltf", q=True, loaded=True),
         "allowList": list(cmds.optionVar(q="SafeModeAllowedlistPaths") or [])}
    if s["moduleListed"]:
        s["modulePath"] = cmds.moduleInfo(path=True, moduleName="i3dGltf")
    if s["loaded"]:
        s["version"] = cmds.pluginInfo("i3dGltf", q=True, version=True)
        s["path"] = cmds.pluginInfo("i3dGltf", q=True, path=True)
        s["autoload"] = cmds.pluginInfo("i3dGltf", q=True, autoload=True)
    mod = os.path.join(mods, "i3dGltf.mod")
    if os.path.exists(mod):
        with open(mod, encoding="utf-8") as f:
            s["modLine"] = f.read()
    return s


def import_fixture(ns):
    sys.path.insert(0, os.path.join(os.environ["I3D_GUI_REPO"], "tests"))
    import fixtures
    d = tempfile.mkdtemp(prefix="gui_fixture_")
    fixtures.write_scene(d)
    cmds.file(os.path.join(d, "scene.gltf"), i=True, type="IRONIC3D glTF", namespace=ns, options="shader=none")
    from i3d_gltf.maya import importer
    rep = importer.last_report()
    return bool(rep and rep["ok"]), (rep or {}).get("roots")


def drop(path):
    from maya.app.general.executeDroppedPythonFile import executeDroppedPythonFile
    return executeDroppedPythonFile(path, "")


def main():
    try:
        before = state()
        R["before"] = before
        if ARGS["session"] == 1:
            gc.step("drop installer")
            drop(ARGS["installer"])
            after = state()
            R["after"] = after
            security = [d for d in R["dialogs"] if "security" in d["title"].lower()]
            check("security warning shown once", len(security) == 1, [d["title"] for d in R["dialogs"]])
            if ARGS.get("deny"):
                check("not loaded after Deny", not after["loaded"])
                check("files installed anyway", after.get("modLine", "").startswith("+ i3dGltf "))
            else:
                check("loaded in this session", after["loaded"], after)
                check("auto-load on", after.get("autoload"))
                check("allow-list unchanged", after["allowList"] == before["allowList"], after["allowList"])
                gc.step("import")
                ok, roots = import_fixture("g1")
                check("import works", ok, roots)
        else:
            gc.step("startup state")
            check("module found at start-up", before["moduleListed"], before.get("modulePath"))
            check("no security warning at start-up", not R["dialogs"], R["dialogs"])
            prefs = os.path.join(before["appDir"], cmds.about(version=True), "prefs", "pluginPrefs.mel")
            text = open(prefs, encoding="utf-8", errors="replace").read() if os.path.exists(prefs) else ""
            R["pluginPrefs"] = [l.strip() for l in text.splitlines() if "i3dGltf" in l]
            if not ARGS.get("deny"):
                check("auto-load recorded in pluginPrefs.mel", R["pluginPrefs"], R["pluginPrefs"])
            gc.step("load by name (as auto-load does)")
            try:
                cmds.loadPlugin("i3dGltf")
            except RuntimeError as exc:
                R["loadError"] = str(exc)
            check("loads by name after restart", cmds.pluginInfo("i3dGltf", q=True, loaded=True))
            gc.step("import")
            ok, roots = import_fixture("g2")
            check("import works", ok, roots)
            check("no security warning while loading", not [d for d in R["dialogs"]
                                                             if "security" in d["title"].lower()], R["dialogs"])
            gc.step("drop uninstaller")
            target = os.path.join(before["appDir"], "modules", "i3dGltf")
            drop(os.path.join(target, "uninstall_i3dGltf.py"))
            after = state()
            R["after"] = after
            check("uninstalled", after["modulesDir"] == [] and not after["loaded"], after)
    except Exception:
        check("script", False, traceback.format_exc())
    R["ok"] = all(c["ok"] for c in R["checks"].values())
    gc.finish()


maya.utils.executeDeferred(main)
