"""Skins, morph targets and animation preparation (milestone 3). No Maya."""

import math
import shutil
import tempfile
import unittest

import fixtures
import gltf_eval

from i3d_gltf.core import Reporter, load, parse_options, prepare_import
from i3d_gltf.core.animation import (Sampler, closest_euler, eulers_from_quats, quat_to_euler_xyz,
                                     select_animation, slerp)
from i3d_gltf.core.options import default_options_string


def _euler_to_quat_xyz(e):
    """Independent: q = qz * qy * qx (X applied first)."""
    def axis(i, a):
        q = [0.0, 0.0, 0.0, math.cos(a / 2)]
        q[i] = math.sin(a / 2)
        return q

    def mul(a, b):
        ax, ay, az, aw = a
        bx, by, bz, bw = b
        return [aw * bx + ax * bw + ay * bz - az * by, aw * by - ax * bz + ay * bw + az * bx,
                aw * bz + ax * by - ay * bx + az * bw, aw * bw - ax * bx - ay * by - az * bz]
    return mul(axis(2, e[2]), mul(axis(1, e[1]), axis(0, e[0])))


def _same_rotation(a, b, places=9):
    d = abs(sum(x * y for x, y in zip(a, b)))
    return abs(d - 1.0) < 10 ** -places


class _Tmp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="core_anim_")
        cls.rig = fixtures.write_rig(cls.dir)
        cls.interp = fixtures.write_interp(cls.dir)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def prepare(self, path, options="", fps=24.0):
        r = Reporter()
        return prepare_import(path, parse_options(options, r), r, fps=fps), r


class TestOptions(unittest.TestCase):
    def test_new_options(self):
        o = parse_options("")
        self.assertEqual((o.animation, o.animSampling, o.timeRange), ("first", "keys", True))
        self.assertTrue(default_options_string().endswith(";animation=first;animSampling=keys;timeRange=1"))
        r = Reporter()
        o = parse_options("animSampling=sometimes;timeRange=maybe;animation=Walk", r)
        self.assertEqual((o.animation, o.animSampling, o.timeRange), ("Walk", "keys", True))
        self.assertEqual(len(r.warnings), 2)


class TestEuler(unittest.TestCase):
    def test_quat_to_euler_roundtrip(self):
        for e in [(0.1, 0.2, 0.3), (-2.0, 1.0, 3.0), (0.5, math.pi / 2, 0.2), (1.0, -math.pi / 2, -0.4),
                  (3.0, 0.0, -3.0)]:
            q = _euler_to_quat_xyz(e)
            back = _euler_to_quat_xyz(quat_to_euler_xyz(q))
            self.assertTrue(_same_rotation(q, back, 7), (e, quat_to_euler_xyz(q)))

    def test_known_values(self):
        h = math.sqrt(0.5)
        self.assertEqual([round(math.degrees(a), 6) for a in quat_to_euler_xyz((0, h, 0, h))], [0, 90, 0])
        self.assertEqual([round(math.degrees(a), 6) for a in quat_to_euler_xyz((h, 0, 0, h))], [90, 0, 0])

    def test_continuity_filter(self):
        prev = (0.0, 0.0, math.radians(170))
        e = closest_euler((0.0, 0.0, math.radians(-170)), prev)
        self.assertAlmostEqual(math.degrees(e[2]), 190.0)
        # flipped solution (x+180, 180-y, z+180) when that is closer
        e = closest_euler((math.pi, 0.1, math.pi), (0.0, math.pi - 0.1, 0.0))
        self.assertTrue(_same_rotation(_euler_to_quat_xyz(e), _euler_to_quat_xyz((math.pi, 0.1, math.pi))))
        self.assertLess(sum((a - b) ** 2 for a, b in zip(e, (0.0, math.pi - 0.1, 0.0))), 1e-9)
        # a long sequence turning about Z never jumps
        quats = [_euler_to_quat_xyz((0, 0, math.radians(a))) for a in range(0, 1080, 40)]
        zs = [math.degrees(e[2]) for e in eulers_from_quats(quats)]
        self.assertTrue(all(abs(b - a - 40) < 1e-6 for a, b in zip(zs, zs[1:])), zs)

    def test_slerp_short_path(self):
        a = _euler_to_quat_xyz((0, 0, math.radians(170)))
        b = [-c for c in _euler_to_quat_xyz((0, 0, math.radians(-170)))]
        mid = slerp(a, b, 0.5)
        self.assertTrue(_same_rotation(mid, _euler_to_quat_xyz((0, 0, math.pi))))


