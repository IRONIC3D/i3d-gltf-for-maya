"""glTF images: locate them, recognise their format, extract embedded ones. No Maya imports.

* External images (relative or ``file://`` URIs) are used in place: their absolute path is
  returned, nothing is copied.
* Embedded images (GLB bufferViews, ``data:`` URIs) are written to an extraction folder
  chosen by the caller. File names come from the image name (or ``image_<index>``) plus the
  extension of the detected format, made unique case-insensitively in image index order,
  so the same file always extracts to the same names. A file whose content is already
  identical is not rewritten (references re-import on every scene open).
* Formats Maya's file node cannot read (WebP, KTX2/Basis, AVIF) are reported, not linked.

Nothing here raises for a bad image: problems become warnings and a status per image.
"""

import hashlib
import os
import re
import struct
from dataclasses import dataclass
from typing import Optional

from .diagnostics import GltfError
from .reader import decode_data_uri, resolve_uri_path

OK = "ok"                    # usable file on disk
MISSING = "missing"          # external file not found (still linked, so it can be relinked)
UNSUPPORTED = "unsupported"  # format Maya's file node cannot read
FAILED = "failed"            # embedded data could not be decoded or written

# format id -> (extension, readable by Maya's file node)
FORMATS = {
    "png": (".png", True),
    "jpeg": (".jpg", True),
    "bmp": (".bmp", True),
    "gif": (".gif", True),
    "tiff": (".tif", True),
    "exr": (".exr", True),
    "hdr": (".hdr", True),
    "dds": (".dds", True),
    "tga": (".tga", True),
    "psd": (".psd", True),
    "webp": (".webp", False),
    "ktx2": (".ktx2", False),
    "avif": (".avif", False),
}

_MIME = {
    "image/png": "png", "image/jpeg": "jpeg", "image/jpg": "jpeg", "image/bmp": "bmp",
    "image/gif": "gif", "image/tiff": "tiff", "image/x-exr": "exr", "image/vnd.radiance": "hdr",
    "image/vnd-ms.dds": "dds", "image/x-dds": "dds", "image/x-tga": "tga", "image/x-targa": "tga",
    "image/vnd.adobe.photoshop": "psd", "image/webp": "webp", "image/ktx2": "ktx2", "image/avif": "avif",
}

_EXT = {
    ".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg", ".bmp": "bmp", ".gif": "gif", ".tif": "tiff",
    ".tiff": "tiff", ".exr": "exr", ".hdr": "hdr", ".dds": "dds", ".tga": "tga", ".psd": "psd",
    ".webp": "webp", ".ktx2": "ktx2", ".basis": "ktx2", ".avif": "avif",
}

FORMAT_NAMES = {"webp": "WebP", "ktx2": "KTX2/Basis Universal", "avif": "AVIF"}

#: How much of an external file is read to recognise it (PNG alpha needs the chunks before IDAT).
_HEAD_BYTES = 1 << 16


