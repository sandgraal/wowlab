# Probe from review of m20/12-crew-desk-fields; reproduces a forget beside an unread same-GUID entry: the other name must stay byte for byte and be named.
"""docs/LAB_PLAN.md §21.3, ruled 2026-11-20 (M20-12 fix round 1): `crew forget
KEY` forgets the name it was given and nothing else; another name of the same
character keeps its own entry, GUID included; when the same character is also
kept under other names, the plan and the result say so in one sentence
naming them.

Two invented names share one invented GUID; one entry is unread (its level is
text). Forget either: the forgotten key is gone from the saved file and every
file under the crew folder, the other entry's bytes are unchanged, and the
plan and the result name the other key.

CONSTRUCTED INPUT: invented keys and an obviously invented GUID, built in
`tmp_path` by the shared crew builders; no real install, no real data dir.
"""

# ruff: noqa: F811  (the fixture imported from test_crew_matches is requested by name)
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "parser"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _crew_build import KEY_A, KEY_B, KEY_C, player
from test_crew_matches import _book, _every_file, make, runner  # noqa: F401  (the fixture)

from wowlab_core import crewdb

GUID = "Player-0000-0000000A"
SENTENCE = "wowlab also keeps the same character, by the game's id, as"


def _entry(data: bytes, key: str) -> bytes:
    start = data.index(f'["{key}"] = {{'.encode())
    return data[start : data.index(b"},", start)]


def _files(env: Any) -> list[Path]:
    return [*_every_file(crewdb.crew_root()), env.file]


def _check(env: Any, gone: str, kept: str) -> None:
    before = env.data()
    kept_entry = _entry(before, kept)
    plan = env.run("crew", "forget", gone, input="")
    assert plan.exit_code != 0 and env.data() == before
    assert f"{SENTENCE} {kept}" in plan.output, "the plan does not name the other name"
    result = env.run("crew", "forget", gone, "--yes")
    assert result.exit_code == 0, result.output
    after = env.data()
    assert _entry(after, kept) == kept_entry, "the other name's entry changed"
    for f in _files(env):
        assert gone.encode() not in f.read_bytes(), f.name
    assert f"{SENTENCE} {kept}" in result.output, "the result does not name the other name"


def _two(unread: str) -> bytes:
    a = player(guid=GUID, **({"level": "x"} if unread == KEY_A else {}))
    b = player(guid=GUID, **({"level": "x"} if unread == KEY_B else {}))
    return _book({KEY_A: a, KEY_B: b, KEY_C: player()}, [])


def test_positive_control_both_readable_constructed(make: Any) -> None:
    env = make(_two(""))
    env.sync()
    _check(env, KEY_A, KEY_B)


@pytest.mark.parametrize("gone, kept", [(KEY_A, KEY_B), (KEY_B, KEY_A)])
def test_forget_beside_an_unread_same_guid_entry_constructed(
    make: Any, gone: str, kept: str
) -> None:
    # KEY_A's entry is unread in both directions.
    env = make(_two(KEY_A))
    env.sync()
    _check(env, gone, kept)
