"""Options, names, scene planning and mesh preparation tests. No Maya."""

import json
import os
import shutil
import tempfile
import unittest

import fixtures
from gltf_builder import UBYTE, USHORT, GltfBuilder

from i3d_gltf.core import GltfFormatError, Reporter, loads, parse_options, prepare_import
from i3d_gltf.core.accessors import AccessorReader
from i3d_gltf.core.meshprep import drop_degenerate, prepare_mesh, triangulate
from i3d_gltf.core.names import NameRegistry, sanitize
from i3d_gltf.core.options import default_options_string, format_options
from i3d_gltf.core.scene import build_scene_plan


class TestOptions(unittest.TestCase):
    def test_defaults(self):
        o = parse_options("")
        self.assertEqual((o.scale, o.importNormals, o.importColors), (1.0, True, True))
        self.assertEqual(default_options_string(), "scale=1.0;importNormals=1;importColors=1")

    def test_overrides_last_wins(self):
        # Maya prepends the default options string to the caller's options.
        o = parse_options("scale=1.0;importNormals=1;importColors=1;scale=0.01;importNormals=false;")
        self.assertEqual((o.scale, o.importNormals, o.importColors), (0.01, False, True))

    def test_bad_values_warn(self):
        r = Reporter()
        o = parse_options("scale=-1;importColors=maybe;junk;what=3", r)
        self.assertEqual((o.scale, o.importColors), (1.0, True))
        self.assertEqual(len(r.warnings), 4)

    def test_roundtrip(self):
        o = parse_options("scale=2.5;importColors=0")
        self.assertEqual(parse_options(format_options(o)).as_dict(), o.as_dict())


class TestNames(unittest.TestCase):
    def test_sanitize(self):
        self.assertEqual(sanitize("Root Node!", "x"), "Root_Node_")
        self.assertEqual(sanitize("3d-child", "x"), "_3d_child")
        self.assertEqual(sanitize("ns:name|path", "x"), "ns_name_path")
        self.assertEqual(sanitize("Ünïcode", "x"), "_n_code")
        self.assertEqual(sanitize("", "node_4"), "node_4")
        self.assertEqual(sanitize(None, "node_4"), "node_4")
        self.assertEqual(sanitize("!!!", "mesh_0"), "mesh_0")
        self.assertEqual(sanitize("ok_Name9", "x"), "ok_Name9")

    def test_registry(self):
        r = NameRegistry()
        self.assertEqual(r.claim("A", 0), "A")
        self.assertEqual(r.claim("A", 3), "A_3")
        self.assertEqual(r.claim("A_3", 5), "A_3_5")
        self.assertEqual(r.claim("A", 3), "A_3_1")


