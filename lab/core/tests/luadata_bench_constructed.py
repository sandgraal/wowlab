"""Constructed SavedVariables benchmark for `wowlab_core.luadata` (M10-04).

CONSTRUCTED INPUT, not a capture. The corpus has no file near the
`docs/LAB_PLAN.md` §6.4 target (50 MB parsed in under 10 s and under
1.5 GB RSS), so, as §6.4 amendment item 5 says, the target is measured on a
generated document until a real file of that size is captured. Not a pytest
module (the name does not start with `test_`); run it by hand:

    uv run python lab/core/tests/luadata_bench_constructed.py              # auction, Forever layout
    uv run python lab/core/tests/luadata_bench_constructed.py --shape all  # every shape and layout
    uv run python lab/core/tests/luadata_bench_constructed.py --parse FILE # time one file

Shapes (all 50 MiB by default, `--mib` to change):

- `auction`: records of an auction addon: a `[number]` key per item, then
  `["string"]` keys holding an item link, integers, a 16-digit float, a
  negative integer, a boolean, a seller name, a six-entry positional array
  and an empty table.
- `collection`: one flat table of `[number] = true` (a collection addon).
- `ids`: one flat positional array of six-digit integers, the densest entry
  count per byte the client plausibly writes.

Layouts: `forever`, what the Forever client writes (docs/LAB_FORMATS.md §4.2
amendment of 2026-09-22: CRLF, a leading blank line, no indentation, no
`-- [n]`, every entry ending in `,`), and `reference`, the §4.2 example
(tab indentation, `-- [n]` after positional entries).

Each measurement runs in a fresh interpreter that only reads and parses the
file, so the peak RSS it prints is the parse (plus the file's bytes and the
interpreter), not the generator. It writes only inside a temporary
directory it removes.
"""

from __future__ import annotations

import argparse
import resource
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path

MiB = 1024 * 1024
SHAPES = ["auction", "collection", "ids"]
LAYOUTS = ["forever", "reference"]


def _auction(i: int, indent: str, comments: bool) -> Iterator[str]:
    inner = indent + "\t" if comments else ""
    deeper = inner + "\t" if comments else ""
    item = 200_000 + i
    price = 1_000 + (i * 7919) % 9_000_000
    link = f"|cnIQ{i % 5}:|Hitem:{item}::::::::80:{1490 + i % 7}:::::::::|h[Constructed {i}]|h|r"
    yield f"{indent}[{item}] = {{"
    yield f'{inner}["itemLink"] = "{link}",'
    yield f'{inner}["itemID"] = {item},'
    yield f'{inner}["quality"] = {i % 5},'
    yield f'{inner}["minBuyout"] = {price},'
    yield f'{inner}["marketValue"] = {price * 0.9731234567891234:.16g},'
    yield f'{inner}["delta"] = -{i % 260},'
    yield f'{inner}["isCommodity"] = {"true" if i % 3 == 0 else "false"},'
    yield f'{inner}["seller"] = "Constructed Seller {i % 97}",'
    yield f'{inner}["history"] = {{'
    for n in range(1, 7):
        yield f"{deeper}{(i * n) % 100_000}," + (f" -- [{n}]" if comments else "")
    yield f"{inner}}},"
    yield f'{inner}["tags"] = {{'
    yield f"{inner}}},"
    yield f"{indent}}},"


def _collection(i: int, indent: str, comments: bool) -> Iterator[str]:
    yield f"{indent}[{100_000 + i * 3}] = true,"


def _ids(i: int, indent: str, comments: bool) -> Iterator[str]:
    yield f"{indent}{100_000 + i}," + (f" -- [{i + 1}]" if comments else "")


def generate(size: int, shape: str, layout: str) -> bytes:
    """A document of at least `size` bytes."""
    comments = layout == "reference"
    eol = "\r\n"
    tab = "\t" if comments else ""
    record = {"auction": _auction, "collection": _collection, "ids": _ids}[shape]
    lines = ["", "CONSTRUCTED_DB = {", f'{tab}["version"] = 3,', f'{tab}["items"] = {{']
    out = [eol.join(lines) + eol]
    total = len(out[0])
    i = 0
    while total < size:
        chunk = eol.join(record(i, tab * 2, comments)) + eol
        out.append(chunk)
        total += len(chunk)
        i += 1
    out.append(f"{tab}}},{eol}}}{eol}CONSTRUCTED_SETTINGS = nil{eol}")
    return "".join(out).encode("utf-8")


def _peak_rss_bytes() -> int:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak if sys.platform == "darwin" else peak * 1024  # Linux reports KiB


def measure(path: Path) -> None:
    """Parse `path` once with `luadata.read`, in this process, and print."""
    from wowlab_core import luadata

    size = path.stat().st_size
    rss_before = _peak_rss_bytes()
    started = time.perf_counter()
    doc = luadata.read(path)
    elapsed = time.perf_counter() - started
    rss_after = _peak_rss_bytes()
    entries = tables = 0
    stack = [a.value for a in doc.assignments]
    while stack:
        value = stack.pop()
        if isinstance(value, luadata.LuaTable):
            tables += 1
            entries += len(value.entries)
            stack.extend(e.value for e in value.entries)
    took = f"{elapsed:.2f} s" if elapsed >= 1 else f"{elapsed * 1000:.1f} ms"
    print(
        f"  {size:,} bytes ({size / MiB:.1f} MiB), {tables:,} tables, {entries:,} entries: "
        f"parse {took}, peak RSS {rss_after / MiB:,.0f} MiB "
        f"(interpreter before the parse {rss_before / MiB:,.0f} MiB)",
        flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mib", type=float, default=50.0, help="document size in MiB")
    parser.add_argument("--shape", choices=[*SHAPES, "all"], default="auction")
    parser.add_argument("--layout", choices=LAYOUTS, default="forever")
    parser.add_argument("--parse", type=Path, help="measure this file instead of generating")
    args = parser.parse_args()
    if args.parse is not None:
        print(f"file {args.parse.name}")
        measure(args.parse)
        return
    runs = [(s, lay) for s in SHAPES for lay in LAYOUTS] if args.shape == "all" else []
    for shape, layout in runs or [(args.shape, args.layout)]:
        data = generate(int(args.mib * MiB), shape, layout)
        with tempfile.TemporaryDirectory(prefix="luadata-bench-") as folder:
            target = Path(folder) / "Constructed.lua"
            target.write_bytes(data)
            del data
            print(f"constructed input: shape {shape}, layout {layout}", flush=True)
            subprocess.run([sys.executable, __file__, "--parse", str(target)], check=True)


if __name__ == "__main__":
    main()
