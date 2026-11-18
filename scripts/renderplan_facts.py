#!/usr/bin/env python3
"""Write a derived fixture of where a render plan's files stand in an
install (M15-09, ADR-0033).

    uv run python scripts/renderplan_facts.py --flavor FOLDER --tables DIR \
        --char LABEL=WOWLAB_LUA [--char ...] --captured YYYY-MM-DD --out PATH \
        [--root INSTALL]

Builds `wowlab_core.renderplan`'s plan for each character's lab-addon
record (`--char`: a label for the fixture and the per-character
`WowLab.lua`) from the recorded tables in `--tables` (files named
`<Table>.<version>.csv` or `<Table>.<version>.subset.csv`, as under
`lab/core/tests/fixtures/wago/`; the version is the flavor's own, from the
install), then asks `casc.open_source` (read-only: every file read into
memory and closed; `shmem` and `index.lock.0` never opened) where each file
the plan names stands: §17.1's four states. Writes, as JSON, the product,
version and build, the locale and platform, and per character the plan's
files with their state, the state's words, the decoded size and the content
hash when local, and the reader's note. Never any bytes of a file (no asset
byte enters the repository), nothing from `.build.info`'s `Tags` cell but
the locale, and no path of the install. Field names say `hash`, not `key`
(gitleaks reads `"…key": "<hex>"` as a credential).

The install is `--root`, else `WOWLAB_WOW_ROOT`, else the platform default
(`wowlab_core.install.discover`).

Refusals (exit 1): `--out` inside a game install or already there (a
committed fixture is never replaced); a table missing from `--tables` for
the install's version; any `casc`, `install`, `labaddon` or `renderplan`
error, in one line on stderr. The output is written only after everything
is read.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from wowlab_core import casc, labaddon, renderplan
from wowlab_core.errors import WowlabError
from wowlab_core.install import InstallError, discover

INSTALL_MARKER = ".build.info"
SCHEMA = 1


class FactsError(Exception):
    """A refusal, reported in one line."""


def refuse_install(out: Path) -> None:
    """Refuse an output path that is a link or sits inside a game install
    (every link of its folder resolved)."""
    if out.is_symlink():
        raise FactsError("--out is a link; a fixture is written to a plain path")
    try:
        parent = out.parent.resolve()
    except (OSError, RuntimeError) as exc:
        raise FactsError(f"cannot resolve the folder of --out ({exc})") from None
    for candidate in (parent, *parent.parents):
        if (candidate / INSTALL_MARKER).exists():
            raise FactsError("--out is inside a game install; nothing but guard writes there")


def read_tables(folder: Path, version: str) -> renderplan.RenderTables:
    """The recorded tables of `version` in `folder`, whole or subset."""
    tables: dict[str, list[dict[str, str]]] = {}
    for name in renderplan.REQUIRED_TABLES:
        for candidate in (f"{name}.{version}.csv", f"{name}.{version}.subset.csv"):
            path = folder / candidate
            if path.is_file():
                with path.open(encoding="utf-8", newline="") as handle:
                    tables[name] = list(csv.DictReader(handle))
                break
        else:
            raise FactsError(f"--tables has no {name} for {version}")
    return renderplan.RenderTables(version, tables)


def character_facts(plan: renderplan.RenderPlan) -> list[dict[str, Any]]:
    rows = []
    for f in plan.files:
        stat = f.stat
        assert stat is not None
        rows.append(
            {
                "file_data_id": f.file_data_id,
                "role": f.role,
                "state": stat.state,
                "words": stat.words,
                "size": stat.size,
                "content_hash": stat.content_key,
                "note": stat.note,
            }
        )
    return rows


def plan_facts(
    install_root: str | None,
    flavor_folder: str,
    tables_dir: Path,
    chars: Sequence[tuple[str, Path]],
    captured: str,
) -> dict[str, Any]:
    source = casc.open_source(discover(install_root), flavor_folder)
    report = source.report
    version = report.index.version
    tables = read_tables(tables_dir, version)
    characters = []
    for label, path in chars:
        plan = renderplan.plan_for_record(tables, labaddon.read_char(path), statter=source)
        characters.append(
            {
                "label": label,
                "chr_model_id": plan.body.chr_model_id,
                "look": plan.look.source,
                "files": character_facts(plan),
            }
        )
    return {
        "schema": SCHEMA,
        "kind": "renderplan-files-derived",
        "made_by": "scripts/renderplan_facts.py",
        "captured": captured,
        "flavor": flavor_folder,
        "product": report.index.product,
        "version": version,
        "build": report.index.build,
        "locale": report.locale,
        "platform": report.platform,
        "tables_build": tables.build,
        "characters": characters,
        "reopens": source.reopens,
    }


def _char(text: str) -> tuple[str, Path]:
    label, sep, path = text.partition("=")
    if not sep or not label or not path:
        raise argparse.ArgumentTypeError("--char is LABEL=PATH")
    return label, Path(path)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--flavor", required=True, help="the flavor folder, as on disk")
    parser.add_argument("--tables", required=True, type=Path, help="the recorded tables' folder")
    parser.add_argument(
        "--char", required=True, action="append", type=_char, help="LABEL=WowLab.lua (repeat)"
    )
    parser.add_argument("--out", required=True, type=Path, help="the JSON file to write")
    parser.add_argument("--root", default=None, help="the install (default: discovery)")
    parser.add_argument("--captured", required=True, help="the capture date, YYYY-MM-DD")
    args = parser.parse_args(argv)
    out: Path = args.out
    try:
        refuse_install(out)
        if out.exists():
            raise FactsError("--out is already there; a fixture is never replaced")
        record = plan_facts(args.root, args.flavor, args.tables, args.char, args.captured)
        text = json.dumps(record, indent=2) + "\n"
        with out.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    except (FactsError, casc.CascError, InstallError, WowlabError, OSError) as exc:
        print(f"renderplan_facts: {exc}", file=sys.stderr)
        return 1
    states = {c["label"]: sorted({f["state"] for f in c["files"]}) for c in record["characters"]}
    print(f"wrote {out}: {record['product']} {record['version']}, states {states}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
