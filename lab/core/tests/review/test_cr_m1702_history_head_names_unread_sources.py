# Probe from review of m17/02-char-history; reproduces the text head naming copies it could not read as where the saves came from
"""`wowlab char history`'s text head says "N saves, from H of S snapshots and
the file in the install". H counts every snapshot that holds the file,
including one whose copy could not be read, and "the file in the install" is
printed even when the install's copy could not be read. In the module's
fixture (five snapshots, one with its object deleted) the install's file is
replaced by an unreadable one (constructed: an unterminated table): the head
then says the saves came from 5 snapshots and the install, while 4 snapshots
and no install copy gave rows. The positive control is the fixture as it
stands, where the install copy is read and appears on a row.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_cli_char_history import T_LIVE, _put, _run, history, user_dirs  # noqa: F401


def test_positive_control_live_copy_read_is_named(history: dict[str, Any]) -> None:  # noqa: F811
    out = _run().stdout
    assert "from the file in the install" in out  # a row really came from it
    assert "and the file in the install, oldest first:" in out


def test_constructed_unread_install_copy_is_not_named_as_a_source(
    history: dict[str, Any],  # noqa: F811
) -> None:
    _put(history["root"], b"WowLabCharDB = {\n", T_LIVE)
    result = _run()
    assert result.exit_code == 0, result.stdout
    out = result.stdout
    assert "  the file in the install: " in out  # listed as unreadable
    assert "from the file in the install" not in out  # no row came from it
    head = next(line for line in out.splitlines() if " saves, from " in line)
    assert "the file in the install" not in head, head
    assert "from 5 of 5 snapshots" not in head, head  # one of the five was not read
