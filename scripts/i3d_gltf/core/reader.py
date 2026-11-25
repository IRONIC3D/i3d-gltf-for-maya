"""glTF 2.0 / GLB reader: container parsing, JSON to data model, buffer resolution.

No Maya imports; usable from plain CPython 3.10+.
"""

import base64
import json
import os
import struct
import sys
from urllib.parse import unquote, urlparse

from . import model as m
from .diagnostics import GltfFormatError, GltfUnsupportedError, Reporter

GLB_MAGIC = b"glTF"
GLB_CHUNK_JSON = 0x4E4F534A
GLB_CHUNK_BIN = 0x004E4942

# extensionsRequired handling -------------------------------------------------
# Required extensions this importer handles fully.
SUPPORTED_REQUIRED_EXTENSIONS = frozenset([
    "KHR_mesh_quantization",  # any accessor component type is decoded generically
])

# Required extensions that only affect appearance (materials/textures). Geometry and
# transforms load correctly without them. Those in APPEARANCE_APPLIED are interpreted by
# core/materials.py; the others are loaded with a warning.
APPEARANCE_ONLY_PREFIXES = ("KHR_materials_",)
APPEARANCE_ONLY_EXTENSIONS = frozenset(["KHR_texture_transform", "KHR_texture_basisu", "EXT_texture_webp",
                                        "EXT_texture_avif"])
APPEARANCE_APPLIED = frozenset([
    "KHR_texture_transform", "KHR_materials_emissive_strength", "KHR_materials_ior",
    "KHR_materials_transmission", "KHR_materials_volume", "KHR_materials_clearcoat", "KHR_materials_sheen",
    "KHR_materials_specular", "KHR_materials_pbrSpecularGlossiness", "KHR_materials_unlit",
])
_TEXTURE_FORMAT_EXTENSIONS = {"KHR_texture_basisu": "KTX2/Basis Universal", "EXT_texture_webp": "WebP",
                              "EXT_texture_avif": "AVIF"}

# Well-known extensions we cannot decode; named explicitly in the error message.
KNOWN_UNSUPPORTED_EXTENSIONS = {
    "KHR_draco_mesh_compression": "Draco-compressed geometry",
    "EXT_meshopt_compression": "meshoptimizer-compressed buffers",
    "KHR_meshopt_compression": "meshoptimizer-compressed buffers",
    "EXT_mesh_gpu_instancing": "GPU instancing",
}


# Optional (used, not required) extensions that change what the scene contains; we
# ignore them and say so, since the result differs from a viewer that supports them.
GEOMETRY_EXTENSIONS_IGNORED = {
    "EXT_mesh_gpu_instancing": "GPU instances are not created",
}


def check_required_extensions(required, reporter):
    """Raise GltfUnsupportedError naming every required extension we cannot honour."""
    bad = []
    for name in required:
        if name in SUPPORTED_REQUIRED_EXTENSIONS:
            continue
        if name in APPEARANCE_APPLIED:
            continue
        if name in _TEXTURE_FORMAT_EXTENSIONS:
            reporter.warning("Required extension '%s': Maya cannot read %s images; textures that only "
                             "exist in that format are skipped" % (name, _TEXTURE_FORMAT_EXTENSIONS[name]))
            continue
        if name in APPEARANCE_ONLY_EXTENSIONS or name.startswith(APPEARANCE_ONLY_PREFIXES):
            reporter.warning("Required extension '%s' affects materials only and is not applied" % name)
            continue
        bad.append(name)
    if bad:
        parts = []
        for name in bad:
            what = KNOWN_UNSUPPORTED_EXTENSIONS.get(name)
            parts.append("'%s' (%s)" % (name, what) if what else "'%s'" % name)
        raise GltfUnsupportedError(
            "This file requires glTF extension%s %s, which %s not supported by this importer. "
            "Re-export the file without %s."
            % ("s" if len(bad) > 1 else "", ", ".join(parts),
               "are" if len(bad) > 1 else "is", "them" if len(bad) > 1 else "it"))


