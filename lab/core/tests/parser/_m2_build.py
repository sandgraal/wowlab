"""Constructed M2 and `.skin` files for the `m2` tests (M15-08; labelled
`constructed`: ADR-0033 lets an asset-format parser be graded on built
files beside the derived fixture and the `@live` tests). Every value is
chosen here and known, so the tests assert it exactly.

`md20` lays the MD20 header out at the offsets `docs/LAB_FORMATS.md` §12
gives, with each array's entries after the header; `chunked` wraps it in
`MD21` and adds `SFID`, `TXID` and any other chunk; `skin` builds a
`SKIN` file with its 64-byte header (five arrays, the u32 at 0x2C and the
shadow-batch array, left empty).
"""

from __future__ import annotations

import struct
from collections.abc import Sequence

HEADER = 0x130  # the MD20 header's size up to the texture-combiner combos

Vertex = tuple[
    tuple[float, float, float],  # position
    bytes,  # four bone weights
    bytes,  # four bone indices
    tuple[float, float, float],  # normal
    tuple[float, float],  # uv
    tuple[float, float],  # uv2
]
Section = tuple[int, int, int, int, int, int]  # geoset, level, v start, v count, i start, i count
# A bone (M15-27): parent, pivot, and its rotation track as (global sequence,
# interpolation, keys as four i16 each), or None for a track with no array
# on no global sequence. Every other field is zero, the key bone id -1, and
# the translation and scale tracks on no global sequence.
Bone = tuple[
    int,
    tuple[float, float, float],
    tuple[int, int, Sequence[tuple[int, int, int, int]]] | None,
]


def quat_i16(v: float) -> int:
    """The i16 that `m2.decode_quaternion` reads as `v` (the inverse of
    `(s < 0 ? s + 32768 : s - 32767) / 32767`, rounded)."""
    return round(v * 32767) - 32768 if v >= 0 else round(v * 32767) + 32767


def quat(x: float, y: float, z: float, w: float) -> tuple[int, int, int, int]:
    return (quat_i16(x), quat_i16(y), quat_i16(z), quat_i16(w))


ONE_TRIANGLE: list[Vertex] = [
    ((0.0, 0.0, 0.0), b"\xff\0\0\0", b"\0\0\0\0", (0.0, 0.0, 1.0), (0.0, 0.0), (0.5, 0.5)),
    ((1.0, 0.0, 0.0), b"\x80\x7f\0\0", b"\x01\x02\0\0", (0.0, 0.0, 1.0), (1.0, 0.0), (0.5, 0.5)),
    ((0.0, 0.0, 2.0), b"\0\0\0\xff", b"\0\0\0\x03", (0.0, -1.0, 0.0), (0.0, 1.0), (0.25, 0.75)),
]


def _arr(header: bytearray, at: int, count: int, offset: int) -> None:
    struct.pack_into("<II", header, at, count, offset)


