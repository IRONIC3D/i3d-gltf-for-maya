"""Materials and textures in Maya (milestone 2). Run with mayapy via tests/run_suite.py --maya."""

import os
import shutil
import tempfile

import maya.cmds as cmds

import fixtures
from maya_helpers import MayaTestCase, fixture_dir, import_file, reference_file

from i3d_gltf.core import Reporter
from i3d_gltf.core import images as img
from i3d_gltf.core.materials import RAW, SRGB, gltf_uv_transform
from i3d_gltf.maya import importer, shading

SHADERS = ("aiStandardSurface", "aiOpenPBRSurface", "openPBRSurface", "standardSurface")
SHADING_TYPES = ["file", "place2dTexture", "multiplyDivide", "reverse", "condition", "bump2d", "uvChooser",
                 "shadingEngine", "standardSurface"]


def strip(name):
    return name.split("|")[-1].split(":")[-1]


_files = {}


def material_file(mode="bufferView", glb=True):
    """Write the material fixture once per mode into the suite's temp folder."""
    key = (mode, glb)
    if key not in _files:
        d = tempfile.mkdtemp(prefix="mat_", dir=fixture_dir())
        b = fixtures.materials_builder(mode)
        if mode == "uri":
            fixtures.write_material_images(d)
        _files[key] = b.write_glb(os.path.join(d, "mats.glb")) if glb else b.write_gltf(os.path.join(d, "mats.gltf"))
    return _files[key]


def tex_root():
    return tempfile.mkdtemp(prefix="tex_", dir=fixture_dir())


def expected_type(requested):
    return shading.resolve_shader_type(requested, Reporter())


def attr(shader_type, slot):
    return shading.SHADER_ATTRS[shader_type][slot]


def source_of(plug):
    src = cmds.listConnections(plug, source=True, destination=False, plugs=True, skipConversionNodes=True)
    return src[0] if src else None


class MaterialTestCase(MayaTestCase):
    shader = "aiStandardSurface"

    def load(self, options="", mode="bufferView", ns="m", glb=True):
        opts = "shader=%s;textureDir=%s;%s" % (self.shader, tex_root().replace("\\", "/"), options)
        import_file(material_file(mode, glb), ns, options=opts)
        self.result = importer.last_result()
        self.shaders = dict((fixtures.MATERIAL_NAMES[i], n) for i, n in self.result.shaders.items())
        return self.result

    def a(self, material, slot):
        st = self.result.shader_type
        return "%s.%s" % (self.shaders[material], attr(st, slot))


