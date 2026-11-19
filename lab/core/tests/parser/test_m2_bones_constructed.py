"""`m2.bone_chain` on constructed files (M15-27; labelled `constructed`,
ADR-0033): a bone, its parents and the first key of each one's rotation,
with known values, and hostile files: a bone past the bones, parents that
loop, a chain deeper than the cap, a global sequence that is not there,
counts of 2**32 - 1 in each array the reader touches, and offsets past the
blob. No real bone is read here: the two fixture body models' sheath bones
are graded through the derived fixture (`test_charmodel_sheathed.py`)."""

from __future__ import annotations

import math
import struct
import time

import pytest
from _m2_build import Bone, chunked, md20, quat, quat_i16

from wowlab_core import m2

pytestmark = pytest.mark.parser

U32_MAX = 2**32 - 1
TURN_Z = (0.0, 0.0, math.sin(math.pi / 4), math.cos(math.pi / 4))  # 90 degrees about z


def _blob(bones: list[Bone], loops: tuple[int, ...] = (1000,)) -> bytearray:
    return md20(bone_list=bones, global_sequences=loops)


def _three() -> list[Bone]:
    """Bone 0 a root at (0, 0, 1); bone 1 under it at (0, 0.5, 1.5), its
    rotation on global sequence 0, linear, two keys (the first a quarter
    turn about z); bone 2 a root with no track."""
    return [
        (-1, (0.0, 0.0, 1.0), None),
        (0, (0.0, 0.5, 1.5), (0, 1, [quat(*TURN_Z), quat(0.0, 0.0, 0.0, 1.0)])),
        (-1, (0.0, 0.0, 0.0), None),
    ]


def _read(blob: bytearray, bone: int) -> tuple[m2.M2Bone, ...]:
    data = chunked(bytes(blob))
    return m2.bone_chain(data, m2.parse_m2(data), bone)


def _bone_at(blob: bytearray, bone: int) -> int:
    _count, offset = struct.unpack_from("<II", blob, 0x2C)
    return int(offset) + 88 * bone


def test_a_bone_and_its_parent_are_read_with_the_first_key_constructed() -> None:
    chain = _read(_blob(_three()), 1)
    assert [b.index for b in chain] == [1, 0]
    child, root = chain
    assert child.parent == 0 and root.parent == -1
    assert child.key_bone_id == -1 and child.flags == 0
    assert child.pivot == pytest.approx((0.0, 0.5, 1.5)) and root.pivot == pytest.approx((0, 0, 1))
    assert child.rotation == pytest.approx(TURN_Z, abs=2e-5)
    assert child.rotation_global_sequence == 0 and child.rotation_interpolation == 1
    assert root.rotation is None and root.rotation_why == (
        "its rotation is not on a global sequence"
    )


def test_the_decoding_rule_constructed() -> None:
    """wowdev.wiki's rule, `(s < 0 ? s + 32768 : s - 32767) / 32767`."""
    assert m2.decode_quaternion((-1, 32767, 0, -32768)) == (1.0, 0.0, -1.0, 0.0)
    for v in (-1.0, -0.5, 0.0, 0.304422, 0.5, 1.0):
        assert m2.decode_quaternion((quat_i16(v),) * 4)[0] == pytest.approx(v, abs=2e-5)


def test_a_track_on_a_global_sequence_with_no_keys_gives_no_rotation_constructed() -> None:
    bones = _three()
    bones[1] = (0, (0.0, 0.5, 1.5), (0, 1, []))
    child = _read(_blob(bones), 1)[0]
    assert child.rotation is None and child.rotation_why == "its rotation track holds no keys"


def test_a_spline_key_reads_its_value_first_constructed() -> None:
    """Interpolation 2 (Bezier): a key is the value then two tangents; the
    reader takes the first value and checks the keys as three wide."""
    bones = _three()
    bones[1] = (0, (0.0, 0.5, 1.5), (0, 2, [quat(*TURN_Z), (0, 0, 0, 0), (0, 0, 0, 0)]))
    blob = _blob(bones)
    track = _bone_at(blob, 1) + 36
    _n, outer = struct.unpack_from("<II", blob, track + 4)
    struct.pack_into("<I", blob, outer, 1)  # one key's time
    _n, outer = struct.unpack_from("<II", blob, track + 12)
    struct.pack_into("<I", blob, outer, 1)  # one key, three quaternions wide
    assert _read(blob, 1)[0].rotation == pytest.approx(TURN_Z, abs=2e-5)


