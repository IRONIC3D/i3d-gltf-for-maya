"""Turn a glTF mesh (all its primitives) into flat arrays ready for one Maya mesh.
No Maya imports.

Topology rules (geometry is never "converted"):
  * Every glTF vertex referenced by a triangle becomes exactly one Maya vertex. No welding.
  * TRIANGLES are taken as-is; TRIANGLE_STRIP and TRIANGLE_FAN are expanded to the
    triangles the glTF spec defines. POINTS/LINES primitives are skipped with a warning.
  * Triangles that repeat a vertex index cannot exist in a Maya mesh (MFnMesh.create
    silently drops them, which would shift face ranges), so they are dropped here with
    a warning and the face ranges account for it.
  * Vertices no triangle references are not created (Maya would keep them as isolated
    vertices). Primitives that share the same attribute accessors share vertices.

Face order is primitive order, so primitive ``p`` occupies faces
``[p.face_start, p.face_start + p.face_count)`` -- milestone 2 assigns materials from that.
"""

from array import array
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import model as m
from .diagnostics import GltfFormatError, Reporter


@dataclass
class PrimitiveRange:
    primitive_index: int
    material: Optional[int]
    face_start: int
    face_count: int
    mode: int
    source: int  # index into MeshData.sources
    degenerate_dropped: int = 0


@dataclass
class VertexSource:
    """A set of primitives sharing the same vertex attribute accessors."""
    attributes: Dict[str, int]
    targets: List[Dict[str, int]]
    vertex_offset: int = 0
    vertex_count: int = 0
    # Maya-local vertex k -> glTF accessor element original_indices[k]; None = identity.
    original_indices: Optional[List[int]] = None


@dataclass
class UVSetData:
    name: str
    texcoord: int
    u: array
    v: array  # already flipped: v = 1 - v
    uv_ids: object  # sequence, one id per face-vertex of faces that have UVs
    face_uv_counts: Optional[List[int]] = None  # None = every face has 3 UVs


@dataclass
class ColorSetData:
    name: str
    color_index: int
    has_alpha: bool
    rgba: array  # 4 floats per coloured vertex
    vertex_ids: object  # Maya vertex ids, one per colour


@dataclass
class MeshData:
    mesh_index: int
    num_vertices: int = 0
    positions: array = field(default_factory=lambda: array("d"))
    num_faces: int = 0
    face_connects: object = field(default_factory=list)
    normals: Optional[array] = None
    normal_vertex_ids: Optional[object] = None  # None = all vertices, in order
    uv_sets: List[UVSetData] = field(default_factory=list)
    color_sets: List[ColorSetData] = field(default_factory=list)
    primitives: List[PrimitiveRange] = field(default_factory=list)
    sources: List[VertexSource] = field(default_factory=list)
    skipped_primitives: List[int] = field(default_factory=list)


def uv_set_name(texcoord):
    """TEXCOORD_0 -> 'map1' (Maya's default set), TEXCOORD_n -> 'map<n+1>'."""
    return "map%d" % (texcoord + 1)


def color_set_name(color_index):
    """COLOR_0 -> 'colorSet1', COLOR_n -> 'colorSet<n+1>' (Maya's own numbering)."""
    return "colorSet%d" % (color_index + 1)


def triangulate(indices, mode):
    """Return a flat list of triangle vertex indices for a triangle-type mode."""
    n = len(indices)
    if mode == m.TRIANGLES:
        return list(indices[:n - n % 3]) if n % 3 else list(indices)
    out = []
    if mode == m.TRIANGLE_STRIP:
        for i in range(n - 2):
            if i % 2 == 0:
                out.extend((indices[i], indices[i + 1], indices[i + 2]))
            else:
                out.extend((indices[i], indices[i + 2], indices[i + 1]))
        return out
    if mode == m.TRIANGLE_FAN:
        v0 = indices[0] if n else 0
        for i in range(n - 2):
            out.extend((indices[i + 1], indices[i + 2], v0))
        return out
    raise ValueError("not a triangle mode: %r" % mode)


def drop_degenerate(tris):
    """Remove triangles that repeat a vertex index. Returns (tris, dropped_count)."""
    a = tris[0::3]
    b = tris[1::3]
    c = tris[2::3]
    bad = [k for k, (x, y, z) in enumerate(zip(a, b, c)) if x == y or y == z or x == z]
    if not bad:
        return tris, 0
    badset = set(bad)
    out = []
    for k in range(len(a)):
        if k not in badset:
            out.extend((a[k], b[k], c[k]))
    return out, len(bad)


