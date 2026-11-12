# Probe from review of m17/02-char-history; reproduces a save with nothing changed shown as "likely restored"
"""The lab-addon records no time (`labaddon.py`, "the addon records no time")
and the client rewrites `WowLab.lua` at every logout and /reload. A /reload
with nothing changed therefore gives the same bytes under a new save time,
as the very next row. `char history` marks every row whose bytes an earlier
row had as "likely restored, so the time is when it was put back". For the
row straight after the identical one, a re-save is the plain reading, and
"likely restored" tells the player something that probably did not happen.

Real records (the module fixture's R1 and R2); the sequence is constructed.
The positive control is the restore shape this flag is for: A, B, then A
again, where the third row is still marked.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_cli_char_history import (  # noqa: F401
    R1,
    R2,
    T1,
    T2,
    T3,
    _install,
    _json,
    _put,
    _record,
    _run,
    _snap,
    user_dirs,
)

_FLAG = "likely restored"


def test_positive_control_a_b_a_is_marked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _install(tmp_path, monkeypatch)
    _put(root, _record(*R1), T1)
    _snap(root, 1)
    _put(root, _record(*R2), T2)
    _snap(root, 2)
    _put(root, _record(*R1), T3)
    rows: list[dict[str, Any]] = _json()["rows"]
    assert rows[2]["same_bytes_as_ns"] == T1
    assert _run().stdout.count(_FLAG) == 1


def test_constructed_resave_with_nothing_changed_is_not_called_a_restore(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _install(tmp_path, monkeypatch)
    _put(root, _record(*R1), T1)
    _snap(root, 1)
    _put(root, _record(*R1), T2)  # a /reload: same bytes, the next save time
    out = _run().stdout
    assert _FLAG not in out, out