class TestSelection(_Tmp):
    def test_select(self):
        doc = load(self.rig)
        r = Reporter()
        self.assertEqual(select_animation(doc, "first", r), 0)
        self.assertIsNone(select_animation(doc, "none", r))
        self.assertEqual(select_animation(doc, "1", r), 1)
        self.assertEqual(select_animation(doc, "Idle", r), 1)
        self.assertEqual(r.warnings, [])
        self.assertEqual(select_animation(doc, "7", r), 0)
        self.assertEqual(select_animation(doc, "Nope", r), 0)
        self.assertEqual(len(r.warnings), 2)


class TestScenePlan(_Tmp):
    def test_joints_and_skinned_transforms(self):
        prep, _ = self.prepare(self.rig)
        plan = prep.plan
        by_name = dict((n.name, n) for n in plan.nodes.values())
        self.assertEqual(sorted(plan.nodes[i].name for i in plan.joints), sorted(fixtures.RIG_JOINTS))
        self.assertTrue(by_name["Twist"].joint)          # between joints, in no skin
        self.assertFalse(by_name["Armature"].joint)      # above the skeleton
        self.assertTrue(by_name["Body"].skinned_leaf)
        self.assertFalse(by_name["Body3"].skinned_leaf)  # animated and has a child
        shapes = dict((s.name, s) for s in plan.shapes)
        self.assertEqual(shapes["Body3Shape"].skin_transform, "Body3_skinned")
        self.assertIsNone(shapes["BodyShape"].skin_transform)
        # morphed meshes are never instanced: MorphA and MorphB share a mesh but not a shape
        self.assertIn("MorphAShape", shapes)
        self.assertIn("MorphBShape", shapes)


class TestDeformers(_Tmp):
    def test_skin_weights(self):
        prep, r = self.prepare(self.rig, "scale=0.5")
        shapes = dict((s.name, s.index) for s in prep.plan.shapes)
        sp = prep.skins[shapes["BodyShape"]]
        self.assertEqual(sp.max_influences, 6)  # JOINTS_0 + JOINTS_1
        self.assertEqual(sp.num_vertices, 22)
        for v in range(sp.num_vertices):
            total = sum(sp.weights[sp.offsets[v]:sp.offsets[v + 1]])
            self.assertAlmostEqual(total, 1.0, 12)
        doc = load(self.rig)
        # inverse bind matrix translation is unit-scaled (x100 x 0.5)
        ev = gltf_eval.Evaluator(self.rig)
        ibm = ev.reader.read_floats(doc.skins[0].inverse_bind_matrices).values
        self.assertAlmostEqual(sp.bind_pre_matrices[1][13], ibm[16 + 13] * 50.0, 4)
        self.assertAlmostEqual(sp.bind_pre_matrices[1][0], ibm[16], 6)
        self.assertEqual(prep.skins[shapes["SkinMorphShape"]].bind_pre_matrices[0],
                         [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0])

    def test_morph_targets(self):
        prep, r = self.prepare(self.rig)
        shapes = dict((s.name, s.index) for s in prep.plan.shapes)
        a = prep.morphs[shapes["MorphAShape"]]
        self.assertEqual([t.name for t in a.targets], ["Smile", "Frown_", "Smile_2"])
        self.assertEqual(a.weights, [0.5, 0.0, 0.25])                         # mesh weights
        self.assertEqual(prep.morphs[shapes["MorphBShape"]].weights, [1.0, 0.5, 0.0])  # node override
        c = prep.morphs[shapes["MorphCShape"]]
        self.assertEqual([t.name for t in c.targets], ["target_0", "target_1"])
        self.assertEqual(c.weights, [0.0, 0.0])
        sparse = a.targets[1]  # only vertices 2 and 6 move
        self.assertEqual(list(sparse.vertices), [2, 6])
        self.assertEqual([round(x, 4) for x in sparse.deltas], [0, 0, 30, 20, 0, 0])
        self.assertEqual(len(a.targets[0].vertices), 9)
        self.assertEqual(list(a.targets[2].vertices), [4])


