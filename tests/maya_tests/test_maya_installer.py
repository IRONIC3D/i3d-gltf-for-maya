"""The release installer, end to end, in an isolated Maya user folder.

The release zip is built and extracted to a folder whose path has spaces and non-ASCII
characters; MAYA_APP_DIR points to another such folder (standing in for a Documents folder
redirected into OneDrive). Every step runs install_i3dGltf.py in a separate mayapy
(this interpreter) without the repository on any Maya path, then a new mayapy with only
that user folder proves Maya finds the module by itself.
"""

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
import zipfile

from maya_helpers import MayaTestCase, fixture_dir, scene_files

import i3d_gltf

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OLD = "0.3.9"

CHILD_PRELUDE = textwrap.dedent('''
    import json, os, sys, traceback
    res = {}
    def done(out):
        with open(out, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False)
        sys.stdout.flush()
        import ctypes
        k = ctypes.WinDLL("kernel32") if sys.platform == "win32" else None
        if k:
            k.TerminateProcess(ctypes.c_void_p(k.GetCurrentProcess()), 0)
        os._exit(0)
''')

LOAD_CHILD = CHILD_PRELUDE + textwrap.dedent('''
    out, gltf = sys.argv[1:3]
    try:
        import maya.standalone
        maya.standalone.initialize(name="python")
        import maya.cmds as cmds, maya.mel as mel
        res["appDir"] = cmds.internalVar(userAppDir=True)
        res["moduleListed"] = "i3dGltf" in (cmds.moduleInfo(listModules=True) or [])
        res["modulePath"] = cmds.moduleInfo(path=True, moduleName="i3dGltf") if res["moduleListed"] else None
        res["moduleSearchPath"] = mel.eval('getenv "MAYA_MODULE_PATH"')
        cmds.loadPlugin("i3dGltf")
        res["version"] = cmds.pluginInfo("i3dGltf", q=True, version=True)
        res["pluginPath"] = cmds.pluginInfo("i3dGltf", q=True, path=True)
        res["optionsScript"] = mel.eval('translator -q -optionsScript "IRONIC3D glTF"')
        cmds.file(gltf, i=True, type="IRONIC3D glTF", namespace="p", options="shader=none")
        res["transforms"] = sorted(cmds.ls("p:*", type="transform"))
        import i3d_gltf
        res["package"] = i3d_gltf.__file__
    except Exception:
        res["error"] = traceback.format_exc()
    done(out)
''')

UPGRADE_CHILD = CHILD_PRELUDE + textwrap.dedent('''
    out, gltf = sys.argv[1:3]
    installer = os.environ["I3D_INSTALLER"]
    try:
        import importlib.util
        import maya.standalone
        maya.standalone.initialize(name="python")
        import maya.cmds as cmds
        cmds.loadPlugin("i3dGltf")
        import i3d_gltf
        res["before"] = [cmds.pluginInfo("i3dGltf", q=True, version=True), i3d_gltf.VERSION]
        spec = importlib.util.spec_from_file_location("inst", installer)
        inst = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(inst)
        app = inst.maya_app_dir()
        # the steps the drag-and-drop installer runs
        res["previouslyLoaded"] = inst.unload_if_loaded()
        res["install"] = inst.install_files(app, inst.bundle_module(os.path.dirname(installer)))
        res["loaded"], res["loadError"] = inst.load_installed(app)
        import i3d_gltf
        res["after"] = [cmds.pluginInfo("i3dGltf", q=True, version=True), i3d_gltf.VERSION]
        cmds.file(gltf, i=True, type="IRONIC3D glTF", namespace="u", options="shader=none")
        res["transforms"] = sorted(cmds.ls("u:*", type="transform"))
    except Exception:
        res["error"] = traceback.format_exc()
    done(out)
''')


