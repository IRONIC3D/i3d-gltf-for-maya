"""Reader tests: containers, URIs, validation, extensions, data model. No Maya."""

import base64
import json
import os
import shutil
import struct
import tempfile
import unittest

import fixtures
from gltf_builder import GltfBuilder

from i3d_gltf.core import GltfFormatError, GltfUnsupportedError, Reporter, load, loads
from i3d_gltf.core.accessors import AccessorReader
from i3d_gltf.core.reader import decode_data_uri, split_glb


def tri_builder():
    b = GltfBuilder()
    m = b.add_mesh([b.add_primitive([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [0, 1, 2])], name="Tri")
    b.add_node("N", mesh=m)
    b.add_scene([0])
    return b


class TempDirCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="i3dgltf_core_")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)


class TestContainers(TempDirCase):
    def test_gltf_external_bin_percent_encoded(self):
        b = tri_builder()
        path = b.write_gltf(os.path.join(self.dir, "tri.gltf"), bin_name="my buffer #1.bin")
        with open(path) as f:
            self.assertEqual(json.load(f)["buffers"][0]["uri"], "my%20buffer%20%231.bin")
        doc = load(path)
        pos = AccessorReader(doc).read(0)
        self.assertEqual(list(pos.values), [0, 0, 0, 1, 0, 0, 0, 1, 0])
        self.assertFalse(doc.is_glb)

    def test_gltf_external_bin_in_subfolder(self):
        b = tri_builder()
        os.makedirs(os.path.join(self.dir, "sub dir"))
        with open(os.path.join(self.dir, "sub dir", "d.bin"), "wb") as f:
            f.write(bytes(b.bin) + b"\0" * ((4 - len(b.bin) % 4) % 4))
        j = b._finish_json("sub%20dir/d.bin")
        path = os.path.join(self.dir, "t.gltf")
        with open(path, "w") as f:
            json.dump(j, f)
        self.assertEqual(AccessorReader(load(path)).read(0).count, 3)

    def test_data_uri(self):
        b = tri_builder()
        path = b.write_gltf(os.path.join(self.dir, "tri.gltf"), embed=True)
        doc = load(path)
        self.assertEqual(AccessorReader(doc).read(1).values.tolist(), [0, 1, 2])
        mime, data = decode_data_uri("data:application/gltf-buffer;base64," + base64.b64encode(b"abc").decode())
        self.assertEqual((mime, data), ("application/gltf-buffer", b"abc"))

    def test_glb_roundtrip_and_detection_by_content(self):
        b = tri_builder()
        path = b.write_glb(os.path.join(self.dir, "tri.glb"))
        doc = load(path)
        self.assertTrue(doc.is_glb)
        self.assertEqual(AccessorReader(doc).read(0).count, 3)
        # a GLB with a .gltf extension is still detected
        other = os.path.join(self.dir, "actually_glb.gltf")
        shutil.copy(path, other)
        self.assertTrue(load(other).is_glb)

    def test_glb_bad_magic(self):
        data = bytearray(tri_builder().glb_bytes())
        data[0:4] = b"gLTX"
        with self.assertRaises(GltfFormatError) as ctx:
            split_glb(bytes(data))
        self.assertIn("magic", str(ctx.exception))

    def test_glb_truncated_file(self):
        data = tri_builder().glb_bytes()
        with self.assertRaises(GltfFormatError) as ctx:
            loads(data[:-8])
        self.assertIn("truncated", str(ctx.exception))

    def test_glb_truncated_chunk(self):
        data = bytearray(tri_builder().glb_bytes())
        json_len = struct.unpack_from("<I", data, 12)[0]
        bin_hdr = 20 + json_len
        struct.pack_into("<I", data, bin_hdr, 10 ** 6)  # BIN chunk claims more than exists
        with self.assertRaises(GltfFormatError) as ctx:
            loads(bytes(data))
        self.assertIn("chunk 1 is truncated", str(ctx.exception))

    def test_glb_too_short_and_bad_version(self):
        with self.assertRaises(GltfFormatError):
            split_glb(b"glTF\x02\0")
        data = bytearray(tri_builder().glb_bytes())
        struct.pack_into("<I", data, 4, 1)
        with self.assertRaises(GltfUnsupportedError):
            split_glb(bytes(data))

    def test_glb_first_chunk_must_be_json(self):
        data = bytearray(tri_builder().glb_bytes())
        struct.pack_into("<I", data, 16, 0x004E4942)
        with self.assertRaises(GltfFormatError):
            split_glb(bytes(data))

    def test_missing_external_buffer(self):
        b = tri_builder()
        path = b.write_gltf(os.path.join(self.dir, "tri.gltf"))
        os.remove(os.path.join(self.dir, "tri.bin"))
        doc = load(path)
        with self.assertRaises(GltfFormatError) as ctx:
            AccessorReader(doc).read(0)
        self.assertIn("tri.bin", str(ctx.exception))

    def test_short_buffer(self):
        b = tri_builder()
        path = b.write_gltf(os.path.join(self.dir, "tri.gltf"))
        with open(os.path.join(self.dir, "tri.bin"), "r+b") as f:
            f.truncate(10)
        with self.assertRaises(GltfFormatError) as ctx:
            AccessorReader(load(path)).read(0)
        self.assertIn("truncated", str(ctx.exception))

    def test_invalid_json(self):
        with self.assertRaises(GltfFormatError):
            loads(b"{ not json")
        with self.assertRaises(GltfFormatError):
            loads(b"[]")
        with self.assertRaises(GltfFormatError):
            loads(b'{"nodes": []}')  # no asset

    def test_version_checks(self):
        with self.assertRaises(GltfUnsupportedError):
            loads(b'{"asset": {"version": "1.0"}}')
        with self.assertRaises(GltfUnsupportedError):
            loads(b'{"asset": {"version": "2.1", "minVersion": "2.1"}}')
        self.assertEqual(loads(b'{"asset": {"version": "2.0"}}').asset.version, "2.0")

    def test_http_uri_rejected(self):
        b = tri_builder()
        j = b._finish_json("http://example.com/x.bin")
        doc = loads(json.dumps(j).encode())
        with self.assertRaises(GltfUnsupportedError):
            AccessorReader(doc).read(0)


