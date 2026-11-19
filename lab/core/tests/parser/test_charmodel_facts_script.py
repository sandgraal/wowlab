"""`scripts/charmodel_facts.py` on a constructed install (M15-11; labelled
`constructed`): its refusals, which are `renderplan_facts.py`'s (an output
already there, one inside an install, a missing table), and a plan whose
files the install does not hold ends with exit 1, nothing written and
nothing in the install changed; and the held-items probe's walk of the bone
array (M15-24) on a stand-in body model. The real record is written on the
owner's install (`fixtures/macos-70959/derived/charmodel.json`)."""

from __future__ import annotations

import importlib.util
import random
import struct
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from _casc_build import FLAVOR, make_source_install, tree_state
from test_renderplan_facts_script import SANGRAAL, _tables

from wowlab_core import m2

pytestmark = pytest.mark.parser

REPO = Path(__file__).resolve().parents[4]
SCRIPT = REPO / "scripts" / "charmodel_facts.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("charmodel_facts", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


script = _load()


def _install(tmp: Path) -> Path:
    return make_source_install(
        tmp / "install", stored=[], encoding_rows=[], root_blocks=[], rng=random.Random(10)
    )


def _run(root: Path, tables: Path, out: Path) -> int:
    return int(
        script.main(
            [
                "--root",
                str(root),
                "--flavor",
                FLAVOR,
                "--tables",
                str(tables),
                "--char",
                f"Sangraal-Sett={SANGRAAL}",
                "--captured",
                "2026-11-18",
                "--out",
                str(out),
            ]
        )
    )


def test_files_the_install_lacks_end_with_exit_1_and_nothing_written_constructed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _install(tmp_path)
    before = tree_state(root)
    out = tmp_path / "out.json"
    assert _run(root, _tables(tmp_path), out) == 1
    assert capsys.readouterr().err.startswith("charmodel_facts: ")
    assert not out.exists()
    assert tree_state(root) == before


def test_refuses_an_output_already_there_constructed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out.json"
    out.write_text("{}", encoding="utf-8")
    assert _run(_install(tmp_path), _tables(tmp_path), out) == 1
    assert "never replaced" in capsys.readouterr().err
    assert out.read_text(encoding="utf-8") == "{}"


def test_refuses_an_output_inside_an_install_constructed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _install(tmp_path)
    before = tree_state(root)
    assert _run(root, _tables(tmp_path), root / "out.json") == 1
    assert "inside a game install" in capsys.readouterr().err
    assert tree_state(root) == before


def test_refuses_a_missing_table_constructed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "out.json"
    assert _run(_install(tmp_path), _tables(tmp_path, skip="ChrModelTextureLayer"), out) == 1
    assert "no ChrModelTextureLayer for" in capsys.readouterr().err
    assert not out.exists()


class _Body:
    """A stand-in for `m2.M2Model` with only what the probe of M15-24 reads:
    an MD21 chunk holding a bone array, and the attachments (constructed)."""

    def __init__(
        self,
        bones: list[tuple[int, tuple[float, float, float]]],
        points: list[tuple[int, int, tuple[float, float, float]]],
        heads: dict[int, tuple[int, int]] | None = None,
        tracks: dict[int, tuple[tuple[int, int, int], tuple[int, int, int]]] | None = None,
    ) -> None:
        """`heads` gives a bone its key bone id and flags (else -1 and 0);
        `tracks` its translation and rotation tracks as (global sequence,
        sequences, keys in the first), the per-sequence arrays written
        after the bone array (else no sequence, global -1)."""
        self.attachments = tuple(
            m2.M2Attachment(index=i, id=pid, bone=bone, position=pos)
            for i, (pid, bone, pos) in enumerate(points)
        )
        at = 0x100
        blob = bytearray(at + 88 * len(bones))
        struct.pack_into("<II", blob, 0x2C, len(bones), at)
        for i, (parent, pivot) in enumerate(bones):
            key, flags = (heads or {}).get(i, (-1, 0))
            struct.pack_into("<iIh", blob, at + 88 * i, key, flags, parent)
            struct.pack_into("<3f", blob, at + 88 * i + 76, *pivot)
            for track_at in (16, 36):
                struct.pack_into("<h", blob, at + 88 * i + track_at + 2, -1)
        for i, pair in (tracks or {}).items():
            for track_at, (global_sequence, sequences, first) in zip((16, 36), pair, strict=True):
                arrays = len(blob)
                blob += struct.pack("<II", first, 0) * sequences
                track = at + 88 * i + track_at
                struct.pack_into("<h", blob, track + 2, global_sequence)
                struct.pack_into("<II", blob, track + 4, sequences, arrays)
        self.data = bytes(blob)
        self.chunks = (m2.M2Chunk(tag="MD21", offset=0, size=len(blob), read=True),)

    def attachment(self, point_id: int) -> m2.M2Attachment | None:
        return next((a for a in self.attachments if a.id == point_id), None)


