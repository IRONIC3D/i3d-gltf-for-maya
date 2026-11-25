"""Build a prepared glTF import into the Maya scene.

Entry points:
  * ``import_file(path, options_string)`` -- used by the translator's reader().
  * ``last_result()`` -- the ImportResult of the most recent import in this session.

Everything that can fail on bad input runs in ``core.prepare_import`` before any node is
created. If building still fails, every node created so far is deleted and the error
is re-raised, so ``cmds.file`` fails without leaving a half-built hierarchy.

Nodes are created with API 2.0 inside the translator call; Maya puts them in the
requested namespace (the translator reports haveNamespaceSupport) and, for
``reference=True``, marks them as owned by the reference. Every node created while
building (including the ones Maya adds for deformers) is tracked with a node-added
callback, so a failure deletes all of them.

Build order: transforms and joints, mesh shapes, materials, blendShapes, skinClusters,
anim curves, playback range.
"""

import math
import os
import time

import maya.OpenMaya as om1  # MFileIO only exists in API 1.0
import maya.api.OpenMaya as om
import maya.cmds as cmds

from ..core import Reporter, parse_options, prepare_import
from ..core import images as img
from ..core.materials import dropped_summary
from . import shading
from .animation import AnimationBuilder, ConnectTarget
from .deformers import build_blendshape, build_skin
from .mesh_builder import create_mesh_shape
from .report import maya_sink

DEFAULT_SHADING_GROUP = shading.DEFAULT_SHADING_GROUP

_last_result = None
_last_error = None
_last_attempt = None  # (path, ImportResult or None, error message or None, Reporter)


def last_result():
    """The ImportResult of the most recent successful import (or None)."""
    return _last_result


def last_error():
    """(path, message, reporter) of the most recent failed import, or None if the most
    recent import succeeded. Useful after ``cmds.file(reference=True)``: Maya does not
    raise when a reference fails to load, it only prints the error."""
    return _last_error


def _path(handle):
    if handle is None or not handle.isValid():
        return None
    return om.MDagPath.getAPathTo(handle.object()).fullPathName()


class ShapeResult(object):
    """A built mesh shape and the per-primitive face ranges milestone 2 assigns materials from.

    ``primitives`` is a list of core.meshprep.PrimitiveRange with ``primitive_index``,
    ``material`` (glTF material index or None), ``face_start`` and ``face_count``.
    ``instance_paths`` are the DAG paths of every instance (face assignment in Maya is
    per instance, e.g. ``'%s.f[%d:%d]' % (path, start, start + count - 1)``).
    """

    def __init__(self, shape_plan, mesh_data, handle, parents=None):
        self.shape_index = shape_plan.index
        self.mesh_index = shape_plan.mesh
        self.node_indices = list(shape_plan.nodes)
        self.mesh_data = mesh_data
        self.primitives = list(mesh_data.primitives)
        self.skinned = shape_plan.skin is not None
        self._handle = handle
        self._parents = parents or {}  # glTF node index -> MObjectHandle of the transform above the shape
        self.target_aliases = []       # blendShape weight alias per morph target
        self._blend_shape = None       # MObjectHandle
        self._skin_cluster = None

    @staticmethod
    def _name(handle):
        if handle is None or not handle.isValid() or not handle.isAlive():
            return None
        return om.MFnDependencyNode(handle.object()).name()

    @property
    def blend_shape(self):
        """blendShape node name (current, with namespace) or None."""
        return self._name(self._blend_shape)

    @property
    def skin_cluster(self):
        return self._name(self._skin_cluster)

    @property
    def mobject(self):
        return self._handle.object()

    @property
    def name(self):
        return om.MFnDependencyNode(self._handle.object()).name()

    @property
    def instance_paths(self):
        return [p.fullPathName() for p in om.MDagPath.getAllPathsTo(self._handle.object())]

    def path_for_node(self, node_index):
        """DAG path (string) of this shape's instance that belongs to glTF node ``node_index``
        (for a skinned shape under an extra '<node>_skinned' transform, that transform's path)."""
        h = self._parents.get(node_index)
        if h is None:
            return None
        parent = om.MDagPath.getAPathTo(h.object())
        for p in om.MDagPath.getAllPathsTo(self._handle.object()):
            q = om.MDagPath(p)
            q.pop()
            if q == parent:
                return p.fullPathName()
        return None

    def face_ranges(self):
        """[(primitive_index, material_index, first_face, face_count), ...]"""
        return [(p.primitive_index, p.material, p.face_start, p.face_count) for p in self.primitives]


