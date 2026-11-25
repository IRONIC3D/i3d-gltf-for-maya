"""glTF materials -> a shader-neutral material plan. No Maya imports.

The plan describes each material as *slots* (base colour, metalness, roughness...) whose
value is either a constant or a ``TexInput`` (a shared texture, one channel or RGB, an
optional factor to multiply by, optionally inverted). ``maya/shading.py`` maps the slots
onto attributes of the chosen shader node type.

Shared textures: one ``TextureSpec`` (one Maya file node + place2dTexture) per distinct
(image, colour space, wrap modes, KHR_texture_transform, UV set). Factors are never put on
the shared file node; they become per-material multiply nodes.

Everything a target shader has no slot for is counted in ``MaterialPlan.dropped`` so the
importer can report it in one compact warning.
"""

import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from . import images as img
from .names import NameRegistry, sanitize

# Material extensions this importer interprets (fully or approximately).
HANDLED_MATERIAL_EXTENSIONS = frozenset([
    "KHR_materials_emissive_strength", "KHR_materials_ior", "KHR_materials_transmission",
    "KHR_materials_volume", "KHR_materials_clearcoat", "KHR_materials_sheen", "KHR_materials_specular",
    "KHR_materials_pbrSpecularGlossiness", "KHR_materials_unlit",
])

REPEAT = 10497
CLAMP_TO_EDGE = 33071
MIRRORED_REPEAT = 33648

SRGB = "sRGB"
RAW = "Raw"

# Slot names (the keys of MaterialSpec.inputs)
COLOR_SLOTS = frozenset(["base_color", "specular_color", "transmission_color", "sheen_color", "emission_color"])


@dataclass
class TextureSpec:
    """One shared Maya file node + place2dTexture."""
    index: int
    image: int
    color_space: str             # SRGB or RAW (resolved to the OCIO name in Maya)
    texcoord: int
    wrap_u: int = REPEAT
    wrap_v: int = REPEAT
    repeat_uv: Tuple[float, float] = (1.0, 1.0)
    offset_uv: Tuple[float, float] = (0.0, 0.0)
    rotate_uv: float = 0.0       # radians, counter-clockwise (place2dTexture.rotateUV)
    name: str = ""               # file node name
    place_name: str = ""         # place2dTexture name
    path: Optional[str] = None   # filled once images are resolved
    status: str = img.OK
    has_alpha: bool = True


@dataclass
class TexInput:
    texture: int                 # index into MaterialPlan.textures
    channel: str                 # "rgb", "r", "g", "b" or "a"
    factor: Any = None           # None, float or (r, g, b): multiplied in
    invert: bool = False         # 1 - (value * factor)

    def constant(self):
        """Value to use when the texture cannot be linked."""
        if self.channel == "rgb":
            return tuple(self.factor) if self.factor is not None else (1.0, 1.0, 1.0)
        v = 1.0 if self.factor is None else float(self.factor)
        return 1.0 - v if self.invert else v


@dataclass
class NormalInput:
    texture: int
    scale: float = 1.0


@dataclass
class MaterialSpec:
    index: int
    name: str
    sg_name: str
    inputs: Dict[str, Any] = field(default_factory=dict)  # slot -> constant | TexInput | NormalInput
    alpha_mode: str = "OPAQUE"
    alpha_cutoff: float = 0.5
    double_sided: bool = False
    transparent: bool = False    # opacity can be below 1 (drives aiOpaque)
    converted: List[str] = field(default_factory=list)  # e.g. "KHR_materials_pbrSpecularGlossiness"

    def utility_name(self, registry, suffix):
        return registry.claim("%s_%s" % (self.name, suffix))


@dataclass
class MaterialPlan:
    materials: Dict[int, MaterialSpec] = field(default_factory=dict)
    textures: List[TextureSpec] = field(default_factory=list)
    images: Dict[int, img.ImageFile] = field(default_factory=dict)
    dropped: Counter = field(default_factory=Counter)  # feature -> number of materials
    registry: Optional[NameRegistry] = None