class TestShaderTypes(MaterialTestCase):
    def test_every_shader_option(self):
        for requested in SHADERS:
            cmds.file(new=True, force=True)
            self.shader = requested
            self.load()
            st = self.result.shader_type
            self.assertEqual(st, expected_type(requested))
            if requested == "openPBRSurface" and "openPBRSurface" not in cmds.allNodeTypes():
                self.assertNotEqual(st, "openPBRSurface")  # e.g. Maya 2024: fell back
                self.assertTrue(any("openPBRSurface" in w for w in self.result.reporter.warnings))
            self.assertEqual(sorted(self.shaders), sorted(fixtures.MATERIAL_NAMES[:-1]))
            for name, node in self.shaders.items():
                self.assertEqual(cmds.nodeType(node), st, node)
                self.assertEqual(strip(node), name)
                sg = self.result.shading_groups[fixtures.MATERIAL_NAMES.index(name)]
                self.assertEqual(strip(sg), name + "SG")
                self.assertEqual(source_of(sg + ".surfaceShader"), node + ".outColor")
            self._check_mapping(st)

    def _check_mapping(self, st):
        msg = st
        self.assertVecAlmost(cmds.getAttr(self.a("Red", "base_color"))[0], (1, 0, 0), msg=msg)
        self.assertAlmostEqual(cmds.getAttr(self.a("Red", "base_weight")), 1.0, msg=msg)
        self.assertAlmostEqual(cmds.getAttr(self.a("Red", "metalness")), 0.25, msg=msg)
        self.assertAlmostEqual(cmds.getAttr(self.a("Red", "roughness")), 0.75, msg=msg)
        self.assertAlmostEqual(cmds.getAttr(self.a("Red", "emission_weight")), 0.0, msg=msg)
        # textured: factor multiplied in by a per-material multiplyDivide, file shared
        src = source_of(self.a("Textured", "base_color"))
        self.assertEqual(cmds.nodeType(src), "multiplyDivide", msg)
        md = src.split(".")[0]
        self.assertVecAlmost(cmds.getAttr(md + ".input2")[0], (0.5, 0.5, 0.5))
        base_file = source_of(md + ".input1").split(".")[0]
        self.assertEqual(cmds.nodeType(base_file), "file")
        self.assertEqual(strip(base_file), "Checker_file")
        # packed metal/rough: B -> metalness (x0.5), G -> roughness (factor 1: direct)
        metal = source_of(self.a("Textured", "metalness"))
        self.assertEqual(cmds.nodeType(metal), "multiplyDivide")
        self.assertAlmostEqual(cmds.getAttr(metal.split(".")[0] + ".input2X"), 0.5)
        packed = source_of(metal.split(".")[0] + ".input1X")
        self.assertTrue(packed.endswith(".outColorB"), packed)
        rough = source_of(self.a("Textured", "roughness"))
        self.assertEqual(rough, packed.replace("outColorB", "outColorG"))
        # normal map through bump2d
        bump = source_of(self.a("Textured", "normal")).split(".")[0]
        self.assertEqual(cmds.nodeType(bump), "bump2d")
        self.assertEqual(cmds.getAttr(bump + ".bumpInterp"), 1)
        self.assertAlmostEqual(cmds.getAttr(bump + ".bumpDepth"), 0.8, 5)
        self.assertEqual(source_of(bump + ".bumpValue"), packed.split(".")[0] + ".outAlpha")
        # emission: strength 2 (x1000 nits for OpenPBR), colour = texture x factor
        scale = shading.EMISSION_SCALE.get(st, 1.0)
        self.assertAlmostEqual(cmds.getAttr(self.a("Textured", "emission_weight")), 2.0 * scale, 3)
        em = source_of(self.a("Textured", "emission_color")).split(".")[0]
        self.assertVecAlmost(cmds.getAttr(em + ".input2")[0], (1, 0.5, 0.25))
        self.assertEqual(source_of(em + ".input1").split(".")[0], base_file)
        # extensions
        self.assertAlmostEqual(cmds.getAttr(self.a("Fancy", "ior")), 1.4, 5)
        self.assertAlmostEqual(cmds.getAttr(self.a("Fancy", "transmission")), 0.5, 5)
        self.assertFalse(cmds.getAttr(self.a("Fancy", "thin_walled")))
        self.assertAlmostEqual(cmds.getAttr(self.a("Fancy", "transmission_depth")), 50.0, 3)  # 0.5 m in cm
        self.assertVecAlmost(cmds.getAttr(self.a("Fancy", "transmission_color"))[0], (0.9, 0.8, 0.7))
        self.assertAlmostEqual(cmds.getAttr(self.a("Fancy", "coat")), 0.7, 5)
        self.assertAlmostEqual(cmds.getAttr(self.a("Fancy", "coat_roughness")), 0.2, 5)
        self.assertAlmostEqual(cmds.getAttr(self.a("Fancy", "coat_ior")), 1.5, 5)
        self.assertAlmostEqual(cmds.getAttr(self.a("Fancy", "sheen")), 1.0, 5)
        self.assertVecAlmost(cmds.getAttr(self.a("Fancy", "sheen_color"))[0], (0.5, 0.4, 0.3))
        self.assertAlmostEqual(cmds.getAttr(self.a("Fancy", "sheen_roughness")), 0.6, 5)
        self.assertAlmostEqual(cmds.getAttr(self.a("Fancy", "specular_weight")), 0.8, 5)
        self.assertVecAlmost(cmds.getAttr(self.a("Fancy", "specular_color"))[0], (1, 0.9, 0.8))
        # spec-gloss conversion
        self.assertVecAlmost(cmds.getAttr(self.a("SpecGloss", "base_color"))[0], (0.2, 0.4, 0.6))
        self.assertAlmostEqual(cmds.getAttr(self.a("SpecGloss", "metalness")), 0.0)
        self.assertAlmostEqual(cmds.getAttr(self.a("SpecGloss", "roughness")), 0.7, 5)
        # unlit: flat emission from base colour texture x factor, no diffuse/specular
        self.assertAlmostEqual(cmds.getAttr(self.a("Unlit", "base_weight")), 0.0)
        self.assertAlmostEqual(cmds.getAttr(self.a("Unlit", "specular_weight")), 0.0)
        self.assertAlmostEqual(cmds.getAttr(self.a("Unlit", "coat")), 0.0)
        self.assertAlmostEqual(cmds.getAttr(self.a("Unlit", "sheen")), 0.0)
        self.assertAlmostEqual(cmds.getAttr(self.a("Unlit", "emission_weight")), 1.0 * scale, 3)
        um = source_of(self.a("Unlit", "emission_color")).split(".")[0]
        self.assertVecAlmost(cmds.getAttr(um + ".input2")[0], (1, 1, 0.5))
        self.assertEqual(source_of(um + ".input1").split(".")[0], base_file)
        # webp: untextured, factor only
        self.assertIsNone(source_of(self.a("WebpMat", "base_color")))
        self.assertVecAlmost(cmds.getAttr(self.a("WebpMat", "base_color"))[0], (0.1, 0.2, 0.3))
        self._check_alpha(st)

    def _check_alpha(self, st):
        # MASK: condition(alpha >= cutoff) on every opacity channel
        op = attr(st, "opacity")
        children = cmds.attributeQuery(op, node=self.shaders["Masked"], listChildren=True) or [op]
        for c in children:
            cond = source_of("%s.%s" % (self.shaders["Masked"], c)).split(".")[0]
            self.assertEqual(cmds.nodeType(cond), "condition")
        self.assertAlmostEqual(cmds.getAttr(cond + ".secondTerm"), 0.3, 5)
        self.assertEqual(cmds.getAttr(cond + ".operation"), 3)
        self.assertTrue(source_of(cond + ".firstTerm").endswith(".outAlpha"))
        # BLEND constant 0.5
        self.assertAlmostEqual(cmds.getAttr("%s.%s" % (self.shaders["Blend"], children[0])), 0.5, 5)
        # OPAQUE untouched
        self.assertAlmostEqual(cmds.getAttr("%s.%s" % (self.shaders["Red"], children[0])), 1.0, 5)
        # BLEND with texture alpha: direct
        self.assertTrue(source_of("%s.%s" % (self.shaders["Unlit"], children[0])).endswith(".outAlpha"))

    def test_none_keeps_initial_shading_group(self):
        self.shader = "none"
        self.load()
        self.assertIsNone(self.result.shader_type)
        self.assertEqual(self.result.shaders, {})
        self.assertEqual(cmds.ls("m:*", type=["file", "shadingEngine", "place2dTexture"]), [])
        for s in self.result.shapes:
            for p in s.instance_paths:
                self.assertTrue(cmds.sets(p, isMember="initialShadingGroup"), p)

    def test_fallback_without_arnold(self):
        real_exists, real_load = shading.node_type_exists, shading.load_plugin
        loads = []
        shading.node_type_exists = lambda t: t not in shading.ARNOLD_TYPES and real_exists(t)
        shading.load_plugin = lambda plugin: loads.append(plugin) or False
        try:
            for requested, want in (("aiStandardSurface", "standardSurface"),
                                    ("aiOpenPBRSurface", "openPBRSurface" if real_exists("openPBRSurface")
                                     else "standardSurface"),
                                    ("openPBRSurface", "openPBRSurface" if real_exists("openPBRSurface")
                                     else "standardSurface"),
                                    ("standardSurface", "standardSurface")):
                cmds.file(new=True, force=True)
                del loads[:]
                self.shader = requested
                self.load()
                self.assertEqual(self.result.shader_type, want, requested)
                fallback = [w for w in self.result.reporter.warnings if w.startswith("Shader '")]
                self.assertEqual(len(fallback), 0 if want == requested else 1, (requested, fallback))
                if requested.startswith("ai"):
                    self.assertEqual(loads, ["mtoa"])  # tried to load mtoa once
                    self.assertIn("mtoa", fallback[0])
                self.assertEqual(cmds.nodeType(self.shaders["Red"]), want)
        finally:
            shading.node_type_exists, shading.load_plugin = real_exists, real_load

    def test_fallback_without_native_openpbr(self):
        real_exists = shading.node_type_exists
        shading.node_type_exists = lambda t: t != "openPBRSurface" and real_exists(t)
        try:
            self.shader = "openPBRSurface"
            self.load()
            want = "aiOpenPBRSurface" if real_exists("aiOpenPBRSurface") else "standardSurface"
            self.assertEqual(self.result.shader_type, want)
            self.assertEqual(len([w for w in self.result.reporter.warnings if w.startswith("Shader '")]), 1)
        finally:
            shading.node_type_exists = real_exists

    def test_arnold_loaded_on_demand(self):
        if not shading.load_plugin("mtoa"):
            self.skipTest("mtoa not installed")
        self.assertTrue(cmds.pluginInfo("mtoa", q=True, loaded=True))


