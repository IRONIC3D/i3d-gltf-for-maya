"""Third-party renderer shaders (milestone 5): RenderMan's PxrSurface and 3Delight's dlPrincipled.

The fallback and availability logic is tested everywhere through the shading hooks. The
network tests load the real plug-in and skip where it is not installed or does not load
(this file sorts last, so the other suites run without these plug-ins loaded)."""

import os
import tempfile

import maya.cmds as cmds

import fixtures
from maya_helpers import MayaTestCase, fixture_dir, import_file

from i3d_gltf.core import Reporter
from i3d_gltf.core.materials import manifold2d_from_transform
from i3d_gltf.maya import importer, shading

RFM = shading.RENDERMAN_PLUGIN
DL = shading.DELIGHT_PLUGIN


def strip(name):
    return name.split("|")[-1].split(":")[-1]


def source_of(plug):
    src = cmds.listConnections(plug, source=True, destination=False, plugs=True, skipConversionNodes=True)
    return src[0] if src else None


_src = {}


def material_file():
    if "glb" not in _src:
        d = tempfile.mkdtemp(prefix="rmat_", dir=fixture_dir())
        _src["glb"] = fixtures.materials_builder("bufferView").write_glb(os.path.join(d, "mats.glb"))
    return _src["glb"]


def tex_root():
    return tempfile.mkdtemp(prefix="rtex_", dir=fixture_dir())


class Hooks(object):
    """Replace shading's node/plug-in hooks for one test."""

    def __init__(self, missing_types=(), load_result=False, installed=True):
        self.missing = set(missing_types)
        self.load_result = load_result
        self.installed = installed
        self.loads = []

    def __enter__(self):
        self.saved = (shading.node_type_exists, shading.load_plugin, shading.plugin_installed, shading.plugin_loaded)
        real_exists = self.saved[0]
        shading.node_type_exists = lambda t: t not in self.missing and real_exists(t)
        shading.load_plugin = lambda p: self.loads.append(p) or self.load_result
        shading.plugin_installed = lambda p: self.installed
        shading.plugin_loaded = lambda p: False
        return self

    def __exit__(self, *exc):
        (shading.node_type_exists, shading.load_plugin, shading.plugin_installed, shading.plugin_loaded) = self.saved


class TestRendererFallback(MayaTestCase):
    def load(self, shader, ns="f"):
        import_file(material_file(), ns, options="shader=%s;textureDir=%s" % (shader, tex_root().replace("\\", "/")))
        return importer.last_report()

    def check_fallback(self, shader, plugin, renderer):
        with Hooks(missing_types=[shader], load_result=False) as h:
            rep = self.load(shader)
        self.assertTrue(rep["ok"])
        self.assertEqual(rep["shaderType"], "standardSurface")
        self.assertEqual(h.loads, [plugin])  # tried to load the renderer once
        fallback = [w for w in rep["warnings"] if w.startswith("Shader '")]
        self.assertEqual(fallback, ["Shader '%s' is not available (the %s plug-in (%s) is not available); "
                                    "using 'standardSurface' instead" % (shader, renderer, plugin)])
        for m in rep["materials"]:
            self.assertEqual(cmds.nodeType(m["shader"]), "standardSurface")

    def test_pxrsurface_without_renderman(self):
        self.check_fallback("PxrSurface", RFM, "RenderMan")

    def test_dlprincipled_without_3delight(self):
        self.check_fallback("dlPrincipled", DL, "3Delight")

    def test_installed_but_fails_to_load(self):
        # The plug-in is found (the options window says "loaded on import") but loadPlugin
        # fails, or loads without registering the node: the import still falls back.
        for load_result in (False, True):
            cmds.file(new=True, force=True)
            with Hooks(missing_types=["PxrSurface"], load_result=load_result, installed=True) as h:
                avail = dict((v, (s, f)) for v, s, f in shading.shader_availability(["PxrSurface"]))
                self.assertEqual(avail["PxrSurface"], (shading.LOADED_ON_IMPORT, None))
                rep = self.load("PxrSurface")
            self.assertEqual(h.loads, [RFM])
            self.assertEqual(rep["shaderType"], "standardSurface")
            self.assertEqual(len([w for w in rep["warnings"] if "RenderMan_for_Maya" in w]), 1)

    def test_resolve_never_loads_when_present(self):
        with Hooks(load_result=False) as h:
            h.missing = set()
            shading.node_type_exists = lambda t: t in ("PxrSurface", "standardSurface")
            r = Reporter()
            self.assertEqual(shading.resolve_shader_type("PxrSurface", r), "PxrSurface")
        self.assertEqual(h.loads, [])
        self.assertEqual(r.warnings, [])


