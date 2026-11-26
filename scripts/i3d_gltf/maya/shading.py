"""Build Maya shading networks from a core.materials.MaterialPlan and assign them.

Shader type choice (``shader`` option) and fallback, always decided by which node types
exist in this session, never by Maya version:

    aiStandardSurface -> standardSurface
    aiOpenPBRSurface  -> openPBRSurface -> standardSurface
    openPBRSurface    -> aiOpenPBRSurface -> standardSurface
    PxrSurface        -> standardSurface
    dlPrincipled      -> standardSurface
    standardSurface   (always available)

When a renderer's type is needed and missing, that renderer's plug-in (``mtoa``,
``RenderMan_for_Maya``, ``plugin_3delight_for_maya``) is loaded first if it is installed.
A fallback is reported with one warning.

Networks (MaterialBuilder: Maya's own and Arnold's shaders, and 3Delight's dlPrincipled):
  * one shader + shading group per glTF material, shared by every mesh using it;
  * one file node + place2dTexture per distinct (image, colour space, wrap, transform,
    UV set), shared between materials; factors become per-material multiplyDivide nodes;
  * normal maps go through bump2d (tangent-space normals), which both Viewport 2.0 and
    Arnold understand (dlPrincipled: its own normal map input instead);
  * alphaMode MASK uses a condition node (alpha >= cutoff).

RenderMan's PxrSurface (PxrSurfaceBuilder) uses RenderMan's own pattern nodes instead:
PxrTexture (+ PxrManifold2D), PxrMetallicWorkflow, PxrNormalMap, PxrArithmetic and
PxrThreshold; see that class.
"""

import math
import os
from collections import defaultdict

import maya.api.OpenMaya as om
import maya.cmds as cmds

from ..core import images as img
from ..core.materials import (COLOR_SLOTS, MIRRORED_REPEAT, RAW, REPEAT, SRGB, NormalInput, TexInput,
                              has_transform, manifold2d_from_transform)

DEFAULT_SHADING_GROUP = "initialShadingGroup"
ARNOLD_PLUGIN = "mtoa"
RENDERMAN_PLUGIN = "RenderMan_for_Maya"
DELIGHT_PLUGIN = "plugin_3delight_for_maya"
ARNOLD_TYPES = frozenset(["aiStandardSurface", "aiOpenPBRSurface"])

#: shader node type -> the renderer plug-in that provides it (types not listed are Maya's own)
TYPE_PLUGINS = {
    "aiStandardSurface": ARNOLD_PLUGIN, "aiOpenPBRSurface": ARNOLD_PLUGIN,
    "PxrSurface": RENDERMAN_PLUGIN, "dlPrincipled": DELIGHT_PLUGIN,
}
RENDERER_NAMES = {ARNOLD_PLUGIN: "Arnold", RENDERMAN_PLUGIN: "RenderMan", DELIGHT_PLUGIN: "3Delight"}
#: plug-in files looked for on MAYA_PLUG_IN_PATH to tell "installed" without loading anything
PLUGIN_FILES = {
    ARNOLD_PLUGIN: ("mtoa.mll", "mtoa.so", "mtoa.bundle"),
    RENDERMAN_PLUGIN: ("RenderMan_for_Maya.py", "RenderMan_for_Maya.mll", "RenderMan_for_Maya.so",
                       "RenderMan_for_Maya.bundle"),
    DELIGHT_PLUGIN: ("plugin_3delight_for_maya.mll", "plugin_3delight_for_maya.so",
                     "plugin_3delight_for_maya.bundle"),
}

