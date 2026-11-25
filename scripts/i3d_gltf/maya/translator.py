"""The MPxFileTranslator (Python API 1.0 -- API 2.0 has no file translator class).

Verified behaviour (Maya 2024-2027):
  * ``filter()`` returns ``"*.gltf;*.glb"``: Maya's file dialogs split translator filters
    on ';' (fileDialogFilterTypes.mel), giving "IRONIC3D glTF ( *.gltf *.glb )".
  * ``identifyFile`` claims both extensions so ``cmds.file(path, i=True)`` auto-detects.
  * ``haveNamespaceSupport()`` must return True. Otherwise Maya ignores the ``namespace``
    flag for import/reference; with it, Maya makes a temporary namespace current while
    reader() runs and renames it to the requested one afterwards.
  * Nodes created with API 2.0 inside reader() during ``reference=True`` are owned by the
    reference node.
"""

import os
import traceback

import maya.OpenMaya as om1
import maya.OpenMayaMPx as ompx

from .. import TRANSLATOR_NAME
from ..core import GltfError
from ..core.options import default_options_string
from . import importer, options_ui

EXTENSIONS = (".gltf", ".glb")
FILTER = "*.gltf;*.glb"
#: MEL procedure Maya's file dialogs call for the import options (see options_ui).
OPTIONS_SCRIPT = options_ui.MEL_PROC


class GltfTranslator(ompx.MPxFileTranslator):
    def __init__(self):
        ompx.MPxFileTranslator.__init__(self)

    def haveReadMethod(self):
        return True

    def haveWriteMethod(self):
        return False

    def haveReferenceMethod(self):
        return False

    def haveNamespaceSupport(self):
        return True

    def canBeOpened(self):
        # File > Open of a .gltf would discard the current scene; allow it like OBJ/FBX do.
        return True

    def defaultExtension(self):
        return "gltf"

    def filter(self):
        return FILTER

    def identifyFile(self, file_object, buffer, size):
        name = file_object.resolvedFullName() or file_object.rawFullName()
        if os.path.splitext(name)[1].lower() in EXTENSIONS:
            return ompx.MPxFileTranslator.kIsMyFileType
        return ompx.MPxFileTranslator.kNotMyFileType

    def reader(self, file_object, options_string, mode):
        path = file_object.expandedFullName() or file_object.resolvedFullName()
        if mode == ompx.MPxFileTranslator.kReferenceAccessMode:
            access = "reference"
        elif mode == ompx.MPxFileTranslator.kOpenAccessMode:
            access = "open"
        else:
            access = "import"
        try:
            importer.import_file(path, options_string or "", access=access)
        except GltfError as exc:
            message = "%s import failed for '%s': %s" % (TRANSLATOR_NAME, path, exc)
            om1.MGlobal.displayError(message)
            raise RuntimeError(message)
        except Exception as exc:
            om1.MGlobal.displayError("%s import failed for '%s' (internal error): %s\n%s"
                                     % (TRANSLATOR_NAME, path, exc, traceback.format_exc()))
            raise
        return True


def creator():
    return ompx.asMPxPtr(GltfTranslator())


def register(plugin_fn):
    options_ui.define_mel_procedure()
    plugin_fn.registerFileTranslator(TRANSLATOR_NAME, None, creator, OPTIONS_SCRIPT,
                                     default_options_string(), False)


def deregister(plugin_fn):
    plugin_fn.deregisterFileTranslator(TRANSLATOR_NAME)
