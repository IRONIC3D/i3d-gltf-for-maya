# IRONIC3D glTF for Maya

A pure-Python glTF 2.0 importer for Autodesk Maya. It registers a Maya file translator,
so `.gltf` and `.glb` files work with File > Import, File > Create Reference and `cmds.file`.
No compiled code and no third-party packages (numpy is not required).

Status: **0.5.0, milestone 5 (RenderMan PxrSurface and 3Delight dlPrincipled materials).** Import only.

## Works with Dayum

IRONIC3D glTF for Maya works with [Dayum](https://usedayum.com), a digital asset manager.
When Dayum exports a project to Maya and this plug-in is installed, the project's build script
imports its glTF and GLB assets into the scene or as references, with Arnold or Standard Surface
materials built from the files. The plug-in is a separate, free download; Dayum does not
include it.

Documentation and downloads: https://ironic3d.com.au/apps/ironic3d-gltf-for-maya

## Supported Maya versions

Maya 2024, 2025, 2026 and 2027 on Windows (tested). Maya 2028 beta is run as an early warning.
The code is plain Python 3.10+ and has no platform-specific parts, but macOS and Linux are untested.

## Install

### Release zip (users)

1. Download `i3dGltf-<version>.zip` from https://ironic3d.com.au/apps/ironic3d-gltf-for-maya or the
   GitHub releases page and extract it anywhere (spaces and non-ASCII characters in that path are fine).
2. Drag `install_i3dGltf.py` from the extracted folder onto a Maya viewport and click Install.
3. Maya shows an *Untrusted Plugin Loading* security warning for the new folder, once, in that session:
   click Allow. The plug-in loads at once (no restart). The installer then offers to turn on auto-load.

The installer copies the module to `<Maya user app dir>/modules/i3dGltf/` and writes
`<Maya user app dir>/modules/i3dGltf.mod` (first line `+ i3dGltf <version> ./i3dGltf`). The user app
dir comes from Maya (`cmds.internalVar(userAppDir=True)`, usually `Documents/maya`, which may be inside
OneDrive). Maya 2024-2027 all read the `.mod` files in that version-independent `modules` folder at
start-up without any environment variable, so one install serves every Maya version on the machine.
The installer never changes Maya's security preferences; from the next start Maya finds the plug-in
through its modules folder and loads it without a warning.

* Upgrade: drop the newer installer (an older loaded copy is unloaded and replaced in the running
  session). A downgrade asks for confirmation.
* Uninstall: drop `uninstall_i3dGltf.py` (in the zip and in the installed `i3dGltf` folder). It removes
  exactly the files the installer recorded in `i3dGltf/install_manifest.json`, the `.mod` file and the
  folders left empty, and turns off auto-load in the running Maya version.
* Command line: `mayapy install_i3dGltf.py [--autoload] [--uninstall] [--force] [--json FILE]`.
* Not supported: a Maya user folder whose path contains non-ASCII characters (for example a Windows
  user name with accents). Maya 2024-2026 do not read module files there, and Maya 2027 reads them
  but cannot run a Python plug-in from there (measured with mayapy); the installer says so and stops. Use the developer route below with an
  ASCII folder instead.

### Developer (MAYA_MODULE_PATH)

Add the repository folder to `MAYA_MODULE_PATH` (it contains `i3dGltf.mod`), for example in `Maya.env`:

```
MAYA_MODULE_PATH=C:/path/to/i3d-gltf-for-maya
```

Then load the plug-in from the Plug-in Manager, or:

```python
import maya.cmds as cmds
cmds.loadPlugin("i3dGltf")
```

Build the release zip with `python tools/build_release.py` (writes `dist/i3dGltf-<version>.zip`;
two builds of the same tree are byte-identical).

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
cmds.file(path, i=True, type="IRONIC3D glTF", options="shader=PxrSurface")
cmds.file(path, i=True, type="IRONIC3D glTF", options="animation=Walk;animSampling=bake")
```

Host applications that drive the importer from scripts: see [CONTRACT.md](CONTRACT.md). After any
`cmds.file` call, `i3d_gltf.maya.importer.last_report()` returns a JSON-serialisable dict for that
load (success or failure, roots, materials, textures, animation, skin and blendShape counts,
warnings); check its `ok` after a reference, since Maya does not raise when a reference fails.

### Options

File > Import, File > Create Reference and File > Open (option box, or the Options pane of the file
browser with *IRONIC3D glTF* as the file type) show an options window with every option below, in
Geometry, Materials and Animation sections; hover a control for a one-line explanation. The Shader
menu lists every type with its renderer, marks a renderer that is installed but not loaded yet as
"loaded on import", and marks the ones this session cannot build (for example `openPBRSurface` on
Maya 2024, Arnold types without MtoA, `PxrSurface` without RenderMan for Maya) with the type the import
will use instead; opening the window never loads a renderer plug-in. Maya remembers the last settings (optionVar `IRONIC3D glTFOptions`) for its file
dialogs only; scripted `cmds.file` calls get the defaults plus their own `options`.

From scripts, options are passed as `key=value;key=value` in the `options` flag.

| Option          | Default | Meaning |
|-----------------|---------|---------|
| `scale`         | `1.0`   | Extra scale multiplier. glTF metres always become Maya centimetres (x100); the factor is baked into vertex positions and translations, not put on a root node. |
| `importNormals` | `1`     | Import `NORMAL` as locked user normals. |
| `importColors`  | `1`     | Import `COLOR_n` as colour sets. |
| `shader`        | `aiStandardSurface` | Shader node type for glTF materials: `aiStandardSurface`, `aiOpenPBRSurface`, `openPBRSurface`, `standardSurface`, `PxrSurface` (RenderMan), `dlPrincipled` (3Delight), or `none` (no materials; everything on `initialShadingGroup`). |
| `textureDir`    | *(empty)* | Base folder for textures extracted from GLB files and data URIs. Empty: `<current project>/<sourceImages file rule>/i3dGltf`. A relative path is relative to the project. A per-file subfolder is always added (see Textures). |
| `animation`     | `first` | Which glTF animation to import: `first`, `none`, an index (`0`, `1`, ...) or an animation name. An index or name that does not exist gives a warning and imports the first. |
| `animSampling`  | `keys`  | `keys`: keys exactly at the file's times. `bake`: one key per frame at the scene frame rate, evaluated with the glTF rules (for curves Maya cannot represent exactly, see Animation). |
| `timeRange`     | `1`     | Set the playback and animation range to the imported animation (import and File > Open only, see Animation). |

Unknown keys and invalid values are reported as warnings and the default is used.
Values cannot contain `;`.

### What you get

* The default scene's node hierarchy as transforms (TRS or matrix, decomposed into channels),
  and joints for skeletons (see Skins).
* One Maya mesh per glTF mesh, all triangle primitives merged. Triangles stay triangles:
  no quad merging, no welding, no cleanup. Triangle strips and fans become their triangles.
* `TEXCOORD_n` as UV sets `map1`, `map2`, ... (V flipped), `COLOR_n` as colour sets
  `colorSet1`, ... (RGB or RGBA), normals as locked normals.
* A glTF mesh used by several nodes becomes a Maya instance (one shape, several parents),
  unless it is skinned or has morph targets.
* Node names come from the glTF file (characters outside `A-Z a-z 0-9 _` become `_`,
  unnamed nodes become `node_<index>`), so re-loading a reference re-creates the same
  names and reference edits are re-applied.
* In a Z-up Maya scene each imported root gets a +90 degree X rotation in its
  `offsetParentMatrix`; the glTF transform values stay in the normal channels.
* One Maya material and shading group per glTF material, shared by every mesh that uses it and
  assigned per face (also on instances). Primitives without a material stay on
  `initialShadingGroup`. Shading node names come from the material and image names with the
  same rules, so reference edits on materials (e.g. a changed colour) are re-applied.

### Skins

* Every glTF node a skin uses as a joint becomes a Maya `joint`, with the glTF hierarchy and
  names. Nodes that sit between two joints (in no skin themselves) also become joints, so a
  skeleton is one unbroken joint chain; they are not influences. Nodes above the top joints
  (for example an `Armature` node) stay transforms.
* Joint rotation stays in the `rotate` channels (`jointOrient` and `rotateAxis` are zero), so
  animation maps one to one, and `segmentScaleCompensate` is off (glTF scale semantics: a
  child inherits its parent's scale).
* Joint display radius follows the skeleton size: 1% of the rest-pose extent of the skeleton,
  at most half its median bone length, divided by each joint's world scale (Maya draws the radius
  in joint space). A lone joint gets 1 cm x `scale`.
* One `skinCluster` per skinned shape (`<node>_skinCluster`): classic linear skinning, one
  influence per skin joint in skin order, `bindPreMatrix` = the file's `inverseBindMatrices`
  (translation scaled like everything else), every `JOINTS_n`/`WEIGHTS_n` set (more than 4
  influences), weights normalised on import (normalisation mode interactive, so painting keeps
  them normalised), `deformUserNormals` on so the imported locked normals follow the joints.
  No `dagPose` is kept: it would record the import pose, not the file's bind pose.
* glTF ignores the transform of a skinned mesh's own node. The skinned shape therefore sits
  under a transform with identity TRS and `inheritsTransform` off: the node's own transform
  when it is a plain leaf (no children, not a joint, never animated; its file TRS is dropped),
  otherwise an extra child transform `<node>_skinned`. The mesh is where its joints put it,
  whatever its parents do, and follows the skeleton when the skeleton root moves.
* Skinned and morphed meshes are never Maya instances (one deformer per shape).
* Skins whose joints are outside the imported scene, and skinned meshes without
  `JOINTS_0`/`WEIGHTS_0`, are imported unskinned with a warning.

### Morph targets

* One `blendShape` per morphed shape (`<node>_blendShape`), created before the skinCluster
  (glTF applies morph targets, then skinning). No target meshes are created: the `POSITION`
  deltas (including sparse accessors) are written directly into the blendShape.
* Weights are aliased with the names from `mesh.extras.targetNames` (sanitised, made unique),
  or `target_<i>`. Initial weights: the node's `weights`, else the mesh's, else 0.
* `NORMAL` and `TANGENT` deltas are not imported (the shape keeps its own normals).

### Animation

* One glTF animation per import (`animation` option). Curves are named `<node>_<attribute>`
  (`Hips_rotateX`) and `<node>_<target name>` for morph weights, and connect to the joint or
  transform channels and to the blendShape weights.
* Keys are placed at the file's times in seconds, independent of the scene frame rate (not
  snapped to frames). Translations are scaled like the geometry.
* `STEP` -> stepped keys; `LINEAR` -> linear keys; `CUBICSPLINE` -> the file's in/out tangents
  as fixed, non-weighted Maya tangents (the same cubic Hermite curve, exact).
* Rotations become Euler `rotateX/Y/Z` (rotate order xyz), continuity filtered. `LINEAR`
  rotations use Maya's *Quaternion Slerp* rotation interpolation on the three curves, which
  evaluates exactly like glTF's quaternion slerp (measured to about 1e-6 degrees between keys
  on Maya 2024-2027). `STEP` rotations are exact Euler steps. `CUBICSPLINE` rotations have no
  exact Maya equivalent (glTF interpolates quaternion components and normalises): the keys are
  exact and the Euler tangents follow the glTF curve at each key, but values between keys are
  approximate, with a warning; import with `animSampling=bake` for frame-exact values.
* `timeRange`: an import (or File > Open of a .gltf/.glb) sets the playback and animation
  range to the animation's first and last key, rounded outwards to frames; with repeated
  imports the last one wins. References never change the range: they are read again every
  time the scene opens, and the scene already stores the range the user chose.
* Channels that target nodes outside the imported scene, `KHR_animation_pointer` targets and
  `weights` channels on nodes without morph targets are skipped (warning or info).

### Materials

| Shader | Renderer | Status |
|---|---|---|
| `aiStandardSurface`, `aiOpenPBRSurface` | Arnold | Available |
| `standardSurface`, `openPBRSurface` | Maya | Available |
| `PxrSurface` | RenderMan | Available (0.5.0) |
| `dlPrincipled` | 3Delight | Available (0.5.0) |
| Redshift material | Redshift | Planned |
| V-Ray material | V-Ray | Planned |

The `shader` option picks the node type. Availability is checked at import time (never by Maya
version); if the type is missing, the first available type along this chain is used, with one
warning. The renderer's plug-in (`mtoa`, `RenderMan_for_Maya`, `plugin_3delight_for_maya`) is loaded
automatically when one of its shaders is needed and it is installed; a plug-in that fails to load
counts as missing. The import never changes the scene's current renderer.

| Requested           | Fallback chain |
|---------------------|----------------|
| `aiStandardSurface` | `standardSurface` |
| `aiOpenPBRSurface`  | `openPBRSurface`, `standardSurface` |
| `openPBRSurface`    | `aiOpenPBRSurface`, `standardSurface` (Maya 2024 has no `openPBRSurface`) |
| `PxrSurface`        | `standardSurface` |
| `dlPrincipled`      | `standardSurface` |
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

RenderMan (`PxrSurface`), built like Dayum's own RenderMan materials:

* `PxrTexture` per image (shared like the file nodes), `linearize` on for colour maps (base colour,
  emissive, specular and sheen colour) and off for data maps; the image is linked as it is and
  RenderMan for Maya's texture manager makes the `.tex` (see Textures).
* Metal/roughness through `PxrMetallicWorkflow` (base colour, metallic from B, specular from the IOR)
  into diffuse, specular face and edge colour, with Artistic fresnel (which `PxrMetallicWorkflow`
  requires) and the GGX model; roughness from G into `specularRoughness`. Factors are `PxrArithmetic`
  multiplies.
* Normal map through `PxrNormalMap` (OpenGL orientation, `bumpScale` = normal scale) into
  `bumpNormal`; emissive into `glowColor` x `glowGain`; alpha into `presence` (`MASK` through
  `PxrThreshold`); `doubleSided` also turns on the lobes' double-sided switches.
* Every `PxrTexture` has a `PxrManifold2D` on its manifold port (RenderMan's recommended network), so
  repeat, offset and rotation can be edited there. It carries `KHR_texture_transform` and the UV set
  (`name_uvSet` = `map2`, ...), with the same image orientation as Maya's file node. Sampler wrap modes set `swrap`/`twrap` (RenderMan 28).
* Clearcoat, sheen (`fuzz`), transmission (`refraction`, `glassIor`) and spec-gloss are approximated.

3Delight (`dlPrincipled`): the same Maya file / place2dTexture networks as for the other shaders
(3Delight renders those nodes itself), into `color`, `metallic`, `roughness`, `specular_level` (from
the IOR), `incandescence`, `opacity` and the normal map input (Normal Map (OpenGL), intensity = normal
scale).

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

Note for RenderMan: the importer writes no `.tex` files, but RenderMan for Maya's texture manager
converts every image when RenderMan renders (and, in an interactive session where RenderMan is the
current renderer, after a file is imported or opened). It writes `<image>.<ext>.tex` next to each
source image, and uses its fallback folder `<project>/renderman/textures` only when that folder is
not writable or *Always Fallback* is on (Preferences > RenderMan > Texture Manager). For read-only
asset libraries, turn *Always Fallback* on.

Warnings and errors go to the Script Editor. If a file cannot be read, `cmds.file(i=True)`
raises and nothing is left in the scene. For `reference=True`, Maya itself does not raise
when a reference fails to load; it prints the error and keeps an empty reference node.

## Known limitations

Geometry

* glTF stores triangles only, so meshes arrive as triangles. They are not merged into quads.
* Triangles that use the same vertex twice are dropped, with a warning. Maya cannot store them.
* Points and lines are skipped, with a warning.
* Draco and meshopt compressed files (`KHR_draco_mesh_compression`,
  `EXT_meshopt_compression`) do not load. The error names the extension.
* Cameras and lights in the file are not imported.

Materials and textures

* Shaders are built for Arnold, RenderMan (`PxrSurface`), 3Delight (`dlPrincipled`) and Maya's own
  Standard Surface and OpenPBR nodes. Redshift and V-Ray materials are not built yet.
* Occlusion textures are not connected: these shaders have no slot for them.
* `PxrSurface`: with RenderMan for Maya before 28, `PxrTexture` has no wrap mode setting, so textures
  set to clamp or mirror repeat instead (warning). Sheen roughness, the specular colour of
  `KHR_materials_specular` and `KHR_materials_volume` depth have no `PxrSurface` equivalent.
* `dlPrincipled`: clearcoat is approximated with the coating thickness; thin-walled transmission,
  volume depth and specular colour have no equivalent.
* RenderMan for Maya writes `.tex` files next to the source textures when it converts them, unless
  *Always Fallback* is on (see Textures). The importer itself never writes them.
* Vertex colours are imported as colour sets but do not tint the material.
* WebP, KTX2/Basis and AVIF textures are skipped, with a warning. The model still loads.
* Textures set to clamp at their edges still repeat in Arnold when a texture transform moves
  the UVs outside the 0 to 1 range.
* Arnold writes `.tx` files next to the source textures when it renders, unless Auto-TX is
  turned off in the render settings. This also happens in a read-only asset library.

Animation and deformation

* One animation is imported per load. A file with several animations needs the `animation`
  option to choose one; importing all of them as Time Editor clips is not available yet.
* Rotations stored with cubic-spline interpolation are exact at the keys and approximate
  between them, with a warning. Use `animSampling=bake` for exact values on every frame.
* Morph targets move points only. Normal and tangent changes stored with a target are not
  imported; Maya recalculates the normals.
* `KHR_animation_pointer` (animated material and other properties) is not supported.

Scenes and references

* A referenced glTF file is read again each time the scene opens, so the plug-in must be
  installed on every machine that opens the scene.
* When a reference fails to load, Maya does not raise an error; it leaves an empty reference
  node. Scripts should check `importer.last_report()`.
* In a Z-up scene the imported top-level nodes are rotated through `offsetParentMatrix`, which
  some other tools ignore.
* An import sets the playback range to the animation. Use `timeRange=0` to leave it alone.

General

* Import only. There is no exporter yet.
* The options window does not list the animations in the file; type a name or index, or pick
  `first` / `none`.
* The installer does not support a Maya user folder with non-ASCII characters in its path (see Install).
* Tested on Windows only.

## Tests

```
python tests/run_all.py          # core tests with this Python, then core + Maya tests on every installed mayapy
mayapy tests/run_suite.py --maya # one Maya version
python tests/run_suite.py        # core tests only, no Maya needed
```

The Maya tests include the release installer end to end (`test_maya_installer.py`: fresh install,
upgrade over a loaded older copy, downgrade refusal, uninstall, then a new mayapy that finds the module
with no `MAYA_MODULE_PATH`). GUI checks need a desktop and are run separately:
`python tests/gui/run_gui_tests.py 2024 2027` drives isolated GUI Maya sessions (own `MAYA_APP_DIR`,
no userSetup, killed by PID on timeout) through the options window, Maya's option boxes and file
browser, and the drag-and-drop installer, and saves screenshots of Maya's own windows.

Test fixtures are generated in code. `tests/perf_large_mesh.py` times a 1M-triangle import,
`tests/perf_skinned.py` a 100k-vertex skinned and animated import, and
`mayapy tests/import_samples.py <folder> [--shader X] [--texture-dir D] [--no-reference]` imports
and references every file in a folder of sample models, reports materials and textures per file,
and tallies warnings and the glTF features used (it never writes into `<folder>`).

Skins, morph targets and animation are checked against `tests/gltf_eval.py`, an independent
pure-Python implementation of the glTF spec (node matrices from animated TRS, morph blending,
linear blend skinning). `mayapy tests/compare_samples.py <files or folders> [--all-animations]`
imports each file and reports the largest difference between Maya's deformed world-space
vertices and the reference at several times, including between keys.

## License and trademarks

MIT, see [LICENSE](LICENSE).

glTF and the glTF logo are trademarks of the Khronos Group Inc. This project is not affiliated with
or endorsed by the Khronos Group. Maya is a trademark of Autodesk, Inc.
