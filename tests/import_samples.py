"""Headless real-world check (not part of the unit suite).

    mayapy tests/import_samples.py <folder>

Imports and references every .gltf/.glb under <folder> (e.g. models downloaded from
KhronosGroup/glTF-Sample-Assets into the git-ignored _samples/ folder) with the exact
host-application cmds.file calls, and prints one line per file.
"""

import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "scripts"))
sys.path.insert(0, HERE)

T = "IRONIC3D glTF"


def main():
    folder = os.path.abspath(sys.argv[1])
    os.environ.setdefault("MAYA_SKIP_USERSETUP_PY", "1")
    if REPO not in os.environ.get("MAYA_MODULE_PATH", ""):
        os.environ["MAYA_MODULE_PATH"] = REPO + os.pathsep + os.environ.get("MAYA_MODULE_PATH", "")
    import maya.standalone
    maya.standalone.initialize(name="python")
    import maya.cmds as cmds
    from i3d_gltf.maya import importer
    cmds.loadPlugin("i3dGltf")

    files = []
    for root, _, names in os.walk(folder):
        files += [os.path.join(root, n) for n in sorted(names) if n.lower().endswith((".gltf", ".glb"))]
    print("MAYA %s" % cmds.about(version=True))
    for path in sorted(files):
        rel = os.path.relpath(path, folder)
        line = []
        for mode in ("import", "reference"):
            cmds.file(new=True, force=True)
            before = set(cmds.ls(assemblies=True))
            t = time.time()
            try:
                if mode == "import":
                    cmds.file(path, i=True, type=T, namespace="smp", ignoreVersion=True,
                              mergeNamespacesOnClash=False, preserveReferences=True)
                else:
                    cmds.file(path, reference=True, type=T, namespace="smp", ignoreVersion=True,
                              mergeNamespacesOnClash=False)
            except RuntimeError as exc:
                line.append("%s: FAILED (%s)" % (mode, str(exc).splitlines()[0][:160]))
                continue
            new = [a for a in cmds.ls(assemblies=True) if a not in before]
            err = importer.last_error()
            if err is not None:
                line.append("%s: FAILED (%s)" % (mode, err[1][:160]))
                continue
            meshes = cmds.ls("smp:*", type="mesh") or []
            faces = sum(cmds.polyEvaluate(m, face=True) for m in meshes)
            res = importer.last_result()
            warns = len(res.reporter.warnings)
            line.append("%s: ok %.2fs roots=%d meshes=%d faces=%d warnings=%d"
                        % (mode, time.time() - t, len(new), len(meshes), faces, warns))
            if mode == "import" and warns:
                for w in sorted(set(res.reporter.warnings))[:4]:
                    line.append("    warning: %s" % w)
        print("%-55s %s" % (rel, line[0]))
        for extra in line[1:]:
            print("%-55s %s" % ("", extra))
    sys.stdout.flush()
    cmds.file(new=True, force=True)
    from run_suite import hard_exit
    hard_exit(0)


if __name__ == "__main__":
    main()
