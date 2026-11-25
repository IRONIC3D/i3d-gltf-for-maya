# Integration contract

For host applications that detect and drive IRONIC3D glTF for Maya from their own
scripts. Everything here is stable from version 0.3.0 onwards unless a later entry in
this file says otherwise.

## 1. Identity

| | |
|---|---|
| Plug-in name in Maya | `i3dGltf` (`cmds.pluginInfo('i3dGltf', query=True, loaded=True)`, `cmds.loadPlugin('i3dGltf')`) |
| File translator name | `IRONIC3D glTF` |
| Python package | `i3d_gltf` |
| Module file | `i3dGltf.mod` |
| Maya versions | 2024, 2025, 2026, 2027 (tested on every change). 2028 beta passes the same tests but is not yet a supported target. |
| Platform | Windows is tested. The code is pure Python with no compiled parts; macOS and Linux are untested. |

The plug-in is pure Python. There are no binaries and no per-version builds: one folder
serves every supported Maya version.

### Where the module file lives

Maya finds the plug-in through `i3dGltf.mod`. A host can detect an install without
running Maya by looking for that file in Maya's module folders:

- `<Documents>\maya\modules\`
- `<Documents>\maya\<version>\modules\`
- `%CommonProgramFiles%\Autodesk Shared\Modules\maya\<version>\`
- every folder listed in the `MAYA_MODULE_PATH` environment variable

`<Documents>` is the user's Documents folder as Windows reports it, which is often
redirected (for example into OneDrive); do not assume `%USERPROFILE%\Documents`.

The first line of the file is always

    + i3dGltf <version> <path>

so the installed version can be read from the file itself.

Finding the file is a hint, not proof. The authority is Maya: `cmds.loadPlugin('i3dGltf')`
either succeeds or raises.

## 2. Version

```python
cmds.pluginInfo('i3dGltf', query=True, version=True)   # '0.3.0', after the plug-in is loaded
import i3d_gltf; i3d_gltf.__version__                  # same string
```

Versions are `major.minor.patch`. Compare them as integers, not as text.

**Minimum version to require: 0.3.0.** It is the first version with skins, morph targets,
animation and the report described in section 4.

## 3. Import call

The plug-in registers a file translator, so the normal Maya calls work for both `.gltf`
and `.glb`:

```python
cmds.loadPlugin('i3dGltf', quiet=True)

# import
cmds.file(path, i=True, type='IRONIC3D glTF', namespace=ns,
          ignoreVersion=True, mergeNamespacesOnClash=False, preserveReferences=True,
          options='scale=1.0;shader=aiStandardSurface')

# reference
cmds.file(path, reference=True, type='IRONIC3D glTF', namespace=ns,
          ignoreVersion=True, mergeNamespacesOnClash=False,
          options='scale=1.0;shader=aiStandardSurface')
