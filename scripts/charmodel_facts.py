#!/usr/bin/env python3
"""Write a derived fixture of the `.glb` files `char model` draws from an
install (M15-11, ADR-0033).

    uv run python scripts/charmodel_facts.py --flavor FOLDER --tables DIR \
        --char LABEL=WOWLAB_LUA [--char ...] --captured YYYY-MM-DD --out PATH \
        [--root INSTALL]

Builds `renderplan`'s plan for each character's lab-addon record from the
recorded tables (as `scripts/renderplan_facts.py` does, whose helpers this
uses), with each file's state from `casc.open_source` (read-only), draws it
with `charmodel.build`, and writes, as JSON: the product, version and
build, whether the game client was running, and per character the `.glb`'s
facts (its size; its node, mesh, primitive, material and image counts; its
vertex and triangle counts; the MD5 of its JSON chunk and of its binary
chunk), each node's name, each model's ids, kind, attachment, and its
section, vertex and triangle counts, each PNG's name, source file, size and
MD5, the body texture's painted count, the files left out, and the
build's time, and for each body model the attachment points 0, 1, 2, 5,
6, 11 and 12 with their bone, stored position and that bone's pivot (read
here from the bone array), and (M15-24, the held-items probe) every other
attachment point the body model holds with the same three facts, and for
the hand points 0, 1 and 2 and the points 26 to 33 the parent of the bone
each sits on with its pivot (and the next bone up when that pivot is the
origin), and for each of those bones its key bone id, flags, parent and
its translation and rotation tracks counted (global sequence, number of
sequences, keys in the first), and (M15-26, the rotation probe) for the
bones of points 26 to 33 their rotation track's first key, and for the
bones of the hand points 1 and 2 and the bones above them their
translation and rotation tracks' first keys (`_keys`: numbers decoded from
the file, never its bytes). No geometry, no pixel, no byte of any file,
no install path.

Refusals (exit 1), as `renderplan_facts.py`'s: `--out` that is a link, one
inside a game install, one already there; a table missing from `--tables`;
any `casc`, `install`, `labaddon`, `renderplan`, `charmodel` or `blp`
error, in one line on stderr. Written only after everything is read.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import struct
import sys
from collections.abc import Buffer, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

from wowlab_core import blp, casc, charmodel, labaddon, m2, process, renderplan
from wowlab_core.errors import WowlabError
from wowlab_core.install import Install, InstallError, discover

SCHEMA = 1


def _sibling() -> ModuleType:
    path = Path(__file__).resolve().parent / "renderplan_facts.py"
    spec = importlib.util.spec_from_file_location("renderplan_facts", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(spec.name, module)
    spec.loader.exec_module(module)
    return module


facts = _sibling()


def character_facts(built: charmodel.CharModel, plan: renderplan.RenderPlan) -> dict[str, Any]:
    """What one character's `.glb` holds, counted and hashed."""
    return {
        "body_file_data_id": plan.body.file_data_id,
        "glb": built.facts.model_dump(),
        "glb_md5": hashlib.md5(built.glb, usedforsecurity=False).hexdigest(),
        "models": [
            {
                "what": m.what,
                "slot": m.slot,
                "item_id": m.item_id,
                "file_data_id": m.file_data_id,
                "skin_file_data_id": m.skin_file_data_id,
                "kind": m.kind,
                "attachment_id": m.attachment_id,
                "placed": m.placed,
                "sections": m.sections,
                "drawn": m.drawn,
                "extra_batches": m.extra_batches,
                "vertices": m.vertices,
                "triangles": m.triangles,
            }
            for m in built.models
        ],
        "pngs": [
            {
                "name": p.name,
                "texture_type": p.texture_type,
                "file_data_id": p.file_data_id,
                "size": [p.width, p.height],
                "md5": hashlib.md5(p.png, usedforsecurity=False).hexdigest(),
                "not_drawn": list(p.not_drawn),
            }
            for p in built.pngs
        ],
        "body_texture_painted": len(built.body_texture.painted),
        "left_out": [f.model_dump() for f in built.left_out],
        "seconds": {k: round(v, 3) for k, v in built.seconds.items()},
    }


