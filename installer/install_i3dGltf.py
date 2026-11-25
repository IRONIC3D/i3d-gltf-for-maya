"""IRONIC3D glTF for Maya - installer.

Drag and drop this file onto a Maya viewport, or run it with mayapy:

    mayapy install_i3dGltf.py              install (or upgrade) for every Maya version
    mayapy install_i3dGltf.py --uninstall  remove what the installer created
    options: --force (allow a downgrade / replace a copy not made by this installer),
             --autoload (set the plug-in to load when Maya starts), --no-load, --json FILE

What it does: copies the folder ``i3dGltf`` next to this file to
``<Maya user app dir>/modules/i3dGltf/`` and writes ``<Maya user app dir>/modules/i3dGltf.mod``
(first line ``+ i3dGltf <version> ./i3dGltf``). Maya reads every .mod file in that folder at
start-up, for every Maya version, without any environment variable. The user app dir comes
from Maya (``cmds.internalVar(userAppDir=True)``), so a Documents folder redirected to
OneDrive, or a custom MAYA_APP_DIR, is handled. The list of installed files is kept in
``i3dGltf/install_manifest.json``; uninstall removes exactly those files, the .mod file and
the folders that are left empty.

The plug-in is loaded in the running Maya straight away (no restart needed). Maya's own
security preferences are never changed by this script.
"""

import json
import os
import re
import shutil
import sys

NAME = "i3dGltf"
TRANSLATOR = "IRONIC3D glTF"
MANIFEST = "install_manifest.json"
TITLE = "IRONIC3D glTF for Maya"

#: exit codes of the command-line mode
OK, FAILED, REFUSED = 0, 1, 2


class InstallError(Exception):
    pass


class Refused(InstallError):
    """The install would downgrade or replace a foreign copy and was not confirmed."""


# --- paths and versions ---------------------------------------------------------------

def here():
    return os.path.dirname(os.path.abspath(__file__))


def bundle_module(folder=None):
    """The module folder shipped next to this script."""
    return os.path.join(folder or here(), NAME)


def bundle_version(module_dir):
    path = os.path.join(module_dir, "scripts", "i3d_gltf", "__init__.py")
    try:
        with open(path, encoding="utf-8") as f:
            m = re.search(r'^VERSION\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"', f.read(), re.M)
    except OSError:
        m = None
    if not m:
        raise InstallError("This does not look like a complete IRONIC3D glTF package: %s is missing or "
                           "unreadable. Extract the whole zip and run the installer from there." % path)
    return m.group(1)


def parse_version(text):
    try:
        return tuple(int(p) for p in text.split("."))
    except (AttributeError, ValueError):
        return (0,)


def modules_dir(app_dir):
    return os.path.join(app_dir, "modules")


def mod_path(app_dir):
    return os.path.join(modules_dir(app_dir), NAME + ".mod")


def target_dir(app_dir):
    return os.path.join(modules_dir(app_dir), NAME)


def mod_text(version):
    return "+ %s %s ./%s\n" % (NAME, version, NAME)


def installed_info(app_dir):
    """{'version', 'path' (resolved module folder), 'ours' (made by this installer)} or None."""
    path = mod_path(app_dir)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            first = f.readline().split(None, 3)
    except OSError:
        return None
    if len(first) < 4 or first[0] != "+" or first[1] != NAME:
        return {"version": "0", "path": None, "ours": False}
    folder = first[3].strip()
    if not os.path.isabs(folder):
        folder = os.path.normpath(os.path.join(modules_dir(app_dir), folder))
    ours = os.path.isfile(os.path.join(folder, MANIFEST))
    return {"version": first[2], "path": folder, "ours": ours}


def is_ascii(path):
    try:
        path.encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


NON_ASCII_NOTE = ("The Maya user folder %s contains non-ASCII characters. Maya cannot load Python plug-ins "
                  "from such a folder (Maya 2024-2026 do not even read module files there). Set MAYA_APP_DIR "
                  "to a folder with a plain ASCII path, or install manually with MAYA_MODULE_PATH (see "
                  "README.txt).")


def _same(a, b):
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


# --- file operations ----------------------------------------------------------------------

def _source_files(src):
    out = []
    for root, dirs, files in os.walk(src):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        for name in sorted(files):
            if name.endswith((".pyc", ".pyo")) or name == MANIFEST:
                continue
            out.append(os.path.relpath(os.path.join(root, name), src).replace("\\", "/"))
    return out