# KHR_texture_transform ---------------------------------------------------------

def texture_transform(info):
    """(offset, rotation, scale, texcoord) of a TextureInfo, applying KHR_texture_transform."""
    t = info.extensions.get("KHR_texture_transform") if isinstance(info.extensions, dict) else None
    texcoord = info.tex_coord
    if not isinstance(t, dict):
        return (0.0, 0.0), 0.0, (1.0, 1.0), texcoord

    def pair(key, default):
        v = t.get(key)
        if isinstance(v, list) and len(v) == 2 and all(isinstance(x, (int, float)) for x in v):
            return float(v[0]), float(v[1])
        return default
    rot = t.get("rotation", 0.0)
    rot = float(rot) if isinstance(rot, (int, float)) else 0.0
    tc = t.get("texCoord")
    if isinstance(tc, int) and not isinstance(tc, bool) and tc >= 0:
        texcoord = tc
    return pair("offset", (0.0, 0.0)), rot, pair("scale", (1.0, 1.0)), texcoord


def gltf_uv_transform(uv, offset, rotation, scale):
    """Apply KHR_texture_transform to a glTF UV (spec order: translation * rotation * scale).

    The rotation sign follows the Khronos TextureTransformTest sample (and three.js /
    the glTF Sample Viewer): u' = cos*u + sin*v, v' = -sin*u + cos*v.
    """
    u = uv[0] * scale[0]
    v = uv[1] * scale[1]
    c = math.cos(rotation)
    s = math.sin(rotation)
    return (c * u + s * v + offset[0], -s * u + c * v + offset[1])


def place2d_uv_transform(uv, repeat_uv, offset_uv, rotate_uv):
    """Maya place2dTexture's UV mapping (measured in Maya): rotateUV turns counter-clockwise
    about (0.5, 0.5) after repeat and offset: out = R(rot) * (uv * repeat + offset - 0.5) + 0.5."""
    x = uv[0] * repeat_uv[0] + offset_uv[0] - 0.5
    y = uv[1] * repeat_uv[1] + offset_uv[1] - 0.5
    c = math.cos(rotate_uv)
    s = math.sin(rotate_uv)
    return (c * x - s * y + 0.5, s * x + c * y + 0.5)


def place2d_from_transform(offset, rotation, scale):
    """place2dTexture (repeatUV, offset, rotateUV radians) reproducing a KHR_texture_transform
    on a mesh whose V was flipped at import (V = 1 - v) and a Maya file node (V up).

    Required map from Maya UV (U, V) to Maya texture lookup (U'', V''):
        u = U, v = 1 - V;  (u', v') = gltf(u, v);  U'' = u', V'' = 1 - v'
    whose linear part is R(+rotation) * diag(scale), so repeat = scale, rotateUV = rotation,
    and the offset is solved from the constant term t = (s*sy + ox, 1 - c*sy - oy):
        offset = R(-rotation) * (t - 0.5) + 0.5
    """
    sx, sy = scale
    c = math.cos(rotation)
    s = math.sin(rotation)
    tx = s * sy + offset[0] - 0.5
    ty = 1.0 - c * sy - offset[1] - 0.5
    ou = c * tx + s * ty + 0.5
    ov = -s * tx + c * ty + 0.5
    return (sx, sy), (_clean(ou), _clean(ov)), rotation


def _clean(x):
    x = round(x, 9)
    return 0.0 if x == 0 else x


# Planning ------------------------------------------------------------------------

def _factor3(values, default=(1.0, 1.0, 1.0)):
    if isinstance(values, (list, tuple)) and len(values) >= 3 and all(isinstance(v, (int, float)) for v in values[:3]):
        return (float(values[0]), float(values[1]), float(values[2]))
    return default


def _num(d, key, default):
    v = d.get(key, default) if isinstance(d, dict) else default
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else default


