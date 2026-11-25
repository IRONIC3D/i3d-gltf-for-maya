"""glTF animation -> per-attribute key lists ready for Maya anim curves. No Maya imports.

Mapping (``animSampling=keys``, the default; keys exactly at the file's times, in seconds):
  * translation / scale / weights: one curve per component. STEP -> stepped keys,
    LINEAR -> linear tangents, CUBICSPLINE -> the file's in/out tangents as slopes per
    second (non-weighted Maya tangents evaluate the same cubic Hermite, so this is exact).
    Translations (values and tangents) are multiplied by the unit scale.
  * rotation: quaternions become rotateX/Y/Z in Maya's default xyz order, continuity
    filtered (each key takes the Euler solution closest to the previous key).
    LINEAR -> the three curves are marked for Maya's quaternion slerp interpolation
    (``rotationInterpolation`` = Quaternion Slerp), which reproduces glTF's slerp
    between keys. STEP -> stepped Euler keys (exact). CUBICSPLINE has no exact Maya
    equivalent (glTF interpolates quaternion components and normalises): the Euler
    tangents are the derivative of that curve at each key, which is exact at the keys
    and approximate between them; use ``animSampling=bake`` for frame-exact values.
``animSampling=bake`` evaluates every channel with the glTF rules at every frame of the
scene frame rate (covering the channel's time range) and keys those values: stepped
for STEP samplers, otherwise linear (slerp for rotations).
"""

import math
from array import array
from bisect import bisect_right
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .diagnostics import Reporter

STEP = "STEP"
LINEAR = "LINEAR"
CUBICSPLINE = "CUBICSPLINE"
INTERPOLATIONS = (STEP, LINEAR, CUBICSPLINE)

TRS_ATTRS = {
    "translation": ("translateX", "translateY", "translateZ"),
    "rotation": ("rotateX", "rotateY", "rotateZ"),
    "scale": ("scaleX", "scaleY", "scaleZ"),
}
CURVE_KIND = {"translation": "TL", "rotation": "TA", "scale": "TU", "weights": "TU"}
_COMPONENTS = {"translation": 3, "rotation": 4, "scale": 3}
TWO_PI = 2.0 * math.pi


@dataclass
class CurvePlan:
    """One Maya anim curve. ``attr`` is a transform attribute name, or ``None`` for a
    morph weight (then ``target`` is the morph target index on the node's blendShape)."""
    node: int
    attr: Optional[str]
    target: Optional[int]
    kind: str                     # "TL" | "TA" | "TU"
    times: array                  # seconds
    values: array                 # Maya internal units: cm (unit-scaled), radians, plain numbers
    interpolation: str            # STEP | LINEAR | CUBICSPLINE (how the Maya keys are set up)
    in_slopes: Optional[array] = None   # CUBICSPLINE: units per second
    out_slopes: Optional[array] = None
    name_base: str = ""
    channel: int = -1             # glTF channel index


@dataclass
class AnimationPlan:
    index: int
    name: str
    curves: List[CurvePlan] = field(default_factory=list)
    slerp_groups: List[Tuple[int, int, int]] = field(default_factory=list)  # curve indices x, y, z
    start: float = 0.0            # seconds, over every imported channel
    end: float = 0.0
    baked: bool = False
    approximated: List[int] = field(default_factory=list)  # channels Maya cannot represent exactly


# selection ------------------------------------------------------------------------------

def select_animation(doc, value, reporter=None):
    """The animation index the ``animation`` option selects, or None."""
    reporter = reporter or Reporter()
    value = (value or "").strip()
    if not doc.animations or value.lower() == "none":
        return None
    if value.lower() in ("first", ""):
        return 0
    if value.isdigit():
        i = int(value)
        if i < len(doc.animations):
            return i
        reporter.warning("Option 'animation=%s': the file has %d animation(s); importing the first"
                         % (value, len(doc.animations)))
        return 0
    for i, a in enumerate(doc.animations):
        if a.name == value:
            return i
    reporter.warning("Option 'animation=%s': no animation has that name (%s); importing the first"
                     % (value, ", ".join(repr(a.name) for a in doc.animations)))
    return 0


# quaternion / Euler helpers ---------------------------------------------------------------

def quat_normalize(q):
    n = math.sqrt(q[0] * q[0] + q[1] * q[1] + q[2] * q[2] + q[3] * q[3])
    if n == 0.0:
        return (0.0, 0.0, 0.0, 1.0)
    return (q[0] / n, q[1] / n, q[2] / n, q[3] / n)


