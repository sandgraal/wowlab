"""Render "What leaves your machine" into README.md and docs/INSTALL.md (M16-07).

`lab/core/src/wowlab_core/disclosure.py` holds the block's only text. This
script rewrites what lies between the `<!-- network:begin -->` and
`<!-- network:end -->` markers in each document and leaves every other line
alone. A document without exactly one pair of markers fails both modes.

    uv run python scripts/gen_network_block.py --check   # exit 1 when a copy is stale
    uv run python scripts/gen_network_block.py --write   # rewrite the copies

`lab/core/tests/test_disclosure.py` runs the same comparison in the test suite.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from wowlab_core.disclosure import BEGIN, END, has_block, render_doc

ROOT = Path(__file__).resolve().parents[1]
DOCS = (ROOT / "README.md", ROOT / "docs" / "INSTALL.md")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="exit 1 if a copy is stale")
    mode.add_argument("--write", action="store_true", help="rewrite the copies")
    args = parser.parse_args(argv)

    stale = 0
    for doc in DOCS:
        current = doc.read_text(encoding="utf-8")
        if not has_block(current):
            # A target that lost its block is the stalest copy there is, and
            # --write cannot know where to put it back.
            print(f"{doc.name}: no {BEGIN} ... {END} pair; put the markers back", file=sys.stderr)
            stale = 1
            continue
        rendered = render_doc(current)
        if rendered == current:
            print(f"{doc.name}: up to date")
        elif args.check:
            print(f"{doc.name}: stale; run scripts/gen_network_block.py --write", file=sys.stderr)
            stale = 1
        else:
            doc.write_text(rendered, encoding="utf-8")
            print(f"{doc.name}: rewritten")
    return stale


if __name__ == "__main__":
    raise SystemExit(main())
