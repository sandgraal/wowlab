#!/usr/bin/env python3
"""Write a derived fixture of the client's hotfix cache (M22-23, ADR-0033
and its note of 2026-11-23).

    uv run python scripts/hotfix_facts.py --flavor FOLDER --item ID [--item ID ...] \
        --captured YYYY-MM-DD --out PATH [--root INSTALL] [--tables-dir DIR]

Reads the flavor folder's newest `Cache/ADB/<locale>/DBCache.bin` through
`wowlab_core.hotfix` (read-only, from the install root one component at a
time) and writes, as JSON: the product, version and build of the flavor
(from `.build.info`), the cache's path relative to the flavor folder, its
header (format version, build), how many records it holds, per table the
cache is read for (`Item` and `ItemSparse`) its hash, record count and
records by status, and, per item asked for, every record the cache holds
for it in `Item` and `ItemSparse`, in file order (push id, status, data
size and the row it holds, decoded), and the row that ends up applied
(file order: a later record wins), decoded as the table's export holds it. With
`--tables-dir` (wowlab's game-data cache, holding `tables/<version>/`), the
overlay's counts against those tables as well. No byte of the file, no
record of any other table, no install path.

Refusals (exit 1): `--out` that is a link, one inside a game install, one
already there; a flavor with no hotfix cache or no version; any `hotfix`,
`gamedata` or `install` error, in one line on stderr. Written only after
everything is read.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from wowlab_core import gamedata, hotfix
from wowlab_core.errors import WowlabError
from wowlab_core.install import InstallError, discover

SCHEMA = 1
TABLES = ("Item", "ItemSparse")  # the item tables; the record is about items
INSTALL_MARKER = ".build.info"


class FactsError(Exception):
    """A refusal, reported in one line."""


def refuse_install(out: Path) -> None:
    """Refuse an output path that is a link or sits inside a game install."""
    if out.is_symlink():
        raise FactsError("--out is a link; a fixture is written to a plain path")
    try:
        parent = out.parent.resolve()
    except (OSError, RuntimeError) as exc:
        raise FactsError(f"cannot resolve the folder of --out ({exc})") from None
    for candidate in (parent, *parent.parents):
        if (candidate / INSTALL_MARKER).exists():
            raise FactsError("--out is inside a game install; nothing but guard writes there")


def hotfix_facts(
    root: str | None,
    flavor_folder: str,
    items: Sequence[int],
    captured: str,
    tables_dir: Path | None,
) -> dict[str, Any]:
    inst = discover(Path(root) if root else None)
    flavor = next((f for f in inst.flavors if f.folder == flavor_folder), None)
    if flavor is None:
        raise FactsError(f"no flavor folder {flavor_folder!r} in the install")
    if flavor.version is None:
        raise FactsError(f"{flavor_folder}: .build.info names no version for it")
    found = hotfix.find_caches(flavor.path)
    if not found:
        raise FactsError(f"{flavor_folder} has no Cache/ADB/<locale>/{hotfix.CACHE_FILE}")
    path = found[0]
    layouts = {name: hotfix.layouts()[name] for name in TABLES}
    cache = hotfix.read(path, TABLES, root=inst.root)
    by_table: dict[str, list[hotfix.HotfixRecord]] = {name: [] for name in layouts}
    for record in cache.records:
        by_table[record.table].append(record)
    tables: dict[str, Any] = {}
    for name, records in by_table.items():
        statuses = Counter(r.status for r in records)
        tables[name] = {
            "hash": f"0x{hotfix.sstrhash(name):08x}",
            "records": len(records),
            "by_status": {str(k): statuses[k] for k in sorted(statuses)},
        }
    rows: list[dict[str, Any]] = []
    for item_id in items:
        entry: dict[str, Any] = {"id": item_id}
        for name, layout in layouts.items():
            # File order, as `gamedata` applies them: a later record wins.
            mine = [r for r in by_table[name] if r.record_id == item_id]
            applied: dict[str, str] | None = None
            size: int | None = None
            listed: list[dict[str, Any]] = []
            for r in mine:
                decoded: dict[str, str] | None = None
                if r.status == hotfix.STATUS_ROW and r.data:
                    decoded = hotfix.decode_row(layout, r)
                    decoded.pop(hotfix.SOURCE_KEY)
                    applied, size = decoded, len(r.data)
                elif r.status in hotfix.STATUS_REMOVED:
                    applied, size = None, None
                listed.append(
                    {"push_id": r.push_id, "status": r.status, "size": len(r.data), "row": decoded}
                )
            entry[name] = {"records": listed, "row": applied, "size": size}
        rows.append(entry)
    overlay: dict[str, Any] | None = None
    if tables_dir is not None:
        version = flavor.version
        report = gamedata.overlay_hotfixes(
            cache,
            version,
            lambda name: gamedata.cached_table_path(name, version, cache_dir=tables_dir),
            tables=TABLES,
            file=path.relative_to(flavor.path).as_posix(),
        ).report()
        overlay = report.model_dump(mode="json")
    return {
        "schema": SCHEMA,
        "kind": "hotfix-derived",
        "captured": captured,
        "product": flavor.product,
        "flavor": flavor.folder,
        "version": flavor.version,
        "build": flavor.build,
        "file": path.relative_to(flavor.path).as_posix(),
        "header": {"version": cache.version, "build": cache.build},
        "records_in_file": cache.records_in_file,
        "tables": tables,
        "items": rows,
        "overlay": overlay,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n", 1)[0])
    parser.add_argument("--flavor", required=True, help="the flavor folder, as on disk")
    parser.add_argument("--item", required=True, action="append", type=int, help="an item id")
    parser.add_argument("--out", required=True, type=Path, help="the JSON file to write")
    parser.add_argument("--root", default=None, help="the install (default: discovery)")
    parser.add_argument("--captured", required=True, help="the capture date, YYYY-MM-DD")
    parser.add_argument(
        "--tables-dir", default=None, type=Path, help="wowlab's game-data cache folder"
    )
    args = parser.parse_args(argv)
    out: Path = args.out
    try:
        refuse_install(out)
        if out.exists():
            raise FactsError("--out is already there; a fixture is never replaced")
        record = hotfix_facts(args.root, args.flavor, args.item, args.captured, args.tables_dir)
        text = json.dumps(record, indent=2, ensure_ascii=False) + "\n"
        with out.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    except (FactsError, InstallError, WowlabError, ValueError, OSError) as exc:
        print(f"hotfix_facts: {exc}", file=sys.stderr)
        return 1
    print(
        f"wrote {out}: {record['product']} {record['version']}, cache build "
        f"{record['header']['build']}, {record['records_in_file']} records"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