def md20(
    *,
    version: int = 272,
    name: bytes = b"Constructed\0",
    flags: int = 0x80,
    vertices: Sequence[Vertex] = ONE_TRIANGLE,
    bones: int = 2,
    views: int = 1,
    textures: Sequence[tuple[int, int]] = ((0, 3),),
    materials: Sequence[tuple[int, int]] = ((0x10, 2),),
    combos: Sequence[int] = (0,),
    attachments: Sequence[tuple[int, int, tuple[float, float, float]]] = (
        (11, 1, (0.0, 0.25, 1.75)),
    ),
    lookup: Sequence[int] = (0xFFFF,) * 11 + (0,),
    bone_list: Sequence[Bone] | None = None,
    global_sequences: Sequence[int] = (),
) -> bytearray:
    """An MD20 blob: the header, then each array's entries, in that order.
    With `bone_list`, the bones are those (`bones` is ignored), each
    rotation track's keys placed before the bone array, and the global
    sequences' durations at 0x14."""
    header = bytearray(HEADER)
    body = bytearray()

    def place(data: bytes) -> int:
        offset = HEADER + len(body)
        body.extend(data)
        body.extend(b"\0" * (-len(body) % 16))
        return offset

    header[0:4] = b"MD20"
    struct.pack_into("<I", header, 4, version)
    _arr(header, 0x08, len(name), place(name))
    struct.pack_into("<I", header, 0x10, flags)
    if bone_list is None:
        _arr(header, 0x2C, bones, place(bytes(88 * bones)))
    else:
        _arr(
            header,
            0x14,
            len(global_sequences),
            place(struct.pack(f"<{len(global_sequences)}I", *global_sequences)),
        )
        records = []
        for parent, pivot, rotation in bone_list:
            record = bytearray(88)
            struct.pack_into("<iIhH", record, 0, -1, 0, parent, 0)
            for track in (16, 36, 56):
                struct.pack_into("<Hh", record, track, 0, -1)
            if rotation is not None:
                gseq, interpolation, keys = rotation
                times = place(struct.pack(f"<{len(keys)}I", *range(len(keys))))
                values = place(b"".join(struct.pack("<4h", *k) for k in keys))
                outer_t = place(struct.pack("<II", len(keys), times))
                outer_v = place(struct.pack("<II", len(keys), values))
                struct.pack_into("<HhIIII", record, 36, interpolation, gseq, 1, outer_t, 1, outer_v)
            struct.pack_into("<3f", record, 76, *pivot)
            records.append(bytes(record))
        _arr(header, 0x2C, len(records), place(b"".join(records)))
    raw = b"".join(
        struct.pack("<3f", *p) + w + b + struct.pack("<3f2f2f", *n, *uv, *uv2)
        for p, w, b, n, uv, uv2 in vertices
    )
    _arr(header, 0x3C, len(vertices), place(raw))
    struct.pack_into("<I", header, 0x44, views)
    _arr(header, 0x50, len(textures), place(b"".join(struct.pack("<II8x", *t) for t in textures)))
    _arr(header, 0x70, len(materials), place(b"".join(struct.pack("<HH", *m) for m in materials)))
    _arr(header, 0x80, len(combos), place(struct.pack(f"<{len(combos)}H", *combos)))
    raw = b"".join(struct.pack("<IHH3f20x", i, b, 0, *p) for i, b, p in attachments)
    _arr(header, 0xF0, len(attachments), place(raw))
    _arr(header, 0xF8, len(lookup), place(struct.pack(f"<{len(lookup)}H", *lookup)))
    return header + body


def chunk(tag: bytes, data: bytes) -> bytes:
    return tag + struct.pack("<I", len(data)) + data


def ids(values: Sequence[int]) -> bytes:
    return struct.pack(f"<{len(values)}I", *values)


def chunked(
    blob: bytes,
    *,
    sfid: Sequence[int] | None = (500, 501),
    txid: Sequence[int] | None = (900,),
    extra: Sequence[tuple[bytes, bytes]] = ((b"LDV1", bytes(16)), (b"AFID", bytes(8))),
) -> bytes:
    """`MD21`, then each `extra` chunk, then `SFID` and `TXID` (when given)."""
    out = bytearray(chunk(b"MD21", blob))
    for tag, data in extra:
        out += chunk(tag, data)
    if sfid is not None:
        out += chunk(b"SFID", ids(sfid))
    if txid is not None:
        out += chunk(b"TXID", ids(txid))
    return bytes(out)


def skin(
    *,
    lookup: Sequence[int] = (0, 1, 2),
    indices: Sequence[int] = (0, 1, 2),
    sections: Sequence[Section] = ((0, 0, 0, 3, 0, 3),),
    batches: Sequence[tuple[int, int, int]] = ((0, 0, 0),),  # section, material, combo
    vertex_base: int = 0,
) -> bytes:
    """A `SKIN` file: the 64-byte header, then the lookup, the indices, the
    bone indices (four zero bytes per lookup entry), the sections and the
    batches."""
    header = bytearray(64)
    header[0:4] = b"SKIN"
    body = bytearray()

    def place(data: bytes) -> int:
        offset = 64 + len(body)
        body.extend(data)
        body.extend(b"\0" * (-len(body) % 16))
        return offset

    _arr(header, 0x04, len(lookup), place(struct.pack(f"<{len(lookup)}H", *lookup)))
    _arr(header, 0x0C, len(indices), place(struct.pack(f"<{len(indices)}H", *indices)))
    _arr(header, 0x14, len(lookup), place(bytes(4 * len(lookup))))
    raw = b"".join(
        struct.pack("<10H7f", g, lv, vs, vc, ist, ic, 1, 0, 1, 0, 0.5, 0.5, 1.0, 0.5, 0.5, 1.0, 2.0)
        for g, lv, vs, vc, ist, ic in sections
    )
    _arr(header, 0x1C, len(sections), place(raw))
    raw = b"".join(
        struct.pack("<Bb11H", 0x10, 0, 0x8000, s, s, 0xFFFF, m, 0, 1, c, 0, 0xFFFF, 0)
        for s, m, c in batches
    )
    _arr(header, 0x24, len(batches), place(raw))
    struct.pack_into("<I", header, 0x2C, vertex_base)
    return bytes(header + body)