# Container -------------------------------------------------------------------

def split_glb(data):
    """Return (json_bytes, bin_bytes_or_None) from a GLB container, validating it."""
    if len(data) < 12:
        raise GltfFormatError("GLB file is too short (%d bytes) to contain a header" % len(data))
    magic, version, length = struct.unpack_from("<4sII", data, 0)
    if magic != GLB_MAGIC:
        raise GltfFormatError("Not a GLB file: bad magic %r (expected b'glTF')" % magic)
    if version != 2:
        raise GltfUnsupportedError("GLB container version %d is not supported (only version 2)" % version)
    if length > len(data):
        raise GltfFormatError("GLB file is truncated: header declares %d bytes but file has %d"
                              % (length, len(data)))
    offset = 12
    json_chunk = None
    bin_chunk = None
    index = 0
    while offset < length:
        if offset + 8 > length:
            raise GltfFormatError("GLB chunk %d header is truncated at byte %d" % (index, offset))
        chunk_len, chunk_type = struct.unpack_from("<II", data, offset)
        start = offset + 8
        end = start + chunk_len
        if end > length:
            raise GltfFormatError("GLB chunk %d is truncated: declares %d bytes, only %d available"
                                  % (index, chunk_len, length - start))
        if index == 0:
            if chunk_type != GLB_CHUNK_JSON:
                raise GltfFormatError("First GLB chunk must be JSON (type 0x%08X found)" % chunk_type)
            json_chunk = bytes(data[start:end])
        elif chunk_type == GLB_CHUNK_BIN and bin_chunk is None:
            if index != 1:
                raise GltfFormatError("GLB BIN chunk must be the second chunk")
            bin_chunk = bytes(data[start:end])
        # other chunk types are ignored, as the spec requires. chunkLength already
        # includes the 4-byte alignment padding.
        offset = end
        index += 1
    if json_chunk is None:
        raise GltfFormatError("GLB file has no JSON chunk")
    return json_chunk, bin_chunk


def _decode_json(raw, what):
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise GltfFormatError("%s is not valid UTF-8: %s" % (what, exc))
    try:
        obj = json.loads(text)
    except ValueError as exc:
        raise GltfFormatError("%s is not valid JSON: %s" % (what, exc))
    if not isinstance(obj, dict):
        raise GltfFormatError("%s top level must be a JSON object" % what)
    return obj


# JSON -> model ---------------------------------------------------------------

