"""Skins, morph targets, animation and the host report (milestone 3).
Run with mayapy via tests/run_suite.py --maya.

The main correctness check compares deformed world-space vertex positions in Maya with
the independent reference evaluator (tests/gltf_eval.py) at many times, including
between keys.
"""

import json
import math
import os
import tempfile

import maya.api.OpenMaya as om
import maya.cmds as cmds

import fixtures
import gltf_eval
import maya_compare
from maya_helpers import MayaTestCase, fixture_dir, import_file, reference_file

from i3d_gltf.maya import importer

TOL = 1e-3  # cm; Maya stores points as floats, the fixtures span ~10 m


def strip(name):
    return "|".join(p.split(":")[-1] for p in name.split("|"))


_paths = {}


def rig_path(glb=True):
    key = ("rig", glb)
    if key not in _paths:
        _paths[key] = fixtures.write_rig(tempfile.mkdtemp(prefix="rig_", dir=fixture_dir()), glb)
    return _paths[key]


def interp_path():
    if "interp" not in _paths:
        _paths["interp"] = fixtures.write_interp(tempfile.mkdtemp(prefix="interp_", dir=fixture_dir()))
    return _paths["interp"]


def node_index(result, name):
    for i, n in result.prepared.plan.nodes.items():
        if n.name == name:
            return i
    raise KeyError(name)


def world_points(path):
    sel = om.MSelectionList()
    sel.add(path)
    return om.MFnMesh(sel.getDagPath(0)).getPoints(om.MSpace.kWorld)


def anim_curves(ns):
    return [c for c in cmds.ls(ns + ":*") if cmds.nodeType(c).startswith("animCurve")]


