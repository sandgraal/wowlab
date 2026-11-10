"""`doctor` says a journal record's state in words (M16-85).

The store line of `doctor`'s text output names the most recent journal
record's state in plain words, never by its name (`open`,
`rollback_incomplete` and the rest: the player-copy rules forbid printing a
constant name). `--json` keeps the state name unchanged, and `--redact` text
says the same words.

The install is the captured tree copied into `tmp_path` as `test_cli.py`
builds it, the user data directory is redirected and the process table is a
fake one with no client. Constructed (L8, boundary): the journal, a single
record per state returned in place of `guard.history`. Nothing reads or
writes a real install.
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

import json
import typing
from pathlib import Path

import pytest
from test_cli import (
    flavor,  # noqa: F401
    idle,  # noqa: F401  (autouse: no client in the process table)
    replayed,  # noqa: F401  (autouse: recorded game data only)
    root,  # noqa: F401
    run,
    user_data,  # noqa: F401
)

from wowlab_core import cli, guard

RECORD_ID = "20261110T120000Z-0001"  # constructed

WORDS = {
    "committed": "finished",
    "open": "did not finish",
    "rolled_back": "failed, and its files were put back",
    "rollback_incomplete": "failed, and putting the files back did not finish",
}
STATES = typing.get_args(guard._State)


def _journal(monkeypatch: pytest.MonkeyPatch, flavor: Path, state: str) -> None:
    record = guard.HistoryRecord(
        id=RECORD_ID,
        created_at="2026-11-10T12:00:00Z",
        label="addon install lab",
        snapshot_id="20261110T115959Z-0001",
        flavor_path=str(flavor),
        state=state,  # type: ignore[arg-type]
        rolled_back=state == "rolled_back",
        paths=(),
        created_dirs=(),
        temps_removed=(),
        temps_left=(),
    )
    monkeypatch.setattr(guard, "history", lambda **k: (record,))


def _store_line(stdout: str) -> str:
    return next(line for line in stdout.splitlines() if "most recent:" in line)


def test_every_state_the_write_gate_journals_has_words() -> None:
    assert set(STATES) == set(WORDS)
    assert cli._JOURNAL_STATE_WORDS == WORDS


@pytest.mark.parametrize("state", STATES)
def test_the_text_says_the_state_in_words_constructed(
    monkeypatch: pytest.MonkeyPatch, flavor: Path, state: str
) -> None:
    _journal(monkeypatch, flavor, state)
    for args in (("doctor",), ("doctor", "--redact")):
        result = run(*args)
        line = _store_line(result.stdout)
        assert f"most recent: {RECORD_ID} ({WORDS[state]}; 0 file(s))" in line, line
        for name in STATES:
            assert name not in line.replace(WORDS[state], ""), (args, name, line)


@pytest.mark.parametrize("state", STATES)
def test_json_keeps_the_state_name_constructed(
    monkeypatch: pytest.MonkeyPatch, flavor: Path, state: str
) -> None:
    _journal(monkeypatch, flavor, state)
    for args in (("doctor", "--json"), ("doctor", "--json", "--redact")):
        report = json.loads(run(*args).stdout)
        assert report["store"]["last_transaction"]["state"] == state, args


def test_a_state_this_version_does_not_know_is_not_printed_by_name() -> None:
    words = cli._journal_state_words("some_later_state")
    assert "some_later_state" not in words
    assert words == "in a state this version of wowlab does not know"
