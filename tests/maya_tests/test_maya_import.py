"""Maya integration tests: run with mayapy via tests/run_suite.py --maya."""

import os
import re

import maya.api.OpenMaya as om
import maya.cmds as cmds
import maya.mel as mel

import fixtures
from maya_helpers import (PLUGIN, T, MayaTestCase, fixture_dir, import_file, new_assemblies,
                          reference_file, scene_files)

import i3d_gltf
from i3d_gltf.maya import importer


def mesh_fn(name):
    sel = om.MSelectionList()
    sel.add(name)
    return om.MFnMesh(sel.getDagPath(0))


def strip_ns(name):
    return "|".join(p.split(":")[-1] for p in name.split("|"))


class TestPluginRegistration(MayaTestCase):
    def test_translator_registered(self):
        self.assertIn(T, cmds.translator(q=True, list=True))
        self.assertEqual(cmds.translator(T, q=True, filter=True), "*.gltf;*.glb")
        self.assertTrue(cmds.translator(T, q=True, readSupport=True))
        self.assertFalse(cmds.translator(T, q=True, writeSupport=True))
        self.assertEqual(cmds.translator(T, q=True, defaultOptions=True),
                         "scale=1.0;importNormals=1;importColors=1;shader=aiStandardSurface;textureDir=;animation=first;animSampling=keys;timeRange=1")

    def test_version_single_source(self):
        self.assertEqual(cmds.pluginInfo(PLUGIN, q=True, version=True), i3d_gltf.VERSION)
        self.assertEqual(i3d_gltf.VERSION, "0.5.0")
        self.assertEqual(i3d_gltf.__version__, i3d_gltf.VERSION)
        repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        with open(os.path.join(repo, "i3dGltf.mod")) as f:
            self.assertIn(" %s " % i3d_gltf.VERSION, f.read())

    def test_file_dialog_filter_lists_both_extensions(self):
        try:
            mel.eval('source "fileDialogFilterTypes.mel"')
            text = mel.eval("buildReadableFileFilterList()")
        except RuntimeError as exc:
            self.skipTest("file dialog MEL not available in standalone: %s" % exc)
        self.assertIn("%s ( *.gltf *.glb )" % T, text)