class TestAssignment(MaterialTestCase):
    def sg(self, name):
        return self.result.shading_groups[fixtures.MATERIAL_NAMES.index(name)]

    def test_multi_material_faces(self):
        self.load()
        self.assertTrue(cmds.sets("m:MultiShape.f[0]", isMember=self.sg("Red")))
        self.assertTrue(cmds.sets("m:MultiShape.f[1:2]", isMember=self.sg("Textured")))
        self.assertFalse(cmds.sets("m:MultiShape.f[0]", isMember=self.sg("Textured")))
        self.assertTrue(cmds.sets("m:MixedShape.f[0]", isMember=self.sg("Masked")))
        self.assertTrue(cmds.sets("m:MixedShape.f[1]", isMember="initialShadingGroup"))
        self.assertTrue(cmds.sets("m:MixedShape.f[2]", isMember=self.sg("Red")))
        # one shading group per material, shared between meshes
        self.assertEqual(len(cmds.ls("m:*", type="shadingEngine")), len(fixtures.MATERIAL_NAMES) - 1)
        red_members = cmds.ls(cmds.sets(self.sg("Red"), q=True), long=True)
        self.assertEqual(sorted(set(m.split(".")[0].split("|")[-1] for m in red_members)),
                         ["m:Mixed", "m:Multi"])  # whole-object assignment lists the transform
        others = "m:OthersShape"
        for k, name in enumerate(("Blend", "Fancy", "SpecGloss", "Unlit", "WebpMat")):
            self.assertTrue(cmds.sets("%s.f[%d]" % (others, k), isMember=self.sg(name)), name)

    def test_instances(self):
        self.load()
        paths = cmds.ls("m:UV2_aShape", allPaths=True, long=True)
        self.assertEqual(len(paths), 2)
        for p in paths:
            self.assertTrue(cmds.sets(p, isMember=self.sg("Transformed")), p)
        # instanced multi-material shape: per-face assignment on each instance
        cmds.file(new=True, force=True)
        b = fixtures.materials_builder("bufferView")
        b.json["nodes"][1]["children"] = []
        b.add_node("Mixed_b", mesh=1, translation=[5, 0, 0])
        b.json["scenes"][0]["nodes"].append(len(b.json["nodes"]) - 1)
        path = b.write_glb(os.path.join(tempfile.mkdtemp(dir=fixture_dir()), "inst.glb"))
        import_file(path, "i", options="textureDir=%s" % tex_root().replace("\\", "/"))
        r = importer.last_result()
        sgs = dict((fixtures.MATERIAL_NAMES[i], n) for i, n in r.shading_groups.items())
        insts = cmds.ls("i:MixedShape", allPaths=True, long=True)
        self.assertEqual(len(insts), 2)
        for p in insts:
            self.assertTrue(cmds.sets(p + ".f[0]", isMember=sgs["Masked"]), p)
            self.assertTrue(cmds.sets(p + ".f[1]", isMember="initialShadingGroup"), p)
            self.assertTrue(cmds.sets(p + ".f[2]", isMember=sgs["Red"]), p)

    def test_shape_flags(self):
        self.load()
        self.assertTrue(cmds.getAttr("m:MultiShape.doubleSided"))   # Red is double-sided
        self.assertFalse(cmds.getAttr("m:SingleShape.doubleSided"))
        self.assertFalse(cmds.getAttr("m:UV2_aShape.doubleSided"))
        if cmds.attributeQuery("aiOpaque", node="m:SingleShape", exists=True):
            self.assertFalse(cmds.getAttr("m:SingleShape.aiOpaque"))  # BLEND 0.5
            self.assertFalse(cmds.getAttr("m:MixedShape.aiOpaque"))   # MASK with texture
            self.assertTrue(cmds.getAttr("m:MultiShape.aiOpaque"))
            self.assertTrue(cmds.getAttr("m:UV2_aShape.aiOpaque"))

    def test_deterministic_names(self):
        names = []
        for ns in ("a", "b"):
            self.load(ns=ns)
            names.append(sorted(strip(n) for n in cmds.ls(ns + ":*") if cmds.nodeType(n) in SHADING_TYPES
                                or cmds.nodeType(n) == self.result.shader_type))
        self.assertTrue(names[0])
        self.assertEqual(names[0], names[1])
        self.assertIn("Checker_file_1_uvChooser", names[0])


