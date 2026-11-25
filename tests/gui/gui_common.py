"""Helpers for scripts that run inside GUI Maya under tests/gui/run_gui.py.

Qt is used here only to look at and photograph the UI (PySide2 on Maya 2024, PySide6 on
2025+); the plug-in itself builds its UI with maya.cmds.
"""

import json
import os
import sys
import threading
import time
import traceback

RESULT = {"ok": False, "steps": [], "dialogs": []}


def qt():
    try:
        from PySide6 import QtCore, QtGui, QtWidgets
        import shiboken6 as shiboken
    except ImportError:
        from PySide2 import QtCore, QtGui, QtWidgets
        import shiboken2 as shiboken
    return QtCore, QtGui, QtWidgets, shiboken


def hard_quit(code=0):
    sys.stdout.flush()
    import ctypes
    k = ctypes.WinDLL("kernel32")
    k.TerminateProcess(ctypes.c_void_p(k.GetCurrentProcess()), code)


def write_result():
    with open(os.environ["I3D_GUI_OUT"] + ".tmp", "w", encoding="utf-8") as f:
        json.dump(RESULT, f, indent=1, ensure_ascii=False, default=str)
    os.replace(os.environ["I3D_GUI_OUT"] + ".tmp", os.environ["I3D_GUI_OUT"])


def finish(ok=None):
    if ok is not None:
        RESULT["ok"] = bool(ok)
    write_result()
    hard_quit(0)


def step(name, **data):
    data["step"] = name
    data["t"] = round(time.time(), 2)
    RESULT["steps"].append(data)
    # progress trail that survives a blocked or killed Maya
    with open(os.environ["I3D_GUI_OUT"] + ".steps", "a", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False, default=str) + "\n")
    return data


def shot_path(name):
    return os.path.join(os.environ["I3D_GUI_SHOTS"], name + ".png")


def grab_widget(widget, name):
    path = shot_path(name)
    pix = widget.grab()
    pix.save(path)
    return path


def grab_main_window(name):
    """Screenshot of Maya's own main window only (never the desktop)."""
    import maya.OpenMayaUI as omui
    _, _, QtWidgets, shiboken = qt()
    ptr = omui.MQtUtil.mainWindow()
    return grab_widget(shiboken.wrapInstance(int(ptr), QtWidgets.QWidget), name)


def grab_control(name, control):
    """Screenshot of one Maya UI element (window or layout) by its Maya name."""
    import maya.OpenMayaUI as omui
    _, _, QtWidgets, shiboken = qt()
    ptr = omui.MQtUtil.findWindow(control) or omui.MQtUtil.findLayout(control) or \
        omui.MQtUtil.findControl(control)
    if not ptr:
        return None
    w = shiboken.wrapInstance(int(ptr), QtWidgets.QWidget)
    return grab_widget(w.window() if w.window() is not None else w, name)


def widget_texts(w):
    """Visible labels/buttons of a widget (to identify dialogs)."""
    _, _, QtWidgets, _ = qt()
    out = []
    for cls in (QtWidgets.QLabel, QtWidgets.QAbstractButton):
        for c in w.findChildren(cls):
            try:
                if c.isVisible() and c.text():
                    out.append(c.text())
            except RuntimeError:
                pass
    return out


def top_dialogs():
    _, _, QtWidgets, _ = qt()
    res = []
    for w in QtWidgets.QApplication.topLevelWidgets():
        try:
            if w.isVisible() and isinstance(w, (QtWidgets.QDialog, QtWidgets.QMessageBox)):
                res.append(w)
        except RuntimeError:
            pass
    return res


def watchdog():
    """Quit (with whatever result there is) shortly before the harness would kill Maya."""
    limit = float(os.environ.get("I3D_GUI_TIMEOUT", "300")) - 15

    def run():
        time.sleep(max(limit, 10))
        RESULT["watchdog"] = "script did not finish in time"
        try:
            write_result()
        finally:
            hard_quit(3)

    t = threading.Thread(target=run)
    t.daemon = True
    t.start()


def boot(script):
    import maya.cmds as cmds
    import maya.utils
    with open(os.environ["I3D_GUI_OUT"] + ".boot", "w") as f:
        f.write("boot %s\n" % time.time())
    watchdog()

    def go():
        with open(os.environ["I3D_GUI_OUT"] + ".boot", "a") as f:
            f.write("go %s\n" % time.time())
        g = {"__name__": "__i3d_gui__", "__file__": script}
        try:
            with open(script, encoding="utf-8") as f:
                code = compile(f.read(), script, "exec")
            exec(code, g)
        except Exception:
            RESULT["error"] = traceback.format_exc()
            finish(False)

    # wait until the main window exists and Maya is idle
    def wait_ui(tries=[0]):
        tries[0] += 1
        import maya.mel as mel
        try:
            ready = bool(mel.eval('global string $gMainWindow; $tmp = $gMainWindow;')) and \
                cmds.window(mel.eval("$tmp = $gMainWindow"), q=True, exists=True)
        except Exception:
            ready = False
        if ready or tries[0] > 200:
            maya.utils.executeDeferred(go)
        else:
            cmds.evalDeferred(wait_ui, lowestPriority=True)

    cmds.evalDeferred(wait_ui, lowestPriority=True)
