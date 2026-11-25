"""Build a prepared glTF import into the Maya scene.

Entry points:
  * ``import_file(path, options_string)`` -- used by the translator's reader().
  * ``last_result()`` -- the ImportResult of the most recent import in this session.

Everything that can fail on bad input runs in ``core.prepare_import`` before any node is
created. If building still fails, every node created so far is deleted and the error
is re-raised, so ``cmds.file`` fails without leaving a half-built hierarchy.

Nodes are created with API 2.0 inside the translator call; Maya puts them in the
requested namespace (the translator reports haveNamespaceSupport) and, for
``reference=True``, marks them as owned by the reference.
"""

import math
import time

import maya.api.OpenMaya as om
import maya.cmds as cmds

from ..core import Reporter, parse_options, prepare_import
from ..core import images as img
from ..core.materials import dropped_summary
from . import shading
from .mesh_builder import create_mesh_shape
from .report import maya_sink

DEFAULT_SHADING_GROUP = shading.DEFAULT_SHADING_GROUP

_last_result = None
_last_error = None


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

    def __init__(self, shape_plan, mesh_data, handle):
        self.shape_index = shape_plan.index
        self.mesh_index = shape_plan.mesh
        self.node_indices = list(shape_plan.nodes)
        self.mesh_data = mesh_data
        self.primitives = list(mesh_data.primitives)
        self._handle = handle

    @property
    def mobject(self):
        return self._handle.object()

    @property
    def name(self):
        return om.MFnDependencyNode(self._handle.object()).name()

    @property
    def instance_paths(self):
        return [p.fullPathName() for p in om.MDagPath.getAllPathsTo(self._handle.object())]

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
    def __init__(self, prepared, reporter, shader_type=None):
        self.prepared = prepared
        self.reporter = reporter
        self.shader_type = shader_type
        self.created_roots = []
        self.transforms = {}
        self.shapes = []
        self.materials = None  # shading.MaterialBuilder

    def build(self):
        plan = self.prepared.plan
        z_up = cmds.upAxis(q=True, axis=True).lower() == "z"
        for i in plan.order:
            self._create_transform(plan.nodes[i], z_up)
        for shape_plan in plan.shapes:
            self._create_shape(shape_plan)
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

    def _create_transform(self, node, z_up):
        parent = self.transforms[node.parent].object() if node.parent is not None else om.MObject.kNullObj
        fn = om.MFnTransform()
        obj = fn.create(parent)
        handle = om.MObjectHandle(obj)
        if node.parent is None:
            self.created_roots.append(handle)
        fn.setName(node.name)
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
        self.transforms[node.index] = handle

    def _create_shape(self, shape_plan):
        md = self.prepared.meshes[shape_plan.mesh]
        if md.num_faces == 0:
            self.reporter.warning("meshes[%d] has no triangles to import; no shape created" % shape_plan.mesh)
            return
        owner = shape_plan.nodes[0]
        shape = create_mesh_shape(md, self.transforms[owner].object(), shape_plan.name, self.reporter)
        for other in shape_plan.nodes[1:]:
            om.MFnDagNode(self.transforms[other].object()).addChild(shape, om.MFnDagNode.kNextPos, True)
        self.shapes.append(ShapeResult(shape_plan, md, om.MObjectHandle(shape)))

    def _assign_default_shading(self):
        paths = []
        for s in self.shapes:
            paths.extend(s.instance_paths)
        if paths:
            cmds.sets(paths, e=True, forceElement=DEFAULT_SHADING_GROUP)

    def cleanup(self):
        """Delete every node created so far (DAG nodes live under the created roots)."""
        if self.materials is not None:
            self.materials.cleanup()
        alive = [h.object() for h in self.created_roots if h.isValid() and h.isAlive()]
        if not alive:
            return
        mod = om.MDagModifier()
        for obj in alive:
            mod.deleteNode(obj)
        mod.doIt()


def import_file(path, options_string="", sink=maya_sink):
    """Import a .gltf/.glb into the current scene. Returns ImportResult.
    Raises core.GltfError (or another exception) on failure after cleaning up."""
    global _last_result, _last_error
    _last_error = None
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
        prepared = prepare_import(path, options, reporter, texture_root=texture_root)
        t1 = time.time()
        builder = SceneBuilder(prepared, reporter, shader_type)
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
        raise
    result = ImportResult(path, options, prepared, reporter)
    result.transforms = builder.transforms
    result.shapes = builder.shapes
    result.shader_type = shader_type
    if builder.materials is not None:
        mb = builder.materials
        result._shaders = mb.handle_map(mb.shaders)
        result._shading_groups = mb.handle_map(mb.shading_groups)
        result._file_nodes = mb.handle_map(mb.files)
    result.timings = {"prepare": t1 - t0, "build": time.time() - t1}
    _last_result = result
    return result
