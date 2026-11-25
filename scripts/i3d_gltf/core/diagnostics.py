"""Errors and message collection. No Maya imports."""


class GltfError(Exception):
    """Base class for every error raised by the glTF reader."""


class GltfFormatError(GltfError):
    """The file is not valid glTF 2.0 (bad JSON, bad GLB container, out-of-range data...)."""


class GltfUnsupportedError(GltfError):
    """The file is valid but uses something this importer does not support."""


INFO = "info"
WARNING = "warning"
ERROR = "error"


class Reporter(object):
    """Collects messages produced during an import.

    ``sink`` is an optional callable ``sink(level, text)`` used to forward messages
    as they arrive (the Maya layer forwards to MGlobal.displayWarning/displayError).
    Messages are always kept in ``self.messages`` so tests can inspect them.
    Identical warnings are only forwarded once to avoid flooding the script editor.
    """

    def __init__(self, sink=None, prefix="glTF: "):
        self.sink = sink
        self.prefix = prefix
        self.messages = []
        self._seen = set()

    def _emit(self, level, text):
        self.messages.append((level, text))
        key = (level, text)
        if key in self._seen:
            return
        self._seen.add(key)
        if self.sink is not None:
            self.sink(level, self.prefix + text)

    def info(self, text):
        self._emit(INFO, text)

    def warning(self, text):
        self._emit(WARNING, text)

    def error(self, text):
        self._emit(ERROR, text)

    def of_level(self, level):
        return [t for (lv, t) in self.messages if lv == level]

    @property
    def warnings(self):
        return self.of_level(WARNING)

    @property
    def errors(self):
        return self.of_level(ERROR)
