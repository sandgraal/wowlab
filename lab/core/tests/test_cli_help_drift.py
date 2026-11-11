"""`undo --help` and `snap restore --help` say what the commands do (M16-08).

`undo` reverses the most recent change any write command made, and a second
`undo` redoes it, so its help names every command that writes through the
gate and says so. `snap restore --paths` can delete a file, and its help says
that in a sentence of its own. Only `--help` is run; nothing reads an install.
"""

from __future__ import annotations

import re

from typer.testing import CliRunner

from wowlab_core import cli

runner = CliRunner()

# Every command that writes into an install through the gate (each opens a
# `guard.transaction`), and so is what `undo` reverses.
WRITERS = ("snap restore", "profile apply", "sv merge", "addon install", "addon remove")


def _help(*path: str) -> str:
    result = runner.invoke(cli.app, [*path, "--help"], terminal_width=200)
    assert result.exit_code == 0, result.output
    # One line per paragraph, whatever the wrap width.
    return re.sub(r"\s+", " ", result.stdout)


def test_undo_help_names_every_write_it_reverses() -> None:
    text = _help("undo")
    for name in WRITERS:
        assert f"`{name}`" in text, name
    assert "an earlier undo" in text


def test_undo_help_says_a_second_undo_redoes_the_change() -> None:
    text = _help("undo")
    assert "running `wowlab undo` a second time redoes what the first one undid" in text
    assert "`snap list`" in text, "and where to go for an older state"


def test_undo_help_carries_no_internal_names() -> None:
    text = _help("undo")
    for internal in ("guard", "store lock", "expected_id", "write gate"):
        assert internal not in text, internal


def test_restore_paths_gives_the_deletion_rule_its_own_sentence() -> None:
    text = _help("snap", "restore")
    sentence = (
        "If the snapshot has no file at a path you name, inside a folder it saved, "
        "the file on disk at that path is deleted."
    )
    assert sentence in text
    assert "covers but does not hold" not in text
