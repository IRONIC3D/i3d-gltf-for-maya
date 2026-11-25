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


# Skins, morph targets, animation (milestone 3) ---------------------------------------------

def _quat_axis(axis, degrees):
    h = math.radians(degrees) / 2.0
    s = math.sin(h)
    n = math.sqrt(sum(a * a for a in axis))
    return [axis[0] / n * s, axis[1] / n * s, axis[2] / n * s, math.cos(h)]


def _m_trs(t=(0, 0, 0), q=(0, 0, 0, 1), s=(1, 1, 1)):
    """Row-major nested 4x4 (column vectors)."""
    x, y, z, w = q
    r = [[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
         [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
         [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]]
    return [[r[i][0] * s[0], r[i][1] * s[1], r[i][2] * s[2], t[i]] for i in range(3)] + [[0, 0, 0, 1]]


def _m_mul(a, b):
    return [[sum(a[r][k] * b[k][c] for k in range(4)) for c in range(4)] for r in range(4)]


def _m_inv_affine(m):
    a = [row[:3] for row in m[:3]]
    det = (a[0][0] * (a[1][1] * a[2][2] - a[1][2] * a[2][1]) - a[0][1] * (a[1][0] * a[2][2] - a[1][2] * a[2][0])
           + a[0][2] * (a[1][0] * a[2][1] - a[1][1] * a[2][0]))
    inv = [[(a[(j + 1) % 3][(i + 1) % 3] * a[(j + 2) % 3][(i + 2) % 3]
             - a[(j + 1) % 3][(i + 2) % 3] * a[(j + 2) % 3][(i + 1) % 3]) / det for j in range(3)] for i in range(3)]
    t = [-sum(inv[i][k] * m[k][3] for k in range(3)) for i in range(3)]
    return [inv[i] + [t[i]] for i in range(3)] + [[0, 0, 0, 1]]


def _col_major(m):
    return [m[r][c] for c in range(4) for r in range(4)]


def strip_mesh(rows=11, height=1.0, width=0.2):
    """A vertical strip of quads (2 vertices per row): positions, normals, indices."""
    pos, nrm, idx = [], [], []
    for r in range(rows):
        y = height * r / (rows - 1)
        pos += [(-width / 2, y, 0.0), (width / 2, y, 0.0)]
        nrm += [(0, 0, 1), (0, 0, 1)]
    for r in range(rows - 1):
        a = 2 * r
        idx += [a, a + 1, a + 3, a, a + 3, a + 2]
    return pos, nrm, idx


ANIM_TIMES = [0.0, 0.3, 0.71, 1.25]   # not frame-aligned at 24 fps
RIG_JOINTS = ["J0", "J1", "J2", "Twist", "J3", "J4", "J5"]


def rig_builder():
    """Skins, morph targets and animation in one file.

    Hierarchy (node index):
      0 Rig (T, R about Y, uniform scale 2)
        1 Armature (T)                      a transform above the skeleton
          2 J0 > 3 J1 > 4 J2 > 5 Twist > 6 J3 > 7 J4 > 8 J5
                                            Twist is in no skin: a joint between joints
        9 Holder (T, R) > 10 Body           skinned (skin 0), own transform, non-trivial parents
        11 Body2                            same mesh, skin 1 (the same joints in another order)
        12 Body3                            skinned (skin 0), animated, has a child
          13 Prop                             so the shape goes under 'Body3_skinned'
        14 SkinMorph                        morph target + skin 2 (J0, J1; no inverseBindMatrices)
        15 MorphA (mesh weights)  16 MorphB (node weights override)  17 MorphC (no target names)
    Strip vertices have up to 6 influences (JOINTS_0/1 + WEIGHTS_0/1); vertex 7's weights do
    not sum to 1. Animation 0 'Main' animates joints, parents and morph weights with every
    interpolation; animation 1 'Idle' only moves J1. J0 is never animated.
    """
    b = GltfBuilder()
    pos, nrm, idx = strip_mesh()
    nv = len(pos)
    joints0 = [[0, 0, 0, 0] for _ in range(nv)]
    weights0 = [[0, 0, 0, 0] for _ in range(nv)]
    joints1 = [[0, 0, 0, 0] for _ in range(nv)]
    weights1 = [[0, 0, 0, 0] for _ in range(nv)]
    centres = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
    for v, p in enumerate(pos):
        ws = [max(0.0, 1.0 - abs(p[1] - c) / 0.9) for c in centres]
        if v == 7:
            ws = [w * 0.9 for w in ws]  # not normalised on purpose
        order = sorted(range(6), key=lambda k: -ws[k])
        for slot, k in enumerate(order):
            if slot < 4:
                joints0[v][slot], weights0[v][slot] = k, ws[k]
            else:
                joints1[v][slot - 4], weights1[v][slot - 4] = k, ws[k]
    extra = {"JOINTS_0": b.add_accessor(joints0, "VEC4", USHORT), "WEIGHTS_0": b.add_accessor(weights0, "VEC4"),
             "JOINTS_1": b.add_accessor(joints1, "VEC4", UBYTE), "WEIGHTS_1": b.add_accessor(weights1, "VEC4")}
    strip = b.add_mesh([b.add_primitive(pos, idx, normals=nrm, extra_attributes=extra)], name="Strip")

    p2, n2, i2 = strip_mesh(rows=3, height=0.4)
    j2 = [[0, 1, 0, 0]] * len(p2)
    w2 = [[1.0 - p[1] / 0.4, p[1] / 0.4, 0, 0] for p in p2]
    tgt = b.add_accessor([(0.05, 0.0, 0.1 * k) for k in range(len(p2))], "VEC3")
    prim = b.add_primitive(p2, i2, normals=n2, extra_attributes={
        "JOINTS_0": b.add_accessor(j2, "VEC4", UBYTE), "WEIGHTS_0": b.add_accessor(w2, "VEC4")})
    prim["targets"] = [{"POSITION": tgt}]
    skin_morph = b.add_mesh([prim], name="SkinMorph", weights=[0.3], extras={"targetNames": ["Bulge"]})

    grid = [(x * 0.5, y * 0.5, 0.0) for y in range(3) for x in range(3)]
    gidx = []
    for y in range(2):
        for x in range(2):
            a = y * 3 + x
            gidx += [a, a + 1, a + 4, a, a + 4, a + 3]
    t0 = b.add_accessor([(0.0, 0.1, 0.0)] * 9, "VEC3")
    t1 = b.add_accessor([0, 0, 0] * 9, "VEC3", buffer_view=None, sparse=([2, 6], [(0, 0, 0.3), (0.2, 0, 0)], UBYTE),
                        count=9)
    t2 = b.add_accessor([(0, 0, 0)] * 4 + [(0.0, 0.0, -0.4)] + [(0, 0, 0)] * 4, "VEC3")
    mprim = b.add_primitive(grid, gidx)
    mprim["targets"] = [{"POSITION": t0}, {"POSITION": t1}, {"POSITION": t2}]
    morph = b.add_mesh([mprim], name="Morph", weights=[0.5, 0.0, 0.25],
                       extras={"targetNames": ["Smile", "Frown!", "Smile"]})
    mprim2 = b.add_primitive(grid, gidx)
    mprim2["targets"] = [{"POSITION": t0}, {"POSITION": t2}]
    morph2 = b.add_mesh([mprim2], name="MorphNoNames")
    cube_pos, cube_nrm, _, _, _, cube_idx = cube_data()
    prop = b.add_mesh([b.add_primitive([(0.1 * x, 0.1 * y, 0.1 * z) for x, y, z in cube_pos], cube_idx,
                                       normals=cube_nrm)], name="PropMesh")

    rig = dict(t=[1.0, 0.0, -0.5], q=_quat_axis((0, 1, 0), 30), s=[2.0, 2.0, 2.0])
    arm = dict(t=[0.0, 0.5, 0.0])
    jl = {2: dict(t=[0, 0, 0]), 3: dict(t=[0, 0.2, 0], q=_quat_axis((0, 0, 1), 10)), 4: dict(t=[0, 0.2, 0]),
          5: dict(t=[0, 0, 0], q=_quat_axis((0, 1, 0), 20)), 6: dict(t=[0, 0.2, 0]), 7: dict(t=[0, 0.2, 0]),
          8: dict(t=[0, 0.2, 0])}
    b.add_node("Rig", translation=rig["t"], rotation=rig["q"], scale=rig["s"],
               children=[1, 9, 11, 12, 14, 15, 16, 17])
    b.add_node("Armature", translation=arm["t"], children=[2])
    names = {2: "J0", 3: "J1", 4: "J2", 5: "Twist", 6: "J3", 7: "J4", 8: "J5"}
    for i in range(2, 9):
        kw = {"translation": jl[i]["t"]}
        if "q" in jl[i]:
            kw["rotation"] = jl[i]["q"]
        if i < 8:
            kw["children"] = [i + 1]
        b.add_node(names[i], **kw)
    b.add_node("Holder", translation=[3, 1, 0], rotation=_quat_axis((1, 0, 0), 40), children=[10])
    b.add_node("Body", mesh=strip, skin=0, translation=[5, 5, 5], rotation=_quat_axis((0, 0, 1), 70))
    b.add_node("Body2", mesh=strip, skin=1, translation=[-2, 0, 0])
    b.add_node("Body3", mesh=strip, skin=0, translation=[0, 0, 1], children=[13])
    b.add_node("Prop", mesh=prop, translation=[0, 1, 0])
    b.add_node("SkinMorph", mesh=skin_morph, skin=2)
    b.add_node("MorphA", mesh=morph, translation=[2, 0, 0])
    b.add_node("MorphB", mesh=morph, translation=[3, 0, 0], weights=[1.0, 0.5, 0.0])
    b.add_node("MorphC", mesh=morph2, translation=[4, 0, 0])
    b.add_scene([0])

    local = {0: _m_trs(rig["t"], rig["q"], rig["s"]), 1: _m_trs(arm["t"])}
    for i in range(2, 9):
        local[i] = _m_trs(jl[i]["t"], jl[i].get("q", (0, 0, 0, 1)))
    parent = {1: 0, 2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 7: 6, 8: 7}
    world = {}
    for i in range(9):
        world[i] = local[i] if i not in parent else _m_mul(world[parent[i]], local[i])

    def ibm(js):
        return b.add_accessor([_col_major(_m_inv_affine(world[j])) for j in js], "MAT4")
    skins = b.json.setdefault("skins", [])
    skins.append({"joints": [2, 3, 4, 6, 7, 8], "inverseBindMatrices": ibm([2, 3, 4, 6, 7, 8]), "skeleton": 2,
                  "name": "Main"})
    skins.append({"joints": [8, 6, 3, 2, 4, 7], "inverseBindMatrices": ibm([8, 6, 3, 2, 4, 7])})
    skins.append({"joints": [2, 3]})

    def sampler(times, values, interp, type_):
        return {"input": b.add_accessor(times, "SCALAR", minmax=True), "output": b.add_accessor(values, type_),
                "interpolation": interp}

    T = ANIM_TIMES
    qa = [_quat_axis((0, 0, 1), a) for a in (10, 120, -100, 60)]
    qa[2] = [-c for c in qa[2]]  # sign flip: same rotation, slerp must take the short way
    samplers = [
        sampler(T, qa, "LINEAR", "VEC4"),                                                          # J1 rotation
        sampler(T, [(0, 0.2, 0), (0.05, 0.25, 0), (0, 0.15, 0.05), (0, 0.2, 0)], "STEP", "VEC3"),   # J2 translation
        sampler(T, [x for k in range(4) for x in ((0.5, 0, 0), (1 + 0.2 * k, 1, 1 - 0.1 * k), (-0.3, 0.2, 0))],
                "CUBICSPLINE", "VEC3"),                                                            # J3 scale
        sampler(T, [_quat_axis((1, 0, 0), a) for a in (0, 45, -30, 10)], "STEP", "VEC4"),          # J4 rotation
        sampler(T, [_quat_axis((0, 1, 0), a) for a in (20, 80, 170, -150)], "LINEAR", "VEC4"),     # Twist rotation
        sampler(T, [x for k in range(4) for x in ((0, 0.4, 0), (0.1 * k, 0.5, 0), (0, -0.2, 0.3))],
                "CUBICSPLINE", "VEC3"),                                                            # Armature translation
        sampler(T, [(0, 0, 1), (0.5, 0, 1), (0.5, 0.5, 1), (0, 0, 1)], "LINEAR", "VEC3"),          # Body3 translation
        sampler(T, [_quat_axis((0, 1, 0), a) for a in (40, 90, 0, 40)], "LINEAR", "VEC4"),         # Holder rotation
        sampler(T, [0.5, 0, 0.25, 1, 0.5, 0, 0, 1, 1, 0.2, 0.1, 0.0], "LINEAR", "SCALAR"),          # MorphA weights
        sampler(T, [c for k in range(4) for c in ((0.1, 0, 0), (0.25 * k, 0.5, 1 - 0.25 * k), (0, 0.3, 0))],
                "CUBICSPLINE", "SCALAR"),                                                          # MorphB weights
        sampler(T, [0, 0, 1, 0.5, 0.25, 1, 0, 0], "STEP", "SCALAR"),                               # MorphC weights
        sampler(T, [0.3, 1.0, 0.0, 0.6], "LINEAR", "SCALAR"),                                      # SkinMorph weights
    ]
    targets = [(3, "rotation"), (4, "translation"), (6, "scale"), (7, "rotation"), (5, "rotation"),
               (1, "translation"), (12, "translation"), (9, "rotation"), (15, "weights"), (16, "weights"),
               (17, "weights"), (14, "weights")]
    channels = [{"sampler": k, "target": {"node": n, "path": p}} for k, (n, p) in enumerate(targets)]
    anims = b.json.setdefault("animations", [])
    anims.append({"name": "Main", "samplers": samplers, "channels": channels})
    idle = [sampler([0.0, 2.0], [(0, 0.2, 0), (0.1, 0.2, 0)], "LINEAR", "VEC3")]
    anims.append({"name": "Idle", "samplers": idle,
                  "channels": [{"sampler": 0, "target": {"node": 3, "path": "translation"}}]})
    return b


def interpolation_builder():
    """One small cube per (path, interpolation): nodes Node_<path>_<interp>, one animation
    with keys at ANIM_TIMES. Rotations include steps above 180 degrees and a sign flip."""
    b = GltfBuilder()
    cube_pos, cube_nrm, _, _, _, cube_idx = cube_data()
    cube = b.add_mesh([b.add_primitive([(0.2 * x, 0.2 * y, 0.2 * z) for x, y, z in cube_pos], cube_idx,
                                       normals=cube_nrm)], name="Cube")
    data = {
        "translation": [(0, 0, 0), (1, 0.5, 0), (1, -0.5, 2), (0, 0, 0)],
        "scale": [(1, 1, 1), (2, 0.5, 1), (0.5, 1.5, 2), (1, 1, 1)],
        "rotation": [_quat_axis((0, 0, 1), 0), _quat_axis((0.6, 0.8, 0), 170),
                     [-c for c in _quat_axis((1, 0, 0), -120)], _quat_axis((0, 1, 0), 90)],
    }
    samplers, channels, nodes = [], [], []
    for path in ("translation", "rotation", "scale"):
        for interp in ("STEP", "LINEAR", "CUBICSPLINE"):
            vals = data[path]
            n = len(vals[0])
            if interp == "CUBICSPLINE":
                out = []
                for k, v in enumerate(vals):
                    out += [tuple(0.3 * (c + 1) * ((-1) ** k) for c in range(n)), v,
                            tuple(-0.2 * (c + 1) for c in range(n))]
            else:
                out = vals
            node = b.add_node("Node_%s_%s" % (path, interp), mesh=cube, translation=[3.0 * len(nodes), 0, 0])
            nodes.append(node)
            samplers.append({"input": b.add_accessor(ANIM_TIMES, "SCALAR", minmax=True),
                             "output": b.add_accessor(out, "VEC4" if path == "rotation" else "VEC3"),
                             "interpolation": interp})
            channels.append({"sampler": len(samplers) - 1, "target": {"node": node, "path": path}})
    b.add_scene(nodes)
    b.json["animations"] = [{"name": "Interp", "samplers": samplers, "channels": channels}]
    return b


def write_rig(directory, glb=True):
    path = os.path.join(directory, "rig.glb" if glb else "rig.gltf")
    if not os.path.exists(path):
        b = rig_builder()
        if glb:
            b.write_glb(path)
        else:
            b.write_gltf(path)
    return path


def write_interp(directory):
    path = os.path.join(directory, "interp.glb")
    if not os.path.exists(path):
        interpolation_builder().write_glb(path)
    return path