# The attachment points recorded per body model: held items (0 shield, 1 main
# hand, 2 off hand), shoulders (5, 6), head (11) and back (12), the slots the
# render plan places (SLOT_ATTACHMENTS, SHIELD_ATTACHMENT).
POINTS = (0, 1, 2, 5, 6, 11, 12)
_BONE = struct.Struct("<II")  # the MD20 header's bone array: count, offset
_BONES_AT = 0x2C
_BONE_SIZE = 88
_PIVOT_AT = 76  # M2CompBone.pivot, three floats (wowdev.wiki, M2 since 264)
_PIVOT = struct.Struct("<3f")


def attachment_points(body: m2.M2Model, data: Buffer) -> dict[str, Any]:
    """Each point of `POINTS` the body model has: its bone, its stored
    position and that bone's pivot (game axes). The pivot is read here from
    the bone array, which the library does not read (bones are counted
    only, §17.3), as evidence for the §17.6 note of M15-11."""
    md21 = next(c for c in body.chunks if c.tag == "MD21")
    blob = memoryview(data).cast("B")[md21.offset : md21.offset + md21.size]
    count, offset = _BONE.unpack_from(blob, _BONES_AT)
    out: dict[str, Any] = {}
    for point_id in POINTS:
        point = body.attachment(point_id)
        if point is None:
            continue
        if not 0 <= point.bone < count:
            raise facts.FactsError(f"attachment {point_id} names bone {point.bone} of {count}")
        pivot = _PIVOT.unpack_from(blob, offset + _BONE_SIZE * point.bone + _PIVOT_AT)
        out[str(point_id)] = {
            "bone": point.bone,
            "position": list(point.position),
            "bone_pivot": list(pivot),
        }
    return out


# M15-24, the held-items probe. The parent of a bone (M2CompBone.parent_bone,
# an i16 at 8; -1 for a root bone, wowdev.wiki, M2 since 264), and the hand
# points whose bones' parents are recorded.
_HEAD = struct.Struct("<iIh")  # M2CompBone: key_bone_id i32 at 0, flags u32 at 4, parent i16 at 8
_TRANSLATION_AT = 16  # M2Track<C3Vector>, 20 bytes
_ROTATION_AT = 36  # M2Track<C4Quaternion>, 20 bytes
_GLOBAL_AT = 2  # in an M2Track: its global sequence, i16 (-1 when none)
_GLOBAL = struct.Struct("<h")
_TIMESTAMPS_AT = 4  # in an M2Track: the per-sequence timestamp arrays, count and offset
HELD = (0, 1, 2)
SHEATH = tuple(range(26, 34))  # the community list's 26 to 33, read for their bones' parents
PARENT_LEVELS = 2  # the parent, and one more level when the parent rests at the origin


def _bones(body: m2.M2Model, data: Buffer) -> tuple[memoryview, int, int]:
    """The MD21 chunk's bytes and its bone array's count and offset, the
    array checked to lie inside the chunk."""
    md21 = next(c for c in body.chunks if c.tag == "MD21")
    blob = memoryview(data).cast("B")[md21.offset : md21.offset + md21.size]
    if len(blob) < _BONES_AT + _BONE.size:
        raise facts.FactsError("the MD21 chunk ends before its bone array")
    count, offset = _BONE.unpack_from(blob, _BONES_AT)
    if offset + _BONE_SIZE * count > len(blob):
        raise facts.FactsError(f"the bone array ({count} at {offset}) ends past the MD21 chunk")
    return blob, count, offset


def _bone(blob: memoryview, count: int, offset: int, bone: int) -> tuple[int, list[float]]:
    """Bone `bone`'s parent index and pivot."""
    if not 0 <= bone < count:
        raise facts.FactsError(f"bone {bone} of {count}")
    at = offset + _BONE_SIZE * bone
    _key, _flags, parent = _HEAD.unpack_from(blob, at)
    return parent, list(_PIVOT.unpack_from(blob, at + _PIVOT_AT))