class ImportResult(object):
    def __init__(self, path, options, prepared, reporter):
        self.path = path
        self.options = options
        self.prepared = prepared
        self.reporter = reporter
        self.transforms = {}  # glTF node index -> MObjectHandle
        self.shapes = []      # ShapeResult, in ScenePlan.shapes order
        self.timings = {}     # seconds: "prepare" (pure Python), "build" (Maya)
        self.root_indices = list(prepared.plan.roots) if prepared else []
        self.shader_type = None   # node type actually used (after fallback), None for shader=none
        self._shaders = {}        # glTF material index -> MObjectHandle
        self._shading_groups = {}
        self._file_nodes = {}     # MaterialPlan texture index -> MObjectHandle
        self._curves = {}         # AnimationPlan curve index -> MObjectHandle
        self.time_range = None    # (first frame, last frame) set on the playback range, or None

    @staticmethod
    def _names(handles):
        return dict((k, om.MFnDependencyNode(h.object()).name()) for k, h in handles.items()
                    if h.isValid() and h.isAlive())

    @property
    def shaders(self):
        """{glTF material index: shader node name} (current names, with namespace)."""
        return self._names(self._shaders)

    @property
    def shading_groups(self):
        return self._names(self._shading_groups)

    @property
    def file_nodes(self):
        """{MaterialPlan texture index: file node name}"""
        return self._names(self._file_nodes)

    @property
    def anim_curves(self):
        """{AnimationPlan curve index: anim curve node name}"""
        return self._names(self._curves)

    @property
    def animation(self):
        """The imported core.animation.AnimationPlan (None when no animation was imported)."""
        return self.prepared.animation if self.prepared else None

    @property
    def blend_shapes(self):
        """{shape index: blendShape node name}"""
        return dict((s.shape_index, s.blend_shape) for s in self.shapes if s.blend_shape)

    @property
    def skin_clusters(self):
        """{shape index: skinCluster node name}"""
        return dict((s.shape_index, s.skin_cluster) for s in self.shapes if s.skin_cluster)

    @property
    def material_plan(self):
        return self.prepared.materials if self.prepared else None

    def texture_stats(self):
        """(linked file nodes, of which the file is missing, images skipped as unreadable/failed)."""
        plan = self.material_plan
        if plan is None:
            return 0, 0, 0
        linked = [plan.textures[i] for i in self._file_nodes]
        missing = sum(1 for t in linked if t.status == img.MISSING)
        skipped = sum(1 for f in plan.images.values() if f.status in (img.UNSUPPORTED, img.FAILED))
        return len(linked), missing, skipped

    @property
    def messages(self):
        return list(self.reporter.messages)

    def node_path(self, node_index):
        return _path(self.transforms.get(node_index))

    @property
    def root_paths(self):
        return [self.node_path(i) for i in self.root_indices]

    def shape_for_mesh(self, mesh_index):
        return [s for s in self.shapes if s.mesh_index == mesh_index]


_Z_UP_MATRIX = om.MMatrix([1.0, 0.0, 0.0, 0.0,
                           0.0, 0.0, 1.0, 0.0,
                           0.0, -1.0, 0.0, 0.0,
                           0.0, 0.0, 0.0, 1.0])  # +90 degrees about X: glTF +Y -> Maya +Z