class TestTextures(MaterialTestCase):
    def file_nodes(self, ns="m"):
        return dict((strip(n), n) for n in cmds.ls(ns + ":*", type="file"))

    def test_shared_file_nodes(self):
        self.load()
        files = self.file_nodes()
        # Checker sRGB (shared by Textured, Masked, Unlit + emission), Checker transformed,
        # packed Raw (metal/rough + normal). WebP: none.
        self.assertEqual(sorted(files), ["Checker_file", "Checker_file_1", "checker_file"])
        users = set()
        for dst in cmds.listConnections(files["Checker_file"], source=False, destination=True) or []:
            users.add(dst)
        self.assertEqual(len(self.result.file_nodes), 3)
        self.assertEqual(len(cmds.ls("m:*", type="place2dTexture")), 3)

    def test_color_spaces(self):
        self.load()
        files = self.file_nodes()
        spaces = shading.available_color_spaces()
        srgb = shading.pick_color_space(SRGB, spaces)
        raw = shading.pick_color_space(RAW, spaces)
        self.assertTrue(srgb and raw, spaces)
        self.assertEqual(cmds.getAttr(files["Checker_file"] + ".colorSpace"), srgb)
        self.assertEqual(cmds.getAttr(files["checker_file"] + ".colorSpace"), raw)
        for f in files.values():
            self.assertTrue(cmds.getAttr(f + ".ignoreColorSpaceFileRules"), f)

    def test_pick_color_space_other_configs(self):
        aces = ["ACES - ACEScg", "Utility - Raw", "Utility - sRGB - Texture", "Utility - Linear - sRGB"]
        self.assertEqual(shading.pick_color_space(SRGB, aces), "Utility - sRGB - Texture")
        self.assertEqual(shading.pick_color_space(RAW, aces), "Utility - Raw")
        studio = ["lin_rec709", "srgb_tx", "Raw"]
        self.assertEqual(shading.pick_color_space(SRGB, studio), "srgb_tx")
        blender = ["Linear Rec.709", "sRGB", "Non-Color"]
        self.assertEqual(shading.pick_color_space(RAW, blender), "Non-Color")
        odd = ["scene-linear", "My sRGB 8bit", "data_raw"]
        self.assertEqual(shading.pick_color_space(SRGB, odd), "My sRGB 8bit")
        self.assertEqual(shading.pick_color_space(RAW, odd), "data_raw")
        self.assertIsNone(shading.pick_color_space(RAW, ["ACEScg"]))
        self.assertIsNone(shading.pick_color_space(SRGB, []))

    def test_samplers_and_transform_numeric(self):
        self.load()
        files = self.file_nodes()
        p0 = source_of(files["Checker_file"] + ".uvCoord").split(".")[0]
        self.assertEqual(cmds.nodeType(p0), "place2dTexture")
        self.assertEqual((cmds.getAttr(p0 + ".wrapU"), cmds.getAttr(p0 + ".mirrorU")), (1, 1))  # mirrored
        self.assertEqual(cmds.getAttr(p0 + ".wrapV"), 0)                                         # clamp
        # transformed texture: copy the place2d values onto a free node and evaluate outUV
        pt = cmds.listConnections(files["Checker_file_1"] + ".repeatUV", source=True, destination=False)[0]
        probe = cmds.createNode("place2dTexture")
        for a in ("repeatUV", "offset", "rotateUV"):
            cmds.connectAttr(pt + "." + a, probe + "." + a)
        t = fixtures.TRANSFORM
        for U, V in ((0, 0), (1, 0), (0, 1), (0.3, 0.8), (0.5, 0.5)):
            cmds.setAttr(probe + ".uvCoord", U, V)
            got = cmds.getAttr(probe + ".outUV")[0]
            u2, v2 = gltf_uv_transform((U, 1.0 - V), t["offset"], t["rotation"], t["scale"])
            # place2dTexture itself maps 1.0 to 0.9999 (measured), hence 3 places
            self.assertAlmostEqual(got[0], u2, 3, (U, V, got))
            self.assertAlmostEqual(got[1], 1.0 - v2, 3, (U, V, got))

    def test_uv_set_link(self):
        self.load()
        f = self.file_nodes()["Checker_file_1"]
        chooser = source_of(source_of(f + ".uvCoord").split(".")[0] + ".uvCoord").split(".")[0]
        self.assertEqual(cmds.nodeType(chooser), "uvChooser")
        self.assertEqual(strip(chooser), "Checker_file_1_uvChooser")
        links = cmds.uvLink(q=True, texture=f)
        self.assertEqual(len(links), 1)
        shape, plug = links[0].split(".", 1)
        self.assertEqual(strip(shape), "UV2_aShape")
        self.assertEqual(cmds.getAttr(links[0]), "map2")
        # other textures use the default set: no chooser
        self.assertNotEqual(cmds.nodeType(source_of(self.file_nodes()["Checker_file"] + ".uvCoord")
                                          .split(".")[0]), "uvChooser")

    def test_embedded_extraction_and_idempotence(self):
        root = tex_root()
        src = material_file("bufferView")
        opts = "shader=%s;textureDir=%s" % (self.shader, root.replace("\\", "/"))
        import_file(src, "e1", options=opts)
        sub = os.path.join(root, img.extraction_subfolder(src))
        self.assertEqual(sorted(os.listdir(sub)), ["Checker.png", "checker_1.png"])
        paths = sorted(os.path.normcase(cmds.getAttr(f + ".fileTextureName")) for f in cmds.ls("e1:*", type="file"))
        self.assertEqual(sorted(set(paths)), sorted(os.path.normcase(os.path.join(sub, n).replace("\\", "/"))
                                                    for n in ("Checker.png", "checker_1.png")))
        stamps = dict((n, os.stat(os.path.join(sub, n)).st_mtime_ns) for n in os.listdir(sub))
        import_file(src, "e2", options=opts)
        self.assertEqual(stamps, dict((n, os.stat(os.path.join(sub, n)).st_mtime_ns) for n in os.listdir(sub)))
        self.assertFalse(any(f.written for f in importer.last_result().material_plan.images.values()))
        # data URIs (.gltf) extract the same way, next to nothing in the source folder
        src2 = material_file("datauri", glb=False)
        before = sorted(os.listdir(os.path.dirname(src2)))
        import_file(src2, "e3", options=opts)
        self.assertEqual(sorted(os.listdir(os.path.join(root, img.extraction_subfolder(src2)))),
                         ["Checker.png", "checker_1.png"])
        self.assertEqual(sorted(os.listdir(os.path.dirname(src2))), before)

    def test_default_location_is_project_sourceimages(self):
        old = cmds.workspace(q=True, rootDirectory=True)
        proj = tempfile.mkdtemp(prefix="proj_", dir=fixture_dir())
        try:
            cmds.workspace(proj, openWorkspace=True)
            src = material_file("bufferView")
            before = sorted(os.listdir(os.path.dirname(src)))
            import_file(src, "d", options="shader=%s" % self.shader)
            sub = os.path.join(proj, "sourceimages", "i3dGltf", img.extraction_subfolder(src))
            self.assertEqual(sorted(os.listdir(sub)), ["Checker.png", "checker_1.png"])
            self.assertEqual(sorted(os.listdir(os.path.dirname(src))), before)  # never next to the source
        finally:
            cmds.workspace(old, openWorkspace=True)

    def test_unwritable_folder_warns(self):
        blocker = os.path.join(tex_root(), "not_a_folder")
        with open(blocker, "w") as f:
            f.write("x")
        import_file(material_file("bufferView"), "u", options="shader=%s;textureDir=%s"
                    % (self.shader, blocker.replace("\\", "/")))
        r = importer.last_result()
        self.assertTrue(any("Could not extract" in w for w in r.reporter.warnings), r.reporter.warnings)
        self.assertEqual(cmds.ls("u:*", type="file"), [])
        self.assertEqual(len(r.shaders), len(fixtures.MATERIAL_NAMES) - 1)  # untextured materials

    def test_external_and_missing_textures(self):
        src = material_file("uri", glb=False)
        d = os.path.dirname(src)
        self.load(mode="uri", glb=False)
        files = self.file_nodes()
        self.assertEqual(os.path.normcase(cmds.getAttr(files["checker_file"] + ".fileTextureName")),
                         os.path.normcase(os.path.join(d, "checker.png").replace("\\", "/")))
        # a missing external image is still linked (relinkable) and reported
        cmds.file(new=True, force=True)
        moved = os.path.join(d, "packed.png")
        shutil.move(moved, moved + ".bak")
        try:
            self.load(mode="uri", glb=False)
            self.assertIn("packed_file", self.file_nodes())
            self.assertTrue(any("not found" in w for w in self.result.reporter.warnings))
            linked, missing, skipped = self.result.texture_stats()
            self.assertEqual((linked, missing), (3, 1))
        finally:
            shutil.move(moved + ".bak", moved)

    def test_one_compact_dropped_warning(self):
        self.load()
        dropped = [w for w in self.result.reporter.warnings if w.startswith("Material features")]
        self.assertEqual(len(dropped), 1)
        self.assertIn("occlusionTexture (1 material)", dropped[0])
        self.assertIn("KHR_materials_iridescence", dropped[0])
        webp = [w for w in self.result.reporter.warnings if "WebP" in w]
        self.assertEqual(len(webp), 1)