class TestInstaller(MayaTestCase):
    @classmethod
    def setUpClass(cls):
        super(TestInstaller, cls).setUpClass()
        spec = importlib.util.spec_from_file_location("build_release", os.path.join(REPO, "tools", "build_release.py"))
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        cls.work = tempfile.mkdtemp(prefix="installer_", dir=fixture_dir())
        zip_path = builder.build(os.path.join(cls.work, "dist"))
        cls.pkg = os.path.join(cls.work, "Downloads é ü", "i3dGltf %s" % i3d_gltf.VERSION)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(cls.pkg)
        cls.installer = os.path.join(cls.pkg, "install_i3dGltf.py")
        # an older release: same files, lower version, plus a file the new one no longer has
        cls.old_pkg = os.path.join(cls.work, "old package", "i3dGltf %s" % OLD)
        shutil.copytree(cls.pkg, cls.old_pkg)
        init = os.path.join(cls.old_pkg, "i3dGltf", "scripts", "i3d_gltf", "__init__.py")
        with open(init, encoding="utf-8") as f:
            text = f.read()
        with open(init, "w", encoding="utf-8") as f:
            f.write(re.sub(r'VERSION = "[^"]+"', 'VERSION = "%s"' % OLD, text))
        with open(os.path.join(cls.old_pkg, "i3dGltf", "scripts", "i3d_gltf", "old_only.py"), "w") as f:
            f.write("# only in the old release\n")
        cls.app = os.path.join(cls.work, "OneDrive - Jose Smith", "Documents", "maya")
        os.makedirs(cls.app)
        cls.modules = os.path.join(cls.app, "modules")
        cls.target = os.path.join(cls.modules, "i3dGltf")
        cls.gltf = scene_files()[0]

    def env(self, app=None):
        env = dict(os.environ)
        norm = os.path.normcase(os.path.normpath(REPO))
        for key in ("MAYA_MODULE_PATH", "PYTHONPATH", "MAYA_PLUG_IN_PATH", "MAYA_SCRIPT_PATH"):
            parts = [p for p in env.get(key, "").split(os.pathsep)
                     if p and not os.path.normcase(os.path.normpath(p)).startswith(norm)]
            if parts:
                env[key] = os.pathsep.join(parts)
            else:
                env.pop(key, None)
        env.update({"MAYA_APP_DIR": app or self.app, "MAYA_SKIP_USERSETUP_PY": "1", "PYTHONUTF8": "0"})
        return env

    def run_py(self, args, name, app=None, cwd=None, extra_env=None):
        """Run mayapy. mayapy 2024-2026 mangle non-ASCII command-line arguments (2027 does
        not), so a script in a non-ASCII folder is run from that folder by its bare name and
        paths go through the environment."""
        out = os.path.join(self.work, name + ".json")
        if os.path.exists(out):
            os.remove(out)
        env = self.env(app)
        env.update(extra_env or {})
        proc = subprocess.run([sys.executable] + args, env=env, cwd=cwd or self.work, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, timeout=600)
        log = proc.stdout.decode("utf-8", "replace")
        self.assertTrue(os.path.exists(out), log[-3000:])
        with open(out, encoding="utf-8") as f:
            return proc.returncode, json.load(f), log

    def run_installer(self, installer, name, *flags, **kw):
        out = os.path.join(self.work, name + ".json")
        return self.run_py([os.path.basename(installer), "--json", out] + list(flags), name,
                           cwd=os.path.dirname(installer), **kw)

    def child(self, source, name, *args, **kw):
        script = os.path.join(self.work, name + ".py")
        with open(script, "w", encoding="utf-8") as f:
            f.write(source)
        out = os.path.join(self.work, name + ".json")
        return self.run_py([script, out] + list(args), name, **kw)[1]

    def mod_line(self):
        with open(os.path.join(self.modules, "i3dGltf.mod"), encoding="utf-8") as f:
            return f.read()

    def test_0_non_ascii_user_folder_refused(self):
        # Maya cannot load Python plug-ins from a non-ASCII folder (2024 does not even read
        # the .mod there), so the installer refuses instead of leaving a broken install.
        app = os.path.join(self.work, "Jösé Müller", "maya")
        os.makedirs(app)
        code, res, log = self.run_installer(self.installer, "non_ascii", app=app)
        self.assertEqual(code, 2, log[-3000:])
        self.assertIn("non-ASCII", res["error"])
        self.assertFalse(os.path.exists(os.path.join(app, "modules", "i3dGltf.mod")))
        self.assertFalse(os.path.exists(os.path.join(app, "modules", "i3dGltf")))

    def test_1_fresh_install(self):
        code, res, log = self.run_installer(self.installer, "fresh")
        self.assertEqual(code, 0, log[-3000:])
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["action"], "install")
        self.assertEqual(res["loaded"], i3d_gltf.VERSION)
        self.assertEqual(os.path.normcase(res["appDir"]), os.path.normcase(os.path.normpath(self.app)))
        self.assertEqual(self.mod_line(), "+ i3dGltf %s ./i3dGltf\n" % i3d_gltf.VERSION)
        for rel in ("plug-ins/i3dGltf.py", "scripts/i3d_gltf/maya/options_ui.py", "install_manifest.json",
                    "uninstall_i3dGltf.py", "install_i3dGltf.py"):
            self.assertTrue(os.path.isfile(os.path.join(self.target, rel)), rel)
        self.assertEqual(sorted(os.listdir(self.modules)), ["i3dGltf", "i3dGltf.mod"])

    def test_2_new_process_finds_module_without_env(self):
        res = self.child(LOAD_CHILD, "load_by_name", self.gltf)
        self.assertNotIn("error", res, res.get("error"))
        self.assertNotIn("Maya_gLTF", res["moduleSearchPath"])
        self.assertTrue(res["moduleListed"])
        self.assertEqual(os.path.normcase(os.path.normpath(res["modulePath"])), os.path.normcase(self.target))
        self.assertEqual(res["version"], i3d_gltf.VERSION)
        self.assertTrue(os.path.normcase(os.path.normpath(res["pluginPath"])).startswith(os.path.normcase(self.target)))
        self.assertTrue(os.path.normcase(res["package"]).startswith(os.path.normcase(self.target)))
        self.assertEqual(res["optionsScript"], "i3dGltfImportOptions")
        self.assertIn("p:Root_Node_", res["transforms"])

    def test_3_reinstall_same_version(self):
        code, res, log = self.run_installer(self.installer, "reinstall")
        self.assertEqual(code, 0, log[-3000:])
        self.assertEqual(res["action"], "reinstall")

    def test_4_downgrade_refused(self):
        before = self.mod_line()
        code, res, log = self.run_installer(os.path.join(self.old_pkg, "install_i3dGltf.py"), "downgrade")
        self.assertEqual(code, 2, log[-3000:])
        self.assertTrue(res.get("refused"), res)
        self.assertIn("newer", res["error"])
        self.assertEqual(self.mod_line(), before)
        self.assertFalse(os.path.exists(os.path.join(self.target, "scripts", "i3d_gltf", "old_only.py")))

    def test_5_downgrade_forced(self):
        code, res, log = self.run_installer(os.path.join(self.old_pkg, "install_i3dGltf.py"), "downgrade_force",
                                            "--force")
        self.assertEqual(code, 0, log[-3000:])
        self.assertEqual((res["action"], res["version"], res["previous"]), ("downgrade", OLD, i3d_gltf.VERSION))
        self.assertEqual(self.mod_line(), "+ i3dGltf %s ./i3dGltf\n" % OLD)

    def test_6_upgrade_while_old_plugin_loaded(self):
        res = self.child(UPGRADE_CHILD, "upgrade_loaded", self.gltf, extra_env={"I3D_INSTALLER": self.installer})
        self.assertNotIn("error", res, res.get("error"))
        self.assertEqual(res["before"], [OLD, OLD])
        self.assertTrue(res["previouslyLoaded"])
        self.assertEqual(res["install"]["action"], "upgrade")
        self.assertIsNone(res["loadError"])
        self.assertEqual(res["after"], [i3d_gltf.VERSION, i3d_gltf.VERSION])
        self.assertIn("u:Root_Node_", res["transforms"])
        self.assertEqual(self.mod_line(), "+ i3dGltf %s ./i3dGltf\n" % i3d_gltf.VERSION)
        # a file only the old release had is gone
        self.assertFalse(os.path.exists(os.path.join(self.target, "scripts", "i3d_gltf", "old_only.py")))

    def test_7_uninstall_removes_exactly_what_was_installed(self):
        user_file = os.path.join(self.target, "my notes.txt")
        with open(user_file, "w") as f:
            f.write("not the installer's")
        other_mod = os.path.join(self.modules, "someoneElse.mod")
        with open(other_mod, "w") as f:
            f.write("+ someoneElse 1.0 ./x\n")
        code, res, log = self.run_installer(os.path.join(self.target, "uninstall_i3dGltf.py"), "uninstall")
        self.assertEqual(code, 0, log[-3000:])
        self.assertEqual(res["action"], "uninstall")
        self.assertFalse(os.path.exists(os.path.join(self.modules, "i3dGltf.mod")))
        self.assertTrue(os.path.exists(other_mod))
        # only the user's file is left
        left = []
        for root, _, files in os.walk(self.target):
            left += [os.path.join(root, f) for f in files]
        self.assertEqual(left, [user_file])
        os.remove(user_file)
        os.rmdir(self.target)
        code, res, log = self.run_installer(self.installer, "uninstall_again", "--uninstall")
        self.assertEqual(code, 0, log[-3000:])
        self.assertEqual(res["action"], "none")
        os.remove(other_mod)
        self.assertEqual(os.listdir(self.modules), [])


if __name__ == "__main__":
    unittest.main()