def _tex_info(doc, d, key, path):
    """Parse an extension's textureInfo dict into a model.TextureInfo (None if absent/invalid)."""
    from .model import TextureInfo
    t = d.get(key) if isinstance(d, dict) else None
    if not isinstance(t, dict):
        return None
    index = t.get("index")
    if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(doc.textures):
        return None
    tc = t.get("texCoord", 0)
    ext = t.get("extensions")
    return TextureInfo(index=index, tex_coord=tc if isinstance(tc, int) and tc >= 0 else 0,
                       scale=_num(t, "scale", 1.0), extensions=ext if isinstance(ext, dict) else {})


class _Planner(object):
    def __init__(self, doc, registry, reporter):
        self.doc = doc
        self.registry = registry
        self.reporter = reporter
        self.plan = MaterialPlan(registry=registry)
        self._tex_keys = {}
        self._image_textures = {}  # image index -> texture spec indices

    # textures
    def texture(self, info, color_space, mat_index, what):
        """TextureSpec index for a TextureInfo, or None if it cannot be used."""
        if info is None:
            return None
        doc = self.doc
        tex = doc.textures[info.index]
        source = tex.source
        if source is None:
            exts = [e for e in ("KHR_texture_basisu", "EXT_texture_webp", "EXT_texture_avif") if e in tex.extensions]
            self.reporter.warning("textures[%d] (%s of materials[%d]) has no image Maya can read%s; texture skipped"
                                  % (info.index, what, mat_index,
                                     " (only %s)" % ", ".join(exts) if exts else ""))
            return None
        sampler = doc.samplers[tex.sampler] if tex.sampler is not None else None
        wrap_u = sampler.wrap_s if sampler else REPEAT
        wrap_v = sampler.wrap_t if sampler else REPEAT
        offset, rotation, scale, texcoord = texture_transform(info)
        repeat_uv, offset_uv, rotate_uv = place2d_from_transform(offset, rotation, scale)
        key = (source, color_space, wrap_u, wrap_v, texcoord,
               tuple(round(x, 7) for x in repeat_uv + offset_uv), round(rotate_uv, 7))
        found = self._tex_keys.get(key)
        if found is not None:
            return found
        spec = TextureSpec(index=len(self.plan.textures), image=source, color_space=color_space, texcoord=texcoord,
                           wrap_u=wrap_u, wrap_v=wrap_v, repeat_uv=repeat_uv, offset_uv=offset_uv,
                           rotate_uv=rotate_uv)
        self.plan.textures.append(spec)
        self._tex_keys[key] = spec.index
        return spec.index

    def tex_input(self, info, color_space, channel, factor, mat_index, what, invert=False):
        t = self.texture(info, color_space, mat_index, what)
        if t is None:
            return None
        return TexInput(texture=t, channel=channel, factor=factor, invert=invert)

    def slot(self, spec, slot, info, color_space, channel, factor, what, invert=False, default=None):
        """Set ``slot`` to a texture (times factor) or to the constant factor."""
        ti = self.tex_input(info, color_space, channel, factor, spec.index, what, invert)
        if ti is not None:
            spec.inputs[slot] = ti
        else:
            if factor is None:
                factor = default
            if factor is not None:
                spec.inputs[slot] = (1.0 - factor) if invert and not isinstance(factor, tuple) else factor

    def drop(self, feature):
        self.plan.dropped[feature] += 1

    # materials
    def material(self, i, vertex_colored):
        doc = self.doc
        mat = doc.materials[i]
        name = self.registry.claim(sanitize(mat.name, "material_%d" % i), i)
        spec = MaterialSpec(index=i, name=name, sg_name=self.registry.claim(name + "SG"),
                            alpha_mode=mat.alpha_mode if mat.alpha_mode in ("OPAQUE", "MASK", "BLEND") else "OPAQUE",
                            alpha_cutoff=mat.alpha_cutoff, double_sided=mat.double_sided)
        if mat.alpha_mode not in ("OPAQUE", "MASK", "BLEND"):
            self.reporter.warning("materials[%d] has unknown alphaMode %r; treated as OPAQUE" % (i, mat.alpha_mode))
        ext = mat.extensions if isinstance(mat.extensions, dict) else {}
        alpha_factor = float(mat.base_color_factor[3]) if len(mat.base_color_factor) > 3 else 1.0
        alpha_info = None

        sg = ext.get("KHR_materials_pbrSpecularGlossiness")
        if isinstance(sg, dict):
            spec.converted.append("KHR_materials_pbrSpecularGlossiness")
            diffuse = sg.get("diffuseFactor")
            diffuse = diffuse if isinstance(diffuse, list) and len(diffuse) == 4 else [1.0, 1.0, 1.0, 1.0]
            diffuse_tex = _tex_info(doc, sg, "diffuseTexture", "")
            self.slot(spec, "base_color", diffuse_tex, SRGB, "rgb", _factor3(diffuse), "diffuseTexture")
            alpha_factor = float(diffuse[3]) if isinstance(diffuse[3], (int, float)) else 1.0
            alpha_info = diffuse_tex
            spec.inputs["base_weight"] = 1.0
            spec.inputs["metalness"] = 0.0
            sg_tex = _tex_info(doc, sg, "specularGlossinessTexture", "")
            self.slot(spec, "specular_color", sg_tex, SRGB, "rgb", _factor3(sg.get("specularFactor")),
                      "specularGlossinessTexture")
            self.slot(spec, "roughness", sg_tex, SRGB, "a", _num(sg, "glossinessFactor", 1.0),
                      "specularGlossinessTexture", invert=True)
        else:
            self.slot(spec, "base_color", mat.base_color_texture, SRGB, "rgb", _factor3(mat.base_color_factor),
                      "baseColorTexture")
            alpha_info = mat.base_color_texture
            spec.inputs["base_weight"] = 1.0
            self.slot(spec, "metalness", mat.metallic_roughness_texture, RAW, "b", mat.metallic_factor,
                      "metallicRoughnessTexture")
            self.slot(spec, "roughness", mat.metallic_roughness_texture, RAW, "g", mat.roughness_factor,
                      "metallicRoughnessTexture")
        if mat.pbr_extensions and isinstance(mat.pbr_extensions, dict):
            for name in mat.pbr_extensions:
                self.drop("pbrMetallicRoughness extension %s" % name)

        # normal map
        if mat.normal_texture is not None:
            t = self.texture(mat.normal_texture, RAW, i, "normalTexture")
            if t is not None:
                spec.inputs["normal"] = NormalInput(texture=t, scale=mat.normal_texture.scale)

        # emission
        strength = 1.0
        es = ext.get("KHR_materials_emissive_strength")
        if isinstance(es, dict):
            strength = _num(es, "emissiveStrength", 1.0)
        emissive = _factor3(mat.emissive_factor, (0.0, 0.0, 0.0))
        if mat.emissive_texture is not None or any(emissive):
            self.slot(spec, "emission_color", mat.emissive_texture, SRGB, "rgb", emissive, "emissiveTexture")
            spec.inputs["emission_weight"] = strength
        else:
            spec.inputs["emission_weight"] = 0.0

        # alpha
        if spec.alpha_mode != "OPAQUE":
            ti = None
            if alpha_info is not None:
                t = self.texture(alpha_info, SRGB, i, "baseColorTexture")
                if t is not None:
                    ti = TexInput(texture=t, channel="a", factor=alpha_factor)
            spec.inputs["opacity"] = ti if ti is not None else alpha_factor

        # extensions
        ior = ext.get("KHR_materials_ior")
        if isinstance(ior, dict):
            spec.inputs["ior"] = _num(ior, "ior", 1.5)
        spc = ext.get("KHR_materials_specular")
        if isinstance(spc, dict):
            self.slot(spec, "specular_weight", _tex_info(doc, spc, "specularTexture", ""), RAW, "a",
                      _num(spc, "specularFactor", 1.0), "specularTexture")
            self.slot(spec, "specular_color", _tex_info(doc, spc, "specularColorTexture", ""), SRGB, "rgb",
                      _factor3(spc.get("specularColorFactor")), "specularColorTexture")
        tr = ext.get("KHR_materials_transmission")
        vol = ext.get("KHR_materials_volume")
        if isinstance(tr, dict):
            self.slot(spec, "transmission", _tex_info(doc, tr, "transmissionTexture", ""), RAW, "r",
                      _num(tr, "transmissionFactor", 0.0), "transmissionTexture")
            spec.inputs["thin_walled"] = not isinstance(vol, dict)
            spec.transparent = True
        if isinstance(vol, dict):
            color = _factor3(vol.get("attenuationColor"))
            distance = vol.get("attenuationDistance")
            if isinstance(distance, (int, float)) and not isinstance(distance, bool) and distance > 0:
                spec.inputs["transmission_color"] = color
                spec.inputs["transmission_depth_m"] = float(distance)  # metres; Maya side scales
            elif color != (1.0, 1.0, 1.0):
                spec.inputs["transmission_color"] = color
            if _num(vol, "thicknessFactor", 0.0) or "thicknessTexture" in vol:
                self.drop("KHR_materials_volume thickness (Maya uses the real geometry)")
        cc = ext.get("KHR_materials_clearcoat")
        if isinstance(cc, dict):
            self.slot(spec, "coat", _tex_info(doc, cc, "clearcoatTexture", ""), RAW, "r",
                      _num(cc, "clearcoatFactor", 0.0), "clearcoatTexture")
            self.slot(spec, "coat_roughness", _tex_info(doc, cc, "clearcoatRoughnessTexture", ""), RAW, "g",
                      _num(cc, "clearcoatRoughnessFactor", 0.0), "clearcoatRoughnessTexture")
            spec.inputs["coat_ior"] = 1.5
            cn = _tex_info(doc, cc, "clearcoatNormalTexture", "")
            if cn is not None:
                t = self.texture(cn, RAW, i, "clearcoatNormalTexture")
                if t is not None:
                    spec.inputs["coat_normal"] = NormalInput(texture=t, scale=cn.scale)
        sh = ext.get("KHR_materials_sheen")
        if isinstance(sh, dict):
            color = _factor3(sh.get("sheenColorFactor"), (0.0, 0.0, 0.0))
            color_tex = _tex_info(doc, sh, "sheenColorTexture", "")
            if color_tex is not None or any(color):
                spec.inputs["sheen"] = 1.0
                self.slot(spec, "sheen_color", color_tex, SRGB, "rgb", color, "sheenColorTexture")
                self.slot(spec, "sheen_roughness", _tex_info(doc, sh, "sheenRoughnessTexture", ""), RAW, "a",
                          _num(sh, "sheenRoughnessFactor", 0.0), "sheenRoughnessTexture")
        if isinstance(ext.get("KHR_materials_unlit"), dict):
            spec.converted.append("KHR_materials_unlit")
            base = spec.inputs.get("base_color", (1.0, 1.0, 1.0))
            spec.inputs["emission_color"] = base
            spec.inputs["emission_weight"] = 1.0
            spec.inputs["base_weight"] = 0.0
            spec.inputs["specular_weight"] = 0.0
            for slot in ("coat", "sheen", "transmission"):
                spec.inputs[slot] = 0.0
            for slot in ("normal", "coat_normal", "coat_roughness", "sheen_color", "sheen_roughness",
                         "metalness", "roughness", "specular_color"):
                spec.inputs.pop(slot, None)

        # what has no equivalent
        if mat.occlusion_texture is not None:
            self.drop("occlusionTexture")
        if vertex_colored:
            self.drop("COLOR_0 vertex colour tint of the base colour")
        for name in sorted(ext):
            if name not in HANDLED_MATERIAL_EXTENSIONS:
                self.drop(name)
        return spec


