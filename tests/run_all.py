"""Run the whole suite against every installed mayapy (Maya 2024-2028) and print a
per-version pass/fail summary.

    python tests/run_all.py                 # all versions found
    python tests/run_all.py 2024 2027       # selected versions
    python tests/run_all.py --timeout 900

Each Maya run uses an isolated environment: MAYA_APP_DIR points at a throw-away folder,
userSetup.py is skipped, and MAYA_MODULE_PATH is this repository, so the plug-in is
loaded through i3dGltf.mod exactly like an installed module. Every subprocess has a
timeout; the test process ends itself with a hard exit (mayapy can hang on exit), and
results are read from a JSON file rather than the exit code.
Exit status: 0 when every required version (2024-2027) passed.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SUITE = os.path.join(HERE, "run_suite.py")
VERSIONS = ["2024", "2025", "2026", "2027", "2028"]
REQUIRED = {"2024", "2025", "2026", "2027"}


def mayapy_path(version):
    if sys.platform == "win32":
        return r"C:\Program Files\Autodesk\Maya%s\bin\mayapy.exe" % version
    if sys.platform == "darwin":
        return "/Applications/Autodesk/maya%s/Maya.app/Contents/bin/mayapy" % version
    return "/usr/autodesk/maya%s/bin/mayapy" % version


def run(cmd, env, timeout, log_path):
    t0 = time.time()
    with open(log_path, "w", encoding="utf-8", errors="replace") as log:
        try:
            proc = subprocess.run(cmd, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=timeout,
                                  cwd=REPO)
            status = proc.returncode
        except subprocess.TimeoutExpired:
            status = "timeout"
    return status, time.time() - t0


def summarize(result_path, status):
    if os.path.exists(result_path):
        with open(result_path) as f:
            r = json.load(f)
        passed = r["ran"] - r["failures"] - r["errors"]
        text = "%s %d/%d" % ("PASS" if r["ok"] else "FAIL", passed, r["ran"])
        if r["skipped"]:
            text += " (%d skipped)" % r["skipped"]
        return r["ok"], text, r
    return False, "NO RESULT (%s)" % status, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("versions", nargs="*", default=VERSIONS)
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--keep-logs", action="store_true")
    args = ap.parse_args()

    work = tempfile.mkdtemp(prefix="i3dgltf_runall_")
    rows = []
    all_required_ok = True

    # Core tests under the current (plain CPython) interpreter
    res = os.path.join(work, "cpython_core.json")
    log = os.path.join(work, "cpython_core.log")
    status, secs = run([sys.executable, SUITE, "--result", res], dict(os.environ), args.timeout, log)
    ok, text, r = summarize(res, status)
    rows.append(("CPython", sys.version.split()[0], text, "-", "%.0fs" % secs, log))
    all_required_ok &= ok

    for v in args.versions:
        exe = mayapy_path(v)
        if not os.path.exists(exe):
            rows.append((v, "-", "not installed", "not installed", "", ""))
            continue
        env = dict(os.environ)
        app_dir = os.path.join(work, "maya_app_%s" % v)
        os.makedirs(app_dir)
        env.update({"MAYA_APP_DIR": app_dir, "MAYA_SKIP_USERSETUP_PY": "1", "MAYA_MODULE_PATH": REPO,
                    "MAYA_DISABLE_CIP": "1", "MAYA_DISABLE_CER": "1", "MAYA_DISABLE_ADP": "1"})
        core_res = os.path.join(work, "%s_core.json" % v)
        core_log = os.path.join(work, "%s_core.log" % v)
        st1, s1 = run([exe, SUITE, "--result", core_res], env, args.timeout, core_log)
        ok1, text1, r1 = summarize(core_res, st1)
        maya_res = os.path.join(work, "%s_maya.json" % v)
        maya_log = os.path.join(work, "%s_maya.log" % v)
        st2, s2 = run([exe, SUITE, "--maya-only", "--result", maya_res], env, args.timeout, maya_log)
        ok2, text2, r2 = summarize(maya_res, st2)
        py = (r1 or r2 or {}).get("python", "?")
        rows.append((v, py, text1, text2, "%.0fs" % (s1 + s2), maya_log))
        if v in REQUIRED:
            all_required_ok &= ok1 and ok2
        for r in (r1, r2):
            if r and r["failed_tests"]:
                for t in r["failed_tests"]:
                    print("  [%s] failed: %s" % (v, t))

    print("")
    print("%-8s %-8s %-22s %-22s %s" % ("Maya", "Python", "core tests", "Maya tests", "time"))
    print("-" * 72)
    for row in rows:
        print("%-8s %-8s %-22s %-22s %s" % row[:5])
    print("-" * 72)
    print("Required versions (2024-2027): %s" % ("ALL PASSED" if all_required_ok else "FAILURES"))
    if args.keep_logs or not all_required_ok:
        print("Logs kept in %s" % work)
    else:
        shutil.rmtree(work, ignore_errors=True)
    sys.exit(0 if all_required_ok else 1)


if __name__ == "__main__":
    main()
