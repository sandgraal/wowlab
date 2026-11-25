"""`wowlab crew send` and `wowlab_core.desktable` (M22-04): each
player's record, written into the account's `WowLab.lua` as `WowLabDeskDB`
for the line the lab-addon adds to a player's tooltip in game.

CONSTRUCTED INPUT, every byte: crew books from `parser/_crew_build.py` with
the four invented keys, open-world encounters built here as a combat-log
scan hands them to the crew database, constructed `Spy.lua` files with an
invented kill-on-sight entry, and databases made at run time under a user
data directory redirected into `tmp_path`, inside a copy of the captured
install `fixtures/macos-70888/`. Nothing here reads a real install or the
real data directory; the write gate writes into the copy only, with a fake
process table.
"""

# ruff: noqa: F811 - `make` is test_crewdb's fixture, imported and requested by name

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

import psutil
import pytest
import typer

from wowlab_core import cli, crewbook, crewdb, desktable, guard, luadata, nemesis, ui, worldpvp

sys.path.insert(0, str(Path(__file__).resolve().parent / "parser"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _crew_build import ACCOUNT, FLAVOR, KEY_A, KEY_B, KEY_C, KEY_D, T2, book, match, player
from _world_lines import OWNER_GUID
from test_crewdb import LIVE, Env, make  # noqa: F401 - the fixture

ZONE = "Invented Test Woods"
SPY_CHAR = f"WTF/Account/{ACCOUNT}/1/Sandgraal-Rizzen/SavedVariables/Spy.lua"
KOS_ONLY = "Inventede-Fakerealm"  # invented: only on a Spy kill-on-sight list
NL = "\r\n"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _matches() -> list[dict[str, Any]]:
    """Five matches (constructed): C against you in four (lost, lost,
    lost, won), D against you in the last (no winner)."""
    return [
        match([KEY_A], [KEY_C], at=T2, won=False),
        match([KEY_A], [KEY_C], at=T2 + 30, won=False),
        match([], [KEY_C], at=T2 + 45, won=False),
        match([], [KEY_C], at=T2 + 60, won=True),
        match([KEY_A], [KEY_D], at=T2 + 90, won=None),
    ]


def _book(*, on: bool = True, c_faced: int = 4, c_won: int = 1) -> bytes:
    players = {
        KEY_A: player(allied=3, allied_won=1),
        KEY_B: player(),
        KEY_C: player(faced=c_faced, faced_won=c_won),
        KEY_D: player(faced=1),
    }
    return book(players, schema=2, matches=_matches(), on=on)


def _p(key: str, *, by_you: bool = False, you: bool = False) -> worldpvp.WorldPlayer:
    name, realm = key.split("-")
    return worldpvp.WorldPlayer(
        key=key,
        name=name,
        realm=realm,
        guid=f"Player-9998-0000000{key[7].upper()}",
        side="foe",
        hits=2,
        killed_by_you=by_you,
        killed_you=you,
    )


def _world(env: Env) -> None:
    """Four open-world encounters (constructed): C kills you three times and
    you kill C once."""
    encounters = [
        worldpvp.WorldEncounter(
            owner_guid=OWNER_GUID,
            start=T2 + 1000 * (i + 1),
            end=T2 + 1000 * (i + 1) + 30,
            zone=ZONE,
            instance_id=0,
            died=you,
            players=[_p(KEY_C, by_you=by_you, you=you)],
        )
        for i, (by_you, you) in enumerate([(False, True), (False, True), (True, True)])
    ]
    scan = worldpvp.WorldScan(start=0, end=100, state=worldpvp.ZoneState(), encounters=encounters)
    resume = crewdb.WorldResume(start=0, how="new")
    out = crewdb.record_world(env.target(), "Logs/invented-0.txt", 100, "0" * 64, resume, scan)
    assert out.state == "scanned", out.words


def _spy(env: Env, key: str = KOS_ONLY) -> None:
    """A constructed Spy file for the captured character, with `key` on its
    kill-on-sight list."""
    text = NL.join(
        [
            "",
            "SpyPerCharDB = {",
            '["version"] = "1.1",',
            '["PlayerData"] = {',
            "},",
            '["KOSData"] = {',
            f'["{key}"] = 1795400050,',
            "},",
            "}",
            "",
        ]
    )
    path = env.flavor / SPY_CHAR
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode())


def _built(make: Any, **kw: Any) -> Env:
    env: Env = make(_book(**kw))
    out = env.sync()
    assert out.state == "synced", out.words
    _world(env)
    _spy(env)
    return env


def _plan(env: Env) -> cli.CrewSendReport:
    """A plan run: --json, no --yes, no answer (the declined shape)."""
    result = env.run("crew", "send", "--json", input="")
    assert result.exit_code == 1, (result.stdout, result.stderr)
    assert result.stderr.endswith(f"{cli.CONFIRM_DECLINED}\n")
    return cli.CrewSendReport.model_validate_json(result.stdout)


def _send(env: Env, *extra: str) -> cli.CrewSendReport:
    result = env.run("crew", "send", "--yes", "--json", *extra)
    assert result.exit_code == 0, (result.stdout, result.stderr)
    return cli.CrewSendReport.model_validate_json(result.stdout)


def _table(env: Env) -> Any:
    return luadata.parse(env.data()).to_python()[desktable.DESK_VARIABLE]


# ─── the records ─────────────────────────────────────────────────────────────


def test_each_row_is_the_record_the_book_s_counters_and_the_spy_mark_constructed(
    make: Any,
) -> None:
    env = _built(make)
    rows = env.rows()
    c = rows[KEY_C].record
    assert c is not None and c.personal.nemesis and c.team.rival
    done = _send(env)
    assert done.written and done.changed
    table = _table(env)
    assert table["schema"] == desktable.DESK_SCHEMA and table["count"] == 3
    assert re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", table["day"])
    # C: four matches (three lost, one won) and three open-world fights;
    # three killing blows on you, one of yours; the book's counters as written.
    assert table["players"][KEY_C] == {
        "fights": 7,
        "kills": 1,
        "deaths": 3,
        "matches": 4,
        "won": 1,
        "book_faced": 4,
        "book_won": 1,
        "nemesis": True,
        "rival": True,
    }
    assert c.fights == 7 and c.team.wins == 1 and c.personal.deaths == 3
    # D: one match with no winner; A only with you and B never against you:
    # no record, no row. The Spy-only player: the mark alone.
    assert table["players"][KEY_D] == {"fights": 1, "matches": 1, "book_faced": 1}
    assert KEY_A not in table["players"] and KEY_B not in table["players"]
    assert table["players"][KOS_ONLY] == {"kos": True}
    assert (done.players, done.nemeses, done.rivals, done.kos) == (3, 1, 1, 1)
    assert done.book_state == "on" and done.spy_unreadable == 0
    assert done.sync is not None and done.sync.state in ("synced", "unchanged")


def test_every_other_variable_keeps_its_bytes_constructed(make: Any) -> None:
    env = _built(make)
    before = env.data()
    _send(env)
    after = env.data()
    assert after.startswith(before)
    added = after[len(before) :]
    assert added.lstrip(b"\r\n").startswith(desktable.DESK_VARIABLE.encode() + b" = {")
    # The file's own line breaks (the capture writes CRLF).
    assert b"\r\n" in added and b"\n" not in added.replace(b"\r\n", b"")
    # The crew book reads as it did.
    assert crewbook.parse_book(after) == crewbook.parse_book(before)


def test_sending_again_the_same_day_changes_nothing_constructed(make: Any) -> None:
    env = _built(make)
    first = _send(env)
    data = env.data()
    again = _send(env)
    assert first.changed and not again.changed and not again.written
    assert again.written_sha256 is None and env.data() == data
    assert again.before is not None and again.before.players == 3
    assert [r.label for r in guard.history()] == ["crew send (3 players)"]


def test_a_second_send_replaces_the_table_in_place_constructed(make: Any) -> None:
    env = _built(make)
    _send(env)
    _spy(env, KEY_B)  # B is now on the list, the Spy-only player is not
    done = _send(env)
    assert done.before is not None and done.before.players == 3
    players = _table(env)["players"]
    assert players[KEY_B] == {"kos": True} and KOS_ONLY not in players
    assert env.data().count(desktable.DESK_VARIABLE.encode() + b" = ") == 1


def test_the_sync_after_a_send_is_not_held_constructed(make: Any) -> None:
    """The send's journal record leaves the book as it was, so the next
    read's sync does not take it for a put-back file."""
    env = _built(make)
    _send(env)
    out = env.sync()
    assert out.state in ("synced", "unchanged") and out.held == [], out.words
    env.save(_book(c_faced=5, c_won=1))  # a session in game: one more match
    out = env.sync()
    assert out.state == "synced" and out.held == [], out.words
    assert env.rows()[KEY_C].faced == 5
    # Control: the same bytes written under another label are a put-back.
    env.gate("sv merge (1 file)", env.data() + b"\r\n")
    assert env.sync().held == ["put_back"]


def test_undo_takes_the_records_out_again_constructed(make: Any) -> None:
    env = _built(make)
    before = env.data()
    done = _send(env)
    result = env.run_bare("undo", "--yes", f"--expect={done.transaction}")
    assert result.exit_code == 0, (result.stdout, result.stderr)
    assert env.data() == before


def test_the_text_plan_and_the_done_line_constructed(make: Any) -> None:
    env = _built(make)
    result = env.run("crew", "send", input="n\n")
    assert result.exit_code == 1
    assert "Write the records of 3 players into WowLab.lua of game account" in result.stdout
    assert "1 Nemesis, 1 Rival team, 1 on a Spy kill-on-sight list" in result.stdout
    assert "  replace  " in result.stdout and guard.history() == ()
    result = env.run("crew", "send", "--yes")
    assert result.exit_code == 0, (result.stdout, result.stderr)
    assert "Wrote the records of 3 players." in result.stdout
    assert "notes: --notes shows them" in result.stdout
    result = env.run("crew", "send")
    assert result.exit_code == 0
    assert "The game already holds these records (3 players" in result.stdout


def test_a_book_that_is_off_is_written_with_a_caveat_constructed(make: Any) -> None:
    env = _built(make, on=False)
    result = env.run("crew", "send", "--yes")
    assert result.exit_code == 0, (result.stdout, result.stderr)
    assert "only while the crew book is on in game" in result.stdout
    assert _send(env).book_state == "off"


def test_no_records_and_no_spy_writes_an_empty_table_constructed(make: Any) -> None:
    env: Env = make(book({KEY_A: player()}, schema=2, matches=[]))
    done = _send(env)
    assert done.players == 0 and done.written
    assert _table(env)["players"] in ({}, [])


def test_no_saved_file_is_refused_constructed(make: Any) -> None:
    env: Env = make(_book())
    env.file.unlink()
    result = env.run("crew", "send", "--yes")
    assert result.exit_code == 1
    assert "There is no WowLab.lua" in result.stderr and guard.history() == ()


def test_refused_while_the_client_runs_constructed(
    make: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = _built(make)
    before = env.data()

    class Game:
        pid = 202

        def name(self) -> str:
            return "Wow"

        def exe(self) -> str:
            return str(env.root / FLAVOR / "Wow.app" / "Contents" / "MacOS" / "Wow")

        def cmdline(self) -> list[str]:
            return [self.exe()]

        def status(self) -> str:
            return "running"

    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: iter([Game()]))
    result = env.run("crew", "send", "--yes")
    assert result.exit_code == 3, (result.stdout, result.stderr)
    assert env.data() == before and guard.history() == ()


# ─── the pin, and the UI's confirm step ──────────────────────────────────────


def test_the_plan_s_pin_lets_the_confirmed_run_write_constructed(make: Any) -> None:
    env = _built(make)
    plan = _plan(env)
    assert not plan.written and plan.changed and guard.history() == ()
    fields = cli.EXPECT_FIELDS[("crew", "send")]
    assert fields == ("target_sha256", "written_sha256")
    assert set(fields) <= set(cli.CrewSendReport.model_fields)
    pin = ":".join(str(getattr(plan, f)) for f in fields)
    done = _send(env, f"--expect={pin}")
    assert done.written and _sha(env.data()) == plan.written_sha256


def test_a_file_changed_between_plan_and_confirm_is_refused_constructed(make: Any) -> None:
    env = _built(make)
    plan = _plan(env)
    pin = f"{plan.target_sha256}:{plan.written_sha256}"
    env.save(_book(c_faced=5))  # a session played in between
    changed = env.data()
    result = env.run("crew", "send", "--yes", f"--expect={pin}")
    assert result.exit_code == 3, (result.stdout, result.stderr)
    assert env.data() == changed and guard.history() == ()


@pytest.mark.parametrize("pin", ["a" * 64, "absent:" + "a" * 64, "A" * 64 + ":absent"])
def test_a_pin_that_is_not_the_report_s_is_a_usage_error(make: Any, pin: str) -> None:
    env = _built(make)
    result = env.run("crew", "send", "--yes", f"--expect={pin}")
    assert result.exit_code == 2, (result.stdout, result.stderr)


def test_the_command_is_in_the_ui_s_confirm_step_with_its_pin_constructed(make: Any) -> None:
    leaf = ui._read_tree(typer.main.get_command(cli.app))[("crew", "send")]
    assert leaf.excluded is None and leaf.confirm == "declined"
    assert leaf.pin == ("target_sha256", "written_sha256")
    env = _built(make)
    plan = _plan(env)
    offer = ui._declined_offer(leaf, json.loads(plan.model_dump_json()))
    assert offer.pin == f"{plan.target_sha256}:{plan.written_sha256}"
    assert "WowLab.lua has changed" in (offer.note or "")


def test_the_journal_names_no_player_constructed(make: Any) -> None:
    env = _built(make)
    _send(env)
    (record,) = guard.history()
    assert record.label == "crew send (3 players)"
    for key in (KEY_A, KEY_B, KEY_C, KEY_D, KOS_ONLY):
        assert key not in record.label


def test_help_names_no_project_vocabulary() -> None:
    result = typer.testing.CliRunner().invoke(
        cli.app, ["crew", "send", "--help"], terminal_width=200
    )
    assert result.exit_code == 0
    assert not re.search(
        r"M\d\d-\d+|ADR-\d+|\bL[1-8]\b|\[verify\]|docs/|Crew\w*Report|desktable", result.stdout
    )


# ─── desktable on its own ────────────────────────────────────────────────────


def _record(**kw: Any) -> nemesis.Record:
    fights = [
        nemesis.Fight(key=KEY_C, source="match", row=1, at=T2, team="loss"),
        nemesis.Fight(key=KEY_C, source="world", row=2, at=T2 + 5, kills=1, deaths=0),
    ]
    record = nemesis.record_of(fights)
    assert record is not None
    return record.model_copy(update=kw)


def test_rows_keep_the_marked_and_the_latest_first_past_the_limit() -> None:
    plain = _record()
    records = [(f"Invented{i}-Fakerealm", plain, T2 + i) for i in range(5)]
    marked = _record(
        personal=nemesis.Personal(kills=0, deaths=3, nemesis=True),
    )
    records.append((KEY_D, marked, 0))
    rows, left_out = desktable.rows_from(records, {}, {KEY_B}, limit=4)
    keys = [r.key for r in rows]
    assert left_out == 3
    assert keys == sorted(keys)
    # The two marked ones, then the two latest fights.
    assert set(keys) == {KEY_D, KEY_B, "Invented4-Fakerealm", "Invented3-Fakerealm"}


def test_the_book_s_counters_are_whole_and_won_never_passes_faced() -> None:
    pvp = crewbook.CrewPvp(faced=2, faced_won=5)
    row = desktable.row_for(KEY_C, _record(), pvp, kos=False)
    assert row is not None and (row.book_faced, row.book_won) == (2, 2)
    row = desktable.row_for(KEY_C, _record(), crewbook.CrewPvp(faced=-1), kos=False)
    assert row is not None and (row.book_faced, row.book_won) == (0, 0)
    assert desktable.row_for(KEY_C, None, None, kos=False) is None


@pytest.mark.parametrize(
    "key",
    ['Q"uote-Fakerealm', "Back\\slash-Fakerealm", "Line\nbreak-Fakerealm", "Ünï-Fakerealm"],
)
def test_a_hostile_key_loads_back_as_itself_constructed(key: str) -> None:
    rows = [desktable.DeskRow(key=key, kos=True)]
    out = desktable.with_table(b"\r\nWowLabDB = {\r\n}\r\n", rows, "2026-11-24")
    loaded = luadata.parse(out).to_python()
    assert loaded[desktable.DESK_VARIABLE]["players"] == {key: {"kos": True}}
    assert loaded["WowLabDB"] in ({}, [])


def test_a_file_that_sets_the_table_twice_is_refused_constructed() -> None:
    data = b"WowLabDeskDB = {\n}\nWowLabDeskDB = {\n}\n"
    with pytest.raises(desktable.DeskTableError):
        desktable.with_table(data, [], "2026-11-24")


def test_a_day_that_is_not_a_date_is_refused() -> None:
    with pytest.raises(desktable.DeskTableError):
        desktable.with_table(b"", [], "24 Nov")


def test_read_table_says_what_the_file_holds() -> None:
    assert desktable.read_table(b"WowLabDB = {\n}\n") is None
    out = desktable.with_table(b"", [desktable.DeskRow(key=KEY_A, kos=True)], "2026-11-24")
    assert desktable.read_table(out) == desktable.DeskTableRead(
        schema_number=1, day="2026-11-24", players=1
    )
    assert desktable.read_table(b'WowLabDeskDB = "x"\n') == desktable.DeskTableRead()


def test_the_crew_write_labels_include_send_records() -> None:
    assert crewdb._CREW_WRITE.fullmatch("crew send (3 players)")
    assert crewdb._CREW_WRITE.fullmatch("crew send (1 player)")
    assert not crewdb._CREW_WRITE.fullmatch("crew send (3 players) x")
