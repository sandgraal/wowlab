"""m2: models and their `.skin` files, read as data (docs/LAB_PLAN.md §17.3,
`docs/LAB_FORMATS.md` §12, ADR-0033, M15-08).

The bytes come from `casc.CascSource.read` (a read-only `memoryview`) or
any other buffer; this module opens no file and writes nothing.

**The M2.** The chunked form: a run of chunks, each a four-byte tag and a
little-endian u32 size. `MD21` holds the whole MD20 blob, whose array
offsets are relative to the blob's own start. `SFID` holds the skin file
data ids (one per view the header counts, then the LOD skins), `TXID` the
texture file data ids (one per texture, non-zero for the type-0 ones in
the models met).
Every other chunk (`AFID`, `BFID`, `SKID`, `LDV1`, `TXAC`, `EXP2` …) is
listed by tag, offset and size and not read. A file that is not chunked
(it starts with `MD20`) is refused as unsupported: the Lab reads the
modern form only.

From the MD20 header (offsets in the blob; every array a `count, offset`
pair): the version at 0x04 (the wiki's table runs from 256 to 274; below
264 the header is laid out differently and is refused; above 274 the file
is read with the 274 layout and a note), the name at 0x08, the global
flags at 0x10, the bones at 0x2C (counted and bounds-checked by
`parse_m2`; `bone_chain` reads one bone and its parents on request, below),
the vertices at 0x3C (48 bytes:
position, four bone weights, four bone indices, normal, two UV pairs;
Z-up, in the bind pose, kept as stored), the view count at 0x44, the
textures at 0x50 (16 bytes: type, flags, a filename array), the materials
at 0x70 (4 bytes: flags, blending mode), the texture combos at 0x80 (the
texture lookup: u16 indices into the textures), the attachments at 0xF0
(40 bytes: id, bone, an unknown u16, position, an animation track not
read) and the attachment lookup at 0xF8 (u16).

**The skin.** `SKIN`, then five arrays: the vertex lookup (u16 indices into
the M2's vertices), the triangle indices (u16 indices into the lookup),
the bone indices (four bytes per lookup entry; counted, not read), the
sections (48 bytes) and the batches (24 bytes), then the u32 at 0x2C kept
as `Skin.vertex_base` (the wiki's `boneCountMax`; what it is in the HD
body models: `docs/LAB_FORMATS.md` §12, amendment of 2026-11-17); the rest
of the 64-byte header (shadow batches, by the wiki) is not read. A section's
`skinSectionId` is the geoset id (`group * 100 + value`); its `Level` is
the high half of the index start (`level << 16` is added to the u16
`indexStart`, as wow.export does; the wiki's "and alike" is not followed
for the vertex start: in the fourteen skins of `humanmale_hd` and
`humanfemale_hd` at 70959 the vertex start never needs it (the largest
lookup has 43,166 entries; §12 amendment of 2026-11-17)).

**Bounds.** What is checked, and what that bounds:

- Every array's `count, offset` against the bytes of its chunk (or of the
  skin) before anything is read: an array that would run past the end is
  refused with `M2MalformedError` naming the array, its count and its
  offset, so a count of 2**32 - 1 is refused without a byte allocated.
  Arithmetic is on Python integers, so nothing wraps, and nothing is read
  past a chunk's end.
- At most `MAX_CHUNKS` (1,024) chunks; an `M2Chunk` is built for each.
- At most `MAX_ENTRIES` (65,535) textures, materials, attachments,
  sections and batches, each of which becomes one Python object, and as
  many texture combos, attachment-lookup entries, `SFID` ids and `TXID`
  ids, each of which becomes a Python int in a tuple. A `TXID` whose count
  differs from the textures' is read with a note (both models met hold
  exactly one id per texture); a texture takes the id at its own index.
- A texture filename of at most `MAX_FILENAME` (1,024) bytes, and the
  filenames' counts summed at most the chunk's size, since they may all
  point at one region and each is copied.
- The sections' index counts summed at most `MAX_SECTION_INDEX_FACTOR` (4)
  times the indices, so drawing every section's `triangles` builds at most
  four times the index array. Each section's ranges are checked against
  the lookup and the indices, every index against the lookup, every
  batch's section against the sections and, when the caller gives the
  model's vertex count, every lookup entry against it.

- `bone_chain` (M15-27) reads one bone and its parents, never the whole
  bone array: the bone index and every parent against the bone count, the
  chain at most `MAX_BONE_DEPTH` (256) long and refused on a loop, and of
  each bone's rotation track only the first key of its first array, when
  the track runs on a global sequence; the outer and the first inner array
  checked against the blob, the global sequence against the global
  sequences' count. A hostile count costs nothing: no array is walked.

The rest is proportional to the input: the vertex columns take about three
times the vertex array at peak while they are built and one copy once
built (measured: 161 MB at peak for 48 MB of vertices); the lookup and
indices one copy each. The caller bounds
the input (`casc.CascSource.read` caps a file at `casc.DECODE_LIMIT`).
"""

