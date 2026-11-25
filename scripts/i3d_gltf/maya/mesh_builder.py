"""Create a Maya mesh shape from core.meshprep.MeshData with bulk API 2.0 calls."""

import maya.api.OpenMaya as om


def _point_array(flat):
    it = iter(flat)
    return om.MPointArray(list(zip(it, it, it)))


def _vector_array(flat):
    it = iter(flat)
    return om.MVectorArray(list(zip(it, it, it)))


def _color_array(flat_rgba):
    it = iter(flat_rgba)
    return om.MColorArray(list(zip(it, it, it, it)))


def create_mesh_shape(md, parent, name, reporter):
    """Create one mesh shape under transform ``parent`` (MObject). Returns the shape MObject."""
    fn = om.MFnMesh()
    fn.create(_point_array(md.positions), om.MIntArray(md.num_faces, 3), om.MIntArray(md.face_connects),
              parent=parent)
    shape = fn.object()
    om.MFnDependencyNode(shape).setName(name)

    if fn.numPolygons != md.num_faces or fn.numVertices != md.num_vertices:
        # Should never happen (degenerates are removed beforehand) but face ranges would
        # be wrong for material assignment, so make it loud.
        reporter.warning("mesh %s: Maya created %d faces / %d vertices, expected %d / %d"
                         % (name, fn.numPolygons, fn.numVertices, md.num_faces, md.num_vertices))

    # UV sets. TEXCOORD_0 goes into Maya's default 'map1'.
    for uvs in md.uv_sets:
        set_name = uvs.name
        if set_name not in fn.getUVSetNames():
            set_name = fn.createUVSet(set_name)
        fn.setUVs(om.MFloatArray(uvs.u), om.MFloatArray(uvs.v), set_name)
        counts = om.MIntArray(md.num_faces, 3) if uvs.face_uv_counts is None else om.MIntArray(uvs.face_uv_counts)
        fn.assignUVs(counts, om.MIntArray(uvs.uv_ids), set_name)
    if md.uv_sets:
        fn.setCurrentUVSetName(md.uv_sets[0].name)

    # Normals: one normal per glTF vertex -> shared vertex normals (Maya locks them).
    if md.normals is not None:
        if md.normal_vertex_ids is None:
            ids = om.MIntArray(range(md.num_vertices))
        else:
            ids = om.MIntArray(md.normal_vertex_ids)
        fn.setVertexNormals(_vector_array(md.normals), ids)

    # Colour sets
    for cs in md.color_sets:
        rep = om.MFnMesh.kRGBA if cs.has_alpha else om.MFnMesh.kRGB
        set_name = fn.createColorSet(cs.name, False, rep=rep)
        fn.setCurrentColorSetName(set_name)
        fn.setVertexColors(_color_array(cs.rgba), om.MIntArray(cs.vertex_ids), rep=rep)
    if md.color_sets:
        fn.setCurrentColorSetName(md.color_sets[0].name)
    return shape