class TestSkin(MayaTestCase):
    def test_joints(self):
        import_file(rig_path(), "s")
        for j in fixtures.RIG_JOINTS:
            self.assertEqual(cmds.nodeType("s:" + j), "joint", j)
            self.assertEqual(cmds.getAttr("s:%s.jointOrient" % j)[0], (0.0, 0.0, 0.0))
            self.assertFalse(cmds.getAttr("s:%s.segmentScaleCompensate" % j))
        for t in ("Rig", "Armature", "Holder"):
            self.assertEqual(cmds.nodeType("s:" + t), "transform")
        self.assertEqual(cmds.listRelatives("s:J3", parent=True), ["s:Twist"])
        self.assertEqual(cmds.listRelatives("s:J0", parent=True), ["s:Armature"])
        # rotation stays in the rotate channels (J1 rest rotation 10 degrees about Z)
        cmds.currentTime(0)
        self.assertAlmostEqual(cmds.getAttr("s:Twist.rotateY"), 20.0, 4)

    def test_skin_clusters(self):
        import_file(rig_path(), "s", options="animation=none")
        res = importer.last_result()
        clusters = res.skin_clusters
        self.assertEqual(sorted(strip(c) for c in clusters.values()),
                         ["Body2_skinCluster", "Body3_skinCluster", "Body_skinCluster", "SkinMorph_skinCluster"])
        sk = "s:Body_skinCluster"
        self.assertEqual(cmds.getAttr(sk + ".skinningMethod"), 0)
        self.assertTrue(cmds.getAttr(sk + ".deformUserNormals"))
        self.assertEqual(cmds.getAttr(sk + ".normalizeWeights"), 1)
        self.assertEqual([strip(i) for i in cmds.skinCluster(sk, q=True, influence=True)],
                         ["J0", "J1", "J2", "J3", "J4", "J5"])
        # two skins share joints (other order)
        self.assertEqual([strip(i) for i in cmds.skinCluster("s:Body2_skinCluster", q=True, influence=True)],
                         ["J5", "J3", "J1", "J0", "J2", "J4"])
        self.assertEqual(cmds.ls("s:*", type="dagPose"), [])
        # bind pre-matrix = file inverseBindMatrices with translation x100
        ev = gltf_eval.Evaluator(rig_path())
        ibm = ev.reader.read_floats(ev.doc.skins[0].inverse_bind_matrices).values
        bpm = cmds.getAttr(sk + ".bindPreMatrix[2]")
        for k in range(16):
            self.assertAlmostEqual(bpm[k], ibm[32 + k] * (100.0 if k in (12, 13, 14) else 1.0), 4)
        # more than 4 influences, normalised (vertex 7's file weights sum to 0.9)
        for v in (7, 10):
            vals = cmds.skinPercent(sk, "s:BodyShape.vtx[%d]" % v, q=True, value=True)
            self.assertEqual(sum(1 for w in vals if w > 1e-9), 6)
            self.assertAlmostEqual(sum(vals), 1.0, 6)

    def test_matches_reference(self):
        import_file(rig_path(), "s")
        res = importer.last_result()
        ev = gltf_eval.Evaluator(rig_path())
        r = maya_compare.compare(res, ev, maya_compare.sample_times(ev))
        self.assertGreater(r["points"], 1000)
        self.assertLess(r["max_error"], TOL, r["where"])

    def test_matches_reference_gltf_scaled_z_up(self):
        cmds.upAxis(axis="z", rotateView=False)
        import_file(rig_path(glb=False), "z", options="scale=0.01")
        res = importer.last_result()
        ev = gltf_eval.Evaluator(rig_path(glb=False))
        r = maya_compare.compare(res, ev, maya_compare.sample_times(ev), z_up=True)
        self.assertLess(r["max_error"], TOL * 0.01, r["where"])
        # translation keys are scaled: J2 translateY keys 0.2 m -> 0.2 cm at scale 0.01
        self.assertAlmostEqual(cmds.keyframe("z:J2_translateY", q=True, valueChange=True)[0], 0.2, 6)

    def test_skinned_mesh_ignores_its_transform(self):
        import_file(rig_path(), "s", options="animation=none")
        # Body: leaf with its own transform in the file -> identity, not inherited
        self.assertEqual(cmds.getAttr("s:Body.translate")[0], (0.0, 0.0, 0.0))
        self.assertFalse(cmds.getAttr("s:Body.inheritsTransform"))
        ident = [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0]
        self.assertEqual(cmds.getAttr("s:Body.worldMatrix[0]"), ident)
        # Body3 is animated and has a child: the shape is under an extra identity transform
        self.assertEqual(sorted(cmds.listRelatives("s:Body3", children=True)), ["s:Body3_skinned", "s:Prop"])
        self.assertFalse(cmds.getAttr("s:Body3_skinned.inheritsTransform"))
        self.assertEqual(cmds.listRelatives("s:Body3_skinned", shapes=True, noIntermediate=True), ["s:Body3Shape"])
        before = [om.MPoint(p) for p in world_points("s:BodyShape")]
        # moving the mesh's own parent does nothing (glTF ignores the skinned node's transform)
        cmds.setAttr("s:Holder.rotateX", 75)
        cmds.setAttr("s:Holder.translateY", 300)
        after = world_points("s:BodyShape")
        self.assertTrue(all(a.distanceTo(b) < 1e-4 for a, b in zip(before, after)))
        # moving the skeleton root moves the mesh exactly once
        cmds.setAttr("s:Rig.translateX", cmds.getAttr("s:Rig.translateX") + 250)
        after = world_points("s:BodyShape")
        for a, b in zip(before, after):
            self.assertAlmostEqual(b.x - a.x, 250.0, 3)
            self.assertAlmostEqual(b.y - a.y, 0.0, 3)
            self.assertAlmostEqual(b.z - a.z, 0.0, 3)

    def test_user_normals_follow_joints(self):
        import_file(rig_path(), "s", options="animation=none")
        path = "s:SkinMorphShape"
        sel = om.MSelectionList()
        sel.add(path)
        fn = om.MFnMesh(sel.getDagPath(0))
        n0 = fn.getVertexNormal(0, True, om.MSpace.kWorld)
        w0 = om.MMatrix(cmds.getAttr("s:J0.worldMatrix[0]"))
        cmds.setAttr("s:J0.rotateZ", 90)  # vertex 0 is fully weighted to J0
        w1 = om.MMatrix(cmds.getAttr("s:J0.worldMatrix[0]"))
        n1 = om.MFnMesh(sel.getDagPath(0)).getVertexNormal(0, True, om.MSpace.kWorld)
        expected = (n0 * w0.inverse() * w1).normal()
        self.assertTrue(fn.isNormalLocked(fn.getFaceNormalIds(0)[0]))
        self.assertAlmostEqual((n1.normal() - expected).length(), 0.0, 4)