class SceneBuilder(object):
    def __init__(self, prepared, reporter, shader_type=None, set_time_range=False):
        self.prepared = prepared
        self.reporter = reporter
        self.shader_type = shader_type
        self.set_time_range = set_time_range
        self.created_roots = []
        self.created = []      # MObjectHandle of every node created during build()
        self.transforms = {}
        self.shapes = []
        self.materials = None  # shading.MaterialBuilder
        self.curves = {}       # AnimationPlan curve index -> MObjectHandle
        self.time_range = None
        self.timings = {}

    def _on_node_added(self, obj, client_data=None):
        self.created.append(om.MObjectHandle(obj))

    def build(self):
        cb = om.MDGMessage.addNodeAddedCallback(self._on_node_added, "dependNode")
        try:
            self._build()
        finally:
            om.MMessage.removeCallback(cb)

    def _build(self):
        plan = self.prepared.plan
        t = time.time()
        z_up = cmds.upAxis(q=True, axis=True).lower() == "z"
        for i in plan.order:
            self._create_transform(plan.nodes[i], z_up)
        for shape_plan in plan.shapes:
            self._create_shape(shape_plan)
        self.timings["build_geometry"] = time.time() - t
        t = time.time()
        mplan = self.prepared.materials
        if mplan is not None and self.shader_type is not None and mplan.materials:
            self.materials = shading.MaterialBuilder(mplan, self.shader_type, plan.unit_scale, self.reporter)
            self.materials.build()
            self.materials.assign(self.shapes)
            summary = dropped_summary(mplan.dropped)
            if summary:
                self.reporter.warning(summary)
        else:
            self._assign_default_shading()
        self.timings["build_materials"] = time.time() - t
        t = time.time()
        self._build_deformers()
        self.timings["build_deformers"] = time.time() - t
        t = time.time()
        self._build_animation()
        self.timings["build_animation"] = time.time() - t

    def _create_transform(self, node, z_up):
        parent = self.transforms[node.parent].object() if node.parent is not None else om.MObject.kNullObj
        if node.joint:
            obj = om.MFnDagNode().create("joint", parent=parent)
            fn = om.MFnTransform(obj)
            # glTF scale semantics: a child does not compensate its parent's scale
            fn.findPlug("segmentScaleCompensate", False).setBool(False)
            if node.joint_radius is not None:
                fn.findPlug("radius", False).setDouble(node.joint_radius)
        else:
            fn = om.MFnTransform()
            obj = fn.create(parent)
        handle = om.MObjectHandle(obj)
        if node.parent is None:
            self.created_roots.append(handle)
        fn.setName(node.name)
        self.transforms[node.index] = handle
        if node.skinned_leaf:
            # glTF ignores the transform of a skinned mesh node: identity and not inherited,
            # so the skinCluster output (world positions) is where the mesh shows.
            fn.findPlug("inheritsTransform", False).setBool(False)
            return
        if node.matrix is not None:
            fn.setTransformation(om.MTransformationMatrix(om.MMatrix(node.matrix)))
        else:
            t = node.translation
            r = node.rotation
            s = node.scale
            if t != [0.0, 0.0, 0.0]:
                fn.setTranslation(om.MVector(t[0], t[1], t[2]), om.MSpace.kTransform)
            if r != [0.0, 0.0, 0.0, 1.0]:
                q = om.MQuaternion(r[0], r[1], r[2], r[3])
                length = math.sqrt(sum(x * x for x in r))
                if length > 0.0 and abs(length - 1.0) > 1e-6:
                    q = om.MQuaternion(r[0] / length, r[1] / length, r[2] / length, r[3] / length)
                fn.setRotation(q, om.MSpace.kTransform)
            if s != [1.0, 1.0, 1.0]:
                fn.setScale(s)
        if node.parent is None and z_up:
            plug = fn.findPlug("offsetParentMatrix", False)
            plug.setMObject(om.MFnMatrixData().create(_Z_UP_MATRIX))

    def _create_shape(self, shape_plan):
        md = self.prepared.meshes[shape_plan.mesh]
        if md.num_faces == 0:
            self.reporter.warning("meshes[%d] has no triangles to import; no shape created" % shape_plan.mesh)
            return
        owner = shape_plan.nodes[0]
        parent = self.transforms[owner]
        if shape_plan.skin_transform:
            fn = om.MFnTransform()
            obj = fn.create(parent.object())
            fn.setName(shape_plan.skin_transform)
            fn.findPlug("inheritsTransform", False).setBool(False)
            parent = om.MObjectHandle(obj)
        shape = create_mesh_shape(md, parent.object(), shape_plan.name, self.reporter)
        parents = {owner: parent}
        for other in shape_plan.nodes[1:]:
            om.MFnDagNode(self.transforms[other].object()).addChild(shape, om.MFnDagNode.kNextPos, True)
            parents[other] = self.transforms[other]
        self.shapes.append(ShapeResult(shape_plan, md, om.MObjectHandle(shape), parents))

    def _assign_default_shading(self):
        paths = []
        for s in self.shapes:
            paths.extend(s.instance_paths)
        if paths:
            cmds.sets(paths, e=True, forceElement=DEFAULT_SHADING_GROUP)

    def _build_deformers(self):
        plan = self.prepared.plan
        registry = plan.names
        for sr in self.shapes:
            morph = self.prepared.morphs.get(sr.shape_index)
            skin = self.prepared.skins.get(sr.shape_index)
            if morph is None and skin is None:
                continue
            base = plan.nodes[sr.node_indices[0]].name
            path = sr.instance_paths[0]
            if morph is not None:
                morph.name = registry.claim(base + "_blendShape")
                bs, sr.target_aliases = build_blendshape(path, morph, morph.name)
                sr._blend_shape = om.MObjectHandle(_dep(bs))
            if skin is not None:
                skin.name = registry.claim(base + "_skinCluster")
                joints = [_path(self.transforms[j]) for j in skin.joints]
                sc = build_skin(path, skin, joints, skin.name)
                sr._skin_cluster = om.MObjectHandle(_dep(sc))

    def _shape_of_node(self, node):
        for sr in self.shapes:
            if node in sr.node_indices:
                return sr
        return None

    def _resolve_plug(self, curve):
        if curve.attr is None:
            sr = self._shape_of_node(curve.node)
            if sr is None or sr._blend_shape is None:
                return None
            fn = om.MFnDependencyNode(sr._blend_shape.object())
            return fn.findPlug("weight", False).elementByLogicalIndex(curve.target)
        handle = self.transforms.get(curve.node)
        if handle is None:
            return None
        return om.MFnDependencyNode(handle.object()).findPlug(curve.attr, False)

    def _build_animation(self):
        anim = self.prepared.animation
        if anim is None or not anim.curves:
            return
        builder = AnimationBuilder(anim, self._resolve_plug, self.prepared.plan.names, self.reporter)
        self.curves = builder.build(ConnectTarget())
        if self.set_time_range and anim.end > anim.start:
            unit = om.MTime.uiUnit()
            first = math.floor(om.MTime(anim.start, om.MTime.kSeconds).asUnits(unit) + 1e-6)
            last = math.ceil(om.MTime(anim.end, om.MTime.kSeconds).asUnits(unit) - 1e-6)
            apply_time_range(first, last)
            self.time_range = (first, last)

    def cleanup(self):
        """Delete every node created so far: the DAG roots (with everything under them),
        the shading networks, and every other tracked node (deformers, anim curves, ...)."""
        if self.materials is not None:
            self.materials.cleanup()
        alive = [h.object() for h in self.created_roots if h.isValid() and h.isAlive()]
        if alive:
            mod = om.MDagModifier()
            for obj in alive:
                mod.deleteNode(obj)
            mod.doIt()
        rest = [om.MFnDependencyNode(h.object()).absoluteName() for h in self.created
                if h.isValid() and h.isAlive()]
        rest = [n for n in rest if cmds.objExists(n)]
        if rest:
            cmds.delete(rest)


