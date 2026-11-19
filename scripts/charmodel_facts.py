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
build's time. No geometry, no pixel, no byte of any file, no install path.

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
import sys
from collections.abc import Sequence
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
        body = m2.parse_m2(source.read(plan.body.file_data_id))
        points = {}
        for model in plan.item_models:
            point = (
                body.attachment(model.attachment_id) if model.attachment_id is not None else None
            )
            if point is not None:
                # The point's stored position (game axes), as evidence for
                # which points the rest pose places (§17.6 note of M15-11).
                points[str(point.id)] = {"bone": point.bone, "position": list(point.position)}
        characters.append(
            {
                "label": label,
                **character_facts(charmodel.build(plan, source), plan),
                "attachment_points": points,
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
