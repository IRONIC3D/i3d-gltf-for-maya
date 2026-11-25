"""Plain data model for a parsed glTF 2.0 document. No Maya imports.

Every object keeps its ``extensions`` and ``extras`` dicts verbatim so later
milestones (and users) can reach data this importer does not interpret yet.
Index references (``mesh``, ``material``...) are plain ints, validated by the reader.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Accessor component types
BYTE = 5120
UNSIGNED_BYTE = 5121
SHORT = 5122
UNSIGNED_SHORT = 5123
UNSIGNED_INT = 5125
FLOAT = 5126

COMPONENT_TYPES = {
    # componentType: (struct/array format char, size in bytes)
    BYTE: ("b", 1),
    UNSIGNED_BYTE: ("B", 1),
    SHORT: ("h", 2),
    UNSIGNED_SHORT: ("H", 2),
    UNSIGNED_INT: ("I", 4),
    FLOAT: ("f", 4),
}

ELEMENT_TYPES = {
    "SCALAR": 1,
    "VEC2": 2,
    "VEC3": 3,
    "VEC4": 4,
    "MAT2": 4,
    "MAT3": 9,
    "MAT4": 16,
}

# Primitive modes
POINTS = 0
LINES = 1
LINE_LOOP = 2
LINE_STRIP = 3
TRIANGLES = 4
TRIANGLE_STRIP = 5
TRIANGLE_FAN = 6

MODE_NAMES = {
    POINTS: "POINTS", LINES: "LINES", LINE_LOOP: "LINE_LOOP", LINE_STRIP: "LINE_STRIP",
    TRIANGLES: "TRIANGLES", TRIANGLE_STRIP: "TRIANGLE_STRIP", TRIANGLE_FAN: "TRIANGLE_FAN",
}


def _dict():
    return field(default_factory=dict)


def _list():
    return field(default_factory=list)


@dataclass
class Asset:
    version: str = "2.0"
    min_version: Optional[str] = None
    generator: Optional[str] = None
    copyright: Optional[str] = None
    extensions: Dict[str, Any] = _dict()
    extras: Any = None


@dataclass
class Buffer:
    byte_length: int = 0
    uri: Optional[str] = None
    name: Optional[str] = None
    extensions: Dict[str, Any] = _dict()
    extras: Any = None


@dataclass
class BufferView:
    buffer: int = 0
    byte_length: int = 0
    byte_offset: int = 0
    byte_stride: Optional[int] = None
    target: Optional[int] = None
    name: Optional[str] = None
    extensions: Dict[str, Any] = _dict()
    extras: Any = None


@dataclass
class SparseAccessor:
    count: int = 0
    indices_buffer_view: int = 0
    indices_byte_offset: int = 0
    indices_component_type: int = UNSIGNED_INT
    values_buffer_view: int = 0
    values_byte_offset: int = 0
    extensions: Dict[str, Any] = _dict()
    extras: Any = None


@dataclass
class Accessor:
    component_type: int = FLOAT
    count: int = 0
    type: str = "SCALAR"
    buffer_view: Optional[int] = None
    byte_offset: int = 0
    normalized: bool = False
    min: Optional[List[float]] = None
    max: Optional[List[float]] = None
    sparse: Optional[SparseAccessor] = None
    name: Optional[str] = None
    extensions: Dict[str, Any] = _dict()
    extras: Any = None

    @property
    def num_components(self):
        return ELEMENT_TYPES[self.type]


@dataclass
class Image:
    uri: Optional[str] = None
    mime_type: Optional[str] = None
    buffer_view: Optional[int] = None
    name: Optional[str] = None
    extensions: Dict[str, Any] = _dict()
    extras: Any = None


@dataclass
class Sampler:
    mag_filter: Optional[int] = None
    min_filter: Optional[int] = None
    wrap_s: int = 10497
    wrap_t: int = 10497
    name: Optional[str] = None
    extensions: Dict[str, Any] = _dict()
    extras: Any = None


@dataclass
class Texture:
    sampler: Optional[int] = None
    source: Optional[int] = None
    name: Optional[str] = None
    extensions: Dict[str, Any] = _dict()
    extras: Any = None


@dataclass
class TextureInfo:
    index: int = 0
    tex_coord: int = 0
    scale: float = 1.0      # normalTexture only
    strength: float = 1.0   # occlusionTexture only
    extensions: Dict[str, Any] = _dict()  # e.g. KHR_texture_transform
    extras: Any = None


@dataclass
class Material:
    name: Optional[str] = None
    base_color_factor: List[float] = field(default_factory=lambda: [1.0, 1.0, 1.0, 1.0])
    base_color_texture: Optional[TextureInfo] = None
    metallic_factor: float = 1.0
    roughness_factor: float = 1.0
    metallic_roughness_texture: Optional[TextureInfo] = None
    normal_texture: Optional[TextureInfo] = None
    occlusion_texture: Optional[TextureInfo] = None
    emissive_texture: Optional[TextureInfo] = None
    emissive_factor: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    alpha_mode: str = "OPAQUE"
    alpha_cutoff: float = 0.5
    double_sided: bool = False
    pbr_extensions: Dict[str, Any] = _dict()
    pbr_extras: Any = None
    extensions: Dict[str, Any] = _dict()
    extras: Any = None


@dataclass
class Primitive:
    attributes: Dict[str, int] = _dict()
    indices: Optional[int] = None
    material: Optional[int] = None
    mode: int = TRIANGLES
    targets: List[Dict[str, int]] = _list()
    extensions: Dict[str, Any] = _dict()
    extras: Any = None


@dataclass
class Mesh:
    primitives: List[Primitive] = _list()
    weights: Optional[List[float]] = None
    name: Optional[str] = None
    extensions: Dict[str, Any] = _dict()
    extras: Any = None

    @property
    def target_names(self):
        """Morph target names from the de-facto ``extras.targetNames`` convention."""
        if isinstance(self.extras, dict):
            names = self.extras.get("targetNames")
            if isinstance(names, list):
                return [str(n) for n in names]
        return None


@dataclass
class Node:
    name: Optional[str] = None
    children: List[int] = _list()
    mesh: Optional[int] = None
    skin: Optional[int] = None
    camera: Optional[int] = None
    matrix: Optional[List[float]] = None  # 16 floats, column-major, as in the file
    translation: Optional[List[float]] = None
    rotation: Optional[List[float]] = None  # quaternion x, y, z, w
    scale: Optional[List[float]] = None
    weights: Optional[List[float]] = None
    extensions: Dict[str, Any] = _dict()
    extras: Any = None


@dataclass
class Skin:
    joints: List[int] = _list()
    inverse_bind_matrices: Optional[int] = None
    skeleton: Optional[int] = None
    name: Optional[str] = None
    extensions: Dict[str, Any] = _dict()
    extras: Any = None


@dataclass
class AnimationSampler:
    input: int = 0
    output: int = 0
    interpolation: str = "LINEAR"
    extensions: Dict[str, Any] = _dict()
    extras: Any = None


@dataclass
class AnimationChannel:
    sampler: int = 0
    target_node: Optional[int] = None
    target_path: str = ""
    target_extensions: Dict[str, Any] = _dict()
    extensions: Dict[str, Any] = _dict()
    extras: Any = None


@dataclass
class Animation:
    channels: List[AnimationChannel] = _list()
    samplers: List[AnimationSampler] = _list()
    name: Optional[str] = None
    extensions: Dict[str, Any] = _dict()
    extras: Any = None


@dataclass
class Camera:
    type: str = "perspective"
    perspective: Optional[Dict[str, Any]] = None
    orthographic: Optional[Dict[str, Any]] = None
    name: Optional[str] = None
    extensions: Dict[str, Any] = _dict()
    extras: Any = None


@dataclass
class Scene:
    nodes: List[int] = _list()
    name: Optional[str] = None
    extensions: Dict[str, Any] = _dict()
    extras: Any = None


@dataclass
class Document:
    asset: Asset = field(default_factory=Asset)
    scene: Optional[int] = None
    scenes: List[Scene] = _list()
    nodes: List[Node] = _list()
    meshes: List[Mesh] = _list()
    accessors: List[Accessor] = _list()
    buffer_views: List[BufferView] = _list()
    buffers: List[Buffer] = _list()
    materials: List[Material] = _list()
    textures: List[Texture] = _list()
    images: List[Image] = _list()
    samplers: List[Sampler] = _list()
    skins: List[Skin] = _list()
    animations: List[Animation] = _list()
    cameras: List[Camera] = _list()
    extensions_used: List[str] = _list()
    extensions_required: List[str] = _list()
    extensions: Dict[str, Any] = _dict()
    extras: Any = None
    # Source information (not part of the glTF JSON)
    source_path: Optional[str] = None
    base_dir: str = ""
    is_glb: bool = False
    glb_bin: Optional[bytes] = None
    json: Dict[str, Any] = _dict()
