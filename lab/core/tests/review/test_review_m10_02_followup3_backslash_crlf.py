# Probe from review of m10/02-lab-capture-followup-3; reproduces table_shape ending a
# quoted string at the LF of an escaped CRLF line break, so a `{` in the string nests.
"""One defect found by running `table_shape` at 30cbc4d (fix round 1).

Lua accepts a backslash followed by a line break inside a quoted string, and
counts CR LF (or LF CR) as ONE line break there (llex.c `read_string` calls
`inclinenumber`, which consumes the pair). Round 1 made a short string stop at
a raw line break; its body pattern `\\.` consumes the backslash and the CR,
then stops at the LF. The rest of the line is read as code: the `{` inside the
string counts as nesting. At cf4763d the same input gave the Lua answer.

The `\\` + LF and `\\` + CR forms are the positive controls: they pass today.
Constructed boundary input (docs/LAB_FORMATS.md §4.1 grammar, CRLF as in the
Forever SavedVariables of the 2026-09-22 amendment); no install (ADR-0012).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "lab_capture.py"


def _load() -> ModuleType:
    name = "lab_capture_review_probe_followup3_backslash_crlf"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


lab_capture = _load()


def _document(line_break: bytes) -> bytes:
    """One table holding two positional strings; the first is continued over a
    backslash-escaped line break and holds a `{` on its second line."""
    lb = line_break
    return b"X = {" + lb + b'"a\\' + lb + b'{",' + lb + b'"b",' + lb + b"}" + lb


@pytest.mark.parametrize(
    "line_break",
    [
        pytest.param(b"\n", id="constructed-control-lf"),
        pytest.param(b"\r", id="constructed-control-cr"),
        pytest.param(b"\r\n", id="constructed-crlf"),
    ],
)
def test_constructed_escaped_line_break_keeps_the_string_open(line_break: bytes) -> None:
    # Depth 1 (the `{` is inside the string), two positional entries.
    assert lab_capture.table_shape(_document(line_break)) == (1, 2)