def _track(blob: memoryview, track: int) -> dict[str, Any]:
    """The M2Track at `track`, counted: its global sequence (-1 when none),
    its number of per-sequence timestamp arrays, and the number of keys in
    the first (sequence 0's, or the global sequence's when it has one;
    `None` when there is no array). Only counts: the keys themselves may
    live in an `.anim` file, and are not read."""
    (global_sequence,) = _GLOBAL.unpack_from(blob, track + _GLOBAL_AT)
    sequences, at = _BONE.unpack_from(blob, track + _TIMESTAMPS_AT)
    first: int | None = None
    if sequences:
        if at + _BONE.size > len(blob):
            raise facts.FactsError(f"a bone track's sequences ({sequences} at {at}) end past MD21")
        first = int(_BONE.unpack_from(blob, at)[0])
    return {"global_sequence": global_sequence, "sequences": sequences, "first_keys": first}


def _chain(blob: memoryview, count: int, offset: int, bone: int) -> list[dict[str, Any]]:
    """The bones above `bone`: the parent with its pivot, and the parent's
    parent when the parent's pivot is (0, 0, 0) (at most `PARENT_LEVELS`;
    none above a root bone)."""
    chain: list[dict[str, Any]] = []
    parent, _ = _bone(blob, count, offset, bone)
    while parent >= 0 and len(chain) < PARENT_LEVELS:
        above, pivot = _bone(blob, count, offset, parent)
        chain.append({"bone": parent, "bone_pivot": pivot})
        if any(pivot):
            break
        parent = above
    return chain


def other_attachment_points(body: m2.M2Model, data: Buffer) -> list[dict[str, Any]]:
    """Every attachment the body model holds whose id is not in `POINTS`, in
    file order: its id, bone, stored position and that bone's pivot (game
    axes). The probe of M15-24: which sheath points (26 to 33 by the
    community list) the file holds, and where, read rather than assumed."""
    blob, count, offset = _bones(body, data)
    return [
        {
            "id": a.id,
            "bone": a.bone,
            "position": list(a.position),
            "bone_pivot": _bone(blob, count, offset, a.bone)[1],
        }
        for a in body.attachments
        if a.id not in POINTS
    ]


def _point_parents(
    body: m2.M2Model, data: Buffer, points: Sequence[int]
) -> dict[str, list[dict[str, Any]]]:
    blob, count, offset = _bones(body, data)
    out: dict[str, list[dict[str, Any]]] = {}
    for point_id in points:
        point = body.attachment(point_id)
        if point is not None:
            out[str(point_id)] = _chain(blob, count, offset, point.bone)
    return out


def held_point_parents(body: m2.M2Model, data: Buffer) -> dict[str, list[dict[str, Any]]]:
    """For each hand point of `HELD` the body model has, the bones above the
    one it sits on (`_chain`). The probe of M15-24."""
    return _point_parents(body, data, HELD)


def sheath_point_parents(body: m2.M2Model, data: Buffer) -> dict[str, list[dict[str, Any]]]:
    """The same for each point of `SHEATH` the body model has (M15-24)."""
    return _point_parents(body, data, SHEATH)


def bone_facts(body: m2.M2Model, data: Buffer) -> dict[str, dict[str, Any]]:
    """For each bone the rows above name (the bones of `POINTS`, `HELD` and
    `SHEATH`, and the bones of their chains), by index in ascending order:
    its `key_bone_id` (-1 when none), its `flags`, its parent, and its
    translation and rotation tracks counted (`_track`). Counts only, no key
    value (M15-24)."""
    blob, count, offset = _bones(body, data)
    named: set[int] = set()
    for point_id in (*POINTS, *SHEATH):
        point = body.attachment(point_id)
        if point is None:
            continue
        named.add(point.bone)
        if point_id in HELD or point_id in SHEATH:
            named.update(link["bone"] for link in _chain(blob, count, offset, point.bone))
    out: dict[str, dict[str, Any]] = {}
    for bone in sorted(named):
        _bone(blob, count, offset, bone)  # the range check
        at = offset + _BONE_SIZE * bone
        key, flags, parent = _HEAD.unpack_from(blob, at)
        out[str(bone)] = {
            "key_bone_id": key,
            "flags": flags,
            "parent": parent,
            "translation": _track(blob, at + _TRANSLATION_AT),
            "rotation": _track(blob, at + _ROTATION_AT),
        }
    return out