ZERO = (0.0, 0.0, 0.0)


def test_held_point_parents_stop_at_a_root_or_after_two_levels_constructed() -> None:
    bones = [
        (-1, (0.0, 0.0, 1.0)),  # 0: a root away from the origin
        (0, ZERO),  # 1
        (1, ZERO),  # 2: point 1's bone; its parent 1 at the origin, then 0
        (-1, ZERO),  # 3: a root at the origin
        (3, ZERO),  # 4: point 0's bone; its parent 3 is a root
        (2, ZERO),  # 5
        (5, ZERO),  # 6: point 2's bone; 5 and 2 both at the origin: two levels only
    ]
    body = _Body(bones, [(0, 4, ZERO), (1, 2, ZERO), (2, 6, ZERO)])
    assert script.held_point_parents(body, body.data) == {
        "0": [{"bone": 3, "bone_pivot": [0.0, 0.0, 0.0]}],
        "1": [
            {"bone": 1, "bone_pivot": [0.0, 0.0, 0.0]},
            {"bone": 0, "bone_pivot": [0.0, 0.0, 1.0]},
        ],
        "2": [
            {"bone": 5, "bone_pivot": [0.0, 0.0, 0.0]},
            {"bone": 2, "bone_pivot": [0.0, 0.0, 0.0]},
        ],
    }


def test_held_point_parents_stop_at_a_parent_away_from_the_origin_constructed() -> None:
    body = _Body([(-1, ZERO), (0, (0.5, 0.0, 0.0)), (1, ZERO)], [(1, 2, ZERO)])
    assert script.held_point_parents(body, body.data) == {
        "1": [{"bone": 1, "bone_pivot": [0.5, 0.0, 0.0]}]
    }


def test_other_attachment_points_skip_the_recorded_seven_in_file_order_constructed() -> None:
    body = _Body(
        [(-1, ZERO), (0, (0.25, 0.5, 1.5))],
        [(27, 1, (0.25, 0.5, 1.5)), (1, 0, ZERO), (12, 1, ZERO), (26, 0, ZERO)],
    )
    assert script.other_attachment_points(body, body.data) == [
        {"id": 27, "bone": 1, "position": [0.25, 0.5, 1.5], "bone_pivot": [0.25, 0.5, 1.5]},
        {"id": 26, "bone": 0, "position": [0.0, 0.0, 0.0], "bone_pivot": [0.0, 0.0, 0.0]},
    ]


def test_a_bone_past_the_array_is_refused_constructed() -> None:
    body = _Body([(-1, ZERO)], [(1, 0, ZERO), (26, 3, ZERO)])
    with pytest.raises(script.facts.FactsError, match="bone 3 of 1"):
        script.other_attachment_points(body, body.data)
    parentless = _Body([(5, ZERO)], [(1, 0, ZERO)])
    with pytest.raises(script.facts.FactsError, match="bone 5 of 1"):
        script.held_point_parents(parentless, parentless.data)


def test_sheath_point_parents_walk_points_26_to_33_only_constructed() -> None:
    bones = [(-1, (0.0, 0.0, 1.5)), (0, ZERO), (-1, ZERO)]
    body = _Body(bones, [(26, 1, ZERO), (29, 0, ZERO), (34, 1, ZERO), (1, 1, ZERO)])
    assert script.sheath_point_parents(body, body.data) == {
        "26": [{"bone": 0, "bone_pivot": [0.0, 0.0, 1.5]}],
        "29": [],
    }


