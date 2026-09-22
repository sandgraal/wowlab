# Probe from review of m10/02-lab-capture-followup; reproduces a blank run before
# "(" dropping a foreign-name refusal, and a quadratic line lookup on LF-only files.
"""Two defects found by running `Identity.scrub` at 7f8c4ef.

1. The follow-up made any run of blanks around a joint character count
   ("Jaina  - Area 52" still refuses), so "a stray space never turns a refusal
   into a note". The parenthesised form `<word> (<realm>)` kept `` ?``: one
   space refuses, two spaces or a tab only produce a note. Still open at
   ebba464.
2. `_line_with_stranger` found the start of a line with
   `max(rfind(b"\\n"), rfind(b"\\r"))`. On a file with only one kind of line
   break, the other `rfind` scans back to byte 0 for every line that holds an
   own realm: 3.45 MB of LF lines took 44.7 s (main: 1.38 s; the same bytes
   with CRLF: 1.80 s). Fixed at ebba464 by a line index; kept as a guard.

Constructed hostile and boundary input with invented names; no install
(ADR-0012).
"""

from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "lab_capture.py"
CHARACTER = "Thrallmar"
REALM = "Area 52"
FOREIGN = "someone else's name on an own realm"


def _load() -> ModuleType:
    name = "lab_capture_review_probe_followup_blank_runs"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


lab_capture = _load()


def _identity() -> object:
    return lab_capture.Identity(characters=[CHARACTER], realms=[REALM])


def _refused(text: bytes) -> bool:
    result = _identity().scrub(text)  # type: ignore[attr-defined]
    return any(p.startswith(FOREIGN) for p in result.problems)


def test_constructed_positive_control_one_space_before_a_paren_refuses() -> None:
    assert _refused(b'"Jaina (Area 52)"')
    assert _refused(b'"Jaina  - Area 52"')  # the joint already tolerates a blank run


@pytest.mark.parametrize("text", [b'"Jaina  (Area 52)"', b'"Jaina\t(Area52)"'])
def test_constructed_a_blank_run_before_a_paren_still_refuses(text: bytes) -> None:
    assert _refused(text)


def _seconds(data: bytes) -> float:
    identity = _identity()
    started = time.perf_counter()
    identity.scrub(data)  # type: ignore[attr-defined]
    return time.perf_counter() - started


def test_constructed_lf_only_file_scrubs_as_fast_as_the_same_file_in_crlf() -> None:
    line = b'["Area 52"] = { ["x"] = 1, ["y"] = 2, ["zzzzzzzzzzzzzzzzzzz"] = 3 },'
    crlf = _seconds((line + b"\r\n") * 20_000)  # positive control: linear on every head
    lf = _seconds((line + b"\n") * 20_000)
    assert crlf < 5, f"CRLF {crlf:.2f} s"
    assert lf < 4 * crlf + 0.5, f"LF {lf:.2f} s against CRLF {crlf:.2f} s"