def _read_manifest(folder):
    try:
        with open(os.path.join(folder, MANIFEST), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _remove_listed(folder, files):
    """Remove the listed files, Python caches, and folders left empty. Returns files kept
    (present in the folder but not ours)."""
    for rel in files:
        p = os.path.join(folder, rel.replace("/", os.sep))
        if os.path.isfile(p):
            os.remove(p)
    for root, dirs, names in os.walk(folder, topdown=False):
        if os.path.basename(root) == "__pycache__":
            for n in names:
                if n.endswith((".pyc", ".pyo")):
                    os.remove(os.path.join(root, n))
        try:
            os.rmdir(root)  # only succeeds when empty
        except OSError:
            pass
    kept = []
    if os.path.isdir(folder):
        for root, _, names in os.walk(folder):
            kept += [os.path.join(root, n) for n in names]
    return kept


def install_files(app_dir, src=None, force=False):
    """Copy the module and write the .mod file. Returns a dict describing what happened.
    Raises Refused for a downgrade or a foreign copy unless ``force``."""
    src = src or bundle_module()
    version = bundle_version(src)
    target = target_dir(app_dir)
    if os.path.isdir(target) and _same(src, target):
        raise InstallError("The installer is running from the installed copy itself (%s). Run the "
                           "install_i3dGltf.py from the extracted zip instead." % target)
    if not is_ascii(target) and not force:
        raise Refused(NON_ASCII_NOTE % target)
    old = installed_info(app_dir)
    action = "install"
    if old is not None:
        cmp = parse_version(old["version"]) > parse_version(version)
        if cmp and not force:
            raise Refused("Version %s is installed, which is newer than %s. Not downgrading without "
                          "confirmation." % (old["version"], version))
        if not old["ours"] and os.path.isdir(target) and not force:
            raise Refused("%s exists but was not created by this installer. Not replacing it without "
                          "confirmation." % target)
        action = "downgrade" if cmp else ("reinstall" if old["version"] == version else "upgrade")
    os.makedirs(modules_dir(app_dir), exist_ok=True)
    manifest = _read_manifest(target) if os.path.isdir(target) else None
    if manifest:
        _remove_listed(target, manifest.get("files", []))
    files = _source_files(src)
    for rel in files:
        dst = os.path.join(target, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(os.path.join(src, rel.replace("/", os.sep)), dst)
    with open(os.path.join(target, MANIFEST), "w", encoding="utf-8") as f:
        json.dump({"name": NAME, "version": version, "files": files, "modFile": NAME + ".mod"}, f, indent=1)
    with open(mod_path(app_dir), "w", encoding="utf-8", newline="\n") as f:
        f.write(mod_text(version))
    return {"action": action, "version": version, "previous": old["version"] if old else None,
            "module": target, "modFile": mod_path(app_dir), "files": len(files) + 1}


def uninstall_files(app_dir):
    """Remove exactly what install_files created. Returns a dict."""
    info = installed_info(app_dir)
    target = target_dir(app_dir)
    manifest = _read_manifest(target)
    if manifest is None:
        if info is None:
            return {"action": "none", "message": "IRONIC3D glTF is not installed in %s." % modules_dir(app_dir)}
        raise InstallError("%s was not created by this installer (no %s); nothing was removed. Delete %s and "
                           "the %s folder by hand if you want to remove it." % (mod_path(app_dir), MANIFEST,
                                                                                 mod_path(app_dir), NAME))
    kept = _remove_listed(target, manifest.get("files", []) + [MANIFEST])
    mod = mod_path(app_dir)
    if info is not None and info["path"] and _same(info["path"], target) and os.path.isfile(mod):
        os.remove(mod)
    return {"action": "uninstall", "version": manifest.get("version"), "module": target, "modFile": mod,
            "kept": kept}


# --- the running Maya session ------------------------------------------------------------

def maya_app_dir():
    import maya.cmds as cmds
    return os.path.normpath(cmds.internalVar(userAppDir=True))


def _plugin_file(app_dir):
    return os.path.join(target_dir(app_dir), "plug-ins", NAME + ".py").replace("\\", "/")


def _purge_package():
    for name in [m for m in sys.modules if m == "i3d_gltf" or m.startswith("i3d_gltf.")]:
        del sys.modules[name]


def other_copy(app_dir):
    """Path of an i3dGltf module Maya registered from somewhere other than the installed
    folder (for example through MAYA_MODULE_PATH), or None."""
    import maya.cmds as cmds
    try:
        if NAME not in (cmds.moduleInfo(listModules=True) or []):
            return None
        path = cmds.moduleInfo(path=True, moduleName=NAME)
    except RuntimeError:
        return None
    return None if not path or _same(path, target_dir(app_dir)) else path


def registered_here(app_dir):
    """True when this Maya session already found the installed module at start-up (then
    Maya trusts its plug-in folder and loads it without a security warning)."""
    import maya.cmds as cmds
    try:
        return NAME in (cmds.moduleInfo(listModules=True) or []) and \
            _same(cmds.moduleInfo(path=True, moduleName=NAME), target_dir(app_dir))
    except RuntimeError:
        return False


def unload_if_loaded():
    import maya.cmds as cmds
    if cmds.pluginInfo(NAME, q=True, loaded=True):
        old_path = cmds.pluginInfo(NAME, q=True, path=True)
        cmds.unloadPlugin(NAME)
        _purge_package()
        return old_path
    _purge_package()
    return None


def load_installed(app_dir):
    """Load the installed copy in this session. Returns (loaded version or None, error)."""
    import maya.cmds as cmds
    scripts = os.path.join(target_dir(app_dir), "scripts")
    sys.path[:] = [p for p in sys.path if not _same(p, scripts)]
    sys.path.insert(0, scripts)
    _purge_package()
    try:
        cmds.loadPlugin(_plugin_file(app_dir))
    except RuntimeError as exc:
        return None, str(exc).strip() or "loadPlugin failed"
    if not cmds.pluginInfo(NAME, q=True, loaded=True):
        return None, "Maya did not load the plug-in (it may have been blocked by Maya's security settings)."
    return cmds.pluginInfo(NAME, q=True, version=True), None


def is_autoload():
    import maya.cmds as cmds
    try:
        return bool(cmds.pluginInfo(NAME, q=True, autoload=True))
    except RuntimeError:
        return False


def set_autoload(on):
    import maya.cmds as cmds
    cmds.pluginInfo(NAME, edit=True, autoload=bool(on))
    cmds.pluginInfo(savePluginPrefs=True)


SECURITY_NOTE = ("Maya will show an 'Untrusted Plugin Loading' security warning for the new folder once, "
                 "in this session only: click Allow. From the next start Maya finds the plug-in through "
                 "its modules folder and does not ask again. This installer does not change Maya's "
                 "security preferences.")


# --- interactive (dropped on a viewport) ---------------------------------------------------

def _dialog(message, buttons=("OK",), icon="information", default=None):
    import maya.cmds as cmds
    return cmds.confirmDialog(title=TITLE, message=message, button=list(buttons), icon=icon,
                              defaultButton=default or buttons[0], cancelButton=buttons[-1],
                              dismissString=buttons[-1])


def run_interactive():
    app_dir = maya_app_dir()
    src = bundle_module()
    version = bundle_version(src)
    old = installed_info(app_dir)
    where = target_dir(app_dir)
    force = False
    note = "" if registered_here(app_dir) else "\n\n" + SECURITY_NOTE
    if not is_ascii(where):
        _dialog(NON_ASCII_NOTE % where, icon="critical")
        return None
    if old is not None and old["version"] == version and old["ours"]:
        choice = _dialog("IRONIC3D glTF %s is already installed in\n%s\n\nReinstall it, or uninstall it?"
                         % (version, where), ("Reinstall", "Uninstall", "Cancel"), "question")
        if choice == "Uninstall":
            return run_uninstall_interactive(app_dir)
        if choice != "Reinstall":
            return None
    elif old is not None and parse_version(old["version"]) > parse_version(version):
        choice = _dialog("A newer version (%s) is installed in\n%s\n\nReplace it with the older version %s?"
                         % (old["version"], where, version), ("Downgrade", "Cancel"), "warning", "Cancel")
        if choice != "Downgrade":
            return None
        force = True
    elif old is not None and not old["ours"] and os.path.isdir(where):
        choice = _dialog("%s exists but was not created by this installer.\n\nInstall %s over it?"
                         % (where, version), ("Install", "Cancel"), "warning", "Cancel")
        if choice != "Install":
            return None
        force = True
    else:
        verb = "Upgrade from %s to %s" % (old["version"], version) if old else "Install version %s" % version
        choice = _dialog("%s of IRONIC3D glTF for Maya?\n\nIt is installed for every Maya version, in\n%s%s"
                         % (verb, where, note), ("Install", "Cancel"), "question")
        if choice != "Install":
            return None

    try:
        was = unload_if_loaded()
        result = install_files(app_dir, src, force=force)
        loaded, error = load_installed(app_dir)
    except Exception as exc:
        _dialog("Installation failed:\n\n%s" % exc, icon="critical")
        raise
    lines = ["IRONIC3D glTF %s was installed (%s)." % (result["version"], result["action"]),
             "", "Module folder: %s" % result["module"], "Module file: %s" % result["modFile"], ""]
    if loaded:
        lines.append("The plug-in is loaded in this Maya session (version %s): File > Import now reads "
                     ".gltf and .glb files." % loaded)
    else:
        lines += ["The files are installed, but the plug-in was not loaded in this session:", error, "",
                  "If you clicked Deny in Maya's security warning, that is expected. Restart Maya: it then "
                  "finds the plug-in in its modules folder. Load it from Windows > Settings/Preferences > "
                  "Plug-in Manager (i3dGltf.py)."]
    other = other_copy(app_dir)
    if other:
        lines += ["", "Note: Maya also found another copy of the module at\n%s\n(for example through "
                      "MAYA_MODULE_PATH). Remove it to avoid loading the wrong version." % other]
    if loaded and not is_autoload():
        if _dialog("Load IRONIC3D glTF automatically every time this version of Maya starts?\n"
                   "(Without it, references to glTF files load only after the plug-in is loaded.)",
                   ("Yes", "No"), "question") == "Yes":
            set_autoload(True)
    if loaded:
        lines.append("Auto-load in this Maya version: %s." % ("on" if is_autoload() else "off"))
    lines += ["", "Other Maya versions on this computer find it at their next start."]
    if was and not _same(os.path.dirname(was), os.path.join(target_dir(app_dir), "plug-ins")):
        lines += ["", "The copy that was loaded before came from %s." % was]
    _dialog("\n".join(lines), icon="information" if loaded else "warning")
    return result


def run_uninstall_interactive(app_dir=None):
    app_dir = app_dir or maya_app_dir()
    info = installed_info(app_dir)
    if info is None:
        _dialog("IRONIC3D glTF is not installed in\n%s" % modules_dir(app_dir))
        return None
    if _dialog("Uninstall IRONIC3D glTF %s from\n%s ?" % (info["version"], target_dir(app_dir)),
               ("Uninstall", "Cancel"), "question", "Cancel") != "Uninstall":
        return None
    try:
        had_autoload = is_autoload()
        if had_autoload:
            set_autoload(False)
        unload_if_loaded()
        result = uninstall_files(app_dir)
    except Exception as exc:
        _dialog("Uninstall failed:\n\n%s" % exc, icon="critical")
        raise
    msg = "IRONIC3D glTF was removed:\n%s\n%s" % (result.get("module"), result.get("modFile"))
    if result.get("kept"):
        msg += "\n\nThese files were not created by the installer and were left in place:\n" + \
               "\n".join(result["kept"][:10])
    if had_autoload:
        msg += "\n\nAuto-load was turned off for this Maya version."
    msg += "\n\nOther Maya versions no longer find the plug-in from their next start."
    _dialog(msg)
    return result


def onMayaDroppedPythonFile(*_args):
    """Called by Maya when this file is dropped on a viewport."""
    try:
        run_interactive()
    finally:
        # Maya imports dropped files as modules; forget this one so a newer installer
        # dropped later in the same session is read again.
        sys.modules.pop(__name__, None)


# --- command line (mayapy) -------------------------------------------------------------------

def _hard_exit(code):
    sys.stdout.flush()
    sys.stderr.flush()
    if sys.platform == "win32":
        import ctypes
        k = ctypes.WinDLL("kernel32")
        k.TerminateProcess(ctypes.c_void_p(k.GetCurrentProcess()), code)
    os._exit(code)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Install IRONIC3D glTF for Maya (run with mayapy).")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--force", action="store_true", help="allow a downgrade / replace a foreign copy")
    ap.add_argument("--autoload", action="store_true", help="set the plug-in to load when Maya starts")
    ap.add_argument("--no-load", action="store_true", help="do not load the plug-in to check the install")
    ap.add_argument("--json", help="write the result as JSON to this file")
    args = ap.parse_args(argv)
    try:
        import maya.standalone
    except ImportError:
        print("Run this script with mayapy, or drag and drop it onto a Maya viewport.")
        return FAILED
    maya.standalone.initialize(name="python")
    result = {"ok": False}
    code = FAILED
    try:
        app_dir = maya_app_dir()
        result["appDir"] = app_dir
        if args.uninstall:
            unload_if_loaded()
            result.update(uninstall_files(app_dir))
            result["ok"] = True
            code = OK
            print(result.get("message") or "Uninstalled %s" % result.get("module"))
        else:
            unload_if_loaded()
            result.update(install_files(app_dir, force=args.force))
            print("%s %s -> %s" % (result["action"].capitalize(), result["version"], result["module"]))
            print("Module file: %s" % result["modFile"])
            if not args.no_load:
                loaded, error = load_installed(app_dir)
                result["loaded"] = loaded
                if error:
                    result["loadError"] = error
                    print("Could not load the plug-in: %s" % error)
                else:
                    print("Plug-in loaded, version %s" % loaded)
                    if args.autoload:
                        set_autoload(True)
                        result["autoload"] = True
            result["ok"] = not result.get("loadError")
            code = OK if result["ok"] else FAILED
    except Refused as exc:
        result["error"] = str(exc)
        result["refused"] = True
        print("Refused: %s (use --force to override)" % exc)
        code = REFUSED
    except Exception as exc:
        result["error"] = "%s: %s" % (type(exc).__name__, exc)
        print("Failed: %s" % result["error"])
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=1, ensure_ascii=False)
    return code


if __name__ == "__main__":
    _hard_exit(main())