FALLBACK_CHAINS = {
    "aiStandardSurface": ("aiStandardSurface", "standardSurface"),
    "aiOpenPBRSurface": ("aiOpenPBRSurface", "openPBRSurface", "standardSurface"),
    "openPBRSurface": ("openPBRSurface", "aiOpenPBRSurface", "standardSurface"),
    "PxrSurface": ("PxrSurface", "standardSurface"),
    "dlPrincipled": ("dlPrincipled", "standardSurface"),
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
#: 3Delight dlPrincipled (attribute names checked with 3Delight for Maya on Maya 2024-2027).
#: specular_level / coat_specular_level / refract_ior are derived slots (see DlPrincipledBuilder).
_DELIGHT = {
    "base_color": "color", "metalness": "metallic", "roughness": "roughness",
    "specular_level": "specular_level", "transmission": "refract_weight", "transmission_color": "refract_color",
    "refract_ior": "refract_ior", "coat": "coating_thickness", "coat_roughness": "coating_roughness",
    "coat_specular_level": "coating_specular_level", "sheen": "sheen_level", "sheen_color": "sheen_color",
    "sheen_roughness": "sheen_roughness", "emission_weight": "incandescence_intensity",
    "emission_color": "incandescence", "opacity": "opacity", "normal": "disp_normal_bump_value",
}
#: RenderMan PxrSurface: the slots that map one to one (base colour, metalness, specular,
#: coat, normals and opacity are wired by PxrSurfaceBuilder itself).
_PXR = {
    "base_weight": "diffuseGain", "roughness": "specularRoughness", "emission_color": "glowColor",
    "emission_weight": "glowGain", "transmission": "refractionGain", "transmission_color": "refractionColor",
    "thin_walled": "thinGlass", "coat_roughness": "clearcoatRoughness", "sheen": "fuzzGain",
    "sheen_color": "fuzzColor", "opacity": "presence", "normal": "bumpNormal", "coat_normal": "clearcoatBumpNormal",
}
#: slot -> attribute, per shader node type (attribute names checked on Maya 2024-2027).
SHADER_ATTRS = {
    "standardSurface": _STANDARD,
    "aiStandardSurface": _STANDARD,
    "openPBRSurface": _OPENPBR,
    "aiOpenPBRSurface": _OPENPBR,
    "dlPrincipled": _DELIGHT,
    "PxrSurface": _PXR,
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


def _default_load_plugin(plugin):
    """Load a renderer plug-in if it is not loaded. True when it is loaded afterwards (a
    plug-in whose initialisation fails can return from loadPlugin without being loaded)."""
    try:
        if not cmds.pluginInfo(plugin, q=True, loaded=True):
            cmds.loadPlugin(plugin, quiet=True)
        return bool(cmds.pluginInfo(plugin, q=True, loaded=True))
    except RuntimeError:
        return False


def _default_plugin_loaded(plugin):
    try:
        return bool(cmds.pluginInfo(plugin, q=True, loaded=True))
    except RuntimeError:
        return False


def _default_plugin_installed(plugin):
    """True when the plug-in file is on MAYA_PLUG_IN_PATH. Only looks at files: never
    loads the plug-in."""
    import maya.mel as mel
    names = PLUGIN_FILES.get(plugin, (plugin + ".mll", plugin + ".py", plugin + ".so", plugin + ".bundle"))
    for folder in (mel.eval('getenv "MAYA_PLUG_IN_PATH"') or "").split(os.pathsep):
        if folder and any(os.path.isfile(os.path.join(folder, n)) for n in names):
            return True
    return False


#: Hooks: tests replace these to simulate a session without a renderer / without openPBRSurface.
#: ``load_plugin(name)``, ``plugin_loaded(name)`` and ``plugin_installed(name)`` take the plug-in
#: name (ARNOLD_PLUGIN, RENDERMAN_PLUGIN, DELIGHT_PLUGIN).
node_type_exists = _default_node_type_exists
load_plugin = _default_load_plugin
plugin_loaded = _default_plugin_loaded
plugin_installed = _default_plugin_installed

#: shader_availability() statuses
AVAILABLE = "available"            # the node type exists in this session
LOADED_ON_IMPORT = "loadedOnImport"  # renderer type; its plug-in is installed but not loaded (the import loads it)
UNAVAILABLE = "unavailable"        # the import falls back to ``fallback``


def _plugin_query(value, hook):
    """``value`` given to shader_availability for is_loaded / is_installed: None (use the
    hook), a bool for every plug-in, or a callable taking the plug-in name."""
    if value is None:
        return hook
    if callable(value):
        return value
    return lambda _plugin: bool(value)


def shader_availability(choices, exists=None, is_loaded=None, is_installed=None):
    """For each ``shader`` option value: (value, status, fallback). Used by the options
    window to mark types this session cannot build. Never loads a plug-in: a renderer's
    type with its plug-in installed but not loaded is LOADED_ON_IMPORT (the import loads
    the plug-in; if that plug-in has no such node the import still falls back, with a
    warning). ``is_loaded`` / ``is_installed``: None (the hooks), a bool, or a callable
    taking the plug-in name; installation is looked up lazily, once per plug-in."""
    exists = exists or node_type_exists
    loaded_q = _plugin_query(is_loaded, plugin_loaded)
    installed_q = _plugin_query(is_installed, plugin_installed)
    loaded = {}
    installed = {}

    def state(t):
        if exists(t):
            return AVAILABLE
        plugin = TYPE_PLUGINS.get(t)
        if plugin is None:
            return UNAVAILABLE
        if plugin not in loaded:
            loaded[plugin] = loaded_q(plugin)
        if not loaded[plugin]:
            if plugin not in installed:
                installed[plugin] = installed_q(plugin)
            if installed[plugin]:
                return LOADED_ON_IMPORT
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
    tried = set()
    chosen = None
    for t in chain:
        plugin = TYPE_PLUGINS.get(t)
        if not node_type_exists(t) and plugin is not None and plugin not in tried:
            tried.add(plugin)
            load_plugin(plugin)
        if node_type_exists(t):
            chosen = t
            break
    if chosen is None:  # standardSurface is part of Maya; reaching this means a broken session
        raise RuntimeError("No usable shader node type (tried %s)" % ", ".join(chain))
    if chosen != requested:
        plugin = TYPE_PLUGINS.get(requested)
        why = ("the %s plug-in (%s) is not available" % (RENDERER_NAMES[plugin], plugin) if plugin
               else "this Maya has no %s node" % requested)
        reporter.warning("Shader '%s' is not available (%s); using '%s' instead" % (requested, why, chosen))
    return chosen


def builder_class(shader_type):
    """The MaterialBuilder subclass that builds ``shader_type`` networks."""
    if shader_type == "PxrSurface":
        return PxrSurfaceBuilder
    if shader_type == "dlPrincipled":
        return DlPrincipledBuilder
    return MaterialBuilder


def f0_from_ior(ior):
    """Normal-incidence reflectance of a dielectric with index ``ior`` (in air)."""
    if not ior or ior <= 0:
        ior = 1.5
    return ((ior - 1.0) / (ior + 1.0)) ** 2


def principled_specular(ior, weight=1.0):
    """The Disney-style 'specular' value of PxrMetallicWorkflow.specular and 3Delight's
    dlPrincipled.specular_level: F0 = 0.08 * specular (0.5 = F0 0.04 = IOR 1.5; formula from
    PxrMetallicWorkflow.osl as shipped with RenderMan). glTF's KHR_materials_ior sets F0 from
    the IOR and KHR_materials_specular's specularFactor scales it."""
    return f0_from_ior(ior) / 0.08 * weight


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

    def _shader_node(self, spec):
        shader = self._node(self.shader_type, spec.name, asShader=True)
        sg = self._track(cmds.sets(renderable=True, noSurfaceShader=True, empty=True, name=spec.sg_name))
        cmds.connectAttr(shader + ".outColor", sg + ".surfaceShader", force=True)
        self.shaders[spec.index] = shader
        self.shading_groups[spec.index] = sg
        return shader, sg

    def _plug_exists(self, plug):
        """True when ``node.attr`` exists; otherwise it is listed in the "no equivalent" warning
        (a renderer plug-in version without that attribute degrades instead of failing)."""
        node, attr = plug.split(".", 1)
        if cmds.attributeQuery(attr.split("[")[0], node=node, exists=True):
            return True
        self.plan.dropped["%s.%s (not in this %s version)" % (cmds.nodeType(node), attr,
                                                             RENDERER_NAMES.get(TYPE_PLUGINS.get(self.shader_type),
                                                                                "renderer"))] += 1
        return False

    def _set_attr(self, plug, *values, **kw):
        if self._plug_exists(plug):
            cmds.setAttr(plug, *values, **kw)

    def _connect_attr(self, src, dst, **kw):
        if self._plug_exists(src) and self._plug_exists(dst):
            cmds.connectAttr(src, dst, **kw)

    def _drop_slot(self, slot):
        self.plan.dropped["%s (no %s slot on %s)" % (SLOT_FEATURES.get(slot, slot), slot, self.shader_type)] += 1

    def _prepare_inputs(self, spec, inputs):
        """Shader-specific rewrite of the slot values (in place) before they are applied."""

    def _connect_normal(self, spec, slot, value, shader, attr):
        bump = self._util("bump2d", spec, slot + "_bump")
        f = self.files[value.texture]
        self.material_textures[spec.index].add(value.texture)
        cmds.connectAttr(f + ".outAlpha", bump + ".bumpValue", force=True)
        cmds.setAttr(bump + ".bumpInterp", 1)  # tangent space normals
        cmds.setAttr(bump + ".bumpDepth", value.scale)
        cmds.connectAttr(bump + ".outNormal", "%s.%s" % (shader, attr), force=True)

    def build_material(self, spec):
        shader, sg = self._shader_node(spec)

        inputs = dict(spec.inputs)
        if "transmission_depth_m" in inputs:
            inputs["transmission_depth"] = inputs.pop("transmission_depth_m") * self.unit_scale
        scale = EMISSION_SCALE.get(self.shader_type, 1.0)
        if "emission_weight" in inputs:
            inputs["emission_weight"] = inputs["emission_weight"] * scale
        self._prepare_inputs(spec, inputs)
        for slot in sorted(inputs):
            value = inputs[slot]
            if slot == "opacity":
                continue
            attr = self._attr(shader, slot)
            if attr is None:
                self._drop_slot(slot)
                continue
            if isinstance(value, NormalInput):
                self._connect_normal(spec, slot, value, shader, attr)
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


class DlPrincipledBuilder(MaterialBuilder):
    """3Delight's dlPrincipled. Textures stay Maya file + place2dTexture nodes (with the same
    multiplyDivide / reverse / condition utilities as the other shaders): 3Delight for Maya
    renders those Maya nodes itself (it ships OSL versions of file, place2dTexture, uvChooser,
    multiplyDivide, reverse and condition), so UV orientation, KHR_texture_transform, sampler
    wrap / mirror, UV sets and OCIO colour spaces behave exactly as on the Arnold path.

    Per glTF feature: base colour -> color, metallic -> metallic, roughness -> roughness,
    IOR and KHR_materials_specular's factor -> specular_level (F0 = 0.08 x level, as
    PxrMetallicWorkflow), emissive -> incandescence (x incandescence_intensity), alpha ->
    opacity, normal map -> disp_normal_bump_value with type "Normal Map (OpenGL)" and the
    normal scale as intensity, clearcoat -> coating_thickness (white coating colour),
    transmission -> refract_weight / refract_ior, sheen -> sheen_level / sheen_color /
    sheen_roughness. Unlit: base colour black (dlPrincipled has no base weight)."""

    def _prepare_inputs(self, spec, inputs):
        ior = inputs.pop("ior", None)
        weight = inputs.pop("specular_weight", 1.0)
        if isinstance(weight, TexInput):
            self.plan.dropped["KHR_materials_specular specularTexture (no specular level map on dlPrincipled)"] += 1
            weight = weight.constant()
        inputs["specular_level"] = principled_specular(ior or 1.5, weight)
        if "transmission" in inputs:
            inputs["refract_ior"] = ior or 1.5
        coat_ior = inputs.pop("coat_ior", 1.5)
        if "coat" in inputs:
            inputs["coat_specular_level"] = principled_specular(coat_ior)
        if inputs.pop("base_weight", 1.0) == 0.0:  # KHR_materials_unlit
            inputs["base_color"] = (0.0, 0.0, 0.0)

    def build_material(self, spec):
        sg = MaterialBuilder.build_material(self, spec)
        if "coat" in spec.inputs:
            self._set_attr(self.shaders[spec.index] + ".coating_color", 1.0, 1.0, 1.0, type="double3")
        return sg

    def _connect_normal(self, spec, slot, value, shader, attr):
        f = self.files[value.texture]
        self.material_textures[spec.index].add(value.texture)
        self._connect_attr(f + ".outColor", "%s.%s" % (shader, attr), force=True)
        self._set_attr(shader + ".disp_normal_bump_type", 2)  # Normal Map (OpenGL): glTF normal maps are +Y up
        self._set_attr(shader + ".disp_normal_bump_intensity", value.scale)


class PxrSurfaceBuilder(MaterialBuilder):
    """RenderMan for Maya's PxrSurface, wired the way Dayum builds its own RenderMan materials
    (PxrTexture, PxrMetallicWorkflow, PxrNormalMap), so an asset looks the same whichever side
    builds it. Per glTF feature:

      * Textures: one PxrTexture per distinct (image, colour space, sampler, transform, UV
        set), shared between materials like the file nodes, named ``<image>_tex``. filename =
        the image itself (RenderMan for Maya's txmanager makes the .tex it renders from).
        linearize on for colour maps (base colour, emissive, specular and sheen colour), off
        for data maps (metallic-roughness, occlusion, normal, ...). swrap / twrap from the
        sampler (REPEAT periodic, MIRRORED_REPEAT mirror, CLAMP_TO_EDGE clamp) where
        PxrTexture has them (RenderMan 28); older PxrTexture always repeats, with a warning.
      * Every PxrTexture gets a PxrManifold2D (``<image>_manifold``) into its manifold port
        (RenderMan's recommended network: the artist can repeat, offset and rotate there).
        KHR_texture_transform sets its values (core.materials.manifold2d_from_transform),
        invertT is left on (default) so the image is the same way up as with Maya's file
        node, and name_uvSet = ``map<n+1>`` for TEXCOORD_n (n > 0).
      * Metal/roughness: PxrMetallicWorkflow (``<material>_metallicWorkflow``: baseColor,
        metallic, specular from the IOR) into diffuseColor, specularFaceColor and
        specularEdgeColor. specularFresnelMode is set to Artistic because PxrMetallicWorkflow
        produces artistic face/edge colours ("The BxDF MUST use Artistic fresnel", its own
        help text), and specularModelType to GGX, glTF's microfacet model (Dayum sets GGX too).
        The packed texture's G channel (resultG) drives specularRoughness and B (resultB)
        metallic.
      * Factors (base colour, metallic, roughness, emissive, alpha...) are PxrArithmetic
        multiply nodes per material (``<material>_<slot>_mul``); a factor of 1 connects the
        PxrTexture directly, as Dayum does.
      * Normal map: PxrNormalMap (``<material>_normal_normalMap``) fed with the raw
        PxrTexture's resultRGB, orientation OpenGL (glTF normal maps are +Y up; Dayum sets
        the preset explicitly too), bumpScale = normalTexture.scale, into bumpNormal.
      * Emission: glowColor (texture x factor), glowGain = emissive strength.
      * Alpha: presence. BLEND: base colour alpha x factor. MASK: PxrThreshold (alpha >=
        alphaCutoff, no transition). OPAQUE: untouched.
      * doubleSided: the shape's doubleSided as for every shader, plus diffuse, specular,
        clearcoat and fuzz DoubleSided on the shader.
      * Clearcoat: clearcoatFaceColor = factor x F0(1.5), clearcoatEdgeColor = factor (GGX);
        sheen: fuzzGain / fuzzColor; transmission: refractionGain, glassIor, glassRoughness,
        thinGlass, refractionColor; KHR_materials_pbrSpecularGlossiness: diffuseColor and
        specularFaceColor directly, white edge.
    Anything else is listed in the import's one "no equivalent" warning.
    """

    WRAP = {REPEAT: "periodic", MIRRORED_REPEAT: "mirror", 33071: "clamp"}
    DOUBLE_SIDED = ("diffuseDoubleSided", "specularDoubleSided", "clearcoatDoubleSided", "fuzzDoubleSided")

    def build_textures(self):
        no_wrap = []
        for t in self.plan.textures:
            if t.status not in (img.OK, img.MISSING):
                continue
            base = t.base or t.name
            tex = self._node("PxrTexture", self.registry.claim(base + "_tex"), asTexture=True)
            self._set_attr(tex + ".filename", t.path.replace("\\", "/"), type="string")
            self._set_attr(tex + ".linearize", t.color_space == SRGB)
            if cmds.attributeQuery("swrap", node=tex, exists=True):
                self._set_attr(tex + ".swrap", self.WRAP.get(t.wrap_u, "periodic"), type="string")
                self._set_attr(tex + ".twrap", self.WRAP.get(t.wrap_v, "periodic"), type="string")
            elif t.wrap_u != REPEAT or t.wrap_v != REPEAT:
                no_wrap.append(tex)
            # Always a PxrManifold2D, also without a transform: it is RenderMan's recommended
            # network and gives the artist repeat / offset / rotation on every texture.
            m = self._node("PxrManifold2D", self.registry.claim(base + "_manifold"), asUtility=True)
            if has_transform(t):
                ss, st, os_, ot, angle = manifold2d_from_transform(t.gltf_offset, t.gltf_rotation, t.gltf_scale)
                for attr, value in (("scaleS", ss), ("scaleT", st), ("offsetS", os_), ("offsetT", ot),
                                    ("angle", angle)):
                    self._set_attr("%s.%s" % (m, attr), value)
            if t.texcoord > 0:
                self._set_attr(m + ".name_uvSet", "map%d" % (t.texcoord + 1), type="string")
            self._connect_attr(m + ".result", tex + ".manifold", force=True)
            self.places[t.index] = m
            self.files[t.index] = tex
        if no_wrap:
            self.reporter.warning("This RenderMan's PxrTexture has no wrap mode setting; %s repeat%s instead of "
                                  "clamping or mirroring" % (", ".join(no_wrap), "" if len(no_wrap) > 1 else "s"))

    def _source(self, material, ti, slot, scale=1.0):
        """Plug carrying a TexInput's value (x scale); returns (plug, is_color)."""
        tex = self.files[ti.texture]
        self.material_textures[material.index].add(ti.texture)
        if ti.channel == "rgb":
            src = tex + ".resultRGB"
            factor = tuple(ti.factor) if ti.factor is not None else (1.0, 1.0, 1.0)
            factor = tuple(float(f) * scale for f in factor)
            if factor != (1.0, 1.0, 1.0):
                ar = self._util("PxrArithmetic", material, slot + "_mul")
                self._set_attr(ar + ".operation", 1)  # Multiply
                self._connect_attr(src, ar + ".input1", force=True)
                self._set_attr(ar + ".input2", *factor, type="double3")
                src = ar + ".resultRGB"
            return src, True
        src = tex + {"r": ".resultR", "g": ".resultG", "b": ".resultB", "a": ".resultA"}[ti.channel]
        f = (1.0 if ti.factor is None else float(ti.factor)) * scale
        if ti.invert:  # scale x (1 - factor x value)
            ar = self._util("PxrArithmetic", material, slot + "_inv")
            self._set_attr(ar + ".operation", 204)  # Multiply-Add: input1 * input2 + input3
            self._connect_attr(src, ar + ".input1R", force=True)
            self._set_attr(ar + ".input2", -f, -f, -f, type="double3")
            self._set_attr(ar + ".input3", scale, scale, scale, type="double3")
            src = ar + ".resultR"
        elif f != 1.0:
            ar = self._util("PxrArithmetic", material, slot + "_mul")
            self._set_attr(ar + ".operation", 1)  # Multiply
            self._connect_attr(src, ar + ".input1R", force=True)
            self._set_attr(ar + ".input2", f, f, f, type="double3")
            src = ar + ".resultR"
        return src, False

    def _connect(self, src, is_color, node, attr):
        if self._plug_exists("%s.%s" % (node, attr)):
            MaterialBuilder._connect(self, src, is_color, node, attr)

    def _set(self, node, attr, value, slot):
        if self._plug_exists("%s.%s" % (node, attr)):
            MaterialBuilder._set(self, node, attr, value, slot)

    def _apply(self, spec, node, attr, value, slot, scale=1.0):
        """Connect (TexInput) or set (constant) ``node.attr`` to ``value`` x ``scale``."""
        if isinstance(value, TexInput):
            src, is_color = self._source(spec, value, slot, scale)
            self._connect(src, is_color, node, attr)
            return
        if scale != 1.0 and not isinstance(value, bool):
            value = tuple(v * scale for v in value) if isinstance(value, tuple) else value * scale
        self._set(node, attr, value, slot)

    def _connect_normal(self, spec, slot, value, shader, attr):
        nm = self._util("PxrNormalMap", spec, slot + "_normalMap")
        tex = self.files[value.texture]
        self.material_textures[spec.index].add(value.texture)
        self._connect_attr(tex + ".resultRGB", nm + ".inputRGB", force=True)
        self._set_attr(nm + ".orientation", 0)  # OpenGL: glTF tangent-space normal maps are +Y up
        self._set_attr(nm + ".bumpScale", value.scale)
        self._connect_attr(nm + ".resultN", "%s.%s" % (shader, attr), force=True)

    def build_material(self, spec):
        shader, sg = self._shader_node(spec)
        # Metal/roughness through PxrMetallicWorkflow needs Artistic fresnel; GGX is glTF's model.
        self._set_attr(shader + ".specularFresnelMode", 0)  # Artistic
        self._set_attr(shader + ".specularModelType", 1)    # GGX
        inputs = dict(spec.inputs)
        spec_gloss = "KHR_materials_pbrSpecularGlossiness" in spec.converted
        unlit = "KHR_materials_unlit" in spec.converted
        ior = inputs.pop("ior", None)
        weight = inputs.pop("specular_weight", 1.0)
        if isinstance(weight, TexInput):
            self.plan.dropped["KHR_materials_specular specularTexture (no specular level map on PxrSurface)"] += 1
            weight = weight.constant()
        base_color = inputs.pop("base_color", (1.0, 1.0, 1.0))
        metal = inputs.pop("metalness", None)
        spec_color = inputs.pop("specular_color", None)
        if inputs.pop("transmission_depth_m", None) is not None:
            self._drop_slot("transmission_depth")
        if unlit:
            pass  # diffuseGain 0, no specular: only glow (emission) remains
        elif spec_gloss:
            self._apply(spec, shader, "diffuseColor", base_color, "base_color")
            if spec_color is not None:
                self._apply(spec, shader, "specularFaceColor", spec_color, "specular_color")
            self._set_attr(shader + ".specularEdgeColor", 1.0, 1.0, 1.0, type="double3")
        else:
            mw = self._util("PxrMetallicWorkflow", spec, "metallicWorkflow")
            self._set_attr(mw + ".specular", principled_specular(ior or 1.5, weight))
            self._apply(spec, mw, "baseColor", base_color, "base_color")
            self._apply(spec, mw, "metallic", 1.0 if metal is None else metal, "metalness")
            self._connect_attr(mw + ".resultDiffuseRGB", shader + ".diffuseColor", force=True)
            self._connect_attr(mw + ".resultSpecularFaceRGB", shader + ".specularFaceColor", force=True)
            self._connect_attr(mw + ".resultSpecularEdgeRGB", shader + ".specularEdgeColor", force=True)
            if spec_color is not None:
                self._drop_slot("specular_color")
        coat = inputs.pop("coat", None)
        coat_ior = inputs.pop("coat_ior", 1.5)
        if coat is not None:
            self._apply(spec, shader, "clearcoatFaceColor", coat, "coat_face", scale=f0_from_ior(coat_ior))
            self._apply(spec, shader, "clearcoatEdgeColor", coat, "coat_edge")
            self._set_attr(shader + ".clearcoatModelType", 1)  # GGX
        if "transmission" in inputs:
            self._set_attr(shader + ".glassIor", ior or 1.5)
            if "roughness" in inputs:
                self._apply(spec, shader, "glassRoughness", inputs["roughness"], "glass_roughness")
        for slot in sorted(inputs):
            value = inputs[slot]
            if slot == "opacity":
                continue
            attr = self._attr(shader, slot)
            if attr is None:
                self._drop_slot(slot)
            elif isinstance(value, NormalInput):
                self._connect_normal(spec, slot, value, shader, attr)
            else:
                self._apply(spec, shader, attr, value, slot)
        if spec.double_sided:
            for attr in self.DOUBLE_SIDED:
                self._set_attr("%s.%s" % (shader, attr), True)
        self._build_opacity(spec, shader, inputs.get("opacity"))
        return sg

    def _build_opacity(self, spec, shader, value):
        if value is None or spec.alpha_mode == "OPAQUE":
            return
        if not isinstance(value, TexInput):
            if value != 1.0:
                self._set_attr(shader + ".presence", float(value))
            return
        src, _ = self._source(spec, value, "alpha")
        if spec.alpha_mode == "MASK":
            th = self._util("PxrThreshold", spec, "alpha_cutoff")
            self._connect_attr(src, th + ".inputRGBR", force=True)
            self._set_attr(th + ".channel", 0)            # Red
            self._set_attr(th + ".threshold", spec.alpha_cutoff)
            self._set_attr(th + ".transitionWidth", 0.0)  # hard cut: alpha >= cutoff -> 1
            self._set_attr(th + ".transitionProfile", 0)  # Linear (a step when the width is 0)
            src = th + ".resultR"
        self._connect_attr(src, shader + ".presence", force=True)

    def _link_uv_sets(self, shape, mats, missing):
        """PxrManifold2D reads another UV set by name (name_uvSet); only check it exists."""
        textures = set()
        for m in mats:
            textures |= self.material_textures.get(m, set())
        need = [self.plan.textures[t] for t in sorted(textures) if self.plan.textures[t].texcoord > 0]
        if not need:
            return
        names = cmds.polyUVSet(shape.instance_paths[0], q=True, allUVSets=True) or []
        for t in need:
            if "map%d" % (t.texcoord + 1) not in names:
                missing[(t.texcoord, self.files[t.index])].add(shape.name)