def _gather(values, ncomp, original_indices):
    """values[original_indices] for flat arrays with ncomp components per element."""
    if original_indices is None:
        return values
    if ncomp == 1:
        idx = original_indices
    else:
        idx = [o * ncomp + k for o in original_indices for k in range(ncomp)]
    return array(values.typecode, map(values.__getitem__, idx))


def _source_key(prim):
    return (tuple(sorted(prim.attributes.items())),
            tuple(tuple(sorted(t.items())) for t in prim.targets))


def _texcoord_sets(attrs):
    return sorted(int(k[9:]) for k in attrs if k.startswith("TEXCOORD_") and k[9:].isdigit())


def _color_sets(attrs):
    return sorted(int(k[6:]) for k in attrs if k.startswith("COLOR_") and k[6:].isdigit())


def prepare_mesh(doc, reader, mesh_index, options, unit_scale, reporter=None):
    """Build MeshData for doc.meshes[mesh_index]. Raises GltfFormatError on invalid data."""
    reporter = reporter or Reporter()
    mesh = doc.meshes[mesh_index]
    mpath = "meshes[%d]" % mesh_index
    data = MeshData(mesh_index=mesh_index)

    # 1. triangles per primitive, grouped by vertex source
    source_keys = {}
    prim_tris = []  # (prim_index, prim, source_index, tris, dropped)
    for j, prim in enumerate(mesh.primitives):
        ppath = "%s.primitives[%d]" % (mpath, j)
        if prim.mode not in (m.TRIANGLES, m.TRIANGLE_STRIP, m.TRIANGLE_FAN):
            reporter.warning("%s: mode %s is not supported (only triangles); primitive skipped"
                             % (ppath, m.MODE_NAMES.get(prim.mode, prim.mode)))
            data.skipped_primitives.append(j)
            continue
        pos_index = prim.attributes.get("POSITION")
        if pos_index is None:
            reporter.warning("%s has no POSITION attribute; primitive skipped" % ppath)
            data.skipped_primitives.append(j)
            continue
        vcount = doc.accessors[pos_index].count
        for sem, acc in prim.attributes.items():
            if doc.accessors[acc].count != vcount:
                raise GltfFormatError("%s: attribute %s has %d elements but POSITION has %d"
                                      % (ppath, sem, doc.accessors[acc].count, vcount))
        if prim.indices is not None:
            idx = reader.read_indices(prim.indices, ppath + ".indices")
            if len(idx) and max(idx) >= vcount:
                bad = max(idx)
                raise GltfFormatError("%s.indices: vertex index %d is out of range (POSITION has %d vertices)"
                                      % (ppath, bad, vcount))
        else:
            idx = range(vcount)
        count = len(idx)
        if prim.mode == m.TRIANGLES and count % 3:
            reporter.warning("%s: %d indices is not a multiple of 3; trailing %d ignored"
                             % (ppath, count, count % 3))
        tris = triangulate(idx, prim.mode)
        tris, dropped = drop_degenerate(tris)
        if dropped:
            reporter.warning("%s: dropped %d degenerate triangle(s) that repeat a vertex "
                             "(Maya cannot represent them)" % (ppath, dropped))
        key = _source_key(prim)
        if key not in source_keys:
            source_keys[key] = len(data.sources)
            data.sources.append(VertexSource(attributes=dict(prim.attributes),
                                             targets=[dict(t) for t in prim.targets],
                                             vertex_count=vcount))
        prim_tris.append((j, prim, source_keys[key], tris, dropped))

    # 2. per source: which vertices are used, compaction map, offsets
    remaps = []
    offset = 0
    for s_index, src in enumerate(data.sources):
        used = set()
        for (_, _, s, tris, _) in prim_tris:
            if s == s_index:
                used.update(tris)
        full = src.vertex_count
        if len(used) == full:
            remaps.append(None)
            src.original_indices = None
        else:
            ordered = sorted(used)
            remap = [-1] * full
            for new, old in enumerate(ordered):
                remap[old] = new
            remaps.append(remap)
            src.original_indices = ordered
            src.vertex_count = len(ordered)
        src.vertex_offset = offset
        offset += src.vertex_count
    data.num_vertices = offset

    # 3. faces in primitive order
    connects = []
    face = 0
    for (j, prim, s, tris, dropped) in prim_tris:
        src = data.sources[s]
        remap = remaps[s]
        if remap is not None:
            tris = list(map(remap.__getitem__, tris))
        if src.vertex_offset:
            tris = list(map(src.vertex_offset.__add__, tris))
        start = face
        nf = len(tris) // 3
        if connects:
            connects.extend(tris)
        else:
            connects = list(tris)
        face += nf
        data.primitives.append(PrimitiveRange(primitive_index=j, material=prim.material, face_start=start,
                                              face_count=nf, mode=prim.mode, source=s,
                                              degenerate_dropped=dropped))
    data.num_faces = face
    data.face_connects = connects

    # 4. positions (unit scale baked in)
    positions = array("d")
    for src in data.sources:
        p = reader.read_floats(src.attributes["POSITION"], ("VEC3",), mpath + " POSITION")
        vals = _gather(p.values, 3, src.original_indices)
        positions.extend([v * unit_scale for v in vals])
    data.positions = positions

    # 5. normals
    if options.importNormals:
        normals = array("f")
        ids = []
        all_have = True
        for src in data.sources:
            acc = src.attributes.get("NORMAL")
            if acc is None:
                all_have = False
                continue
            nrm = reader.read_floats(acc, ("VEC3",), mpath + " NORMAL")
            normals.extend(_gather(nrm.values, 3, src.original_indices))
            ids.extend(range(src.vertex_offset, src.vertex_offset + src.vertex_count))
        if len(normals):
            data.normals = normals
            data.normal_vertex_ids = None if all_have else ids

    # 6. UV sets
    sets = sorted(set(n for src in data.sources for n in _texcoord_sets(src.attributes)))
    for n in sets:
        sem = "TEXCOORD_%d" % n
        u = array("f")
        v = array("f")
        bases = {}
        for s_index, src in enumerate(data.sources):
            acc = src.attributes.get(sem)
            if acc is None:
                continue
            bases[s_index] = len(u)
            uv = reader.read_floats(acc, ("VEC2",), "%s %s" % (mpath, sem))
            vals = _gather(uv.values, 2, src.original_indices)
            u.extend(vals[0::2])
            v.extend([1.0 - x for x in vals[1::2]])
        identity = all(bases[s] == data.sources[s].vertex_offset for s in bases)
        all_faces = all(pr.source in bases for pr in data.primitives)
        if identity and all_faces:
            uv_ids = connects
            counts = None
        else:
            uv_ids = []
            counts = []
            for pr in data.primitives:
                if pr.source in bases:
                    delta = bases[pr.source] - data.sources[pr.source].vertex_offset
                    seg = connects[pr.face_start * 3:(pr.face_start + pr.face_count) * 3]
                    uv_ids.extend(map(delta.__add__, seg) if delta else seg)
                    counts.extend([3] * pr.face_count)
                else:
                    counts.extend([0] * pr.face_count)
        data.uv_sets.append(UVSetData(name=uv_set_name(n), texcoord=n, u=u, v=v, uv_ids=uv_ids,
                                      face_uv_counts=counts))

    # 7. colour sets
    if options.importColors:
        csets = sorted(set(n for src in data.sources for n in _color_sets(src.attributes)))
        for n in csets:
            sem = "COLOR_%d" % n
            rgba = array("f")
            ids = []
            has_alpha = False
            for src in data.sources:
                acc = src.attributes.get(sem)
                if acc is None:
                    continue
                col = reader.read_floats(acc, ("VEC3", "VEC4"), "%s %s" % (mpath, sem))
                vals = _gather(col.values, col.num_components, src.original_indices)
                if col.num_components == 4:
                    has_alpha = True
                    rgba.extend(vals)
                else:
                    cnt = len(vals) // 3
                    tmp = array("f", bytes(4 * 4 * cnt))
                    tmp[0::4] = vals[0::3]
                    tmp[1::4] = vals[1::3]
                    tmp[2::4] = vals[2::3]
                    tmp[3::4] = array("f", [1.0]) * cnt
                    rgba.extend(tmp)
                ids.extend(range(src.vertex_offset, src.vertex_offset + src.vertex_count))
            data.color_sets.append(ColorSetData(name=color_set_name(n), color_index=n, has_alpha=has_alpha,
                                                rgba=rgba, vertex_ids=ids))
    return data
