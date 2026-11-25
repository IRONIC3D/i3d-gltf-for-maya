"""Compare deformed Maya meshes with the reference evaluator (gltf_eval). Needs Maya.

``compare(result, evaluator, times)`` sets the scene time to each time (seconds), reads
every imported shape instance's world-space points and compares them, through the
importer's Maya-vertex -> glTF-accessor-element mapping (MeshData.sources), with the
evaluator's world positions (scaled to centimetres x the scale option, rotated for a
Z-up scene). Returns a dict with the max error (cm), where it happened, and the scene's
size for context.
"""

import math

import maya.api.OpenMaya as om
import maya.cmds as cmds


def set_time(seconds):
    cmds.currentTime(om.MTime(seconds, om.MTime.kSeconds).asUnits(om.MTime.uiUnit()), update=True)


def _vertex_map(md):
    """[(maya vertex id, primitive index, accessor element), ...]"""
    first_prim = {}
    for pr in md.primitives:
        first_prim.setdefault(pr.source, pr.primitive_index)
    out = []
    for s, src in enumerate(md.sources):
        if s not in first_prim:
            continue
        oi = src.original_indices
        for k in range(src.vertex_count):
            out.append((src.vertex_offset + k, first_prim[s], oi[k] if oi is not None else k))
    return out


def compare(result, evaluator, times, z_up=None, skip_nodes=()):
    factor = result.prepared.plan.unit_scale
    if z_up is None:
        z_up = cmds.upAxis(q=True, axis=True).lower() == "z"
    maps = dict((sr.shape_index, _vertex_map(sr.mesh_data)) for sr in result.shapes)
    worst = {"max_error": 0.0, "where": None, "points": 0, "extent": 0.0}
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    for t in times:
        set_time(t)
        ref = evaluator.positions(t)
        for sr in result.shapes:
            for node in sr.node_indices:
                path = sr.path_for_node(node)
                if path is None or node not in ref or node in skip_nodes:
                    continue
                sel = om.MSelectionList()
                sel.add(path)
                pts = om.MFnMesh(sel.getDagPath(0)).getPoints(om.MSpace.kWorld)
                prims = ref[node]
                for vid, prim, elem in maps[sr.shape_index]:
                    x, y, z = prims[prim][elem]
                    if z_up:
                        x, y, z = x, -z, y
                    x, y, z = x * factor, y * factor, z * factor
                    p = pts[vid]
                    err = math.sqrt((p.x - x) ** 2 + (p.y - y) ** 2 + (p.z - z) ** 2)
                    worst["points"] += 1
                    for k, c in enumerate((x, y, z)):
                        lo[k] = min(lo[k], c)
                        hi[k] = max(hi[k], c)
                    if err > worst["max_error"]:
                        worst["max_error"] = err
                        worst["where"] = (t, path, vid, (p.x, p.y, p.z), (x, y, z))
    if worst["points"]:
        worst["extent"] = math.sqrt(sum((hi[k] - lo[k]) ** 2 for k in range(3)))
    return worst


def sample_times(evaluator, extra=(), count=7):
    """Key-range start and end, evenly spaced times between (mostly between keys), plus extras."""
    t0, t1 = evaluator.time_range()
    if t1 <= t0:
        return [t0] + list(extra)
    out = [t0 + (t1 - t0) * k / (count - 1) for k in range(count)]
    out += [t0 + (t1 - t0) * f for f in (0.123, 0.377, 0.618)]
    return sorted(set(out + list(extra)))