def quat_to_euler_xyz(q):
    """Euler angles (radians) for Maya rotate order xyz (X applied first) from a glTF
    quaternion (x, y, z, w)."""
    x, y, z, w = quat_normalize(q)
    m00 = 1.0 - 2.0 * (y * y + z * z)
    m01 = 2.0 * (x * y - z * w)
    m10 = 2.0 * (x * y + z * w)
    m11 = 1.0 - 2.0 * (x * x + z * z)
    m20 = 2.0 * (x * z - y * w)
    m21 = 2.0 * (y * z + x * w)
    m22 = 1.0 - 2.0 * (x * x + y * y)
    sy = max(-1.0, min(1.0, -m20))
    if abs(sy) < 0.99999999:
        return (math.atan2(m21, m22), math.asin(sy), math.atan2(m10, m00))
    return (math.atan2(sy * m01, m11), math.copysign(math.pi / 2.0, sy), 0.0)


def closest_euler(e, prev):
    """The equivalent xyz Euler rotation closest to ``prev`` (continuity filter): the
    better of (x, y, z) and (x + pi, pi - y, z + pi), each angle moved by whole turns."""
    px, py, pz = prev
    best = None
    best_d = 0.0
    for cx, cy, cz in ((e[0], e[1], e[2]), (e[0] + math.pi, math.pi - e[1], e[2] + math.pi)):
        cx += TWO_PI * round((px - cx) / TWO_PI)
        cy += TWO_PI * round((py - cy) / TWO_PI)
        cz += TWO_PI * round((pz - cz) / TWO_PI)
        d = (cx - px) * (cx - px) + (cy - py) * (cy - py) + (cz - pz) * (cz - pz)
        if best is None or d < best_d - 1e-12:
            best, best_d = (cx, cy, cz), d
    return best


def eulers_from_quats(quats):
    out = []
    prev = None
    for q in quats:
        e = quat_to_euler_xyz(q)
        if prev is not None:
            e = closest_euler(e, prev)
        out.append(e)
        prev = e
    return out


def slerp(a, b, s):
    """glTF spherical linear interpolation (shortest path)."""
    d = a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3]
    if d < 0.0:
        b = (-b[0], -b[1], -b[2], -b[3])
        d = -d
    if d > 0.9999999:
        return quat_normalize(tuple(a[k] + s * (b[k] - a[k]) for k in range(4)))
    ang = math.acos(d)
    sa = math.sin(ang)
    w0 = math.sin((1.0 - s) * ang) / sa
    w1 = math.sin(s * ang) / sa
    return tuple(w0 * a[k] + w1 * b[k] for k in range(4))


# sampler evaluation (glTF rules) ------------------------------------------------------------

class Sampler(object):
    """Decoded sampler data: ``values[k]`` / ``in_tan[k]`` / ``out_tan[k]`` are tuples."""

    def __init__(self, times, values, interpolation, in_tan=None, out_tan=None, rotation=False):
        self.times = times
        self.values = values
        self.interpolation = interpolation
        self.in_tan = in_tan
        self.out_tan = out_tan
        self.rotation = rotation

    def evaluate(self, t):
        times = self.times
        n = len(times)
        if n == 1 or t <= times[0]:
            v = self.values[0]
            return quat_normalize(v) if self.rotation else v
        if t >= times[-1]:
            v = self.values[-1]
            return quat_normalize(v) if self.rotation else v
        k = bisect_right(times, t) - 1
        t0 = times[k]
        dt = times[k + 1] - t0
        s = (t - t0) / dt
        v0 = self.values[k]
        v1 = self.values[k + 1]
        if self.interpolation == STEP:
            return quat_normalize(v0) if self.rotation else v0
        if self.interpolation == LINEAR:
            if self.rotation:
                return slerp(quat_normalize(v0), quat_normalize(v1), s)
            return tuple(a + s * (b - a) for a, b in zip(v0, v1))
        s2 = s * s
        s3 = s2 * s
        h00 = 2 * s3 - 3 * s2 + 1
        h10 = s3 - 2 * s2 + s
        h01 = -2 * s3 + 3 * s2
        h11 = s3 - s2
        b0 = self.out_tan[k]
        a1 = self.in_tan[k + 1]
        v = tuple(h00 * v0[c] + h10 * dt * b0[c] + h01 * v1[c] + h11 * dt * a1[c] for c in range(len(v0)))
        return quat_normalize(v) if self.rotation else v


def _split(flat, n_keys, ncomp, cubic):
    """Output accessor values -> (values, in_tangents, out_tangents) as lists of tuples."""
    def elem(i):
        return tuple(flat[i * ncomp:(i + 1) * ncomp])
    if not cubic:
        return [elem(k) for k in range(n_keys)], None, None
    return ([elem(3 * k + 1) for k in range(n_keys)], [elem(3 * k) for k in range(n_keys)],
            [elem(3 * k + 2) for k in range(n_keys)])