# M15-26, the rotation probe: the first key of a track, read. An M2Track is
# interpolation u16 at 0, global sequence i16 at 2, the timestamps (an array
# of per-sequence u32 arrays) at 4 and the values (the same shape) at 12; a
# rotation value is an M2CompQuat, four i16 (x, y, z, w), a translation three
# floats; a Bezier or Hermite key (interpolation 2 or 3) holds the value and
# two tangents (wowdev.wiki, M2, since WotLK). A sequence's keys are in the
# M2 itself only when its M2Sequence (64 bytes, the array at 0x1C, flags u32
# at 12) has flag 0x20; else they are in an `.anim` file, which is not read.
# A global sequence's (durations: u32, the array at 0x14) keys are taken to be
# in the M2 (**[verify]**: the decoded quaternions' norms are recorded).
_SEQUENCES_AT = 0x1C
_SEQUENCE_SIZE = 64
_SEQUENCE_FLAGS_AT = 12
_IN_M2 = 0x20
_GLOBAL_LOOPS_AT = 0x14
_U16 = struct.Struct("<H")
_U32 = struct.Struct("<I")
_VALUES_AT = 12
_QUAT = struct.Struct("<4h")
_SPLINES = (2, 3)  # Bezier, Hermite: value, in tangent, out tangent
HAND = (1, 2)  # the hand points whose bones' first keys are read


def _array_in(blob: memoryview, count: int, at: int, size: int, what: str) -> None:
    if at + count * size > len(blob):
        raise facts.FactsError(f"{what} ({count} at {at}) end past MD21")


def _quat(raw: tuple[int, ...]) -> list[float]:
    """An M2CompQuat as four floats (wowdev.wiki: `(s < 0 ? s + 32768 :
    s - 32767) / 32767`)."""
    return [(s + 32768 if s < 0 else s - 32767) / 32767 for s in raw]


def _degrees(a: Sequence[float], b: Sequence[float]) -> float:
    """The angle between two rotations, in degrees (unit quaternions)."""
    dot = min(1.0, abs(sum(x * y for x, y in zip(a, b, strict=True))))
    return math.degrees(2 * math.acos(dot))


def _sequences(blob: memoryview) -> list[tuple[int, bool]]:
    """Each M2Sequence's animation id and whether its keys are in the M2
    (flag 0x20), in file order; the array checked to lie inside MD21."""
    count, at = _BONE.unpack_from(blob, _SEQUENCES_AT)
    _array_in(blob, count, at, _SEQUENCE_SIZE, "the sequences")
    return [
        (
            _U16.unpack_from(blob, at + _SEQUENCE_SIZE * i)[0],
            bool(_U32.unpack_from(blob, at + _SEQUENCE_SIZE * i + _SEQUENCE_FLAGS_AT)[0] & _IN_M2),
        )
        for i in range(count)
    ]


def _key_array(
    blob: memoryview,
    times_at: int,
    values_at: int,
    index: int,
    sequence: tuple[int, bool] | None,
    stride: int,
    rotation: bool,
) -> dict[str, Any]:
    """Array `index` of a track: its sequence's animation id (`None` on a
    global sequence), whether its keys are in the M2, its key count, and,
    when they are, its first timestamp and first value, and for a rotation
    that value's norm and the largest angle, in degrees, of any of its keys
    from the first."""
    sequence_id, in_m2 = sequence if sequence is not None else (None, True)
    keys, key_times = _BONE.unpack_from(blob, times_at + _BONE.size * index)
    key_values, value_data = _BONE.unpack_from(blob, values_at + _BONE.size * index)
    if key_values != keys:
        raise facts.FactsError(
            f"a bone track's array {index} has {keys} times and {key_values} values"
        )
    out: dict[str, Any] = {
        "index": index,
        "sequence_id": sequence_id,
        "in_m2": in_m2,
        "keys": keys,
        "first_time": None,
        "first_value": None,
        "first_norm": None,
        "max_degrees_from_first": None,
    }
    if not in_m2 or not keys:
        return out
    _array_in(blob, keys, key_times, _U32.size, "a bone track's timestamps")
    _array_in(blob, keys, value_data, stride, "a bone track's keys")
    out["first_time"] = _U32.unpack_from(blob, key_times)[0]
    if not rotation:
        out["first_value"] = list(_PIVOT.unpack_from(blob, value_data))
        return out
    quats = [_quat(_QUAT.unpack_from(blob, value_data + stride * k)) for k in range(keys)]
    out["first_value"] = quats[0]
    out["first_norm"] = math.sqrt(sum(x * x for x in quats[0]))
    out["max_degrees_from_first"] = max(_degrees(quats[0], q) for q in quats)
    return out


