IRONIC3D glTF for Maya {version}
===============================

A free glTF 2.0 importer for Autodesk Maya 2024, 2025, 2026 and 2027 (Windows tested).
It adds .gltf and .glb to File > Import, File > Create Reference and File > Open.

Install
-------
1. Extract this zip anywhere (any folder name works).
2. Start Maya and drag install_i3dGltf.py from the extracted folder onto the viewport.
3. Click Install. Maya then shows an "Untrusted Plugin Loading" security warning for
   the new folder: click Allow. The plug-in is loaded straight away; no restart is needed.
   This warning appears only in the session that runs the installer; from the next start
   Maya finds the plug-in in its modules folder and loads it without asking.
4. Answer whether Maya should load it automatically at start-up.

Not supported: a Maya user folder with non-ASCII characters in its path (for example an
accented Windows user name); Maya cannot load the plug-in from there and the installer
stops with an explanation.

One install serves every Maya version on the computer. The files go to the "modules"
folder of your Maya user folder, usually

    Documents\maya\modules\i3dGltf.mod
    Documents\maya\modules\i3dGltf\

(Documents may be inside OneDrive). Dropping a newer version's installer upgrades it.

Command line: mayapy install_i3dGltf.py [--autoload] [--uninstall] [--force]

Uninstall
---------
Drag uninstall_i3dGltf.py onto the Maya viewport (it is in this zip and in the installed
i3dGltf folder). It removes exactly the files the installer created.

Use
---
File > Import (or Create Reference), choose a .gltf or .glb file. The option box shows
the import options: scale, normals, vertex colours, shader type, texture folder and
animation settings.

Documentation, source and support:
  https://ironic3d.com.au/apps/ironic3d-gltf-for-maya
  https://github.com/IRONIC3D/i3d-gltf-for-maya

MIT licence, see LICENSE. glTF and the glTF logo are trademarks of the Khronos Group Inc.
Maya is a trademark of Autodesk, Inc.