def test_bone_facts_name_the_points_bones_and_their_chains_constructed() -> None:
    bones = [
        (-1, ZERO),  # 0: the hand chain's root, at the origin
        (0, ZERO),  # 1: point 1's parent
        (1, ZERO),  # 2: point 1's bone
        (-1, (0.0, 0.0, 2.0)),  # 3: point 5's parent, not walked
        (3, (0.0, 0.5, 1.5)),  # 4: point 5's bone
        (3, (-0.3, 0.0, 1.5)),  # 5: point 26's bone
        (-1, ZERO),  # 6: no point's
    ]
    body = _Body(
        bones,
        [(1, 2, ZERO), (5, 4, (0.0, 0.5, 1.5)), (26, 5, (-0.3, 0.0, 1.5)), (40, 6, ZERO)],
        heads={1: (66, 0x200), 3: (23, 0)},
        tracks={1: ((-1, 3, 1), (-1, 3, 1)), 5: ((-1, 0, 0), (2, 1, 24))},
    )
    none = {"global_sequence": -1, "sequences": 0, "first_keys": None}
    plain = {"key_bone_id": -1, "flags": 0, "translation": none, "rotation": none}
    assert script.bone_facts(body, body.data) == {
        "0": {**plain, "parent": -1},
        "1": {
            "key_bone_id": 66,
            "flags": 0x200,
            "parent": 0,
            "translation": {"global_sequence": -1, "sequences": 3, "first_keys": 1},
            "rotation": {"global_sequence": -1, "sequences": 3, "first_keys": 1},
        },
        "2": {**plain, "parent": 1},
        "3": {**plain, "key_bone_id": 23, "parent": -1},
        "4": {**plain, "parent": 3},
        "5": {
            **plain,
            "parent": 3,
            "rotation": {"global_sequence": 2, "sequences": 1, "first_keys": 24},
        },
    }


def test_a_bone_array_past_the_chunk_is_refused_constructed() -> None:
    body = _Body([(-1, ZERO)], [(1, 0, ZERO)])
    hostile = bytearray(body.data)
    struct.pack_into("<I", hostile, 0x2C, 0xFFFF_FFFF)  # a count no chunk holds
    for read in (script.other_attachment_points, script.held_point_parents, script.bone_facts):
        with pytest.raises(script.facts.FactsError, match="ends past the MD21 chunk"):
            read(body, bytes(hostile))
    short = _Body([(-1, ZERO)], [(1, 0, ZERO)])
    short.chunks = (m2.M2Chunk(tag="MD21", offset=0, size=0x20, read=True),)
    with pytest.raises(script.facts.FactsError, match="ends before its bone array"):
        script.bone_facts(short, short.data)


def test_a_track_whose_sequences_lie_past_the_chunk_is_refused_constructed() -> None:
    body = _Body([(-1, ZERO)], [(1, 0, ZERO)], tracks={0: ((-1, 1, 1), (-1, 0, 0))})
    hostile = bytearray(body.data)
    struct.pack_into("<I", hostile, 0x100 + 16 + 8, len(hostile) - 4)  # 4 bytes left, 8 needed
    with pytest.raises(script.facts.FactsError, match="end past MD21"):
        script.bone_facts(body, bytes(hostile))


# M15-26, the rotation probe: tracks with their keys (constructed).
IDENTITY = (32767, 32767, 32767, -1)  # M2CompQuat of (0, 0, 0, 1)
QUARTER_Z = (32767, 32767, -9598, -9598)  # about (0, 0, 0.7071, 0.7071): 90 degrees about z
Tracks = dict[tuple[int, int], tuple[int, int, list[tuple[list[int], bytes]]]]


