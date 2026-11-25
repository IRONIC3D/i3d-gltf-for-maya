"""Reference comparison for real files (not part of the unit suite).

    mayapy tests/compare_samples.py <file or folder> [...] [--options "k=v;k=v"] [--times N]
                                    [--json FILE] [--z-up]

Imports every .gltf/.glb (exact host-application cmds.file import call, shader=none so
no texture is written anywhere), then compares the deformed world-space vertex positions
in Maya with the independent reference evaluator (tests/gltf_eval.py) at the animation's
start and end, evenly spaced times and a few times between keys. Prints one line per file:
max position error in cm, relative to the scene extent, import time, nodes built.
Nothing is written next to the input files.
"""

import argparse
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "scripts"))
sys.path.insert(0, HERE)

T = "IRONIC3D glTF"


def gather(paths):
    files = []
    for p in paths:
        if os.path.isdir(p):
            for root, _, names in os.walk(p):
                files += [os.path.join(root, n) for n in sorted(names) if n.lower().endswith((".gltf", ".glb"))]
        else:
            files.append(p)
    return sorted(files)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--options", default="")
    ap.add_argument("--times", type=int, default=7)
    ap.add_argument("--json")
    ap.add_argument("--z-up", action="store_true")
    ap.add_argument("--all-animations", action="store_true", help="import and compare each animation in turn")
    ap.add_argument("--max-points", type=int, default=400000, help="skip comparison above this many vertices x times")
    args = ap.parse_args()

    os.environ.setdefault("MAYA_SKIP_USERSETUP_PY", "1")
    if REPO not in os.environ.get("MAYA_MODULE_PATH", ""):
        os.environ["MAYA_MODULE_PATH"] = REPO + os.pathsep + os.environ.get("MAYA_MODULE_PATH", "")
    import maya.standalone
    maya.standalone.initialize(name="python")
    import maya.cmds as cmds
    from i3d_gltf.maya import importer
    import gltf_eval
    import maya_compare
    cmds.loadPlugin("i3dGltf")
    project = tempfile.mkdtemp(prefix="i3d_compare_proj_")
    cmds.workspace(project, openWorkspace=True)
    options = "shader=none;" + args.options
    rows = []
    print("MAYA %s options=%s" % (cmds.about(version=True), options))
    jobs = []
    from i3d_gltf.core import load
    for path in gather(args.paths):
        if args.all_animations:
            n = len(load(path).animations)
            jobs += [(path, k) for k in range(n)] or [(path, None)]
        else:
            jobs.append((path, None))
    for path, anim in jobs:
        opts = options + (";animation=%d" % anim if anim is not None else "")
        label = os.path.basename(path) + ("#%d" % anim if anim is not None else "")
        cmds.file(new=True, force=True)
        if args.z_up:
            cmds.upAxis(axis="z", rotateView=False)
        row = {"file": path, "animation": anim}
        t = time.time()
        try:
            cmds.file(path, i=True, type=T, namespace="cmp", ignoreVersion=True, mergeNamespacesOnClash=False,
                      preserveReferences=True, options=opts)
        except RuntimeError as exc:
            row["error"] = str(exc).splitlines()[0][:200]
            print("%-50s IMPORT FAILED %s" % (label, row["error"]))
            rows.append(row)
            continue
        row["import_s"] = time.time() - t
        res = importer.last_result()
        ev = gltf_eval.Evaluator(path, res.animation.index if res.animation else None)
        times = maya_compare.sample_times(ev, count=args.times)
        nverts = sum(sr.mesh_data.num_vertices * len(sr.node_indices) for sr in res.shapes)
        if nverts * len(times) > args.max_points:
            times = times[:max(2, args.max_points // max(1, nverts))]
        t = time.time()
        cmp = maya_compare.compare(res, ev, times)
        row.update(max_error_cm=cmp["max_error"], extent_cm=cmp["extent"], points=cmp["points"], times=len(times),
                   compare_s=time.time() - t, where=str(cmp["where"]),
                   skins=len(res.skin_clusters), blendshapes=len(res.blend_shapes), curves=len(res.anim_curves),
                   joints=len(res.prepared.plan.joints), warnings=sorted(set(res.reporter.warnings)))
        rel = cmp["max_error"] / cmp["extent"] if cmp["extent"] else 0.0
        print("%-50s err=%.2e cm (rel %.1e, extent %.0f cm) import=%.2fs skins=%d bs=%d curves=%d joints=%d times=%d"
              % (label, cmp["max_error"], rel, cmp["extent"], row["import_s"], row["skins"],
                 row["blendshapes"], row["curves"], row["joints"], len(times)))
        for w in row["warnings"]:
            print("    warning: %s" % w[:200])
        sys.stdout.flush()
        rows.append(row)
    if args.json:
        with open(args.json, "w") as f:
            json.dump(rows, f, indent=1)
    cmds.file(new=True, force=True)
    from run_suite import hard_exit
    hard_exit(0)


if __name__ == "__main__":
    main()
