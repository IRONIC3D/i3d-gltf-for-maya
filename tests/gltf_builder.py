"""Minimal glTF / GLB writer used to generate test fixtures in code. No Maya imports.

Only what the tests need; not part of the shipped package.
"""

import base64
import json
import os
import struct

FMT = {5120: "b", 5121: "B", 5122: "h", 5123: "H", 5125: "I", 5126: "f"}
SIZE = {5120: 1, 5121: 1, 5122: 2, 5123: 2, 5125: 4, 5126: 4}
NCOMP = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT2": 4, "MAT3": 9, "MAT4": 16}

FLOAT = 5126
UBYTE = 5121
USHORT = 5123
UINT = 5125
BYTE = 5120
SHORT = 5122


def flatten(values):
    out = []
    for v in values:
        if isinstance(v, (list, tuple)):
            out.extend(v)
        else:
            out.append(v)
    return out


class GltfBuilder(object):
    def __init__(self, generator="i3d_gltf tests"):
        self.json = {"asset": {"version": "2.0", "generator": generator}}
        self.bin = bytearray()

    # low level --------------------------------------------------------------
    def _list(self, key):
        return self.json.setdefault(key, [])

    def _add(self, key, obj):
        lst = self._list(key)
        lst.append(obj)
        return len(lst) - 1

    def _align(self, n=4):
        while len(self.bin) % n:
            self.bin.append(0)

    def add_buffer_view(self, data, stride=None, target=None):
        self._align()
        offset = len(self.bin)
        self.bin.extend(data)
        bv = {"buffer": 0, "byteOffset": offset, "byteLength": len(data)}
        if stride:
            bv["byteStride"] = stride
        if target:
            bv["target"] = target
        return self._add("bufferViews", bv)

    @staticmethod
    def pack(values, ctype):
        values = flatten(values)
        return struct.pack("<%d%s" % (len(values), FMT[ctype]), *values)

    def add_accessor(self, values, type_="VEC3", ctype=FLOAT, normalized=False, buffer_view=True,
                     byte_offset=0, minmax=False, sparse=None, count=None, name=None):
        flat = flatten(values)
        n = NCOMP[type_]
        acc = {"componentType": ctype, "type": type_,
               "count": count if count is not None else len(flat) // n}
        if buffer_view is True:
            acc["bufferView"] = self.add_buffer_view(self.pack(flat, ctype))
        elif buffer_view is not None and buffer_view is not False:
            acc["bufferView"] = buffer_view
        if byte_offset:
            acc["byteOffset"] = byte_offset
        if normalized:
            acc["normalized"] = True
        if minmax and flat:
            acc["min"] = [min(flat[k::n]) for k in range(n)]
            acc["max"] = [max(flat[k::n]) for k in range(n)]
        if sparse:
            idx, vals, ictype = sparse
            acc["sparse"] = {
                "count": len(idx),
                "indices": {"bufferView": self.add_buffer_view(self.pack(idx, ictype)), "componentType": ictype},
                "values": {"bufferView": self.add_buffer_view(self.pack(flatten(vals), ctype))},
            }
        if name:
            acc["name"] = name
        return self._add("accessors", acc)

    def add_interleaved(self, streams, extra_pad=0):
        """streams: list of (values, type_, ctype). Returns accessor indices sharing one
        strided bufferView."""
        counts = set(len(flatten(v)) // NCOMP[t] for v, t, c in streams)
        assert len(counts) == 1
        count = counts.pop()
        offsets = []
        stride = 0
        for v, t, c in streams:
            offsets.append(stride)
            size = NCOMP[t] * SIZE[c]
            stride += (size + 3) & ~3
        stride += extra_pad
        data = bytearray(stride * count)
        for (v, t, c), off in zip(streams, offsets):
            flat = flatten(v)
            n = NCOMP[t]
            for i in range(count):
                struct.pack_into("<%d%s" % (n, FMT[c]), data, i * stride + off, *flat[i * n:(i + 1) * n])
        bv = self.add_buffer_view(bytes(data), stride=stride, target=34962)
        out = []
        for (v, t, c), off in zip(streams, offsets):
            acc = {"bufferView": bv, "byteOffset": off, "componentType": c, "type": t, "count": count}
            if t == "VEC3" and c == FLOAT and not out:
                flat = flatten(v)
                acc["min"] = [min(flat[k::3]) for k in range(3)]
                acc["max"] = [max(flat[k::3]) for k in range(3)]
            out.append(self._add("accessors", acc))
        return out

    # high level -------------------------------------------------------------
    def add_primitive(self, positions, indices=None, normals=None, uvs=None, colors=None, mode=None,
                      material=None, index_ctype=USHORT, extra_attributes=None):
        attrs = {"POSITION": self.add_accessor(positions, "VEC3", minmax=True)}
        if normals is not None:
            attrs["NORMAL"] = self.add_accessor(normals, "VEC3")
        for k, uv in enumerate(uvs or []):
            attrs["TEXCOORD_%d" % k] = self.add_accessor(uv, "VEC2")
        for k, col in enumerate(colors or []):
            n = len(col[0]) if isinstance(col[0], (list, tuple)) else 4
            attrs["COLOR_%d" % k] = self.add_accessor(col, "VEC3" if n == 3 else "VEC4")
        if extra_attributes:
            attrs.update(extra_attributes)
        prim = {"attributes": attrs}
        if indices is not None:
            prim["indices"] = self.add_accessor(indices, "SCALAR", index_ctype)
        if mode is not None:
            prim["mode"] = mode
        if material is not None:
            prim["material"] = material
        return prim

    def add_mesh(self, primitives, name=None, **extra):
        mesh = {"primitives": primitives}
        if name is not None:
            mesh["name"] = name
        mesh.update(extra)
        return self._add("meshes", mesh)

    def add_node(self, name=None, **kw):
        node = {}
        if name is not None:
            node["name"] = name
        node.update(kw)
        return self._add("nodes", node)

    def add_material(self, name):
        return self._add("materials", {"name": name})

    def add_scene(self, nodes, name=None, default=True):
        s = {"nodes": list(nodes)}
        if name:
            s["name"] = name
        idx = self._add("scenes", s)
        if default:
            self.json["scene"] = idx
        return idx

    # output -----------------------------------------------------------------
    def _finish_json(self, uri):
        j = json.loads(json.dumps(self.json))
        if self.bin:
            self._align()
            buf = {"byteLength": len(self.bin)}
            if uri is not None:
                buf["uri"] = uri
            j["buffers"] = [buf]
        return j

    def write_gltf(self, path, embed=False, bin_name=None):
        """Write .gltf with an external .bin (default) or a base64 data URI."""
        self._align()
        if embed:
            uri = "data:application/octet-stream;base64," + base64.b64encode(bytes(self.bin)).decode("ascii")
        else:
            bin_name = bin_name or os.path.splitext(os.path.basename(path))[0] + ".bin"
            with open(os.path.join(os.path.dirname(path), bin_name), "wb") as f:
                f.write(self.bin)
            from urllib.parse import quote
            uri = quote(bin_name)
        j = self._finish_json(uri if self.bin else None)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(j, f, indent=1)
        return path

    def glb_bytes(self):
        j = self._finish_json(None)
        jb = json.dumps(j).encode("utf-8")
        jb += b" " * ((4 - len(jb) % 4) % 4)
        chunks = struct.pack("<II", len(jb), 0x4E4F534A) + jb
        if self.bin:
            b = bytes(self.bin) + b"\0" * ((4 - len(self.bin) % 4) % 4)
            chunks += struct.pack("<II", len(b), 0x004E4942) + b
        return struct.pack("<4sII", b"glTF", 2, 12 + len(chunks)) + chunks

    def write_glb(self, path):
        with open(path, "wb") as f:
            f.write(self.glb_bytes())
        return path
