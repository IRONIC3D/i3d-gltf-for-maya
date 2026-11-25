# IRONIC3D glTF for Maya

A pure-Python glTF 2.0 importer for Autodesk Maya. It registers a Maya file translator,
so `.gltf` and `.glb` files work with File > Import, File > Create Reference and `cmds.file`.
No compiled code and no third-party packages (numpy is not required).

Status: **0.1.0, milestone 1 (geometry and hierarchy).** Import only.

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
```

### Options

Passed as `key=value;key=value` in the `options` flag.

| Option          | Default | Meaning |
|-----------------|---------|---------|
| `scale`         | `1.0`   | Extra scale multiplier. glTF metres always become Maya centimetres (x100); the factor is baked into vertex positions and translations, not put on a root node. |
| `importNormals` | `1`     | Import `NORMAL` as locked user normals. |
| `importColors`  | `1`     | Import `COLOR_n` as colour sets. |

Unknown keys and invalid values are reported as warnings and the default is used.

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
* All meshes use `initialShadingGroup` for now.

Warnings and errors go to the Script Editor. If a file cannot be read, `cmds.file(i=True)`
raises and nothing is left in the scene. For `reference=True`, Maya itself does not raise
when a reference fails to load; it prints the error and keeps an empty reference node.

### Not supported yet

Materials and textures, animation, skins, morph targets (parsed, not built), cameras,
lights, an options dialog, and export. Points and lines primitives are skipped with a warning.
Files that require `KHR_draco_mesh_compression`, `EXT_meshopt_compression`,
`KHR_texture_basisu`, `EXT_texture_webp` or other unsupported extensions are refused with a
message naming the extension.

## Tests

```
python tests/run_all.py          # core tests with this Python, then core + Maya tests on every installed mayapy
mayapy tests/run_suite.py --maya # one Maya version
python tests/run_suite.py        # core tests only, no Maya needed
```

Test fixtures are generated in code. `tests/perf_large_mesh.py` times a 1M-triangle import, and
`tests/import_samples.py <folder>` imports and references every file in a folder of sample models.

## License and trademarks

MIT, see [LICENSE](LICENSE).

glTF and the glTF logo are trademarks of the Khronos Group Inc. This project is not affiliated with
or endorsed by the Khronos Group. Maya is a trademark of Autodesk, Inc.