class TestImport(MayaTestCase):
    def test_import_gltf_and_glb_namespaced(self):
        for path, ns in zip(scene_files(), ("g", "b")):
            added = new_assemblies(import_file, path, ns)
            self.assertEqual(sorted(strip_ns(a) for a in added), ["|Root_Node_", "|Second_Root"])
            for a in added:
                self.assertTrue(a.startswith("|%s:" % ns), a)
                for d in cmds.listRelatives(a, allDescendents=True, fullPath=True) or []:
                    self.assertTrue(d.split("|")[-1].startswith(ns + ":"), d)
                    self.assertFalse(cmds.referenceQuery(d, isNodeReferenced=True))

    def test_auto_detect_without_type(self):
        gltf, glb = scene_files()
        for path in (gltf, glb):
            cmds.file(new=True, force=True)
            added = new_assemblies(cmds.file, path, i=True)
            self.assertEqual(sorted(strip_ns(a) for a in added), ["|Root_Node_", "|Second_Root"])

    def test_hierarchy_names_and_transforms(self):
        import_file(scene_files()[0], "s")
        root = "|s:Root_Node_"
        self.assertEqual(sorted(cmds.listRelatives(root, children=True)), ["s:Cube", "s:Cube_3", "s:node_2"])
        self.assertEqual(cmds.listRelatives("s:_3d_child", parent=True), ["s:node_2"])
        self.assertFalse(cmds.ls("s:Orphan"))  # not in the default scene
        self.assertVecAlmost(cmds.getAttr(root + ".translate")[0], (100, 200, 300))
        self.assertVecAlmost(cmds.getAttr("s:Cube.rotate")[0], (0, 90, 0))
        self.assertVecAlmost(cmds.getAttr("s:Cube.scale")[0], (2, 1, 1))
        self.assertVecAlmost(cmds.getAttr("s:Cube_3.translate")[0], (0, 0, -200))
        # matrix node decomposed into channels
        self.assertVecAlmost(cmds.getAttr("s:node_2.translate")[0], (0, 50, 0))
        self.assertVecAlmost(cmds.getAttr("s:node_2.rotate")[0], (0, 0, 90))
        self.assertVecAlmost(cmds.getAttr("s:node_2.scale")[0], (1, 1, 1))
        # world position of a vertex through the matrix node: (1,0,0)m -> rotZ90 -> (0,1,0)
        self.assertVecAlmost(cmds.pointPosition("s:_3d_childShape.vtx[1]", world=True), (100, 350, 300))
        self.assertEqual(cmds.listRelatives("s:Cube", shapes=True), ["s:CubeShape"])

    def test_triangles_stay_triangles(self):
        import_file(scene_files()[1], "s")
        fn = mesh_fn("s:Cube|s:CubeShape")
        self.assertEqual((fn.numVertices, fn.numPolygons), (24, 12))
        counts, _ = fn.getVertices()
        self.assertEqual(set(counts), {3})
        multi = mesh_fn("s:_3d_childShape")
        self.assertEqual((multi.numVertices, multi.numPolygons), (15, 7))
        self.assertEqual(set(multi.getVertices()[0]), {3})
        self.assertVecAlmost(tuple(fn.getPoint(0, om.MSpace.kObject))[:3], (-50, -50, 50))

    def test_uvs_flipped_and_named(self):
        import_file(scene_files()[0], "s")
        fn = mesh_fn("s:Cube|s:CubeShape")
        self.assertEqual(list(fn.getUVSetNames()), ["map1", "map2"])
        _, _, uv0, uv1, _, _ = fixtures.cube_data()
        u, v = fn.getUVs("map1")
        self.assertEqual(len(u), 24)
        for k in range(24):
            self.assertAlmostEqual(u[k], uv0[k][0], 5)
            self.assertAlmostEqual(v[k], 1.0 - uv0[k][1], 5)
        u2, v2 = fn.getUVs("map2")
        self.assertAlmostEqual(v2[3], 1.0 - uv1[3][1], 5)
        # face-vertex UV ids follow the vertex ids
        self.assertEqual(fn.getPolygonUVid(1, 2, "map1"), fn.getPolygonVertices(1)[2])

    def test_normals_locked(self):
        import_file(scene_files()[0], "s")
        fn = mesh_fn("s:Cube|s:CubeShape")
        _, nrm, _, _, _, _ = fixtures.cube_data()
        self.assertTrue(all(fn.isNormalLocked(i) for i in range(fn.numNormals)))
        for vid in (0, 5, 13, 23):
            n = fn.getFaceVertexNormal(_face_of_vertex(fn, vid), vid)
            self.assertVecAlmost((n.x, n.y, n.z), nrm[vid])

    def test_normals_option_off(self):
        import_file(scene_files()[0], "s", options="importNormals=0")
        fn = mesh_fn("s:Cube|s:CubeShape")
        self.assertFalse(any(fn.isNormalLocked(i) for i in range(fn.numNormals)))

    def test_colors(self):
        import_file(scene_files()[0], "s")
        fn = mesh_fn("s:Cube|s:CubeShape")
        self.assertEqual(list(fn.getColorSetNames()), ["colorSet1"])
        self.assertEqual(fn.getColorRepresentation("colorSet1"), om.MFnMesh.kRGBA)
        cols = fn.getVertexColors("colorSet1")
        _, _, _, _, col, _ = fixtures.cube_data()
        for k in (0, 7, 23):
            c = cols[k]
            self.assertVecAlmost((c.r, c.g, c.b, c.a), col[k])
        multi = mesh_fn("s:_3d_childShape")
        self.assertEqual(multi.getColorRepresentation("colorSet1"), om.MFnMesh.kRGB)
        c = multi.getVertexColors("colorSet1")[1]
        self.assertVecAlmost((c.r, c.g, c.b, c.a), (0, 1, 0, 1))

    def test_colors_option_off(self):
        import_file(scene_files()[0], "s", options="importColors=0")
        self.assertEqual(list(mesh_fn("s:Cube|s:CubeShape").getColorSetNames()), [])

    def test_multi_primitive_face_ranges(self):
        import_file(scene_files()[0], "s")
        result = importer.last_result()
        shape = [s for s in result.shapes if s.mesh_index == 1][0]
        self.assertEqual(shape.face_ranges(), [(0, 0, 0, 1), (1, 1, 1, 3), (3, None, 4, 2), (4, 1, 6, 1)])
        self.assertEqual([strip_ns(p) for p in shape.instance_paths],
                         ["|Root_Node_|node_2|_3d_child|_3d_childShape"])
        fn = mesh_fn("s:_3d_childShape")
        pts = fn.getPoints(om.MSpace.kObject)

        def face_pts(f):
            return [tuple(round(pts[v][k], 4) for k in range(3)) for v in fn.getPolygonVertices(f)]
        self.assertEqual(face_pts(1), [(0, 0, 100), (100, 0, 100), (0, 100, 100)])  # strip tri 0
        self.assertEqual(face_pts(2), [(100, 0, 100), (100, 100, 100), (0, 100, 100)])  # strip tri 1
        self.assertEqual(face_pts(6), [(0, 0, 0), (100, 100, 0), (0, 100, 0)])  # prim E shares prim A verts
        counts, _ = fn.getAssignedUVs("map1")
        self.assertEqual(list(counts), [3, 0, 0, 0, 0, 0, 3])
        # only prim B (vertices 4..8) has NORMAL: those are locked, the others are Maya's own
        locked = {}
        for f in range(fn.numPolygons):
            for corner, vid in enumerate(fn.getPolygonVertices(f)):
                nid = fn.getFaceNormalIds(f)[corner]
                locked[vid] = fn.isNormalLocked(nid)
        self.assertEqual(sorted(v for v, l in locked.items() if l), [4, 5, 6, 7, 8])
        n = fn.getFaceVertexNormal(1, 4)
        self.assertVecAlmost((n.x, n.y, n.z), (0, 0, 1))
        # LINES primitive skipped with a collected warning
        self.assertTrue(any("LINES" in w for w in result.reporter.warnings))
        # per-face materials; the primitive without a material stays on initialShadingGroup
        path = shape.instance_paths[0]
        self.assertTrue(cmds.sets(path + ".f[0]", isMember="s:matASG"))
        self.assertTrue(cmds.sets(path + ".f[1:3]", isMember="s:matBSG"))
        self.assertTrue(cmds.sets(path + ".f[4:5]", isMember="initialShadingGroup"))
        self.assertTrue(cmds.sets(path + ".f[6]", isMember="s:matBSG"))

    def test_scale_option_baked(self):
        import_file(scene_files()[0], "s", options="scale=0.01")
        self.assertVecAlmost(cmds.getAttr("s:Root_Node_.translate")[0], (1, 2, 3))
        self.assertVecAlmost(cmds.getAttr("s:Root_Node_.scale")[0], (1, 1, 1))
        fn = mesh_fn("s:Cube|s:CubeShape")
        self.assertVecAlmost(tuple(fn.getPoint(0))[:3], (-0.5, -0.5, 0.5))

    def test_bad_option_warns_and_defaults(self):
        import_file(scene_files()[0], "s", options="scale=abc;bogus=1")
        warnings = importer.last_result().reporter.warnings
        self.assertTrue(any("scale" in w for w in warnings), warnings)
        self.assertTrue(any("bogus" in w for w in warnings), warnings)
        self.assertVecAlmost(cmds.getAttr("s:Root_Node_.translate")[0], (100, 200, 300))

    def test_sparse_accessors(self):
        path = fixtures.sparse_builder().write_gltf(os.path.join(fixture_dir(), "sparse.gltf"), embed=True)
        import_file(path, "sp")
        fn = mesh_fn("sp:SparseNodeShape")
        self.assertEqual((fn.numVertices, fn.numPolygons), (6, 2))
        pts = [tuple(round(p[k], 4) for k in range(3)) for p in fn.getPoints()]
        self.assertEqual(pts, [(0, 0, 0), (200, 0, 0), (0, 0, 300), (0, 0, 0), (0, 400, 0), (0, 0, 400)])

    def test_instanced_mesh(self):
        import_file(scene_files()[0], "s")
        parents = cmds.listRelatives("s:Cube|s:CubeShape", allParents=True)
        self.assertEqual(sorted(parents), ["s:Cube", "s:Cube_3"])
        self.assertEqual(len(cmds.ls(type="mesh", long=True)), 2)  # CubeShape + _3d_childShape
        self.assertEqual(len(cmds.ls(type="mesh", allPaths=True)), 3)
        result = importer.last_result()
        cube = result.shape_for_mesh(0)[0]
        self.assertEqual(len(cube.instance_paths), 2)
        for p in cube.instance_paths:
            self.assertTrue(cmds.sets(p, isMember="s:matASG"), p)

    def test_deterministic_names(self):
        gltf, glb = scene_files()
        import_file(gltf, "a")
        import_file(gltf, "b")
        import_file(glb, "c")
        names = {}
        for ns in ("a", "b", "c"):
            names[ns] = sorted(strip_ns(n) for n in cmds.ls(ns + ":*", long=True, dag=True))
        self.assertTrue(names["a"])
        self.assertEqual(names["a"], names["b"])
        self.assertEqual(names["a"], names["c"])

    def test_z_up_scene(self):
        cmds.upAxis(axis="z", rotateView=False)
        import_file(scene_files()[0], "z")
        m = cmds.getAttr("z:Root_Node_.offsetParentMatrix")
        self.assertVecAlmost(m, [1, 0, 0, 0, 0, 0, 1, 0, 0, -1, 0, 0, 0, 0, 0, 1])
        # channels stay the glTF values; the up-axis rotation sits in offsetParentMatrix
        self.assertVecAlmost(cmds.getAttr("z:Root_Node_.translate")[0], (100, 200, 300))
        self.assertVecAlmost(cmds.xform("z:Root_Node_", q=True, ws=True, t=True), (100, -300, 200))
        self.assertVecAlmost(cmds.getAttr("z:Cube.offsetParentMatrix"),
                             [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1])

    def test_unsupported_required_extension_fails_cleanly(self):
        path = fixtures.unsupported_builder().write_glb(os.path.join(fixture_dir(), "draco.glb"))
        before = cmds.ls(assemblies=True)
        with self.assertRaises(RuntimeError) as ctx:
            import_file(path, "bad")
        self.assertIn("KHR_draco_mesh_compression", str(ctx.exception))
        self.assertEqual(cmds.ls(assemblies=True), before)

    def test_corrupt_glb_fails_cleanly(self):
        path = os.path.join(fixture_dir(), "trunc.glb")
        data = fixtures.sparse_builder().glb_bytes()
        with open(path, "wb") as f:
            f.write(data[:-10])
        before = cmds.ls(assemblies=True)
        with self.assertRaises(RuntimeError) as ctx:
            import_file(path, "bad")
        self.assertIn("truncated", str(ctx.exception))
        self.assertEqual(cmds.ls(assemblies=True), before)

    def test_build_failure_leaves_no_nodes(self):
        original = importer.create_mesh_shape

        def boom(md, parent, name, reporter):
            if md.mesh_index == 1:
                raise RuntimeError("synthetic build failure")
            return original(md, parent, name, reporter)
        importer.create_mesh_shape = boom
        try:
            before = cmds.ls(assemblies=True)
            with self.assertRaises(RuntimeError):
                import_file(scene_files()[0], "boom")
            self.assertEqual(cmds.ls(assemblies=True), before)
            self.assertEqual(cmds.ls("boom:*"), [])
        finally:
            importer.create_mesh_shape = original


