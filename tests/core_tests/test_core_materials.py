"""Core material planning, images and texture transform maths (no Maya)."""

import math
import os
import random
import shutil
import tempfile
import unittest

import fixtures
from gltf_builder import png_bytes

from i3d_gltf.core import Reporter, parse_options, prepare_import
from i3d_gltf.core import images as img
from i3d_gltf.core.materials import (RAW, SRGB, NormalInput, TexInput, gltf_uv_transform, has_transform,
                                     manifold2d_from_transform, manifold2d_uv, place2d_from_transform,
                                     place2d_uv_transform)


def _tmp():
    return tempfile.mkdtemp(prefix="core_mat_", dir=os.environ.get("I3DGLTF_TEST_TMP"))


class TestTextureTransformMaths(unittest.TestCase):
    def expected(self, U, V, off, rot, scale):
        """What the Maya file node must sample for mesh UV (U, V): glTF transform on the
        un-flipped UV, then flipped back into Maya's V-up texture space."""
        u2, v2 = gltf_uv_transform((U, 1.0 - V), off, rot, scale)
        return u2, 1.0 - v2

    def test_place2d_reproduces_gltf_transform(self):
        rnd = random.Random(7)
        cases = [((0, 0), 0.0, (1, 1)), ((0.5, 0), 0.0, (1, 1)), ((0, 0), 0.3927, (1, 1)),
                 ((0, 0), 0.0, (1.5, 1.5)), ((-0.2, -0.1), 0.3, (1.5, 1.5))]
        cases += [((rnd.uniform(-2, 2), rnd.uniform(-2, 2)), rnd.uniform(-4, 4),
                   (rnd.uniform(-3, 3), rnd.uniform(-3, 3))) for _ in range(20)]
        for off, rot, scale in cases:
            rep, offset, rotate = place2d_from_transform(off, rot, scale)
            for U, V in ((0, 0), (1, 0), (0, 1), (0.3, 0.7), (2.5, -1.25)):
                got = place2d_uv_transform((U, V), rep, offset, rotate)
                want = self.expected(U, V, off, rot, scale)
                self.assertAlmostEqual(got[0], want[0], 6, (off, rot, scale, U, V))
                self.assertAlmostEqual(got[1], want[1], 6, (off, rot, scale, U, V))

    def test_identity(self):
        self.assertEqual(place2d_from_transform((0, 0), 0.0, (1, 1)), ((1, 1), (0.0, 0.0), 0.0))

    def test_pxr_manifold_reproduces_gltf_transform(self):
        # RenderMan PxrManifold2D (invertT on) feeding PxrTexture, which reads t from the top
        # of the image like glTF: the lookup must be the glTF transform of the un-flipped UV.
        rnd = random.Random(11)
        cases = [((0, 0), 0.0, (1, 1)), ((0.25, -0.1), 0.4, (2.0, 0.5)), ((0, 0), 0.3927, (1, 1))]
        cases += [((rnd.uniform(-2, 2), rnd.uniform(-2, 2)), rnd.uniform(-4, 4),
                   (rnd.uniform(-3, 3), rnd.uniform(-3, 3))) for _ in range(20)]
        for off, rot, scale in cases:
            m = manifold2d_from_transform(off, rot, scale)
            for U, V in ((0, 0), (1, 0), (0, 0.999), (0.3, 0.7), (0.5, 0.5)):
                got = manifold2d_uv((U, V), *m)
                want = gltf_uv_transform((U, 1.0 - V), off, rot, scale)
                self.assertAlmostEqual(got[0], want[0], 6, (off, rot, scale, U, V))
                self.assertAlmostEqual(got[1], want[1], 6, (off, rot, scale, U, V))
        self.assertEqual(manifold2d_from_transform((0, 0), 0.0, (1, 1)), (1.0, 1.0, 0.0, 0.0, 0.0))

    def test_rotation_direction_matches_khronos_sample(self):
        # TextureTransformTest: with rotation 22.5 deg, surface UV (0.421, 1.016) must show the
        # arrow tip that sits at texel (0.778, 0.778) of Arrow.png (the "correct" marker).
        u, v = gltf_uv_transform((0.4209518, 1.0162675), (0, 0), 0.39269908, (1, 1))
        self.assertAlmostEqual(u, 0.7778175, 4)
        self.assertAlmostEqual(v, 0.7778175, 4)


