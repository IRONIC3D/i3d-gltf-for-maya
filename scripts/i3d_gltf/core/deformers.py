"""Skin weights and morph target deltas per Maya shape, ready for bulk deformer setup.
No Maya imports.

Both work on the Maya vertex order of core.meshprep.MeshData: Maya vertex
``src.vertex_offset + k`` is glTF accessor element ``src.original_indices[k]`` (or ``k``)
of vertex source ``src``. Everything that can fail on bad data runs here, before any
Maya node exists.
"""

from array import array
from dataclasses import dataclass, field
from typing import List

from .diagnostics import GltfFormatError, Reporter
from .meshprep import _gather
from .names import NameRegistry, sanitize


@dataclass
class SkinPlan:
    """One skinCluster. Influence ``k`` is glTF node ``joints[k]``."""
    shape: int                       # index into ScenePlan.shapes
    skin: int                        # glTF skin index
    joints: List[int]                # glTF node indices, skin order
    bind_pre_matrices: List[List[float]]  # one 16-float list per joint (glTF column-major ==
    #                                        Maya MMatrix row-major), translation unit-scaled
    offsets: array = field(default_factory=lambda: array("i", [0]))  # vertex v: [offsets[v], offsets[v+1])
    influences: array = field(default_factory=lambda: array("i"))    # influence index per entry
    weights: array = field(default_factory=lambda: array("d"))       # normalised weight per entry
    max_influences: int = 0
    name: str = ""

    @property
    def num_vertices(self):
        return len(self.offsets) - 1


@dataclass
class MorphTarget:
    name: str              # sanitised, unique within the blendShape (Maya may still adjust it)
    vertices: array        # Maya vertex ids with a non-zero delta ('i')
    deltas: array          # 3 floats per entry, unit-scaled ('d')


@dataclass
class MorphPlan:
    """One blendShape; target ``t`` is glTF morph target ``t`` of the mesh."""
    shape: int
    targets: List[MorphTarget]
    weights: List[float]   # initial weights: node weights, else mesh weights, else 0
    name: str = ""


IDENTITY = [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]


def _skin_sets(attrs):
    return sorted(int(k[7:]) for k in attrs if k.startswith("JOINTS_") and k[7:].isdigit()
                  and ("WEIGHTS_%s" % k[7:]) in attrs)


def prepare_skin(doc, reader, shape_plan, md, unit_scale, reporter=None):
    """SkinPlan for one skinned shape. Raises GltfFormatError on invalid skin data."""
    reporter = reporter or Reporter()
    skin_index = shape_plan.skin
    skin = doc.skins[skin_index]
    spath = "skins[%d]" % skin_index
    njoints = len(skin.joints)
    if len(set(skin.joints)) != njoints:
        raise GltfFormatError("%s lists the same joint more than once" % spath)

    # bind pre-matrices
    if skin.inverse_bind_matrices is not None:
        ibm = reader.read_floats(skin.inverse_bind_matrices, ("MAT4",), spath + ".inverseBindMatrices")
        if ibm.count < njoints:
            raise GltfFormatError("%s.inverseBindMatrices has %d matrices for %d joints" % (spath, ibm.count, njoints))
        vals = ibm.values
        mats = []
        for k in range(njoints):
            m = [float(x) for x in vals[16 * k:16 * k + 16]]
            m[12] *= unit_scale
            m[13] *= unit_scale
            m[14] *= unit_scale
            mats.append(m)
    else:
        mats = [list(IDENTITY) for _ in range(njoints)]

    # weights, per Maya vertex
    offsets = array("i", [0])
    infl = array("i")
    wts = array("d")
    unweighted = 0
    max_inf = 0
    mpath = "meshes[%d]" % md.mesh_index
    for src in md.sources:
        sets = _skin_sets(src.attributes)
        n = src.vertex_count
        if not sets:
            # a primitive without skin attributes in a skinned mesh: rigid on joint 0
            reporter.warning("%s: a primitive of a skinned mesh has no JOINTS_0/WEIGHTS_0; its %d vertices "
                             "follow the skin's first joint" % (mpath, n))
            for _ in range(n):
                infl.append(0)
                wts.append(1.0)
                offsets.append(len(infl))
            max_inf = max(max_inf, 1)
            continue
        jl = []
        wl = []
        for s in sets:
            jd = reader.read(src.attributes["JOINTS_%d" % s])
            if jd.type != "VEC4" or jd.is_float:
                raise GltfFormatError("%s JOINTS_%d must be VEC4 unsigned byte/short" % (mpath, s))
            wd = reader.read_floats(src.attributes["WEIGHTS_%d" % s], ("VEC4",), "%s WEIGHTS_%d" % (mpath, s))
            jl.append(_gather(jd.values, 4, src.original_indices))
            wl.append(_gather(wd.values, 4, src.original_indices))
        for v in range(n):
            pairs = {}
            for jv, wv in zip(jl, wl):
                base = 4 * v
                for c in range(4):
                    w = wv[base + c]
                    if w > 0.0:
                        j = jv[base + c]
                        if j >= njoints:
                            raise GltfFormatError("%s: vertex joint index %d is out of range (%s has %d joints)"
                                                  % (mpath, j, spath, njoints))
                        pairs[j] = pairs.get(j, 0.0) + w
            total = sum(pairs.values())
            if total <= 0.0:
                unweighted += 1
                pairs = {0: 1.0}
                total = 1.0
            for j in sorted(pairs):
                infl.append(j)
                wts.append(pairs[j] / total)
            if len(pairs) > max_inf:
                max_inf = len(pairs)
            offsets.append(len(infl))
    if unweighted:
        reporter.warning("%s: %d vertices have no skin weight; they follow the skin's first joint"
                         % (mpath, unweighted))
    return SkinPlan(shape=shape_plan.index, skin=skin_index, joints=list(skin.joints), bind_pre_matrices=mats,
                    offsets=offsets, influences=infl, weights=wts, max_influences=max_inf)


