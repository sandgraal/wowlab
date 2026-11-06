# Probe from review of m16/16-notes-flag; reproduces the goldens' normaliser rewriting backslashes past the path it normalises (JSON and `_safe` escapes become "/").
"""Constructed strings only (hostile/boundary): no install, no fixture.

`test_cli_notes._placeholders` turns each backslash after `<tmp>`,
`<package>` or `~` into "/" up to the end of the line, a quote or a
parenthesis, so it rewrites more than the path. A `--json` string holding a
path and then an escaped newline, or a text line holding a path and then a
`_safe` escape (`\\x85`), compares equal to a different output. The
snapshots are the proof that `--json` is unchanged (M16-16 acceptance), so
the normaliser must change nothing outside the path itself.

Positive control: a Windows path after `<tmp>` still becomes forward
slashes.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path, PureWindowsPath

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import test_cli_notes as notes

TMP = Path("/nowhere")


def test_positive_control_a_windows_path_after_tmp_is_normalised() -> None:
    # The real Windows spelling, through the known paths (rewritten after
    # review of 7f42070, with the code reviewer's approval).
    tmp = Path(str(PureWindowsPath("C:/t0")))
    known = [str(tmp), str(PureWindowsPath("C:/t0/World of Warcraft"))]
    assert notes._placeholders("Install: C:\\t0\\World of Warcraft (x)", tmp, known) == (
        "Install: <tmp>/World of Warcraft (x)"
    )


def test_an_escaped_newline_after_a_path_in_json_is_kept_constructed() -> None:
    text = json.dumps({"error": "/nowhere/x\nsecond line"})
    assert notes._placeholders(text, TMP) == '{"error": "<tmp>/x\\nsecond line"}'


def test_a_safe_escape_after_a_path_is_kept_constructed() -> None:
    text = "File: <tmp>/World of Warcraft/foo\\x85bar"
    assert notes._placeholders(text, TMP) == text
