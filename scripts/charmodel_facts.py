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
the hand points 0, 1 and 2 the parent of the bone each sits on with its
pivot (and the next bone up when that pivot is the origin). No geometry,
no pixel, no byte of any file, no install path.

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
_PARENT_AT = 8
_PARENT = struct.Struct("<h")
HELD = (0, 1, 2)
PARENT_LEVELS = 2  # the parent, and one more level when the parent rests at the origin


def _bones(body: m2.M2Model, data: Buffer) -> tuple[memoryview, int, int]:
    md21 = next(c for c in body.chunks if c.tag == "MD21")
    blob = memoryview(data).cast("B")[md21.offset : md21.offset + md21.size]
    count, offset = _BONE.unpack_from(blob, _BONES_AT)
    return blob, count, offset


def _bone(blob: memoryview, count: int, offset: int, bone: int) -> tuple[int, list[float]]:
    """Bone `bone`'s parent index and pivot."""
    if not 0 <= bone < count:
        raise facts.FactsError(f"bone {bone} of {count}")
    at = offset + _BONE_SIZE * bone
    (parent,) = _PARENT.unpack_from(blob, at + _PARENT_AT)
    return parent, list(_PIVOT.unpack_from(blob, at + _PIVOT_AT))


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


def held_point_parents(body: m2.M2Model, data: Buffer) -> dict[str, list[dict[str, Any]]]:
    """For each hand point of `HELD` the body model has, the bones above the
    one it sits on: the parent with its pivot, and the parent's parent when
    the parent's pivot is (0, 0, 0) (at most `PARENT_LEVELS`; none above a
    root bone). The probe of M15-24."""
    blob, count, offset = _bones(body, data)
    out: dict[str, list[dict[str, Any]]] = {}
    for point_id in HELD:
        point = body.attachment(point_id)
        if point is None:
            continue
        chain: list[dict[str, Any]] = []
        parent, _ = _bone(blob, count, offset, point.bone)
        while parent >= 0 and len(chain) < PARENT_LEVELS:
            above, pivot = _bone(blob, count, offset, parent)
            chain.append({"bone": parent, "bone_pivot": pivot})
            if any(pivot):
                break
            parent = above
        out[str(point_id)] = chain
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
