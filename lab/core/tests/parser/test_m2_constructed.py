"""`m2` on constructed files (M15-08; labelled `constructed`, ADR-0033):
a minimal M2 (one triangle, one texture, `TXID`, `SFID`) and skin (two
sections, one with level 1) with known values, and hostile files: an
array past its chunk, a count of 2**32 - 1, a chunk past the file, an
index past the lookup. Real files are graded by `test_m2_derived.py`
(the derived fixture) and `test_m2_live.py` (`@live`)."""

from __future__ import annotations

import struct
import tracemalloc
from pathlib import Path

import pytest
from _m2_build import ONE_TRIANGLE, chunk, chunked, md20, skin

from wowlab_core import m2
from wowlab_core.errors import WowlabError

pytestmark = pytest.mark.parser

U32_MAX = 2**32 - 1


def _minimal() -> m2.M2Model:
    return m2.parse_m2(chunked(bytes(md20())))


def test_minimal_model_reads_every_known_value_constructed() -> None:
    model = _minimal()
    assert model.version == 272 and model.name == "Constructed" and model.flags == 0x80
    assert model.chunk_tags == ("MD21", "LDV1", "AFID", "SFID", "TXID")
    assert [c.read for c in model.chunks] == [True, False, False, True, True]
    assert model.chunks[0].offset == 8
    assert model.bone_count == 2 and model.view_count == 1
    assert model.skin_file_data_ids == (500,) and model.lod_skin_file_data_ids == (501,)
    assert model.texture_file_data_ids == (900,)
    assert model.textures == (
        m2.M2Texture(index=0, type=0, flags=3, file_data_id=900, filename=""),
    )
    assert model.materials == (m2.M2Material(index=0, flags=0x10, blending_mode=2),)
    assert model.texture_combos == (0,)
    assert model.attachments == (
        m2.M2Attachment(index=0, id=11, bone=1, position=(0.0, 0.25, 1.75)),
    )
    assert model.attachment(11) == model.attachments[0]
    assert model.attachment(5) is None
    assert model.notes == ()


def test_minimal_model_vertices_are_columns_as_stored_constructed() -> None:
    v = _minimal().vertices
    assert v.count == 3
    assert list(v.positions) == [c for p, *_ in ONE_TRIANGLE for c in p]
    assert v.position(2) == (0.0, 0.0, 2.0)
    assert list(v.normals) == [0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, -1.0, 0.0]
    assert list(v.uv) == [0.0, 0.0, 1.0, 0.0, 0.0, 1.0]
    assert list(v.uv2) == [0.5, 0.5, 0.5, 0.5, 0.25, 0.75]
    assert v.bone_weights == b"\xff\0\0\0\x80\x7f\0\0\0\0\0\xff"
    assert v.bone_indices == b"\0\0\0\0\x01\x02\0\0\0\0\0\x03"


def test_texture_types_other_than_zero_have_no_file_id_constructed() -> None:
    blob = md20(textures=((1, 0), (6, 3), (0, 2), (19, 0)))
    model = m2.parse_m2(chunked(bytes(blob), txid=(0, 0, 4242, 0)))
    assert [(t.type, t.file_data_id) for t in model.textures] == [
        (1, None),
        (6, None),
        (0, 4242),
        (19, None),
    ]


def test_skin_with_a_level_one_section_adds_level_shift_to_index_start_constructed() -> None:
    # Section 1's stored index start is 2; level 1 puts it at 65,538.
    indices = [0, 1, 2] + [0] * (65_538 - 3) + [2, 1, 0]
    data = skin(
        lookup=(0, 1, 2),
        indices=indices,
        sections=((0, 0, 0, 3, 0, 3), (1301, 1, 0, 3, 2, 3)),
        batches=((0, 0, 0), (1, 0, 0)),
    )
    result = m2.parse_skin(data, vertex_count=3)
    assert list(result.lookup) == [0, 1, 2] and len(result.indices) == 65_541
    first, second = result.sections
    assert (first.geoset_id, first.level, first.index_start, first.index_count) == (0, 0, 0, 3)
    assert (second.geoset_id, second.group, second.value) == (1301, 13, 1)
    assert (second.level, second.index_start_stored, second.index_start) == (1, 2, 65_538)
    assert (second.vertex_start, second.vertex_count) == (0, 3)
    assert list(result.triangles(first)) == [0, 1, 2]
    assert list(result.triangles(second)) == [2, 1, 0]
    assert second.center_position == (0.5, 0.5, 1.0) and second.sort_radius == 2.0
    assert [b.section_index for b in result.batches] == [0, 1]
    batch = result.batches[1]
    assert (batch.flags, batch.shader_id, batch.texture_count) == (0x10, 0x8000, 1)
    assert batch.color_index == m2.NO_INDEX and batch.texture_combo_index == 0
    assert result.bone_count == 3 and result.vertex_base == 0