class TestScenePlan(unittest.TestCase):
    def plan(self, b, options=""):
        return build_scene_plan(loads(b.glb_bytes()), parse_options(options))

    def test_scene_plan(self):
        b, info = fixtures.scene_builder()
        plan = self.plan(b)
        self.assertEqual(plan.roots, [0, 6])
        self.assertEqual(plan.order, [0, 1, 2, 4, 3, 6])
        for i, name in enumerate(info["node_names"]):
            if name is None:
                self.assertNotIn(i, plan.nodes)
            else:
                self.assertEqual(plan.nodes[i].name, name)
        self.assertEqual(plan.nodes[0].translation, [100, 200, 300])
        self.assertEqual(plan.nodes[2].matrix[13], 50)
        self.assertEqual([(s.name, s.nodes) for s in plan.shapes], [("CubeShape", [1, 3]), ("_3d_childShape", [4])])

    def test_scale_option(self):
        b, _ = fixtures.scene_builder()
        plan = self.plan(b, "scale=0.5")
        self.assertEqual(plan.unit_scale, 50.0)
        self.assertEqual(plan.nodes[0].translation, [50, 100, 150])

    def test_names_deterministic(self):
        b, _ = fixtures.scene_builder()
        a = [(n.index, n.name) for n in self.plan(b).nodes.values()]
        c = [(n.index, n.name) for n in self.plan(b).nodes.values()]
        self.assertEqual(a, c)

    def test_no_instancing_between_ancestor_and_descendant(self):
        b = GltfBuilder()
        m = b.add_mesh([b.add_primitive([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [0, 1, 2])])
        b.add_node("P", mesh=m, children=[1])
        b.add_node("C", mesh=m)
        b.add_node("Other", mesh=m)
        b.add_scene([0, 2])
        plan = self.plan(b)
        self.assertEqual([s.nodes for s in plan.shapes], [[0, 2], [1]])

    def test_skinned_or_weighted_nodes_get_own_shape(self):
        b = GltfBuilder()
        m = b.add_mesh([b.add_primitive([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [0, 1, 2])])
        b.add_node("A", mesh=m)
        b.add_node("B", mesh=m, weights=[0.5])
        b.add_scene([0, 1])
        self.assertEqual(len(self.plan(b).shapes), 2)

    def test_hierarchy_errors(self):
        b = GltfBuilder()
        b.add_node("A", children=[1])
        b.add_node("B", children=[0])
        b.add_scene([])
        with self.assertRaises(GltfFormatError):
            self.plan(b)
        b = GltfBuilder()
        b.add_node("A", children=[2])
        b.add_node("B", children=[2])
        b.add_node("C")
        b.add_scene([0, 1])
        with self.assertRaises(GltfFormatError) as ctx:
            self.plan(b)
        self.assertIn("more than one parent", str(ctx.exception))

    def test_no_scenes_imports_roots(self):
        b = GltfBuilder()
        b.add_node("A", children=[1])
        b.add_node("B")
        b.add_node("C")
        r = Reporter()
        plan = build_scene_plan(loads(b.glb_bytes()), parse_options(""), r)
        self.assertEqual(plan.roots, [0, 2])


class TestTriangulation(unittest.TestCase):
    def test_modes(self):
        self.assertEqual(triangulate([0, 1, 2, 3, 4, 5, 6], 4), [0, 1, 2, 3, 4, 5])
        self.assertEqual(triangulate([0, 1, 2, 3, 4], 5), [0, 1, 2, 1, 3, 2, 2, 3, 4])
        self.assertEqual(triangulate([0, 1, 2, 3, 4], 6), [1, 2, 0, 2, 3, 0, 3, 4, 0])
        self.assertEqual(triangulate(range(3), 5), [0, 1, 2])
        self.assertEqual(triangulate([0, 1], 6), [])

    def test_degenerate(self):
        self.assertEqual(drop_degenerate([0, 1, 2, 1, 1, 3, 2, 3, 4]), ([0, 1, 2, 2, 3, 4], 1))
        self.assertEqual(drop_degenerate([0, 1, 2]), ([0, 1, 2], 0))


class TestMeshPrep(unittest.TestCase):
    def prep(self, b, mesh=0, options="", scale=100.0):
        doc = loads(b.glb_bytes())
        r = Reporter()
        md = prepare_mesh(doc, AccessorReader(doc), mesh, parse_options(options), scale, r)
        return md, r

    def test_multi_primitive(self):
        b, _ = fixtures.scene_builder()
        md, r = self.prep(b, mesh=1)
        self.assertEqual((md.num_vertices, md.num_faces), (15, 7))
        self.assertEqual([(p.primitive_index, p.material, p.face_start, p.face_count) for p in md.primitives],
                         [(0, 0, 0, 1), (1, 1, 1, 3), (3, None, 4, 2), (4, 1, 6, 1)])
        self.assertEqual(md.skipped_primitives, [2])
        self.assertEqual(len(md.sources), 3)  # prim A and E share one vertex source
        self.assertEqual(md.face_connects[18:21], [0, 2, 3])
        uv = md.uv_sets[0]
        self.assertEqual(uv.name, "map1")
        self.assertEqual(uv.face_uv_counts, [3, 0, 0, 0, 0, 0, 3])
        self.assertEqual(list(uv.uv_ids), [0, 1, 2, 0, 2, 3])
        self.assertEqual(list(uv.v), [1.0, 1.0, 0.0, 0.0])
        # normals only on prim B's vertices (4..8)
        self.assertEqual(md.normal_vertex_ids, [4, 5, 6, 7, 8])
        cs = md.color_sets[0]
        self.assertFalse(cs.has_alpha)
        self.assertEqual(list(cs.rgba[:8]), [1, 0, 0, 1, 0, 1, 0, 1])
        self.assertEqual(cs.vertex_ids, [0, 1, 2, 3])
        self.assertTrue(any("LINES" in w for w in r.warnings))

    def test_positions_scaled_and_uv_flipped(self):
        b, _ = fixtures.scene_builder()
        md, _ = self.prep(b, mesh=0, scale=100.0)
        self.assertEqual(list(md.positions[:3]), [-50.0, -50.0, 50.0])
        _, _, uv0, _, _, _ = fixtures.cube_data()
        self.assertAlmostEqual(md.uv_sets[0].v[2], 1.0 - uv0[2][1], places=6)
        self.assertIsNone(md.normal_vertex_ids)
        self.assertIs(md.uv_sets[0].uv_ids, md.face_connects)
        self.assertEqual([u.name for u in md.uv_sets], ["map1", "map2"])
        self.assertTrue(md.color_sets[0].has_alpha)

    def test_options_off(self):
        b, _ = fixtures.scene_builder()
        md, _ = self.prep(b, mesh=0, options="importNormals=0;importColors=0")
        self.assertIsNone(md.normals)
        self.assertEqual(md.color_sets, [])

    def test_compaction_of_unreferenced_vertices(self):
        b = GltfBuilder()
        pos = [(0, 0, 0), (9, 9, 9), (1, 0, 0), (0, 1, 0)]
        m = b.add_mesh([b.add_primitive(pos, [0, 2, 3], uvs=[[(0, 0), (5, 5), (1, 0), (0, 1)]])])
        b.add_node("n", mesh=m)
        b.add_scene([0])
        md, _ = self.prep(b, scale=1.0)
        self.assertEqual(md.num_vertices, 3)
        self.assertEqual(list(md.positions), [0, 0, 0, 1, 0, 0, 0, 1, 0])
        self.assertEqual(md.face_connects, [0, 1, 2])
        self.assertEqual(md.sources[0].original_indices, [0, 2, 3])
        self.assertEqual(list(md.uv_sets[0].u), [0, 1, 0])

    def test_degenerate_dropped_and_ranges(self):
        b = GltfBuilder()
        pos = [(0, 0, 0), (1, 0, 0), (0, 1, 0), (1, 1, 0)]
        m = b.add_mesh([b.add_primitive(pos, [0, 1, 2, 1, 1, 3, 1, 3, 2]),
                        b.add_primitive(pos, [0, 1, 2])])
        b.add_node("n", mesh=m)
        b.add_scene([0])
        md, r = self.prep(b)
        self.assertEqual([(p.face_start, p.face_count, p.degenerate_dropped) for p in md.primitives],
                         [(0, 2, 1), (2, 1, 0)])
        self.assertTrue(any("degenerate" in w for w in r.warnings))

    def test_index_out_of_range(self):
        b = GltfBuilder()
        m = b.add_mesh([b.add_primitive([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [0, 1, 7])])
        b.add_node("n", mesh=m)
        b.add_scene([0])
        with self.assertRaises(GltfFormatError) as ctx:
            self.prep(b)
        self.assertIn("out of range", str(ctx.exception))

    def test_attribute_count_mismatch(self):
        b = GltfBuilder()
        prim = b.add_primitive([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [0, 1, 2])
        prim["attributes"]["NORMAL"] = b.add_accessor([(0, 0, 1)], "VEC3")
        b.add_mesh([prim])
        b.add_node("n", mesh=0)
        b.add_scene([0])
        with self.assertRaises(GltfFormatError):
            self.prep(b)

    def test_quantized_and_normalized_attributes(self):
        b = GltfBuilder()
        pos = b.add_accessor([(0, 0, 0), (2, 0, 0), (0, 3, 0)], "VEC3", USHORT)  # KHR_mesh_quantization
        uv = b.add_accessor([(0, 0), (255, 0), (0, 255)], "VEC2", UBYTE, normalized=True)
        col = b.add_accessor([(65535, 0, 0, 65535)] * 3, "VEC4", USHORT, normalized=True)
        m = b.add_mesh([{"attributes": {"POSITION": pos, "TEXCOORD_0": uv, "COLOR_0": col}}])
        b.add_node("n", mesh=m)
        b.add_scene([0])
        b.json["extensionsRequired"] = b.json["extensionsUsed"] = ["KHR_mesh_quantization"]
        md, _ = self.prep(b, scale=1.0)
        self.assertEqual(list(md.positions), [0, 0, 0, 2, 0, 0, 0, 3, 0])
        self.assertEqual(list(md.uv_sets[0].u), [0, 1, 0])
        self.assertEqual(list(md.uv_sets[0].v), [1, 1, 0])
        self.assertEqual(list(md.color_sets[0].rgba[:4]), [1, 0, 0, 1])

    def test_sparse_mesh(self):
        md, _ = self.prep(fixtures.sparse_builder(), scale=1.0)
        self.assertEqual(list(md.positions), [0, 0, 0, 2, 0, 0, 0, 0, 3, 0, 0, 0, 0, 4, 0, 0, 0, 4])


class TestPrepareImport(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="i3dgltf_core_")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_gltf_and_glb_identical(self):
        g, glb, _ = fixtures.write_scene(self.dir)
        a = prepare_import(g, parse_options(""))
        b = prepare_import(glb, parse_options(""))
        for k in a.meshes:
            self.assertEqual(list(a.meshes[k].positions), list(b.meshes[k].positions))
            self.assertEqual(a.meshes[k].face_connects, b.meshes[k].face_connects)
        self.assertEqual(sorted(a.meshes), [0, 1])  # Orphan's scene is not imported


if __name__ == "__main__":
    unittest.main()
