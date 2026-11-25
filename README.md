# IRONIC3D glTF for Maya

A pure-Python glTF 2.0 importer for Autodesk Maya. It registers a Maya file translator,
so `.gltf` and `.glb` files work with File > Import, File > Create Reference and `cmds.file`.
No compiled code and no third-party packages (numpy is not required).

Status: **0.2.0, milestone 2 (materials and textures).** Import only.

## Supported Maya versions

Maya 2024, 2025, 2026 and 2027 on Windows (tested). Maya 2028 beta is run as an early warning.
The code is plain Python 3.10+ and has no platform-specific parts, but macOS and Linux are untested.

## Install

Add the repository folder to `MAYA_MODULE_PATH` (it contains `i3dGltf.mod`), for example in `Maya.env`:

```
MAYA_MODULE_PATH=C:/src/Maya_gLTF
```

Then load the plug-in from the Plug-in Manager, or:

```python
import maya.cmds as cmds
cmds.loadPlugin("i3dGltf")
```

## Usage

The translator type is `IRONIC3D glTF`. One translator handles both `.gltf` and `.glb`;
without `type=` Maya picks it by file extension.

```python
# import
cmds.file(path, i=True, type="IRONIC3D glTF", namespace="asset",
          ignoreVersion=True, mergeNamespacesOnClash=False, preserveReferences=True)

# reference
cmds.file(path, reference=True, type="IRONIC3D glTF", namespace="asset",
          ignoreVersion=True, mergeNamespacesOnClash=False)

# with options
cmds.file(path, i=True, type="IRONIC3D glTF", options="scale=0.01;importColors=0")
cmds.file(path, i=True, type="IRONIC3D glTF", options="shader=openPBRSurface;textureDir=D:/tex")
```

### Options

Passed as `key=value;key=value` in the `options` flag.

| Option          | Default | Meaning |
|-----------------|---------|---------|
| `scale`         | `1.0`   | Extra scale multiplier. glTF metres always become Maya centimetres (x100); the factor is baked into vertex positions and translations, not put on a root node. |
| `importNormals` | `1`     | Import `NORMAL` as locked user normals. |
| `importColors`  | `1`     | Import `COLOR_n` as colour sets. |
| `shader`        | `aiStandardSurface` | Shader node type for glTF materials: `aiStandardSurface`, `aiOpenPBRSurface`, `openPBRSurface`, `standardSurface`, or `none` (no materials; everything on `initialShadingGroup`). |
| `textureDir`    | *(empty)* | Base folder for textures extracted from GLB files and data URIs. Empty: `<current project>/<sourceImages file rule>/i3dGltf`. A relative path is relative to the project. A per-file subfolder is always added (see Textures). |

Unknown keys and invalid values are reported as warnings and the default is used.
Values cannot contain `;`.

### What you get

* The default scene's node hierarchy as transforms (TRS or matrix, decomposed into channels).
* One Maya mesh per glTF mesh, all triangle primitives merged. Triangles stay triangles:
  no quad merging, no welding, no cleanup. Triangle strips and fans become their triangles.
* `TEXCOORD_n` as UV sets `map1`, `map2`, ... (V flipped), `COLOR_n` as colour sets
  `colorSet1`, ... (RGB or RGBA), normals as locked normals.
* A glTF mesh used by several nodes becomes a Maya instance (one shape, several parents).
* Node names come from the glTF file (characters outside `A-Z a-z 0-9 _` become `_`,
  unnamed nodes become `node_<index>`), so re-loading a reference re-creates the same
  names and reference edits are re-applied.
* In a Z-up Maya scene each imported root gets a +90 degree X rotation in its
  `offsetParentMatrix`; the glTF transform values stay in the normal channels.
* One Maya material and shading group per glTF material, shared by every mesh that uses it and
  assigned per face (also on instances). Primitives without a material stay on
  `initialShadingGroup`. Shading node names come from the material and image names with the
  same rules, so reference edits on materials (e.g. a changed colour) are re-applied.

### Materials

The `shader` option picks the node type. Availability is checked at import time (never by Maya
version); if the type is missing, the first available type along this chain is used, with one
warning. The Arnold plug-in (`mtoa`) is loaded automatically when an Arnold shader is needed and
installed.

| Requested           | Fallback chain |
|---------------------|----------------|
| `aiStandardSurface` | `standardSurface` |
| `aiOpenPBRSurface`  | `openPBRSurface`, `standardSurface` |
| `openPBRSurface`    | `aiOpenPBRSurface`, `standardSurface` (Maya 2024 has no `openPBRSurface`) |
| `standardSurface`   | always available |

Mapped from glTF:

* Metallic-roughness: base colour factor x texture, metallic/roughness factors x the packed
  texture (G = roughness, B = metalness). Factors are multiplied with `multiplyDivide` nodes so
  file nodes stay shareable.
