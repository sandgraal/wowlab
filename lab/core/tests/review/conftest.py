"""Review-probe collection rules.

Wall-clock timing probes are skipped on Windows. `docs/LAB_PLAN.md` §6.4
states its performance target (50 MB in under 10 s) as measured on the
owner's M1, and the `MAX_COST` budget is calibrated there; a probe that
compares a parse at the budget with that target is a measurement of the
owner's machine. GitHub's shared Windows runner is slower (the `0,` positive
control alone takes about 11 s there), so on win32 those probes report the
runner, not the parser.

A probe is a wall-clock timing probe when its file name ends in
`_time_target.py` (for example
`test_m10_04_semicolon_list_at_budget_misses_time_target.py`). Name a new
one that way and it is skipped on Windows with no change here. Only timing
probes match: memory probes end in `_memory_target.py` and, like every other
probe, keep running on Windows. The hook sees the whole session's items, so
it only touches files in this folder.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
TIMING_PROBE_SUFFIX = "_time_target.py"

SKIP_ON_WINDOWS = pytest.mark.skip(
    reason=(
        "wall-clock timing probe: the docs/LAB_PLAN.md §6.4 target (and MAX_COST) is "
        "calibrated on the owner's M1; a shared Windows runner is slower and would "
        "measure the machine, not the parser"
    )
)


def is_timing_probe(path: Path) -> bool:
    """A review probe (a file in this folder) named `*_time_target.py`."""
    return path.parent == HERE and path.name.endswith(TIMING_PROBE_SUFFIX)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if sys.platform != "win32":
        return
    for item in items:
        if is_timing_probe(item.path):
            item.add_marker(SKIP_ON_WINDOWS)