def sniff_format(head):
    """Recognise an image from its first bytes. Returns a format id or None."""
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if head[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[:12] == b"\xabKTX 20\xbb\r\n\x1a\n":
        return "ktx2"
    if head[4:8] == b"ftyp" and head[8:12] in (b"avif", b"avis"):
        return "avif"
    if head[:2] == b"BM":
        return "bmp"
    if head[:4] in (b"GIF8",):
        return "gif"
    if head[:4] in (b"II*\0", b"MM\0*"):
        return "tiff"
    if head[:4] == b"\x76\x2f\x31\x01":
        return "exr"
    if head[:2] == b"#?":
        return "hdr"
    if head[:4] == b"DDS ":
        return "dds"
    if head[:4] == b"8BPS":
        return "psd"
    return None


def png_has_alpha(data):
    """True/False for a PNG (colour type with alpha, or a tRNS chunk), None if unreadable."""
    if data[:8] != b"\x89PNG\r\n\x1a\n" or len(data) < 33:
        return None
    color_type = data[25]
    if color_type in (4, 6):
        return True
    pos = 8
    while pos + 8 <= len(data):
        length, kind = struct.unpack_from(">I4s", data, pos)
        if kind == b"tRNS":
            return True
        if kind in (b"IDAT", b"IEND"):
            return False
        pos += 12 + length
    return None  # ran out of header bytes before IDAT


def has_alpha(fmt, head):
    """Whether the image can carry an alpha channel: False only when known not to."""
    if fmt == "jpeg":
        return False
    if fmt == "png":
        result = png_has_alpha(head)
        return True if result is None else result
    return True


@dataclass
class ImageFile:
    index: int
    status: str
    path: Optional[str] = None       # absolute path to link (also for MISSING)
    format: Optional[str] = None
    embedded: bool = False
    has_alpha: bool = True
    written: bool = False            # extracted in this import (False if already identical)
    message: str = ""


def source_hash(path):
    """Short stable hash of the absolute source path (case-insensitive on Windows)."""
    norm = os.path.normcase(os.path.abspath(path))
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()[:8]


def extraction_subfolder(source_path):
    """``<file stem>_<hash>``: per source file, stable across imports and sessions."""
    from .names import sanitize
    stem = os.path.splitext(os.path.basename(source_path or "embedded"))[0]
    return "%s_%s" % (sanitize(stem, "gltf"), source_hash(source_path or stem))


_FILE_INVALID = re.compile(r"[^A-Za-z0-9_.-]")


def _file_stem(image, index):
    name = image.name if isinstance(image.name, str) else ""
    stem, ext = os.path.splitext(name)
    if ext.lower() in _EXT:
        name = stem
    name = _FILE_INVALID.sub("_", name).strip("._")
    return name or "image_%d" % index


def embedded_file_names(doc):
    """{image index: file stem} for every embedded image, unique case-insensitively."""
    used = set()
    out = {}
    for i, image in enumerate(doc.images):
        if image.uri is not None and not image.uri.startswith("data:"):
            continue
        stem = _file_stem(image, i)
        candidate = stem
        if candidate.lower() in used:
            candidate = "%s_%d" % (stem, i)
        n = 1
        while candidate.lower() in used:
            candidate = "%s_%d_%d" % (stem, i, n)
            n += 1
        used.add(candidate.lower())
        out[i] = candidate
    return out


def _read_head(path):
    with open(path, "rb") as f:
        return f.read(_HEAD_BYTES)


def _write_if_changed(path, data):
    """Write ``data`` unless the file already has exactly this content. Returns True if written."""
    try:
        if os.path.getsize(path) == len(data):
            with open(path, "rb") as f:
                if f.read() == data:
                    return False
    except OSError:
        pass
    tmp = path + ".i3dtmp"
    with open(tmp, "wb") as f:
        f.write(data)
    try:
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return True


def resolve_images(doc, reader, indices, extract_dir, reporter):
    """Locate/extract the images in ``indices``. Returns {index: ImageFile}.

    ``reader`` is an AccessorReader (for GLB bufferViews). ``extract_dir`` is where embedded
    images go; it is created if needed. Failures are warnings, never exceptions.
    """
    out = {}
    names = None
    write_error = None
    for i in sorted(indices):
        image = doc.images[i]
        uri = image.uri
        if uri is not None and not uri.startswith("data:"):
            try:
                path = resolve_uri_path(uri, doc.base_dir)
            except GltfError as exc:
                out[i] = ImageFile(i, FAILED, message=str(exc))
                continue
            fmt_hint = _MIME.get((image.mime_type or "").lower()) or _EXT.get(os.path.splitext(path)[1].lower())
            if not os.path.isfile(path):
                out[i] = ImageFile(i, MISSING, path=path, format=fmt_hint,
                                   message="images[%d]: file not found: %s" % (i, path))
                continue
            try:
                head = _read_head(path)
            except OSError as exc:
                out[i] = ImageFile(i, MISSING, path=path, format=fmt_hint,
                                   message="images[%d]: cannot read %s: %s" % (i, path, exc))
                continue
            fmt = sniff_format(head) or fmt_hint
            out[i] = _finish(ImageFile(i, OK, path=path, format=fmt, has_alpha=has_alpha(fmt, head)))
            continue

        # embedded
        try:
            if uri is not None:
                mime, data = decode_data_uri(uri)
            elif image.buffer_view is not None:
                mime, data = image.mime_type, bytes(reader.buffer_view_bytes(image.buffer_view))
            else:
                out[i] = ImageFile(i, FAILED, embedded=True, message="images[%d] has neither uri nor bufferView" % i)
                continue
        except GltfError as exc:
            out[i] = ImageFile(i, FAILED, embedded=True, message="images[%d]: %s" % (i, exc))
            continue
        fmt = sniff_format(data[:64]) or _MIME.get((mime or image.mime_type or "").lower())
        info = ImageFile(i, OK, format=fmt, embedded=True, has_alpha=has_alpha(fmt, data[:_HEAD_BYTES]))
        _finish(info)
        if info.status != OK:
            out[i] = info
            continue
        if extract_dir is None:
            info.status = FAILED
            info.message = "no folder to extract embedded images to"
            out[i] = info
            continue
        if names is None:
            names = embedded_file_names(doc)
        ext = FORMATS[fmt][0] if fmt in FORMATS else ".img"
        path = os.path.join(extract_dir, names[i] + ext)
        info.path = path
        if write_error is None:
            try:
                os.makedirs(extract_dir, exist_ok=True)
                info.written = _write_if_changed(path, data)
            except OSError as exc:
                write_error = exc
        if write_error is not None:
            info.status = FAILED
            info.message = "cannot write %s: %s" % (path, write_error)
        out[i] = info

    failed_writes = [f for f in out.values() if f.embedded and f.status == FAILED and f.path]
    if failed_writes:
        reporter.warning("Could not extract %d embedded texture(s) to '%s' (%s); those textures are not "
                         "linked. Set the textureDir option to a writable folder."
                         % (len(failed_writes), extract_dir, write_error))
    for f in sorted(out.values(), key=lambda f: f.index):
        if f.status == UNSUPPORTED or (f.status == FAILED and not (f.embedded and f.path)):
            reporter.warning(f.message)
    missing = [f for f in out.values() if f.status == MISSING]
    if missing:
        reporter.warning("%d texture file(s) not found; linked anyway so they can be relinked: %s"
                         % (len(missing), ", ".join(sorted(f.path for f in missing))[:600]))
    return out


def _finish(info):
    fmt = info.format
    if fmt is None:
        return info  # unknown: let Maya try
    readable = FORMATS.get(fmt, (None, True))[1]
    if not readable:
        info.status = UNSUPPORTED
        info.message = ("images[%d] is %s, which Maya's file node cannot read; texture skipped"
                        % (info.index, FORMAT_NAMES.get(fmt, fmt)))
    return info
