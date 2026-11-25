"""Scene planning: which nodes to build, their names, transforms (with the unit scale
baked in) and how meshes are shared between nodes. No Maya imports.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .diagnostics import GltfFormatError, Reporter
from .names import NameRegistry, sanitize

#: glTF is in metres; Maya's internal unit is always centimetres.
METERS_TO_CENTIMETERS = 100.0

TRS_PATHS = frozenset(("translation", "rotation", "scale"))


def unit_scale(options):
    """The factor baked into positions/translations: 100 x the user ``scale`` option."""
    return METERS_TO_CENTIMETERS * float(options.scale)


@dataclass
class ShapePlan:
    """One Maya mesh shape. ``nodes[0]`` owns it; the other nodes are Maya instances.

    Skinned shapes (``skin`` set) follow the glTF rule that the mesh node's own transform
    is ignored: the shape sits under a transform with identity TRS and inheritsTransform
    off, so its world matrix is identity and the skinCluster output is the world position.
    That transform is the node's own transform when the node is a plain leaf (no children,
    not a joint, never animated), otherwise an extra child transform ``skin_transform``.
    """
    index: int
    mesh: int
    name: str
    nodes: List[int] = field(default_factory=list)
    skin: Optional[int] = None
    weights: Optional[tuple] = None
    skin_transform: Optional[str] = None  # name of the extra identity transform, if any


@dataclass
class NodePlan:
    index: int
    name: str
    parent: Optional[int]
    children: List[int]
    translation: List[float]  # unit scale already applied
    rotation: List[float]     # quaternion x, y, z, w
    scale: List[float]
    matrix: Optional[List[float]] = None  # column-major 4x4, translation already scaled
    mesh: Optional[int] = None
    shape: Optional[int] = None  # index into ScenePlan.shapes
    joint: bool = False          # created as a Maya joint
    skinned_leaf: bool = False   # holds a skinned shape directly: identity TRS, no inheritance


@dataclass
class ScenePlan:
    scene_index: Optional[int]
    roots: List[int]
    order: List[int]  # parents before children (depth-first, glTF child order)
    nodes: Dict[int, NodePlan]
    shapes: List[ShapePlan]
    unit_scale: float
    names: Optional[NameRegistry] = None  # every name claimed so far (materials claim after nodes)
    joints: List[int] = field(default_factory=list)  # nodes built as joints, sorted

    def shape_of_node(self, node_index):
        """The ShapePlan built for this node's mesh, or None."""
        n = self.nodes.get(node_index)
        return self.shapes[n.shape] if n is not None and n.shape is not None else None


def _parents(doc):
    parent = {}
    for i, n in enumerate(doc.nodes):
        for c in n.children:
            if c in parent:
                raise GltfFormatError("nodes[%d] has more than one parent (nodes[%d] and nodes[%d])"
                                      % (c, parent[c], i))
            if c == i:
                raise GltfFormatError("nodes[%d] lists itself as a child" % i)
            parent[c] = i
    # cycle check over the whole node graph (with one parent each, a cycle is a parent loop)
    done = set()
    for i in range(len(doc.nodes)):
        path = set()
        j = i
        while j is not None and j not in done:
            if j in path:
                raise GltfFormatError("Node hierarchy contains a cycle at nodes[%d]" % j)
            path.add(j)
            j = parent.get(j)
        done.update(path)
    return parent


def _is_ancestor(parent, a, b):
    """True if node a is an ancestor of node b."""
    p = parent.get(b)
    while p is not None:
        if p == a:
            return True
        p = parent.get(p)
    return False


def animated_nodes(doc):
    """{node index: set of animated paths} over every animation in the file (not only the
    imported one, so the scene structure does not depend on the ``animation`` option)."""
    out = {}
    for a in doc.animations:
        for c in a.channels:
            if c.target_node is not None:
                out.setdefault(c.target_node, set()).add(c.target_path)
    return out


def joint_nodes(doc, parent, visited):
    """Nodes built as Maya joints: every node a skin uses as a joint, plus the nodes that
    sit between two joints in the hierarchy (so a skeleton is one unbroken joint chain;
    those extra joints are not influences). Ancestors above the top joints stay transforms.
    Skins with joints outside the imported scene are left out (their meshes are static)."""
    joints = set()
    bad_skins = set()
    for s_index, skin in enumerate(doc.skins):
        if not skin.joints:
            bad_skins.add(s_index)
            continue
        missing = [j for j in skin.joints if j not in visited]
        if missing:
            bad_skins.add(s_index)
            continue
        joints.update(skin.joints)
    above = set()  # every ancestor of a joint
    for j in joints:
        p = parent.get(j)
        while p is not None and p not in above:
            above.add(p)
            p = parent.get(p)
    between = set()
    for n in above - joints:
        p = parent.get(n)
        while p is not None:
            if p in joints:
                between.add(n)
                break
            p = parent.get(p)
    return joints | between, bad_skins