from __future__ import annotations

import struct
import sys
from array import array
from collections.abc import Buffer
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from wowlab_core.errors import WowlabError

__all__ = [
    "ATTACHMENT_SIZE",
    "BATCH_SIZE",
    "BONE_SIZE",
    "MATERIAL_SIZE",
    "MAX_BONE_DEPTH",
    "MAX_CHUNKS",
    "MAX_ENTRIES",
    "MAX_FILENAME",
    "MAX_KNOWN_VERSION",
    "MAX_SECTION_INDEX_FACTOR",
    "MIN_VERSION",
    "NO_INDEX",
    "SECTION_SIZE",
    "TEXTURE_SIZE",
    "VERTEX_SIZE",
    "M2Attachment",
    "M2Bone",
    "M2Chunk",
    "M2Error",
    "M2MalformedError",
    "M2Material",
    "M2Model",
    "M2Texture",
    "M2UnsupportedError",
    "M2Vertices",
    "Skin",
    "SkinBatch",
    "SkinSection",
    "bone_chain",
    "decode_quaternion",
    "parse_m2",
    "parse_skin",
]

MIN_VERSION = 264  # the WotLK header layout; earlier versions add arrays
MAX_KNOWN_VERSION = 274  # the last version in the wiki's table
NO_INDEX = 0xFFFF  # a u16 index field that names nothing

VERTEX_SIZE = 48
TEXTURE_SIZE = 16
MATERIAL_SIZE = 4
ATTACHMENT_SIZE = 40
BONE_SIZE = 88  # M2CompBone since 264; counted, not read
SECTION_SIZE = 48
BATCH_SIZE = 24
_SKIN_BONE_SIZE = 4  # four bone indices per lookup entry; counted, not read

_CHUNK_HEAD = struct.Struct("<4sI")
_ARRAY = struct.Struct("<II")
_U32 = struct.Struct("<I")
_HEADER_END = 0x100  # the end of the attachment lookup, the last field read
_SKIN_HEADER_END = 48  # magic, five arrays, one u32
_TEXTURE = struct.Struct("<II")  # type, flags (then the filename array)
_MATERIAL = struct.Struct("<HH")
_ATTACHMENT = struct.Struct("<IHH3f")
_SECTION = struct.Struct("<10H3f3ff")
_BATCH = struct.Struct("<Bb11H")
_READ_TAGS = (b"MD21", b"SFID", b"TXID")

# The MD20 header's fields read here, by offset.
_NAME = 0x08
_FLAGS = 0x10
_BONES = 0x2C
_VERTICES = 0x3C
_VIEWS = 0x44
_TEXTURES = 0x50
_MATERIALS = 0x70
_TEXTURE_COMBOS = 0x80
_ATTACHMENTS = 0xF0
_ATTACHMENT_LOOKUP = 0xF8

# The skin header's arrays, by offset.
_SKIN_LOOKUP = 0x04
_SKIN_INDICES = 0x0C
_SKIN_BONES = 0x14
_SKIN_SECTIONS = 0x1C
_SKIN_BATCHES = 0x24
_SKIN_VERTEX_BASE = 0x2C  # the wiki's boneCountMax; see `Skin`

_FLOATS_PER_VERTEX = VERTEX_SIZE // 4

# Caps on what a hostile file could make this module build (security review
# of 2026-11-17). Real files are far below each: 7 chunks, 12 textures with
# empty filenames, 126 sections and batches, 45 attachments, 11 materials.
MAX_CHUNKS = 1024
MAX_FILENAME = 1024  # bytes of one texture filename
MAX_ENTRIES = 0xFFFF  # textures, materials, attachments, sections, batches: u16-indexed
MAX_SECTION_INDEX_FACTOR = 4  # the sections' index counts summed, against the indices
MAX_BONE_DEPTH = 256  # bones in one chain (the fixture body models: under 20 deep)

# M2CompBone (88 bytes since 264, wowdev.wiki): key bone id i32 at 0, flags
# u32 at 4, parent i16 at 8, then three M2Tracks of 20 bytes (translation at
# 16, rotation at 36, scale at 56) and the pivot, three floats, at 76. An
# M2Track: interpolation u16 at 0, global sequence i16 at 2 (-1: none), the
# timestamps' and the values' arrays of per-sequence arrays at 4 and 12.
_BONE_HEAD = struct.Struct("<iIh")
_BONE_ROTATION = 36
_BONE_PIVOT = 76
_TRACK_HEAD = struct.Struct("<Hh")
_TRACK_TIMES = 4
_TRACK_VALUES = 12
_PIVOT = struct.Struct("<3f")
_QUAT = struct.Struct("<4h")
_GLOBAL_SEQUENCES = 0x14


