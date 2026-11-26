# Integration contract

For host applications that detect and drive IRONIC3D glTF for Maya from their own
scripts. Everything here is stable from version 0.3.0 onwards unless a later entry in
this file says otherwise.

## Change notes

A dated note is added here, newest first, whenever something a host relies on changes
(plug-in name, import call, options, report keys, module locations).

- **2026-11-26, version 0.5.0.**
  - The `shader` option takes two new values: `PxrSurface` (RenderMan for Maya) and
    `dlPrincipled` (3Delight for Maya) (sections 3 and 6). With either, `shaderType` in the
    report is `PxrSurface` / `dlPrincipled` when that type was built, and `materials[].shader`
    is the PxrSurface / dlPrincipled node. When the renderer is not installed, or its plug-in
    fails to load, the import falls back to `standardSurface` with a warning and `shaderType`
    says `standardSurface`.
  - The change is additive: no other option, default, report key or module location changed,
    and the minimum version for everything that existed before stays 0.3.0. A host that
    passes `shader=PxrSurface` or `shader=dlPrincipled` must require **0.5.0 or later**: older
    versions reject the value with a warning and build the default `aiStandardSurface`.
- **2026-11-25, version 0.4.0.**
  - New release package `i3dGltf-<version>.zip` with a drag-and-drop installer. The
    installer writes the module file to `<Maya user app dir>/modules/i3dGltf.mod` and the
    module folder to `<Maya user app dir>/modules/i3dGltf/` (section 1). The module file's
    path field is now relative: `+ i3dGltf 0.4.0 ./i3dGltf` (relative to the folder that
    holds the .mod file).
  - The translator now has an options window (File > Import / Create Reference / Open
    option boxes). It edits the same `options` keys; no option was added, removed or
    renamed, and no default changed. Maya remembers the window's last settings in the
    optionVar `IRONIC3D glTFOptions`; that only affects the file dialogs, never a
    `cmds.file` call, with or without `options` (section 3).
  - In the Maya session that runs the installer, Maya shows its "Untrusted Plugin Loading"
    warning once for the new folder; from the next start the plug-in loads through the
    module without a warning (section 1).
  - Joints get a display radius relative to the skeleton size instead of Maya's default.
  - Nothing a 0.3.0 host relies on changed; the minimum version stays 0.3.0.
- **2026-11-25, version 0.3.0:** first version of this contract.

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

Maya finds the plug-in through `i3dGltf.mod`.

**Release installer (the normal install, from 0.4.0).** `install_i3dGltf.py` from the
release zip writes

| | Maya expression | Typical Windows path |
|---|---|---|
| Module file | `cmds.internalVar(userAppDir=True) + 'modules/i3dGltf.mod'` | `C:\Users\<name>\Documents\maya\modules\i3dGltf.mod` |
| Module folder | `cmds.internalVar(userAppDir=True) + 'modules/i3dGltf/'` | `C:\Users\<name>\Documents\maya\modules\i3dGltf\` |

With Documents redirected into OneDrive, Maya's user app dir (and so these files) is
`C:\Users\<name>\OneDrive\Documents\maya\modules\`; the installer never builds the path
itself, it uses what Maya reports. With `MAYA_APP_DIR` set the files are under
`%MAYA_APP_DIR%\modules\` (that case is what the installer tests run). The `modules` folder
directly under the user app dir is version independent: Maya 2024, 2025, 2026 and 2027
all read it at start-up without any environment variable (verified on each), so one
install serves every Maya version. The module folder also holds `install_manifest.json`
(the list of installed files, used by the uninstaller) and the installer scripts.
The installer refuses a user app dir whose path is not plain ASCII: Maya cannot load the
plug-in from there.

Maya's plug-in security (Preferences > Security, on by default in 2024-2027) trusts plug-in
folders that come from a module found at start-up. So `cmds.loadPlugin('i3dGltf')` in any
session started after the install loads without a prompt. Only the session that ran the
installer, where the folder is not yet a module, shows Maya's "Untrusted Plugin Loading"
warning once (Allow / Deny). The installer does not change the security preferences.

A host can detect an install without running Maya by looking for `i3dGltf.mod` in, first,
the user app dir's `modules` folder above. `<Documents>` is the user's Documents folder as
Windows reports it (`SHGetKnownFolderPath(FOLDERID_Documents)`), which is often redirected;
do not assume `%USERPROFILE%\Documents`. Manual installs can also put the file in Maya's
other module folders:

- `<Documents>\maya\<version>\modules\`
- `%CommonProgramFiles%\Autodesk Shared\Modules\maya\<version>\`
- every folder listed in the `MAYA_MODULE_PATH` environment variable

The first line of the file is always

    + i3dGltf <version> <path>

so the installed version can be read from the file itself. The installer writes the path
relative to the folder holding the `.mod` file (`+ i3dGltf 0.4.0 ./i3dGltf`); resolve a
relative path against that folder. Manual installs may use an absolute path.

Finding the file is a hint, not proof. The authority is Maya: `cmds.loadPlugin('i3dGltf')`
either succeeds or raises.

## 2. Version

```python
cmds.pluginInfo('i3dGltf', query=True, version=True)   # '0.5.0', after the plug-in is loaded
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
| `shader` | `aiStandardSurface`, `aiOpenPBRSurface`, `openPBRSurface`, `standardSurface`, `PxrSurface` (0.5.0+), `dlPrincipled` (0.5.0+), `none` | `aiStandardSurface` | Shader type to build. `none` builds no materials at all. |
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
- **The options window does not affect scripted calls.** Maya's file dialogs remember the
  window's last settings in the optionVar `IRONIC3D glTFOptions`; `cmds.file` does not
  read it, so a call gets the defaults above plus its own `options` (tested).
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

