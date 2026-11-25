"""Import options window wiring (the parts that work without a GUI), shader availability
and joint display radius. The window itself is exercised in GUI Maya by
tests/gui/gui_options_dialog.py."""

import os
import tempfile

import maya.cmds as cmds
import maya.mel as mel

import fixtures
from maya_helpers import T, MayaTestCase, fixture_dir, import_file

from i3d_gltf.core.options import IMPORT_OPTION_SPECS, default_options_string, dialog_string, dialog_values
from i3d_gltf.maya import importer, options_ui, shading

SHADERS = [s for s in IMPORT_OPTION_SPECS if s.name == "shader"][0].choices


class TestOptionsScriptWiring(MayaTestCase):
    def test_registered_options_script(self):
        self.assertEqual(mel.eval('translator -q -optionsScript "%s"' % T), "i3dGltfImportOptions")
        self.assertEqual(mel.eval('translator -q -defaultOptions "%s"' % T), default_options_string())
        self.assertTrue(mel.eval("exists i3dGltfImportOptions"))

    def test_query_through_mel_round_trips(self):
        # what Maya's file dialog does: call the MEL procedure with a result callback
        mel.eval('global string $i3dTestOpts; global proc i3dTestCallback(string $o) '
                 '{ global string $i3dTestOpts; $i3dTestOpts = $o; }')
        text = ("scale=0.5;importNormals=0;importColors=1;shader=standardSurface;"
                "textureDir=D:/my textures/é;animation=Walk Cycle;animSampling=bake;timeRange=0")
        options_ui._state.update(controls={}, posted=text + ";unknownKey=3")
        mel.eval('i3dGltfImportOptions "" "query" "" "i3dTestCallback"')
        got = mel.eval("$tmp = $i3dTestOpts")
        self.assertEqual(got, text)
        self.assertEqual(dialog_values(got), dialog_values(text))
        # other actions are declined
        self.assertEqual(mel.eval('i3dGltfImportOptions "" "fileselected" "" "i3dTestCallback"'), 0)

    def test_remembered_dialog_settings_do_not_reach_scripted_calls(self):
        # Maya's file dialogs store the last window settings in "<translator>Options"; a
        # host's cmds.file call must still get the registered defaults + its own options.
        from maya_helpers import scene_files
        var = T + "Options"
        old = cmds.optionVar(q=var) if cmds.optionVar(exists=var) else None
        cmds.optionVar(stringValue=(var, "scale=7;shader=none;animation=none"))
        try:
            import_file(scene_files()[0], "rem")
            res = importer.last_result()
            self.assertEqual(res.options.scale, 1.0)
            self.assertEqual(res.options.shader, "aiStandardSurface")
        finally:
            if old is None:
                cmds.optionVar(remove=var)
            else:
                cmds.optionVar(stringValue=(var, old))

    def test_menu_lists_every_shader(self):
        avail = shading.shader_availability(SHADERS, exists=lambda t: t == "standardSurface",
                                            arnold_is_loaded=False, arnold_is_installed=False)
        items = options_ui.shader_menu(SHADERS, avail)
        self.assertEqual([v for v, _ in items], list(SHADERS))
        labels = dict(items)
        self.assertEqual(labels["standardSurface"], "standardSurface (Maya)")
        self.assertEqual(labels["aiStandardSurface"], "aiStandardSurface - not available, uses standardSurface")
        self.assertEqual(labels["none"], "none (no materials)")

    def test_grouping_follows_spec_table(self):
        groups = options_ui.grouped_specs()
        self.assertEqual([g for g, _ in groups], ["Geometry", "Materials", "Animation"])
        self.assertEqual(sum(len(s) for _, s in groups), len(IMPORT_OPTION_SPECS))


class TestShaderAvailability(MayaTestCase):
    def test_maya_2024_like_session_with_arnold_installed(self):
        calls = []

        def installed():
            calls.append(1)
            return True

        old = shading.arnold_installed
        shading.arnold_installed = installed
        try:
            avail = shading.shader_availability(SHADERS, exists=lambda t: t == "standardSurface",
                                                arnold_is_loaded=False)
        finally:
            shading.arnold_installed = old
        self.assertEqual(avail, [("aiStandardSurface", shading.ARNOLD_ON_IMPORT, None),
                                 ("aiOpenPBRSurface", shading.ARNOLD_ON_IMPORT, None),
                                 ("openPBRSurface", shading.UNAVAILABLE, "aiOpenPBRSurface"),
                                 ("standardSurface", shading.AVAILABLE, None),
                                 ("none", shading.AVAILABLE, None)])
        self.assertEqual(len(calls), 1)  # looked up once, lazily

    def test_no_arnold(self):
        avail = dict((v, (s, f)) for v, s, f in shading.shader_availability(
            SHADERS, exists=lambda t: t in ("standardSurface", "openPBRSurface"), arnold_is_loaded=False,
            arnold_is_installed=False))
        self.assertEqual(avail["aiStandardSurface"], (shading.UNAVAILABLE, "standardSurface"))
        self.assertEqual(avail["aiOpenPBRSurface"], (shading.UNAVAILABLE, "openPBRSurface"))
        self.assertEqual(avail["openPBRSurface"], (shading.AVAILABLE, None))

    def test_arnold_loaded_without_openpbr(self):
        avail = dict((v, (s, f)) for v, s, f in shading.shader_availability(
            SHADERS, exists=lambda t: t in ("standardSurface", "aiStandardSurface"), arnold_is_loaded=True))
        self.assertEqual(avail["aiOpenPBRSurface"], (shading.UNAVAILABLE, "standardSurface"))
        self.assertEqual(avail["aiStandardSurface"], (shading.AVAILABLE, None))

    def test_real_session_never_loads_arnold(self):
        loaded_before = cmds.pluginInfo("mtoa", q=True, loaded=True)
        avail = shading.shader_availability(SHADERS)
        self.assertEqual(cmds.pluginInfo("mtoa", q=True, loaded=True), loaded_before)
        self.assertEqual(dict((v, s) for v, s, _ in avail)["standardSurface"], shading.AVAILABLE)
        if not loaded_before:
            self.assertIn(dict((v, s) for v, s, _ in avail)["aiStandardSurface"],
                          (shading.ARNOLD_ON_IMPORT, shading.UNAVAILABLE))


class TestJointRadius(MayaTestCase):
    def test_radius_relative_to_skeleton(self):
        path = fixtures.write_rig(tempfile.mkdtemp(prefix="rig_radius_", dir=fixture_dir()), True)
        import_file(path, "r", options="animation=none")
        res = importer.last_result()
        radii = {}
        for i in res.prepared.plan.joints:
            node = res.prepared.plan.nodes[i]
            radii[node.name] = cmds.getAttr("r:%s.radius" % node.name)
            self.assertAlmostEqual(radii[node.name], node.joint_radius, 6)
        self.assertTrue(radii)
        self.assertNotIn(1.0, radii.values())
        # deterministic: a second import gives the same radii
        import_file(path, "r2", options="animation=none")
        for name, r in radii.items():
            self.assertEqual(cmds.getAttr("r2:%s.radius" % name), r)
        # scaling the import scales the radius with it
        import_file(path, "r3", options="animation=none;scale=2")
        for name, r in radii.items():
            self.assertAlmostEqual(cmds.getAttr("r3:%s.radius" % name), r * 2, delta=r * 2e-3)
