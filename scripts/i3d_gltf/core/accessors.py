"""Accessor decoding with the standard library only (array / memoryview / struct).

Results are flat ``array.array`` objects (``count * num_components`` items):
  * float accessors and normalized integer accessors -> array('f') of float values
  * other integer accessors -> array of the matching integer type
"""

import struct
import sys
from array import array

from . import model as m
from .diagnostics import GltfFormatError
from .reader import load_buffer

_LITTLE = sys.byteorder == "little"

# array typecode with the right item size for each struct format char
_TYPECODES = {}
for _fmt in ("b", "B", "h", "H", "f"):
    _TYPECODES[_fmt] = _fmt
_TYPECODES["I"] = "I" if array("I").itemsize == 4 else "L"

_NORMALIZE = {
    "b": lambda v: max(v / 127.0, -1.0),
    "B": lambda v: v / 255.0,
    "h": lambda v: max(v / 32767.0, -1.0),
    "H": lambda v: v / 65535.0,
    "I": lambda v: v / 4294967295.0,
}


class AccessorData(object):
    __slots__ = ("values", "count", "num_components", "type", "component_type", "is_float")

    def __init__(self, values, count, num_components, type_, component_type, is_float):
        self.values = values
        self.count = count
        self.num_components = num_components
        self.type = type_
        self.component_type = component_type
        self.is_float = is_float

    def element(self, i):
        n = self.num_components
        return tuple(self.values[i * n:(i + 1) * n])

    def __len__(self):
        return self.count


def _element_layout(type_, comp_size):
    """Return (element_size, [(byte_offset, ncomp), ...]) honouring the glTF rule that
    matrix columns are aligned to 4 bytes."""
    n = m.ELEMENT_TYPES[type_]
    if type_ in ("MAT2", "MAT3", "MAT4"):
        rows = {"MAT2": 2, "MAT3": 3, "MAT4": 4}[type_]
        col = rows * comp_size
        col_stride = (col + 3) & ~3
        return col_stride * rows, [(c * col_stride, rows) for c in range(rows)]
    return n * comp_size, [(0, n)]