def _real(plugin, node_type):
    if not shading.load_plugin(plugin) or not shading.node_type_exists(node_type):
        raise __import__("unittest").SkipTest("%s does not load in this Maya" % plugin)


class TestPxrSurface(MayaTestCase):
    @classmethod
    def setUpClass(cls):
        MayaTestCase.setUpClass()
        _real(RFM, "PxrSurface")

    def load(self, ns="p", src=None):
        src = src or material_file()
        import_file(src, ns, options="shader=PxrSurface;textureDir=%s" % tex_root().replace("\\", "/"))
        self.report = importer.last_report()
        self.result = importer.last_result()
        self.ns = ns
        return self.report

    def n(self, name):
        return "%s:%s" % (self.ns, name)

    def test_report_and_types(self):
        rep = self.load()
        self.assertTrue(rep["ok"])
        self.assertEqual(rep["shaderType"], "PxrSurface")
        self.assertEqual(sorted(strip(m["shader"]) for m in rep["materials"]), sorted(fixtures.MATERIAL_NAMES[:-1]))
        for m in rep["materials"]:
            self.assertEqual(cmds.nodeType(m["shader"]), "PxrSurface")
            self.assertEqual(source_of(m["shadingGroup"] + ".surfaceShader"), m["shader"] + ".outColor")
            self.assertEqual(cmds.getAttr(m["shader"] + ".specularFresnelMode"), 0)  # Artistic
            self.assertEqual(cmds.getAttr(m["shader"] + ".specularModelType"), 1)    # GGX
        nodes = [t["node"] for t in rep["textures"] if t["node"]]
        self.assertEqual(sorted(strip(n) for n in nodes), ["Checker_tex", "Checker_tex_1", "checker_tex"])
        for n in nodes:
            self.assertEqual(cmds.nodeType(n), "PxrTexture")
        self.assertFalse([w for w in rep["warnings"] if w.startswith("Shader '")])

    def test_textures_linearize_and_wrap(self):
        self.load()
        self.assertTrue(cmds.getAttr(self.n("Checker_tex.linearize")))      # base colour / emissive: sRGB
        self.assertFalse(cmds.getAttr(self.n("checker_tex.linearize")))     # metal-rough + normal: raw
        path = cmds.getAttr(self.n("Checker_tex.filename"))
        self.assertTrue(os.path.isfile(path), path)
        if cmds.attributeQuery("swrap", node=self.n("Checker_tex"), exists=True):
            self.assertEqual(cmds.getAttr(self.n("Checker_tex.swrap")), "mirror")
            self.assertEqual(cmds.getAttr(self.n("Checker_tex.twrap")), "clamp")
            self.assertEqual(cmds.getAttr(self.n("checker_tex.swrap")), "periodic")
        else:
            self.assertTrue(any("no wrap mode" in w for w in self.report["warnings"]))

    def test_metal_roughness_and_factors(self):
        self.load()
        sh = self.n("Textured")
        mw = source_of(sh + ".diffuseColor").split(".")[0]
        self.assertEqual(strip(mw), "Textured_metallicWorkflow")
        self.assertEqual(cmds.nodeType(mw), "PxrMetallicWorkflow")
        self.assertEqual(source_of(sh + ".specularFaceColor"), mw + ".resultSpecularFaceRGB")
        self.assertEqual(source_of(sh + ".specularEdgeColor"), mw + ".resultSpecularEdgeRGB")
        self.assertAlmostEqual(cmds.getAttr(mw + ".specular"), 0.5, 5)  # IOR 1.5
        # base colour x 0.5 through PxrArithmetic
        mul = source_of(mw + ".baseColor").split(".")[0]
        self.assertEqual(cmds.nodeType(mul), "PxrArithmetic")
        self.assertEqual(cmds.getAttr(mul + ".operation"), 1)
        self.assertEqual(source_of(mul + ".input1"), self.n("Checker_tex.resultRGB"))
        self.assertEqual([round(x, 5) for x in cmds.getAttr(mul + ".input2")[0]], [0.5, 0.5, 0.5])
        # packed: B -> metallic (x 0.5), G -> roughness (factor 1: direct)
        metal = source_of(mw + ".metallic").split(".")[0]
        self.assertEqual(source_of(metal + ".input1R"), self.n("checker_tex.resultB"))
        self.assertAlmostEqual(cmds.getAttr(metal + ".input2R"), 0.5, 5)
        self.assertEqual(source_of(sh + ".specularRoughness"), self.n("checker_tex.resultG"))
        # constants
        red = self.n("Red_metallicWorkflow")
        self.assertEqual([round(x, 5) for x in cmds.getAttr(red + ".baseColor")[0]], [1, 0, 0])
        self.assertAlmostEqual(cmds.getAttr(red + ".metallic"), 0.25, 5)
        self.assertAlmostEqual(cmds.getAttr(self.n("Red.specularRoughness")), 0.75, 5)
        # IOR 1.4 and specularFactor 0.8 -> specular level
        f0 = ((1.4 - 1) / (1.4 + 1)) ** 2
        self.assertAlmostEqual(cmds.getAttr(self.n("Fancy_metallicWorkflow.specular")), f0 / 0.08 * 0.8, 4)

    def test_normal_emission_alpha(self):
        self.load()
        sh = self.n("Textured")
        nm = source_of(sh + ".bumpNormal").split(".")[0]
        self.assertEqual(strip(nm), "Textured_normal_normalMap")
        self.assertEqual(cmds.nodeType(nm), "PxrNormalMap")
        self.assertEqual(source_of(nm + ".inputRGB"), self.n("checker_tex.resultRGB"))
        self.assertEqual(cmds.getAttr(nm + ".orientation"), 0)  # OpenGL
        self.assertAlmostEqual(cmds.getAttr(nm + ".bumpScale"), 0.8, 5)
        # emission: colour x factor, strength 2
        self.assertAlmostEqual(cmds.getAttr(sh + ".glowGain"), 2.0, 5)
        em = source_of(sh + ".glowColor").split(".")[0]
        self.assertEqual([round(x, 5) for x in cmds.getAttr(em + ".input2")[0]], [1, 0.5, 0.25])
        self.assertAlmostEqual(cmds.getAttr(self.n("Red.glowGain")), 0.0)
        # MASK: PxrThreshold on the base colour alpha
        th = source_of(self.n("Masked.presence")).split(".")[0]
        self.assertEqual(cmds.nodeType(th), "PxrThreshold")
        self.assertEqual(source_of(th + ".inputRGBR"), self.n("Checker_tex.resultA"))
        self.assertAlmostEqual(cmds.getAttr(th + ".threshold"), 0.3, 5)
        self.assertEqual(cmds.getAttr(th + ".transitionWidth"), 0.0)
        # BLEND constant, OPAQUE untouched, BLEND textured (unlit)
        self.assertAlmostEqual(cmds.getAttr(self.n("Blend.presence")), 0.5, 5)
        self.assertAlmostEqual(cmds.getAttr(self.n("Red.presence")), 1.0, 5)
        self.assertEqual(source_of(self.n("Unlit.presence")), self.n("Checker_tex.resultA"))
        # unlit: glow only
        self.assertAlmostEqual(cmds.getAttr(self.n("Unlit.diffuseGain")), 0.0)
        self.assertIsNone(source_of(self.n("Unlit.specularFaceColor")))
        self.assertAlmostEqual(cmds.getAttr(self.n("Unlit.glowGain")), 1.0)

    def test_double_sided(self):
        self.load()
        for attr in shading.PxrSurfaceBuilder.DOUBLE_SIDED:
            self.assertTrue(cmds.getAttr(self.n("Red.%s" % attr)), attr)
            self.assertFalse(cmds.getAttr(self.n("Textured.%s" % attr)), attr)
        self.assertTrue(cmds.getAttr(self.n("MultiShape.doubleSided")))
        self.assertFalse(cmds.getAttr(self.n("SingleShape.doubleSided")))

    def test_transform_and_uv_set_manifold(self):
        self.load()
        tex = self.n("Checker_tex_1")
        m = source_of(tex + ".manifold").split(".")[0]
        self.assertEqual(strip(m), "Checker_manifold")
        self.assertEqual(cmds.nodeType(m), "PxrManifold2D")
        t = fixtures.TRANSFORM
        want = manifold2d_from_transform(t["offset"], t["rotation"], t["scale"])
        got = [cmds.getAttr(m + "." + a) for a in ("scaleS", "scaleT", "offsetS", "offsetT", "angle")]
        for g, w in zip(got, want):
            self.assertAlmostEqual(g, w, 4)
        self.assertTrue(cmds.getAttr(m + ".invertT"))
        self.assertEqual(cmds.getAttr(m + ".name_uvSet"), "map2")
        self.assertIsNone(source_of(self.n("Checker_tex.manifold")))  # no transform: no manifold
        self.assertEqual(source_of(self.n("Transformed_metallicWorkflow.baseColor")), tex + ".resultRGB")

    def test_spec_gloss_and_clearcoat(self):
        self.load()
        sg = self.n("SpecGloss")
        self.assertIsNone(source_of(sg + ".diffuseColor"))
        self.assertEqual([round(x, 5) for x in cmds.getAttr(sg + ".diffuseColor")[0]], [0.2, 0.4, 0.6])
        self.assertEqual([round(x, 5) for x in cmds.getAttr(sg + ".specularFaceColor")[0]], [0.3, 0.3, 0.3])
        self.assertEqual([round(x, 5) for x in cmds.getAttr(sg + ".specularEdgeColor")[0]], [1, 1, 1])
        self.assertAlmostEqual(cmds.getAttr(sg + ".specularRoughness"), 0.7, 5)
        fancy = self.n("Fancy")
        self.assertAlmostEqual(cmds.getAttr(fancy + ".clearcoatFaceColor")[0][0], 0.7 * 0.04, 5)
        self.assertAlmostEqual(cmds.getAttr(fancy + ".clearcoatEdgeColor")[0][0], 0.7, 5)
        self.assertAlmostEqual(cmds.getAttr(fancy + ".clearcoatRoughness"), 0.2, 5)
        self.assertAlmostEqual(cmds.getAttr(fancy + ".refractionGain"), 0.5, 5)
        self.assertAlmostEqual(cmds.getAttr(fancy + ".glassIor"), 1.4, 5)
        self.assertAlmostEqual(cmds.getAttr(fancy + ".fuzzGain"), 1.0, 5)

    def test_deterministic_names_and_no_files_next_to_source(self):
        src = material_file()
        before = sorted(os.listdir(os.path.dirname(src)))
        names = []
        for ns in ("a", "b"):
            self.load(ns=ns, src=src)
            names.append(sorted(strip(x) for x in cmds.ls(ns + ":*") if cmds.nodeType(x).startswith("Pxr")))
        self.assertTrue(names[0])
        self.assertEqual(names[0], names[1])
        self.assertIn("Textured_metallicWorkflow", names[0])
        self.assertEqual(sorted(os.listdir(os.path.dirname(src))), before)  # no .tex, no extraction

    def test_reference(self):
        src = material_file()
        cmds.workspace(fixture_dir(), openWorkspace=True)
        cmds.file(src, reference=True, type="IRONIC3D glTF", namespace="rp", ignoreVersion=True,
                  mergeNamespacesOnClash=False, options="shader=PxrSurface")
        rep = importer.last_report()
        self.assertTrue(rep["ok"])
        self.assertEqual(rep["shaderType"], "PxrSurface")
        self.assertTrue(cmds.referenceQuery("rp:Textured_metallicWorkflow", isNodeReferenced=True))
        self.assertTrue(cmds.referenceQuery("rp:Checker_tex", isNodeReferenced=True))


