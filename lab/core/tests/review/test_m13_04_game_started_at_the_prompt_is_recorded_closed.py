# Probe from review of m13/04-first-run; reproduces a first-run snapshot recorded "client closed" when the game started while the yes/no prompt waited.
"""`firstrun.run` asks the client state once, before the yes/no question,
and passes that answer to `snapshot_flavor` after the player replies. The
prompt waits for as long as the player likes; if the game is started in that
time, the snapshot is taken with the game running but its manifest records
`client_running: false`, no "The game is running" line is printed, and
`snap show` / profile notes that key on the flag stay silent.

M13-04 asks that a running game be "said plainly, behaving as `snap create`
does today"; `snap create` checks the client immediately before it captures
(the positive control below shows it records the running client).

Constructed: the captured tree copied into `tmp_path` and a fake process
table that gains a client while the (replaced) prompt is answered. Nothing
reads or writes a real install.
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_cli import (
    _running,
    flavor,  # noqa: F401
    idle,  # noqa: F401  (autouse: no client in the process table)
    replayed,  # noqa: F401  (autouse: recorded game data only)
    root,  # noqa: F401
    run,
    user_data,  # noqa: F401  (autouse: the store lives in tmp_path)
)

from wowlab_core import cli
from wowlab_core.snapshot import SnapshotStore


def test_positive_control_snap_create_records_a_running_client_constructed(
    root: Path, flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _running(monkeypatch, flavor)
    assert run("snap", "create").exit_code == 0
    (manifest,) = SnapshotStore().list()
    assert manifest.client_running is True


def test_a_game_started_while_the_prompt_waits_is_not_recorded_closed_constructed(
    root: Path, flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_interactive", lambda: True)

    def player_starts_the_game_then_answers_yes(question: str) -> bool:
        _running(monkeypatch, flavor)
        return True

    monkeypatch.setattr(cli, "_ask", player_starts_the_game_then_answers_yes)
    result = run("doctor", "--offline")
    assert result.exit_code == 0, (result.stdout, result.stderr)
    (manifest,) = SnapshotStore().list()
    assert manifest.client_running is not False, (
        "the snapshot was taken with the game running but records it as closed"
    )