class M2Error(WowlabError):
    """Base class for everything this module raises on purpose. `path` is
    the file concerned, when the caller named one."""

    def __init__(self, message: str, path: Path | None = None) -> None:
        super().__init__(message if path is None else f"{message} ({path})")
        self.path = path


class M2MalformedError(M2Error):
    """The bytes are not what the format says: a chunk or an array that
    runs past its end, a size that is not a whole number of entries, an
    index that names nothing, a magic that is not there."""


class M2UnsupportedError(M2Error):
    """A well-formed file in a form this reader does not read: an M2 that
    is not chunked, or a version below `MIN_VERSION`."""


class M2Chunk(BaseModel):
    """One chunk of the M2: its tag, the offset of its data in the file and
    its size. `read` says whether this module read it."""

    model_config = ConfigDict(frozen=True)

    tag: str
    offset: int
    size: int
    read: bool


class M2Texture(BaseModel):
    """One entry of the header's textures. `type` 0 is a file named by
    `file_data_id` (from `TXID`); other types are filled at render time
    (by the wiki, §12 and §17.3: 1 the body skin, 2 the object skin or
    cape, 6 the hair, 19 the eyes, 15 to 18 the guild tabard). `file_data_id` is `None` when
    `TXID` holds 0 for it or is absent. `filename` is the header's own
    (empty in the chunked models met)."""

    model_config = ConfigDict(frozen=True)

    index: int
    type: int
    flags: int
    file_data_id: int | None
    filename: str


class M2Material(BaseModel):
    """One entry of the header's materials (render flags): `flags` and the
    `blending_mode` enum, as stored."""

    model_config = ConfigDict(frozen=True)

    index: int
    flags: int
    blending_mode: int


class M2Attachment(BaseModel):
    """One attachment point: its `id` (the slot's attachment id), the bone
    it moves with and its position in model space, as stored (Z-up)."""

    model_config = ConfigDict(frozen=True)

    index: int
    id: int
    bone: int
    position: tuple[float, float, float]


class M2Bone(BaseModel):
    """One bone, as `bone_chain` reads it: its key bone id, flags, parent
    (-1: a root) and pivot (Z-up, model space), and its rotation track's
    first key. `rotation` is that key decoded (`decode_quaternion`, x, y, z,
    w; the rule is wowdev.wiki's, **[verify]**) when the track runs on a
    global sequence and holds a key; else None, and `rotation_why` says why.
    `rotation_global_sequence` and `rotation_interpolation` are the track's,
    as stored. A track's later keys, its per-animation arrays, and the
    translation and scale tracks are not read."""

    model_config = ConfigDict(frozen=True)

    index: int
    key_bone_id: int
    flags: int
    parent: int
    pivot: tuple[float, float, float]
    rotation: tuple[float, float, float, float] | None
    rotation_why: str | None = None
    rotation_global_sequence: int
    rotation_interpolation: int


class M2Vertices(BaseModel):
    """The header's vertices, one column per field, as stored (Z-up, the
    bind pose). `positions` and `normals` hold three floats per vertex,
    `uv` and `uv2` two; `bone_weights` and `bone_indices` four bytes per
    vertex."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    count: int
    positions: array[float]
    normals: array[float]
    uv: array[float]
    uv2: array[float]
    bone_weights: bytes
    bone_indices: bytes

    def position(self, i: int) -> tuple[float, float, float]:
        """The position of vertex `i`."""
        p = self.positions
        return (p[3 * i], p[3 * i + 1], p[3 * i + 2])


class M2Model(BaseModel):
    """What `parse_m2` read. `chunks` lists every chunk in file order;
    `skin_file_data_ids` holds one id per view (`view_count`), in order,
    `lod_skin_file_data_ids` the rest of `SFID`; `texture_file_data_ids`
    is `TXID` as stored. `notes` says what was read with a caveat (a
    version above the table, a `TXID` or `SFID` shorter than the header's
    counts)."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    version: int
    name: str
    flags: int
    chunks: tuple[M2Chunk, ...]
    bone_count: int
    view_count: int
    vertices: M2Vertices
    textures: tuple[M2Texture, ...]
    materials: tuple[M2Material, ...]
    texture_combos: tuple[int, ...]
    attachments: tuple[M2Attachment, ...]
    attachment_lookup: tuple[int, ...]
    skin_file_data_ids: tuple[int, ...]
    lod_skin_file_data_ids: tuple[int, ...]
    texture_file_data_ids: tuple[int, ...]
    notes: tuple[str, ...] = ()

    @property
    def chunk_tags(self) -> tuple[str, ...]:
        """Every chunk's tag, in file order."""
        return tuple(c.tag for c in self.chunks)

    def attachment(self, attachment_id: int) -> M2Attachment | None:
        """The attachment with id `attachment_id`, through the lookup when
        it names one, else the first with that id; `None` when none has."""
        lookup = self.attachment_lookup
        if 0 <= attachment_id < len(lookup) and lookup[attachment_id] != NO_INDEX:
            i = lookup[attachment_id]
            if i < len(self.attachments) and self.attachments[i].id == attachment_id:
                return self.attachments[i]
        return next((a for a in self.attachments if a.id == attachment_id), None)