def _dedupe_times(times):
    """Indices to keep so times are strictly increasing (of equal times the last wins).
    None if the times decrease somewhere."""
    keep = []
    for i in range(len(times)):
        if i + 1 < len(times):
            if times[i + 1] < times[i]:
                return None
            if times[i + 1] == times[i]:
                continue
        keep.append(i)
    return keep


# planning ---------------------------------------------------------------------------------

def prepare_animation(doc, reader, plan, options, morph_names, fps=24.0, reporter=None):
    """AnimationPlan for the animation the options select, or None.

    ``morph_names``: {glTF node index: [target names]} for nodes whose shape has a
    blendShape (weights channels on other nodes are skipped with a warning).
    Bad channels are skipped with a warning; this never raises for bad animation data
    except for unreadable accessors.
    """
    reporter = reporter or Reporter()
    index = select_animation(doc, options.animation, reporter)
    if index is None:
        return None
    anim = doc.animations[index]
    bake = options.animSampling == "bake"
    factor = plan.unit_scale
    out = AnimationPlan(index=index, name=anim.name or "animation_%d" % index, baked=bake)
    apath = "animations[%d]" % index
    seen = {}
    skipped_outside = 0
    starts = []
    ends = []
    decoded = {}
    for ci, ch in enumerate(anim.channels):
        cpath = "%s.channels[%d]" % (apath, ci)
        node = ch.target_node
        path = ch.target_path
        if node is None:
            continue  # e.g. KHR_animation_pointer targets; nothing to animate on a node
        if path not in TRS_ATTRS and path != "weights":
            reporter.warning("%s: target path '%s' is not supported; channel skipped" % (cpath, path))
            continue
        if node not in plan.nodes:
            skipped_outside += 1
            continue
        if (node, path) in seen:
            reporter.warning("%s animates nodes[%d].%s again (also channel %d); the later channel is used"
                             % (cpath, node, path, seen[(node, path)]))
        seen[(node, path)] = ci
        sampler = anim.samplers[ch.sampler]
        interp = sampler.interpolation
        if interp not in INTERPOLATIONS:
            reporter.warning("%s: unknown interpolation '%s'; channel skipped" % (cpath, interp))
            continue
        if path == "weights":
            names = morph_names.get(node)
            if names is None:
                reporter.warning("%s animates morph weights of nodes[%d], which has no morph targets; "
                                 "channel skipped" % (cpath, node))
                continue
            ncomp = len(names)
        else:
            ncomp = _COMPONENTS[path]
        key = (ch.sampler, ncomp)
        if key not in decoded:
            decoded[key] = _decode_sampler(reader, sampler, ncomp, path == "rotation", "%s.samplers[%d]"
                                           % (apath, ch.sampler), reporter)
        smp = decoded[key]
        if smp is None:
            continue
        starts.append(smp.times[0])
        ends.append(smp.times[-1])
        _plan_channel(out, ci, node, path, smp, plan, factor, bake, fps, morph_names)
    if skipped_outside:
        reporter.info("%s: %d channel(s) target nodes outside the imported scene" % (apath, skipped_outside))
    # a channel animated twice: keep only the last one
    last = dict(((c.node, c.attr, c.target), c.channel) for c in out.curves)
    if len(last) != len(out.curves):
        keep = [i for i, c in enumerate(out.curves) if last[(c.node, c.attr, c.target)] == c.channel]
        remap = dict((old, new) for new, old in enumerate(keep))
        out.curves = [out.curves[i] for i in keep]
        out.slerp_groups = [tuple(remap[i] for i in g) for g in out.slerp_groups if all(i in remap for i in g)]
    if not out.curves:
        return out
    out.start = min(starts)
    out.end = max(ends)
    for c in out.curves:
        c.name_base = "%s_%s" % (plan.nodes[c.node].name,
                                 c.attr if c.attr is not None else morph_names[c.node][c.target])
    if out.approximated:
        reporter.warning("%s: %d CUBICSPLINE rotation channel(s) cannot be represented exactly by Maya curves; "
                         "keys are exact, values between keys are approximate. Import with "
                         "animSampling=bake for frame-exact values." % (apath, len(out.approximated)))
    return out