class TestValidation(unittest.TestCase):
    def base(self):
        b = tri_builder()
        return b._finish_json("data:application/octet-stream;base64," + base64.b64encode(bytes(b.bin)).decode())

    def check_error(self, j, text):
        with self.assertRaises(GltfFormatError) as ctx:
            loads(json.dumps(j).encode())
        self.assertIn(text, str(ctx.exception))

    def test_out_of_range_references(self):
        j = self.base()
        j["nodes"][0]["mesh"] = 7
        self.check_error(j, "nodes[0].mesh")
        j = self.base()
        j["meshes"][0]["primitives"][0]["indices"] = 99
        self.check_error(j, "out of range")
        j = self.base()
        j["scenes"][0]["nodes"] = [3]
        self.check_error(j, "scenes[0].nodes[0]")
        j = self.base()
        j["bufferViews"][0]["byteLength"] = 10 ** 6
        self.check_error(j, "exceeds buffers[0]")

    def test_bad_stride_and_types(self):
        j = self.base()
        j["bufferViews"][0]["byteStride"] = 6
        self.check_error(j, "byteStride")
        j = self.base()
        j["accessors"][0]["componentType"] = 1234
        self.check_error(j, "componentType")
        j = self.base()
        j["accessors"][0]["type"] = "VEC5"
        self.check_error(j, "type")


class TestRequiredExtensions(unittest.TestCase):
    def test_unsupported_named(self):
        for ext in ("KHR_draco_mesh_compression", "EXT_meshopt_compression", "VENDOR_something_unknown"):
            b = fixtures.unsupported_builder(ext)
            with self.assertRaises(GltfUnsupportedError) as ctx:
                loads(b.glb_bytes())
            self.assertIn(ext, str(ctx.exception))

    def test_multiple_named(self):
        b = fixtures.unsupported_builder()
        b.json["extensionsRequired"].append("EXT_meshopt_compression")
        with self.assertRaises(GltfUnsupportedError) as ctx:
            loads(b.glb_bytes())
        self.assertIn("KHR_draco_mesh_compression", str(ctx.exception))
        self.assertIn("EXT_meshopt_compression", str(ctx.exception))

    def test_supported_and_appearance_only(self):
        r = Reporter()
        loads(fixtures.unsupported_builder("KHR_mesh_quantization").glb_bytes(), reporter=r)
        self.assertEqual(r.warnings, [])
        for handled in ("KHR_materials_unlit", "KHR_texture_transform", "KHR_materials_clearcoat"):
            r = Reporter()
            loads(fixtures.unsupported_builder(handled).glb_bytes(), reporter=r)
            self.assertEqual(r.warnings, [], handled)
        for name, text in (("KHR_materials_iridescence", "not applied"), ("KHR_texture_basisu", "KTX2"),
                           ("EXT_texture_webp", "WebP")):
            r = Reporter()
            loads(fixtures.unsupported_builder(name).glb_bytes(), reporter=r)  # geometry still loads
            self.assertEqual(len(r.warnings), 1)
            self.assertIn(name, r.warnings[0])
            self.assertIn(text, r.warnings[0])

    def test_used_but_not_required_is_fine(self):
        b = tri_builder()
        b.json["extensionsUsed"] = ["KHR_draco_mesh_compression"]
        r = Reporter()
        doc = loads(b.glb_bytes(), reporter=r)
        self.assertEqual(doc.extensions_used, ["KHR_draco_mesh_compression"])
        self.assertEqual(r.warnings, [])

    def test_optional_gpu_instancing_warns(self):
        b = tri_builder()
        b.json["extensionsUsed"] = ["EXT_mesh_gpu_instancing"]
        r = Reporter()
        loads(b.glb_bytes(), reporter=r)
        self.assertEqual(len(r.warnings), 1)
        self.assertIn("EXT_mesh_gpu_instancing", r.warnings[0])