def target_names(mesh, count):
    """Sanitised, unique names: ``extras.targetNames`` when it has one name per target,
    otherwise ``target_<i>``."""
    names = mesh.target_names
    if names is None or len(names) != count:
        names = [None] * count
    reg = NameRegistry()
    return [reg.claim(sanitize(names[t], "target_%d" % t), t) for t in range(count)]


def prepare_morph(doc, reader, shape_plan, md, unit_scale, reporter=None):
    """MorphPlan for one shape whose mesh has morph targets (None if it has none).
    POSITION deltas only; NORMAL/TANGENT deltas are not imported."""
    reporter = reporter or Reporter()
    mesh = doc.meshes[md.mesh_index]
    mpath = "meshes[%d]" % md.mesh_index
    counts = set(len(p.targets) for i, p in enumerate(mesh.primitives) if i not in md.skipped_primitives)
    count = max(counts) if counts else 0
    if count == 0:
        return None
    if len(counts) > 1:
        reporter.warning("%s: primitives have different numbers of morph targets (%s); missing targets "
                         "have no effect" % (mpath, ", ".join(str(c) for c in sorted(counts))))
    targets = []
    names = target_names(mesh, count)
    for t in range(count):
        vids = array("i")
        deltas = array("d")
        for src in md.sources:
            if t >= len(src.targets) or "POSITION" not in src.targets[t]:
                continue
            acc = src.targets[t]["POSITION"]
            full = doc.accessors[src.attributes["POSITION"]].count
            if doc.accessors[acc].count != full:
                raise GltfFormatError("%s: morph target %d POSITION has %d elements, the primitive has %d vertices"
                                      % (mpath, t, doc.accessors[acc].count, full))
            d = reader.read_floats(acc, ("VEC3",), "%s morph target %d POSITION" % (mpath, t))
            vals = _gather(d.values, 3, src.original_indices)
            base = src.vertex_offset
            xs = vals[0::3]
            ys = vals[1::3]
            zs = vals[2::3]
            for k, (x, y, z) in enumerate(zip(xs, ys, zs)):
                if x or y or z:
                    vids.append(base + k)
                    deltas.extend((x * unit_scale, y * unit_scale, z * unit_scale))
        targets.append(MorphTarget(name=names[t], vertices=vids, deltas=deltas))
    weights = None
    what = None
    if shape_plan.weights is not None:
        weights, what = list(shape_plan.weights), "nodes[%d].weights" % shape_plan.nodes[0]
    elif mesh.weights is not None:
        weights, what = list(mesh.weights), mpath + ".weights"
    if weights is None:
        weights = [0.0] * count
    elif len(weights) != count:
        reporter.warning("%s has %d values for %d morph targets" % (what, len(weights), count))
        weights = (weights + [0.0] * count)[:count]
    return MorphPlan(shape=shape_plan.index, targets=targets, weights=[float(w) for w in weights])


def prepare_deformers(doc, reader, plan, meshes, reporter=None):
    """(skins, morphs): {shape index: SkinPlan}, {shape index: MorphPlan}. Deformer node
    names are claimed later by the Maya layer (after the material names)."""
    reporter = reporter or Reporter()
    skins = {}
    morphs = {}
    for sp in plan.shapes:
        md = meshes.get(sp.mesh)
        if md is None or md.num_faces == 0:
            continue
        mp = prepare_morph(doc, reader, sp, md, plan.unit_scale, reporter)
        if mp is not None:
            morphs[sp.index] = mp
        if sp.skin is not None:
            skins[sp.index] = prepare_skin(doc, reader, sp, md, plan.unit_scale, reporter)
    return skins, morphs
