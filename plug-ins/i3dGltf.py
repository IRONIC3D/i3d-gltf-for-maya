"""IRONIC3D glTF for Maya - plug-in entry point (kept thin).

Registers the "IRONIC3D glTF" file translator (.gltf / .glb import).
The implementation lives in the ``i3d_gltf`` package under ../scripts.
"""

import os
import sys

import maya.OpenMayaMPx as ompx

# No maya_useNewAPI here on purpose: MPxFileTranslator only exists in Python API 1.0.
# Scene building inside the package uses maya.api.OpenMaya (API 2.0).


def _ensure_package_on_path(load_path):
    """Make ``i3d_gltf`` importable when the plug-in is loaded by path, without the module.

    Maya does not define ``__file__`` when it runs a Python plug-in, so the folder comes
    from MFnPlugin.loadPath().
    """
    try:
        import i3d_gltf  # noqa: F401  (normal case: scripts/ is on PYTHONPATH via the module)
    except ImportError:
        scripts = os.path.normpath(os.path.join(load_path, os.pardir, "scripts"))
        if scripts not in sys.path:
            sys.path.insert(0, scripts)


def initializePlugin(mobject):
    # Vendor is only settable by the first MFnPlugin built on this object, and the load
    # path is needed before the package (which holds the version) can be imported.
    plugin = ompx.MFnPlugin(mobject, "IRONIC3D", "0", "Any")
    _ensure_package_on_path(plugin.loadPath())
    import i3d_gltf
    from i3d_gltf.maya import translator

    plugin.setVersion(i3d_gltf.VERSION)
    translator.register(plugin)


def uninitializePlugin(mobject):
    from i3d_gltf.maya import translator

    plugin = ompx.MFnPlugin(mobject)
    translator.deregister(plugin)
