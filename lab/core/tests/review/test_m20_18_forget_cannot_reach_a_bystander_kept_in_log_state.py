# Probe from review of m20/18-world-pvp; reproduces a player the log only names (a friendly player hitting an NPC) kept by name and GUID in `world_logs.state` (`ZoneState.names`), where `crew forget KEY --yes` refuses ("no player") and leaves them
"""CONSTRUCTED INPUT (the crew book and log builders of `test_crew_world`):
the real log's SPELL_DAMAGE with the owner swapped for an invented
friendly player (`Inventedc-Otherfakerealm`) hitting the real NPC. No
encounter, no players row.

BACKLOG M20-18: "forget leaves no key and no GUID of the player in the
database or its copies"; the brief: `crew forget` removes a player
completely. The positive control shows the name and GUID are stored by
the scan; the probe then forgets the key.
"""

# ruff: noqa: F811 - `make` is test_crewdb's fixture, imported and requested by name

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "parser"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _world_lines import (
    ALLY_C,
    FRIENDLY,
    SPELL_DAMAGE_YOU_TO_NPC,
    _at,
    _owner_as,
    log,
)
from test_crew_world import (
    _all_bytes,
    _book,
    _json,
    _scan,
    _write_log,
    owner,  # noqa: F401 - the fixture
)
from test_crewdb import make  # noqa: F401 - the fixture

KEY_C = "Inventedc-Otherfakerealm"


def _scanned(make: Any) -> Any:
    env = make(_book())
    env.sync()  # a database first, so the scan also makes today's copy
    line = _at(_owner_as(SPELL_DAMAGE_YOU_TO_NPC, ALLY_C, FRIENDLY), 20)
    _write_log(env, log(line))
    _json(_scan(env, "--json"))
    return env


def _held(env: Any) -> list[str]:
    return [name for name, data in _all_bytes(env) if ALLY_C[0].encode() in data]


def test_positive_control_the_scan_keeps_the_bystander(make: Any, owner: Any) -> None:
    assert _held(_scanned(make))


def test_forget_reaches_every_player_the_database_keeps(make: Any, owner: Any) -> None:
    env = _scanned(make)
    env.run("crew", "forget", KEY_C, "--yes")
    assert _held(env) == []