class _Parser(object):
    def __init__(self, root):
        self.root = root
        self.counts = dict((k, len(root.get(k) or [])) for k in (
            "accessors", "bufferViews", "buffers", "materials", "meshes", "nodes", "skins",
            "textures", "images", "samplers", "scenes", "cameras", "animations"))

    # helpers
    def fail(self, path, msg):
        raise GltfFormatError("%s: %s" % (path, msg))

    def obj(self, value, path):
        if not isinstance(value, dict):
            self.fail(path, "expected an object")
        return value

    def arr(self, value, path):
        if not isinstance(value, list):
            self.fail(path, "expected an array")
        return value

    def num(self, d, key, path, default=None, required=False, integer=False, minimum=None):
        if key not in d:
            if required:
                self.fail(path, "missing required property '%s'" % key)
            return default
        v = d[key]
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            self.fail("%s.%s" % (path, key), "expected a number, got %r" % (v,))
        if integer:
            if isinstance(v, float):
                if v != int(v):
                    self.fail("%s.%s" % (path, key), "expected an integer, got %r" % v)
                v = int(v)
        else:
            v = float(v)
        if minimum is not None and v < minimum:
            self.fail("%s.%s" % (path, key), "must be >= %s (got %s)" % (minimum, v))
        return v

    def ref(self, d, key, collection, path, required=False):
        v = self.num(d, key, path, required=required, integer=True)
        if v is None:
            return None
        return self.check_ref(v, collection, "%s.%s" % (path, key))

    def check_ref(self, v, collection, path):
        if isinstance(v, bool) or not isinstance(v, int):
            self.fail(path, "expected an index, got %r" % (v,))
        n = self.counts[collection]
        if v < 0 or v >= n:
            self.fail(path, "%s index %d is out of range (file has %d)" % (collection, v, n))
        return v

    def floats(self, d, key, path, length=None):
        if key not in d:
            return None
        v = self.arr(d[key], "%s.%s" % (path, key))
        if length is not None and len(v) != length:
            self.fail("%s.%s" % (path, key), "expected %d numbers, got %d" % (length, len(v)))
        out = []
        for i, x in enumerate(v):
            if isinstance(x, bool) or not isinstance(x, (int, float)):
                self.fail("%s.%s[%d]" % (path, key, i), "expected a number")
            out.append(float(x))
        return out

    def string(self, d, key, path, default=None):
        v = d.get(key, default)
        if v is not None and not isinstance(v, str):
            self.fail("%s.%s" % (path, key), "expected a string")
        return v

    def ext(self, d):
        e = d.get("extensions")
        return e if isinstance(e, dict) else {}

    def items(self, key):
        return [(i, self.obj(v, "%s[%d]" % (key, i)))
                for i, v in enumerate(self.arr(self.root.get(key, []), key))]

    # sections
    def asset(self):
        a = self.root.get("asset")
        if not isinstance(a, dict):
            raise GltfFormatError("Missing required 'asset' object: this is not a glTF 2.0 file")
        version = str(a.get("version", ""))
        min_version = a.get("minVersion")
        major = version.split(".")[0]
        if min_version is not None:
            if str(min_version) not in ("2.0",):
                raise GltfUnsupportedError("File requires glTF minVersion %s; only 2.0 is supported"
                                           % min_version)
        elif major != "2":
            raise GltfUnsupportedError("glTF version '%s' is not supported (only 2.x)" % version)
        return m.Asset(version=version, min_version=min_version, generator=a.get("generator"),
                       copyright=a.get("copyright"), extensions=self.ext(a), extras=a.get("extras"))

    def buffers(self):
        out = []
        for i, b in self.items("buffers"):
            p = "buffers[%d]" % i
            out.append(m.Buffer(byte_length=self.num(b, "byteLength", p, required=True, integer=True, minimum=1),
                                uri=self.string(b, "uri", p), name=b.get("name"),
                                extensions=self.ext(b), extras=b.get("extras")))
        return out

    def buffer_views(self):
        out = []
        for i, v in self.items("bufferViews"):
            p = "bufferViews[%d]" % i
            stride = self.num(v, "byteStride", p, integer=True)
            if stride is not None and (stride < 4 or stride > 252 or stride % 4):
                self.fail(p + ".byteStride", "must be a multiple of 4 between 4 and 252 (got %d)" % stride)
            out.append(m.BufferView(buffer=self.ref(v, "buffer", "buffers", p, required=True),
                                    byte_length=self.num(v, "byteLength", p, required=True, integer=True, minimum=1),
                                    byte_offset=self.num(v, "byteOffset", p, 0, integer=True, minimum=0),
                                    byte_stride=stride, target=self.num(v, "target", p, integer=True),
                                    name=v.get("name"), extensions=self.ext(v), extras=v.get("extras")))
        return out

    def accessors(self):
        out = []
        for i, a in self.items("accessors"):
            p = "accessors[%d]" % i
            ctype = self.num(a, "componentType", p, required=True, integer=True)
            if ctype not in m.COMPONENT_TYPES:
                self.fail(p + ".componentType", "invalid component type %d" % ctype)
            etype = a.get("type")
            if etype not in m.ELEMENT_TYPES:
                self.fail(p + ".type", "invalid element type %r" % (etype,))
            sparse = None
            if "sparse" in a:
                s = self.obj(a["sparse"], p + ".sparse")
                sp = p + ".sparse"
                si = self.obj(s.get("indices"), sp + ".indices")
                sv = self.obj(s.get("values"), sp + ".values")
                ictype = self.num(si, "componentType", sp + ".indices", required=True, integer=True)
                if ictype not in (m.UNSIGNED_BYTE, m.UNSIGNED_SHORT, m.UNSIGNED_INT):
                    self.fail(sp + ".indices.componentType", "invalid sparse index type %d" % ictype)
                sparse = m.SparseAccessor(
                    count=self.num(s, "count", sp, required=True, integer=True, minimum=1),
                    indices_buffer_view=self.ref(si, "bufferView", "bufferViews", sp + ".indices", required=True),
                    indices_byte_offset=self.num(si, "byteOffset", sp + ".indices", 0, integer=True, minimum=0),
                    indices_component_type=ictype,
                    values_buffer_view=self.ref(sv, "bufferView", "bufferViews", sp + ".values", required=True),
                    values_byte_offset=self.num(sv, "byteOffset", sp + ".values", 0, integer=True, minimum=0),
                    extensions=self.ext(s), extras=s.get("extras"))
            out.append(m.Accessor(
                component_type=ctype,
                count=self.num(a, "count", p, required=True, integer=True, minimum=1),
                type=etype,
                buffer_view=self.ref(a, "bufferView", "bufferViews", p),
                byte_offset=self.num(a, "byteOffset", p, 0, integer=True, minimum=0),
                normalized=bool(a.get("normalized", False)),
                min=self.floats(a, "min", p), max=self.floats(a, "max", p),
                sparse=sparse, name=a.get("name"), extensions=self.ext(a), extras=a.get("extras")))
        return out

    def texture_info(self, d, key, path):
        if key not in d:
            return None
        t = self.obj(d[key], "%s.%s" % (path, key))
        p = "%s.%s" % (path, key)
        return m.TextureInfo(index=self.ref(t, "index", "textures", p, required=True),
                             tex_coord=self.num(t, "texCoord", p, 0, integer=True, minimum=0),
                             scale=self.num(t, "scale", p, 1.0), strength=self.num(t, "strength", p, 1.0),
                             extensions=self.ext(t), extras=t.get("extras"))

    def materials(self):
        out = []
        for i, mat in self.items("materials"):
            p = "materials[%d]" % i
            pbr = self.obj(mat.get("pbrMetallicRoughness", {}), p + ".pbrMetallicRoughness")
            pp = p + ".pbrMetallicRoughness"
            out.append(m.Material(
                name=mat.get("name"),
                base_color_factor=self.floats(pbr, "baseColorFactor", pp, 4) or [1.0, 1.0, 1.0, 1.0],
                base_color_texture=self.texture_info(pbr, "baseColorTexture", pp),
                metallic_factor=self.num(pbr, "metallicFactor", pp, 1.0),
                roughness_factor=self.num(pbr, "roughnessFactor", pp, 1.0),
                metallic_roughness_texture=self.texture_info(pbr, "metallicRoughnessTexture", pp),
                normal_texture=self.texture_info(mat, "normalTexture", p),
                occlusion_texture=self.texture_info(mat, "occlusionTexture", p),
                emissive_texture=self.texture_info(mat, "emissiveTexture", p),
                emissive_factor=self.floats(mat, "emissiveFactor", p, 3) or [0.0, 0.0, 0.0],
                alpha_mode=self.string(mat, "alphaMode", p, "OPAQUE"),
                alpha_cutoff=self.num(mat, "alphaCutoff", p, 0.5),
                double_sided=bool(mat.get("doubleSided", False)),
                pbr_extensions=self.ext(pbr), pbr_extras=pbr.get("extras"),
                extensions=self.ext(mat), extras=mat.get("extras")))
        return out

    def textures(self):
        return [m.Texture(sampler=self.ref(t, "sampler", "samplers", "textures[%d]" % i),
                          source=self.ref(t, "source", "images", "textures[%d]" % i),
                          name=t.get("name"), extensions=self.ext(t), extras=t.get("extras"))
                for i, t in self.items("textures")]

    def images(self):
        return [m.Image(uri=self.string(im, "uri", "images[%d]" % i),
                        mime_type=self.string(im, "mimeType", "images[%d]" % i),
                        buffer_view=self.ref(im, "bufferView", "bufferViews", "images[%d]" % i),
                        name=im.get("name"), extensions=self.ext(im), extras=im.get("extras"))
                for i, im in self.items("images")]

    def samplers(self):
        out = []
        for i, s in self.items("samplers"):
            p = "samplers[%d]" % i
            out.append(m.Sampler(mag_filter=self.num(s, "magFilter", p, integer=True),
                                 min_filter=self.num(s, "minFilter", p, integer=True),
                                 wrap_s=self.num(s, "wrapS", p, 10497, integer=True),
                                 wrap_t=self.num(s, "wrapT", p, 10497, integer=True),
                                 name=s.get("name"), extensions=self.ext(s), extras=s.get("extras")))
        return out

    def attr_map(self, d, path):
        out = {}
        for k, v in self.obj(d, path).items():
            out[str(k)] = self.check_ref(v, "accessors", "%s.%s" % (path, k))
        return out

    def meshes(self):
        out = []
        for i, mesh in self.items("meshes"):
            p = "meshes[%d]" % i
            prims = []
            for j, pr in enumerate(self.arr(mesh.get("primitives"), p + ".primitives")):
                pp = "%s.primitives[%d]" % (p, j)
                self.obj(pr, pp)
                targets = [self.attr_map(t, "%s.targets[%d]" % (pp, k))
                           for k, t in enumerate(self.arr(pr.get("targets", []), pp + ".targets"))]
                prims.append(m.Primitive(attributes=self.attr_map(pr.get("attributes", {}), pp + ".attributes"),
                                         indices=self.ref(pr, "indices", "accessors", pp),
                                         material=self.ref(pr, "material", "materials", pp),
                                         mode=self.num(pr, "mode", pp, m.TRIANGLES, integer=True),
                                         targets=targets, extensions=self.ext(pr), extras=pr.get("extras")))
            out.append(m.Mesh(primitives=prims, weights=self.floats(mesh, "weights", p), name=mesh.get("name"),
                              extensions=self.ext(mesh), extras=mesh.get("extras")))
        return out

    def nodes(self):
        out = []
        for i, n in self.items("nodes"):
            p = "nodes[%d]" % i
            children = [self.check_ref(c, "nodes", "%s.children[%d]" % (p, k))
                        for k, c in enumerate(self.arr(n.get("children", []), p + ".children"))]
            out.append(m.Node(name=n.get("name") if isinstance(n.get("name"), str) else None,
                              children=children,
                              mesh=self.ref(n, "mesh", "meshes", p), skin=self.ref(n, "skin", "skins", p),
                              camera=self.ref(n, "camera", "cameras", p),
                              matrix=self.floats(n, "matrix", p, 16),
                              translation=self.floats(n, "translation", p, 3),
                              rotation=self.floats(n, "rotation", p, 4),
                              scale=self.floats(n, "scale", p, 3),
                              weights=self.floats(n, "weights", p),
                              extensions=self.ext(n), extras=n.get("extras")))
        return out

    def skins(self):
        out = []
        for i, s in self.items("skins"):
            p = "skins[%d]" % i
            out.append(m.Skin(joints=[self.check_ref(j, "nodes", "%s.joints[%d]" % (p, k))
                                      for k, j in enumerate(self.arr(s.get("joints"), p + ".joints"))],
                              inverse_bind_matrices=self.ref(s, "inverseBindMatrices", "accessors", p),
                              skeleton=self.ref(s, "skeleton", "nodes", p),
                              name=s.get("name"), extensions=self.ext(s), extras=s.get("extras")))
        return out

    def animations(self):
        out = []
        for i, a in self.items("animations"):
            p = "animations[%d]" % i
            samplers = []
            for k, s in enumerate(self.arr(a.get("samplers"), p + ".samplers")):
                sp = "%s.samplers[%d]" % (p, k)
                self.obj(s, sp)
                samplers.append(m.AnimationSampler(input=self.ref(s, "input", "accessors", sp, required=True),
                                                   output=self.ref(s, "output", "accessors", sp, required=True),
                                                   interpolation=self.string(s, "interpolation", sp, "LINEAR"),
                                                   extensions=self.ext(s), extras=s.get("extras")))
            channels = []
            for k, c in enumerate(self.arr(a.get("channels"), p + ".channels")):
                cp = "%s.channels[%d]" % (p, k)
                self.obj(c, cp)
                t = self.obj(c.get("target"), cp + ".target")
                sampler = self.num(c, "sampler", cp, required=True, integer=True)
                if sampler < 0 or sampler >= len(samplers):
                    self.fail(cp + ".sampler", "sampler index %d out of range" % sampler)
                channels.append(m.AnimationChannel(sampler=sampler,
                                                   target_node=self.ref(t, "node", "nodes", cp + ".target"),
                                                   target_path=self.string(t, "path", cp + ".target", ""),
                                                   target_extensions=self.ext(t),
                                                   extensions=self.ext(c), extras=c.get("extras")))
            out.append(m.Animation(channels=channels, samplers=samplers, name=a.get("name"),
                                   extensions=self.ext(a), extras=a.get("extras")))
        return out

    def cameras(self):
        return [m.Camera(type=self.string(c, "type", "cameras[%d]" % i, "perspective"),
                         perspective=c.get("perspective"), orthographic=c.get("orthographic"),
                         name=c.get("name"), extensions=self.ext(c), extras=c.get("extras"))
                for i, c in self.items("cameras")]

    def scenes(self):
        return [m.Scene(nodes=[self.check_ref(n, "nodes", "scenes[%d].nodes[%d]" % (i, k))
                               for k, n in enumerate(self.arr(s.get("nodes", []), "scenes[%d].nodes" % i))],
                        name=s.get("name"), extensions=self.ext(s), extras=s.get("extras"))
                for i, s in self.items("scenes")]