class TestMorph(MayaTestCase):
    def test_blendshapes(self):
        import_file(rig_path(), "m", options="animation=none")
        res = importer.last_result()
        self.assertEqual(len(res.blend_shapes), 4)
        bs = "m:MorphA_blendShape"
        self.assertEqual(cmds.nodeType(bs), "blendShape")
        aliases = cmds.aliasAttr(bs, q=True)
        self.assertEqual(aliases[0::2], ["Smile", "Frown_", "Smile_2"])
        self.assertEqual(aliases[1::2], ["weight[0]", "weight[1]", "weight[2]"])
        self.assertEqual([round(cmds.getAttr(bs + "." + a), 6) for a in ("Smile", "Frown_", "Smile_2")],
                         [0.5, 0.0, 0.25])  # mesh default weights
        self.assertEqual([round(cmds.getAttr("m:MorphB_blendShape.w[%d]" % i), 6) for i in range(3)],
                         [1.0, 0.5, 0.0])   # node weights override
        self.assertEqual(cmds.aliasAttr("m:MorphC_blendShape", q=True)[0::2], ["target_0", "target_1"])
        # no target meshes: only the imported shapes exist
        self.assertEqual(sorted(strip(m) for m in cmds.ls("m:*", type="mesh") if not cmds.getAttr(m + ".io")),
                         sorted(["BodyShape", "Body2Shape", "Body3Shape", "PropShape", "SkinMorphShape",
                                 "MorphAShape", "MorphBShape", "MorphCShape"]))

    def test_sparse_target_values(self):
        import_file(rig_path(), "m", options="animation=none")
        bs = "m:MorphA_blendShape"
        for i, w in enumerate((0.0, 1.0, 0.0)):
            cmds.setAttr("%s.w[%d]" % (bs, i), w)
        self.assertEqual([round(x, 3) for x in cmds.pointPosition("m:MorphAShape.vtx[2]", local=True)], [100, 0, 30])
        self.assertEqual([round(x, 3) for x in cmds.pointPosition("m:MorphAShape.vtx[6]", local=True)], [20, 100, 0])
        self.assertEqual([round(x, 3) for x in cmds.pointPosition("m:MorphAShape.vtx[4]", local=True)], [50, 50, 0])

    def test_blendshape_before_skin(self):
        import_file(rig_path(), "m")
        hist = [strip(h) for h in cmds.listHistory("m:SkinMorphShape", pruneDagObjects=True, interestLevel=1)
                if cmds.nodeType(h) in ("blendShape", "skinCluster")]
        self.assertEqual(hist, ["SkinMorph_skinCluster", "SkinMorph_blendShape"])  # skin is downstream


