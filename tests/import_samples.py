"""Headless real-world check (not part of the unit suite).

    mayapy tests/import_samples.py <folder> [--shader aiStandardSurface] [--texture-dir DIR]
                                            [--no-reference] [--json FILE]

Imports (and references, unless --no-reference) every .gltf/.glb under <folder> (e.g. models
downloaded from KhronosGroup/glTF-Sample-Assets into the git-ignored _samples/ folder) with the
exact host-application cmds.file calls, and prints one line per file: materials created,
textures linked / missing / skipped, warnings. At the end it prints a tally of distinct
warnings and of the glTF extensions and material features the files use.

Nothing is written into <folder>: embedded textures go to --texture-dir (default: a new
temporary folder) and the Maya project is switched to a temporary folder for the run.
"""

import argparse
import collections
import json
import os
import re
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
if "--use-installed" not in sys.argv:
    sys.path.insert(0, os.path.join(REPO, "scripts"))
sys.path.insert(0, HERE)

T = "IRONIC3D glTF"


DROPPED_PREFIX = "Material features with no equivalent were skipped: "


def warning_keys(text):
    """Group similar warnings: numbers, quoted names and paths become placeholders. The
    one-per-import 'skipped features' warning is split into one key per feature."""
    if text.startswith(DROPPED_PREFIX):
        return ["skipped feature: " + re.sub(r" \(\d+ materials?\)$", "", part)
                for part in text[len(DROPPED_PREFIX):].split("; ")]
    return [warning_key(text)]


def warning_key(text):
    text = re.sub(r"'[^']*'", "'...'", text)
    text = re.sub(r"[A-Za-z]:[\\/][^\s;,)]*", "<path>", text)
    text = re.sub(r"\d+(\.\d+)?", "N", text)
    return text[:220]


