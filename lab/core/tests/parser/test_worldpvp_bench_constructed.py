"""The time and memory bound of the world PvP reader (M20-18, docs/LAB_PLAN.md
§21.3 as built). CONSTRUCTED INPUT: the real log's lines repeated, with a
fight against one of two invented players every block and a new encounter
every block (the blocks are more than `ENCOUNTER_GAP_SECONDS` apart), so a
large log holds many encounters, read in bounded parts as `crew scan-log`
reads it.

The target: a log of 1 GB read in under 120 s, with Python's own
allocations under 200 MB at their peak. The default suite reads a 16 MB
log against a bound scaled from it; the 1 GB case runs only when
`WOWLAB_BENCH_1GB=1` is set, since it writes 1 GB under the test's
temporary folder. Both are `serial`: they measure time.
"""

from __future__ import annotations

import datetime
import os
import time
import tracemalloc
from pathlib import Path

import pytest
from _world_lines import FOE_A, FOE_B, HEADER, REAL, ZONE, hits_you, you_hit

from wowlab_core import worldpvp

OWNERS: dict[str, str | None] = {"Player-9999-00000001": None}
_START = datetime.datetime(2026, 4, 1, 2, 16, 31, tzinfo=datetime.UTC)  # written as -4


def _stamp(seconds: int) -> str:
    t = _START + datetime.timedelta(seconds=seconds)
    return f"{t.month}/{t.day}/{t.year} {t:%H:%M:%S}.000-4"


def _restamped(line: str, seconds: int) -> str:
    return _stamp(seconds) + line[line.index("  ") :]


def _write(path: Path, size: int) -> int:
    """A log of about `size` bytes; returns the blocks written."""
    body = [line for line in REAL[2:] if line]
    gap = worldpvp.ENCOUNTER_GAP_SECONDS + 1
    blocks = 0
    with path.open("wb") as f:
        f.write((HEADER + "\r\n" + ZONE + "\r\n").encode())
        written = 0
        while written < size:
            at = blocks * gap
            lines = [_restamped(line, at) for line in body]
            foe = FOE_A if blocks % 2 else FOE_B
            lines += [_restamped(you_hit(foe, 0), at), _restamped(hits_you(foe, 0), at + 1)]
            chunk = ("\r\n".join(lines) + "\r\n").encode()
            f.write(chunk)
            written += len(chunk)
            blocks += 1
    return blocks


def _read_all(path: Path) -> tuple[int, int, float, int]:
    """Read the log as `crew scan-log` does, part by part: (encounters,
    parts, seconds, peak bytes allocated)."""
    tracemalloc.start()
    started = time.perf_counter()
    start, state, encounters, parts = 0, None, 0, 0
    try:
        while True:
            scan = worldpvp.read_world(path, OWNERS, start=start, state=state)
            encounters += len(scan.encounters)
            parts += 1
            if not scan.more:
                break
            start, state = scan.end, scan.state
        took = time.perf_counter() - started
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return encounters, parts, took, peak


@pytest.mark.serial
@pytest.mark.parser
def test_constructed_a_16_mb_log_of_many_encounters_reads_within_the_bound(tmp_path: Path) -> None:
    path = tmp_path / "WoWCombatLog-040126_021630.txt"
    blocks = _write(path, 16 << 20)
    encounters, _, took, peak = _read_all(path)
    assert encounters == blocks
    size = path.stat().st_size
    print(f"\n{size / 1e6:.0f} MB, {encounters} encounters: {took:.1f} s, peak {peak / 1e6:.0f} MB")
    # 1 GB in 120 s is 8.4 MB a second; tracemalloc slows a read about twice,
    # and a loaded machine more, so the bound is a quarter of that rate.
    assert took < max(5.0, size / (2.1e6))
    assert peak < 200e6


@pytest.mark.serial
@pytest.mark.parser
def test_constructed_a_1_gb_log_reads_within_the_target(tmp_path: Path) -> None:
    if os.environ.get("WOWLAB_BENCH_1GB") != "1":
        pytest.skip("set WOWLAB_BENCH_1GB=1 to write and read a 1 GB log")
    path = tmp_path / "WoWCombatLog-040126_021630.txt"
    blocks = _write(path, 1 << 30)
    tracemalloc_free = time.perf_counter()
    start, state, encounters, parts = 0, None, 0, 0
    while True:
        scan = worldpvp.read_world(path, OWNERS, start=start, state=state)
        encounters += len(scan.encounters)
        parts += 1
        if not scan.more:
            break
        start, state = scan.end, scan.state
    took = time.perf_counter() - tracemalloc_free
    print(f"\n1 GB, {encounters} encounters in {parts} parts: {took:.1f} s")
    assert encounters == blocks
    assert took < 120
    _, _, _, peak = _read_all(path)
    print(f"peak {peak / 1e6:.0f} MB")
    assert peak < 200e6
