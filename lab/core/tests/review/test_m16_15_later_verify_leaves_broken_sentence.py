# Probe from review of m16/15-player-output-lint; reproduces a second [verify] in one report being cut out of a sentence that used it as its predicate ("... characters or bytes is.").
"""Real fixture sandbox (`fixtures/macos`, as the lint test builds it).

`_player_copy` prints the first `[verify]` of a run as "(not yet
confirmed)" and deletes every later one. Several notes use the marker as a
predicate ("whether it counts characters or bytes is [verify]"), so deleting
it leaves a sentence with no ending: `char macros` prints "... whether it
counts characters or bytes is." and "(whether the client rewrites it at
logout is)". §18.1 item 4 and `.claude/rules/player-copy.md` ask for the
marker in player words at most once per report; they do not allow a line
the player cannot read.

Positive control: the first such note in a run reads "is (not yet
confirmed)".
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import test_cli_player_copy as pc

from wowlab_core import cli

runner = CliRunner()
CHAR = "1/Labcharb-Labrealmd"
# A predicate with nothing after it: "is." or "is)" at a clause's end.
DANGLING = re.compile(r"\b(?:is|are)[.)](?:\s|$)")


def test_first_marker_reads_in_words_positive_control(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pc.sandbox(tmp_path, monkeypatch, "")
    out = runner.invoke(cli.app, ["macros", "check", "--character", CHAR])
    assert out.exit_code == 0, out.stderr
    assert "characters or bytes is (not yet confirmed)" in out.stdout


def test_no_sentence_is_left_without_its_predicate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pc.sandbox(tmp_path, monkeypatch, "")
    out = runner.invoke(cli.app, ["char", "macros", "--character", CHAR])
    assert out.exit_code == 0, out.stderr
    dangling = [line for line in out.stdout.splitlines() if DANGLING.search(line)]
    assert dangling == [], dangling
