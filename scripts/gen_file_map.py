"""Render the file-map tables in docs/LAB_FILE_MAP.md from filemap.toml (M10-06).

`lab/core/src/wowlab_core/filemap.toml` is the single source of truth for
the file map. This script rewrites the tables between the
`<!-- filemap:begin <section> -->` / `<!-- filemap:end <section> -->` markers
in `docs/LAB_FILE_MAP.md` and leaves every other line alone.

    uv run python scripts/gen_file_map.py --check   # exit 1 when the doc is stale
    uv run python scripts/gen_file_map.py --write   # rewrite the tables

`lab/core/tests/test_filemap.py` runs the same comparison in the test suite.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from wowlab_core.filemap import render_doc

DOC = Path(__file__).resolve().parents[1] / "docs" / "LAB_FILE_MAP.md"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="exit 1 if the doc is stale")
    mode.add_argument("--write", action="store_true", help="rewrite the doc's tables")
    args = parser.parse_args(argv)

    current = DOC.read_text(encoding="utf-8")
    rendered = render_doc(current)
    if rendered == current:
        print(f"{DOC.name}: up to date")
        return 0
    if args.check:
        print(f"{DOC.name}: stale; run scripts/gen_file_map.py --write", file=sys.stderr)
        return 1
    DOC.write_text(rendered, encoding="utf-8")
    print(f"{DOC.name}: rewritten")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
