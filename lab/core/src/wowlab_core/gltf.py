"""gltf: a scene to glTF 2.0 binary (`.glb`) bytes, with the standard library
only (`json`, `struct`, `array`, `hashlib`; docs/LAB_PLAN.md §17.6, M15-11).

The bytes are returned, not written: where they go is the caller's choice
(never an install, L1). Geometry and pixels come in as bytes inside the
Pydantic models below; nothing here reads a file.

**What a file holds.** One scene; one node per `GltfNode`, each with its
mesh and a translation; per mesh one set of vertex attributes shared by its
primitives: POSITION and NORMAL (float32, three a vertex), TEXCOORD_0
(float32, two a vertex); per primitive its uint32 triangle indices (mode 4,
triangles) and its material. Each material is metallic-roughness with
metallic 0 and roughness 1 and, when it has one, a base-colour texture
whose image is a PNG stored in the binary chunk (`mimeType` `image/png`);
one sampler, linear filtering, repeat wrapping. POSITION accessors carry
`min` and `max`, as the specification requires. The layout: a 12-byte
header (`glTF`, version 2, total length), the JSON chunk padded with spaces
to four bytes, the binary chunk padded with zeros to four bytes, every
buffer view starting on a four-byte boundary.

**Axes.** The game's models are Z-up; glTF is Y-up. `to_y_up` maps a
game position or normal `(x, y, z)` to `(x, z, -y)`, the mapping wow.export
writes; it is a rotation (determinant 1), so the triangles' winding is kept.
Whether the signs give the model facing the way it does in game is
**[verify]**: the owner opens one file in a glTF viewer. Texture
coordinates are written as stored (the game's and glTF's both start at the
top left of an image, **[verify]** with the same look).

**Bounds.** Checked before anything is built: each attribute a whole
number of vertices, the three agreeing, every value a finite number, at
least one node, every index below the vertex count,
a whole number of triangles, a material index that names a material, an
image index that names an image, and the whole file under 2**32 bytes
(the header's length field). `MAX_FILE_BYTES` is the cap a caller may lower.
"""

from __future__ import annotations

import hashlib
import json
import math
import struct
import sys
from array import array
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from wowlab_core.errors import WowlabError

__all__ = [
    "GLB_MAGIC",
    "MAX_FILE_BYTES",
    "GlbFacts",
    "GltfError",
    "GltfImage",
    "GltfMaterial",
    "GltfMesh",
    "GltfNode",
    "GltfPrimitive",
    "GltfScene",
    "encode_glb",
    "floats",
    "to_y_up",
    "uints",
]

GLB_MAGIC = b"glTF"
_VERSION = 2
_JSON = 0x4E4F534A  # "JSON"
_BIN = 0x004E4942  # "BIN\0"
MAX_FILE_BYTES = 2**32 - 1
"""The most bytes a `.glb` can have: its header's length is a uint32."""

_FLOAT = 5126
_UINT = 5125
_ARRAY_BUFFER = 34962
_ELEMENT_ARRAY_BUFFER = 34963
_LINEAR = 9729
_REPEAT = 10497
_TRIANGLES = 4
_LITTLE = sys.byteorder == "little"


class GltfError(WowlabError):
    """The scene cannot be written as a `.glb`: an attribute or index list
    that does not fit, an index past the vertices, a reference to nothing,
    or a file past the cap."""


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class GltfImage(_Frozen):
    """A PNG image, stored in the binary chunk."""

    name: str
    png: bytes = Field(repr=False)


class GltfMaterial(_Frozen):
    """A material: its base-colour image (`image`, an index into the
    scene's images, or None for a plain grey), how its alpha is read, and
    whether both faces are drawn."""

    name: str
    image: int | None
    alpha_mode: Literal["OPAQUE", "MASK", "BLEND"] = "OPAQUE"
    alpha_cutoff: float | None = None
    double_sided: bool = False


class GltfPrimitive(_Frozen):
    """Triangles: `indices` are little-endian uint32, three a triangle, into
    the mesh's vertices; `material` an index into the scene's materials."""

    indices: bytes = Field(repr=False)
    material: int | None


class GltfMesh(_Frozen):
    """Vertices, little-endian float32: `positions` and `normals` three a
    vertex, `uvs` two; Y-up already (`to_y_up`)."""

    name: str
    positions: bytes = Field(repr=False)
    normals: bytes = Field(repr=False)
    uvs: bytes = Field(repr=False)
    primitives: tuple[GltfPrimitive, ...]

    @property
    def vertex_count(self) -> int:
        return len(self.positions) // 12


class GltfNode(_Frozen):
    """A mesh placed at `translation` (Y-up)."""

    name: str
    mesh: GltfMesh
    translation: tuple[float, float, float] = (0.0, 0.0, 0.0)


