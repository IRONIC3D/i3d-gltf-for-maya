"""Shared helpers for the Maya tests (run under mayapy with maya.standalone initialised)."""

import os
import tempfile
import unittest

import maya.cmds as cmds

import fixtures

T = "IRONIC3D glTF"
PLUGIN = "i3dGltf"

_fixture_dir = None


def fixture_dir():
    global _fixture_dir
    if _fixture_dir is None:
        # run_suite.py provides (and removes) a temp root
        _fixture_dir = tempfile.mkdtemp(prefix="maya_", dir=os.environ.get("I3DGLTF_TEST_TMP"))
    return _fixture_dir


def ensure_plugin():
    if not cmds.pluginInfo(PLUGIN, q=True, loaded=True):
        cmds.loadPlugin(PLUGIN)


def scene_files():
    d = fixture_dir()
    g = os.path.join(d, "scene.gltf")
    if not os.path.exists(g):
        fixtures.write_scene(d)
    return g, os.path.join(d, "scene.glb")


def import_file(path, namespace, options=None):
    """Exactly the host application's import call (plus optional options)."""
    kw = dict(i=True, type=T, namespace=namespace, ignoreVersion=True, mergeNamespacesOnClash=False,
              preserveReferences=True)
    if options is not None:
        kw["options"] = options
    return cmds.file(path, **kw)


def reference_file(path, namespace):
    """Exactly the host application's reference call."""
    return cmds.file(path, reference=True, type=T, namespace=namespace, ignoreVersion=True,
                     mergeNamespacesOnClash=False)


def new_assemblies(fn, *args, **kwargs):
    before = set(cmds.ls(assemblies=True, long=True))
    fn(*args, **kwargs)
    after = cmds.ls(assemblies=True, long=True)
    return [a for a in after if a not in before]


class MayaTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ensure_plugin()

    def setUp(self):
        cmds.file(new=True, force=True)
        if cmds.upAxis(q=True, axis=True) != "y":
            cmds.upAxis(axis="y", rotateView=False)

    def assertVecAlmost(self, a, b, places=4, msg=None):
        self.assertEqual(len(a), len(b), msg)
        for x, y in zip(a, b):
            self.assertAlmostEqual(x, y, places=places, msg="%s != %s %s" % (list(a), list(b), msg or ""))
