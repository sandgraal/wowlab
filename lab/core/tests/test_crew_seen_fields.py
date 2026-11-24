"""What M20-11 records, at the desk (M20-12, docs/LAB_PLAN.md §21.2 "Opponents,
as built" and §21.3): the sync keeps each player's GUID, race, faction, spec,
honor level and latest scoreboard line in a schema 3 crew database (the copy
kept first, a database built by main's statements migrated), the latest
value winning and every value bounded; a solo shuffle is kept and shown as
one; one player met under two names (the same GUID) is shown with the other
names; and `crew forget` leaves neither the key nor the GUID of the player in
any file under the crew folder, in the saved file or in the game's backup.

CONSTRUCTED INPUT, every byte: ADR-0036 term 3 keeps every real crew record
out of the repository. Each book is built at run time by
`parser/_crew_build.py` inside a copy of the captured install in `tmp_path`,
with the four invented keys only and obviously invented GUIDs; every
database is built at run time under a user data directory redirected into
`tmp_path`. Nothing here reads a real install or the real data directory,
and no test writes to an install except through the write gate, on the
copy, with a fake process table.
"""

# ruff: noqa: F811  (the fixture imported from test_crew_matches is requested by name)
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest

from wowlab_core import cli, crewbook, crewdb

sys.path.insert(0, str(Path(__file__).resolve().parent / "parser"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _crew_build import (
    ACCOUNT,
    FLAVOR,
    KEY_A,
    KEY_B,
    KEY_C,
    KEY_D,
    MAP,
    MAP_ID,
    T0,
    T1,
    T2,
    account_file,
    match,
    player,
)
from test_crew_matches import Env, _book, _every_file, make, runner  # noqa: F401  (the fixture)

# Obviously invented ids, in the game's shape (never a player key's shape).
GUID_A = "Player-0000-0000000A"
GUID_B = "Player-0000-0000000B"
GUID_C = "Player-0000-0000000C"


def _line(at: int = T2, **more: Any) -> dict[str, Any]:
    """A scoreboard line as M20-11 keeps it: no name, no GUID, its time."""
    line: dict[str, Any] = {
        "faction": 1,
        "damageDone": 1000,
        "healingDone": 10,
        "killingBlows": 2,
        "deaths": 1,
        "talentSpec": "Frost",
        "at": at,
    }
    line.update(more)
    return line


def _seen(guid: str = GUID_A, **more: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "guid": guid,
        "race": "Gnome",
        "faction": "Alliance",
        "spec": "Frost",
        "honor_level": 12,
        "score": _line(),
        "allied": 1,
        "pvp_last": T2,
        "pvp_map": MAP,
        "pvp_side": "with",
    }
    fields.update(more)
    return fields


def _base(**a: Any) -> bytes:
    """KEY_A with everything M20-11 keeps, KEY_C with a GUID of their own,
    KEY_D plain, and one battleground: A with you, C against."""
    return _book(
        {
            KEY_A: player(**_seen(**a)),
            KEY_C: player(guid=GUID_C, faced=1, pvp_last=T2, pvp_side="against"),
            KEY_D: player(),
        },
        [match([KEY_A], [KEY_C])],
    )


def _db_rows(env: Env) -> dict[str, dict[str, Any]]:
    conn = sqlite3.connect(env.db())
    conn.row_factory = sqlite3.Row
    try:
        return {r["player_key"]: dict(r) for r in conn.execute("SELECT * FROM players")}
    finally:
        conn.close()


# ─── the sync keeps the new fields ───────────────────────────────────────────


def test_a_sync_keeps_what_m20_11_records_constructed(make: Any) -> None:
    env = make(_base())
    out = env.sync()
    assert out.state == "synced" and out.left_out == 0, out.words
    a = env.rows()[KEY_A]
    assert (a.guid, a.race, a.faction, a.spec, a.honor_level) == (
        GUID_A,
        "Gnome",
        "Alliance",
        "Frost",
        12,
    )
    assert a.score is not None and a.score.at == T2
    assert a.score.values == {
        "faction": 1,
        "damageDone": 1000,
        "healingDone": 10,
        "killingBlows": 2,
        "deaths": 1,
        "talentSpec": "Frost",
    }
    row = _db_rows(env)[KEY_A]
    assert row["guid"] == GUID_A and row["score_at"] == T2
    assert json.loads(row["score"]).get("damageDone") == 1000
    assert env.rows()[KEY_D].guid is None and env.rows()[KEY_D].score is None


def test_the_latest_value_wins_and_a_field_left_out_keeps_the_row_constructed(
    make: Any,
) -> None:
    env = make(_base())
    env.sync()
    # An older entry (a book put back, say) changes nothing it holds.
    env.save(_base(race="Dwarf", last=T0, pvp_last=T1, score=_line(at=T1, deaths=9)))
    env.sync(by_hand=True, confirm=lambda *_: True)
    a = env.rows()[KEY_A]
    assert a.race == "Gnome" and a.score is not None and a.score.values.get("deaths") == 1
    # A newer one wins; a field it leaves out keeps the row's value.
    newer = _seen(spec="Arcane", score=_line(at=T2 + 60, deaths=4), pvp_last=T2 + 60)
    del newer["race"]
    env.save(_book({KEY_A: player(**newer), KEY_C: player(guid=GUID_C)}, []))
    env.sync(by_hand=True, confirm=lambda *_: True)
    a = env.rows()[KEY_A]
    assert (a.race, a.spec) == ("Gnome", "Arcane")
    assert a.score is not None and (a.score.at, a.score.values.get("deaths")) == (T2 + 60, 4)


@pytest.mark.parametrize(
    "more",
    [
        {"honor_level": crewdb.HONOR_LEVEL_MAX + 1},
        {"race": "x" * (crewdb.TEXT_MAX_BYTES + 1)},
        {"score": _line(damageDone=crewdb.SCORE_NUMBER_MAX + 1)},
        {"score": _line(damageDone=-crewdb.SCORE_NUMBER_MAX - 1)},
        {"score": _line(className="x" * (crewdb.TEXT_MAX_BYTES + 1))},
        {"score": _line(**{f"f{i}": i for i in range(crewdb.SCORE_FIELDS_MAX)})},
        {"score": _line(at=1000)},
        {"score": _line(**{"n" * (crewdb.SCORE_NAME_MAX_BYTES + 1): 1})},
    ],
    ids=[
        "honor_constructed",
        "race_constructed",
        "score_number_constructed",
        "score_negative_constructed",
        "score_text_constructed",
        "score_fields_constructed",
        "score_time_constructed",
        "score_name_constructed",
    ],
)
def test_a_new_field_out_of_bounds_leaves_out_that_entry_only_constructed(
    make: Any, more: dict[str, Any]
) -> None:
    env = make(_base(**more))
    out = env.sync()
    assert out.state == "synced" and out.left_out == 1, out.words
    assert KEY_A not in env.rows() and KEY_C in env.rows()
    listed = env.json("crew", "list")
    assert [u["key"] for u in listed["unread"]] == [KEY_A]
    assert "invented" not in json.dumps(listed["unread"])


def test_a_new_field_at_its_bound_is_taken_constructed(make: Any) -> None:
    line = _line(damageDone=crewdb.SCORE_NUMBER_MAX, healingDone=-crewdb.SCORE_NUMBER_MAX)
    env = make(_base(honor_level=crewdb.HONOR_LEVEL_MAX, score=line))
    assert env.sync().left_out == 0
    assert env.rows()[KEY_A].honor_level == crewdb.HONOR_LEVEL_MAX


# ─── solo shuffle, and a key with no entry ───────────────────────────────────


def _shuffle_book() -> bytes:
    shuffle = match([KEY_A, KEY_C, KEY_D], [], won=None, kind="shuffle", at=T2 + 600)
    return _book(
        {KEY_A: player(**_seen()), KEY_C: player()},
        [match([KEY_A, KEY_B], [KEY_C]), shuffle],
    )


def test_a_shuffle_and_a_key_with_no_entry_sync_and_show_constructed(make: Any) -> None:
    env = make(_shuffle_book())
    out = env.sync()
    assert out.state == "synced" and (out.left_out, out.matches_left_out) == (0, 0), out.words
    assert [(m.kind, m.won, m.team, m.foes) for m in env.matches()] == [
        ("shuffle", None, [KEY_A, KEY_C, KEY_D], []),
        ("bg", True, [KEY_A, KEY_B], [KEY_C]),
    ]
    conn = sqlite3.connect(env.db())
    try:
        kept = conn.execute("SELECT kind, shuffle FROM matches ORDER BY at").fetchall()
    finally:
        conn.close()
    assert kept == [("bg", 0), ("arena", 1)]
    result = env.run("crew", "matches")
    assert result.exit_code == 0, result.output
    report = cli.CrewMatchesReport.model_validate(env.json("crew", "matches"))
    lines = result.stdout.splitlines()
    assert lines[0] == report.words and lines[1 : 1 + len(report.lines)] == report.lines
    assert report.lines[0].endswith(
        f"{MAP} (solo shuffle): sides change each round, so there is no one result"
    )
    assert report.lines[1] == f"    In it with you: {KEY_A}, {KEY_C}, {KEY_D}"
    assert "Against you" not in report.lines[1] and report.lines[2].endswith("your side won")
    assert [m.kind for m in report.matches] == ["shuffle", "bg"]
    # B has no entry in the book and is in a match: kept by the match, never unread.
    assert env.json("crew", "list")["unread"] == []
    assert KEY_B in crewdb.holds(FLAVOR, ACCOUNT)


def test_a_shuffle_with_sides_or_a_result_is_left_out_constructed(make: Any) -> None:
    odd = match([KEY_A], [KEY_C], kind="shuffle")
    env = make(_book({KEY_A: player(), KEY_C: player()}, [odd]))
    out = env.sync()
    assert out.matches_left_out == 1 and env.matches() == []


# ─── a database main built ───────────────────────────────────────────────────

# The statements main's crewdb ran to make a schema 2 database (M20-02),
# frozen here so the migration is tested against what main wrote.
_MAIN_SCHEMA_2 = (
    "PRAGMA application_id = 1464615746",
    "CREATE TABLE players ("
    " player_key TEXT PRIMARY KEY NOT NULL, name TEXT NOT NULL, realm TEXT NOT NULL,"
    " class TEXT, level INTEGER, role TEXT, guild TEXT, met INTEGER NOT NULL DEFAULT 0,"
    " first_at INTEGER, last_at INTEGER, where_name TEXT, where_id INTEGER,"
    " rating TEXT CHECK (rating IN ('good', 'bad')), note TEXT,"
    " allied INTEGER NOT NULL DEFAULT 0, faced INTEGER NOT NULL DEFAULT 0,"
    " allied_won INTEGER NOT NULL DEFAULT 0, faced_won INTEGER NOT NULL DEFAULT 0,"
    " pvp_last INTEGER, pvp_map TEXT, pvp_side TEXT CHECK (pvp_side IN ('with', 'against')),"
    " met_seen INTEGER NOT NULL DEFAULT 0, allied_seen INTEGER NOT NULL DEFAULT 0,"
    " faced_seen INTEGER NOT NULL DEFAULT 0, allied_won_seen INTEGER NOT NULL DEFAULT 0,"
    " faced_won_seen INTEGER NOT NULL DEFAULT 0, in_book INTEGER NOT NULL DEFAULT 0,"
    " marks_changed INTEGER NOT NULL, marks_agreed INTEGER NOT NULL,"
    " rating_own INTEGER NOT NULL DEFAULT 0 CHECK (rating_own IN (0, 1)),"
    " note_own INTEGER NOT NULL DEFAULT 0 CHECK (note_own IN (0, 1))) STRICT",
    "CREATE INDEX players_last ON players (last_at)",
    "CREATE INDEX players_rating ON players (rating)",
    "CREATE INDEX players_faced ON players (faced)",
    "CREATE INDEX players_allied ON players (allied)",
    "CREATE TABLE matches (match_id INTEGER PRIMARY KEY, at INTEGER NOT NULL, map TEXT,"
    " map_id INTEGER NOT NULL, kind TEXT NOT NULL CHECK (kind IN ('bg', 'arena')),"
    " won INTEGER CHECK (won IN (0, 1))) STRICT",
    "CREATE UNIQUE INDEX matches_at_map ON matches (at, map_id)",
    "CREATE TABLE match_players ("
    " match_id INTEGER NOT NULL REFERENCES matches (match_id) ON DELETE CASCADE,"
    " player_key TEXT NOT NULL, side TEXT NOT NULL CHECK (side IN ('team', 'foes')),"
    " PRIMARY KEY (match_id, player_key)) STRICT",
    "CREATE INDEX match_players_match ON match_players (match_id)",
    "CREATE INDEX match_players_key ON match_players (player_key)",
    "CREATE TABLE meta (name TEXT PRIMARY KEY NOT NULL, value ANY) STRICT",
    "INSERT INTO players (player_key, name, realm, met, rating, in_book, marks_changed,"
    " marks_agreed) VALUES ('Inventedc-Otherfakerealm', 'Inventedc', 'Otherfakerealm', 7,"
    " 'bad', 0, 1, 1)",
    "INSERT INTO matches (at, map, map_id, kind, won) VALUES (1795000000, 'Invented Ring',"
    " 572, 'arena', 0)",
    "INSERT INTO match_players (match_id, player_key, side) VALUES (1,"
    " 'Inventedc-Otherfakerealm', 'foes')",
    "PRAGMA user_version = 2",
)


def test_a_schema_2_database_main_built_is_copied_and_migrated_constructed(
    make: Any,
) -> None:
    env = make(_base())
    path = env.db()
    path.parent.mkdir(parents=True, mode=0o700)
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        for statement in _MAIN_SCHEMA_2:
            conn.execute(statement)
    finally:
        conn.close()
    rows = env.rows()  # the first open migrates
    assert rows[KEY_C].met == 7 and rows[KEY_C].rating == "bad" and rows[KEY_C].guid is None
    copy = path.with_name(path.name + ".before-schema-2")
    conn = sqlite3.connect(copy)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        assert conn.execute("SELECT player_key, met FROM players").fetchall() == [(KEY_C, 7)]
        assert "guid" not in {r[1] for r in conn.execute("PRAGMA table_info(players)")}
    finally:
        conn.close()
    conn = sqlite3.connect(path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == crewdb.SCHEMA == 6
        names = {r[1] for r in conn.execute("PRAGMA table_info(players)")}
        assert {"guid", "race", "faction", "spec", "honor_level", "score", "score_at"} <= names
        assert conn.execute("SELECT shuffle FROM matches").fetchall() == [(0,)]
    finally:
        conn.close()
    assert [(m.kind, m.won, m.foes) for m in env.matches()] == [("arena", False, [KEY_C])]
    out = env.sync()
    assert out.state == "synced", out.words
    assert env.rows()[KEY_C].guid == GUID_C and env.rows()[KEY_A].guid == GUID_A


# ─── one player, two names ───────────────────────────────────────────────────


def _two_names() -> bytes:
    """A and B are one character (the same GUID): renamed, met again."""
    return _book(
        {
            KEY_A: player(**_seen(), note="invented note about them"),
            KEY_B: player(guid=GUID_A, met=1, last=T1 + 60),
            KEY_C: player(guid=GUID_C),
        },
        [match([KEY_A], [KEY_C]), match([KEY_B], [KEY_C], at=T2 + 60)],
    )


def test_two_names_with_one_guid_are_shown_together_constructed(make: Any) -> None:
    env = make(_two_names())
    listed = env.json("crew", "list")
    seen = {p["key"]: p["also_known_as"] for p in listed["players"]}
    assert seen == {KEY_A: [KEY_B], KEY_B: [KEY_A], KEY_C: []}
    text = env.run("crew", "list")
    assert text.exit_code == 0, text.output
    line_a = next(x for x in text.stdout.splitlines() if x.startswith(f"  {KEY_A}:"))
    assert line_a.endswith(f"also seen as {KEY_B}")
    shown = env.json("crew", "show", KEY_B)
    also = [x for x in shown["lines"] if x.startswith("  Also seen as:")]
    assert also == [
        f"  Also seen as: {KEY_A} (the same character by the game's id: renamed or moved realm)"
    ]
    assert shown["player"]["also_known_as"] == [KEY_A] and shown["player"]["guid"] == GUID_A
    plain = env.run("crew", "show", KEY_B)
    assert plain.stdout.splitlines()[: len(shown["lines"])] == shown["lines"]


def test_show_text_and_json_say_the_same_with_the_new_lines_constructed(make: Any) -> None:
    env = make(_base())
    shown = env.json("crew", "show", KEY_A)
    plain = env.run("crew", "show", KEY_A)
    assert plain.stdout.splitlines()[: len(shown["lines"])] == shown["lines"]
    lines = shown["lines"]
    assert "  Race: Gnome" in lines and "  Faction: Alliance" in lines
    assert "  Specialization in their latest match: Frost" in lines
    assert "  Honor level: 12" in lines
    assert f"  The game's id for the character: {GUID_A}" in lines
    score = [x for x in lines if x.startswith("  Their scoreboard line")]
    assert len(score) == 1 and "damageDone 1000" in score[0] and "talentSpec Frost" in score[0]
    assert shown["player"]["score"]["values"]["damageDone"] == 1000
    listed = env.run("crew", "list")
    assert any(x.startswith(f"  {KEY_A}: MAGE 60 Frost;") for x in listed.stdout.splitlines())


# ─── forget: the name given, everywhere ──────────────────────────────────────


def _holders(env: Env, text: str) -> list[str]:
    """The files under the crew folder, and the saved file and its backup,
    whose bytes hold `text`."""
    files = [*_every_file(crewdb.crew_root()), env.file]
    backup = env.file.with_name(env.file.name + ".bak")
    if backup.exists():
        files.append(backup)
    return [f.name for f in files if text.encode() in f.read_bytes()]


def test_forget_of_one_name_forgets_that_name_only_and_says_so_constructed(
    make: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fix round 1 ruling: the GUID link is for showing only. Forgetting B
    removes B's entry (and the GUID on it) everywhere; A, the same
    character, is a separate entry and keeps its own fields, GUID included;
    the plan and the result say so in one sentence."""
    env = make(_two_names())
    env.file.with_name(env.file.name + ".bak").write_bytes(account_file(_two_names()))
    env.sync()
    # A daily copy from another day holds both names too.
    monkeypatch.setattr(crewdb, "_today", lambda: "2026-11-01")
    env.save(_two_names().replace(b'["met"] = 1,', b'["met"] = 2,'))
    assert env.sync().state == "synced"
    assert crewdb.copies(FLAVOR, ACCOUNT)

    sentence = (
        f"wowlab also keeps the same character, by the game's id, as {KEY_A}; forgetting "
        "those names too removes the character."
    )
    plan = env.run("crew", "forget", KEY_B, "--json", input="")
    assert plan.exit_code != 0
    report = json.loads(plan.stdout)
    assert report["players"] == [KEY_B] and report["written"] is False
    assert report["also_known_as"] == [KEY_A]
    assert sentence in plan.stderr

    result = env.run("crew", "forget", KEY_B, "--yes")
    assert result.exit_code == 0, result.output
    assert f"Forgot {KEY_B}:" in result.stdout and sentence in result.stdout
    assert _holders(env, KEY_B) == []
    assert set(env.rows()) == {KEY_A, KEY_C}
    assert env.rows()[KEY_A].guid == GUID_A and env.rows()[KEY_A].also_known_as == []
    kept = {p.key: p.guid for p in crewbook.parse_book(env.data()).players}
    assert kept.get(KEY_A) == GUID_A and KEY_B not in kept


def test_forget_leaves_no_guid_of_the_entry_in_any_file_constructed(make: Any) -> None:
    env = make(_base())
    env.sync()
    result = env.run("crew", "forget", KEY_C, "--yes")
    assert result.exit_code == 0, result.output
    assert "same character" not in result.stdout
    for text in (KEY_C, GUID_C):
        assert _holders(env, text) == [], text
    assert env.rows()[KEY_A].guid == GUID_A


def test_forget_of_a_name_only_the_database_keeps_says_the_other_name_constructed(
    make: Any,
) -> None:
    env = make(_two_names())
    env.sync()
    # The game's list drops both names; the database keeps them.
    env.save(_book({KEY_C: player(guid=GUID_C)}, []))
    env.sync()
    result = env.run("crew", "forget", KEY_A, "--yes")
    assert result.exit_code == 0, result.output
    assert f"as {KEY_B}; forgetting those names too" in result.stdout
    assert _holders(env, KEY_A) == []
    assert KEY_B in env.rows() and env.rows()[KEY_B].guid == GUID_A


def test_an_unread_entry_holding_the_same_guid_is_another_entry_constructed(
    make: Any,
) -> None:
    """D's entry cannot be read and holds A's GUID: forgetting A forgets A,
    and D's entry is left as it is (no refusal, no link from it)."""
    env = make(_book({KEY_A: player(**_seen()), KEY_D: player(level="x", guid=GUID_A)}, []))
    result = env.run("crew", "forget", KEY_A, "--yes")
    assert result.exit_code == 0, result.output
    assert KEY_A.encode() not in env.data() and KEY_D.encode() in env.data()


def test_a_forgotten_name_in_another_players_scoreboard_line_is_removed_constructed(
    make: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Security finding 2: C's kept line holds A's name in a text field. A
    forget of A drops that line (not C's row) from the database and every
    copy; the saved file no longer holds it (its own refusal covers it)."""
    held = _line(note=KEY_A)
    env = make(_book({KEY_A: player(), KEY_C: player(guid=GUID_C, score=held)}, []))
    env.sync()
    monkeypatch.setattr(crewdb, "_today", lambda: "2026-11-01")
    # The game's file moves on: C's entry no longer carries the line.
    env.save(_book({KEY_A: player(met=4), KEY_C: player(guid=GUID_C)}, []))
    assert env.sync().state == "synced"
    assert crewdb.copies(FLAVOR, ACCOUNT)
    db_files = [f for f in _holders(env, KEY_A) if ".sqlite" in f]
    assert len(db_files) >= 2  # the database and its daily copy
    result = env.run("crew", "forget", KEY_A, "--yes")
    assert result.exit_code == 0, result.output
    assert _holders(env, KEY_A) == []
    c = env.rows()[KEY_C]
    assert c.score is None and c.guid == GUID_C


def test_a_guid_not_in_the_game_shape_is_left_out_and_never_links_constructed(
    make: Any,
) -> None:
    """Fix round 2 ruling: an unshaped GUID is dropped, the rest of the
    player synced, and the sync says how many without quoting it."""
    odd = "Player-1-ABC"
    env = make(_book({KEY_A: player(guid=odd), KEY_B: player(guid=odd), KEY_C: player()}, []))
    out = env.sync()
    assert out.state == "synced" and out.left_out == 0 and out.ids_left_out == 2, out.words
    assert out.words.endswith(
        "The game's ids of 2 players were not in the game's shape and were not kept; the rest "
        "was synced."
    )
    assert odd not in out.words
    rows = env.rows()
    assert set(rows) == {KEY_A, KEY_B, KEY_C}
    assert rows[KEY_A].guid is None and rows[KEY_A].met == 3
    assert all(r.also_known_as == [] for r in rows.values())
    listed = env.json("crew", "list")
    assert listed["unread"] == [] and odd not in json.dumps(listed)
    for f in _every_file(crewdb.crew_root()):
        assert odd.encode() not in f.read_bytes(), f.name
    assert crewbook.guid_shaped(GUID_A) and not crewbook.guid_shaped(odd)


# ─── shared output still counts only ─────────────────────────────────────────


@pytest.mark.parametrize(
    "args",
    [(), ("--json",), ("--redact",), ("--redact", "--json")],
    ids=["text_constructed", "json_constructed", "redact_constructed", "redact_json_constructed"],
)
def test_doctor_prints_counts_and_no_record_constructed(make: Any, args: tuple[str, ...]) -> None:
    env = make(_two_names())
    env.sync()
    if "--redact" not in args:
        assert env.run("snap", "create", "-m", "first").exit_code == 0
    result = runner.invoke(
        cli.app, ["doctor", *args, "--root", str(env.root)], terminal_width=10_000
    )
    assert result.exit_code in (0, 1), result.output
    out = result.output
    for text in (KEY_A, KEY_B, KEY_C, GUID_A, GUID_C, "Gnome", "Alliance", "damageDone", "Frost"):
        assert text not in out, text


def test_the_reader_reads_m20_11_fields_into_the_player() -> None:
    read = crewbook.parse_book(account_file(_base()))
    a = next(p for p in read.players if p.key == KEY_A)
    assert a.guid == GUID_A and a.score is not None and a.score.at == T2
    assert read.unread == [] and read.matches_unread == []
    assert read.matches[0].map_id == MAP_ID