class SkinSection(BaseModel):
    """One section (submesh). `geoset_id` is the stored `skinSectionId`
    (`group * 100 + value`); `index_start` is the u16 start with
    `level << 16` added (`index_start_stored` is the u16 as stored).
    `vertex_start` and `vertex_count` are positions in the vertex lookup,
    `index_start` and `index_count` in the indices."""

    model_config = ConfigDict(frozen=True)

    index: int
    geoset_id: int
    level: int
    vertex_start: int
    vertex_count: int
    index_start: int
    index_start_stored: int
    index_count: int
    bone_count: int
    bone_combo_index: int
    bone_influences: int
    center_bone_index: int
    center_position: tuple[float, float, float]
    sort_center_position: tuple[float, float, float]
    sort_radius: float

    @property
    def group(self) -> int:
        """The geoset group (`geoset_id // 100`)."""
        return self.geoset_id // 100

    @property
    def value(self) -> int:
        """The geoset value within its group (`geoset_id % 100`)."""
        return self.geoset_id % 100


class SkinBatch(BaseModel):
    """One batch (texture unit): which section it draws, with which
    material and texture combo. `NO_INDEX` (0xFFFF) in a u16 index field
    names nothing."""

    model_config = ConfigDict(frozen=True)

    index: int
    flags: int
    priority_plane: int
    shader_id: int
    section_index: int
    geoset_index: int
    color_index: int
    material_index: int
    material_layer: int
    texture_count: int
    texture_combo_index: int
    texture_coord_combo_index: int
    texture_weight_combo_index: int
    texture_transform_combo_index: int


