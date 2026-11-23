"""`wowlab live recap` (M22-05) through Typer's runner.

The install is the `macos-70888/` capture copied into `tmp_path`
(`parser/_crew_build.install`), its account `WowLab.lua` holding a
CONSTRUCTED schema 2 crew book. The character `1/Sandgraal-Rizzen` is given
two real lab-addon records: `macos-70450/`'s record of `Sangraal-Sett`,
snapshotted as the earlier save, then its own captured record as the live
file (one folder walking through two characters' records is the constructed
part, as in `test_cli_char_history.py`). Syndicator's file is the real
capture of `macos/`, its character renamed to this one's and, after the
snapshot, one item count raised (CONSTRUCTED edits of real bytes). The
combat log is constructed (`parser/_world_lines.py`): an open-world fight,
then a battleground on the book's map. The owner's game id is set through
`_owner_guids`, as `test_crew_world.py` does (the captured records are older
than the id). The user data directory is redirected into `tmp_path`.
"""

# ruff: noqa: F811 - `make` is test_crewdb's fixture, imported and requested by name

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from wowlab_core import cli, snapshot, worldpvp

sys.path.insert(0, str(Path(__file__).resolve().parent / "parser"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _crew_build import ACCOUNT, FLAVOR, KEY_C, MAP, MAP_ID, book, match, player
from _world_lines import (
    FOE_A,
    FOE_B,
    FOE_D,
    OWNER_GUID,
    hits_you,
    log,
    stamp,
    you_die,
    you_hit,
    you_kill,
    zone,
)
from test_crewdb import Env, make  # noqa: F401 - the fixture

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CHAR = "1/Sandgraal-Rizzen"
SV = "SavedVariables/WowLab.lua"
EARLIER = FIXTURES / "macos-70450" / "forever" / "WTF" / "Account"
SYNDICATOR = FIXTURES / "macos" / "forever" / "WTF" / "Account" / "90000001#6" / "SavedVariables"
BASE = worldpvp.seconds_of(stamp(0))
assert BASE is not None
T_BEFORE = (BASE - 3600) * 10**9
T_LIVE = (BASE + 3600) * 10**9
BAT_WING = b'["itemCount"] = 2,\r\n["itemID"] = 12223,'


@pytest.fixture
def owner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_owner_guids", lambda lay, account: ({OWNER_GUID: None}, 0))


def _earlier_record() -> bytes:
    (acct,) = [p for p in EARLIER.iterdir() if p.is_dir()]
    return (acct / "1" / "Sangraal-Sett" / SV).read_bytes()


def _syndicator(raised: bool) -> bytes:
    data = (
        (SYNDICATOR / "Syndicator.lua")
        .read_bytes()
        .replace(b"Labchard Labrealme", b"Sandgraal Rizzen")
    )
    assert data.count(BAT_WING) == 1
    return data.replace(BAT_WING, b'["itemCount"] = 5,\r\n["itemID"] = 12223,') if raised else data


def _battle_log() -> bytes:
    return log(
        you_hit(FOE_A, 10),
        hits_you(FOE_B, 11),
        you_kill(FOE_A, 13),
        zone(100, instance=MAP_ID, name=MAP),
        you_hit(FOE_D, 105),
        you_kill(FOE_D, 106),
        hits_you(FOE_D, 111, overkill=0),
        you_die(112),
        zone(200),
    )


def _session(env: Env) -> Path:
    """The earlier save in a snapshot, then the live save, Syndicator and
    the log as the module docstring says."""
    acct = env.flavor / "WTF" / "Account" / ACCOUNT
    lab = acct / CHAR / SV
    lab.write_bytes(_earlier_record())
    os.utime(lab, ns=(T_BEFORE, T_BEFORE))
    syn = acct / "SavedVariables" / "Syndicator.lua"
    syn.write_bytes(_syndicator(raised=False))
    snapshot.SnapshotStore().create(
        env.root,
        [f"{FLAVOR}/WTF"],
        flavor_folder=FLAVOR,
        now=datetime(2026, 4, 1, 1, 0, 0, tzinfo=UTC),
    )
    lab.write_bytes(
        (
            FIXTURES / "macos-70888" / "forever" / "WTF" / "Account" / ACCOUNT / CHAR / SV
        ).read_bytes()
    )
    os.utime(lab, ns=(T_LIVE, T_LIVE))
    syn.write_bytes(_syndicator(raised=True))
    logs = env.flavor / "Logs"
    logs.mkdir()
    path = logs / "WoWCombatLog-040126_021630.txt"
    path.write_bytes(_battle_log())
    os.utime(path, ns=(T_LIVE - 10**9, T_LIVE - 10**9))
    return path


def _book() -> bytes:
    return book(
        {KEY_C: player()},
        schema=2,
        matches=[
            match([], [KEY_C], at=BASE + 205, won=True),
            match([], [KEY_C], at=BASE - 7200, won=False),  # before the earlier save
        ],
    )


def _json(env: Env, *args: str) -> dict[str, Any]:
    result = env.run("live", "recap", "--json", *args)
    assert result.exit_code == 0, (result.stdout, result.stderr, result.exception)
    data: dict[str, Any] = json.loads(result.stdout)
    cli.LiveRecapReport.model_validate(data)
    return data


def test_the_recap_names_the_match_and_the_session_constructed(make: Any, owner: Any) -> None:
    env = make(_book())
    _session(env)
    data = _json(env)
    assert data["character"] == CHAR and data["characters"] == 1 and data["logs"] == 1
    m = data["match"]
    assert (m["zone"], m["instance_id"], m["over"], m["ended_by"]) == (MAP, MAP_ID, True, "left")
    assert [o["key"] for o in m["opponents"]] == ["Inventedd-Otherfakerealm"]
    assert m["opponents"][0]["killed_by_you"] and m["opponents"][0]["killed_you"]
    assert (m["killing_blows"], m["deaths"]) == (1, 1)
    assert (m["result"], m["result_from"]) == ("won", "crew_book")
    assert m["book_foes"] == [KEY_C]
    s = data["session"]
    assert (s["since_ns"], s["saved_at_ns"]) == (T_BEFORE, T_LIVE)
    assert s["since_snapshot"] is not None
    assert s["level_after"] == 13 and s["level_before"] is not None
    assert s["gear_changes"], "two characters' records differ in gear"
    assert [(n["item_id"], n["name"], n["before"], n["after"]) for n in s["new_items"]] == [
        (12223, "Meaty Bat Wing", 2, 5)
    ]
    assert s["items_note"] is None
    assert [(x["at"], x["won"]) for x in s["matches"]] == [(BASE + 205, True)]
    met = {o["key"]: o for o in s["opponents"]}
    assert set(met) == {"Inventeda-Fakerealm", "Inventedb-Fakerealm", KEY_C}
    assert met[KEY_C]["matches"] == 1 and met[KEY_C]["encounters"] == 0
    assert met["Inventeda-Fakerealm"]["encounters"] == 1
    assert met["Inventeda-Fakerealm"]["killed_by_you"]
    assert "Inventedd-Otherfakerealm" not in met, "the battleground's fights are not the open world"


def test_the_text_says_what_the_json_says_and_is_player_copy_constructed(
    make: Any, owner: Any
) -> None:
    from test_cli_player_copy import Install as PlayerCopyInstall
    from test_cli_player_copy import hits

    env = make(_book())
    _session(env)
    data = _json(env)
    result = env.run("live", "recap")
    assert result.exit_code == 0, result.output
    for line in data["lines"]:
        assert line in result.stdout
    text = result.stdout
    assert f"Match: {MAP}, from " in text
    assert "Result: won (from the crew book)." in text
    assert "Your killing blows: 1; your deaths: 1." in text
    assert "Meaty Bat Wing: 2 to 5" in text
    assert "1 match: 1 won, 0 lost." in text
    says = PlayerCopyInstall.of(env.root)
    problems: list[str] = []
    for args in (("live", "recap"), ("live", "recap", "--notes"), ("live", "recap", "--help")):
        got = env.run(*args)
        assert got.exit_code == 0, got.output
        problems += [f"{' '.join(args)}: {h}" for h in hits(got.stdout + got.stderr, says)]
    assert problems == []


def test_with_no_game_id_and_no_earlier_save_it_says_so_constructed(make: Any) -> None:
    env = make(_book())
    data = _json(env)
    assert data["match"] is None
    assert "which characters are yours" in data["match_words"]
    s = data["session"]
    assert s is not None and s["since_ns"] is None
    assert s["not_compared"] == "wowlab has no earlier save of this character to compare with"


def test_a_match_under_way_waits_for_its_result_constructed(make: Any, owner: Any) -> None:
    env = make(_book())
    path = _session(env)
    data = path.read_bytes()
    path.write_bytes(data[: data.rindex(b"\r\n", 0, len(data) - 2) + 2])  # drop the zone line out
    m = _json(env)["match"]
    assert m["over"] is False and m["result"] is None
    text = env.run("live", "recap").stdout
    assert "still going on" in text


def test_the_recap_reads_and_writes_nothing_in_the_install_constructed(
    make: Any, owner: Any
) -> None:
    import hashlib

    env = make(_book())
    _session(env)

    def state() -> dict[str, tuple[str, int]]:
        return {
            str(p.relative_to(env.root)): (
                hashlib.sha256(p.read_bytes()).hexdigest(),
                p.stat().st_mtime_ns,
            )
            for p in sorted(env.root.rglob("*"))
            if p.is_file()
        }

    before = state()
    _json(env)
    env.run("live", "recap")
    assert state() == before
