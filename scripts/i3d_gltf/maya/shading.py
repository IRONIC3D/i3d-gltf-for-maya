"""Build Maya shading networks from a core.materials.MaterialPlan and assign them.

Shader type choice (``shader`` option) and fallback, always decided by which node types
exist in this session, never by Maya version:

    aiStandardSurface -> standardSurface
    aiOpenPBRSurface  -> openPBRSurface -> standardSurface
    openPBRSurface    -> aiOpenPBRSurface -> standardSurface
    standardSurface   (always available)

When an Arnold type is needed and missing, the ``mtoa`` plug-in is loaded first if it is
installed. A fallback is reported with one warning.

Networks:
  * one shader + shading group per glTF material, shared by every mesh using it;
  * one file node + place2dTexture per distinct (image, colour space, wrap, transform,
    UV set), shared between materials; factors become per-material multiplyDivide nodes;
  * normal maps go through bump2d (tangent-space normals), which both Viewport 2.0 and
    Arnold understand;
  * alphaMode MASK uses a condition node (alpha >= cutoff).
"""

import math
import os
from collections import defaultdict

import maya.api.OpenMaya as om
import maya.cmds as cmds

from ..core import images as img
from ..core.materials import COLOR_SLOTS, MIRRORED_REPEAT, RAW, REPEAT, SRGB, NormalInput, TexInput

DEFAULT_SHADING_GROUP = "initialShadingGroup"
ARNOLD_PLUGIN = "mtoa"
ARNOLD_TYPES = frozenset(["aiStandardSurface", "aiOpenPBRSurface"])

FALLBACK_CHAINS = {
    "aiStandardSurface": ("aiStandardSurface", "standardSurface"),
    "aiOpenPBRSurface": ("aiOpenPBRSurface", "openPBRSurface", "standardSurface"),
    "openPBRSurface": ("openPBRSurface", "aiOpenPBRSurface", "standardSurface"),
    "standardSurface": ("standardSurface",),
}

_STANDARD = {
    "base_weight": "base", "base_color": "baseColor", "metalness": "metalness",
    "roughness": "specularRoughness", "specular_weight": "specular", "specular_color": "specularColor",
    "ior": "specularIOR", "transmission": "transmission", "transmission_color": "transmissionColor",
    "transmission_depth": "transmissionDepth", "thin_walled": "thinWalled", "coat": "coat",
    "coat_roughness": "coatRoughness", "coat_ior": "coatIOR", "coat_normal": "coatNormal", "sheen": "sheen",
    "sheen_color": "sheenColor", "sheen_roughness": "sheenRoughness", "emission_weight": "emission",
    "emission_color": "emissionColor", "opacity": "opacity", "normal": "normalCamera",
}
_OPENPBR = {
    "base_weight": "baseWeight", "base_color": "baseColor", "metalness": "baseMetalness",
    "roughness": "specularRoughness", "specular_weight": "specularWeight", "specular_color": "specularColor",
    "ior": "specularIOR", "transmission": "transmissionWeight", "transmission_color": "transmissionColor",
    "transmission_depth": "transmissionDepth", "thin_walled": "geometryThinWalled", "coat": "coatWeight",
    "coat_roughness": "coatRoughness", "coat_ior": "coatIOR", "coat_normal": "geometryCoatNormal",
    "sheen": "fuzzWeight", "sheen_color": "fuzzColor", "sheen_roughness": "fuzzRoughness",
    "emission_weight": "emissionLuminance", "emission_color": "emissionColor", "opacity": "geometryOpacity",
    "normal": "normalCamera",
}
#: slot -> attribute, per shader node type (attribute names checked on Maya 2024-2027).
SHADER_ATTRS = {
    "standardSurface": _STANDARD,
    "aiStandardSurface": _STANDARD,
    "openPBRSurface": _OPENPBR,
    "aiOpenPBRSurface": _OPENPBR,
}
#: OpenPBR emission is a luminance in nits; Arnold renders 1000 nits like emission weight 1
#: of standard_surface (measured), so glTF emissive strength is multiplied by this.
EMISSION_SCALE = {"openPBRSurface": 1000.0, "aiOpenPBRSurface": 1000.0}