def _keyed(
    body: _Body,
    tracks: Tracks,
    sequences: tuple[tuple[int, int], ...] = (),
    loops: tuple[int, ...] = (),
) -> _Body:
    """`body` with its sequences (animation id, flags), its global sequence
    durations, and `tracks`, by (bone, 16 or 36): (interpolation, global
    sequence, per-sequence arrays of (timestamps, the values' bytes))."""
    blob = bytearray(body.data)
    struct.pack_into("<II", blob, 0x1C, len(sequences), len(blob))
    for animation, flags in sequences:
        record = bytearray(64)
        struct.pack_into("<H", record, 0, animation)
        struct.pack_into("<I", record, 12, flags)
        blob += record
    struct.pack_into("<II", blob, 0x14, len(loops), len(blob))
    blob += struct.pack(f"<{len(loops)}I", *loops)
    for (bone, track_at), (interpolation, global_sequence, arrays) in tracks.items():
        heads = []
        for times, values in arrays:
            times_at = len(blob)
            blob += struct.pack(f"<{len(times)}I", *times)
            values_at = len(blob)
            blob += values
            heads.append((len(times), times_at, values_at))
        times_heads = len(blob)
        blob += b"".join(struct.pack("<II", n, at) for n, at, _ in heads)
        values_heads = len(blob)
        blob += b"".join(struct.pack("<II", n, at) for n, _, at in heads)
        track = 0x100 + 88 * bone + track_at
        struct.pack_into("<Hh", blob, track, interpolation, global_sequence)
        struct.pack_into(
            "<IIII", blob, track + 4, len(arrays), times_heads, len(arrays), values_heads
        )
    body.data = bytes(blob)
    body.chunks = (m2.M2Chunk(tag="MD21", offset=0, size=len(blob), read=True),)
    return body


def _quats(*raw: tuple[int, int, int, int]) -> bytes:
    return b"".join(struct.pack("<4h", *q) for q in raw)


def test_sheath_rotation_keys_read_a_global_sequences_first_key_constructed() -> None:
    body = _keyed(
        _Body([(-1, ZERO), (0, (-0.3, 0.0, 1.5))], [(26, 1, (-0.3, 0.0, 1.5)), (29, 0, ZERO)]),
        {(1, 36): (1, 1, [([0, 50, 100], _quats(IDENTITY, QUARTER_Z, IDENTITY))])},
        loops=(100, 200),
    )
    rows = script.sheath_rotation_keys(body, body.data)
    assert rows["29"] == {
        "bone": 0,
        "rotation": {
            "interpolation": 0,
            "global_sequence": -1,
            "global_duration": None,
            "arrays": 0,
            "arrays_with_keys": 0,
            "first_array": None,
            "animation_0_array": None,
        },
    }
    rotation = rows["26"]["rotation"]
    first = rotation.pop("first_array")
    assert rotation == {
        "interpolation": 1,
        "global_sequence": 1,
        "global_duration": 200,
        "arrays": 1,
        "arrays_with_keys": 1,
        "animation_0_array": None,
    }
    assert first["first_value"] == [0.0, 0.0, 0.0, 1.0] and first["first_norm"] == 1.0
    assert first["max_degrees_from_first"] == pytest.approx(90.0, abs=0.01)
    assert (first["index"], first["sequence_id"], first["in_m2"]) == (0, None, True)
    assert (first["keys"], first["first_time"]) == (3, 0)


