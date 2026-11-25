"""Timing check for a large skinned + animated file (not part of the unit suite).

    mayapy tests/perf_skinned.py [grid_n] [joints] [keys]     # default 316 100 1000
    mayapy tests/perf_skinned.py --file some.glb               # time a real file instead

Synthetic case: an n x n grid (~100k vertices at n=316), a chain of ``joints`` joints,
4 influences per vertex, and one animation with ``keys`` LINEAR keys on every joint's
rotation and translation. Imports it with the exact cmds.file import call and prints
timings (prepare / geometry / deformers / animation).
"""

import math
import os
import shutil
import sys
import tempfile
import time
from array import array

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "scripts"))
sys.path.insert(0, HERE)


def skinned_grid_glb(path, n, njoints, nkeys):
    import fixtures
    from gltf_builder import FLOAT, UINT, USHORT, GltfBuilder
    pos, nrm, uv, idx = fixtures.grid_arrays(n)
    count = len(pos) // 3
    # grid lies in XZ (0..1); stand it up along Y so the chain bends it: y <- z
    p = array("f", pos)
    p[1::3], p[2::3] = pos[2::3], array("f", [0.0]) * count
    seg = 1.0 / njoints
    joints = array("H", [0]) * (4 * count)
    weights = array("f", [0.0]) * (4 * count)
    ys = p[1::3]
    for v in range(count):
        j = min(njoints - 1, int(ys[v] / seg))
        base = 4 * v
        for k, (jj, w) in enumerate(((j, 0.4), (j - 1, 0.3), (j + 1, 0.2), (j - 2, 0.1))):
            joints[base + k] = min(njoints - 1, max(0, jj))
            weights[base + k] = w
    b = GltfBuilder()

    def acc(data, ctype, type_, cnt, minmax=None):
        a = {"bufferView": b.add_buffer_view(data.tobytes()), "componentType": ctype, "type": type_, "count": cnt}
        if minmax:
            a["min"], a["max"] = minmax
        return b._add("accessors", a)
    attrs = {"POSITION": acc(p, FLOAT, "VEC3", count, ([0, 0, 0], [1, 1, 0])),
             "NORMAL": acc(nrm, FLOAT, "VEC3", count), "TEXCOORD_0": acc(uv, FLOAT, "VEC2", count),
             "JOINTS_0": acc(joints, USHORT, "VEC4", count), "WEIGHTS_0": acc(weights, FLOAT, "VEC4", count)}
    mesh = b.add_mesh([{"attributes": attrs, "indices": acc(idx, UINT, "SCALAR", len(idx))}], name="Grid")
    # chain: joint k at height k * seg
    first = 2
    b.add_node("Skinned", mesh=mesh, skin=0)                       # 0
    b.add_node("Armature", children=[first])                      # 1
    for k in range(njoints):
        kw = {"translation": [0, seg if k else 0.0, 0]}
        if k + 1 < njoints:
            kw["children"] = [first + k + 1]
        b.add_node("J%d" % k, **kw)
    ibm = array("f")
    for k in range(njoints):
        ibm.extend([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, -k * seg, 0, 1])
    b.json["skins"] = [{"joints": list(range(first, first + njoints)),
                        "inverseBindMatrices": acc(ibm, FLOAT, "MAT4", njoints)}]
    times = array("f", [k / 30.0 for k in range(nkeys)])
    t_acc = acc(times, FLOAT, "SCALAR", nkeys, ([0.0], [times[-1]]))
    samplers, channels = [], []
    for k in range(njoints):
        rot = array("f")
        tr = array("f")
        for i in range(nkeys):
            a = 0.3 * math.sin(i * 0.05 + k * 0.1) / njoints * 10
            rot.extend([0.0, 0.0, math.sin(a / 2), math.cos(a / 2)])
            tr.extend([0.001 * math.sin(i * 0.1), seg if k else 0.0, 0.0])
        samplers.append({"input": t_acc, "output": acc(rot, FLOAT, "VEC4", nkeys), "interpolation": "LINEAR"})
        channels.append({"sampler": len(samplers) - 1, "target": {"node": first + k, "path": "rotation"}})
        samplers.append({"input": t_acc, "output": acc(tr, FLOAT, "VEC3", nkeys), "interpolation": "LINEAR"})
        channels.append({"sampler": len(samplers) - 1, "target": {"node": first + k, "path": "translation"}})
    b.json["animations"] = [{"name": "Wave", "samplers": samplers, "channels": channels}]
    b.add_scene([0, 1])
    b.write_glb(path)
    return count, len(idx) // 3


def main():
    args = sys.argv[1:]
    os.environ.setdefault("MAYA_SKIP_USERSETUP_PY", "1")
    if REPO not in os.environ.get("MAYA_MODULE_PATH", ""):
        os.environ["MAYA_MODULE_PATH"] = REPO + os.pathsep + os.environ.get("MAYA_MODULE_PATH", "")
    import maya.standalone
    maya.standalone.initialize(name="python")
    import maya.cmds as cmds
    from i3d_gltf.maya import importer
    cmds.loadPlugin("i3dGltf")
    try:  # load Arnold up front: its one-time plug-in load is not import time
        cmds.loadPlugin("mtoa", quiet=True)
    except RuntimeError:
        pass
    work = tempfile.mkdtemp(prefix="i3dgltf_perf_skin_")
    cmds.workspace(work, openWorkspace=True)
    try:
        if args and args[0] == "--file":
            path = args[1]
            desc = os.path.basename(path)
            gen = 0.0
        else:
            n, nj, nk = [int(a) for a in (args + ["316", "100", "1000"][len(args):])][:3]
            path = os.path.join(work, "skinned.glb")
            t = time.time()
            verts, tris = skinned_grid_glb(path, n, nj, nk)
            gen = time.time() - t
            desc = "synthetic grid=%d verts=%d tris=%d joints=%d keys/channel=%d" % (n, verts, tris, nj, nk)
        t = time.time()
        cmds.file(path, i=True, type="IRONIC3D glTF", namespace="perf", ignoreVersion=True,
                  mergeNamespacesOnClash=False, preserveReferences=True,
                  options="textureDir=%s" % os.path.join(work, "tex").replace("\\", "/"))
        total = time.time() - t
        res = importer.last_result()
        tm = res.timings
        print("PERF maya=%s %s generate=%.2fs import_total=%.2fs prepare=%.2fs geometry=%.2fs materials=%.2fs "
              "deformers=%.2fs animation=%.2fs skins=%d curves=%d"
              % (cmds.about(version=True), desc, gen, total, tm["prepare"], tm.get("build_geometry", 0),
                 tm.get("build_materials", 0), tm.get("build_deformers", 0), tm.get("build_animation", 0),
                 len(res.skin_clusters), len(res.anim_curves)))
    finally:
        cmds.file(new=True, force=True)
        shutil.rmtree(work, ignore_errors=True)
    sys.stdout.flush()
    from run_suite import hard_exit
    hard_exit(0)


if __name__ == "__main__":
    main()