def _strided_read(data, start, count, stride, fmt, comp_size, elem_size, segments, ncomp):
    """Decode ``count`` elements starting at byte ``start``. Pure C-speed slicing."""
    tc = _TYPECODES[fmt]
    if count == 0:
        return array(tc)
    if stride == elem_size and len(segments) == 1:
        out = array(tc)
        out.frombytes(bytes(data[start:start + count * elem_size]))
        if not _LITTLE:
            out.byteswap()
        return out
    out = array(tc, bytes(count * ncomp * comp_size))
    span = stride * (count - 1) + elem_size
    region = memoryview(data)[start:start + span]
    if stride % comp_size == 0 and all(off % comp_size == 0 for off, _ in segments):
        # span is a multiple of comp_size when the data is properly aligned
        view = region.cast(fmt) if len(region) % comp_size == 0 else None
    else:
        view = None
    k = 0
    for off, cnt in segments:
        for c in range(cnt):
            if view is not None:
                first = (off + c * comp_size) // comp_size
                comp = array(tc)
                comp.frombytes(view[first::stride // comp_size][:count].tobytes())
            else:  # unaligned data: slow but correct
                comp = array(tc, [struct.unpack_from("<" + fmt, data, start + i * stride + off + c * comp_size)[0]
                                  for i in range(count)])
            if not _LITTLE and view is not None:
                comp.byteswap()
            out[k::ncomp] = comp
            k += 1
    return out


class AccessorReader(object):
    """Decodes accessors of a Document, loading buffers lazily and caching results."""

    def __init__(self, doc):
        self.doc = doc
        self._buffers = {}
        self._cache = {}

    def buffer(self, index):
        data = self._buffers.get(index)
        if data is None:
            data = load_buffer(self.doc, index)
            self._buffers[index] = data
        return data

    def buffer_view_bytes(self, index):
        bv = self.doc.buffer_views[index]
        data = self.buffer(bv.buffer)
        return memoryview(data)[bv.byte_offset:bv.byte_offset + bv.byte_length]

    def _read_raw(self, path, view_index, byte_offset, count, type_, ctype, allow_stride=True):
        fmt, size = m.COMPONENT_TYPES[ctype]
        ncomp = m.ELEMENT_TYPES[type_]
        elem_size, segments = _element_layout(type_, size)
        bv = self.doc.buffer_views[view_index]
        stride = bv.byte_stride if (allow_stride and bv.byte_stride) else elem_size
        if stride < elem_size:
            raise GltfFormatError("%s: bufferViews[%d].byteStride %d is smaller than the element size %d"
                                  % (path, view_index, stride, elem_size))
        needed = byte_offset + stride * (count - 1) + elem_size
        if needed > bv.byte_length:
            raise GltfFormatError("%s: needs %d bytes from bufferViews[%d] but it is only %d bytes long"
                                  % (path, needed, view_index, bv.byte_length))
        data = self.buffer(bv.buffer)
        if bv.byte_offset + bv.byte_length > len(data):
            raise GltfFormatError("%s: bufferViews[%d] exceeds the loaded buffer data" % (path, view_index))
        return _strided_read(data, bv.byte_offset + byte_offset, count, stride, fmt, size, elem_size,
                             segments, ncomp)

    def read(self, index):
        """Return AccessorData for accessors[index] (cached)."""
        cached = self._cache.get(index)
        if cached is not None:
            return cached
        acc = self.doc.accessors[index]
        path = "accessors[%d]" % index
        fmt, size = m.COMPONENT_TYPES[acc.component_type]
        ncomp = acc.num_components
        tc = _TYPECODES[fmt]
        if acc.buffer_view is not None:
            values = self._read_raw(path, acc.buffer_view, acc.byte_offset, acc.count, acc.type,
                                    acc.component_type)
        else:
            values = array(tc, bytes(acc.count * ncomp * size))
        if acc.sparse is not None:
            values = self._apply_sparse(path, acc, values)
        is_float = acc.component_type == m.FLOAT
        if acc.normalized and not is_float:
            norm = _NORMALIZE[fmt]
            values = array("f", [norm(v) for v in values])
            is_float = True
        data = AccessorData(values, acc.count, ncomp, acc.type, acc.component_type, is_float)
        self._cache[index] = data
        return data

    def _apply_sparse(self, path, acc, values):
        sp = acc.sparse
        if sp.count > acc.count:
            raise GltfFormatError("%s.sparse.count %d exceeds accessor count %d" % (path, sp.count, acc.count))
        indices = self._read_raw(path + ".sparse.indices", sp.indices_buffer_view, sp.indices_byte_offset,
                                 sp.count, "SCALAR", sp.indices_component_type, allow_stride=False)
        subst = self._read_raw(path + ".sparse.values", sp.values_buffer_view, sp.values_byte_offset,
                               sp.count, acc.type, acc.component_type, allow_stride=False)
        values = array(values.typecode, values)  # never mutate shared data
        n = acc.num_components
        prev = -1
        for k, i in enumerate(indices):
            if i >= acc.count:
                raise GltfFormatError("%s.sparse.indices[%d] = %d is out of range (count %d)"
                                      % (path, k, i, acc.count))
            if i <= prev:
                raise GltfFormatError("%s.sparse.indices must be strictly increasing" % path)
            prev = i
            values[i * n:(i + 1) * n] = subst[k * n:(k + 1) * n]
        return values

    def read_floats(self, index, expect_types=None, path=""):
        """Read an accessor and return float values (array('f') or array('d')),
        converting non-normalized integers (KHR_mesh_quantization) as plain numbers."""
        data = self.read(index)
        if expect_types and data.type not in expect_types:
            raise GltfFormatError("%s: accessors[%d] has type %s, expected %s"
                                  % (path or "attribute", index, data.type, "/".join(expect_types)))
        if data.is_float:
            return data
        return AccessorData(array("f", data.values), data.count, data.num_components, data.type,
                            data.component_type, True)

    def read_indices(self, index, path=""):
        data = self.read(index)
        if data.type != "SCALAR" or data.component_type not in (m.UNSIGNED_BYTE, m.UNSIGNED_SHORT,
                                                                m.UNSIGNED_INT):
            raise GltfFormatError("%s: index accessor %d must be SCALAR unsigned byte/short/int"
                                  % (path or "indices", index))
        return data.values
