#!/usr/bin/env python3
"""Write a derived fixture of the body textures `compositor` composes from
an install (M15-10, ADR-0033).

    uv run python scripts/compositor_facts.py --flavor FOLDER --tables DIR \
        --char LABEL=WOWLAB_LUA [--char ...] --captured YYYY-MM-DD --out PATH \
        [--root INSTALL]

Builds `renderplan`'s plan for each character's lab-addon record from the
recorded tables (as `scripts/renderplan_facts.py` does, whose helpers this
uses), composes the body texture with `compositor.compose` reading each
file through `casc.open_source` (read-only), and writes, as JSON: the
product, version and build, and per character the layout's size, the
composite's MD5 (`rgba_md5`), the same with the customization layers sorted
by texture target id instead of `Layer` (wow.export's order; the layer order
is unconfirmed), every painted layer's ids, blend mode, rectangle, texture
size, format and stretch, what was not painted and why, the alpha-0 pixels
of DXT1 inputs of alpha depth 0 (the three-colour block question of
`blp`), and the best of three composite times. No pixel and no byte of any
file, no install path.

Refusals (exit 1), as `renderplan_facts.py`'s: `--out` that is a link, one
inside a game install, one already there; a table missing from `--tables`;
any `casc`, `install`, `labaddon`, `renderplan`, `compositor` or `blp`
error, in one line on stderr. Written only after everything is read.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

from wowlab_core import blp, casc, compositor, labaddon, renderplan
from wowlab_core.errors import WowlabError
from wowlab_core.install import InstallError, discover

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


class _Counting:
    """The source's reads, and the DXT1 depth-0 inputs' alpha-0 pixels."""

    def __init__(self, source: casc.CascSource) -> None:
        self.source = source
        self.dxt1_alpha0: dict[int, int] = {}

    def read(self, fdid: int) -> memoryview:
        return self.source.read(fdid)

    def census(self, fdids: Sequence[int]) -> None:
        for fdid in fdids:
            image = blp.decode(self.source.read(fdid))
            if image.format == "dxt1" and image.header.alpha_depth == 0:
                self.dxt1_alpha0[fdid] = image.rgba[3::4].count(0)


def _painted(p: compositor.PaintedLayer) -> dict[str, Any]:
    j = p.job
    return {
        "kind": j.kind,
        "file_data_id": j.file_data_id,
        "blend_mode": j.blend_mode,
        "blend": j.blend,
        "section_type": j.section_type,
        "rect": [j.x, j.y, j.width, j.height],
        "layer": j.layer,
        "layer_row_id": j.layer_row_id,
        "slot": j.slot,
        "component_section": j.component_section,
        "texture": [p.texture_width, p.texture_height],
        "format": p.texture_format,
        "scale": [p.scale_x, p.scale_y],
    }


def character(
    source: casc.CascSource, tables: renderplan.RenderTables, label: str, path: Path
) -> dict[str, Any]:
    plan = renderplan.plan_for_record(tables, labaddon.read_char(path))
    reader = _Counting(source)
    best = float("inf")
    result = None
    for _ in range(3):
        start = time.perf_counter()
        result = compositor.compose(plan, reader)
        best = min(best, time.perf_counter() - start)
    assert result is not None
    by_target = plan.model_copy(
        update={"layers": tuple(sorted(plan.layers, key=lambda la: la.texture_target_id))}
    )
    other = compositor.compose(by_target, reader)
    reader.census(sorted({p.job.file_data_id for p in result.painted}))
    return {
        "label": label,
        "chr_model_id": plan.body.chr_model_id,
        "layout_id": plan.body.layout_id,
        "size": [result.width, result.height],
        "rgba_md5": result.rgba_md5(),
        "target_order_rgba_md5": other.rgba_md5(),
        "painted": [_painted(p) for p in result.painted],
        "not_painted": [n.model_dump() for n in result.not_painted],
        "dxt1_depth0_alpha0_pixels": {str(k): v for k, v in sorted(reader.dxt1_alpha0.items())},
        "seconds_best_of_3": round(best, 3),
    }


def composite_facts(
    install_root: str | None,
    flavor_folder: str,
    tables_dir: Path,
    chars: Sequence[tuple[str, Path]],
    captured: str,
) -> dict[str, Any]:
    source = casc.open_source(discover(install_root), flavor_folder)
    report = source.report
    version = report.index.version
    tables = facts.read_tables(tables_dir, version)
    return {
        "schema": SCHEMA,
        "kind": "compositor-derived",
        "made_by": "scripts/compositor_facts.py",
        "captured": captured,
        "flavor": flavor_folder,
        "product": report.index.product,
        "version": version,
        "build": report.index.build,
        "tables_build": tables.build,
        "characters": [character(source, tables, label, path) for label, path in chars],
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
        record = composite_facts(args.root, args.flavor, args.tables, args.char, args.captured)
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
        print(f"compositor_facts: {exc}", file=sys.stderr)
        return 1
    md5s = {c["label"]: c["rgba_md5"] for c in record["characters"]}
    print(f"wrote {out}: {record['product']} {record['version']}, {md5s}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