class TestDlPrincipled(MayaTestCase):
    @classmethod
    def setUpClass(cls):
        MayaTestCase.setUpClass()
        _real(DL, "dlPrincipled")

    def load(self, ns="d"):
        import_file(material_file(), ns, options="shader=dlPrincipled;textureDir=%s" % tex_root().replace("\\", "/"))
        self.report = importer.last_report()
        self.ns = ns
        return self.report

    def n(self, name):
        return "%s:%s" % (self.ns, name)

    def test_network(self):
        rep = self.load()
        self.assertEqual(rep["shaderType"], "dlPrincipled")
        for m in rep["materials"]:
            self.assertEqual(cmds.nodeType(m["shader"]), "dlPrincipled")
        sh = self.n("Textured")
        mul = source_of(sh + ".color").split(".")[0]
        self.assertEqual(cmds.nodeType(mul), "multiplyDivide")
        self.assertEqual(source_of(mul + ".input1"), self.n("Checker_file.outColor"))
        self.assertEqual(source_of(sh + ".roughness"), self.n("checker_file.outColorG"))
        self.assertEqual(source_of(source_of(sh + ".metallic").split(".")[0] + ".input1X"),
                         self.n("checker_file.outColorB"))
        # normal map: OpenGL type, scale as intensity, raw file colour
        self.assertEqual(source_of(sh + ".disp_normal_bump_value"), self.n("checker_file.outColor"))
        self.assertEqual(cmds.getAttr(sh + ".disp_normal_bump_type"), 2)
        self.assertAlmostEqual(cmds.getAttr(sh + ".disp_normal_bump_intensity"), 0.8, 5)
        # colour spaces: same file nodes as the Arnold path
        spaces = shading.available_color_spaces()
        self.assertEqual(cmds.getAttr(self.n("Checker_file.colorSpace")), shading.pick_color_space("sRGB", spaces))
        self.assertEqual(cmds.getAttr(self.n("checker_file.colorSpace")), shading.pick_color_space("Raw", spaces))
        # emission, specular level, alpha
        self.assertAlmostEqual(cmds.getAttr(sh + ".incandescence_intensity"), 2.0, 5)
        self.assertAlmostEqual(cmds.getAttr(sh + ".specular_level"), 0.5, 5)
        cond = source_of(self.n("Masked.opacity")).split(".")[0]
        self.assertEqual(cmds.nodeType(cond), "condition")
        self.assertAlmostEqual(cmds.getAttr(self.n("Blend.opacity")), 0.5, 5)
        # constants, unlit, extensions
        self.assertEqual([round(x, 5) for x in cmds.getAttr(self.n("Red.color"))[0]], [1, 0, 0])
        self.assertAlmostEqual(cmds.getAttr(self.n("Red.metallic")), 0.25, 5)
        self.assertEqual([round(x, 5) for x in cmds.getAttr(self.n("Unlit.color"))[0]], [0, 0, 0])
        self.assertAlmostEqual(cmds.getAttr(self.n("Unlit.specular_level")), 0.0, 5)
        self.assertAlmostEqual(cmds.getAttr(self.n("Fancy.refract_weight")), 0.5, 5)
        self.assertAlmostEqual(cmds.getAttr(self.n("Fancy.refract_ior")), 1.4, 5)
        self.assertAlmostEqual(cmds.getAttr(self.n("Fancy.coating_thickness")), 0.7, 5)
        self.assertAlmostEqual(cmds.getAttr(self.n("Fancy.sheen_level")), 1.0, 5)
        # UV set link and transform as on the Arnold path
        self.assertEqual(cmds.nodeType(source_of(self.n("Checker_place2d_1.uvCoord"))), "uvChooser")