class TestModelParsing(unittest.TestCase):
    def test_full_model(self):
        b = tri_builder()
        j = b.json
        acc_t = b.add_accessor([0.0, 1.0], "SCALAR")
        acc_v = b.add_accessor([(0, 0, 0), (1, 0, 0)], "VEC3")
        acc_ibm = b.add_accessor([1.0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1], "MAT4")
        j["images"] = [{"uri": "tex%20a.png", "name": "img"}, {"bufferView": 0, "mimeType": "image/png"}]
        j["samplers"] = [{"magFilter": 9729, "minFilter": 9987, "wrapS": 33071}]
        j["textures"] = [{"source": 0, "sampler": 0, "extensions": {"EXT_x": {"a": 1}}}]
        j["materials"] = [{
            "name": "M", "doubleSided": True, "alphaMode": "MASK", "alphaCutoff": 0.3,
            "pbrMetallicRoughness": {"baseColorFactor": [1, 0, 0, 1], "metallicFactor": 0.2,
                                     "baseColorTexture": {"index": 0, "texCoord": 1,
                                                          "extensions": {"KHR_texture_transform": {"scale": [2, 2]}}}},
            "normalTexture": {"index": 0, "scale": 0.5}, "emissiveFactor": [1, 1, 0],
            "extensions": {"KHR_materials_emissive_strength": {"emissiveStrength": 4}},
            "extras": {"note": "hi"}}]
        j["meshes"][0]["primitives"][0]["material"] = 0
        j["meshes"][0]["primitives"][0]["targets"] = [{"POSITION": 0}, {"POSITION": 0, "NORMAL": 0}]
        j["meshes"][0]["weights"] = [0.25, 0.75]
        j["meshes"][0]["extras"] = {"targetNames": ["smile", "frown"]}
        j["nodes"][0]["weights"] = [0.5, 0.5]
        j["nodes"].append({"name": "joint"})
        j["skins"] = [{"joints": [1], "inverseBindMatrices": acc_ibm, "skeleton": 1}]
        j["nodes"][0]["skin"] = 0
        j["animations"] = [{"name": "Anim", "samplers": [{"input": acc_t, "output": acc_v}],
                            "channels": [{"sampler": 0, "target": {"node": 1, "path": "translation"}}]}]
        j["extras"] = {"top": True}
        j["extensions"] = {"VENDOR_meta": {"k": "v"}}
        doc = loads(b.glb_bytes())
        mat = doc.materials[0]
        self.assertEqual(mat.base_color_factor, [1, 0, 0, 1])
        self.assertEqual(mat.base_color_texture.tex_coord, 1)
        self.assertEqual(mat.base_color_texture.extensions["KHR_texture_transform"]["scale"], [2, 2])
        self.assertEqual(mat.normal_texture.scale, 0.5)
        self.assertEqual((mat.alpha_mode, mat.alpha_cutoff, mat.double_sided), ("MASK", 0.3, True))
        self.assertEqual(mat.extensions["KHR_materials_emissive_strength"]["emissiveStrength"], 4)
        self.assertEqual(mat.extras, {"note": "hi"})
        self.assertEqual(doc.images[0].uri, "tex%20a.png")
        self.assertEqual(doc.images[1].buffer_view, 0)
        self.assertEqual(doc.samplers[0].wrap_s, 33071)
        self.assertEqual(doc.samplers[0].wrap_t, 10497)
        self.assertEqual(doc.textures[0].extensions, {"EXT_x": {"a": 1}})
        mesh = doc.meshes[0]
        self.assertEqual(len(mesh.primitives[0].targets), 2)
        self.assertEqual(mesh.weights, [0.25, 0.75])
        self.assertEqual(mesh.target_names, ["smile", "frown"])
        self.assertEqual(doc.nodes[0].weights, [0.5, 0.5])
        self.assertEqual(doc.skins[0].joints, [1])
        ibm = AccessorReader(doc).read(doc.skins[0].inverse_bind_matrices)
        self.assertEqual(ibm.type, "MAT4")
        self.assertEqual(doc.animations[0].channels[0].target_path, "translation")
        self.assertEqual(doc.animations[0].samplers[0].interpolation, "LINEAR")
        self.assertEqual(doc.extras, {"top": True})
        self.assertEqual(doc.extensions["VENDOR_meta"], {"k": "v"})

    def test_matrix_and_trs(self):
        b, _ = fixtures.scene_builder()
        doc = loads(b.glb_bytes())
        self.assertEqual(len(doc.nodes[2].matrix), 16)
        self.assertIsNone(doc.nodes[2].translation)
        self.assertEqual(doc.nodes[0].translation, [1, 2, 3])
        self.assertEqual(doc.scene, 0)
        self.assertEqual(len(doc.scenes), 2)


if __name__ == "__main__":
    unittest.main()
