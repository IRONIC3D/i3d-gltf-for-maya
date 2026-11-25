"""Accessor decoding tests. No Maya."""

import struct
import unittest

from gltf_builder import BYTE, FLOAT, SHORT, UBYTE, UINT, USHORT, GltfBuilder

from i3d_gltf.core import GltfFormatError, loads
from i3d_gltf.core.accessors import AccessorReader


def reader_for(b):
    b.json.setdefault("scenes", [{"nodes": []}])
    return AccessorReader(loads(b.glb_bytes()))


class TestComponentTypes(unittest.TestCase):
    def test_all_component_and_element_types(self):
        samples = {
            BYTE: [-128, -1, 0, 5, 127],
            UBYTE: [0, 1, 128, 254, 255],
            SHORT: [-32768, -2, 0, 3, 32767],
            USHORT: [0, 1, 40000, 65534, 65535],
            UINT: [0, 1, 70000, 2 ** 31, 2 ** 32 - 1],
            FLOAT: [-1.5, 0.0, 0.25, 3.0, 1e6],
        }
        sizes = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}
        for ctype, vals in samples.items():
            for etype, n in sizes.items():
                flat = (vals * ((n * 2) // len(vals) + 2))[:n * 2]
                b = GltfBuilder()
                b.add_accessor(flat, etype, ctype)
                data = reader_for(b).read(0)
                self.assertEqual(data.count, 2, (ctype, etype))
                self.assertEqual(data.num_components, n)
                self.assertEqual(list(data.values), flat, (ctype, etype))

    def test_matrix_column_padding(self):
        # MAT2 of bytes: each 2-byte column padded to 4 bytes
        b = GltfBuilder()
        raw = struct.pack("<bbxxbbxx", 1, 2, 3, 4) + struct.pack("<bbxxbbxx", 5, 6, 7, 8)
        bv = b.add_buffer_view(raw)
        b._add("accessors", {"bufferView": bv, "componentType": BYTE, "type": "MAT2", "count": 2})
        # MAT3 of shorts: each 6-byte column padded to 8 bytes
        cols = b"".join(struct.pack("<hhhxx", *c) for c in ((1, 2, 3), (4, 5, 6), (7, 8, 9)))
        bv2 = b.add_buffer_view(cols)
        b._add("accessors", {"bufferView": bv2, "componentType": SHORT, "type": "MAT3", "count": 1})
        r = reader_for(b)
        self.assertEqual(list(r.read(0).values), [1, 2, 3, 4, 5, 6, 7, 8])
        self.assertEqual(list(r.read(1).values), list(range(1, 10)))

    def test_normalized(self):
        b = GltfBuilder()
        b.add_accessor([0, 255, 128, 51], "VEC4", UBYTE, normalized=True)
        b.add_accessor([-128, -127, 0, 127], "VEC4", BYTE, normalized=True)
        b.add_accessor([0, 65535, 32768, 13107], "VEC4", USHORT, normalized=True)
        b.add_accessor([-32768, 32767], "VEC2", SHORT, normalized=True)
        r = reader_for(b)
        exp = [[0, 1, 128 / 255.0, 0.2], [-1, -1, 0, 1], [0, 1, 32768 / 65535.0, 0.2], [-1, 1]]
        for i, e in enumerate(exp):
            d = r.read(i)
            self.assertTrue(d.is_float)
            for x, y in zip(d.values, e):
                self.assertAlmostEqual(x, y, places=5)

    def test_interleaved_stride(self):
        pos = [(0, 1, 2), (3, 4, 5), (6, 7, 8)]
        uv = [(0.5, 0.25), (1, 2), (3, 4)]
        col = [(255, 0, 0, 255), (0, 255, 0, 128), (0, 0, 255, 0)]
        b = GltfBuilder()
        a_pos, a_uv, a_col = b.add_interleaved([(pos, "VEC3", FLOAT), (uv, "VEC2", FLOAT), (col, "VEC4", UBYTE)],
                                                extra_pad=8)
        b.json["accessors"][a_col]["normalized"] = True
        r = reader_for(b)
        self.assertEqual(list(r.read(a_pos).values), [float(x) for p in pos for x in p])
        self.assertEqual(list(r.read(a_uv).values), [0.5, 0.25, 1, 2, 3, 4])
        c = r.read(a_col).values
        self.assertAlmostEqual(c[5], 1.0)
        self.assertAlmostEqual(c[7], 128 / 255.0, places=6)

    def test_byte_offset_within_view(self):
        b = GltfBuilder()
        bv = b.add_buffer_view(struct.pack("<6f", 9, 9, 1, 2, 3, 4))
        b._add("accessors", {"bufferView": bv, "byteOffset": 8, "componentType": FLOAT, "type": "VEC2", "count": 2})
        self.assertEqual(list(reader_for(b).read(0).values), [1, 2, 3, 4])

    def test_accessor_exceeds_view(self):
        b = GltfBuilder()
        bv = b.add_buffer_view(struct.pack("<3f", 1, 2, 3))
        b._add("accessors", {"bufferView": bv, "componentType": FLOAT, "type": "VEC3", "count": 2})
        with self.assertRaises(GltfFormatError):
            reader_for(b).read(0)

    def test_no_buffer_view_is_zeros(self):
        b = GltfBuilder()
        b._add("accessors", {"componentType": FLOAT, "type": "VEC3", "count": 4})
        b._add("accessors", {"componentType": USHORT, "type": "SCALAR", "count": 3})
        b.add_accessor([1, 2, 3], "VEC3")  # make sure a buffer exists
        r = reader_for(b)
        self.assertEqual(list(r.read(0).values), [0.0] * 12)
        self.assertEqual(list(r.read(1).values), [0, 0, 0])


class TestSparse(unittest.TestCase):
    def test_sparse_on_buffer(self):
        b = GltfBuilder()
        b.add_accessor([(0, 0, 0), (1, 1, 1), (2, 2, 2), (3, 3, 3)], "VEC3",
                       sparse=([1, 3], [(9, 8, 7), (6, 5, 4)], UINT))
        d = reader_for(b).read(0)
        self.assertEqual(list(d.values), [0, 0, 0, 9, 8, 7, 2, 2, 2, 6, 5, 4])

    def test_sparse_without_buffer_view(self):
        b = GltfBuilder()
        b.add_accessor([0] * 5, "SCALAR", USHORT, buffer_view=None, count=5, sparse=([0, 4], [7, 9], UBYTE))
        self.assertEqual(list(reader_for(b).read(0).values), [7, 0, 0, 0, 9])

    def test_sparse_normalized(self):
        b = GltfBuilder()
        b.add_accessor([0, 0], "SCALAR", UBYTE, normalized=True, sparse=([1], [255], UBYTE))
        self.assertEqual(list(reader_for(b).read(0).values), [0.0, 1.0])

    def test_sparse_index_out_of_range(self):
        b = GltfBuilder()
        b.add_accessor([(0, 0, 0), (1, 1, 1)], "VEC3", sparse=([5], [(1, 1, 1)], USHORT))
        with self.assertRaises(GltfFormatError) as ctx:
            reader_for(b).read(0)
        self.assertIn("out of range", str(ctx.exception))

    def test_sparse_does_not_mutate_shared_view(self):
        b = GltfBuilder()
        a0 = b.add_accessor([1.0, 2.0, 3.0], "SCALAR")
        b._add("accessors", {"bufferView": b.json["accessors"][a0]["bufferView"], "componentType": FLOAT,
                             "type": "SCALAR", "count": 3,
                             "sparse": {"count": 1,
                                        "indices": {"bufferView": b.add_buffer_view(struct.pack("<H", 0)),
                                                    "componentType": USHORT},
                                        "values": {"bufferView": b.add_buffer_view(struct.pack("<f", 5))}}})
        r = reader_for(b)
        self.assertEqual(list(r.read(1).values), [5, 2, 3])
        self.assertEqual(list(r.read(0).values), [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