From 0.5.0, `shader=PxrSurface` builds RenderMan for Maya materials and
`shader=dlPrincipled` 3Delight for Maya materials:

| | `PxrSurface` (RenderMan) | `dlPrincipled` (3Delight) |
|---|---|---|
| Renderer plug-in | `RenderMan_for_Maya` | `plugin_3delight_for_maya` |
| Textures | `PxrTexture` with a `PxrManifold2D` on its manifold port, `linearize` on for colour maps (base colour, emissive), off for data maps (metallic-roughness, normal) | Maya `file` + `place2dTexture`, colour space sRGB / Raw as for the other shaders |
| Metal/roughness | `PxrMetallicWorkflow` into diffuse / specular face / edge colour; Artistic fresnel, GGX | `metallic`, `roughness` |
| Normal map | `PxrNormalMap` (OpenGL, `bumpScale` = normal scale) into `bumpNormal` | `disp_normal_bump_value`, type Normal Map (OpenGL), intensity = normal scale |
| Alpha | `presence` (MASK through `PxrThreshold`) | `opacity` (MASK through `condition`) |

The renderer's plug-in is loaded by the import when it is installed but not loaded, as
`mtoa` is for the Arnold types. If it is not installed, fails to load, or loads without
the node type, the import uses `standardSurface` with a warning; `shaderType` gives the
type that was used. The plug-in never changes the scene's current renderer or render
settings.

Texture conversion: the plug-in links image files as they are and writes no `.tex` files.
RenderMan for Maya's texture manager converts the images when RenderMan renders (and, in an
interactive session with RenderMan as the current renderer, after a file is imported or
opened). By default it writes `<image>.<ext>.tex` next to each source image, and only uses its
fallback folder (`<project>/renderman/textures`) when that folder is not writable or the
RenderMan preference *Always Fallback* (Texture Manager) is on. For read-only asset libraries,
turn that preference on.

Other renderers (Redshift, V-Ray) are not built.

## 7. Links

- Source and releases: https://github.com/IRONIC3D/i3d-gltf-for-maya
- Documentation and downloads: https://ironic3d.com.au/apps/ironic3d-gltf-for-maya

## 8. Text for a "Read more" panel

> Maya has no built-in glTF importer. To send glTF and GLB files to Maya, install
> IRONIC3D glTF for Maya, a free, open-source plug-in for Maya 2024 to 2027: download the
> zip from https://ironic3d.com.au/apps/ironic3d-gltf-for-maya or from the releases on
> https://github.com/IRONIC3D/i3d-gltf-for-maya, unzip it and drag install_i3dGltf.py onto
> the Maya viewport. It loads straight away, without restarting Maya.