def _keys(blob: memoryview, track: int, rotation: bool) -> dict[str, Any]:
    """The track at `track` with its first key read: the interpolation, the
    global sequence and its duration, how many per-sequence arrays it has and
    how many of them hold a key, the first array holding one (`_key_array`),
    and, on a track without a global sequence, the first array holding one
    whose sequence's animation id is 0 (which animation that is stays
    **[verify]**)."""
    (interpolation,) = _U16.unpack_from(blob, track)
    (global_sequence,) = _GLOBAL.unpack_from(blob, track + _GLOBAL_AT)
    arrays, times_at = _BONE.unpack_from(blob, track + _TIMESTAMPS_AT)
    values, values_at = _BONE.unpack_from(blob, track + _VALUES_AT)
    if values != arrays:
        raise facts.FactsError(f"a bone track has {arrays} timestamp and {values} value arrays")
    _array_in(blob, arrays, times_at, _BONE.size, "a bone track's sequences")
    _array_in(blob, values, values_at, _BONE.size, "a bone track's values")
    out: dict[str, Any] = {
        "interpolation": interpolation,
        "global_sequence": global_sequence,
        "global_duration": None,
        "arrays": arrays,
        "arrays_with_keys": 0,
        "first_array": None,
        "animation_0_array": None,
    }
    sequences: list[tuple[int, bool]] | None = None
    if global_sequence >= 0:
        loops, loops_at = _BONE.unpack_from(blob, _GLOBAL_LOOPS_AT)
        if global_sequence >= loops:
            raise facts.FactsError(f"global sequence {global_sequence} of {loops}")
        _array_in(blob, loops, loops_at, _U32.size, "the global sequences")
        out["global_duration"] = _U32.unpack_from(blob, loops_at + _U32.size * global_sequence)[0]
    elif arrays:
        sequences = _sequences(blob)
        if arrays > len(sequences):
            raise facts.FactsError(
                f"a bone track has {arrays} arrays for {len(sequences)} sequences"
            )
    counts = [_BONE.unpack_from(blob, times_at + _BONE.size * i)[0] for i in range(arrays)]
    out["arrays_with_keys"] = sum(1 for n in counts if n)
    stride = (_QUAT if rotation else _PIVOT).size * (3 if interpolation in _SPLINES else 1)

    def read(index: int) -> dict[str, Any]:
        sequence = sequences[index] if sequences is not None else None
        return _key_array(blob, times_at, values_at, index, sequence, stride, rotation)

    first = next((i for i, n in enumerate(counts) if n), None)
    if first is not None:
        out["first_array"] = read(first)
    if sequences is not None:
        zero = next((i for i, n in enumerate(counts) if n and sequences[i][0] == 0), None)
        if zero is not None:
            out["animation_0_array"] = read(zero)
    return out


def sheath_rotation_keys(body: m2.M2Model, data: Buffer) -> dict[str, dict[str, Any]]:
    """For each point of `SHEATH` the body model has, its bone and that
    bone's rotation track with its first key read (`_keys`). The probe of
    M15-26: whether a sheath bone's rotation could give a carry angle."""
    blob, count, offset = _bones(body, data)
    out: dict[str, dict[str, Any]] = {}
    for point_id in SHEATH:
        point = body.attachment(point_id)
        if point is None:
            continue
        _bone(blob, count, offset, point.bone)  # the range check
        at = offset + _BONE_SIZE * point.bone
        out[str(point_id)] = {"bone": point.bone, "rotation": _keys(blob, at + _ROTATION_AT, True)}
    return out


