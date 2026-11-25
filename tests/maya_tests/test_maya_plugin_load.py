"""Plug-in loading regressions.

Maya does not define ``__file__`` when it runs a Python plug-in, so loading the plug-in by
absolute path without the module (repo not on MAYA_MODULE_PATH, scripts/ not on sys.path)
used to crash. The rest of the suite always runs with the module installed, so this test
starts a separate mayapy without it.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap

import maya.cmds as cmds

from maya_helpers import PLUGIN, MayaTestCase, fixture_dir, scene_files

import i3d_gltf

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CHILD = textwrap.dedent('''
    import importlib.util, json, os, sys, traceback
    out, plugin, gltf = sys.argv[1:4]
    res = {}
    try:
        import maya.standalone
        maya.standalone.initialize(name="python")
        import maya.cmds as cmds
        res["importable_before_load"] = importlib.util.find_spec("i3d_gltf") is not None
        res["module_installed"] = "i3dGltf" in (cmds.moduleInfo(listModules=True) or [])
        cmds.loadPlugin(plugin)
        res["version"] = cmds.pluginInfo("i3dGltf", q=True, version=True)
        res["vendor"] = cmds.pluginInfo("i3dGltf", q=True, vendor=True)
        cmds.file(gltf, i=True, type="IRONIC3D glTF", namespace="p", options="shader=none")
        res["transforms"] = sorted(cmds.ls("p:*", type="transform"))
    except Exception:
        res["error"] = traceback.format_exc()
    with open(out, "w") as f:
        json.dump(res, f)
    sys.stdout.flush()
    if sys.platform == "win32":
        import ctypes
        k = ctypes.WinDLL("kernel32")
        k.TerminateProcess(ctypes.c_void_p(k.GetCurrentProcess()), 0)
    os._exit(0)
''')


class TestPluginLoadByPath(MayaTestCase):
    def test_load_by_absolute_path_without_module(self):
        work = tempfile.mkdtemp(prefix="bypath_", dir=fixture_dir())
        script = os.path.join(work, "child.py")
        with open(script, "w") as f:
            f.write(CHILD)
        out = os.path.join(work, "result.json")
        env = dict(os.environ)
        norm = os.path.normcase(os.path.normpath(REPO))
        for key in ("MAYA_MODULE_PATH", "PYTHONPATH", "MAYA_PLUG_IN_PATH", "MAYA_SCRIPT_PATH"):
            parts = [p for p in env.get(key, "").split(os.pathsep)
                     if p and not os.path.normcase(os.path.normpath(p)).startswith(norm)]
            if parts:
                env[key] = os.pathsep.join(parts)
            else:
                env.pop(key, None)
        env["MAYA_SKIP_USERSETUP_PY"] = "1"
        plugin = os.path.join(REPO, "plug-ins", "i3dGltf.py").replace("\\", "/")
        proc = subprocess.run([sys.executable, script, out, plugin, scene_files()[0]], env=env, cwd=work,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=600)
        self.assertTrue(os.path.exists(out), proc.stdout.decode("utf-8", "replace")[-3000:])
        with open(out) as f:
            res = json.load(f)
        self.assertNotIn("error", res, res.get("error"))
        self.assertFalse(res["module_installed"])
        self.assertFalse(res["importable_before_load"])
        self.assertEqual(res["version"], i3d_gltf.VERSION)
        self.assertEqual(res["vendor"], "IRONIC3D")
        self.assertIn("p:Root_Node_", res["transforms"])


class TestPluginInfo(MayaTestCase):
    def test_version_and_vendor(self):
        version = cmds.pluginInfo(PLUGIN, q=True, version=True)
        self.assertRegex(version, r"^\d+\.\d+\.\d+$")
        self.assertEqual(version, i3d_gltf.VERSION)
        self.assertEqual(cmds.pluginInfo(PLUGIN, q=True, vendor=True), "IRONIC3D")
        self.assertEqual(i3d_gltf.VENDOR, "IRONIC3D")
        with open(os.path.join(REPO, "i3dGltf.mod")) as f:
            self.assertEqual(re.split(r"\s+", f.read().strip())[2], i3d_gltf.VERSION)
