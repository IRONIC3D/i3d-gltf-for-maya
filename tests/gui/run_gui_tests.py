"""GUI checks in real, isolated Maya sessions (not part of run_all.py: they need a desktop).

    python tests/gui/run_gui_tests.py [versions...] [--only dialog|install] [--out DIR]

dialog   the import options window (gui_options_dialog.py), plug-in from the repository module.
install  the drag-and-drop installer (gui_installer.py): session 1 calls Maya's own
         drop handler on install_i3dGltf.py from the release zip and answers the dialogs
         (Install, Maya's security warning: Allow, auto-load: Yes); session 2 starts Maya
         again on the same user folder (with -noAutoloadPlugins, so no other plug-in of the
         machine loads), checks the auto-load entry in pluginPrefs.mel, loads the plug-in by
         name (what auto-load does) and checks that Maya shows no security warning.

Every Maya runs with its own MAYA_APP_DIR, MAYA_SKIP_USERSETUP_PY=1 and a controlled
MAYA_MODULE_PATH, with a timeout, and is killed by PID (see run_gui.py).
"""

import argparse
import json
import os
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import run_gui  # noqa: E402


def summarize(tag, res):
    r = res.get("result") or {}
    checks = r.get("checks", {})
    bad = {k: v for k, v in checks.items() if not v["ok"]}
    print("%-22s %-28s %5.0fs  %s" % (tag, res["status"], res["seconds"],
                                      "PASS" if r.get("ok") else "FAIL %s" % (list(bad) or r.get("error")
                                                                             or r.get("watchdog"))))
    return bool(r.get("ok"))


def run_dialog(version, out):
    shots = os.path.join(out, "dialog_%s" % version)
    os.makedirs(shots, exist_ok=True)
    return run_gui.run(version, os.path.join(HERE, "gui_options_dialog.py"), os.path.join(shots, "result.json"),
                       timeout=420, module_path=REPO, shots=shots, keep_app_dir=True)


def run_install(version, out, deny=False):
    sys.path.insert(0, os.path.join(REPO, "tools"))
    import build_release
    tag = "install_deny" if deny else "install"
    base = os.path.join(out, "%s_%s" % (tag, version))
    os.makedirs(base, exist_ok=True)
    zpath = build_release.build(os.path.join(base, "dist"))
    pkg = os.path.join(base, "Extracted é", "i3dGltf")
    with zipfile.ZipFile(zpath) as zf:
        zf.extractall(pkg)
    app = tempfile.mkdtemp(prefix="i3d app %s " % version)  # spaces, ASCII
    results = []
    for session in (1, 2):
        shots = os.path.join(base, "session%d" % session)
        os.makedirs(shots, exist_ok=True)
        res = run_gui.run(version, os.path.join(HERE, "gui_installer.py"), os.path.join(shots, "result.json"),
                          timeout=420, app_dir=app, shots=shots,
                          args=json.dumps({"session": session, "installer": os.path.join(pkg, "install_i3dGltf.py"),
                                           "deny": deny}))
        results.append(res)
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("versions", nargs="*", default=["2024", "2027"])
    ap.add_argument("--only", choices=("dialog", "install", "deny"))
    ap.add_argument("--out", default=os.path.join(tempfile.gettempdir(), "i3dgltf_gui"))
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    ok = True
    report = {}
    for v in a.versions:
        if a.only in (None, "dialog"):
            res = run_dialog(v, a.out)
            report["dialog_%s" % v] = res
            ok &= summarize("%s dialog" % v, res)
        if a.only in (None, "install"):
            for k, res in enumerate(run_install(v, a.out), 1):
                report["install_%s_s%d" % (v, k)] = res
                ok &= summarize("%s install s%d" % (v, k), res)
        if a.only == "deny":
            for k, res in enumerate(run_install(v, a.out, deny=True), 1):
                report["deny_%s_s%d" % (v, k)] = res
                summarize("%s deny s%d" % (v, k), res)
    with open(os.path.join(a.out, "gui_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1, ensure_ascii=False, default=str)
    print("report: %s" % os.path.join(a.out, "gui_report.json"))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
