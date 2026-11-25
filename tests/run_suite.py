"""Run the test suite inside ONE interpreter.

    python  tests/run_suite.py                 # core tests only (any CPython 3.10+, or mayapy)
    mayapy  tests/run_suite.py --maya          # core + Maya tests under maya.standalone
    mayapy  tests/run_suite.py --maya-only -k Reference

Writes a JSON summary when --result FILE is given, prints a final 'SUITE RESULT' line, and
ends mayapy with a hard process exit (mayapy can hang or crash in its exit handlers).
"""

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)


def hard_exit(code):
    """End the process without running Maya's exit handlers. On Windows os._exit() still
    runs DLL exit tables, where mayapy sometimes crashes (and writes a crash-recovery
    scene to %TEMP%), so terminate the process outright."""
    sys.stdout.flush()
    sys.stderr.flush()
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.GetCurrentProcess.restype = wintypes.HANDLE
        k.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
        k.TerminateProcess(k.GetCurrentProcess(), code)
    os._exit(code)


def _filter(suite, pattern):
    out = unittest.TestSuite()
    for t in suite:
        if isinstance(t, unittest.TestSuite):
            out.addTests(_filter(t, pattern))
        elif pattern.lower() in t.id().lower():
            out.addTest(t)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--maya", action="store_true", help="also run the Maya tests (needs mayapy)")
    ap.add_argument("--maya-only", action="store_true")
    ap.add_argument("--result", help="write a JSON summary here")
    ap.add_argument("-k", dest="pattern", help="only run tests whose id contains this text")
    ap.add_argument("-q", action="store_true", help="less verbose")
    args = ap.parse_args()
    use_maya = args.maya or args.maya_only

    sys.path.insert(0, os.path.join(REPO, "scripts"))
    sys.path.insert(0, HERE)
    tmp = tempfile.mkdtemp(prefix="i3dgltf_suite_")
    os.environ["I3DGLTF_TEST_TMP"] = tmp

    maya_version = None
    if use_maya:
        os.environ.setdefault("MAYA_SKIP_USERSETUP_PY", "1")
        mp = os.environ.get("MAYA_MODULE_PATH", "")
        if REPO not in mp.split(os.pathsep):
            os.environ["MAYA_MODULE_PATH"] = REPO + (os.pathsep + mp if mp else "")
        t0 = time.time()
        import maya.standalone
        maya.standalone.initialize(name="python")
        import maya.cmds as cmds
        maya_version = cmds.about(version=True)
        print("maya.standalone %s initialised in %.1fs" % (maya_version, time.time() - t0))

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    dirs = []
    if not args.maya_only:
        dirs.append(os.path.join(HERE, "core_tests"))
    if use_maya:
        dirs.append(os.path.join(HERE, "maya_tests"))
    for d in dirs:
        sys.path.insert(0, d)
        suite.addTests(loader.discover(d, pattern="test_*.py", top_level_dir=d))
    if args.pattern:
        suite = _filter(suite, args.pattern)

    t0 = time.time()
    result = unittest.TextTestRunner(stream=sys.stdout, verbosity=1 if args.q else 2).run(suite)
    elapsed = time.time() - t0
    ok = result.wasSuccessful() and result.testsRun > 0
    summary = {
        "python": sys.version.split()[0],
        "maya": maya_version,
        "ran": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "ok": ok,
        "seconds": round(elapsed, 1),
        "failed_tests": [t.id() for t, _ in result.failures + result.errors],
    }
    if args.result:
        with open(args.result, "w") as f:
            json.dump(summary, f, indent=1)
    print("SUITE RESULT: %s" % json.dumps(summary))
    code = 0 if ok else 1
    if use_maya:
        import maya.cmds as cmds
        cmds.file(new=True, force=True)  # release file handles before cleanup
    shutil.rmtree(tmp, ignore_errors=True)
    if use_maya:
        hard_exit(code)
    sys.exit(code)


if __name__ == "__main__":
    main()