SLOT_FEATURES = {
    "transmission": "KHR_materials_transmission", "transmission_color": "KHR_materials_volume",
    "transmission_depth": "KHR_materials_volume", "thin_walled": "KHR_materials_transmission",
    "coat": "KHR_materials_clearcoat", "coat_roughness": "KHR_materials_clearcoat",
    "coat_normal": "KHR_materials_clearcoat normal", "coat_ior": "KHR_materials_clearcoat",
    "sheen": "KHR_materials_sheen", "sheen_color": "KHR_materials_sheen",
    "sheen_roughness": "KHR_materials_sheen", "ior": "KHR_materials_ior",
    "specular_weight": "KHR_materials_specular", "specular_color": "KHR_materials_specular",
}

# OCIO colour space names, most preferred first. Configs differ (Maya default, ACES, studio).
COLOR_SPACE_CANDIDATES = {
    SRGB: ("sRGB Encoded Rec.709 (sRGB)", "sRGB", "sRGB - Texture", "Utility - sRGB - Texture",
           "Input - Generic - sRGB - Texture", "srgb_texture", "srgb_tx", "sRGB Texture"),
    RAW: ("Raw", "Utility - Raw", "raw", "Non-Color", "data", "Data"),
}

P2D_ATTRS = ("coverage", "translateFrame", "rotateFrame", "mirrorU", "mirrorV", "stagger", "wrapU", "wrapV",
             "repeatUV", "offset", "rotateUV", "noiseUV", "vertexUvOne", "vertexUvTwo", "vertexUvThree",
             "vertexCameraOne")


# Node type availability (replaceable by tests) --------------------------------------

def _default_node_type_exists(type_name):
    return type_name in (cmds.allNodeTypes() or [])


def _default_load_arnold():
    try:
        if not cmds.pluginInfo(ARNOLD_PLUGIN, q=True, loaded=True):
            cmds.loadPlugin(ARNOLD_PLUGIN, quiet=True)
        return True
    except RuntimeError:
        return False


def _default_arnold_loaded():
    try:
        return bool(cmds.pluginInfo(ARNOLD_PLUGIN, q=True, loaded=True))
    except RuntimeError:
        return False


def _default_arnold_installed():
    """True when the mtoa plug-in file is on MAYA_PLUG_IN_PATH. Only looks at files: never
    loads the plug-in."""
    import maya.mel as mel
    names = ("mtoa.mll", "mtoa.so", "mtoa.bundle")
    for folder in (mel.eval('getenv "MAYA_PLUG_IN_PATH"') or "").split(os.pathsep):
        if folder and any(os.path.isfile(os.path.join(folder, n)) for n in names):
            return True
    return False


#: Hooks: tests replace these to simulate a session without Arnold / without openPBRSurface.
node_type_exists = _default_node_type_exists
load_arnold = _default_load_arnold
arnold_loaded = _default_arnold_loaded
arnold_installed = _default_arnold_installed

#: shader_availability() statuses
AVAILABLE = "available"            # the node type exists in this session
ARNOLD_ON_IMPORT = "arnoldOnImport"  # Arnold type; mtoa is installed but not loaded (loaded by the import)
UNAVAILABLE = "unavailable"        # the import falls back to ``fallback``


