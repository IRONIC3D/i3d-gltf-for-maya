"""Build blendShape and skinCluster deformers from core.deformers plans.

Both are created with the Maya commands (which set up the deformer chain, orig shape and
painting connections the same way the UI does) and then filled with bulk API calls:

  * blendShape: created first, so it sits before the skinCluster in the chain (glTF
    applies morph targets, then skinning). Targets are written straight into
    ``inputTarget[0].inputTargetGroup[t].inputTargetItem[6000]`` as delta points plus
    the list of moved vertices; no target meshes are created. Weights are aliased with
    the target names.
  * skinCluster: classic linear skinning, one influence per skin joint in skin order,
    ``bindPreMatrix`` = the file's inverseBindMatrices (translation unit-scaled),
    ``geomMatrix`` = identity (the skinned shape's world matrix is identity, see
    core.scene.ShapePlan), weights normalised in core and set with
    MFnSkinCluster.setWeights in dense chunks, ``deformUserNormals`` on (milestone 1
    imports glTF normals as locked user normals). The dagPose ``cmds.skinCluster``
    records is deleted: it would hold the import pose, not the file's bind pose.
"""

from array import array

import maya.api.OpenMaya as om
import maya.api.OpenMayaAnim as oma
import maya.cmds as cmds

from ..core.names import NameRegistry

#: blendShape inputTargetItem index for a full-weight (1.0) target.
FULL_WEIGHT_ITEM = 6000
#: upper bound for one dense setWeights call (vertices x influences doubles).
DENSE_CHUNK = 2000000


def _plug(name):
    sel = om.MSelectionList()
    sel.add(name)
    return sel.getPlug(0)


def _dep(name):
    sel = om.MSelectionList()
    sel.add(name)
    return sel.getDependNode(0)


def _dag(name):
    sel = om.MSelectionList()
    sel.add(name)
    return sel.getDagPath(0)


def _vertex_component(ids=None, count=None):
    fn = om.MFnSingleIndexedComponent()
    obj = fn.create(om.MFn.kMeshVertComponent)
    if ids is not None:
        fn.addElements(list(ids))
    else:
        fn.setCompleteData(count)
    return obj


def build_blendshape(shape_path, morph, name):
    """Create the blendShape for ``morph`` (core.deformers.MorphPlan) on the shape.
    Returns (blendShape node name, [weight alias per target])."""
    bs = cmds.blendShape(shape_path, name=name, origin="local")[0]
    reserved = set(cmds.listAttr(bs) or []) | set(cmds.listAttr(bs, shortNames=True) or [])
    registry = NameRegistry(reserved)
    aliases = []
    group = _plug(bs + ".inputTarget[0].inputTargetGroup")
    for t, target in enumerate(morph.targets):
        item = group.elementByLogicalIndex(t).child(0).elementByLogicalIndex(FULL_WEIGHT_ITEM)
        pts_plug = _child(item, "inputPointsTarget")
        comp_plug = _child(item, "inputComponentsTarget")
        d = target.deltas
        it = iter(d)
        points = om.MPointArray([om.MPoint(x, y, z) for x, y, z in zip(it, it, it)])
        pts_plug.setMObject(om.MFnPointArrayData().create(points))
        clist = om.MFnComponentListData()
        cobj = clist.create()
        if len(target.vertices):
            clist.add(_vertex_component(target.vertices))
        comp_plug.setMObject(cobj)
        weight = "%s.weight[%d]" % (bs, t)
        cmds.setAttr(weight, morph.weights[t] if t < len(morph.weights) else 0.0)
        alias = registry.claim(target.name, t)
        cmds.aliasAttr(alias, weight)
        aliases.append(alias)
    return bs, aliases


def _child(plug, name):
    for i in range(plug.numChildren()):
        c = plug.child(i)
        if om.MFnAttribute(c.attribute()).name == name:
            return c
    raise RuntimeError("blendShape plug %s has no child %s" % (plug.name(), name))


def build_skin(shape_path, skin, joint_paths, name):
    """Create the skinCluster for ``skin`` (core.deformers.SkinPlan). ``joint_paths``:
    full DAG paths of the influence joints, in skin joint order. Returns the node name."""
    node = cmds.skinCluster(joint_paths, shape_path, toSelectedBones=True, bindMethod=0, skinMethod=0,
                            normalizeWeights=1, maximumInfluences=max(1, skin.max_influences),
                            obeyMaxInfluences=False, removeUnusedInfluence=False, name=name)[0]
    poses = cmds.listConnections(node + ".bindPose", source=True, destination=False) or []
    if poses:
        cmds.delete(poses)
    cmds.setAttr(node + ".deformUserNormals", True)

    fn = oma.MFnSkinCluster(_dep(node))
    physical = {}
    for k, p in enumerate(fn.influenceObjects()):
        physical[p.fullPathName()] = k
    bpm = _plug(node + ".bindPreMatrix")
    phys_of_slot = []
    for slot, jp in enumerate(joint_paths):
        path = _dag(jp)
        logical = fn.indexForInfluenceObject(path)
        bpm.elementByLogicalIndex(logical).setMObject(
            om.MFnMatrixData().create(om.MMatrix(skin.bind_pre_matrices[slot])))
        phys_of_slot.append(physical[path.fullPathName()])
    _plug(node + ".geomMatrix").setMObject(om.MFnMatrixData().create(om.MMatrix()))

    ni = len(physical)
    nv = skin.num_vertices
    shape = _dag(shape_path)
    influences = om.MIntArray(list(range(ni)))
    chunk = max(1, DENSE_CHUNK // max(1, ni))
    offsets, infl, wts = skin.offsets, skin.influences, skin.weights
    for v0 in range(0, nv, chunk):
        v1 = min(nv, v0 + chunk)
        dense = array("d", bytes(8 * (v1 - v0) * ni))
        for v in range(v0, v1):
            row = (v - v0) * ni
            for e in range(offsets[v], offsets[v + 1]):
                dense[row + phys_of_slot[infl[e]]] = wts[e]
        comp = _vertex_component(count=nv) if (v0 == 0 and v1 == nv) else _vertex_component(range(v0, v1))
        fn.setWeights(shape, comp, influences, om.MDoubleArray(dense), False)
    return node