def _has_skin_attributes(mesh):
    return any("JOINTS_0" in p.attributes and "WEIGHTS_0" in p.attributes for p in mesh.primitives)


def build_scene_plan(doc, options, reporter=None):
    reporter = reporter or Reporter()
    factor = unit_scale(options)
    parent = _parents(doc)

    # which roots
    if doc.scenes:
        scene_index = doc.scene if doc.scene is not None else 0
        roots = list(doc.scenes[scene_index].nodes)
        if doc.scene is None and len(doc.scenes) > 1:
            reporter.warning("No default scene set; importing scenes[0] of %d" % len(doc.scenes))
    else:
        scene_index = None
        roots = [i for i in range(len(doc.nodes)) if i not in parent]
        if roots:
            reporter.info("File has no scenes; importing all %d root nodes" % len(roots))
    seen_roots = set()
    for r in roots:
        if r in parent:
            raise GltfFormatError("scenes[%s] lists nodes[%d] as a root, but it is the child of nodes[%d]"
                                  % (scene_index, r, parent[r]))
        if r in seen_roots:
            raise GltfFormatError("scenes[%s] lists nodes[%d] twice" % (scene_index, r))
        seen_roots.add(r)

    # depth-first order; detects cycles
    order = []
    visited = set()
    for r in roots:
        stack = [r]
        while stack:
            i = stack.pop()
            if i in visited:
                raise GltfFormatError("Node hierarchy contains a cycle at nodes[%d]" % i)
            visited.add(i)
            order.append(i)
            stack.extend(reversed(doc.nodes[i].children))

    # names, in glTF index order so they never depend on traversal
    registry = NameRegistry()
    names = {}
    for i in sorted(visited):
        names[i] = registry.claim(sanitize(doc.nodes[i].name, "node_%d" % i), i)

    nodes = {}
    for i in order:
        n = doc.nodes[i]
        if n.matrix is not None:
            mat = list(n.matrix)
            mat[12] *= factor
            mat[13] *= factor
            mat[14] *= factor
            if n.translation or n.rotation or n.scale:
                reporter.warning("nodes[%d] has both matrix and TRS; using matrix" % i)
        else:
            mat = None
        t = n.translation or [0.0, 0.0, 0.0]
        nodes[i] = NodePlan(index=i, name=names[i], parent=parent.get(i), children=list(n.children),
                            translation=[t[0] * factor, t[1] * factor, t[2] * factor],
                            rotation=list(n.rotation or [0.0, 0.0, 0.0, 1.0]),
                            scale=list(n.scale or [1.0, 1.0, 1.0]),
                            matrix=mat, mesh=n.mesh)

    joints, bad_skins = joint_nodes(doc, parent, visited)
    for i in joints:
        nodes[i].joint = True
    animated = animated_nodes(doc)

    # mesh sharing: nodes with the same mesh share one shape (Maya instancing), except
    # where Maya cannot instance (a node and its ancestor) and for deformed meshes:
    # skinned and morphed meshes get one shape per node (a deformer per shape).
    shapes = []
    skin_transform_nodes = []
    for i in sorted(visited):
        n = doc.nodes[i]
        if n.mesh is None:
            continue
        weights = tuple(n.weights) if n.weights is not None else None
        mesh = doc.meshes[n.mesh]
        skin = n.skin
        if skin is not None:
            if skin in bad_skins:
                reporter.warning("nodes[%d]: skins[%d] has no joints or uses joints outside the imported "
                                 "scene; the mesh is imported unskinned" % (i, skin))
                skin = None
            elif not _has_skin_attributes(mesh):
                reporter.warning("nodes[%d]: meshes[%d] has no JOINTS_0/WEIGHTS_0; the mesh is imported "
                                 "unskinned" % (i, n.mesh))
                skin = None
        morphed = any(p.targets for p in mesh.primitives)
        target = None
        if skin is None and not morphed:
            for s in shapes:
                if s.mesh == n.mesh and s.skin is None and s.weights == weights:
                    if any(_is_ancestor(parent, i, o) or _is_ancestor(parent, o, i) for o in s.nodes):
                        continue
                    target = s
                    break
        if target is None:
            target = ShapePlan(index=len(shapes), mesh=n.mesh,
                               name=registry.claim(names[i] + "Shape", i),
                               nodes=[], skin=skin, weights=weights)
            shapes.append(target)
            if skin is not None:
                if n.children or i in joints or animated.get(i, set()) & TRS_PATHS:
                    skin_transform_nodes.append((i, target))
                else:
                    nodes[i].skinned_leaf = True
        target.nodes.append(i)
        nodes[i].shape = target.index
    for i, target in skin_transform_nodes:
        target.skin_transform = registry.claim(names[i] + "_skinned", i)
    return ScenePlan(scene_index=scene_index, roots=roots, order=order, nodes=nodes, shapes=shapes,
                     unit_scale=factor, names=registry, joints=sorted(joints))
