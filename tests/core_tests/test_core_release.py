"""Release package (tools/build_release.py). No Maya."""

import importlib.util
import os
import shutil
import tempfile
import unittest
import zipfile

import i3d_gltf

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def load_builder():
    spec = importlib.util.spec_from_file_location("build_release", os.path.join(REPO, "tools", "build_release.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestBuildRelease(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.builder = load_builder()
        cls.tmp = tempfile.mkdtemp(prefix="release_", dir=os.environ.get("I3DGLTF_TEST_TMP"))
        cls.a = cls.builder.build(os.path.join(cls.tmp, "a"))
        cls.b = cls.builder.build(os.path.join(cls.tmp, "b"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_name_follows_version(self):
        self.assertEqual(os.path.basename(self.a), "i3dGltf-%s.zip" % i3d_gltf.VERSION)

    def test_deterministic(self):
        with open(self.a, "rb") as fa, open(self.b, "rb") as fb:
            self.assertEqual(fa.read(), fb.read())

    def test_contents(self):
        with zipfile.ZipFile(self.a) as zf:
            names = zf.namelist()
            self.assertEqual(names, sorted(names))
            for required in ("install_i3dGltf.py", "uninstall_i3dGltf.py", "README.txt", "LICENSE", "i3dGltf.mod",
                             "i3dGltf/plug-ins/i3dGltf.py", "i3dGltf/scripts/i3d_gltf/__init__.py",
                             "i3dGltf/scripts/i3d_gltf/maya/options_ui.py", "i3dGltf/install_i3dGltf.py",
                             "i3dGltf/uninstall_i3dGltf.py"):
                self.assertIn(required, names)
            for n in names:
                self.assertFalse(n.startswith(("tests/", "_samples/", "tools/", "dist/", ".git")), n)
                self.assertNotIn("__pycache__", n)
                self.assertFalse(n.endswith((".pyc", ".log")), n)
                top = n.split("/")[0]
                self.assertIn(top, ("install_i3dGltf.py", "uninstall_i3dGltf.py", "README.txt", "LICENSE",
                                    "i3dGltf.mod", "i3dGltf"), n)
            self.assertEqual(zf.read("i3dGltf.mod").decode("ascii"), "+ i3dGltf %s ./i3dGltf\n" % i3d_gltf.VERSION)
            self.assertIn(i3d_gltf.VERSION, zf.read("README.txt").decode("utf-8").splitlines()[0])
            self.assertIn('VERSION = "%s"' % i3d_gltf.VERSION,
                          zf.read("i3dGltf/scripts/i3d_gltf/__init__.py").decode("utf-8"))
            self.assertNotIn(b"\r\n", zf.read("install_i3dGltf.py"))
            for i in zf.infolist():
                self.assertEqual(i.date_time, self.builder.FIXED_TIME)

    def test_every_package_file_is_shipped(self):
        with zipfile.ZipFile(self.a) as zf:
            names = set(zf.namelist())
        for root, dirs, files in os.walk(os.path.join(REPO, "scripts")):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for f in files:
                if f.endswith(".py"):
                    rel = os.path.relpath(os.path.join(root, f), REPO).replace("\\", "/")
                    self.assertIn("i3dGltf/" + rel, names)


if __name__ == "__main__":
    unittest.main()