class TestImages(unittest.TestCase):
    def test_sniff(self):
        self.assertEqual(img.sniff_format(fixtures.checker_png()), "png")
        self.assertEqual(img.sniff_format(b"\xff\xd8\xff\xe0rest"), "jpeg")
        self.assertEqual(img.sniff_format(fixtures.WEBP_BYTES), "webp")
        self.assertEqual(img.sniff_format(b"\xabKTX 20\xbb\r\n\x1a\n" + b"\0" * 8), "ktx2")
        self.assertEqual(img.sniff_format(b"\0\0\0\x1cftypavif"), "avif")
        self.assertIsNone(img.sniff_format(b"hello"))

    def test_png_alpha(self):
        self.assertTrue(img.png_has_alpha(fixtures.checker_png(alpha=True)))
        self.assertFalse(img.png_has_alpha(fixtures.checker_png(alpha=False)))
        self.assertFalse(img.has_alpha("jpeg", b""))

    def test_subfolder_stable(self):
        a = img.extraction_subfolder(r"C:\lib\My Model.glb")
        self.assertEqual(a, img.extraction_subfolder(r"C:\lib\My Model.glb"))
        self.assertTrue(a.startswith("My_Model_"))
        self.assertEqual(len(a.split("_")[-1]), 8)
        self.assertNotEqual(a, img.extraction_subfolder(r"C:\other\My Model.glb"))

    def test_embedded_names_unique_case_insensitive(self):
        doc = prepare_import(self._write("bufferView"), parse_options("shader=standardSurface"),
                             Reporter(), texture_root=_tmp()).doc
        names = img.embedded_file_names(doc)
        self.assertEqual(names[0], "Checker")
        self.assertEqual(names[1], "checker_1")
        self.assertEqual(names[2], "Webp")

    def _write(self, mode, glb=True):
        d = _tmp()
        b = fixtures.materials_builder(mode)
        if mode == "uri":
            fixtures.write_material_images(d)
        return b.write_glb(os.path.join(d, "m.glb")) if glb else b.write_gltf(os.path.join(d, "m.gltf"))


