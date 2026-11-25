"""Pure Python glTF 2.0 reading and import preparation. Never imports Maya.

``prepare_import`` does everything that can fail on bad input (parsing, validation,
accessor decoding, triangulation) before the Maya layer touches the scene.
"""

from dataclasses import dataclass, field
from typing import Dict

from .accessors import AccessorReader
from .diagnostics import GltfError, GltfFormatError, GltfUnsupportedError, Reporter
from .meshprep import MeshData, prepare_mesh
from .model import Document
from .options import ImportOptions, parse_options
from .reader import load, loads
from .scene import ScenePlan, build_scene_plan

__all__ = [
    "AccessorReader", "GltfError", "GltfFormatError", "GltfUnsupportedError", "Reporter",
    "MeshData", "Document", "ImportOptions", "parse_options", "load", "loads", "ScenePlan",
    "PreparedImport", "prepare_import",
]


@dataclass
class PreparedImport:
    doc: Document
    options: ImportOptions
    plan: ScenePlan
    reader: AccessorReader
    meshes: Dict[int, MeshData] = field(default_factory=dict)


def prepare_import(path, options, reporter=None):
    reporter = reporter or Reporter()
    doc = load(path, reporter)
    reader = AccessorReader(doc)
    plan = build_scene_plan(doc, options, reporter)
    prepared = PreparedImport(doc=doc, options=options, plan=plan, reader=reader)
    for shape in plan.shapes:
        if shape.mesh not in prepared.meshes:
            prepared.meshes[shape.mesh] = prepare_mesh(doc, reader, shape.mesh, options, plan.unit_scale,
                                                       reporter)
    return prepared
