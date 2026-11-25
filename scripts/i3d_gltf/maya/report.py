"""Forward Reporter messages to Maya's script editor / command line."""

import maya.api.OpenMaya as om

from ..core.diagnostics import ERROR, INFO, WARNING


def maya_sink(level, text):
    if level == ERROR:
        om.MGlobal.displayError(text)
    elif level == WARNING:
        om.MGlobal.displayWarning(text)
    elif level == INFO:
        om.MGlobal.displayInfo(text)