def parse_json(root, reporter=None):
    """Build a Document from an already-decoded glTF JSON object."""
    reporter = reporter or Reporter()
    p = _Parser(root)
    asset = p.asset()
    required = [str(x) for x in p.arr(root.get("extensionsRequired", []), "extensionsRequired")]
    used = [str(x) for x in p.arr(root.get("extensionsUsed", []), "extensionsUsed")]
    check_required_extensions(required, reporter)
    for name in used:
        if name in GEOMETRY_EXTENSIONS_IGNORED and name not in required:
            reporter.warning("Optional extension '%s' is not supported; using the file's fallback data (%s)"
                             % (name, GEOMETRY_EXTENSIONS_IGNORED[name]))
    doc = m.Document(
        asset=asset,
        scene=p.ref(root, "scene", "scenes", "root"),
        scenes=p.scenes(), nodes=p.nodes(), meshes=p.meshes(), accessors=p.accessors(),
        buffer_views=p.buffer_views(), buffers=p.buffers(), materials=p.materials(),
        textures=p.textures(), images=p.images(), samplers=p.samplers(), skins=p.skins(),
        animations=p.animations(), cameras=p.cameras(),
        extensions_used=used, extensions_required=required,
        extensions=p.ext(root), extras=root.get("extras"), json=root)
    for i, bv in enumerate(doc.buffer_views):
        if bv.byte_offset + bv.byte_length > doc.buffers[bv.buffer].byte_length:
            raise GltfFormatError("bufferViews[%d]: range %d..%d exceeds buffers[%d].byteLength %d"
                                  % (i, bv.byte_offset, bv.byte_offset + bv.byte_length, bv.buffer,
                                     doc.buffers[bv.buffer].byte_length))
    return doc


