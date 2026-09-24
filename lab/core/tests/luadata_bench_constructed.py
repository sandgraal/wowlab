"""Constructed SavedVariables benchmark for `wowlab_core.luadata` (M10-04).

CONSTRUCTED INPUT, not a capture. The corpus has no file near the
`docs/LAB_PLAN.md` §6.4 target (50 MB parsed in under 10 s and under
1.5 GB RSS), so, as §6.4 amendment item 5 says, the target is measured on a
generated document until a real file of that size is captured. Not a pytest
module (the name does not start with `test_`); run it by hand:

    uv run python lab/core/tests/luadata_bench_constructed.py              # auction, Forever layout
    uv run python lab/core/tests/luadata_bench_constructed.py --shape all  # every shape and layout
    uv run python lab/core/tests/luadata_bench_constructed.py --at-budget  # the cost budget
    uv run python lab/core/tests/luadata_bench_constructed.py --parse FILE # time one file

Shapes (all 50 MiB by default, `--mib` to change):

- `auction`: records of an auction addon: a `[number]` key per item, then
  `["string"]` keys holding an item link, integers, a 16-digit float, a
  negative integer, a boolean, a seller name, a six-entry positional array
  and an empty table.
- `collection`: one flat table of `[number] = true` (a collection addon).
- `ids`: one flat positional array of distinct six-digit integers.
- `zeros`, `trues`: one flat positional array of `0` or of `true`, the
  densest entry count per byte in the client's layout (four and seven bytes
  an entry). At 50 MiB these exceed `luadata.MAX_COST`, so they are
  refused with `LuaLimitError`; `--at-budget` measures them at the budget.

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
from collections.abc import Callable, Iterator
from pathlib import Path

MiB = 1024 * 1024
SHAPES = ["auction", "collection", "ids", "zeros", "trues"]
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


def _zeros(i: int, indent: str, comments: bool) -> Iterator[str]:
    yield f"{indent}0," + (f" -- [{i + 1}]" if comments else "")


def _trues(i: int, indent: str, comments: bool) -> Iterator[str]:
    yield f"{indent}true," + (f" -- [{i + 1}]" if comments else "")


def generate(size: int, shape: str, layout: str) -> bytes:
    """A document of at least `size` bytes."""
    comments = layout == "reference"
    eol = "\r\n"
    tab = "\t" if comments else ""
    record = {
        "auction": _auction,
        "collection": _collection,
        "ids": _ids,
        "zeros": _zeros,
        "trues": _trues,
    }[shape]
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


# Documents for the cost budget (`--at-budget`): one flat table (or, for
# `assign`, the top level) of fixed-length items, each shape filled to just
# under `luadata.MAX_COST`. Cheap shapes are what the client writes densely;
# costly ones carry wide trivia or distinct comments (hand-edited files,
# §4.1), which the budget charges for.
BUDGET_SHAPES: dict[str, Callable[[int], bytes]] = {
    # cheap
    "zeros": lambda i: b"0,\r\n",
    "trues": lambda i: b"true,\r\n",
    "numkeys": lambda i: b"[%d] = 1,\r\n" % (1_000_000 + i),
    "strkeys": lambda i: b'["k%07d"] = 1,\r\n' % i,
    "distinct-numbers": lambda i: b"%d,\r\n" % (1_000_000 + i),
    "distinct-strings": lambda i: b'"s%07d",\r\n' % i,
    "tables": lambda i: b"{},\r\n",
    "assign": lambda i: b"a=1\n",
    # costly
    "wide-tables": lambda i: b'  [  "k%07d"  ]  =  {  }  ,  -- %07d\r\n' % (i, i),
    "wide-strings": lambda i: b'  [  "k%07d"  ]  =  "v%07d"  ,  -- %07d\r\n' % (i, i, i),
    "ref-comments": lambda i: b'\t\t"s%07d", -- [%07d]\r\n' % (i, i + 1),
    "key-comments": lambda i: b"[%d]=1,--%070d\n" % (1_000_000 + i, i),
    "table-comments": lambda i: b"{--%070d\n},--%070d\n" % (i, i),
}


def generate_items(count: int, shape: str) -> tuple[bytes, int, int]:
    """The document, the byte offset of the first item and the item length."""
    item = BUDGET_SHAPES[shape]
    head, tail = (b"\r\n", b"") if shape == "assign" else (b"\r\nX = {\r\n", b"}\r\n")
    repeated = shape in ("zeros", "trues", "tables", "assign")
    body = item(0) * count if repeated else b"".join(map(item, range(count)))
    return head + body + tail, len(head), len(item(0))


def _count_at_budget(shape: str) -> int:
    """Items of `shape` that fit the budget: parse an over-full document in a
    fresh interpreter and read the offset where the budget refused it."""
    from wowlab_core.luadata import MAX_FILE_BYTES

    item_length = len(BUDGET_SHAPES[shape](0))
    probe = min(8_999_999, (MAX_FILE_BYTES - 64) // item_length)  # seven-digit ids
    first_fit = _refused_at(probe, shape)
    if first_fit is None:
        raise SystemExit(f"{shape}: {probe:,} items fit the budget; make the probe larger")
    # The input buffer is charged too, so a smaller document holds more items.
    # Solve for the count from the per-item charge the first probe implies,
    # then probe just past it once (or a little further, until refused).
    from wowlab_core.luadata import MAX_COST

    data_length = len(generate_items(probe, shape)[0])
    per_item = (MAX_COST - data_length) / first_fit
    estimate = int(MAX_COST / (per_item + item_length))
    for step in range(8):
        probe = int(estimate * (1.002 + 0.01 * step)) + 10
        fits = _refused_at(probe, shape)
        if fits is not None:
            break
    else:
        raise SystemExit(f"{shape}: no refusal past the estimate {estimate:,}")
    # The probe's extra items were input bytes the real document does not
    # hold: probe just past `fits` until the count settles.
    for _ in range(4):
        probe = fits + 2 + int((probe - fits) * item_length / per_item)
        again = _refused_at(probe, shape)
        if again is None or again == fits:
            return fits
        fits = again
    return fits


def _refused_at(count: int, shape: str) -> int | None:
    data, first, length = generate_items(count, shape)
    with tempfile.TemporaryDirectory(prefix="luadata-bench-") as folder:
        target = Path(folder) / "Overfull.lua"
        target.write_bytes(data)
        del data
        out = subprocess.run(
            [sys.executable, __file__, "--offset", str(target)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.split()
    if out[0] == "parsed":
        return None
    return (int(out[1]) - first) // length


def _offset(path: Path) -> None:
    from wowlab_core import luadata

    try:
        luadata.read(path)
    except luadata.LuaLimitError as err:
        print("refused", err.offset, flush=True)
        return
    print("parsed", flush=True)


def _peak_rss_bytes() -> int:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak if sys.platform == "darwin" else peak * 1024  # Linux reports KiB


def measure(path: Path) -> None:
    """Parse `path` once with `luadata.read`, in this process, and print."""
    from wowlab_core import luadata

    size = path.stat().st_size
    rss_before = _peak_rss_bytes()
    started = time.perf_counter()
    try:
        doc = luadata.read(path)
    except luadata.LuaLimitError as err:
        elapsed = time.perf_counter() - started
        print(f"  {size:,} bytes: refused after {elapsed:.2f} s: {err}", flush=True)
        return
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
    parser.add_argument(
        "--at-budget",
        nargs="*",
        metavar="SHAPE",
        help="each budget shape (or those named) filled to just under MAX_COST, then one more",
    )
    parser.add_argument("--offset", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.parse is not None:
        print(f"file {args.parse.name}")
        measure(args.parse)
        return
    if args.offset is not None:
        _offset(args.offset)
        return
    if args.at_budget is not None:
        for shape in args.at_budget or BUDGET_SHAPES:
            count = _count_at_budget(shape)
            for n in (count, count + 1):
                data = generate_items(n, shape)[0]
                _run(data, f"shape {shape}, {n:,} items, {len(data) / MiB:.1f} MiB")
        return
    runs = [(s, lay) for s in SHAPES for lay in LAYOUTS] if args.shape == "all" else []
    for shape, layout in runs or [(args.shape, args.layout)]:
        _run(generate(int(args.mib * MiB), shape, layout), f"shape {shape}, layout {layout}")


def _run(data: bytes, label: str) -> None:
    """Write `data` to a temporary file and measure it in a fresh interpreter."""
    with tempfile.TemporaryDirectory(prefix="luadata-bench-") as folder:
        target = Path(folder) / "Constructed.lua"
        target.write_bytes(data)
        print(f"constructed input: {label}", flush=True)
        subprocess.run([sys.executable, __file__, "--parse", str(target)], check=True)


if __name__ == "__main__":
    main()
