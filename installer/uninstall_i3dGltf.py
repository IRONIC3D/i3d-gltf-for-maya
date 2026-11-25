"""IRONIC3D glTF for Maya - uninstaller.

Drag and drop this file onto a Maya viewport to remove what install_i3dGltf.py installed
(``<Maya user app dir>/modules/i3dGltf.mod`` and the files it copied into
``<Maya user app dir>/modules/i3dGltf/``). From a command line:
``mayapy install_i3dGltf.py --uninstall``.

It needs install_i3dGltf.py next to it (both are in the release zip and in the installed
module folder).
"""

import importlib.util
import os
import sys


def _installer():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "install_i3dGltf.py")
    spec = importlib.util.spec_from_file_location("_i3dgltf_installer_for_uninstall", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def onMayaDroppedPythonFile(*_args):
    try:
        _installer().run_uninstall_interactive()
    finally:
        sys.modules.pop(__name__, None)


if __name__ == "__main__":
    inst = _installer()
    inst._hard_exit(inst.main(["--uninstall"] + sys.argv[1:]))