def is_glb_bytes(head):
    return head[:4] == GLB_MAGIC


def load(path, reporter=None):
    """Read a .gltf or .glb file into a Document. The container type is detected
    from the file content (GLB magic), not from the extension."""
    reporter = reporter or Reporter()
    try:
        with open(path, "rb") as f:
            data = f.read()
    except (IOError, OSError) as exc:
        raise GltfFormatError("Cannot read '%s': %s" % (path, exc))
    return loads(data, os.path.dirname(os.path.abspath(path)), reporter, source_path=path)


def loads(data, base_dir="", reporter=None, source_path=None):
    reporter = reporter or Reporter()
    is_glb = is_glb_bytes(data)
    if is_glb:
        json_bytes, bin_chunk = split_glb(data)
        root = _decode_json(json_bytes, "GLB JSON chunk")
    else:
        bin_chunk = None
        root = _decode_json(data, "glTF JSON")
    doc = parse_json(root, reporter)
    doc.source_path = source_path
    doc.base_dir = base_dir
    doc.is_glb = is_glb
    doc.glb_bin = bin_chunk
    return doc


# Buffers -----------------------------------------------------------------------

def decode_data_uri(uri):
    """Decode a ``data:`` URI. Returns (mime_type, bytes)."""
    header, sep, payload = uri.partition(",")
    if not sep:
        raise GltfFormatError("Malformed data URI (no comma)")
    meta = header[5:]
    parts = meta.split(";")
    mime = parts[0] or "text/plain"
    try:
        if "base64" in parts[1:]:
            return mime, base64.b64decode(payload, validate=False)
        return mime, unquote(payload).encode("latin-1")
    except Exception as exc:
        raise GltfFormatError("Malformed data URI: %s" % exc)