def test_skin_keeps_the_vertex_base_as_stored_constructed() -> None:
    assert m2.parse_skin(skin(vertex_base=37_813)).vertex_base == 37_813


def test_version_above_the_table_is_read_with_a_note_constructed() -> None:
    model = m2.parse_m2(chunked(bytes(md20(version=275))))
    assert model.version == 275
    assert model.notes == (
        "version 275 is above the wiki's table (which ends at 274); read with the 274 layout",
    )
    assert model.vertices.count == 3


def test_missing_and_short_id_chunks_are_noted_constructed() -> None:
    model = m2.parse_m2(chunked(bytes(md20(views=2)), sfid=(7,), txid=None))
    assert model.skin_file_data_ids == (7,) and model.lod_skin_file_data_ids == ()
    assert model.textures[0].file_data_id is None
    assert model.notes == (
        "SFID holds 1 ids for 2 views",
        "no TXID chunk: the texture file data ids are unknown",
    )


@pytest.mark.parametrize(
    ("field", "size", "what"),
    [
        (0x2C, 88, "bones"),
        (0x3C, 48, "vertices"),
        (0x50, 16, "textures"),
        (0x70, 4, "materials"),
        (0x80, 2, "texture combos"),
        (0xF0, 40, "attachments"),
        (0xF8, 2, "attachment lookup"),
        (0x08, 1, "name"),
    ],
)
def test_hostile_count_of_two_to_the_32_is_refused_without_allocating_constructed(
    field: int, size: int, what: str
) -> None:
    blob = md20()
    struct.pack_into("<I", blob, field, U32_MAX)
    data = chunked(bytes(blob))
    tracemalloc.start()
    try:
        with pytest.raises(m2.M2MalformedError, match=f"the {what} array \\({U32_MAX} of"):
            m2.parse_m2(data)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 1_000_000, f"{peak} bytes allocated before the refusal"


@pytest.mark.parametrize("field", [0x3C, 0x50, 0xF0])
def test_hostile_offset_past_the_chunk_is_refused_constructed(field: int) -> None:
    blob = md20()
    count, _ = struct.unpack_from("<II", blob, field)
    struct.pack_into("<I", blob, field + 4, len(blob) - 1)
    with pytest.raises(m2.M2MalformedError, match="runs past the end of its"):
        m2.parse_m2(chunked(bytes(blob)))
    assert count > 0


def test_array_ending_exactly_at_the_chunk_end_is_read_constructed() -> None:
    blob = md20(lookup=(0, 0xFFFF))
    struct.pack_into("<II", blob, 0xF8, 2, len(blob) - 4)
    blob[-4:] = b"\x05\0\x06\0"
    assert m2.parse_m2(chunked(bytes(blob))).attachment_lookup == (5, 6)


def test_md21_offsets_never_reach_past_md21_into_the_next_chunk_constructed() -> None:
    blob = md20()
    struct.pack_into("<II", blob, 0xF8, 4, len(blob))  # the next chunk's bytes follow
    with pytest.raises(m2.M2MalformedError, match="attachment lookup"):
        m2.parse_m2(chunked(bytes(blob)))


def test_hostile_chunk_size_past_the_file_is_refused_constructed() -> None:
    data = bytearray(chunked(bytes(md20())))
    struct.pack_into("<I", data, 4, U32_MAX)
    with pytest.raises(m2.M2MalformedError, match=f"says {U32_MAX} bytes"):
        m2.parse_m2(data)


def test_trailing_bytes_shorter_than_a_chunk_header_are_refused_constructed() -> None:
    with pytest.raises(m2.M2MalformedError, match="not a chunk header"):
        m2.parse_m2(chunked(bytes(md20())) + b"TXI")