class TestAnimationPlan(_Tmp):
    def test_keys(self):
        prep, r = self.prepare(self.rig, "scale=2")
        anim = prep.animation
        self.assertEqual((anim.index, anim.name, anim.baked), (0, "Main", False))
        self.assertEqual((anim.start, anim.end), (0.0, 1.25))
        names = [c.name_base for c in anim.curves]
        self.assertIn("J1_rotateX", names)
        self.assertIn("MorphA_Smile", names)
        self.assertIn("MorphA_Frown_", names)
        self.assertIn("MorphC_target_1", names)
        by = dict((c.name_base, c) for c in anim.curves)
        self.assertAlmostEqual(by["J1_rotateX"].times[2], 0.71, 6)
        self.assertEqual(by["J2_translateY"].interpolation, "STEP")
        self.assertAlmostEqual(by["J2_translateY"].values[1], 0.25 * 200.0, 4)   # unit scale x option
        self.assertEqual(by["Armature_translateY"].interpolation, "CUBICSPLINE")
        self.assertAlmostEqual(by["Armature_translateY"].out_slopes[0], -0.2 * 200.0, 4)
        self.assertAlmostEqual(by["J3_scaleX"].in_slopes[0], 0.5, 6)              # scale: not unit-scaled
        # J1 rotation keys: continuity filtered (the sign-flipped key does not jump)
        zs = [math.degrees(v) for v in by["J1_rotateZ"].values]
        self.assertEqual([round(z, 4) for z in zs], [10, 120, 260, 420])
        self.assertEqual(len(anim.slerp_groups), 3)  # J1, Twist, Holder (LINEAR rotations)

    def test_bake(self):
        prep, r = self.prepare(self.interp, "animSampling=bake", fps=24.0)
        anim = prep.animation
        self.assertTrue(anim.baked)
        c = anim.curves[0]
        self.assertEqual(len(c.times), 31)  # frames 0..30 cover 0..1.25 s
        self.assertAlmostEqual(c.times[1], 1 / 24.0)
        self.assertEqual(anim.approximated, [])
        self.assertTrue(all(c.interpolation in ("STEP", "LINEAR") for c in anim.curves))
        # baked values equal the reference evaluator at the frames
        ev = gltf_eval.Evaluator(self.interp)
        node = [cc for cc in anim.curves if cc.attr == "scaleY"][2]  # CUBICSPLINE scale
        for k in (3, 11, 25):
            t = node.times[k]
            ref = ev.channels[(node.node, "scale")].sample(t)
            self.assertAlmostEqual(node.values[k], ref[1], 6)

    def test_cubic_rotation_warns(self):
        prep, r = self.prepare(self.interp)
        self.assertEqual(len(prep.animation.approximated), 1)
        self.assertTrue(any("animSampling=bake" in w for w in r.warnings))

    def test_none(self):
        prep, r = self.prepare(self.rig, "animation=none")
        self.assertIsNone(prep.animation)

    def test_sampler_matches_reference(self):
        """core.animation.Sampler and the test evaluator implement the spec independently."""
        ev = gltf_eval.Evaluator(self.interp)
        for (node, path), ch in ev.channels.items():
            vals = [tuple(v) for v in ch.v]
            smp = Sampler(ch.times, vals, ch.interp, [tuple(v) for v in getattr(ch, "a", vals)],
                          [tuple(v) for v in getattr(ch, "b", vals)], rotation=path == "rotation")
            for t in (0.1, 0.5, 0.9, 1.1):
                a = smp.evaluate(t)
                b = ch.sample(t)
                if path == "rotation":
                    self.assertTrue(_same_rotation(a, b, 9), (path, ch.interp, t))
                else:
                    for x, y in zip(a, b):
                        self.assertAlmostEqual(x, y, 9)


if __name__ == "__main__":
    unittest.main()
