# Probe from review of m18/01-luadata-nil-holes-tests; reproduces M18-01T pending marks that M18-01 cannot delete without failing the grader-diff gate
"""Constructed (not a real capture): reads the branch's own test sources.

M18-01 changes `luadata.py`, so `scripts/check_grader_diff.py` (M16-40,
the CI job "Graders activated by marker deletion only") judges it: a grader
may change only by deleting complete `xfail(strict=True)` marker blocks, and
no other file under `tests/` (an oracle or helper module) may change at all.

M18-01T puts two of its pending marks where that cannot be done:

- `parser/_luadata_oracle.py` is a support module, so deleting its
  `_PENDING.update(...)` line fails the gate outright;
- `test_sv_dump_c_encoder.py` applies its mark through
  `_PENDING.update(dict.fromkeys(..., _M18_01))`, which is not a marker
  block; the only removable block is the `_M18_01 = ...` assignment, and
  removing it leaves `_M18_01` dangling (NameError at collection).

So no deletion that the gate accepts activates those graders. Reproduced
end to end on a local commit: the gate prints
`FAIL '.../_luadata_oracle.py' (a test support or data file changed on a
load-bearing PR)` and `FAIL '.../test_sv_dump_c_encoder.py' ... deletes
lines that are not a complete xfail(strict=True) marker block`.

Positive control: a grader marked with an alias decorator and a
`marks=ALIAS` param keyword on its own line, the forms the gate's grammar names, is fully
activated by removing its blocks.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[4]
TESTS = ROOT / "lab" / "core" / "tests"
SCRIPT = ROOT / "scripts" / "check_grader_diff.py"
REASON = "M18-01 not implemented"


def _gate() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_check_grader_diff_m18_01t", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _activate(gate: ModuleType, text: str) -> tuple[str, list[str]]:
    """`text` with every marker block the gate accepts removed, and the
    names those removed blocks bound that the result still loads."""
    lines = gate.split_lines(text)
    blocks = gate.find_markers(text).blocks
    removed_names: set[str] = set()
    for start, end in blocks:
        chunk = "".join(lines[start:end])
        try:
            node = ast.parse(chunk.strip())
        except SyntaxError:
            continue
        for n in ast.walk(node):
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                removed_names.add(n.id)
    keep = [line for i, line in enumerate(lines) if not any(s <= i < e for s, e in blocks)]
    head = "".join(keep)
    loaded = {
        n.id
        for n in ast.walk(ast.parse(head))
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
    }
    return head, sorted(removed_names & loaded)


def _pending_files() -> list[Path]:
    return sorted(
        p for p in TESTS.rglob("*.py") if "review" not in p.parts and REASON in p.read_text()
    )


def test_m18_01_pending_marks_live_only_in_test_modules() -> None:
    support = [p.relative_to(ROOT).as_posix() for p in _pending_files()]
    support = [p for p in support if not Path(p).name.startswith("test_")]
    assert support == [], f"pending marks in support modules the gate freezes: {support}"


def test_m18_01_pending_marks_are_removable_marker_blocks() -> None:
    gate = _gate()
    bad = {}
    for path in _pending_files():
        if not path.name.startswith("test_"):
            continue
        head, dangling = _activate(gate, path.read_text())
        if REASON in head or dangling:
            bad[path.relative_to(ROOT).as_posix()] = {
                "reason_left": REASON in head,
                "dangling": dangling,
            }
    assert bad == {}


def test_positive_control_alias_and_param_marks_are_fully_removable() -> None:
    gate = _gate()
    text = (
        "import pytest\n"
        f'PENDING = pytest.mark.xfail(strict=True, reason="{REASON}")\n'
        "CASES = [\n"
        "    pytest.param(\n"
        '        "a",\n'
        "        marks=PENDING,\n"
        "    ),\n"
        '    pytest.param("b", id="b"),\n'
        "]\n"
        "\n"
        "\n"
        "@PENDING\n"
        "def test_x() -> None:\n"
        "    pass\n"
    )
    head, dangling = _activate(gate, text)
    assert REASON not in head and dangling == []
    compile(head, "<head>", "exec")
    # Negative control: the shape the branch uses is caught.
    bad = (
        "import pytest\n"
        f'_M = pytest.mark.xfail(strict=True, reason="{REASON}")\n'
        "_P = {}\n"
        '_P.update(dict.fromkeys(("a",), _M))\n'
    )
    _head, dangling = _activate(gate, bad)
    assert dangling == ["_M"]