def shader_availability(choices, exists=None, arnold_is_loaded=None, arnold_is_installed=None):
    """For each ``shader`` option value: (value, status, fallback). Used by the options
    window to mark types this session cannot build. Never loads a plug-in: an Arnold type
    with mtoa installed but not loaded is ARNOLD_ON_IMPORT (the import loads mtoa; if that
    mtoa has no such node the import still falls back, with a warning)."""
    exists = exists or node_type_exists
    loaded = arnold_loaded() if arnold_is_loaded is None else arnold_is_loaded
    installed = None  # looked up lazily: only needed when mtoa is not loaded

    def state(t):
        nonlocal installed
        if exists(t):
            return AVAILABLE
        if t in ARNOLD_TYPES and not loaded:
            if installed is None:
                installed = arnold_installed() if arnold_is_installed is None else arnold_is_installed
            if installed:
                return ARNOLD_ON_IMPORT
        return UNAVAILABLE

    out = []
    for value in choices:
        if value not in FALLBACK_CHAINS:  # "none"
            out.append((value, AVAILABLE, None))
            continue
        status = state(value)
        fallback = None
        if status == UNAVAILABLE:
            fallback = next((t for t in FALLBACK_CHAINS[value][1:] if state(t) != UNAVAILABLE), None)
        out.append((value, status, fallback))
    return out


def resolve_shader_type(requested, reporter):
    """The node type to build for ``requested`` (an option value other than 'none')."""
    chain = FALLBACK_CHAINS[requested]
    tried_arnold = False
    chosen = None
    for t in chain:
        if not node_type_exists(t) and t in ARNOLD_TYPES and not tried_arnold:
            tried_arnold = True
            load_arnold()
        if node_type_exists(t):
            chosen = t
            break
    if chosen is None:  # standardSurface is part of Maya; reaching this means a broken session
        raise RuntimeError("No usable shader node type (tried %s)" % ", ".join(chain))
    if chosen != requested:
        why = ("the Arnold plug-in (mtoa) is not available" if requested in ARNOLD_TYPES
               else "this Maya has no %s node" % requested)
        reporter.warning("Shader '%s' is not available (%s); using '%s' instead" % (requested, why, chosen))
    return chosen


def pick_color_space(role, available):
    """Name of the OCIO colour space for SRGB or RAW in ``available``, or None."""
    if not available:
        return None
    for name in COLOR_SPACE_CANDIDATES[role]:
        if name in available:
            return name
    lower = dict((a.lower(), a) for a in available)
    for name in COLOR_SPACE_CANDIDATES[role]:
        if name.lower() in lower:
            return lower[name.lower()]
    for a in available:
        low = a.lower()
        if role == RAW and ("raw" in low or "non-color" in low):
            return a
        if role == SRGB and "srgb" in low and "linear" not in low and "ap1" not in low and "p3" not in low:
            return a
    return None


def available_color_spaces():
    try:
        return list(cmds.colorManagementPrefs(q=True, inputSpaceNames=True) or [])
    except RuntimeError:
        return []


def default_texture_root():
    """<current project>/<sourceImages rule>/i3dGltf, or None if there is no project."""
    try:
        rule = cmds.workspace(fileRuleEntry="sourceImages") or "sourceimages"
        base = cmds.workspace(expandName=rule)
    except RuntimeError:
        return None
    if not base:
        return None
    return os.path.normpath(os.path.join(base, "i3dGltf"))


def expand_project_path(path):
    """Resolve a path relative to the current project (like Maya's own file rules)."""
    if not path or os.path.isabs(path):
        return path
    try:
        return os.path.normpath(cmds.workspace(expandName=path))
    except RuntimeError:
        return os.path.abspath(path)


# Building ---------------------------------------------------------------------------------

def _plug(node, attr):
    sel = om.MSelectionList()
    sel.add(node)
    return om.MFnDependencyNode(sel.getDependNode(0)).findPlug(attr, False)


