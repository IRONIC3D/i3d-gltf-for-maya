"""Options window string handling (the functions the dialog uses) and joint display radii.
No Maya."""

import unittest

from i3d_gltf.core.options import (IMPORT_OPTION_SPECS, OPTION_GROUPS, clean_text_value, default_options_string,
                                   dialog_string, dialog_values, parse_options)
from i3d_gltf.core.scene import NodePlan, joint_radii
from i3d_gltf.core.diagnostics import Reporter


class TestDialogStrings(unittest.TestCase):
    def test_every_option_has_ui_metadata(self):
        for s in IMPORT_OPTION_SPECS:
            self.assertIn(s.group, OPTION_GROUPS, s.name)
            self.assertTrue(s.label and s.label != s.name or s.name == "shader" or s.label, s.name)
            self.assertTrue(s.help, s.name)
            self.assertNotIn("\n", s.help)
            self.assertLessEqual(len(s.help), 110, s.name)
        names = [s.name for s in IMPORT_OPTION_SPECS]
        self.assertEqual(sorted(names), sorted(["scale", "shader", "textureDir", "importNormals", "importColors",
                                                "animation", "animSampling", "timeRange"]))
        tex = [s for s in IMPORT_OPTION_SPECS if s.name == "textureDir"][0]
        self.assertEqual(tex.widget, "folder")
        anim = [s for s in IMPORT_OPTION_SPECS if s.name == "animation"][0]
        self.assertEqual([p for p, _ in anim.presets], ["first", "none"])

    def test_defaults_round_trip(self):
        self.assertEqual(dialog_string(dialog_values("")), default_options_string())
        self.assertEqual(dialog_string(dialog_values(default_options_string())), default_options_string())

    def test_round_trip_posted_equals_read_back(self):
        text = ("scale=0.0254;importNormals=0;importColors=1;shader=openPBRSurface;textureDir=D:/tex dir/é;"
                "animation=Walk Cycle;animSampling=bake;timeRange=0")
        values = dialog_values(text)
        self.assertEqual(values["scale"], 0.0254)
        self.assertEqual(values["textureDir"], "D:/tex dir/é")
        self.assertEqual(values["animation"], "Walk Cycle")
        self.assertEqual(dialog_string(values), text)
        self.assertEqual(dialog_values(dialog_string(values)), values)

    def test_unknown_and_invalid_keys_ignored_silently(self):
        values = dialog_values("future=1;scale=-3;shader=bogus;junk;animSampling=keys")
        self.assertEqual(values, dialog_values(""))

    def test_last_occurrence_wins(self):
        # Maya prepends the translator defaults to stored options
        self.assertEqual(dialog_values(default_options_string() + ";scale=2")["scale"], 2.0)

    def test_posted_string_always_parses_cleanly(self):
        values = dialog_values("")
        values.update(scale=0.0, textureDir="C:\\a;b\\c ", shader="nope", animation=" none ")
        text = dialog_string(values)
        r = Reporter()
        o = parse_options(text, r)
        self.assertEqual(r.warnings, [])
        self.assertEqual(o.scale, 1.0)
        self.assertEqual(o.textureDir, "C:/ab/c")
        self.assertEqual(o.shader, "aiStandardSurface")
        self.assertEqual(o.animation, "none")
        self.assertEqual(clean_text_value(None), "")


def _node(i, parent, t=(0.0, 0.0, 0.0), s=(1.0, 1.0, 1.0), r=(0.0, 0.0, 0.0, 1.0), matrix=None):
    return NodePlan(index=i, name="n%d" % i, parent=parent, children=[], translation=list(t), rotation=list(r),
                    scale=list(s), matrix=matrix)


class TestJointRadius(unittest.TestCase):
    def chain(self, root_scale=1.0, step=10.0):
        # 0 Armature (not a joint) > 1 > 2 > 3 > 4 (joints, one bone of `step` each along Y)
        nodes = {0: _node(0, None, s=(root_scale,) * 3)}
        for i in range(1, 5):
            nodes[i] = _node(i, i - 1, t=(0.0, step if i > 1 else 0.0, 0.0))
        return nodes, [0, 1, 2, 3, 4], [1, 2, 3, 4]

    def test_relative_to_skeleton_size(self):
        nodes, order, joints = self.chain()
        radii = joint_radii(nodes, order, joints, 100.0)
        # extent 30 -> 0.3; median bone 10 -> cap 5; radius 0.3
        self.assertEqual(sorted(radii), joints)
        self.assertEqual(set(radii.values()), {0.3})
        big = joint_radii(*self.chain(step=1000.0), unit_scale_factor=100.0)
        self.assertEqual(set(big.values()), {30.0})

    def test_compensates_parent_scale(self):
        # Mixamo style: Armature scaled 0.01, joints in centimetres x100
        nodes, order, joints = self.chain(root_scale=0.01, step=1000.0)
        radii = joint_radii(nodes, order, joints, 100.0)
        # world extent 30 -> world radius 0.3, local = 0.3 / 0.01
        self.assertEqual(set(radii.values()), {30.0})

    def test_bone_length_cap_and_single_joint(self):
        nodes = {0: _node(0, None), 1: _node(1, 0, t=(100.0, 0, 0)), 2: _node(2, 0, t=(0, 1.0, 0)),
                 3: _node(3, 0, t=(0, 2.0, 0)), 9: _node(9, None)}
        radii = joint_radii(nodes, [0, 1, 2, 3, 9], [0, 1, 2, 3, 9], 100.0)
        # skeleton 0: extent ~100 -> 1.0, median bone (1, 2, 100) = 2 -> cap 1.0 ... min = 1.0
        self.assertAlmostEqual(radii[0], 1.0)
        self.assertEqual(radii[9], 1.0)  # single joint: 1 cm x scale option
        self.assertEqual(joint_radii({9: _node(9, None)}, [9], [9], 2.5)[9], 0.025)

    def test_deterministic(self):
        nodes, order, joints = self.chain(step=7.123456)
        a = joint_radii(nodes, order, joints, 100.0)
        b = joint_radii(nodes, list(order), list(reversed(joints)), 100.0)
        self.assertEqual(a, b)
        self.assertEqual(a[1], float("%.4g" % (7.123456 * 3 * 0.01)))


if __name__ == "__main__":
    unittest.main()
