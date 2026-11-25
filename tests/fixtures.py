"""Test fixtures generated in code (no third-party sample files). No Maya imports."""

import math
import os
from array import array

from gltf_builder import FLOAT, UBYTE, UINT, USHORT, GltfBuilder

S45 = math.sin(math.pi / 4)
C45 = math.cos(math.pi / 4)


def cube_data():
    """24-vertex unit cube (0.5 half extent), 12 triangles, per-face normals, 2 UV sets, RGBA colours."""
    faces = [
        ((0, 0, 1), [(-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1)]),
        ((0, 0, -1), [(1, -1, -1), (-1, -1, -1), (-1, 1, -1), (1, 1, -1)]),
        ((1, 0, 0), [(1, -1, 1), (1, -1, -1), (1, 1, -1), (1, 1, 1)]),
        ((-1, 0, 0), [(-1, -1, -1), (-1, -1, 1), (-1, 1, 1), (-1, 1, -1)]),
        ((0, 1, 0), [(-1, 1, 1), (1, 1, 1), (1, 1, -1), (-1, 1, -1)]),
        ((0, -1, 0), [(-1, -1, -1), (1, -1, -1), (1, -1, 1), (-1, -1, 1)]),
    ]
    pos, nrm, uv0, uv1, col, idx = [], [], [], [], [], []
    for f, (n, corners) in enumerate(faces):
        base = len(pos)
        for k, c in enumerate(corners):
            pos.append(tuple(0.5 * x for x in c))
            nrm.append(n)
            uv0.append(((k in (1, 2)) * 1.0, (k in (2, 3)) * 0.25 + 0.1 * f))
            uv1.append((0.5, 0.125 * k))
            col.append((f / 5.0, k / 3.0, 0.5, 0.25 + 0.125 * k))
        idx += [base, base + 1, base + 2, base, base + 2, base + 3]
    return pos, nrm, uv0, uv1, col, idx


def scene_builder():
    """The main fixture: hierarchy, TRS + matrix nodes, instancing, multi-primitive mesh,
    awkward names. Returns (GltfBuilder, info dict)."""
    b = GltfBuilder()
    mat_a = b.add_material("matA")
    mat_b = b.add_material("matB")
    pos, nrm, uv0, uv1, col, idx = cube_data()
    cube = b.add_mesh([b.add_primitive(pos, idx, normals=nrm, uvs=[uv0, uv1], colors=[col], material=mat_a)],
                      name="CubeMesh")

    # multi-primitive mesh
    quad = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)]
    prim_a = b.add_primitive(quad, [0, 1, 2], uvs=[[(0, 0), (1, 0), (1, 1), (0, 1)]],
                             colors=[[(1, 0, 0), (0, 1, 0), (0, 0, 1), (1, 1, 1)]], material=mat_a)
    strip = [(0, 0, 1), (1, 0, 1), (0, 1, 1), (1, 1, 1), (0, 2, 1)]
    prim_b = b.add_primitive(strip, [0, 1, 2, 3, 4], mode=5, material=mat_b, normals=[(0, 0, 1)] * 5)
    prim_c = b.add_primitive([(0, 0, 0), (1, 1, 1)], [0, 1], mode=1)  # LINES -> skipped
    tris = [(2, 0, 0), (3, 0, 0), (2, 1, 0), (3, 0, 0), (3, 1, 0), (2, 1, 0)]
    prim_d = b.add_primitive(tris, None)  # non-indexed, no material
    prim_e = {"attributes": dict(prim_a["attributes"]),
              "indices": b.add_accessor([0, 2, 3], "SCALAR", UBYTE), "material": mat_b}
    multi = b.add_mesh([prim_a, prim_b, prim_c, prim_d, prim_e], name="Multi")

    # matrix: rotate 90 deg about Z, translate (0, 0.5, 0)
    matrix = [0, 1, 0, 0, -1, 0, 0, 0, 0, 0, 1, 0, 0, 0.5, 0, 1]
    b.add_node("Root Node!", translation=[1, 2, 3], children=[1, 2, 3])            # 0
    b.add_node("Cube", mesh=cube, rotation=[0, S45, 0, C45], scale=[2, 1, 1])      # 1
    b.add_node(None, matrix=matrix, children=[4])                                 # 2
    b.add_node("Cube", mesh=cube, translation=[0, 0, -2])                          # 3 (instance)
    b.add_node("3d-child", mesh=multi)                                            # 4
    b.add_node("Orphan", mesh=cube)                                               # 5 not in scene
    b.add_node("Second Root", translation=[-1, 0, 0])                             # 6
    b.add_scene([0, 6], name="Main")
    b.add_scene([5], name="Other", default=False)
    b.json["scene"] = 0
    info = {
        "node_names": ["Root_Node_", "Cube", "node_2", "Cube_3", "_3d_child", None, "Second_Root"],
        "roots": ["Root_Node_", "Second_Root"],
    }
    return b, info