def material_features(doc):
    """Feature names used by the document's materials/textures (for the tally)."""
    feats = set()
    for name in doc.extensions_used:
        feats.add("ext:" + name)
    for m in doc.materials:
        feats.add("alphaMode:" + str(m.alpha_mode))
        if m.double_sided:
            feats.add("doubleSided")
        for attr in ("base_color_texture", "metallic_roughness_texture", "normal_texture", "occlusion_texture",
                     "emissive_texture"):
            info = getattr(m, attr)
            if info is not None:
                feats.add(attr)
                if info.tex_coord:
                    feats.add("texCoord>0")
                if "KHR_texture_transform" in info.extensions:
                    feats.add("KHR_texture_transform (used)")
        if any(m.emissive_factor):
            feats.add("emissiveFactor")
        if m.normal_texture is not None and m.normal_texture.scale != 1.0:
            feats.add("normal scale != 1")
        for e in m.extensions:
            feats.add("material ext:" + e)
    for s in doc.samplers:
        for w in (s.wrap_s, s.wrap_t):
            if w != 10497:
                feats.add("sampler wrap %d" % w)
    for im in doc.images:
        if im.buffer_view is not None:
            feats.add("image: GLB bufferView")
        elif im.uri and im.uri.startswith("data:"):
            feats.add("image: data URI")
        elif im.uri:
            feats.add("image: external file")
        ext = (im.mime_type or os.path.splitext(im.uri or "")[1] or "?").lower()
        feats.add("image type: " + ext)
    for mesh in doc.meshes:
        for p in mesh.primitives:
            if "COLOR_0" in p.attributes and p.material is not None:
                feats.add("COLOR_0 on a material primitive")
    if doc.skins:
        feats.add("skins")
    if doc.animations:
        feats.add("animations")
        if len(doc.animations) > 1:
            feats.add("several animations")
        for a in doc.animations:
            for smp in a.samplers:
                feats.add("interpolation " + smp.interpolation)
    if any(p.targets for mesh in doc.meshes for p in mesh.primitives):
        feats.add("morph targets")
    return feats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--shader", default="aiStandardSurface")
    ap.add_argument("--texture-dir", default=None)
    ap.add_argument("--no-reference", action="store_true")
    ap.add_argument("--json", default=None)
    ap.add_argument("--no-autotx", action="store_true", help="turn off Arnold auto-TX before importing")
    ap.add_argument("--use-installed", action="store_true",
                    help="load the plug-in Maya finds by itself (e.g. an installed release), not this repository")
    args = ap.parse_args()
    folder = os.path.abspath(args.folder)
    tex_dir = os.path.abspath(args.texture_dir or tempfile.mkdtemp(prefix="i3d_samples_tex_"))
    if os.path.normcase(tex_dir).startswith(os.path.normcase(folder)):
        sys.exit("--texture-dir must not be inside the sample folder")

    os.environ.setdefault("MAYA_SKIP_USERSETUP_PY", "1")
    if not args.use_installed and REPO not in os.environ.get("MAYA_MODULE_PATH", ""):
        os.environ["MAYA_MODULE_PATH"] = REPO + os.pathsep + os.environ.get("MAYA_MODULE_PATH", "")
    import maya.standalone
    maya.standalone.initialize(name="python")
    import maya.cmds as cmds
    cmds.loadPlugin("i3dGltf")
    from i3d_gltf.maya import importer
    plugin_path = cmds.pluginInfo("i3dGltf", q=True, path=True)
    print("PLUGIN %s %s  package %s" % (cmds.pluginInfo("i3dGltf", q=True, version=True), plugin_path,
                                        os.path.dirname(importer.__file__)))
    if args.use_installed and os.path.normcase(os.path.abspath(plugin_path)).startswith(os.path.normcase(REPO)):
        sys.exit("--use-installed, but the plug-in was loaded from the repository")
    if args.no_autotx:
        try:
            cmds.loadPlugin("mtoa", quiet=True)
            import mtoa.core
            mtoa.core.createOptions()
            cmds.setAttr("defaultArnoldRenderOptions.autotx", False)
            print("Arnold auto-TX off")
        except Exception as exc:
            print("Could not turn off Arnold auto-TX: %s" % exc)
    project = tempfile.mkdtemp(prefix="i3d_samples_proj_")
    cmds.workspace(project, openWorkspace=True)
    options = "shader=%s;textureDir=%s" % (args.shader, tex_dir.replace("\\", "/"))

    files = []
    for root, _, names in os.walk(folder):
        files += [os.path.join(root, n) for n in sorted(names) if n.lower().endswith((".gltf", ".glb"))]
    print("MAYA %s  shader=%s  textureDir=%s" % (cmds.about(version=True), args.shader, tex_dir))
    warnings = collections.Counter()
    features = collections.Counter()
    rows = []
    failures = 0
    for path in sorted(files):
        rel = os.path.relpath(path, folder)
        line = []
        row = {"file": rel}
        for mode in (("import",) if args.no_reference else ("import", "reference")):
            cmds.file(new=True, force=True)
            before = set(cmds.ls(assemblies=True))
            t = time.time()
            try:
                if mode == "import":
                    cmds.file(path, i=True, type=T, namespace="smp", ignoreVersion=True,
                              mergeNamespacesOnClash=False, preserveReferences=True, options=options)
                else:
                    cmds.file(path, reference=True, type=T, namespace="smp", ignoreVersion=True,
                              mergeNamespacesOnClash=False, options=options)
            except RuntimeError as exc:
                line.append("%s: FAILED (%s)" % (mode, str(exc).splitlines()[0][:160]))
                row[mode] = "FAILED"
                failures += 1
                continue
            new = [a for a in cmds.ls(assemblies=True) if a not in before]
            err = importer.last_error()
            if err is not None:
                line.append("%s: FAILED (%s)" % (mode, err[1][:160]))
                row[mode] = "FAILED"
                failures += 1
                continue
            meshes = cmds.ls("smp:*", type="mesh", noIntermediate=True) or []
            faces = sum(cmds.polyEvaluate(m, face=True) for m in meshes)
            res = importer.last_result()
            linked, missing, skipped = res.texture_stats()
            ws = sorted(set(res.reporter.warnings))
            line.append("%s: ok %.2fs roots=%d meshes=%d faces=%d shader=%s materials=%d textures=%d "
                        "missing=%d skipped=%d skins=%d blendShapes=%d curves=%d warnings=%d"
                        % (mode, time.time() - t, len(new), len(meshes), faces, res.shader_type,
                           len(res.shaders), linked, missing, skipped, len(res.skin_clusters),
                           len(res.blend_shapes), len(res.anim_curves), len(ws)))
            row[mode] = "ok"
            if mode == "import":
                row.update(materials=len(res.shaders), textures=linked, missing=missing, skipped=skipped,
                           warnings=ws)
                for w in ws:
                    for key in warning_keys(w):
                        warnings[key] += 1
                    line.append("    warning: %s" % w[:300])
                for f in material_features(res.prepared.doc):
                    features[f] += 1
        rows.append(row)
        print("%-60s %s" % (rel, line[0]))
        for extra in line[1:]:
            print("%-60s %s" % ("", extra))
        sys.stdout.flush()

    print("")
    print("FILES %d  FAILURES %d" % (len(files), failures))
    print("DISTINCT WARNINGS (files):")
    for k, v in warnings.most_common():
        print("  %4d  %s" % (v, k))
    print("FEATURES (files using them):")
    for k, v in sorted(features.items(), key=lambda kv: (-kv[1], kv[0])):
        print("  %4d  %s" % (v, k))
    if args.json:
        with open(args.json, "w") as f:
            json.dump({"rows": rows, "warnings": dict(warnings), "features": dict(features),
                       "failures": failures}, f, indent=1)
    sys.stdout.flush()
    cmds.file(new=True, force=True)
    from run_suite import hard_exit
    hard_exit(1 if failures else 0)


if __name__ == "__main__":
    main()