@pytest.mark.parametrize(
    ("data", "error", "match"),
    [
        (b"", m2.M2MalformedError, "no MD21 chunk"),
        (chunk(b"SFID", b"\0" * 4), m2.M2MalformedError, "no MD21 chunk"),
        (chunk(b"MD21", b"MD20" + bytes(20)), m2.M2MalformedError, "header needs 256"),
        (chunk(b"MD21", b"MD2X" + bytes(0x200)), m2.M2MalformedError, "not MD20"),
        (bytes(md20()), m2.M2UnsupportedError, "not chunked"),
        (chunked(bytes(md20(version=263))), m2.M2UnsupportedError, "below 264"),
        (
            chunked(bytes(md20()), sfid=None, txid=None) + chunk(b"TXID", b"\0" * 6),
            m2.M2MalformedError,
            "not a whole number",
        ),
        (chunked(bytes(md20())) + chunk(b"SFID", b""), m2.M2MalformedError, "appears twice"),
    ],
)
def test_refusals_name_what_is_wrong_constructed(
    data: bytes, error: type[m2.M2Error], match: str
) -> None:
    with pytest.raises(error, match=match) as caught:
        m2.parse_m2(data)
    assert isinstance(caught.value, WowlabError)


def test_unknown_chunks_appearing_twice_are_listed_constructed() -> None:
    data = chunked(bytes(md20()), extra=((b"EXP2", b"ab"), (b"EXP2", b"")))
    model = m2.parse_m2(data)
    assert [(c.tag, c.size, c.read) for c in model.chunks[1:3]] == [
        ("EXP2", 2, False),
        ("EXP2", 0, False),
    ]


def test_parse_takes_a_read_only_memoryview_and_a_bytearray_constructed() -> None:
    data = chunked(bytes(md20()))
    assert m2.parse_m2(memoryview(data).toreadonly()).version == 272
    assert m2.parse_m2(bytearray(data)).version == 272
    assert m2.parse_skin(memoryview(skin()).toreadonly()).sections[0].index_count == 3


def test_error_names_the_path_when_given_constructed() -> None:
    where = Path("model.m2")
    with pytest.raises(m2.M2MalformedError) as caught:
        m2.parse_m2(b"", where)
    assert caught.value.path == where and str(caught.value).endswith("(model.m2)")


@pytest.mark.parametrize(
    "field", [0x04, 0x0C, 0x14, 0x1C, 0x24], ids=["lookup", "indices", "bones", "sec", "batch"]
)
def test_hostile_skin_count_of_two_to_the_32_is_refused_without_allocating_constructed(
    field: int,
) -> None:
    data = bytearray(skin())
    struct.pack_into("<I", data, field, U32_MAX)
    tracemalloc.start()
    try:
        with pytest.raises(m2.M2MalformedError, match=f"\\({U32_MAX} of"):
            m2.parse_skin(data)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 1_000_000


def test_hostile_skin_offset_past_the_end_is_refused_constructed() -> None:
    data = bytearray(skin())
    struct.pack_into("<I", data, 0x20, len(data))  # the sections' offset
    with pytest.raises(m2.M2MalformedError, match="the sections array"):
        m2.parse_skin(data)


@pytest.mark.parametrize(
    ("kwargs", "vertex_count", "match"),
    [
        ({"indices": (0, 1, 3)}, None, "an index names skin vertex 3; the lookup holds 3"),
        ({"indices": (0, 1, 2, 0)}, None, "4 indices are not whole triangles"),
        ({"lookup": (0, 1, 9)}, 3, "the lookup names vertex 9; the model holds 3"),
        ({"sections": ((0, 0, 1, 3, 0, 3),)}, None, "section 0's vertices runs from 1 for 3"),
        ({"sections": ((0, 0, 0, 3, 0, 6),)}, None, "section 0's indices runs from 0 for 6"),
        (
            {"sections": ((0, 1, 0, 3, 0, 3),)},
            None,
            "section 0's indices runs from 65536 for 3, past the 3 entries",
        ),
        ({"batches": ((1, 0, 0),)}, None, "batch 0 names section 1; the skin holds 1"),
    ],
)
def test_skin_ranges_are_checked_constructed(
    kwargs: dict[str, object], vertex_count: int | None, match: str
) -> None:
    with pytest.raises(m2.M2MalformedError, match=match):
        m2.parse_skin(skin(**kwargs), vertex_count=vertex_count)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("data", "match"),
    [(b"SKIN" + bytes(10), "header needs 48"), (b"NIKS" + bytes(60), "not SKIN")],
)
def test_skin_magic_and_header_refusals_constructed(data: bytes, match: str) -> None:
    with pytest.raises(m2.M2MalformedError, match=match):
        m2.parse_skin(data)


