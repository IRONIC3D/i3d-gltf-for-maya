"""Build the release package dist/i3dGltf-<version>.zip from the repository.

    python tools/build_release.py [--out-dir DIR] [--list]

The zip is deterministic: entries are sorted, timestamps and permissions are fixed, text
files get LF line endings, and Python caches are left out, so two builds of the same tree
are byte-identical. Contents (paths inside the zip):

    install_i3dGltf.py           drag-and-drop / mayapy installer
    uninstall_i3dGltf.py         drag-and-drop uninstaller
    README.txt                   end-user instructions
    LICENSE
    i3dGltf.mod                  "+ i3dGltf <version> ./i3dGltf" (for a manual MAYA_MODULE_PATH install)
    i3dGltf/plug-ins/...         the module folder the installer copies
    i3dGltf/scripts/...
    i3dGltf/install_i3dGltf.py   (so the installed folder can uninstall itself)
    i3dGltf/uninstall_i3dGltf.py

Tests, sample models and development files are not included.
"""

import argparse
import hashlib
import io
import os
import re
import sys
import zipfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAME = "i3dGltf"
FIXED_TIME = (2020, 1, 1, 0, 0, 0)
TEXT_EXT = (".py", ".mel", ".txt", ".md", ".mod", ".json")


def version():
    with open(os.path.join(REPO, "scripts", "i3d_gltf", "__init__.py"), encoding="utf-8") as f:
        v = re.search(r'^VERSION\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"', f.read(), re.M).group(1)
    with open(os.path.join(REPO, NAME + ".mod"), encoding="utf-8") as f:
        mod_version = f.readline().split()[2]
    if mod_version != v:
        raise SystemExit("Version mismatch: i3d_gltf.VERSION is %s but %s.mod says %s" % (v, NAME, mod_version))
    return v


def _read(path):
    with open(path, "rb") as f:
        data = f.read()
    if path.endswith(TEXT_EXT) or os.path.basename(path) == "LICENSE":
        data = data.replace(b"\r\n", b"\n")
    return data


def _tree(rel_root):
    out = []
    base = os.path.join(REPO, rel_root)
    for root, dirs, files in os.walk(base):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__" and not d.startswith("."))
        for name in sorted(files):
            if name.endswith((".pyc", ".pyo")) or name.startswith("."):
                continue
            full = os.path.join(root, name)
            out.append((os.path.relpath(full, REPO).replace("\\", "/"), full))
    return out


def entries(v):
    """[(path in zip, bytes)] sorted by path."""
    items = {}
    for zpath, full in _tree("plug-ins") + _tree("scripts"):
        items["%s/%s" % (NAME, zpath)] = _read(full)
    for script in ("install_i3dGltf.py", "uninstall_i3dGltf.py"):
        data = _read(os.path.join(REPO, "installer", script))
        items[script] = data
        items["%s/%s" % (NAME, script)] = data
    with open(os.path.join(REPO, "installer", "README.txt"), encoding="utf-8") as f:
        readme = f.read().replace("{version}", v)
    items["README.txt"] = readme.replace("\r\n", "\n").encode("utf-8")
    items["LICENSE"] = _read(os.path.join(REPO, "LICENSE"))
    items[NAME + ".mod"] = ("+ %s %s ./%s\n" % (NAME, v, NAME)).encode("ascii")
    return sorted(items.items())


def build(out_dir=None):
    v = version()
    out_dir = out_dir or os.path.join(REPO, "dist")
    os.makedirs(out_dir, exist_ok=True)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for zpath, data in entries(v):
            info = zipfile.ZipInfo(zpath, date_time=FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 0
            info.external_attr = 0o644 << 16
            zf.writestr(info, data, compresslevel=9)
    path = os.path.join(out_dir, "%s-%s.zip" % (NAME, v))
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(buf.getvalue())
    os.replace(tmp, path)
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()
    path = build(args.out_dir)
    with open(path, "rb") as f:
        data = f.read()
    print("%s  %d bytes  sha256 %s" % (path, len(data), hashlib.sha256(data).hexdigest()))
    if args.list:
        with zipfile.ZipFile(path) as zf:
            for i in zf.infolist():
                print("  %8d  %s" % (i.file_size, i.filename))


if __name__ == "__main__":
    sys.exit(main())