def resolve_uri_path(uri, base_dir):
    """Map a relative (percent-encoded) URI or file:// URI to a local path."""
    parsed = urlparse(uri)
    if parsed.scheme == "file":
        path = unquote(parsed.path)
        if sys.platform == "win32" and len(path) > 2 and path[0] == "/" and path[2] == ":":
            path = path[1:]
        if parsed.netloc:
            path = "//" + parsed.netloc + path
        return os.path.normpath(path)
    if parsed.scheme and len(parsed.scheme) > 1:
        raise GltfUnsupportedError("URI scheme '%s' is not supported (%s); only relative paths, "
                                   "file:// and data: URIs are" % (parsed.scheme, uri))
    path = unquote(uri)
    if os.path.isabs(path):
        return os.path.normpath(path)
    return os.path.normpath(os.path.join(base_dir, path))


def load_buffer(doc, index):
    """Return the bytes of buffers[index], checking byteLength."""
    buf = doc.buffers[index]
    if buf.uri is None:
        if not doc.is_glb or index != 0:
            raise GltfFormatError("buffers[%d] has no uri and is not the GLB binary chunk" % index)
        if doc.glb_bin is None:
            raise GltfFormatError("buffers[0] refers to the GLB binary chunk, but the file has none")
        data = doc.glb_bin
    elif buf.uri.startswith("data:"):
        data = decode_data_uri(buf.uri)[1]
    else:
        path = resolve_uri_path(buf.uri, doc.base_dir)
        try:
            with open(path, "rb") as f:
                data = f.read()
        except (IOError, OSError) as exc:
            raise GltfFormatError("buffers[%d]: cannot read external buffer '%s': %s" % (index, path, exc))
    if len(data) < buf.byte_length:
        raise GltfFormatError("buffers[%d] is truncated: byteLength is %d but only %d bytes are available"
                              % (index, buf.byte_length, len(data)))
    return data