def test_a_per_sequence_track_names_its_animation_and_reads_animation_0_constructed() -> None:
    """Array 0 is animation 5, array 1 is empty, array 2 is animation 0; a
    sequence without flag 0x20 keeps its keys in an `.anim` file: counted,
    not read."""
    vec = struct.pack("<3f", 0.25, -0.75, 1.0)
    body = _keyed(
        _Body([(-1, ZERO), (0, ZERO)], [(1, 1, ZERO)]),
        {
            (0, 16): (1, -1, [([7], struct.pack("<3f", 1, 2, 3)), ([], b""), ([0, 9], vec * 2)]),
            (1, 16): (1, -1, [([], b""), ([0], vec)]),
            (1, 36): (1, -1, [([0], _quats(QUARTER_Z)), ([0], _quats(IDENTITY))]),
        },
        sequences=((5, 0x20), (0, 0x00), (0, 0x20)),
    )
    hand = script.hand_bone_keys(body, body.data)["1"]
    assert [row["bone"] for row in hand] == [1, 0]
    own = hand[0]["translation"]
    assert own["first_array"]["sequence_id"] == 0 and not own["first_array"]["in_m2"]
    assert own["first_array"]["keys"] == 1 and own["first_array"]["first_value"] is None
    assert own["animation_0_array"] == own["first_array"]
    rotation = hand[0]["rotation"]["first_array"]
    assert rotation["sequence_id"] == 5
    assert rotation["first_value"] == pytest.approx([0.0, 0.0, 0.7071, 0.7071], abs=1e-4)
    parent = hand[1]["translation"]
    assert parent["arrays"] == 3 and parent["arrays_with_keys"] == 2
    assert parent["first_array"]["sequence_id"] == 5
    assert parent["first_array"]["first_value"] == [1.0, 2.0, 3.0]
    assert parent["first_array"]["first_time"] == 7
    assert parent["animation_0_array"]["index"] == 2
    assert parent["animation_0_array"]["first_value"] == [0.25, -0.75, 1.0]
    assert parent["animation_0_array"]["first_norm"] is None


def test_a_hermite_rotation_steps_over_its_tangents_constructed() -> None:
    """A key of interpolation 3 is the value and two tangents: the second
    key is three quaternions on, and the tangents are not keys."""
    junk = (0, 0, 0, 0)
    body = _keyed(
        _Body([(-1, ZERO)], [(26, 0, ZERO)]),
        {(0, 36): (3, 0, [([0, 10], _quats(IDENTITY, junk, junk, QUARTER_Z, junk, junk))])},
        loops=(10,),
    )
    first = script.sheath_rotation_keys(body, body.data)["26"]["rotation"]["first_array"]
    assert first["max_degrees_from_first"] == pytest.approx(90.0, abs=0.01)


def _sheath(tracks: Tracks, **kw: Any) -> _Body:
    return _keyed(_Body([(-1, ZERO)], [(26, 0, ZERO)]), tracks, **kw)


def test_hostile_tracks_are_refused_constructed() -> None:
    one = [([0], _quats(IDENTITY))]
    unknown = _sheath({(0, 36): (1, 3, one)}, loops=(1, 2))
    with pytest.raises(script.facts.FactsError, match="global sequence 3 of 2"):
        script.sheath_rotation_keys(unknown, unknown.data)
    unsequenced = _sheath({(0, 36): (1, -1, one * 2)}, sequences=((0, 0x20),))
    with pytest.raises(script.facts.FactsError, match="2 arrays for 1 sequences"):
        script.sheath_rotation_keys(unsequenced, unsequenced.data)
    past = _sheath({(0, 36): (1, 0, one)}, loops=(1,))
    hostile = bytearray(past.data)
    values_heads = struct.unpack_from("<I", hostile, 0x100 + 36 + 16)[0]
    struct.pack_into("<I", hostile, values_heads, 0x7FFF_FFFF)  # a key count unlike its times'
    with pytest.raises(script.facts.FactsError, match="1 times and 2147483647 values"):
        script.sheath_rotation_keys(past, bytes(hostile))
    times_heads = struct.unpack_from("<I", hostile, 0x100 + 36 + 8)[0]
    struct.pack_into("<I", hostile, times_heads, 0x7FFF_FFFF)  # both counts past the chunk
    with pytest.raises(script.facts.FactsError, match=r"timestamps .* end past MD21"):
        script.sheath_rotation_keys(past, bytes(hostile))
    struct.pack_into("<I", hostile, 0x100 + 36 + 12, 2)  # two value arrays for one
    with pytest.raises(script.facts.FactsError, match="1 timestamp and 2 value arrays"):
        script.sheath_rotation_keys(past, bytes(hostile))
    struct.pack_into("<II", hostile, 0x100 + 36 + 12, 1, len(hostile) - 4)
    with pytest.raises(script.facts.FactsError, match=r"values .* end past MD21"):
        script.sheath_rotation_keys(past, bytes(hostile))