def _face_of_vertex(fn, vid):
    it = om.MItMeshVertex(fn.dagPath())
    it.setIndex(vid)
    return it.getConnectedFaces()[0]


class TestReference(MayaTestCase):
    def test_reference_nodes_owned_and_namespaced(self):
        for path, ns in zip(scene_files(), ("rg", "rb")):
            added = new_assemblies(reference_file, path, ns)
            self.assertEqual(sorted(strip_ns(a) for a in added), ["|Root_Node_", "|Second_Root"])
            ref_node = cmds.referenceQuery(path, referenceNode=True)
            owned = set(cmds.referenceQuery(ref_node, nodes=True, dagPath=True))
            dag = cmds.ls(ns + ":*", dag=True, long=True)
            self.assertTrue(dag)
            for n in cmds.ls(added, dag=True, long=True):
                self.assertTrue(cmds.referenceQuery(n, isNodeReferenced=True), n)
                self.assertTrue(n.split("|")[-1].startswith(ns + ":"), n)
            self.assertIn("%s:CubeShape" % ns, {o.split("|")[-1] for o in owned})
            for o in owned:  # nothing the reference owns lives outside its namespace
                self.assertTrue(o.split("|")[-1].startswith(ns + ":"), o)
            owned_uuids = set(cmds.ls(list(owned), uuid=True))
            added_uuids = set(cmds.ls(cmds.ls(added, dag=True, long=True), uuid=True))
            self.assertTrue(added_uuids and added_uuids <= owned_uuids)
            self.assertEqual(cmds.referenceQuery(ref_node, namespace=True), ":" + ns)

    def test_reference_edits_survive_save_and_reopen(self):
        gltf, glb = scene_files()
        reference_file(gltf, "r1")
        reference_file(glb, "r2")
        cmds.setAttr("r1:Cube_3.translateX", 7.5)
        cmds.setAttr("r2:Root_Node_.rotateY", 33.0)
        cmds.setAttr("r1:_3d_child.visibility", 0)
        before = sorted(cmds.ls("r1:*", "r2:*", long=True, dag=True))
        ma = os.path.join(fixture_dir(), "refscene.ma")
        cmds.file(rename=ma)
        cmds.file(save=True, type="mayaAscii", force=True)
        cmds.file(new=True, force=True)
        self.assertFalse(cmds.ls("r1:*"))
        cmds.file(ma, open=True, force=True)
        self.assertAlmostEqual(cmds.getAttr("r1:Cube_3.translateX"), 7.5)
        self.assertAlmostEqual(cmds.getAttr("r2:Root_Node_.rotateY"), 33.0)
        self.assertEqual(cmds.getAttr("r1:_3d_child.visibility"), False)
        self.assertEqual(sorted(cmds.ls("r1:*", "r2:*", long=True, dag=True)), before)
        self.assertTrue(cmds.referenceQuery("r1:Cube_3", isNodeReferenced=True))
        self.assertTrue(cmds.sets("r1:Cube|r1:CubeShape", isMember="r1:matASG"))
        edits = cmds.referenceQuery(cmds.referenceQuery(gltf, referenceNode=True), editStrings=True)
        self.assertTrue(any("r1:Cube_3.translate" in e and "7.5" in e for e in edits), edits)

    def test_reference_unsupported_extension(self):
        # Maya does not raise when a reference fails to load (same as a missing file): it
        # prints the translator error, keeps an empty reference node, adds no nodes.
        path = fixtures.unsupported_builder("EXT_meshopt_compression").write_gltf(
            os.path.join(fixture_dir(), "meshopt.gltf"))
        before = cmds.ls(assemblies=True)
        try:
            reference_file(path, "bad")
        except RuntimeError as exc:  # accept either behaviour, but it must name the extension
            self.assertIn("EXT_meshopt_compression", str(exc))
        self.assertEqual(cmds.ls(assemblies=True), before)
        err = importer.last_error()
        self.assertIsNotNone(err)
        self.assertIn("EXT_meshopt_compression", err[1])
        self.assertEqual(cmds.ls("bad:*", dag=True), [])