class TestAnimation(MayaTestCase):
    def _interp(self, ns="a", options=None):
        import_file(interp_path(), ns, options=options)
        return importer.last_result()

    def test_interpolation_modes(self):
        self._interp()
        for path, attr in (("translation", "translateX"), ("scale", "scaleY"), ("rotation", "rotateZ")):
            for interp, tangent in (("STEP", "step"), ("LINEAR", "linear"), ("CUBICSPLINE", "fixed")):
                curve = "a:Node_%s_%s_%s" % (path, interp, attr)
                self.assertTrue(cmds.objExists(curve), curve)
                self.assertEqual(set(cmds.keyTangent(curve, q=True, outTangentType=True)), {tangent}, curve)
                self.assertFalse(cmds.getAttr(curve + ".weightedTangents"), curve)
        # LINEAR rotation: Maya quaternion slerp; the others: plain Euler curves
        self.assertEqual(cmds.getAttr("a:Node_rotation_LINEAR_rotateX.rotationInterpolation"), 4)
        self.assertEqual(cmds.getAttr("a:Node_rotation_STEP_rotateX.rotationInterpolation"), 1)

    def test_interpolation_matches_reference(self):
        res = self._interp()
        ev = gltf_eval.Evaluator(interp_path())
        cubic_rot = node_index(res, "Node_rotation_CUBICSPLINE")
        times = maya_compare.sample_times(ev, count=11)
        r = maya_compare.compare(res, ev, times, skip_nodes=(cubic_rot,))
        self.assertLess(r["max_error"], TOL, r["where"])
        # CUBICSPLINE rotation: exact at the keys, approximate between (documented)
        r = maya_compare.compare(res, ev, fixtures.ANIM_TIMES)
        self.assertLess(r["max_error"], TOL, r["where"])
        self.assertTrue(any("animSampling=bake" in w for w in res.reporter.warnings))

    def test_linear_rotation_is_slerp_between_keys(self):
        res = self._interp()
        ev = gltf_eval.Evaluator(interp_path())
        node = node_index(res, "Node_rotation_LINEAR")
        ch = ev.channels[(node, "rotation")]
        worst = 0.0
        for k in range(200):
            t = 1.25 * k / 199.0
            maya_compare.set_time(t)
            r = [math.radians(a) for a in cmds.getAttr("a:Node_rotation_LINEAR.rotate")[0]]
            q = om.MEulerRotation(*r).asQuaternion()
            ref = ch.sample(t)
            d = min(1.0, abs(q.x * ref[0] + q.y * ref[1] + q.z * ref[2] + q.w * ref[3]))
            worst = max(worst, math.degrees(2 * math.acos(d)))
        self.assertLess(worst, 1e-3)

    def test_keys_at_file_times_any_frame_rate(self):
        cmds.currentUnit(time="ntsc")  # 30 fps: none of the key times is on a frame
        try:
            res = self._interp()
            times = cmds.keyframe("a:Node_translation_LINEAR_translateX", q=True, timeChange=True)
            # exactly the file's (float32) times, not snapped to frames
            self.assertEqual([round(t / 30.0, 6) for t in times], fixtures.ANIM_TIMES)
            self.assertNotEqual(times[1], round(times[1]))
            ev = gltf_eval.Evaluator(interp_path())
            r = maya_compare.compare(res, ev, [0.1, 0.5, 0.9], skip_nodes=(node_index(res, "Node_rotation_CUBICSPLINE"),))
            self.assertLess(r["max_error"], TOL, r["where"])
            self.assertEqual(res.time_range, (0, 38))  # 1.25 s at 30 fps = 37.5 -> 38
        finally:
            cmds.currentUnit(time="film")

    def test_bake(self):
        res = self._interp(options="animSampling=bake")
        self.assertEqual(cmds.keyframe("a:Node_rotation_CUBICSPLINE_rotateX", q=True, keyframeCount=True), 31)
        ev = gltf_eval.Evaluator(interp_path())
        frames = [f / 24.0 for f in range(0, 31, 3)]
        r = maya_compare.compare(res, ev, frames)  # every node, cubic rotation included, at frames
        self.assertLess(r["max_error"], TOL, r["where"])
        self.assertFalse(any("animSampling=bake" in w for w in res.reporter.warnings))

    def test_animation_option(self):
        import_file(rig_path(), "n", options="animation=none")
        self.assertEqual(anim_curves("n"), [])
        self.assertIsNone(importer.last_result().animation)
        for value in ("1", "Idle"):
            ns = "i" + value
            import_file(rig_path(), ns, options="animation=" + value)
            self.assertEqual(sorted(strip(c) for c in anim_curves(ns)),
                             ["J1_translateX", "J1_translateY", "J1_translateZ"])
            self.assertEqual(importer.last_result().animation.name, "Idle")
        import_file(rig_path(), "bad", options="animation=Walk")
        self.assertEqual(importer.last_result().animation.name, "Main")
        self.assertTrue(any("Walk" in w for w in importer.last_result().reporter.warnings))

    def test_time_range(self):
        cmds.playbackOptions(minTime=100, maxTime=200, animationStartTime=100, animationEndTime=200)
        import_file(rig_path(), "t1", options="timeRange=0")
        self.assertEqual((cmds.playbackOptions(q=True, min=True), cmds.playbackOptions(q=True, max=True)), (100, 200))
        reference_file(rig_path(), "t2")
        self.assertEqual((cmds.playbackOptions(q=True, min=True), cmds.playbackOptions(q=True, max=True)), (100, 200))
        import_file(rig_path(), "t3")
        self.assertEqual((cmds.playbackOptions(q=True, min=True), cmds.playbackOptions(q=True, max=True)), (0, 30))
        self.assertEqual((cmds.playbackOptions(q=True, ast=True), cmds.playbackOptions(q=True, aet=True)), (0, 30))
        import_file(rig_path(), "t4", options="animation=Idle")  # the last import wins
        self.assertEqual(cmds.playbackOptions(q=True, max=True), 48)

    def test_deterministic_names(self):
        import_file(rig_path(), "a")
        import_file(rig_path(), "b")
        names = {}
        for ns in ("a", "b"):
            names[ns] = sorted(strip(n) for n in cmds.ls(ns + ":*", long=True))
        short = set(n.split("|")[-1] for n in names["a"])
        self.assertEqual(names["a"], names["b"])
        for n in ("J1_rotateX", "Armature_translateY", "MorphA_Smile_2", "MorphA_blendShape", "Body_skinCluster",
                  "BodyShapeOrig"):
            self.assertIn(n, short)

    def test_build_failure_removes_everything(self):
        original = importer.build_skin

        def boom(*args, **kwargs):
            if args[3].startswith("Body3"):
                raise RuntimeError("synthetic skin failure")
            return original(*args, **kwargs)
        before = set(cmds.ls())
        importer.build_skin = boom
        try:
            with self.assertRaises(RuntimeError):
                import_file(rig_path(), "boom")
        finally:
            importer.build_skin = original
        self.assertEqual(set(cmds.ls()) - before, set())
        self.assertFalse(importer.last_report()["ok"])