```

`options` is a `key=value;key=value` string. Every key is optional. Unknown keys and bad
values produce a warning and the default is used.

| Key | Values | Default | Meaning |
|---|---|---|---|
| `scale` | number | `1.0` | Multiplies the size. glTF is in metres and is always converted to Maya's centimetres first, so `1.0` means real-world size. |
| `shader` | `aiStandardSurface`, `aiOpenPBRSurface`, `openPBRSurface`, `standardSurface`, `none` | `aiStandardSurface` | Shader type to build. `none` builds no materials at all. |
| `textureDir` | folder path | empty | Where images embedded in the file are extracted. Empty means the Maya project's `sourceimages/i3dGltf/` folder. |
| `importNormals` | `0`, `1` | `1` | Use the file's normals. |
| `importColors` | `0`, `1` | `1` | Import vertex colours as colour sets. |
| `animation` | `first`, `none`, an index, or a name | `first` | Which animation in the file to import. |
| `animSampling` | `keys`, `bake` | `keys` | `keys` keeps the keys as the file has them; `bake` writes a key on every frame. |
| `timeRange` | `0`, `1` | `1` | Set the playback range to the imported animation. Applies to imports only; a reference never changes the range. |

Notes:

- **Animation is on by default.** Node animation, skinned meshes and morph targets all
  arrive without any option. Pass `animation=none` for a static import.
- **Keys arrive as the file has them.** One kind of curve cannot be matched exactly this
  way: rotations stored with cubic-spline interpolation. Their keys are exact but the values
  between keys are approximate, and the report carries a warning. `animSampling=bake` gives
  exact values on every frame.
- **There is no up-axis option.** glTF is Y-up. In a Z-up Maya scene the imported roots
  are rotated automatically.
- **Geometry is never converted.** Triangles arrive as triangles.
- **References.** A referenced glTF file is read again every time the scene opens, so the
  plug-in must be installed (through the module file) on any machine that opens the scene.
  Node names are derived from the file and do not change between loads.

## 4. What it returns

`cmds.file` itself returns only what Maya returns. The details come from one call made
straight after it:

```python
from i3d_gltf.maya import importer
report = importer.last_report()
```

`last_report()` describes the most recent load attempt in this Maya session, whether it
succeeded or failed. It returns `None` if nothing has been loaded yet. The result is a
plain dictionary that `json.dumps` accepts:

| Key | Type | Content |
|---|---|---|
| `ok` | bool | Whether the load succeeded. |
| `path` | string | Absolute path of the source file. |
| `version` | string | Plug-in version. |
| `error` | string or null | The error message when `ok` is false. |
| `roots` | list of strings | Full DAG paths of the new top-level nodes, including the namespace. |
| `shaderType` | string or null | The shader node type actually used (it can differ from the one requested when that type is unavailable). Null with `shader=none`. |
| `materials` | list | One entry per material built: `name` (the glTF name), `shader`, `shadingGroup` (Maya node names). |
| `textures` | list | One entry per texture: `image`, `node` (file node, or null), `path` (or null), `embedded` (bool), `status`. |
| `animation` | object or null | `name`, `index`, `start` and `end` in seconds, `curves` (count). |
| `skins` | int | Number of skin clusters built. |
| `blendShapes` | int | Number of blend shapes built. |
| `warnings` | list of strings | Every warning, in order. |

Texture `status` is one of:

- `linked`: the file exists and is connected.
- `missing`: an external image the file points to was not found. The file node is still
  created with the expected path, so it can be relinked.
- `unsupported`: an image format Maya cannot read (WebP, KTX2/Basis, AVIF). No texture.
- `failed`: an embedded image could not be written to disk. No texture.

### How failure is signalled

- **Import** (`i=True`): `cmds.file` raises `RuntimeError` with the message, and nothing
  is left in the scene.
- **Reference** (`reference=True`): Maya does not raise when a reference fails to load. It
  prints the error and leaves an empty reference node. Always check
  `last_report()['ok']` after a reference call.
- **A missing or unreadable buffer** (`.bin`) is a failure, not a warning: the geometry
  cannot be built without it. It is reported through `error`.

## 5. Coverage

| Input | Result |
|---|---|
| `.gltf` with separate `.bin` and image files | Supported. Images are linked where they are, never copied. |
| `.gltf` with embedded (base64) data | Supported. |
| `.glb` | Supported. Embedded images are extracted to disk. |
| Triangle meshes, strips, fans | Supported, imported as triangles. |
| Points and lines | Skipped with a warning. |
| Skinning | Supported, as joints and skin clusters. |
| Morph targets | Supported, as blend shapes. |
| Node animation (move, rotate, scale) and morph weight animation | Supported, one animation per load. |
| Several animations in one file | The one chosen by `animation` is imported; the rest are ignored. |
| Draco geometry (`KHR_draco_mesh_compression`) | Fails with an error naming the extension. |
| Meshopt geometry (`EXT_meshopt_compression`) | Fails with an error naming the extension. |
| KTX2/Basis, WebP, AVIF textures | The model loads; those textures are skipped with a warning (`status: unsupported`). |
| Cameras and lights | Not imported. |

## 6. Materials

With the default options the plug-in creates one `aiStandardSurface` and one shading group
per glTF material, with `file` and `place2dTexture` nodes for the textures, and assigns
them per face. If Arnold is not available it falls back to Maya's `standardSurface` and
says so in `warnings`; `shaderType` in the report gives the type that was used.

A host that supplies its own material for a model should pass `shader=none`. The plug-in
then builds no shaders, shading groups or texture nodes, and every face stays on
`initialShadingGroup`, ready for the host's assignment.

The plug-in does not create RenderMan (`PxrSurface`) or other third-party renderer
materials.

## 7. Links

- Source and releases: https://github.com/IRONIC3D/i3d-gltf-for-maya
- Documentation and downloads: https://ironic3d.com.au/apps/ironic3d-gltf-for-maya

## 8. Text for a "Read more" panel

> Maya has no built-in glTF importer. To send glTF and GLB files to Maya, install
> IRONIC3D glTF for Maya, a free, open-source plug-in for Maya 2024 to 2027. Download it
> from https://ironic3d.com.au/apps/ironic3d-gltf-for-maya or get the source on GitHub,
> then restart Maya.
