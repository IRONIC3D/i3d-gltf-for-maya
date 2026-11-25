"""Maya side of IRONIC3D glTF: scene builders and the file translator.

Scene building uses Python API 2.0 (maya.api.OpenMaya) and maya.cmds. Only the
translator class itself uses API 1.0 (MPxFileTranslator has no API 2.0 equivalent).
"""