def test_hostile_bone_past_the_bones_is_refused_constructed() -> None:
    with pytest.raises(m2.M2MalformedError, match="bone 3 is named; the model has 3 bones"):
        _read(_blob(_three()), 3)
    bones = _three()
    bones[0] = (7, (0.0, 0.0, 1.0), None)
    with pytest.raises(m2.M2MalformedError, match="bone 7 is named"):
        _read(_blob(bones), 1)


def test_hostile_parents_that_loop_are_refused_constructed() -> None:
    bones = _three()
    bones[0] = (1, (0.0, 0.0, 1.0), None)
    with pytest.raises(m2.M2MalformedError, match="loop back"):
        _read(_blob(bones), 1)
    bones[0] = (0, (0.0, 0.0, 1.0), None)  # its own parent
    with pytest.raises(m2.M2MalformedError, match="loop back"):
        _read(_blob(bones), 0)


def test_boundary_a_chain_of_the_cap_is_read_and_one_deeper_is_refused_constructed() -> None:
    depth = m2.MAX_BONE_DEPTH
    bones: list[Bone] = [(i - 1, (0.0, 0.0, float(i)), None) for i in range(depth + 1)]
    assert len(_read(_blob(bones), depth - 1)) == depth
    started = time.perf_counter()
    with pytest.raises(m2.M2MalformedError, match=f"deeper than {depth}"):
        _read(_blob(bones), depth)
    assert time.perf_counter() - started < 1.0


def test_hostile_global_sequence_past_the_model_is_refused_constructed() -> None:
    bones = _three()
    bones[1] = (0, (0.0, 0.5, 1.5), (3, 1, [quat(*TURN_Z)]))
    with pytest.raises(m2.M2MalformedError, match="global sequence 3; the model has 1"):
        _read(_blob(bones), 1)


@pytest.mark.parametrize(
    ("where", "what"),
    [
        (4, "a rotation's timestamps"),
        (12, "a rotation's values"),
    ],
)
def test_hostile_outer_count_of_two_to_the_32_is_refused_constructed(where: int, what: str) -> None:
    blob = _blob(_three())
    struct.pack_into("<I", blob, _bone_at(blob, 1) + 36 + where, U32_MAX)
    with pytest.raises(m2.M2MalformedError, match=what):
        _read(blob, 1)


@pytest.mark.parametrize(
    ("where", "what"),
    [
        (4, "a rotation's first timestamps"),
        (12, "a rotation's first keys"),
    ],
)
def test_hostile_inner_count_of_two_to_the_32_is_refused_constructed(where: int, what: str) -> None:
    blob = _blob(_three())
    _n, outer = struct.unpack_from("<II", blob, _bone_at(blob, 1) + 36 + where)
    struct.pack_into("<I", blob, outer, U32_MAX)
    with pytest.raises(m2.M2MalformedError, match=what):
        _read(blob, 1)


@pytest.mark.parametrize("where", [4, 12])
def test_hostile_inner_offset_past_the_blob_is_refused_constructed(where: int) -> None:
    blob = _blob(_three())
    _n, outer = struct.unpack_from("<II", blob, _bone_at(blob, 1) + 36 + where)
    struct.pack_into("<I", blob, outer + 4, len(blob) - 2)
    with pytest.raises(m2.M2MalformedError, match="runs past the end"):
        _read(blob, 1)


def test_hostile_global_sequence_count_past_the_blob_is_refused_constructed() -> None:
    blob = _blob(_three())
    struct.pack_into("<I", blob, 0x14, U32_MAX)
    with pytest.raises(m2.M2MalformedError, match="global sequences"):
        _read(blob, 1)


def test_hostile_timestamps_and_keys_that_disagree_are_refused_constructed() -> None:
    blob = _blob(_three())
    _n, outer = struct.unpack_from("<II", blob, _bone_at(blob, 1) + 36 + 4)
    struct.pack_into("<I", blob, outer, 1)  # one time for two keys
    with pytest.raises(m2.M2MalformedError, match="1 timestamps and 2 keys"):
        _read(blob, 1)


def test_bytes_that_are_not_the_models_are_refused_constructed() -> None:
    data = chunked(bytes(_blob(_three())))
    model = m2.parse_m2(data)
    with pytest.raises(m2.M2MalformedError, match="not the model's"):
        m2.bone_chain(data[:100], model, 1)