def _decode_sampler(reader, sampler, ncomp, rotation, spath, reporter):
    times_data = reader.read_floats(sampler.input, ("SCALAR",), spath + ".input")
    times = list(times_data.values)
    n = len(times)
    cubic = sampler.interpolation == CUBICSPLINE
    out = reader.read_floats(sampler.output, None, spath + ".output")
    flat = out.values
    expected = n * ncomp * (3 if cubic else 1)
    if len(flat) != expected:
        reporter.warning("%s: output has %d values, expected %d (%d keys x %d components%s); channel skipped"
                         % (spath, len(flat), expected, n, ncomp, " x 3" if cubic else ""))
        return None
    if any(t != t for t in times):
        reporter.warning("%s: input has NaN times; channel skipped" % spath)
        return None
    values, in_tan, out_tan = _split(flat, n, ncomp, cubic)
    keep = _dedupe_times(times)
    if keep is None:
        reporter.warning("%s: input times are not increasing; channel skipped" % spath)
        return None
    if len(keep) != n:
        reporter.info("%s: %d key(s) at duplicate times dropped" % (spath, n - len(keep)))
        times = [times[i] for i in keep]
        values = [values[i] for i in keep]
        if cubic:
            in_tan = [in_tan[i] for i in keep]
            out_tan = [out_tan[i] for i in keep]
    return Sampler(times, values, sampler.interpolation, in_tan, out_tan, rotation)


def _bake_times(t0, t1, fps):
    f0 = int(math.floor(t0 * fps + 1e-6))
    f1 = int(math.ceil(t1 * fps - 1e-6))
    return [f / fps for f in range(f0, max(f0, f1) + 1)]


def _plan_channel(out, ci, node, path, smp, plan, factor, bake, fps, morph_names):
    kind = CURVE_KIND[path]
    if bake:
        times = _bake_times(smp.times[0], smp.times[-1], fps)
        values = [smp.evaluate(t) for t in times]
        interp = STEP if smp.interpolation == STEP else LINEAR
        in_tan = out_tan = None
    else:
        times = smp.times
        values = smp.values
        interp = smp.interpolation
        in_tan, out_tan = smp.in_tan, smp.out_tan
    t_arr = array("d", times)

    def add(attr, target, vals, ins=None, outs=None, interpolation=interp):
        out.curves.append(CurvePlan(node=node, attr=attr, target=target, kind=kind, times=t_arr,
                                    values=array("d", vals), interpolation=interpolation,
                                    in_slopes=array("d", ins) if ins is not None else None,
                                    out_slopes=array("d", outs) if outs is not None else None, channel=ci))
        return len(out.curves) - 1

    if path == "rotation":
        quats = [quat_normalize(v) for v in values]
        eulers = eulers_from_quats(quats)
        if interp == CUBICSPLINE:
            ins, outs = _euler_slopes(smp, eulers)
            for c, attr in enumerate(TRS_ATTRS[path]):
                add(attr, None, [e[c] for e in eulers], [s[c] for s in ins], [s[c] for s in outs])
            out.approximated.append(ci)
        else:
            idx = [add(attr, None, [e[c] for e in eulers]) for c, attr in enumerate(TRS_ATTRS[path])]
            if interp == LINEAR and len(times) > 1:
                out.slerp_groups.append(tuple(idx))
        return
    scale = factor if path == "translation" else 1.0
    attrs = TRS_ATTRS.get(path) or [None] * len(values[0])
    for c, attr in enumerate(attrs):
        vals = [v[c] * scale for v in values]
        ins = outs = None
        if interp == CUBICSPLINE:
            ins = [v[c] * scale for v in in_tan]
            outs = [v[c] * scale for v in out_tan]
        add(attr, None if attr is not None else c, vals, ins, outs)


def _euler_slopes(smp, eulers):
    """Euler-angle slopes (radians per second) of glTF's CUBICSPLINE quaternion curve at
    each key: one-sided numeric derivatives of the normalised Hermite curve."""
    times = smp.times
    n = len(times)
    ins = []
    outs = []
    for k in range(n):
        e = eulers[k]
        slopes = []
        for side in (-1, 1):
            j = k + side
            if j < 0 or j >= n:
                slopes.append(None)
                continue
            h = abs(times[j] - times[k]) * 1e-5
            q = smp.evaluate(times[k] + side * h)
            e2 = closest_euler(quat_to_euler_xyz(q), e)
            slopes.append(tuple((e2[c] - e[c]) / (side * h) for c in range(3)))
        a, b = slopes
        ins.append(a if a is not None else (b if b is not None else (0.0, 0.0, 0.0)))
        outs.append(b if b is not None else ins[-1])
    return ins, outs


def target_names_for_nodes(plan, morphs):
    """{node index: [target names]} for nodes whose shape has a MorphPlan."""
    out = {}
    for shape_index, mp in morphs.items():
        for node in plan.shapes[shape_index].nodes:
            out[node] = [t.name for t in mp.targets]
    return out