class GltfScene(_Frozen):
    nodes: tuple[GltfNode, ...]
    materials: tuple[GltfMaterial, ...]
    images: tuple[GltfImage, ...]


class GlbFacts(_Frozen):
    """What a written `.glb` holds, counted, with the MD5 of its JSON chunk
    and of its binary chunk (as padded in the file)."""

    bytes: int
    nodes: int
    meshes: int
    primitives: int
    materials: int
    images: int
    vertices: int
    triangles: int
    json_md5: str
    bin_md5: str


def to_y_up(x: float, y: float, z: float) -> tuple[float, float, float]:
    """The game's Z-up `(x, y, z)` as glTF's Y-up: `(x, z, -y)` (**[verify]**,
    the module docstring)."""
    return (x, z, -y)


def floats(values: Sequence[float] | array[float]) -> bytes:
    """Little-endian float32 bytes."""
    out = array("f", values)
    if not _LITTLE:
        out.byteswap()
    return out.tobytes()


def uints(values: Sequence[int] | array[int]) -> bytes:
    """Little-endian uint32 bytes."""
    out = array("I", values)
    if out.itemsize != 4:  # pragma: no cover - every supported platform has a 4-byte I
        out = array("L", values)
    if not _LITTLE:
        out.byteswap()
    return out.tobytes()


def _read_floats(data: bytes) -> array[float]:
    out = array("f")
    out.frombytes(data)
    if not _LITTLE:
        out.byteswap()
    return out


def _read_uints(data: bytes) -> array[int]:
    out = array("I")
    out.frombytes(data)
    if not _LITTLE:
        out.byteswap()
    return out


def _check(scene: GltfScene) -> None:
    if not scene.nodes:
        raise GltfError("a scene with no nodes")
    for n, node in enumerate(scene.nodes):
        mesh = node.mesh
        what = f"node {n} ({node.name!r})"
        if len(mesh.positions) % 12:
            raise GltfError(f"{what}: {len(mesh.positions)} position bytes, not three floats each")
        count = mesh.vertex_count
        if count == 0:
            raise GltfError(f"{what}: no vertices")
        if len(mesh.normals) != count * 12:
            raise GltfError(f"{what}: {len(mesh.normals)} normal bytes for {count} vertices")
        if len(mesh.uvs) != count * 8:
            raise GltfError(
                f"{what}: {len(mesh.uvs)} texture-coordinate bytes for {count} vertices"
            )
        if not all(math.isfinite(v) for v in node.translation):
            raise GltfError(f"{what}: a translation that is not a finite number")
        if not all(map(math.isfinite, _read_floats(mesh.normals))):
            raise GltfError(f"{what}: a normal that is not a finite number")
        if not all(map(math.isfinite, _read_floats(mesh.uvs))):
            raise GltfError(f"{what}: a texture coordinate that is not a finite number")
        if not mesh.primitives:
            raise GltfError(f"{what}: no triangles")
        for p, prim in enumerate(mesh.primitives):
            if not prim.indices or len(prim.indices) % 12:
                raise GltfError(
                    f"{what}, part {p}: {len(prim.indices)} index bytes, not whole triangles"
                )
            if max(_read_uints(prim.indices)) >= count:
                raise GltfError(f"{what}, part {p}: an index past its {count} vertices")
            if prim.material is not None and not 0 <= prim.material < len(scene.materials):
                raise GltfError(f"{what}, part {p}: material {prim.material} does not exist")
    for m, material in enumerate(scene.materials):
        if material.alpha_cutoff is not None and not math.isfinite(material.alpha_cutoff):
            raise GltfError(f"material {m} ({material.name!r}): an alpha cutoff that is not finite")
        if material.image is not None and not 0 <= material.image < len(scene.images):
            raise GltfError(
                f"material {m} ({material.name!r}): image {material.image} does not exist"
            )


class _Buffer:
    def __init__(self) -> None:
        self.parts: list[bytes] = []
        self.size = 0
        self.views: list[dict[str, object]] = []

    def view(self, data: bytes, target: int | None = None) -> int:
        pad = -self.size % 4
        if pad:
            self.parts.append(bytes(pad))
            self.size += pad
        entry: dict[str, object] = {"buffer": 0, "byteOffset": self.size, "byteLength": len(data)}
        if target is not None:
            entry["target"] = target
        self.parts.append(data)
        self.size += len(data)
        self.views.append(entry)
        return len(self.views) - 1


def _bounds(positions: bytes) -> tuple[list[float], list[float]]:
    values = _read_floats(positions)
    if not all(map(math.isfinite, values)):
        raise GltfError("a vertex position that is not a finite number")
    lows = [min(values[i::3]) for i in range(3)]
    highs = [max(values[i::3]) for i in range(3)]
    return lows, highs