def test_empty_skin_arrays_are_read_constructed() -> None:
    result = m2.parse_skin(skin(lookup=(), indices=(), sections=(), batches=()))
    assert len(result.lookup) == 0 and result.sections == () and result.batches == ()


def _shared_filename_model(textures: int, length: int) -> bytes:
    """Constructed hostile file: `textures` textures whose filename arrays
    all point at one NUL-free run of `length` bytes (the model's name)."""
    blob = md20(name=b"A" * length, textures=((0, 0),) * textures)
    _, name_at = struct.unpack_from("<II", blob, 0x08)
    _, tex_at = struct.unpack_from("<II", blob, 0x50)
    for i in range(textures):
        struct.pack_into("<II", blob, tex_at + 16 * i + 8, length, name_at)
    return chunked(bytes(blob), txid=(0,) * textures)


def test_hostile_shared_long_filename_is_refused_without_copies_constructed() -> None:
    data = _shared_filename_model(2_000, 1_000)
    tracemalloc.start()
    try:
        with pytest.raises(m2.M2MalformedError, match="texture filenames hold 2000000 bytes"):
            m2.parse_m2(data)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 2_000_000, f"{peak} bytes for {len(data)} bytes of input"


def test_hostile_filename_over_the_cap_is_refused_constructed() -> None:
    with pytest.raises(m2.M2MalformedError, match="texture 0's filename holds 1025 bytes"):
        m2.parse_m2(_shared_filename_model(1, 1_025))


def test_filename_within_the_cap_is_read_up_to_its_nul_constructed() -> None:
    blob = md20(name=b"tex.blp\0junk")
    _, name_at = struct.unpack_from("<II", blob, 0x08)
    _, tex_at = struct.unpack_from("<II", blob, 0x50)
    struct.pack_into("<II", blob, tex_at + 8, 12, name_at)
    assert m2.parse_m2(chunked(bytes(blob))).textures[0].filename == "tex.blp"


@pytest.mark.parametrize("what", ["textures", "materials"])
def test_hostile_more_entries_than_a_u16_names_is_refused_constructed(what: str) -> None:
    many = ((0, 0),) * (m2.MAX_ENTRIES + 1)
    blob = md20(**{what: many})  # type: ignore[arg-type]
    with pytest.raises(m2.M2MalformedError, match=f"the {what} array holds 65536 entries"):
        m2.parse_m2(chunked(bytes(blob), txid=None))


def test_hostile_more_batches_than_a_u16_names_is_refused_constructed() -> None:
    data = skin(batches=((0, 0, 0),) * (m2.MAX_ENTRIES + 1))
    with pytest.raises(m2.M2MalformedError, match="the batches array holds 65536 entries"):
        m2.parse_skin(data)


def test_chunk_count_is_capped_constructed() -> None:
    extra = ((b"EXP2", b""),) * (m2.MAX_CHUNKS - 3)  # MD21, SFID and TXID make 1,024
    assert len(m2.parse_m2(chunked(bytes(md20()), extra=extra)).chunks) == m2.MAX_CHUNKS
    with pytest.raises(m2.M2MalformedError, match="more than 1024 chunks") as caught:
        m2.parse_m2(chunked(bytes(md20()), extra=(*extra, (b"EXP2", b""))))
    assert len(str(caught.value)) < 200


def test_no_md21_message_names_eight_tags_and_a_count_constructed() -> None:
    data = b"".join(chunk(f"T{i:03d}".encode(), b"") for i in range(20))
    with pytest.raises(m2.M2MalformedError) as caught:
        m2.parse_m2(data)
    assert str(caught.value) == (
        "no MD21 chunk among 20 chunks (the first: T000 T001 T002 T003 T004 T005 T006 T007)"
    )


def test_hostile_sections_summing_past_four_times_the_indices_are_refused_constructed() -> None:
    one = (0, 0, 0, 3, 0, 3)
    assert len(m2.parse_skin(skin(sections=(one,) * 4)).sections) == 4
    with pytest.raises(m2.M2MalformedError, match="the 5 sections draw 15 indices in all"):
        m2.parse_skin(skin(sections=(one,) * 5))
