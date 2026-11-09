"""Serial RSS probe of the `wtfconfig` budgets (M16-25).

CONSTRUCTED inputs (L8: boundary and hostile cases), and every test id says
so. For each file type a child process builds the densest document its
budget allows (exactly `max_lines` typed lines filling `max_bytes`, every
name distinct, so no view shares an entry), parses it, builds its views, and
reports its own peak RSS growth. A second child does the same one line past
the budget, where the refusal must come before any line model is built.

The file is `serial` (root `conftest.py`): an RSS reading is calibrated on
an idle machine and runs alone after the parallel pass. The child reads its
own `VmHWM` on Linux, because `ru_maxrss` there carries the parent's
high-water mark across `execve` (the reason is in
`test_luadata_budget_constructed.py`). The input is built before the first
reading and kept alive, so the growth is the parse's own.
"""

from __future__ import annotations

import subprocess
import sys
import warnings

import pytest

pytestmark = [
    pytest.mark.parser,
    pytest.mark.skipif(sys.platform == "win32", reason="uses the resource module"),
]

CHILD = r"""
import resource, sys
from wowlab_core import wtfconfig
kind, over = sys.argv[1], sys.argv[2] == "over"
budget = {"config": wtfconfig.CONFIG_BUDGET, "bindings": wtfconfig.BINDINGS_BUDGET,
          "macros": wtfconfig.MACROS_BUDGET}[kind]
parse = {"config": wtfconfig.parse_config, "bindings": wtfconfig.parse_bindings,
         "macros": wtfconfig.parse_macros}[kind]
width = budget.max_bytes // budget.max_lines  # bytes per line, ending included

def line(i):
    if kind == "config":
        text = b'SET n%d "' % i
        return text + b"v" * (width - len(text) - 2) + b'"\n'
    if kind == "bindings":
        text = b"bind K%d " % i
        return text + b"A" * (width - len(text) - 1) + b"\n"
    if i == 0:
        return b'VER 3 01 "n" "1"\n'
    text = b"/cast %d " % i
    return text + b"s" * (width - len(text) - 1) + b"\n"

data = b"".join(line(i) for i in range(budget.max_lines + (1 if over else 0)))
assert over or (len(data) <= budget.max_bytes), len(data)

def peak():
    if sys.platform.startswith("linux"):
        with open("/proc/self/status") as status:
            for row in status:
                if row.startswith("VmHWM:"):
                    return int(row.split()[1]) * 1024
        raise SystemExit("no VmHWM in /proc/self/status")
    scale = 1 if sys.platform == "darwin" else 1024
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * scale

before = peak()
try:
    doc = parse(data)
except wtfconfig.WtfLimitError as err:
    print("refused", err.kind, err.line, peak() - before, len(data))
else:
    views = (doc.cvars, doc.effective()) if kind == "config" else (
        doc.bindings if kind == "bindings" else doc.macros)
    assert doc.to_bytes() == data
    print("parsed", len(doc.lines), "-", peak() - before, len(data))
"""

# Measured on Linux (M16-25): about 75 MiB for config, 68 MiB for bindings
# and 76 MiB for macros at the budget. The limit leaves room for another
# platform's allocator; a budget that let a document hold several times
# that would fail here.
PARSED_LIMIT = 160 * 1024 * 1024
# The refusal counts line breaks at C speed and builds nothing per line.
REFUSED_LIMIT = 8 * 1024 * 1024
TRIES = 5 if sys.platform == "darwin" else 1  # see the luadata probe


def _probe(kind: str, over: bool) -> tuple[str, str, str, int]:
    for _ in range(TRIES):
        out = subprocess.run(
            [sys.executable, "-c", CHILD, kind, "over" if over else "at"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.split()
        outcome, what, detail, grown = out[0], out[1], out[2], int(out[3])
        warnings.warn(
            # A plain UserWarning: the readings show in the warnings summary
            # (not a fault). A warning class defined here could not be
            # unpickled by an xdist controller, which cannot import a module
            # under `parser/` by name.
            f"probe reading, {sys.platform} {kind} {outcome}: growth {grown / 2**20:.2f} MiB",
            UserWarning,
            stacklevel=2,
        )
        if grown > 0 or over:
            break
    return outcome, what, detail, grown


@pytest.mark.parametrize("kind", ["config", "bindings", "macros"], ids=lambda k: f"constructed-{k}")
def test_constructed_document_at_the_budget_stays_under_the_memory_limit(kind: str) -> None:
    outcome, _, _, grown = _probe(kind, over=False)
    assert outcome == "parsed"
    assert grown > 0, "the child read no growth: it is not measuring the parse"
    assert grown < PARSED_LIMIT, f"a {kind} document at its budget grew RSS by {grown} bytes"


@pytest.mark.parametrize("kind", ["config", "bindings", "macros"], ids=lambda k: f"constructed-{k}")
def test_constructed_one_line_past_the_budget_is_refused_before_building_lines(kind: str) -> None:
    outcome, what, line, grown = _probe(kind, over=True)
    assert (outcome, what) == ("refused", "lines")
    assert int(line) > 1
    assert grown < REFUSED_LIMIT, f"refusing a {kind} document grew RSS by {grown} bytes"