class Skin(BaseModel):
    """What `parse_skin` read. `lookup` maps a skin vertex to an M2 vertex;
    `indices` are triangles (three per face) of skin vertices. `bone_count`
    is the bone-index array's count (one entry of four per lookup entry;
    not read). `vertex_base` is the header's u32 at 0x2C, as stored: the
    wiki names it `boneCountMax`, but in the fourteen skins of
    `humanmale_hd` and `humanfemale_hd` at 70959 it is where the skin's
    vertices start in the M2's vertex array (0 for the view skin, then the
    sum of the earlier skins' lookup counts; the last skin's base plus its
    lookup count is the M2's vertex count). For a LOD skin the lookup is,
    probably, relative to `vertex_base` (in those two models a triangle's
    mean edge is close to the view skin's with the base added and 2.7 to
    5.2 times longer without it; recorded in the derived fixture, **[verify]**
    for other models; §12 amendment of 2026-11-17). `triangles` does not add
    it: for the view skin it is 0."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    lookup: array[int]
    indices: array[int]
    bone_count: int
    vertex_base: int
    sections: tuple[SkinSection, ...]
    batches: tuple[SkinBatch, ...]

    def triangles(self, section: SkinSection) -> array[int]:
        """The section's triangle indices through the lookup, three per
        face: M2 vertex indices for a skin whose `vertex_base` is 0 (the
        view skin); for a LOD skin, probably relative to `vertex_base`
        (**[verify]**, §12 amendment of 2026-11-17)."""
        lookup = self.lookup
        end = section.index_start + section.index_count
        return array("I", map(lookup.__getitem__, self.indices[section.index_start : end]))


def _octets(data: Buffer) -> memoryview:
    """`data` as a flat, contiguous view of unsigned bytes."""
    view = memoryview(data)
    if not view.c_contiguous:
        view = memoryview(view.tobytes())
    return view if view.format == "B" and view.ndim == 1 else view.cast("B")


def _array(blob: memoryview, at: int, size: int, what: str, path: Path | None) -> tuple[int, int]:
    """The `count, offset` pair at `at`, refused unless `count` entries of
    `size` bytes from `offset` lie inside `blob`."""
    count, offset = _ARRAY.unpack_from(blob, at)
    if count and offset + count * size > len(blob):
        raise M2MalformedError(
            f"the {what} array ({count} of {size} bytes at offset {offset}) runs past the "
            f"end of its {len(blob)} bytes",
            path,
        )
    return count, offset


def _u16s(blob: memoryview, count: int, offset: int) -> array[int]:
    out = array("H")
    out.frombytes(blob[offset : offset + 2 * count])
    if sys.byteorder == "big":
        out.byteswap()
    return out


def _u32s(blob: memoryview, count: int, offset: int) -> array[int]:
    out = array("I")
    out.frombytes(blob[offset : offset + 4 * count])
    if sys.byteorder == "big":
        out.byteswap()
    return out


def _text(blob: memoryview, at: int, what: str, path: Path | None, cap: int | None = None) -> str:
    """The text of the `count, offset` array at `at`, up to its first NUL.
    With `cap`, a count above it is refused before any byte is copied."""
    count, offset = _array(blob, at, 1, what, path)
    if cap is not None and count > cap:
        raise M2MalformedError(f"the {what} holds {count} bytes; at most {cap} are read", path)
    raw = blob[offset : offset + count]
    return bytes(raw).split(b"\0", 1)[0].decode("latin-1")


def _entries(count: int, what: str, path: Path | None) -> None:
    """Refuse more entries of a u16-indexed array than a u16 can name."""
    if count > MAX_ENTRIES:
        raise M2MalformedError(
            f"the {what} array holds {count} entries; a u16 index names at most {MAX_ENTRIES}",
            path,
        )


def _chunks(data: memoryview, path: Path | None) -> list[M2Chunk]:
    chunks: list[M2Chunk] = []
    at = 0
    seen: set[str] = set()
    while at < len(data):
        if len(chunks) == MAX_CHUNKS:
            raise M2MalformedError(
                f"more than {MAX_CHUNKS} chunks (the first: "
                f"{' '.join(c.tag for c in chunks[:8])} …); real models have a handful",
                path,
            )
        if at + _CHUNK_HEAD.size > len(data):
            raise M2MalformedError(
                f"{len(data) - at} bytes after the last chunk at offset {at}: not a chunk header",
                path,
            )
        raw, size = _CHUNK_HEAD.unpack_from(data, at)
        start = at + _CHUNK_HEAD.size
        tag = raw.decode("latin-1")
        if start + size > len(data):
            raise M2MalformedError(
                f"chunk {tag!r} at offset {at} says {size} bytes; {len(data) - start} remain",
                path,
            )
        read = raw in _READ_TAGS
        if read and tag in seen:
            raise M2MalformedError(f"chunk {tag!r} appears twice (again at offset {at})", path)
        seen.add(tag)
        chunks.append(M2Chunk(tag=tag, offset=start, size=size, read=read))
        at = start + size
    return chunks


def _ids(data: memoryview, chunk: M2Chunk | None, path: Path | None, limit: int) -> tuple[int, ...]:
    """A chunk of u32 ids, refused when it holds more than `limit` of them
    (each becomes a Python int in a tuple)."""
    if chunk is None:
        return ()
    if chunk.size % 4:
        raise M2MalformedError(
            f"chunk {chunk.tag!r} holds {chunk.size} bytes, not a whole number of u32 ids", path
        )
    if chunk.size // 4 > limit:
        raise M2MalformedError(
            f"chunk {chunk.tag!r} holds {chunk.size // 4} ids; at most {limit} are read", path
        )
    return tuple(_u32s(data, chunk.size // 4, chunk.offset))


def _vertices(blob: memoryview, count: int, offset: int) -> M2Vertices:
    """The vertex columns, sliced out of one float and one u32 view of the
    array (no per-vertex Python loop)."""
    raw = blob[offset : offset + count * VERTEX_SIZE]
    floats = array("f")
    floats.frombytes(raw)
    words = array("I")
    words.frombytes(raw)
    if sys.byteorder == "big":
        floats.byteswap()
    k = _FLOATS_PER_VERTEX

    def columns(first: int, width: int) -> array[float]:
        out = array("f", bytes(4 * width * count))
        for j in range(width):
            out[j::width] = floats[first + j :: k]
        return out

    # Words 3 and 4 hold four bytes each (weights, then bone indices). They
    # are never read as numbers: `frombytes` then `tobytes` gives the bytes
    # back in file order on any machine, so no byte swap is needed.
    weights = words[3::k]
    bones = words[4::k]
    return M2Vertices(
        count=count,
        positions=columns(0, 3),
        normals=columns(5, 3),
        uv=columns(8, 2),
        uv2=columns(10, 2),
        bone_weights=weights.tobytes(),
        bone_indices=bones.tobytes(),
    )


def parse_m2(data: Buffer, path: Path | None = None) -> M2Model:
    """Read a chunked M2 (module docstring). `path` only names the file in
    errors. Raises `M2MalformedError` or `M2UnsupportedError`."""
    octets = _octets(data)
    if bytes(octets[:4]) == b"MD20":
        raise M2UnsupportedError(
            "the M2 is not chunked (it starts with MD20); only the chunked form is read", path
        )
    chunks = _chunks(octets, path)
    by_tag = {c.tag: c for c in chunks if c.read}
    md21 = by_tag.get("MD21")
    if md21 is None:
        raise M2MalformedError(
            f"no MD21 chunk among {len(chunks)} chunks"
            + (f" (the first: {' '.join(c.tag for c in chunks[:8])})" if chunks else ""),
            path,
        )
    blob = octets[md21.offset : md21.offset + md21.size]
    if len(blob) < _HEADER_END:
        raise M2MalformedError(
            f"the MD21 chunk holds {len(blob)} bytes; the MD20 header needs {_HEADER_END}", path
        )
    if bytes(blob[:4]) != b"MD20":
        raise M2MalformedError(f"the MD21 chunk starts {bytes(blob[:4])!r}, not MD20", path)
    (version,) = _U32.unpack_from(blob, 4)
    notes: list[str] = []
    if version < MIN_VERSION:
        raise M2UnsupportedError(
            f"M2 version {version} is below {MIN_VERSION}; its header is laid out differently",
            path,
        )
    if version > MAX_KNOWN_VERSION:
        notes.append(
            f"version {version} is above the wiki's table (which ends at {MAX_KNOWN_VERSION}); "
            f"read with the {MAX_KNOWN_VERSION} layout"
        )
    name = _text(blob, _NAME, "name", path)
    (flags,) = _U32.unpack_from(blob, _FLAGS)
    bone_count, _ = _array(blob, _BONES, BONE_SIZE, "bones", path)
    v_count, v_offset = _array(blob, _VERTICES, VERTEX_SIZE, "vertices", path)
    (view_count,) = _U32.unpack_from(blob, _VIEWS)
    t_count, t_offset = _array(blob, _TEXTURES, TEXTURE_SIZE, "textures", path)
    _entries(t_count, "textures", path)
    m_count, m_offset = _array(blob, _MATERIALS, MATERIAL_SIZE, "materials", path)
    _entries(m_count, "materials", path)
    c_count, c_offset = _array(blob, _TEXTURE_COMBOS, 2, "texture combos", path)
    _entries(c_count, "texture combos", path)
    a_count, a_offset = _array(blob, _ATTACHMENTS, ATTACHMENT_SIZE, "attachments", path)
    _entries(a_count, "attachments", path)
    l_count, l_offset = _array(blob, _ATTACHMENT_LOOKUP, 2, "attachment lookup", path)
    _entries(l_count, "attachment lookup", path)

    sfid = _ids(octets, by_tag.get("SFID"), path, MAX_ENTRIES)
    txid = _ids(octets, by_tag.get("TXID"), path, MAX_ENTRIES)
    if "SFID" not in by_tag:
        notes.append("no SFID chunk: the skin file data ids are unknown")
    elif len(sfid) < view_count:
        notes.append(f"SFID holds {len(sfid)} ids for {view_count} views")
    if "TXID" not in by_tag:
        notes.append("no TXID chunk: the texture file data ids are unknown")
    elif len(txid) != t_count:
        notes.append(f"TXID holds {len(txid)} ids for {t_count} textures")

    # The filenames may all point at one region: each is capped, and their
    # counts summed may not pass the blob, before any is copied.
    named = sum(
        _ARRAY.unpack_from(blob, t_offset + i * TEXTURE_SIZE + _TEXTURE.size)[0]
        for i in range(t_count)
    )
    if named > len(blob):
        raise M2MalformedError(
            f"the {t_count} texture filenames hold {named} bytes in all, more than the "
            f"{len(blob)} bytes of the MD21 chunk",
            path,
        )
    textures = []
    for i in range(t_count):
        at = t_offset + i * TEXTURE_SIZE
        kind, tex_flags = _TEXTURE.unpack_from(blob, at)
        fdid = txid[i] if i < len(txid) and txid[i] else None
        textures.append(
            M2Texture(
                index=i,
                type=kind,
                flags=tex_flags,
                file_data_id=fdid,
                filename=_text(
                    blob, at + _TEXTURE.size, f"texture {i}'s filename", path, MAX_FILENAME
                ),
            )
        )
    materials = []
    for i in range(m_count):
        mat_flags, blending = _MATERIAL.unpack_from(blob, m_offset + i * MATERIAL_SIZE)
        materials.append(M2Material(index=i, flags=mat_flags, blending_mode=blending))
    attachments = []
    for i in range(a_count):
        a_id, bone, _unknown, x, y, z = _ATTACHMENT.unpack_from(
            blob, a_offset + i * ATTACHMENT_SIZE
        )
        attachments.append(M2Attachment(index=i, id=a_id, bone=bone, position=(x, y, z)))
    views = min(view_count, len(sfid))
    return M2Model(
        version=version,
        name=name,
        flags=flags,
        chunks=tuple(chunks),
        bone_count=bone_count,
        view_count=view_count,
        vertices=_vertices(blob, v_count, v_offset),
        textures=tuple(textures),
        materials=tuple(materials),
        texture_combos=tuple(_u16s(blob, c_count, c_offset)),
        attachments=tuple(attachments),
        attachment_lookup=tuple(_u16s(blob, l_count, l_offset)),
        skin_file_data_ids=sfid[:views],
        lod_skin_file_data_ids=sfid[views:],
        texture_file_data_ids=txid,
        notes=tuple(notes),
    )


def _check_range(
    what: str, start: int, count: int, length: int, into: str, path: Path | None
) -> None:
    if start + count > length:
        raise M2MalformedError(
            f"{what} runs from {start} for {count}, past the {length} entries of the {into}", path
        )


def parse_skin(data: Buffer, path: Path | None = None, *, vertex_count: int | None = None) -> Skin:
    """Read a `.skin` file (module docstring). With `vertex_count` (the
    M2's), every lookup entry is checked against it. `path` only names the
    file in errors. Raises `M2MalformedError`."""
    blob = _octets(data)
    if len(blob) < _SKIN_HEADER_END:
        raise M2MalformedError(
            f"the skin holds {len(blob)} bytes; its header needs {_SKIN_HEADER_END}", path
        )
    if bytes(blob[:4]) != b"SKIN":
        raise M2MalformedError(f"the skin starts {bytes(blob[:4])!r}, not SKIN", path)
    lk_count, lk_offset = _array(blob, _SKIN_LOOKUP, 2, "vertex lookup", path)
    ix_count, ix_offset = _array(blob, _SKIN_INDICES, 2, "indices", path)
    bone_count, _ = _array(blob, _SKIN_BONES, _SKIN_BONE_SIZE, "bone indices", path)
    s_count, s_offset = _array(blob, _SKIN_SECTIONS, SECTION_SIZE, "sections", path)
    _entries(s_count, "sections", path)
    b_count, b_offset = _array(blob, _SKIN_BATCHES, BATCH_SIZE, "batches", path)
    _entries(b_count, "batches", path)
    (vertex_base,) = _U32.unpack_from(blob, _SKIN_VERTEX_BASE)

    lookup = _u16s(blob, lk_count, lk_offset)
    indices = _u16s(blob, ix_count, ix_offset)
    summed = sum(_SECTION.unpack_from(blob, s_offset + i * SECTION_SIZE)[5] for i in range(s_count))
    if summed > MAX_SECTION_INDEX_FACTOR * ix_count:
        raise M2MalformedError(
            f"the {s_count} sections draw {summed} indices in all, more than "
            f"{MAX_SECTION_INDEX_FACTOR} times the {ix_count} indices there are",
            path,
        )
    if ix_count % 3:
        raise M2MalformedError(f"{ix_count} indices are not whole triangles", path)
    if indices and max(indices) >= lk_count:
        raise M2MalformedError(
            f"an index names skin vertex {max(indices)}; the lookup holds {lk_count}", path
        )
    if vertex_count is not None and lookup and max(lookup) >= vertex_count:
        raise M2MalformedError(
            f"the lookup names vertex {max(lookup)}; the model holds {vertex_count}", path
        )
    sections = []
    for i in range(s_count):
        fields = _SECTION.unpack_from(blob, s_offset + i * SECTION_SIZE)
        (geoset, level, v_start, v_count, i_start, i_count, b_n, b_combo, b_infl, center_bone) = (
            fields[:10]
        )
        section = SkinSection(
            index=i,
            geoset_id=geoset,
            level=level,
            vertex_start=v_start,
            vertex_count=v_count,
            index_start=(level << 16) + i_start,
            index_start_stored=i_start,
            index_count=i_count,
            bone_count=b_n,
            bone_combo_index=b_combo,
            bone_influences=b_infl,
            center_bone_index=center_bone,
            center_position=(fields[10], fields[11], fields[12]),
            sort_center_position=(fields[13], fields[14], fields[15]),
            sort_radius=fields[16],
        )
        _check_range(f"section {i}'s vertices", v_start, v_count, lk_count, "lookup", path)
        _check_range(
            f"section {i}'s indices", section.index_start, i_count, ix_count, "indices", path
        )
        sections.append(section)
    batches = []
    for i in range(b_count):
        flags, plane, *rest = _BATCH.unpack_from(blob, b_offset + i * BATCH_SIZE)
        batch = SkinBatch(
            index=i,
            flags=flags,
            priority_plane=plane,
            shader_id=rest[0],
            section_index=rest[1],
            geoset_index=rest[2],
            color_index=rest[3],
            material_index=rest[4],
            material_layer=rest[5],
            texture_count=rest[6],
            texture_combo_index=rest[7],
            texture_coord_combo_index=rest[8],
            texture_weight_combo_index=rest[9],
            texture_transform_combo_index=rest[10],
        )
        if batch.section_index >= s_count:
            raise M2MalformedError(
                f"batch {i} names section {batch.section_index}; the skin holds {s_count}", path
            )
        batches.append(batch)
    return Skin(
        lookup=lookup,
        indices=indices,
        bone_count=bone_count,
        vertex_base=vertex_base,
        sections=tuple(sections),
        batches=tuple(batches),
    )


# ─── bones (M15-27) ──────────────────────────────────────────────────────────


def decode_quaternion(raw: tuple[int, ...]) -> tuple[float, float, float, float]:
    """An M2CompQuat's four i16 (x, y, z, w) as floats, each
    `(s < 0 ? s + 32768 : s - 32767) / 32767` (wowdev.wiki; **[verify]**
    as a rule: every key decoded on the two fixture body models at 70959 has
    a length within 5e-5 of 1, `docs/LAB_FORMATS.md` §12, amendment of
    2026-11-19)."""
    x, y, z, w = ((s + 32768 if s < 0 else s - 32767) / 32767 for s in raw)
    return (x, y, z, w)


def _md21(data: memoryview, model: M2Model, path: Path | None) -> memoryview:
    md21 = next((c for c in model.chunks if c.tag == "MD21"), None)
    if md21 is None or md21.offset + md21.size > len(data):
        raise M2MalformedError("the bytes given are not the model's: no MD21 chunk there", path)
    return data[md21.offset : md21.offset + md21.size]


def _first_rotation(
    blob: memoryview, at: int, path: Path | None
) -> tuple[tuple[float, float, float, float] | None, str | None, int, int]:
    """The first key of the rotation track at `at`: (value or None, why
    not, global sequence, interpolation)."""
    interpolation, global_sequence = _TRACK_HEAD.unpack_from(blob, at)
    if global_sequence < 0:
        return None, "its rotation is not on a global sequence", global_sequence, interpolation
    loops, _ = _array(blob, _GLOBAL_SEQUENCES, 4, "global sequences", path)
    if global_sequence >= loops:
        raise M2MalformedError(
            f"a bone's rotation names global sequence {global_sequence}; the model has {loops}",
            path,
        )
    times, times_at = _array(blob, at + _TRACK_TIMES, 8, "a rotation's timestamps", path)
    values, values_at = _array(blob, at + _TRACK_VALUES, 8, "a rotation's values", path)
    if not values or not times:
        return None, "its rotation track holds no keys", global_sequence, interpolation
    # The first per-sequence array only: one u32 time and one 8-byte key a
    # key (a spline's key holds two tangents after its value; the value comes
    # first, so the first value is read the same way).
    keys, _ = _array(blob, times_at, 4, "a rotation's first timestamps", path)
    stride = _QUAT.size * (3 if interpolation in (2, 3) else 1)
    count, key_at = _array(blob, values_at, stride, "a rotation's first keys", path)
    if count != keys:
        raise M2MalformedError(
            f"a bone's rotation holds {keys} timestamps and {count} keys in its first array", path
        )
    if not count:
        return None, "its rotation track holds no keys", global_sequence, interpolation
    return decode_quaternion(_QUAT.unpack_from(blob, key_at)), None, global_sequence, interpolation


def bone_chain(
    data: Buffer, model: M2Model, bone: int, path: Path | None = None
) -> tuple[M2Bone, ...]:
    """Bone `bone` of `model` (`data` the bytes `parse_m2` read it from),
    then its parent, and so on to the root: each bone's head, pivot and the
    first key of its rotation (`M2Bone`). Raises `M2MalformedError` for an
    index past the bones, a chain that loops or runs deeper than
    `MAX_BONE_DEPTH`, or a track that runs past the blob (the module
    docstring)."""
    blob = _md21(_octets(data), model, path)
    count, offset = _array(blob, _BONES, BONE_SIZE, "bones", path)
    chain: list[M2Bone] = []
    seen: set[int] = set()
    at_bone = bone
    while at_bone >= 0:
        if at_bone >= count:
            raise M2MalformedError(f"bone {at_bone} is named; the model has {count} bones", path)
        if at_bone in seen:
            raise M2MalformedError(f"bone {at_bone}'s parents loop back to it", path)
        if len(chain) == MAX_BONE_DEPTH:
            raise M2MalformedError(
                f"bone {bone}'s chain runs deeper than {MAX_BONE_DEPTH} bones", path
            )
        seen.add(at_bone)
        at = offset + BONE_SIZE * at_bone
        key, flags, parent = _BONE_HEAD.unpack_from(blob, at)
        rotation, why, global_sequence, interpolation = _first_rotation(
            blob, at + _BONE_ROTATION, path
        )
        chain.append(
            M2Bone(
                index=at_bone,
                key_bone_id=key,
                flags=flags,
                parent=parent,
                pivot=_PIVOT.unpack_from(blob, at + _BONE_PIVOT),
                rotation=rotation,
                rotation_why=why,
                rotation_global_sequence=global_sequence,
                rotation_interpolation=interpolation,
            )
        )
        at_bone = parent
    return tuple(chain)
