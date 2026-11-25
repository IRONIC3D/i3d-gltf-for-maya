"""Deterministic Maya node naming. No Maya imports.

Rules (stable across imports, so references can re-apply edits by name):
  1. Every character outside [A-Za-z0-9_] becomes '_' (one '_' per character; no collapsing).
  2. A name starting with a digit gets a '_' prefix.
  3. A missing/empty name, or one with no letter or digit left after step 1, uses the
     fallback ``<kind>_<index>`` (``node_3``, ``mesh_0``).
  4. Names are made unique within one import, processing objects in glTF index order:
     a clash appends ``_<index>``; if that also clashes, ``_<index>_<n>`` with n = 1, 2...
Nothing depends on scene state, counters across imports, or time.
"""

import re

_INVALID = re.compile(r"[^A-Za-z0-9_]")
_HAS_ALNUM = re.compile(r"[A-Za-z0-9]")


def sanitize(name, fallback):
    if not name or not isinstance(name, str):
        return fallback
    s = _INVALID.sub("_", name)
    if not _HAS_ALNUM.search(s):
        return fallback
    if s[0].isdigit():
        s = "_" + s
    return s


class NameRegistry(object):
    """Hands out unique names within one import."""

    def __init__(self, reserved=()):
        self._used = set(reserved)

    def claim(self, base, index=None):
        name = base
        if name in self._used and index is not None:
            name = "%s_%d" % (base, index)
        if name in self._used:
            root = name
            n = 1
            while "%s_%d" % (root, n) in self._used:
                n += 1
            name = "%s_%d" % (root, n)
        self._used.add(name)
        return name

    def __contains__(self, name):
        return name in self._used