class TestReferenceAnimation(MayaTestCase):
    def test_reference_owns_everything_and_survives(self):
        reference_file(rig_path(), "r1")
        res = importer.last_result()
        ref_node = cmds.referenceQuery(rig_path(), referenceNode=True)
        owned = set(cmds.referenceQuery(ref_node, nodes=True))
        made = (list(res.skin_clusters.values()) + list(res.blend_shapes.values()) + list(res.anim_curves.values())
                + ["r1:" + j for j in fixtures.RIG_JOINTS])
        self.assertEqual(len(res.anim_curves), 33)
        for n in made:
            self.assertTrue(n.startswith("r1:"), n)
            self.assertIn(n, owned)
        self.assertEqual([n for n in owned if not n.startswith("r1:")], [])
        maya_compare.set_time(0.5)
        rot = cmds.getAttr("r1:J1.rotate")[0]
        pts = [tuple(p)[:3] for p in world_points("r1:BodyShape")]

        # a second reference without node animation on morphs/J0 to carry edits
        other = os.path.join(os.path.dirname(rig_path()), "rig_copy.glb")
        if not os.path.exists(other):
            fixtures.rig_builder().write_glb(other)
        cmds.file(other, reference=True, type="IRONIC3D glTF", namespace="r2", ignoreVersion=True,
                  mergeNamespacesOnClash=False, options="animation=Idle")
        cmds.setAttr("r2:J0.rotateZ", 25.0)
        cmds.setAttr("r2:MorphA_blendShape.Smile", 0.9)
        ma = os.path.join(fixture_dir(), "animref.ma")
        cmds.file(rename=ma)
        cmds.file(save=True, type="mayaAscii", force=True)
        cmds.file(new=True, force=True)
        cmds.file(ma, open=True, force=True)
        self.assertAlmostEqual(cmds.getAttr("r2:J0.rotateZ"), 25.0)
        self.assertAlmostEqual(cmds.getAttr("r2:MorphA_blendShape.Smile"), 0.9)
        # animation plays after reopening
        maya_compare.set_time(0.5)
        self.assertEqual([round(a, 4) for a in cmds.getAttr("r1:J1.rotate")[0]], [round(a, 4) for a in rot])
        after = [tuple(p)[:3] for p in world_points("r1:BodyShape")]
        self.assertLess(max(math.sqrt(sum((a - b) ** 2 for a, b in zip(p, q))) for p, q in zip(pts, after)), 1e-3)
        maya_compare.set_time(0.0)
        y0 = cmds.getAttr("r2:J1.translateX")
        maya_compare.set_time(2.0)
        self.assertAlmostEqual(cmds.getAttr("r2:J1.translateX") - y0, 10.0, 4)
        self.assertTrue(cmds.referenceQuery("r1:Body_skinCluster", isNodeReferenced=True))


