"""Timing check for a large synthetic mesh (not part of the unit suite).

    mayapy tests/perf_large_mesh.py [grid_n]      # default 708 -> ~1M triangles

Writes a GLB with one n x n grid (positions, normals, UVs, uint32 indices), imports it
with the exact cmds.file import call and prints timings.
"""

import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "scripts"))
sys.path.insert(0, HERE)


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 708
    os.environ.setdefault("MAYA_SKIP_USERSETUP_PY", "1")
    if REPO not in os.environ.get("MAYA_MODULE_PATH", ""):
        os.environ["MAYA_MODULE_PATH"] = REPO + os.pathsep + os.environ.get("MAYA_MODULE_PATH", "")
    import maya.standalone
    maya.standalone.initialize(name="python")
    import maya.cmds as cmds
    import maya.api.OpenMaya as om
    import fixtures
    from i3d_gltf.maya import importer

    cmds.loadPlugin("i3dGltf")
    work = tempfile.mkdtemp(prefix="i3dgltf_perf_")
    try:
        path = os.path.join(work, "grid.glb")
        t = time.time()
        verts, tris = fixtures.large_grid_glb(path, n)
        gen = time.time() - t
        try:
            import numpy  # noqa: F401
            has_numpy = True
        except ImportError:
            has_numpy = False
        t = time.time()
        cmds.file(path, i=True, type="IRONIC3D glTF", namespace="perf", ignoreVersion=True,
                  mergeNamespacesOnClash=False, preserveReferences=True)
        total = time.time() - t
        res = importer.last_result()
        sel = om.MSelectionList()
        sel.add("perf:GridShape")
        fn = om.MFnMesh(sel.getDagPath(0))
        assert (fn.numVertices, fn.numPolygons) == (verts, tris), (fn.numVertices, fn.numPolygons)
        print("PERF maya=%s python=%s numpy_available=%s (not used) grid=%d verts=%d tris=%d "
              "file_size=%.1fMB generate=%.2fs import_total=%.2fs prepare=%.2fs build=%.2fs"
              % (cmds.about(version=True), sys.version.split()[0], has_numpy, n, verts, tris,
                 os.path.getsize(path) / 1e6, gen, total, res.timings["prepare"], res.timings["build"]))
    finally:
        cmds.file(new=True, force=True)
        shutil.rmtree(work, ignore_errors=True)
    from run_suite import hard_exit
    hard_exit(0)


if __name__ == "__main__":
    main()