_pending_range = {"range": None, "callbacks": []}


def _apply_pending(*_args):
    rng = _pending_range["range"]
    for cb in _pending_range["callbacks"]:
        try:
            om.MMessage.removeCallback(cb)
        except RuntimeError:  # pragma: no cover
            pass
    _pending_range["callbacks"] = []
    _pending_range["range"] = None
    if rng is not None:
        cmds.playbackOptions(minTime=rng[0], maxTime=rng[1], animationStartTime=rng[0], animationEndTime=rng[1])


def apply_time_range(first, last):
    """Set the playback and animation range. Maya restores the playback range when a file
    import (cmds.file i=True) finishes, so it is set now and once more right after the
    import/open completes (one-shot scene message callbacks)."""
    cmds.playbackOptions(minTime=first, maxTime=last, animationStartTime=first, animationEndTime=last)
    if not om1.MFileIO.isReadingFile():
        return  # called outside cmds.file: nothing will restore the range
    _pending_range["range"] = (first, last)
    if not _pending_range["callbacks"]:
        _pending_range["callbacks"] = [om.MSceneMessage.addCallback(om.MSceneMessage.kAfterImport, _apply_pending),
                                       om.MSceneMessage.addCallback(om.MSceneMessage.kAfterOpen, _apply_pending)]


def _dep(name):
    sel = om.MSelectionList()
    sel.add(name)
    return sel.getDependNode(0)