def write_scene(directory, stem="scene"):
    b, info = scene_builder()
    return (b.write_gltf(os.path.join(directory, stem + ".gltf")),
            b.write_glb(os.path.join(directory, stem + ".glb")), info)


def sparse_builder():
    """Triangle whose positions come from a sparse accessor on top of a base buffer, plus a
    second triangle whose positions are a sparse accessor without bufferView (zeros base)."""
    b = GltfBuilder()
    base = [(0, 0, 0), (1, 0, 0), (0, 1, 0), (9, 9, 9)]
    pos = b.add_accessor(base, "VEC3", sparse=([1, 3], [(2, 0, 0), (0, 0, 3)], USHORT))
    acc_zero = b.add_accessor([0, 0, 0] * 3, "VEC3", buffer_view=None,
                              sparse=([1, 2], [(0, 4, 0), (0, 0, 4)], UBYTE), count=3)
    m = b.add_mesh([{"attributes": {"POSITION": pos}, "indices": b.add_accessor([0, 1, 3], "SCALAR", USHORT)},
                    {"attributes": {"POSITION": acc_zero}}], name="Sparse")
    b.add_node("SparseNode", mesh=m)
    b.add_scene([0])
    return b


def unsupported_builder(extension="KHR_draco_mesh_compression"):
    b = GltfBuilder()
    m = b.add_mesh([b.add_primitive([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [0, 1, 2])])
    b.add_node("N", mesh=m)
    b.add_scene([0])
    b.json["extensionsUsed"] = [extension]
    b.json["extensionsRequired"] = [extension]
    return b


def grid_arrays(n):
    """n x n quads -> (n+1)^2 vertices, 2*n*n triangles. Returns (positions array('f'),
    normals array('f'), uvs array('f'), indices array('I'))."""
    verts = n + 1
    xs = [i / float(n) for i in range(verts)]
    pos = array("f")
    uv = array("f")
    for j in range(verts):
        y = xs[j]
        row = array("f", [0.0]) * (verts * 3)
        row[0::3] = array("f", xs)
        row[1::3] = array("f", [0.0]) * verts
        row[2::3] = array("f", [y]) * verts
        pos.extend(row)
        r2 = array("f", [0.0]) * (verts * 2)
        r2[0::2] = array("f", xs)
        r2[1::2] = array("f", [y]) * verts
        uv.extend(r2)
    nrm = array("f", [0.0, 1.0, 0.0]) * (verts * verts)
    idx = array("I")
    for j in range(n):
        a0 = j * verts
        b0 = a0 + verts
        quads = array("I", [0]) * (n * 6)
        r = range(n)
        quads[0::6] = array("I", [a0 + i for i in r])
        quads[1::6] = array("I", [b0 + i for i in r])
        quads[2::6] = array("I", [a0 + i + 1 for i in r])
        quads[3::6] = array("I", [a0 + i + 1 for i in r])
        quads[4::6] = array("I", [b0 + i for i in r])
        quads[5::6] = array("I", [b0 + i + 1 for i in r])
        idx.extend(quads)
    return pos, nrm, uv, idx


def large_grid_glb(path, n):
    """Write a GLB with one n x n grid mesh (2n^2 triangles) using bulk packing."""
    pos, nrm, uv, idx = grid_arrays(n)
    b = GltfBuilder()
    count = len(pos) // 3
    p = b._add("accessors", {"bufferView": b.add_buffer_view(pos.tobytes()), "componentType": FLOAT,
                             "type": "VEC3", "count": count, "min": [0, 0, 0], "max": [1, 0, 1]})
    nn = b._add("accessors", {"bufferView": b.add_buffer_view(nrm.tobytes()), "componentType": FLOAT,
                              "type": "VEC3", "count": count})
    t = b._add("accessors", {"bufferView": b.add_buffer_view(uv.tobytes()), "componentType": FLOAT,
                             "type": "VEC2", "count": count})
    i = b._add("accessors", {"bufferView": b.add_buffer_view(idx.tobytes()), "componentType": UINT,
                             "type": "SCALAR", "count": len(idx)})
    m = b.add_mesh([{"attributes": {"POSITION": p, "NORMAL": nn, "TEXCOORD_0": t}, "indices": i}], name="Grid")
    b.add_node("Grid", mesh=m)
    b.add_scene([0])
    b.write_glb(path)
    return count, len(idx) // 3


# Materials ------------------------------------------------------------------------------

def checker_png(alpha=True, size=8):
    """Small RGBA checker; alpha ramps 0..255 along x so MASK/BLEND have something to cut."""
    from gltf_builder import png_bytes
    return png_bytes(size, size, lambda x, y: ((255, 0, 0) if (x + y) % 2 else (0, 0, 255)) + (x * 255 // (size - 1),),
                     alpha=alpha)


def packed_png(size=4):
    from gltf_builder import png_bytes
    return png_bytes(size, size, lambda x, y: (255, 128, 64, 255), alpha=False)


WEBP_BYTES = b"RIFF\x1a\x00\x00\x00WEBPVP8L\x0d\x00\x00\x00\x2f\x00\x00\x00\x10\x07\x10\x11\x11\x88\x88\xfe\x07\x00"

# material index -> name, in the order materials_builder creates them
MATERIAL_NAMES = ["Red", "Textured", "Masked", "Blend", "Transformed", "Fancy", "SpecGloss", "Unlit", "WebpMat",
                  "Unused"]
TRANSFORM = {"offset": [0.25, -0.1], "rotation": 0.4, "scale": [2.0, 0.5], "texCoord": 1}


def materials_builder(mode="bufferView", external_names=("checker.png", "packed.png")):
    """Every material feature the importer maps. ``mode``: how images are stored
    ('bufferView', 'datauri' or 'uri' -- for 'uri' write the PNGs with write_material_images).

    Meshes / nodes:
      MultiMesh   prim0 Red (1 tri), prim1 Textured (2 tris)          node 'Multi'
      MixedMesh   prim0 Masked, prim1 no material, prim2 Red          node 'Mixed'
      UV2Mesh     Transformed (texCoord 1 via KHR_texture_transform) nodes 'UV2_a', 'UV2_b' (instances)
      OtherMesh   Blend, Fancy, SpecGloss, Unlit, WebpMat             node 'Others'
      SingleMesh  Blend only (single-sided)                          node 'Single'
    """
    b = GltfBuilder()
    if mode == "uri":
        img0 = b.add_image(mode="uri", uri=external_names[0])
        img1 = b.add_image(mode="uri", uri=external_names[1])
    else:
        img0 = b.add_image(checker_png(), mode=mode, name="Checker")
        img1 = b.add_image(packed_png(), mode=mode, name="checker")  # same name modulo case
    img2 = b.add_image(WEBP_BYTES, mime="image/webp", mode="bufferView" if mode == "uri" else mode, name="Webp")
    s_mirror = b.add_sampler(33648, 33071)
    t0 = b.add_texture(img0, s_mirror)
    t1 = b.add_texture(img1)
    t2 = b.add_texture(img2)
    tr = {"KHR_texture_transform": dict(TRANSFORM)}
    b.add_material("Red", doubleSided=True,
                   pbrMetallicRoughness={"baseColorFactor": [1, 0, 0, 1], "metallicFactor": 0.25,
                                         "roughnessFactor": 0.75})
    b.add_material("Textured", pbrMetallicRoughness={
        "baseColorFactor": [0.5, 0.5, 0.5, 1], "baseColorTexture": {"index": t0},
        "metallicFactor": 0.5, "roughnessFactor": 1.0, "metallicRoughnessTexture": {"index": t1}},
        normalTexture={"index": t1, "scale": 0.8}, occlusionTexture={"index": t1},
        emissiveTexture={"index": t0}, emissiveFactor=[1, 0.5, 0.25],
        extensions={"KHR_materials_emissive_strength": {"emissiveStrength": 2.0}})
    b.add_material("Masked", alphaMode="MASK", alphaCutoff=0.3,
                   pbrMetallicRoughness={"baseColorTexture": {"index": t0}})
    b.add_material("Blend", alphaMode="BLEND", pbrMetallicRoughness={"baseColorFactor": [0, 1, 0, 0.5]})
    b.add_material("Transformed", pbrMetallicRoughness={"baseColorTexture": {"index": t0, "extensions": tr}})
    b.add_material("Fancy", extensions={
        "KHR_materials_ior": {"ior": 1.4},
        "KHR_materials_transmission": {"transmissionFactor": 0.5},
        "KHR_materials_volume": {"attenuationDistance": 0.5, "attenuationColor": [0.9, 0.8, 0.7],
                                 "thicknessFactor": 0.1},
        "KHR_materials_clearcoat": {"clearcoatFactor": 0.7, "clearcoatRoughnessFactor": 0.2},
        "KHR_materials_sheen": {"sheenColorFactor": [0.5, 0.4, 0.3], "sheenRoughnessFactor": 0.6},
        "KHR_materials_specular": {"specularFactor": 0.8, "specularColorFactor": [1, 0.9, 0.8]},
        "KHR_materials_iridescence": {"iridescenceFactor": 1.0}})
    b.add_material("SpecGloss", extensions={"KHR_materials_pbrSpecularGlossiness": {
        "diffuseFactor": [0.2, 0.4, 0.6, 1], "specularFactor": [0.3, 0.3, 0.3], "glossinessFactor": 0.3}})
    b.add_material("Unlit", alphaMode="BLEND", extensions={"KHR_materials_unlit": {}},
                   pbrMetallicRoughness={"baseColorFactor": [1, 1, 0.5, 1], "baseColorTexture": {"index": t0}})
    b.add_material("WebpMat", pbrMetallicRoughness={"baseColorFactor": [0.1, 0.2, 0.3, 1],
                                                    "baseColorTexture": {"index": t2}})
    b.add_material("Unused")
    b.json["extensionsUsed"] = sorted(["KHR_texture_transform", "KHR_materials_emissive_strength",
                                       "KHR_materials_ior", "KHR_materials_transmission", "KHR_materials_volume",
                                       "KHR_materials_clearcoat", "KHR_materials_sheen", "KHR_materials_specular",
                                       "KHR_materials_iridescence", "KHR_materials_pbrSpecularGlossiness",
                                       "KHR_materials_unlit"])

    def tri(x, material, uv2=False, ntris=1):
        pos, uv0, uv1, idx = [], [], [], []
        for k in range(ntris):
            base = len(pos)
            pos += [(x + k, 0, 0), (x + k + 1, 0, 0), (x + k, 1, 0)]
            uv0 += [(0, 0), (1, 0), (0, 1)]
            uv1 += [(0.5, 0.5), (1, 0.5), (0.5, 1)]
            idx += [base, base + 1, base + 2]
        return b.add_primitive(pos, idx, uvs=[uv0, uv1] if uv2 else [uv0], material=material)

    multi = b.add_mesh([tri(0, 0), tri(2, 1, ntris=2)], name="MultiMesh")
    mixed = b.add_mesh([tri(0, 2), tri(2, None), tri(4, 0)], name="MixedMesh")
    uv2 = b.add_mesh([tri(0, 4, uv2=True)], name="UV2Mesh")
    others = b.add_mesh([tri(2 * k, m) for k, m in enumerate((3, 5, 6, 7, 8))], name="OtherMesh")
    single = b.add_mesh([tri(0, 3)], name="SingleMesh")
    b.add_node("Multi", mesh=multi)                                   # 0
    b.add_node("Mixed", mesh=mixed, translation=[0, 2, 0])            # 1
    b.add_node("UV2_a", mesh=uv2, translation=[0, 4, 0])              # 2
    b.add_node("UV2_b", mesh=uv2, translation=[0, 6, 0])              # 3
    b.add_node("Others", mesh=others, translation=[0, 8, 0])          # 4
    b.add_node("Single", mesh=single, translation=[0, 10, 0])         # 5
    b.add_scene([0, 1, 2, 3, 4, 5])
    return b


def write_material_images(directory, names=("checker.png", "packed.png")):
    with open(os.path.join(directory, names[0]), "wb") as f:
        f.write(checker_png())
    with open(os.path.join(directory, names[1]), "wb") as f:
        f.write(packed_png())