* Normal texture and its scale, through `bump2d` (tangent-space normals) into the shader's
  normal input; used by Viewport 2.0 and Arnold.
* Emissive factor x texture and `KHR_materials_emissive_strength`. OpenPBR emission is a
  luminance: weight x 1000 nits, which Arnold renders like emission weight 1.
* `alphaMode`: `OPAQUE` ignores alpha; `BLEND` drives opacity from base colour alpha; `MASK`
  uses a `condition` node (alpha >= `alphaCutoff`). Arnold's `aiOpaque` is turned off on shapes
  that need it. Alpha of JPEG textures (no alpha channel) is not used.
* `doubleSided` sets the shape's `doubleSided` (a shape is double-sided if any of its
  materials is).
* `KHR_materials_ior`, `_transmission` (thin-walled unless `_volume` is present), `_volume`
  (attenuation colour/distance as transmission colour/depth), `_clearcoat` (including its normal
  texture), `_sheen` (OpenPBR: fuzz) and `_specular`.
* `KHR_materials_unlit`: base colour (factor x texture) drives emission at full weight; base,
  specular, coat, sheen and transmission are zero; alpha modes still apply.
* `KHR_materials_pbrSpecularGlossiness`: converted approximately (diffuse as base colour,
  metalness 0, specular colour, roughness = 1 - glossiness), with a warning.

Not mapped, reported once per import in one compact warning: occlusion textures (these shaders
have no slot), multiplying the base colour by `COLOR_0` (the colour set is still imported),
`KHR_materials_volume` thickness, and other material extensions (iridescence, anisotropy,
dispersion, ...).

### Textures

* External images are linked in place by absolute path, never copied. A missing file is still
  linked (so it can be relinked) and reported.
* Embedded images (GLB, data URIs) are extracted to
  `<textureDir, or project sourceimages/i3dGltf>/<file name>_<hash of the source path>/`, named
  after the image (or `image_<index>`) with the extension of the detected format. Nothing is
  ever written next to the source file. A file whose content is already identical is not
  rewritten (references re-import on every scene open). An unwritable folder gives a warning
  and untextured materials, not a failed import.
* One file node + `place2dTexture` per distinct image, colour space, sampler, texture transform
  and UV set, shared between materials.
* Colour spaces: sRGB for base colour, emissive, specular colour and sheen colour; Raw for data
  maps. The names are looked up in the scene's OCIO config (with fallbacks for other configs);
  `ignoreColorSpaceFileRules` is set so file rules do not override them.
* Samplers: `REPEAT`, `MIRRORED_REPEAT` and `CLAMP_TO_EDGE` set wrap/mirror on
  `place2dTexture`. Maya has no clamp-to-edge: clamp turns wrapping off (Maya's default colour
  outside 0-1), and Arnold still repeats the image there. Filters are ignored.
* `KHR_texture_transform` (offset, rotation, scale, texCoord) becomes `repeatUV`, `offset` and
  `rotateUV` on `place2dTexture`, compensating for the V flip of the UVs.
* A `texCoord` other than 0 links the texture to UV set `map<n+1>` with `uvLink` (a
  `uvChooser`), which Viewport 2.0 and Arnold both use.
* WebP, KTX2/Basis and AVIF images cannot be read by Maya's file node: warning, no texture.
  Files that *require* those extensions load (geometry and untextured materials).

Note for Arnold renders: MtoA's auto-TX conversion writes `.tx` files next to the source
textures. For read-only asset libraries, turn it off (`defaultArnoldRenderOptions.autotx`).

Warnings and errors go to the Script Editor. If a file cannot be read, `cmds.file(i=True)`
raises and nothing is left in the scene. For `reference=True`, Maya itself does not raise
when a reference fails to load; it prints the error and keeps an empty reference node.

### Not supported yet

Animation, skins, morph targets (parsed, not built), cameras, lights, an options dialog, and
export. Points and lines primitives are skipped with a warning. Files that require
`KHR_draco_mesh_compression`, `EXT_meshopt_compression` or other unsupported geometry
extensions are refused with a message naming the extension.

## Tests

```
python tests/run_all.py          # core tests with this Python, then core + Maya tests on every installed mayapy
mayapy tests/run_suite.py --maya # one Maya version
python tests/run_suite.py        # core tests only, no Maya needed
```

Test fixtures are generated in code. `tests/perf_large_mesh.py` times a 1M-triangle import, and
`mayapy tests/import_samples.py <folder> [--shader X] [--texture-dir D] [--no-reference]` imports
and references every file in a folder of sample models, reports materials and textures per file,
and tallies warnings and the glTF features used (it never writes into `<folder>`).

## License and trademarks

MIT, see [LICENSE](LICENSE).

glTF and the glTF logo are trademarks of the Khronos Group Inc. This project is not affiliated with
or endorsed by the Khronos Group. Maya is a trademark of Autodesk, Inc.