def scene_fps():
    """Frames per second of the scene time unit."""
    return om.MTime(1.0, om.MTime.kSeconds).asUnits(om.MTime.uiUnit())


def import_file(path, options_string="", sink=maya_sink, access="import"):
    """Import a .gltf/.glb into the current scene. Returns ImportResult.
    Raises core.GltfError (or another exception) on failure after cleaning up.

    ``access``: "import", "open" or "reference" (from the translator). References never
    change the playback range: they re-import on every scene open, and the scene already
    stores the range the user chose."""
    global _last_result, _last_error, _last_attempt
    _last_error = None
    _last_attempt = None
    reporter = Reporter(sink)
    t0 = time.time()
    try:
        options = parse_options(options_string, reporter)
        shader_type = None
        texture_root = None
        if options.shader != "none":
            shader_type = shading.resolve_shader_type(options.shader, reporter)
            texture_root = (shading.expand_project_path(options.textureDir) if options.textureDir
                            else shading.default_texture_root())
        prepared = prepare_import(path, options, reporter, texture_root=texture_root, fps=scene_fps())
        t1 = time.time()
        builder = SceneBuilder(prepared, reporter, shader_type,
                               set_time_range=options.timeRange and access != "reference")
        try:
            builder.build()
        except Exception:
            try:
                builder.cleanup()
            except Exception as exc:  # pragma: no cover - best effort
                reporter.warning("cleanup after failed import also failed: %s" % exc)
            raise
    except Exception as exc:
        _last_error = (path, str(exc), reporter)
        _last_attempt = (path, None, str(exc), reporter)
        raise
    result = ImportResult(path, options, prepared, reporter)
    result.transforms = builder.transforms
    result.shapes = builder.shapes
    result.shader_type = shader_type
    result._curves = builder.curves
    result.time_range = builder.time_range
    if builder.materials is not None:
        mb = builder.materials
        result._shaders = mb.handle_map(mb.shaders)
        result._shading_groups = mb.handle_map(mb.shading_groups)
        result._file_nodes = mb.handle_map(mb.files)
    result.timings = {"prepare": t1 - t0, "build": time.time() - t1}
    result.timings.update(builder.timings)
    _last_result = result
    _last_attempt = (path, result, None, reporter)
    return result


_STATUS = {img.OK: "linked", img.MISSING: "missing", img.UNSUPPORTED: "unsupported", img.FAILED: "failed"}


def last_report():
    """Plain, JSON-serialisable description of the most recent import attempt in this
    session (success or failure), or None. See CONTRACT.md section 4. Node names are
    resolved now, so they carry the final namespace (Maya renames the temporary
    namespace after the translator returns)."""
    if _last_attempt is None:
        return None
    from .. import VERSION
    path, result, error, reporter = _last_attempt
    report = {
        "ok": result is not None, "path": os.path.abspath(path), "version": VERSION, "error": error,
        "roots": [], "shaderType": None, "materials": [], "textures": [], "animation": None,
        "skins": 0, "blendShapes": 0, "warnings": [str(w) for w in reporter.warnings],
    }
    if result is None:
        return report
    report["roots"] = [p for p in result.root_paths if p]
    report["shaderType"] = result.shader_type
    doc = result.prepared.doc
    plan = result.material_plan
    if plan is not None:
        shaders = result.shaders
        groups = result.shading_groups
        for i in sorted(shaders):
            report["materials"].append({"name": doc.materials[i].name or "material_%d" % i,
                                        "shader": shaders[i], "shadingGroup": groups.get(i)})
        files = result.file_nodes
        for t in plan.textures:
            im = plan.images.get(t.image)
            report["textures"].append({
                "image": doc.images[t.image].name or "image_%d" % t.image,
                "node": files.get(t.index),
                "path": im.path if im is not None else t.path,
                "embedded": bool(im.embedded) if im is not None else False,
                "status": _STATUS.get(im.status if im is not None else t.status, "failed"),
            })
    anim = result.animation
    if anim is not None and anim.curves:
        report["animation"] = {"name": anim.name, "index": anim.index, "start": anim.start, "end": anim.end,
                               "curves": len(result.anim_curves)}
    report["skins"] = len(result.skin_clusters)
    report["blendShapes"] = len(result.blend_shapes)
    return report