class TestReport(MayaTestCase):
    def _report(self):
        rep = importer.last_report()
        json.dumps(rep)
        self.assertEqual(sorted(rep), sorted(["ok", "path", "version", "error", "roots", "shaderType", "materials",
                                              "textures", "animation", "skins", "blendShapes", "warnings"]))
        return rep

    def test_none_before_any_load(self):
        saved = importer._last_attempt
        importer._last_attempt = None
        try:
            self.assertIsNone(importer.last_report())
        finally:
            importer._last_attempt = saved

    def test_import_animated_skinned(self):
        import_file(rig_path(), "rp")
        rep = self._report()
        self.assertTrue(rep["ok"])
        self.assertIsNone(rep["error"])
        self.assertEqual(rep["path"], os.path.abspath(rig_path()))
        self.assertEqual(rep["version"], "0.3.0")
        self.assertEqual(rep["roots"], ["|rp:Rig"])
        self.assertEqual(rep["animation"], {"name": "Main", "index": 0, "start": 0.0, "end": 1.25, "curves": 33})
        self.assertEqual((rep["skins"], rep["blendShapes"]), (4, 4))

    def test_materials_and_textures(self):
        from test_maya_materials import material_file
        import_file(material_file("bufferView"), "mt", options="textureDir=" + tempfile.mkdtemp(dir=fixture_dir()))
        rep = self._report()
        self.assertTrue(rep["ok"])
        self.assertIsNotNone(rep["shaderType"])
        names = [m["name"] for m in rep["materials"]]
        self.assertIn("Red", names)
        red = [m for m in rep["materials"] if m["name"] == "Red"][0]
        self.assertTrue(red["shader"].startswith("mt:") and red["shadingGroup"].startswith("mt:"))
        statuses = dict((t["image"], t["status"]) for t in rep["textures"])
        self.assertEqual(statuses.get("Webp"), "unsupported")
        self.assertEqual(statuses.get("Checker"), "linked")
        linked = [t for t in rep["textures"] if t["status"] == "linked"]
        self.assertTrue(all(t["embedded"] and t["node"].startswith("mt:") and os.path.isfile(t["path"])
                            for t in linked))
        self.assertIsNone([t for t in rep["textures"] if t["status"] == "unsupported"][0]["node"])
        self.assertTrue(any("WebP" in w for w in rep["warnings"]), rep["warnings"])

    def test_missing_external_texture(self):
        d = tempfile.mkdtemp(prefix="missing_", dir=fixture_dir())
        path = fixtures.materials_builder("uri").write_gltf(os.path.join(d, "mats.gltf"))  # images not written
        import_file(path, "mx")
        rep = self._report()
        self.assertTrue(rep["ok"])
        missing = [t for t in rep["textures"] if t["status"] == "missing"]
        self.assertTrue(missing)
        self.assertTrue(all(t["node"] and not t["embedded"] and t["path"].endswith(".png") for t in missing))

    def test_shader_none(self):
        import_file(rig_path(), "sn", options="shader=none")
        rep = self._report()
        self.assertEqual((rep["shaderType"], rep["materials"], rep["textures"]), (None, [], []))

    def test_reference_names_carry_namespace(self):
        reference_file(rig_path(), "rr")
        rep = self._report()
        self.assertTrue(rep["ok"])
        self.assertEqual(rep["roots"], ["|rr:Rig"])

    def test_failed_import_and_reference(self):
        path = fixtures.unsupported_builder().write_glb(os.path.join(fixture_dir(), "draco_rep.glb"))
        with self.assertRaises(RuntimeError):
            import_file(path, "bad")
        rep = self._report()
        self.assertFalse(rep["ok"])
        self.assertIn("KHR_draco_mesh_compression", rep["error"])
        self.assertEqual((rep["roots"], rep["materials"], rep["textures"], rep["animation"]), ([], [], [], None))
        import_file(rig_path(), "good")
        self.assertTrue(importer.last_report()["ok"])
        try:
            reference_file(path, "badref")
        except RuntimeError:
            pass
        rep = self._report()
        self.assertFalse(rep["ok"])
        self.assertIn("KHR_draco_mesh_compression", rep["error"])
        self.assertEqual(rep["path"], os.path.abspath(path))