def hand_bone_keys(body: m2.M2Model, data: Buffer) -> dict[str, list[dict[str, Any]]]:
    """For each point of `HAND` the body model has, its bone and the bones
    above it (`_chain`), in that order, each with its translation and
    rotation tracks' first keys read (`_keys`). The probe of M15-26: the one
    stored position of the hand points' bones."""
    blob, count, offset = _bones(body, data)
    out: dict[str, list[dict[str, Any]]] = {}
    for point_id in HAND:
        point = body.attachment(point_id)
        if point is None:
            continue
        _bone(blob, count, offset, point.bone)
        bones = [point.bone, *(link["bone"] for link in _chain(blob, count, offset, point.bone))]
        out[str(point_id)] = [
            {
                "bone": bone,
                "translation": _keys(blob, offset + _BONE_SIZE * bone + _TRANSLATION_AT, False),
                "rotation": _keys(blob, offset + _BONE_SIZE * bone + _ROTATION_AT, True),
            }
            for bone in bones
        ]
    return out


def _running(inst: Install, flavor: str) -> bool | None:
    try:
        state = process.client_state([inst.root], flavor_folders=[flavor])
    except Exception:
        return None
    if state is process.ClientState.RUNNING:
        return True
    if state is process.ClientState.NOT_RUNNING:
        return False
    return None


def model_facts(
    install_root: str | None,
    flavor_folder: str,
    tables_dir: Path,
    chars: Sequence[tuple[str, Path]],
    captured: str,
) -> dict[str, Any]:
    inst = discover(install_root)
    source = casc.open_source(inst, flavor_folder)
    report = source.report
    version = report.index.version
    tables = facts.read_tables(tables_dir, version)
    characters = []
    for label, path in chars:
        plan = renderplan.plan_for_record(tables, labaddon.read_char(path), statter=source)
        data = source.read(plan.body.file_data_id)
        body = m2.parse_m2(data)
        points = attachment_points(body, data)
        characters.append(
            {
                "label": label,
                **character_facts(charmodel.build(plan, source), plan),
                "attachment_points": points,
                "other_attachment_points": other_attachment_points(body, data),
                "held_point_parents": held_point_parents(body, data),
                "sheath_point_parents": sheath_point_parents(body, data),
                "bones": bone_facts(body, data),
                "sheath_rotation_keys": sheath_rotation_keys(body, data),
                "hand_bone_keys": hand_bone_keys(body, data),
            }
        )
    return {
        "schema": SCHEMA,
        "kind": "charmodel-derived",
        "made_by": "scripts/charmodel_facts.py",
        "captured": captured,
        "flavor": flavor_folder,
        "product": report.index.product,
        "version": version,
        "build": report.index.build,
        "tables_build": tables.build,
        "client_running": _running(inst, flavor_folder),
        "characters": characters,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--flavor", required=True, help="the flavor folder, as on disk")
    parser.add_argument("--tables", required=True, type=Path, help="the recorded tables' folder")
    parser.add_argument(
        "--char",
        required=True,
        action="append",
        type=facts._char,
        help="LABEL=WowLab.lua (repeat)",
    )
    parser.add_argument("--out", required=True, type=Path, help="the JSON file to write")
    parser.add_argument("--root", default=None, help="the install (default: discovery)")
    parser.add_argument("--captured", required=True, help="the capture date, YYYY-MM-DD")
    args = parser.parse_args(argv)
    out: Path = args.out
    try:
        facts.refuse_install(out)
        if out.exists():
            raise facts.FactsError("--out is already there; a fixture is never replaced")
        record = model_facts(args.root, args.flavor, args.tables, args.char, args.captured)
        text = json.dumps(record, indent=2) + "\n"
        with out.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    except (
        facts.FactsError,
        casc.CascError,
        blp.BlpError,
        InstallError,
        WowlabError,
        OSError,
    ) as exc:
        print(f"charmodel_facts: {exc}", file=sys.stderr)
        return 1
    md5s = {c["label"]: c["glb"]["bin_md5"] for c in record["characters"]}
    print(f"wrote {out}: {record['product']} {record['version']}, {md5s}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