def material_usage(doc, plan, meshes):
    """{material index: uses_vertex_colour} for every material on a built primitive."""
    used = {}
    for shape in plan.shapes:
        md = meshes.get(shape.mesh)
        if md is None or md.num_faces == 0:
            continue
        for pr in md.primitives:
            if pr.material is None or pr.face_count == 0:
                continue
            prim = doc.meshes[shape.mesh].primitives[pr.primitive_index]
            used[pr.material] = used.get(pr.material, False) or "COLOR_0" in prim.attributes
    return used


def plan_materials(doc, scene_plan, meshes, reader, extract_dir, reporter, registry=None):
    """Build the MaterialPlan for every material used by a built primitive, and resolve
    (extract) the images those materials need. Never raises for bad material data."""
    registry = registry if registry is not None else NameRegistry()
    planner = _Planner(doc, registry, reporter)
    usage = material_usage(doc, scene_plan, meshes)
    for i in sorted(usage):
        planner.plan.materials[i] = planner.material(i, usage[i])
    plan = planner.plan

    # images
    plan.images = img.resolve_images(doc, reader, set(t.image for t in plan.textures), extract_dir, reporter)
    for t in plan.textures:
        f = plan.images.get(t.image)
        t.status = f.status if f else img.FAILED
        t.path = f.path if f else None
        t.has_alpha = f.has_alpha if f else True
    usable = set(t.index for t in plan.textures if t.status in (img.OK, img.MISSING))

    # drop inputs whose texture cannot be linked (fall back to the factor)
    for spec in plan.materials.values():
        for slot, value in list(spec.inputs.items()):
            if isinstance(value, TexInput) and value.texture not in usable:
                spec.inputs[slot] = value.constant()
            elif isinstance(value, NormalInput) and value.texture not in usable:
                del spec.inputs[slot]
            elif isinstance(value, TexInput) and slot == "opacity" and not plan.textures[value.texture].has_alpha:
                spec.inputs[slot] = value.constant()  # e.g. JPEG: no alpha channel, use the factor
        op = spec.inputs.get("opacity")
        if isinstance(op, TexInput):
            spec.transparent = True
        elif op is not None:
            if spec.alpha_mode == "MASK":  # constant alpha: the cutoff decides once
                op = spec.inputs["opacity"] = 1.0 if op >= spec.alpha_cutoff else 0.0
            if op < 1.0:
                spec.transparent = True

    # names for the shared texture nodes, in first-use order
    used_textures = set()
    for spec in plan.materials.values():
        for value in spec.inputs.values():
            if isinstance(value, (TexInput, NormalInput)):
                used_textures.add(value.texture)
    for t in plan.textures:
        if t.index not in used_textures:
            t.status = "unused"
            continue
        base = sanitize(_image_base(doc, t.image), "image_%d" % t.image)
        t.name = registry.claim(base + "_file")
        t.place_name = registry.claim(base + "_place2d")

    converted = Counter(c for s in plan.materials.values() for c in s.converted)
    if converted.get("KHR_materials_pbrSpecularGlossiness"):
        reporter.warning("%d material(s) use KHR_materials_pbrSpecularGlossiness; converted approximately to "
                         "metal/roughness (metalness 0, specular colour, roughness = 1 - glossiness)"
                         % converted["KHR_materials_pbrSpecularGlossiness"])
    return plan


def _image_base(doc, index):
    image = doc.images[index]
    name = image.name if isinstance(image.name, str) and image.name else None
    if name is None and image.uri and not image.uri.startswith("data:"):
        from urllib.parse import unquote
        name = unquote(image.uri.replace("\\", "/").rsplit("/", 1)[-1])
    if name:
        import os
        stem, ext = os.path.splitext(name)
        if ext.lower() in img._EXT:
            name = stem
    return name or "image_%d" % index


def dropped_summary(dropped):
    """One line listing skipped features with material counts, or '' if none."""
    if not dropped:
        return ""
    parts = ["%s (%d material%s)" % (k, v, "" if v == 1 else "s") for k, v in sorted(dropped.items())]
    return "Material features with no equivalent were skipped: " + "; ".join(parts)
