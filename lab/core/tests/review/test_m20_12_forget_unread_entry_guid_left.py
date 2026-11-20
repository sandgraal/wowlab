# Probe from review of m20/12-crew-desk-fields; reproduces `crew forget` of a player whose own entry is unread leaving their GUID in the saved file under another name.
"""docs/LAB_PLAN.md §21.3 (as built, M20-12): `crew forget` of one name takes
every key with a GUID that key holds, and "refuses, writing nothing, when the
GUID would still be in the saved file or its backup". M20-12's acceptance:
forget leaves no key and no GUID of the player in the saved file.

When the forgotten player's OWN entry is unread (a field of the wrong type),
`_same_character` collects no GUID from it (it reads parsed players only), so
the other key holding that GUID is neither taken nor does `ids_left` refuse:
the forget succeeds and the GUID stays in WowLab.lua.

CONSTRUCTED INPUT: invented keys and an obviously invented GUID, built in
`tmp_path` by the shared crew builders; no real install, no real data dir.
"""

# ruff: noqa: F811  (the fixture imported from test_crew_matches is requested by name)
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "parser"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _crew_build import KEY_A, KEY_B, KEY_C, player
from test_crew_matches import _book, make, runner  # noqa: F401  (the fixture)

GUID = "Player-0000-0000000A"


def _forget_a(env: Any) -> Any:
    return env.run("crew", "forget", KEY_A, "--yes")


def test_positive_control_a_readable_entry_takes_the_other_name_constructed(make: Any) -> None:
    env = make(_book({KEY_A: player(guid=GUID), KEY_B: player(guid=GUID), KEY_C: player()}, []))
    result = _forget_a(env)
    assert result.exit_code == 0, result.output
    assert GUID.encode() not in env.data()
    assert KEY_B.encode() not in env.data()


def test_an_unread_own_entry_leaves_no_guid_behind_constructed(make: Any) -> None:
    # A's own entry is unread (its level is text) and holds the GUID; B, the
    # same character by that GUID, is readable.
    env = make(
        _book({KEY_A: player(level="x", guid=GUID), KEY_B: player(guid=GUID), KEY_C: player()}, [])
    )
    before = env.data()
    result = _forget_a(env)
    # Either the forget takes B too, or it refuses and writes nothing.
    if result.exit_code == 0:
        assert GUID.encode() not in env.data(), "the forgotten player's GUID is still in the file"
    else:
        assert env.data() == before