class MaterialBuilder(object):
    def __init__(self, plan, shader_type, unit_scale, reporter):
        self.plan = plan
        self.shader_type = shader_type
        self.attrs = SHADER_ATTRS[shader_type]
        self.unit_scale = unit_scale
        self.reporter = reporter
        self.registry = plan.registry
        self.created = []           # MObjectHandles of every node created here
        self.handles = {}           # node name at creation -> MObjectHandle
        self.files = {}             # TextureSpec index -> file node name
        self.places = {}            # TextureSpec index -> place2dTexture name
        self.shaders = {}           # material index -> shader node name
        self.shading_groups = {}    # material index -> shading group name
        self.material_textures = defaultdict(set)  # material index -> TextureSpec indices
        self._spaces = None
        self._cs_warned = set()

    # helpers
    def _track(self, name):
        sel = om.MSelectionList()
        sel.add(name)
        handle = om.MObjectHandle(sel.getDependNode(0))
        self.created.append(handle)
        self.handles[name] = handle
        return name

    def handle_map(self, names):
        """{key: MObjectHandle} for a {key: node name} dict built here. Names are only valid
        while the translator runs (Maya renames the temporary import namespace afterwards)."""
        return dict((k, self.handles[v]) for k, v in names.items())

    def _node(self, node_type, name, **kw):
        return self._track(cmds.shadingNode(node_type, name=name, **kw))

    def _util(self, node_type, material, suffix):
        return self._node(node_type, self.registry.claim("%s_%s" % (material.name, suffix)), asUtility=True)

    def _color_space(self, role):
        if self._spaces is None:
            self._spaces = available_color_spaces()
        name = pick_color_space(role, self._spaces)
        if name is None and role not in self._cs_warned:
            self._cs_warned.add(role)
            self.reporter.warning("No %s colour space found in the OCIO config; file nodes keep their default"
                                  % ("sRGB texture" if role == SRGB else "Raw/data"))
        return name

    # textures
    def build_textures(self):
        for t in self.plan.textures:
            if t.status not in (img.OK, img.MISSING):
                continue
            f = self._node("file", t.name, asTexture=True, isColorManaged=True)
            p = self._node("place2dTexture", t.place_name, asUtility=True)
            for a in P2D_ATTRS:
                cmds.connectAttr("%s.%s" % (p, a), "%s.%s" % (f, a), force=True)
            cmds.connectAttr(p + ".outUV", f + ".uvCoord", force=True)
            cmds.connectAttr(p + ".outUvFilterSize", f + ".uvFilterSize", force=True)
            cmds.setAttr(f + ".fileTextureName", t.path.replace("\\", "/"), type="string")
            cs = self._color_space(t.color_space)
            if cs:
                cmds.setAttr(f + ".ignoreColorSpaceFileRules", 1)
                cmds.setAttr(f + ".colorSpace", cs, type="string")
            for axis, wrap in (("U", t.wrap_u), ("V", t.wrap_v)):
                cmds.setAttr("%s.wrap%s" % (p, axis), 1 if wrap in (REPEAT, MIRRORED_REPEAT) else 0)
                cmds.setAttr("%s.mirror%s" % (p, axis), 1 if wrap == MIRRORED_REPEAT else 0)
            if t.repeat_uv != (1.0, 1.0):
                cmds.setAttr(p + ".repeatUV", *t.repeat_uv)
            if t.offset_uv != (0.0, 0.0):
                cmds.setAttr(p + ".offset", *t.offset_uv)
            if t.rotate_uv:
                _plug(p, "rotateUV").setMAngle(om.MAngle(t.rotate_uv, om.MAngle.kRadians))
            self.files[t.index] = f
            self.places[t.index] = p

    def _source(self, material, ti, slot):
        """Plug carrying a TexInput's value; returns (plug, is_color)."""
        f = self.files[ti.texture]
        self.material_textures[material.index].add(ti.texture)
        if ti.channel == "rgb":
            src = f + ".outColor"
            if ti.factor is not None and tuple(ti.factor) != (1.0, 1.0, 1.0):
                md = self._util("multiplyDivide", material, slot + "_mul")
                cmds.connectAttr(src, md + ".input1", force=True)
                cmds.setAttr(md + ".input2", *ti.factor, type="double3")
                src = md + ".output"
            return src, True
        src = f + {"r": ".outColorR", "g": ".outColorG", "b": ".outColorB", "a": ".outAlpha"}[ti.channel]
        if ti.factor is not None and float(ti.factor) != 1.0:
            md = self._util("multiplyDivide", material, slot + "_mul")
            cmds.connectAttr(src, md + ".input1X", force=True)
            cmds.setAttr(md + ".input2X", float(ti.factor))
            src = md + ".outputX"
        if ti.invert:
            rv = self._util("reverse", material, slot + "_inv")
            cmds.connectAttr(src, rv + ".inputX", force=True)
            src = rv + ".outputX"
        return src, False

    def _connect(self, src, is_color, node, attr):
        dst = "%s.%s" % (node, attr)
        children = cmds.attributeQuery(attr, node=node, listChildren=True) or []
        if is_color or not children:
            cmds.connectAttr(src, dst, force=True)
        else:
            for c in children:
                cmds.connectAttr(src, "%s.%s" % (node, c), force=True)

    def _set(self, node, attr, value, slot):
        plug = "%s.%s" % (node, attr)
        if isinstance(value, bool):
            cmds.setAttr(plug, value)
        elif isinstance(value, tuple):
            cmds.setAttr(plug, *value, type="double3")
        else:
            children = cmds.attributeQuery(attr, node=node, listChildren=True) or []
            if children:  # scalar onto a colour (opacity of standard surface)
                cmds.setAttr(plug, value, value, value, type="double3")
            elif slot == "transmission_depth":
                p = _plug(node, attr)
                if p.attribute().apiType() == om.MFn.kDoubleLinearAttribute:
                    p.setMDistance(om.MDistance(value, om.MDistance.kCentimeters))
                else:
                    p.setDouble(value)
            else:
                cmds.setAttr(plug, value)

    def _attr(self, shader, slot):
        attr = self.attrs.get(slot)
        if attr is None or not cmds.attributeQuery(attr, node=shader, exists=True):
            return None
        return attr

    def build_material(self, spec):
        shader = self._node(self.shader_type, spec.name, asShader=True)
        sg = self._track(cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=spec.sg_name))
        cmds.connectAttr(shader + ".outColor", sg + ".surfaceShader", force=True)
        self.shaders[spec.index] = shader
        self.shading_groups[spec.index] = sg

        inputs = dict(spec.inputs)
        if "transmission_depth_m" in inputs:
            inputs["transmission_depth"] = inputs.pop("transmission_depth_m") * self.unit_scale
        scale = EMISSION_SCALE.get(self.shader_type, 1.0)
        if "emission_weight" in inputs:
            inputs["emission_weight"] = inputs["emission_weight"] * scale
        for slot in sorted(inputs):
            value = inputs[slot]
            if slot == "opacity":
                continue
            attr = self._attr(shader, slot)
            if attr is None:
                self.plan.dropped["%s (no %s slot on %s)" % (SLOT_FEATURES.get(slot, slot), slot,
                                                            self.shader_type)] += 1
                continue
            if isinstance(value, NormalInput):
                bump = self._util("bump2d", spec, slot + "_bump")
                f = self.files[value.texture]
                self.material_textures[spec.index].add(value.texture)
                cmds.connectAttr(f + ".outAlpha", bump + ".bumpValue", force=True)
                cmds.setAttr(bump + ".bumpInterp", 1)  # tangent space normals
                cmds.setAttr(bump + ".bumpDepth", value.scale)
                cmds.connectAttr(bump + ".outNormal", "%s.%s" % (shader, attr), force=True)
            elif isinstance(value, TexInput):
                src, is_color = self._source(spec, value, slot)
                self._connect(src, is_color, shader, attr)
            else:
                if slot in COLOR_SLOTS and not isinstance(value, tuple):
                    value = (value, value, value)
                self._set(shader, attr, value, slot)
        self._build_opacity(spec, shader, inputs.get("opacity"))
        return sg

    def _build_opacity(self, spec, shader, value):
        if value is None or spec.alpha_mode == "OPAQUE":
            return
        attr = self._attr(shader, "opacity")
        if attr is None:
            self.plan.dropped["alphaMode %s (no opacity on %s)" % (spec.alpha_mode, self.shader_type)] += 1
            return
        if not isinstance(value, TexInput):
            if value != 1.0:
                self._set(shader, attr, float(value), "opacity")
            return
        src, _ = self._source(spec, value, "alpha")
        if spec.alpha_mode == "MASK":
            cond = self._util("condition", spec, "alpha_cutoff")
            cmds.connectAttr(src, cond + ".firstTerm", force=True)
            cmds.setAttr(cond + ".secondTerm", spec.alpha_cutoff)
            cmds.setAttr(cond + ".operation", 3)  # Greater or Equal
            cmds.setAttr(cond + ".colorIfTrue", 1, 1, 1, type="double3")
            cmds.setAttr(cond + ".colorIfFalse", 0, 0, 0, type="double3")
            src = cond + ".outColorR"
        self._connect(src, False, shader, attr)

    def build(self):
        self.build_textures()
        for i in sorted(self.plan.materials):
            self.build_material(self.plan.materials[i])

    # assignment
    def assign(self, shapes):
        """Per-face assignment on every instance, shape flags, UV set links."""
        missing_uv = defaultdict(set)
        for shape in shapes:
            by_sg = defaultdict(list)
            mats = set()
            for (_, mat, start, count) in shape.face_ranges():
                if count <= 0:
                    continue
                sg = self.shading_groups.get(mat, DEFAULT_SHADING_GROUP)
                if mat in self.shading_groups:
                    mats.add(mat)
                ranges = by_sg[sg]
                if ranges and ranges[-1][1] == start:
                    ranges[-1][1] = start + count
                else:
                    ranges.append([start, start + count])
            paths = shape.instance_paths
            if len(by_sg) == 1:
                cmds.sets(paths, e=True, forceElement=list(by_sg)[0])
            else:
                for path in paths:
                    for sg in sorted(by_sg):
                        comps = ["%s.f[%d:%d]" % (path, a, b - 1) for a, b in by_sg[sg]]
                        cmds.sets(comps, e=True, forceElement=sg)
            self._shape_flags(shape, mats)
            self._link_uv_sets(shape, mats, missing_uv)
        for (texcoord, file_node), shapes_missing in sorted(missing_uv.items()):
            self.reporter.warning("%s uses TEXCOORD_%d, which %s do not have; Maya uses the default UV set there"
                                  % (file_node, texcoord, ", ".join(sorted(shapes_missing))))

    def _shape_flags(self, shape, mats):
        specs = [self.plan.materials[m] for m in mats]
        if not specs:
            return
        node = shape.instance_paths[0]
        # Double-sided if any material on the shape is (Maya's flag is per shape).
        if not any(s.double_sided for s in specs):
            cmds.setAttr(node + ".doubleSided", False)
        if any(s.transparent for s in specs) and cmds.attributeQuery("aiOpaque", node=node, exists=True):
            cmds.setAttr(node + ".aiOpaque", 0)

    def _link_uv_sets(self, shape, mats, missing):
        textures = set()
        for m in mats:
            textures |= self.material_textures.get(m, set())
        need = [self.plan.textures[t] for t in sorted(textures) if self.plan.textures[t].texcoord > 0]
        if not need:
            return
        node = shape.instance_paths[0]
        names = cmds.polyUVSet(node, q=True, allUVSets=True) or []
        indices = cmds.polyUVSet(node, q=True, allUVSetsIndices=True) or []
        index_of = dict(zip(names, indices))
        for t in need:
            set_name = "map%d" % (t.texcoord + 1)
            if set_name not in index_of:
                missing[(t.texcoord, self.files[t.index])].add(shape.name)
                continue
            cmds.uvLink(make=True, uvSet="%s.uvSet[%d].uvSetName" % (node, index_of[set_name]),
                        texture=self.files[t.index])
            place = self.places[t.index]
            chooser = cmds.listConnections(place + ".uvCoord", source=True, destination=False, type="uvChooser")
            if chooser and chooser[0] not in self.handles:  # give uvLink's chooser a stable name
                name = cmds.rename(chooser[0], self.registry.claim(t.name + "_uvChooser"))
                self._track(name)

    def cleanup(self):
        alive = [h.object() for h in self.created if h.isValid() and h.isAlive()]
        if alive:
            mod = om.MDGModifier()
            for obj in alive:
                mod.deleteNode(obj)
            mod.doIt()