class TestMaterialReference(MaterialTestCase):
    def test_reference_owns_everything_and_edits_survive(self):
        src = material_file("bufferView")
        cmds.workspace(fixture_dir(), openWorkspace=True)
        before = set(cmds.ls())
        reference_file(src, "rm")
        new = set(cmds.ls()) - before
        ref_node = cmds.referenceQuery(src, referenceNode=True)
        owned = set(cmds.ls(cmds.referenceQuery(ref_node, nodes=True)))
        r = importer.last_result()
        self.assertEqual(len(r.shaders), len(fixtures.MATERIAL_NAMES) - 1)
        shading_nodes = [n for n in new if cmds.nodeType(n) in SHADING_TYPES or n in r.shaders.values()]
        self.assertTrue(len(shading_nodes) > 20, shading_nodes)
        for n in new:
            if n == ref_node or cmds.nodeType(n) == "reference":
                continue
            self.assertTrue(n.startswith("rm:"), n)
            self.assertIn(n, owned, n)
        red = "rm:Red"
        base = attr(r.shader_type, "base_color")
        cmds.setAttr("%s.%s" % (red, base), 0, 1, 0, type="double3")
        ma = os.path.join(fixture_dir(), "matref.ma")
        cmds.file(rename=ma)
        cmds.file(save=True, type="mayaAscii", force=True)
        cmds.file(new=True, force=True)
        cmds.file(ma, open=True, force=True)
        self.assertVecAlmost(cmds.getAttr("%s.%s" % (red, base))[0], (0, 1, 0))
        self.assertTrue(cmds.sets("rm:MultiShape.f[0]", isMember="rm:RedSG"))
        self.assertTrue(cmds.sets("rm:MultiShape.f[1:2]", isMember="rm:TexturedSG"))
        self.assertTrue(cmds.sets("rm:MixedShape.f[1]", isMember="initialShadingGroup"))
        self.assertTrue(cmds.referenceQuery("rm:RedSG", isNodeReferenced=True))
        self.assertTrue(cmds.referenceQuery("rm:Checker_file", isNodeReferenced=True))
        self.assertTrue(os.path.isfile(cmds.getAttr("rm:Checker_file.fileTextureName")))
        self.assertEqual(cmds.nodeType(source_of("rm:Checker_place2d_1.uvCoord")), "uvChooser")