class TestMaterialPlan(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = _tmp()
        cls.src = fixtures.materials_builder("bufferView").write_glb(os.path.join(cls.dir, "mats.glb"))
        cls.root = os.path.join(cls.dir, "textures")
        cls.reporter = Reporter()
        cls.prepared = prepare_import(cls.src, parse_options("shader=aiStandardSurface"), cls.reporter,
                                      texture_root=cls.root)
        cls.plan = cls.prepared.materials
        cls.by_name = dict((m.name, m) for m in cls.plan.materials.values())

    def test_only_used_materials(self):
        self.assertEqual(sorted(self.by_name), sorted(fixtures.MATERIAL_NAMES[:-1]))
        self.assertEqual(self.by_name["Red"].sg_name, "RedSG")

    def test_constants(self):
        red = self.by_name["Red"].inputs
        self.assertEqual(red["base_color"], (1.0, 0.0, 0.0))
        self.assertEqual(red["metalness"], 0.25)
        self.assertEqual(red["roughness"], 0.75)
        self.assertEqual(red["emission_weight"], 0.0)
        self.assertTrue(self.by_name["Red"].double_sided)

    def test_textured(self):
        m = self.by_name["Textured"].inputs
        self.assertIsInstance(m["base_color"], TexInput)
        self.assertEqual((m["base_color"].channel, m["base_color"].factor), ("rgb", (0.5, 0.5, 0.5)))
        self.assertEqual((m["metalness"].channel, m["metalness"].factor), ("b", 0.5))
        self.assertEqual((m["roughness"].channel, m["roughness"].factor), ("g", 1.0))
        self.assertEqual(m["metalness"].texture, m["roughness"].texture)
        self.assertEqual(self.plan.textures[m["metalness"].texture].color_space, RAW)
        self.assertEqual(self.plan.textures[m["base_color"].texture].color_space, SRGB)
        self.assertIsInstance(m["normal"], NormalInput)
        self.assertAlmostEqual(m["normal"].scale, 0.8)
        self.assertEqual(m["emission_weight"], 2.0)
        self.assertEqual(m["emission_color"].factor, (1.0, 0.5, 0.25))

    def test_shared_textures(self):
        base = lambda n: self.by_name[n].inputs["base_color"].texture
        self.assertEqual(base("Textured"), base("Masked"))
        self.assertNotEqual(base("Textured"), base("Transformed"))
        t = self.plan.textures[base("Transformed")]
        self.assertEqual(t.texcoord, 1)
        self.assertEqual(t.repeat_uv, (2.0, 0.5))
        self.assertAlmostEqual(t.rotate_uv, 0.4)
        self.assertEqual((t.wrap_u, t.wrap_v), (33648, 33071))
        # metal-rough and normal share the Raw node of the packed image
        m = self.by_name["Textured"].inputs
        self.assertEqual(m["normal"].texture, m["roughness"].texture)
        names = [t.name for t in self.plan.textures if t.name]
        self.assertEqual(len(names), len(set(names)))
        self.assertIn("Checker_file", names)
        # the file's own transform is kept for renderers with their own UV nodes (PxrManifold2D)
        self.assertEqual((t.gltf_offset, t.gltf_rotation, t.gltf_scale), ((0.25, -0.1), 0.4, (2.0, 0.5)))
        self.assertTrue(has_transform(t))
        self.assertFalse(has_transform(self.plan.textures[base("Textured")]))
        self.assertEqual(t.base, "Checker")

    def test_alpha(self):
        masked = self.by_name["Masked"]
        self.assertEqual(masked.inputs["opacity"].channel, "a")
        self.assertTrue(masked.transparent)
        blend = self.by_name["Blend"]
        self.assertEqual(blend.inputs["opacity"], 0.5)
        self.assertTrue(blend.transparent)
        self.assertNotIn("opacity", self.by_name["Red"].inputs)
        self.assertFalse(self.by_name["Red"].transparent)

    def test_extensions(self):
        f = self.by_name["Fancy"].inputs
        self.assertEqual(f["ior"], 1.4)
        self.assertEqual(f["transmission"], 0.5)
        self.assertIs(f["thin_walled"], False)  # volume present
        self.assertEqual(f["transmission_depth_m"], 0.5)
        self.assertEqual(f["transmission_color"], (0.9, 0.8, 0.7))
        self.assertEqual((f["coat"], f["coat_roughness"]), (0.7, 0.2))
        self.assertEqual((f["sheen"], f["sheen_color"], f["sheen_roughness"]), (1.0, (0.5, 0.4, 0.3), 0.6))
        self.assertEqual((f["specular_weight"], f["specular_color"]), (0.8, (1.0, 0.9, 0.8)))
        self.assertTrue(self.by_name["Fancy"].transparent)

    def test_spec_gloss(self):
        s = self.by_name["SpecGloss"].inputs
        self.assertEqual(s["base_color"], (0.2, 0.4, 0.6))
        self.assertEqual(s["metalness"], 0.0)
        self.assertAlmostEqual(s["roughness"], 0.7)
        self.assertEqual(s["specular_color"], (0.3, 0.3, 0.3))
        self.assertTrue(any("SpecularGlossiness" in w for w in self.reporter.warnings))

    def test_unlit(self):
        u = self.by_name["Unlit"].inputs
        self.assertEqual(u["base_weight"], 0.0)
        self.assertEqual(u["specular_weight"], 0.0)
        self.assertEqual((u["coat"], u["sheen"]), (0.0, 0.0))
        self.assertEqual(u["emission_weight"], 1.0)
        self.assertIsInstance(u["emission_color"], TexInput)
        self.assertEqual(u["emission_color"].factor, (1.0, 1.0, 0.5))
        self.assertEqual(u["opacity"].channel, "a")  # alpha modes still honoured

    def test_webp_skipped(self):
        self.assertEqual(self.by_name["WebpMat"].inputs["base_color"], (0.1, 0.2, 0.3))
        self.assertTrue(any("WebP" in w for w in self.reporter.warnings), self.reporter.warnings)

    def test_dropped(self):
        d = self.plan.dropped
        self.assertEqual(d["occlusionTexture"], 1)
        self.assertEqual(d["KHR_materials_iridescence"], 1)
        self.assertTrue(any("thickness" in k for k in d))

    def test_extraction(self):
        sub = os.path.join(self.root, img.extraction_subfolder(self.src))
        self.assertEqual(sorted(os.listdir(sub)), ["Checker.png", "checker_1.png"])  # webp not written
        with open(os.path.join(sub, "Checker.png"), "rb") as f:
            self.assertEqual(f.read(), fixtures.checker_png())
        # identical content is not rewritten
        r = Reporter()
        again = prepare_import(self.src, parse_options(""), r, texture_root=self.root).materials
        self.assertFalse(any(f.written for f in again.images.values()))

    def test_unwritable_folder(self):
        blocker = os.path.join(self.dir, "a_file")
        with open(blocker, "w") as f:
            f.write("x")
        r = Reporter()
        plan = prepare_import(self.src, parse_options(""), r, texture_root=blocker).materials
        self.assertTrue(any("Could not extract" in w for w in r.warnings), r.warnings)
        self.assertEqual(plan.materials[1].inputs["base_color"], (0.5, 0.5, 0.5))
        self.assertNotIn("normal", plan.materials[1].inputs)

    def test_shader_none_skips_materials(self):
        p = prepare_import(self.src, parse_options("shader=none"), Reporter(), texture_root=self.root)
        self.assertIsNone(p.materials)

    def test_external_images_used_in_place(self):
        d = _tmp()
        fixtures.write_material_images(d)
        src = fixtures.materials_builder("uri").write_gltf(os.path.join(d, "ext.gltf"))
        os.remove(os.path.join(d, "packed.png"))
        r = Reporter()
        plan = prepare_import(src, parse_options(""), r, texture_root=os.path.join(d, "out")).materials
        self.assertEqual(plan.images[0].path, os.path.join(d, "checker.png"))
        self.assertEqual(plan.images[0].status, img.OK)
        self.assertEqual(plan.images[1].status, img.MISSING)
        self.assertTrue(any("not found" in w for w in r.warnings))
        self.assertFalse(os.path.exists(os.path.join(d, "out", img.extraction_subfolder(src), "checker.png")))

    def test_jpeg_alpha_not_used(self):
        d = _tmp()
        b = fixtures.materials_builder("bufferView")
        jpeg = b"\xff\xd8\xff\xe0" + b"\0" * 32
        b.json["images"][0]["mimeType"] = "image/jpeg"
        view = b.json["images"][0]["bufferView"]
        bv = b.json["bufferViews"][view]
        b.bin[bv["byteOffset"]:bv["byteOffset"] + len(jpeg)] = jpeg
        src = b.write_glb(os.path.join(d, "j.glb"))
        plan = prepare_import(src, parse_options(""), Reporter(), texture_root=d).materials
        masked = [m for m in plan.materials.values() if m.name == "Masked"][0]
        self.assertEqual(masked.inputs["opacity"], 1.0)
        self.assertFalse(masked.transparent)


class TestOptions(unittest.TestCase):
    def test_shader_option(self):
        self.assertEqual(parse_options("").shader, "aiStandardSurface")
        self.assertEqual(parse_options("shader=openPBRSurface").shader, "openPBRSurface")
        self.assertEqual(parse_options("shader=PxrSurface").shader, "PxrSurface")
        self.assertEqual(parse_options("shader=dlPrincipled").shader, "dlPrincipled")
        r = Reporter()
        self.assertEqual(parse_options("shader=lambert", r).shader, "aiStandardSurface")
        self.assertTrue(r.warnings)
        self.assertEqual(parse_options("textureDir=C:/x y/z").textureDir, "C:/x y/z")
