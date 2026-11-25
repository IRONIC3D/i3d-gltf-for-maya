"""Build Maya anim curves from a core.animation.AnimationPlan.

Curves are created unconnected (MFnAnimCurve.create(type)), keyed in bulk with
``addKeys`` at the file's times in seconds (so keys do not depend on the scene frame
rate), then handed to a *target*. ``ConnectTarget`` connects each curve to its plug;
a later milestone can add a target that puts the same curves into a Time Editor clip
instead, without changing how curves are built.

Rotation groups that the plan marks for slerp are switched to Maya's Quaternion Slerp
rotation interpolation with the ``rotationInterpolation`` command (the curve attribute
is read-only); that evaluates exactly like glTF's LINEAR quaternion slerp (verified to
~1e-6 degrees on Maya 2024-2027).
"""

import maya.api.OpenMaya as om
import maya.api.OpenMayaAnim as oma
import maya.cmds as cmds

from ..core.animation import CUBICSPLINE, LINEAR, STEP

_CURVE_TYPES = {
    "TL": oma.MFnAnimCurve.kAnimCurveTL,
    "TA": oma.MFnAnimCurve.kAnimCurveTA,
    "TU": oma.MFnAnimCurve.kAnimCurveTU,
}
_TANGENTS = {
    STEP: (oma.MFnAnimCurve.kTangentStep, oma.MFnAnimCurve.kTangentStep),
    LINEAR: (oma.MFnAnimCurve.kTangentLinear, oma.MFnAnimCurve.kTangentLinear),
    CUBICSPLINE: (oma.MFnAnimCurve.kTangentFixed, oma.MFnAnimCurve.kTangentFixed),
}


class ConnectTarget(object):
    """Connect every curve's output to its destination plug (one MDGModifier)."""

    def __init__(self):
        self._mod = om.MDGModifier()

    def add(self, curve_obj, plug):
        self._mod.connect(om.MFnDependencyNode(curve_obj).findPlug("output", False), plug)

    def finish(self):
        self._mod.doIt()


def create_curve(curve):
    """An unconnected anim curve holding ``curve`` (core.animation.CurvePlan)'s keys."""
    fn = oma.MFnAnimCurve()
    obj = fn.create(_CURVE_TYPES[curve.kind])
    # Curves created while Maya reads a file (cmds.file import/reference) default to
    # weighted tangents, which turns the Hermite segments into different Beziers.
    fn.setIsWeighted(False)
    times = om.MTimeArray([om.MTime(t, om.MTime.kSeconds) for t in curve.times])
    tin, tout = _TANGENTS[curve.interpolation]
    fn.addKeys(times, om.MDoubleArray(curve.values), tin, tout)
    if curve.interpolation == CUBICSPLINE:
        # slope per second; x = 1 second with convertUnits=False (verified exact)
        for k in range(len(curve.times)):
            fn.setTangentsLocked(k, False)
            fn.setTangent(k, 1.0, curve.in_slopes[k], True, convertUnits=False)
            fn.setTangent(k, 1.0, curve.out_slopes[k], False, convertUnits=False)
    return obj


class AnimationBuilder(object):
    """``resolve_plug(curve_plan)`` returns the destination MPlug or None (skipped)."""

    def __init__(self, plan, resolve_plug, registry, reporter):
        self.plan = plan
        self.resolve_plug = resolve_plug
        self.registry = registry
        self.reporter = reporter
        self.curves = {}  # curve plan index -> MObjectHandle

    def build(self, target):
        for i, c in enumerate(self.plan.curves):
            plug = self.resolve_plug(c)
            if plug is None:
                continue
            obj = create_curve(c)
            self.curves[i] = om.MObjectHandle(obj)
            om.MFnDependencyNode(obj).setName(self.registry.claim(c.name_base))
            target.add(obj, plug)
        target.finish()
        slerp = []
        for group in self.plan.slerp_groups:
            if all(i in self.curves for i in group):
                slerp.extend(om.MFnDependencyNode(self.curves[i].object()).name() for i in group)
        if slerp:
            cmds.rotationInterpolation(slerp, convert="quaternionSlerp")
        return self.curves