def encode_glb(scene: GltfScene, *, limit: int = MAX_FILE_BYTES) -> tuple[bytes, GlbFacts]:
    """The scene as `.glb` bytes, and what they hold. `GltfError` for a
    scene that does not fit (the module docstring), before anything is
    built, and for a file past `limit` bytes."""
    _check(scene)
    buf = _Buffer()
    accessors: list[dict[str, object]] = []
    meshes: list[dict[str, object]] = []
    nodes: list[dict[str, object]] = []
    triangles = 0
    vertices = 0
    primitive_count = 0

    def accessor(view: int, kind: str, count: int, component: int, **extra: object) -> int:
        accessors.append(
            {"bufferView": view, "componentType": component, "count": count, "type": kind, **extra}
        )
        return len(accessors) - 1

    for node in scene.nodes:
        mesh = node.mesh
        count = mesh.vertex_count
        vertices += count
        lows, highs = _bounds(mesh.positions)
        position = accessor(
            buf.view(mesh.positions, _ARRAY_BUFFER), "VEC3", count, _FLOAT, min=lows, max=highs
        )
        normal = accessor(buf.view(mesh.normals, _ARRAY_BUFFER), "VEC3", count, _FLOAT)
        uv = accessor(buf.view(mesh.uvs, _ARRAY_BUFFER), "VEC2", count, _FLOAT)
        primitives: list[dict[str, object]] = []
        for prim in mesh.primitives:
            n = len(prim.indices) // 4
            triangles += n // 3
            primitive_count += 1
            entry: dict[str, object] = {
                "attributes": {"POSITION": position, "NORMAL": normal, "TEXCOORD_0": uv},
                "indices": accessor(
                    buf.view(prim.indices, _ELEMENT_ARRAY_BUFFER), "SCALAR", n, _UINT
                ),
                "mode": _TRIANGLES,
            }
            if prim.material is not None:
                entry["material"] = prim.material
            primitives.append(entry)
        meshes.append({"name": mesh.name, "primitives": primitives})
        placed: dict[str, object] = {"name": node.name, "mesh": len(meshes) - 1}
        if any(node.translation):
            placed["translation"] = list(node.translation)
        nodes.append(placed)

    images = [
        {"name": image.name, "bufferView": buf.view(image.png), "mimeType": "image/png"}
        for image in scene.images
    ]
    materials: list[dict[str, object]] = []
    for material in scene.materials:
        pbr: dict[str, object] = {"metallicFactor": 0.0, "roughnessFactor": 1.0}
        if material.image is None:
            pbr["baseColorFactor"] = [0.5, 0.5, 0.5, 1.0]
        else:
            pbr["baseColorTexture"] = {"index": material.image}
        entry = {
            "name": material.name,
            "pbrMetallicRoughness": pbr,
            "alphaMode": material.alpha_mode,
            "doubleSided": material.double_sided,
        }
        if material.alpha_mode == "MASK" and material.alpha_cutoff is not None:
            entry["alphaCutoff"] = material.alpha_cutoff
        materials.append(entry)

    document: dict[str, object] = {
        "asset": {"version": "2.0", "generator": "wowlab"},
        "scene": 0,
        "scenes": [{"nodes": list(range(len(nodes)))}],
        "nodes": nodes,
        "meshes": meshes,
        "accessors": accessors,
        "bufferViews": buf.views,
        "buffers": [{"byteLength": buf.size + (-buf.size % 4)}],
    }
    if materials:
        document["materials"] = materials
    if images:
        document["images"] = images
        document["textures"] = [{"source": i, "sampler": 0} for i in range(len(images))]
        document["samplers"] = [
            {"magFilter": _LINEAR, "minFilter": _LINEAR, "wrapS": _REPEAT, "wrapT": _REPEAT}
        ]
    text = json.dumps(document, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    text += b" " * (-len(text) % 4)
    binary = b"".join(buf.parts)
    binary += bytes(-len(binary) % 4)
    total = 12 + 8 + len(text) + 8 + len(binary)
    if total > min(limit, MAX_FILE_BYTES):
        raise GltfError(
            f"the model would be {total:,} bytes; the cap is {min(limit, MAX_FILE_BYTES):,}"
        )
    out = b"".join(
        (
            struct.pack("<4sII", GLB_MAGIC, _VERSION, total),
            struct.pack("<II", len(text), _JSON),
            text,
            struct.pack("<II", len(binary), _BIN),
            binary,
        )
    )
    facts = GlbFacts(
        bytes=total,
        nodes=len(nodes),
        meshes=len(meshes),
        primitives=primitive_count,
        materials=len(materials),
        images=len(images),
        vertices=vertices,
        triangles=triangles,
        json_md5=hashlib.md5(text, usedforsecurity=False).hexdigest(),
        bin_md5=hashlib.md5(binary, usedforsecurity=False).hexdigest(),
    )
    return out, facts
