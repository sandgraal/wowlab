"""Matches at the desk (M20-09, docs/LAB_PLAN.md §21.3): the sync copies a
schema 2 book's `matches` into wowlab's crew database once each and adds
up the four match counters by rule 2 within the stated bound; `crew
matches`, the match filters and sorts of `crew list`, the record in `crew
show`; and `crew forget` clearing a player from every match the database
and its copies keep.

CONSTRUCTED INPUT, every byte: there is no real schema 2 saved file yet,
and ADR-0036 term 3 keeps every real crew record out of the repository.
Each book is built at run time by `parser/_crew_build.py` inside a copy of
the captured install `fixtures/macos-70888/` in `tmp_path`, with the four
invented keys only; every database is built at run time under a user data
directory redirected into `tmp_path`. Nothing here reads a real install or
the real data directory, and no test writes to an install except through
the write gate, on the copy, with a fake process table.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import psutil
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, crewbook, crewdb, guard, install, snapshot

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
    book,
    match,
    player,
)
from _crew_build import (
    install as build_install,
)
from _user_dirs import redirect_user_dirs

runner = CliRunner()
LIVE = f"WTF/Account/{ACCOUNT}/SavedVariables/WowLab.lua"
DAY = 86_400


class _Proc:
    """A process that is not the game, for the write gate's client check."""

    def __init__(self, exe: str) -> None:
        self._exe = exe

    @property
    def pid(self) -> int:
        return 101

    def name(self) -> str:
        return "python3"

    def exe(self) -> str:
        return self._exe

    def cmdline(self) -> list[str]:
        return [self._exe]

    def status(self) -> str:
        return "running"


class Env:
    """One constructed install and the crew database of its one book."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.file = root / FLAVOR / LIVE

    def save(self, crew: bytes) -> None:
        """The saved file as a later game session leaves it."""
        self.file.write_bytes(account_file(crew))

    def data(self) -> bytes:
        return self.file.read_bytes()

    def book(self) -> crewbook.CrewBook:
        return crewbook.parse_book(self.data())

    def target(self) -> crewdb.Target:
        return crewdb.Target(
            install_root=os.path.realpath(self.root),
            flavor_folder=FLAVOR,
            flavor_path=str(self.root / FLAVOR),
            account=ACCOUNT,
            file=LIVE,
        )

    def sync(self, **kw: Any) -> crewdb.SyncOutcome:
        kw.setdefault("reread", self.data)
        return crewdb.sync(self.target(), self.data(), self.book(), **kw)

    def rows(self) -> dict[str, crewdb.CrewRow]:
        return {r.key: r for r in crewdb.read(FLAVOR, ACCOUNT)}

    def matches(self, key: str | None = None) -> list[crewdb.CrewMatchRow]:
        return crewdb.matches(FLAVOR, ACCOUNT, key)

    def db(self) -> Path:
        return crewdb.database_path(FLAVOR, ACCOUNT)

    def gate(self, label: str, data: bytes) -> None:
        """A write through the write gate, as `snap restore` makes one."""
        (flavor,) = install.discover(self.root).flavors
        with guard.transaction(flavor, label=label) as tx:
            tx.write(LIVE, data)

    def run(self, *args: str, input: str | None = None) -> Any:
        extra = ["--root", str(self.root), "--flavor", FLAVOR]
        return runner.invoke(cli.app, [*args, *extra], input=input, terminal_width=10_000)

    def json(self, *args: str) -> dict[str, Any]:
        result = self.run(*args, "--json")
        assert result.exit_code == 0, result.output
        value = json.loads(result.stdout)
        assert isinstance(value, dict)
        return value


@pytest.fixture
def make(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Callable[[bytes | None], Env]]:
    monkeypatch.delenv("WOWLAB_WOW_ROOT", raising=False)
    monkeypatch.delenv("WOWLAB_FLAVOR", raising=False)
    home = tmp_path / "home"
    home.mkdir()
    redirect_user_dirs(monkeypatch, home)
    procs = [_Proc(str(tmp_path / "elsewhere" / "python3"))]
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: iter(procs))
    made: list[Env] = []

    def build(crew: bytes | None) -> Env:
        folder = tmp_path / f"i{len(made)}"
        folder.mkdir()
        env = Env(build_install(folder, crew))
        made.append(env)
        return env

    yield build
    for env in made:
        assert not list(env.root.rglob("*.sqlite*")), "a database inside the install (L1)"


def _book(players: dict[str, Any], games: list[dict[str, Any]], **more: Any) -> bytes:
    """A schema 2 book holding `players` and the matches `games`."""
    return book(players, schema=2, matches=games, **more)


def _four(games: list[dict[str, Any]] | None = None, **counts: Any) -> bytes:
    """The four invented players, KEY_A with the match counters `counts`,
    and one match: KEY_A and KEY_B with you, KEY_C and KEY_D against."""
    if games is None:
        games = [match([KEY_A, KEY_B], [KEY_C, KEY_D])]
    return _book(
        {
            KEY_A: player(**counts),
            KEY_B: player(),
            KEY_C: player(),
            KEY_D: player(),
        },
        games,
    )


def _match_rows(path: Path) -> list[tuple[Any, ...]]:
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT at, map, map_id, kind, won FROM matches").fetchall()
    finally:
        conn.close()


# ─── the sync: matches once each (rule 3) ────────────────────────────────────


def test_a_sync_copies_each_match_once_constructed(make: Any) -> None:
    env = make(_four())
    first = env.sync()
    assert first.state == "synced" and first.plan is not None
    assert first.plan.matches_added == 1 and "1 match added" in first.words
    (m,) = env.matches()
    assert (m.at, m.map, m.map_id, m.kind, m.won) == (T2, MAP, MAP_ID, "bg", True)
    assert (m.team, m.foes) == ([KEY_A, KEY_B], [KEY_C, KEY_D])
    second = env.sync()
    assert second.state == "unchanged"
    assert len(_match_rows(env.db())) == 1


def test_a_match_the_game_dropped_and_named_again_is_not_added_twice_constructed(
    make: Any,
) -> None:
    env = make(_four())
    env.sync()
    later = match([KEY_A], [KEY_C], at=T2 + 600)
    # The game's list dropped the first match ...
    env.save(_four([later]))
    assert env.sync().plan.matches_added == 1  # type: ignore[union-attr]
    # ... and a later save names it again, beside the other.
    env.save(_four([match([KEY_A, KEY_B], [KEY_C, KEY_D]), later]))
    out = env.sync()
    assert out.state == "synced" and out.plan is not None and out.plan.matches_added == 0
    assert len(_match_rows(env.db())) == 2
    assert [m.at for m in env.matches()] == [T2 + 600, T2]  # newest first


def test_a_match_a_book_lists_twice_is_added_once_constructed(make: Any) -> None:
    twice = match([KEY_A], [KEY_C])
    env = make(_four([twice, twice]))
    out = env.sync()
    assert out.plan is not None and out.plan.matches_added == 1
    assert len(_match_rows(env.db())) == 1


def test_a_schema_1_book_adds_no_match_constructed(make: Any) -> None:
    env = make(book({KEY_A: player(), KEY_B: player()}, schema=1))
    out = env.sync()
    assert out.state == "synced" and out.plan is not None and out.plan.matches_added == 0
    assert env.matches() == []


# ─── the sync: the four counters (rule 2, the stated bound) ─────────────────


def test_the_four_counters_add_up_across_syncs_constructed(make: Any) -> None:
    env = make(_four(allied=2, faced=1, allied_won=1, faced_won=0))
    env.sync()
    a = env.rows()[KEY_A]
    assert (a.allied, a.faced, a.allied_won, a.faced_won) == (2, 1, 1, 0)
    env.save(_four(allied=5, faced=1, allied_won=3, faced_won=1))
    env.sync()
    a = env.rows()[KEY_A]
    assert (a.allied, a.faced, a.allied_won, a.faced_won) == (5, 1, 3, 1)


def test_a_player_dropped_and_met_again_keeps_the_earlier_totals_constructed(
    make: Any,
) -> None:
    env = make(_four(allied=4, faced=2, allied_won=2, faced_won=1))
    env.sync()
    # The game's list dropped KEY_A ...
    env.save(_book({KEY_B: player()}, [match([KEY_B], [])]))
    env.sync()
    assert env.rows()[KEY_A].allied == 4 and not env.rows()[KEY_A].in_game_list
    # ... and met them again: the new counts are added whole.
    env.save(_four(allied=1, faced=1, allied_won=1, faced_won=0))
    env.sync()
    a = env.rows()[KEY_A]
    assert (a.allied, a.faced, a.allied_won, a.faced_won) == (5, 3, 3, 1)


def test_a_lower_count_in_the_saved_file_adds_the_whole_new_value_constructed(
    make: Any,
) -> None:
    env = make(_four(allied=6, faced=0, allied_won=0, faced_won=0))
    env.sync()
    env.save(_four(allied=2, faced=0, allied_won=0, faced_won=0))
    env.sync()
    assert env.rows()[KEY_A].allied == 8


def test_the_latest_match_comes_from_the_saved_file_when_not_older_constructed(
    make: Any,
) -> None:
    env = make(_four(allied=1, pvp_last=T2, pvp_map=MAP, pvp_side="with"))
    env.sync()
    a = env.rows()[KEY_A]
    assert (a.pvp_last, a.pvp_map, a.pvp_side) == (T2, MAP, "with")
    env.save(_four(allied=1, pvp_last=T1, pvp_map="Invented Older Valley", pvp_side="against"))
    env.sync()
    assert env.rows()[KEY_A].pvp_map == MAP


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("allied", crewdb.COUNTER_MAX + 1),
        ("faced", crewdb.COUNTER_MAX + 1),
        ("allied_won", crewdb.COUNTER_MAX + 1),
        ("faced_won", crewdb.COUNTER_MAX + 1),
        ("pvp_last", T0 - 30 * 365 * DAY),
        ("pvp_map", "x" * (crewdb.TEXT_MAX_BYTES + 1)),
    ],
)
def test_a_counter_or_match_field_out_of_bounds_leaves_out_that_entry_only_constructed(
    make: Any, field: str, value: Any
) -> None:
    env = make(_four(**{field: value}))
    out = env.sync()
    assert out.state == "synced" and out.left_out == 1
    assert "could not be read" in out.words and str(value) not in out.words
    rows = env.rows()
    assert KEY_A not in rows and {KEY_B, KEY_C, KEY_D} <= set(rows)
    # The match still names KEY_A: the match is the book's, not the entry's.
    assert len(env.matches()) == 1
    listed = env.json("crew", "list")
    assert any(u["key"] == KEY_A for u in listed["unread"])


def test_a_counter_at_the_bound_is_taken_constructed(make: Any) -> None:
    env = make(_four(allied=crewdb.COUNTER_MAX))
    assert env.sync().left_out == 0
    assert env.rows()[KEY_A].allied == crewdb.COUNTER_MAX


# ─── the sync: what it will not take ─────────────────────────────────────────


@pytest.mark.parametrize(
    "bad",
    [
        pytest.param({"at": T0 - 30 * 365 * DAY}, id="time-before-2004"),
        pytest.param({"map_id": None}, id="no-map-id"),
        pytest.param({"map": "y" * (crewdb.TEXT_MAX_BYTES + 1)}, id="map-too-long"),
        pytest.param({"foes": [KEY_C, KEY_C]}, id="a-key-twice"),
        pytest.param({"foes": [KEY_A]}, id="a-key-on-both-sides"),
        pytest.param({"foes": ["Inventedc"]}, id="a-key-without-a-realm"),
    ],
)
def test_a_match_out_of_bounds_is_left_out_and_counted_constructed(
    make: Any, bad: dict[str, Any]
) -> None:
    broken = match([KEY_A], [KEY_C], at=T2 + 60)
    for name, value in bad.items():
        if value is None:
            broken.pop(name)
        else:
            broken[name] = value
    env = make(_four([match([KEY_A, KEY_B], [KEY_C, KEY_D]), broken]))
    out = env.sync()
    assert out.state == "synced" and out.matches_left_out == 1
    assert "1 match of the book could not be read and was not synced." in out.words
    assert [m.at for m in env.matches()] == [T2]


def test_a_match_the_reader_could_not_read_is_counted_too_constructed(make: Any) -> None:
    env = make(_four([match([KEY_A], [KEY_C]), {"at": T2 + 60, "kind": "duel"}]))
    out = env.sync()
    assert out.matches_left_out == 1 and len(env.matches()) == 1


def test_more_than_500_matches_syncs_nothing_constructed(make: Any) -> None:
    games = [match([KEY_A], [KEY_C], at=T2 - i) for i in range(crewdb.MAX_MATCHES + 1)]
    env = make(_four(games))
    out = env.sync()
    assert out.state == "refused" and "number of matches" in out.words
    assert not env.db().exists() or env.rows() == {}


def test_a_held_sync_copies_no_match_and_a_sync_by_hand_never_lowers_a_count_constructed(
    make: Any,
) -> None:
    env = make(_four(allied=5, faced=3, allied_won=2, faced_won=1))
    env.sync()
    before = env.db().read_bytes()
    # wowlab puts an older book back (as `snap restore` does): lower counts
    # and a match the database has not seen.
    older = _four(
        [match([KEY_A], [KEY_C], at=T2 - 3600)], allied=1, faced=1, allied_won=0, faced_won=0
    )
    env.gate("snap restore", account_file(older))
    held = env.sync()
    assert held.state == "held" and "put_back" in held.held
    assert held.plan is not None and held.plan.matches_added == 1
    assert env.db().read_bytes() == before  # nothing copied
    assert len(env.matches()) == 1
    done = env.sync(by_hand=True, confirm=lambda plan, why: True)
    assert done.state == "synced"
    a = env.rows()[KEY_A]
    assert (a.allied, a.faced, a.allied_won, a.faced_won) == (5, 3, 2, 1)
    assert len(env.matches()) == 2


def test_a_database_built_before_matches_were_synced_takes_them_constructed(
    make: Any,
) -> None:
    """A database made from a schema 1 book (players only, as main built it
    before this ticket) takes the matches of the schema 2 book that follows."""
    env = make(book({KEY_A: player(), KEY_C: player()}, schema=1))
    env.sync()
    env.save(_book({KEY_A: player(allied=1), KEY_C: player(faced=1)}, [match([KEY_A], [KEY_C])]))
    out = env.sync()
    assert out.state == "synced" and out.plan is not None and out.plan.matches_added == 1
    assert env.rows()[KEY_A].allied == 1 and env.rows()[KEY_C].faced == 1


# ─── crew forget reaches every match ─────────────────────────────────────────


def _every_file(folder: Path) -> list[Path]:
    return [f for f in folder.rglob("*") if f.is_file()]


def test_forget_clears_a_player_from_every_match_and_every_copy_constructed(
    make: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = make(_four())
    env.sync()
    # A second sync on another day keeps a daily copy that holds KEY_C.
    monkeypatch.setattr(crewdb, "_today", lambda: "2026-11-01")
    env.save(_four([match([KEY_A, KEY_B], [KEY_C, KEY_D]), match([KEY_B], [KEY_C], at=T2 + 60)]))
    env.sync()
    assert crewdb.copies(FLAVOR, ACCOUNT)
    crew = crewdb.crew_root()
    assert any(KEY_C.encode() in f.read_bytes() for f in _every_file(crew))

    result = env.run("crew", "forget", KEY_C, "--yes")
    assert result.exit_code == 0, result.output

    assert all(KEY_C not in (*m.team, *m.foes) for m in env.matches())
    assert len(env.matches()) == 2  # the matches stay, without the player
    for f in _every_file(crew):
        assert KEY_C.encode() not in f.read_bytes(), f.name
        assert not f.name.endswith("-journal"), f.name
    for args in (("crew", "matches"), ("crew", "list"), ("crew", "matches", KEY_A)):
        text = env.run(*args)
        assert text.exit_code == 0, text.output
        assert KEY_C not in text.stdout
        assert KEY_C not in json.dumps(env.json(*args))
    gone = env.run("crew", "matches", KEY_C)
    assert gone.exit_code != 0 and "no player" in gone.output
    doctor = env.run("doctor", "--json")
    assert KEY_C not in doctor.stdout


def test_forget_reaches_a_key_only_a_match_holds_constructed(make: Any) -> None:
    # KEY_D is in a match the database keeps; the game's list has no entry
    # for them and the database no player row.
    env = make(_book({KEY_A: player(), KEY_C: player()}, [match([KEY_A], [KEY_C, KEY_D])]))
    env.sync()
    assert KEY_D not in env.rows() and KEY_D in crewdb.holds(FLAVOR, ACCOUNT)
    result = env.run("crew", "forget", KEY_D, "--yes")
    assert result.exit_code == 0, result.output
    assert all(KEY_D not in m.foes for m in env.matches())
    for f in _every_file(crewdb.crew_root()):
        assert KEY_D.encode() not in f.read_bytes()


def test_a_forgotten_player_is_not_added_back_by_the_next_sync_constructed(make: Any) -> None:
    env = make(_four())
    env.sync()
    assert env.run("crew", "forget", KEY_B, "--yes").exit_code == 0
    env.sync()
    assert all(KEY_B not in m.team for m in env.matches())
    assert KEY_B not in env.rows()


def test_forget_all_leaves_no_match_constructed(make: Any) -> None:
    env = make(_four())
    env.sync()
    assert env.run("crew", "forget", "--all", "--yes").exit_code == 0
    assert env.json("crew", "matches")["matches"] == []
    for f in _every_file(crewdb.crew_root()):
        for key in (KEY_A, KEY_B, KEY_C, KEY_D):
            assert key.encode() not in f.read_bytes()


# ─── crew matches ────────────────────────────────────────────────────────────


def _two_matches() -> bytes:
    return _four(
        [
            match([KEY_A, KEY_B], [KEY_C, KEY_D], won=True),
            match([KEY_A], [KEY_B], at=T2 + 3600, won=None, kind="arena", map="Invented Ring"),
        ]
    )


def test_crew_matches_text_and_json_say_the_same_constructed(make: Any) -> None:
    env = make(_two_matches())
    result = env.run("crew", "matches")
    assert result.exit_code == 0, result.output
    report = cli.CrewMatchesReport.model_validate(env.json("crew", "matches"))
    assert report.total == 2 and report.player is None
    lines = result.stdout.splitlines()
    assert lines[0] == report.words
    assert lines[1 : 1 + len(report.lines)] == report.lines
    assert report.lines[0].endswith("Invented Ring (arena): no winner recorded")
    assert report.lines[1] == f"    With you: {KEY_A}"
    assert report.lines[2] == f"    Against you: {KEY_B}"
    assert report.lines[3].endswith(f"{MAP} (battleground): your side won")
    assert [m.kind for m in report.matches] == ["arena", "bg"]
    assert report.matches[1].team == [KEY_A, KEY_B] and report.matches[1].foes == [KEY_C, KEY_D]


def test_crew_matches_of_one_player_lists_only_theirs_constructed(make: Any) -> None:
    env = make(_two_matches())
    report = env.json("crew", "matches", "inventedc-otherfakerealm")
    assert report["player"] == KEY_C and report["total"] == 1
    assert report["matches"][0]["foes"] == [KEY_C, KEY_D]
    assert f"with {KEY_C} in them" in report["words"]
    text = env.run("crew", "matches", KEY_C)
    assert text.exit_code == 0 and text.stdout.splitlines()[0] == report["words"]


def test_crew_matches_with_the_book_never_turned_on_says_so_constructed(make: Any) -> None:
    env = make(None)
    result = env.run("crew", "matches")
    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == [
        f"The crew book is not turned on for game account {ACCOUNT} in {FLAVOR} (as of your "
        "last logout or /reload). Turn it on in game with /wowlab crew on."
    ]
    assert env.json("crew", "matches")["matches"] == []


HOSTILE_MAP = "Invented\x1b[31m Map" + chr(0x202E) + "<b>x</b>\x07"


def test_a_hostile_map_name_is_shown_as_text_constructed(make: Any) -> None:
    env = make(
        _four(
            [match([KEY_A], [KEY_C], map=HOSTILE_MAP)],
            allied=1,
            pvp_last=T2,
            pvp_map=HOSTILE_MAP,
            pvp_side="with",
        )
    )
    for args in (("crew", "matches"), ("crew", "show", KEY_A), ("crew", "list")):
        result = env.run(*args)
        assert result.exit_code == 0, result.output
        out = result.stdout
        for raw in ("\x1b", chr(0x202E), "\x07"):
            assert raw not in out, (args, repr(raw))
    shown = env.run("crew", "matches").stdout
    assert "<b>x</b>" in shown  # text, as it is: a terminal draws no markup
    raw_json = env.run("crew", "matches", "--json").stdout
    assert "\x1b" not in raw_json and chr(0x202E) not in raw_json
    assert json.loads(raw_json)["matches"][0]["map"] == HOSTILE_MAP


# ─── crew list: the filters and the sorts ────────────────────────────────────


def _record_book() -> bytes:
    return _book(
        {
            KEY_A: player(allied=3, faced=0, last=T1),
            KEY_B: player(allied=1, faced=5, last=T0),
            KEY_C: player(faced=2, last=T0, pvp_last=T2, pvp_map=MAP, pvp_side="against"),
            KEY_D: player(last=T0),
        },
        [
            match([KEY_A], [KEY_C], map="Invented 100%_Gulch"),
            match([KEY_B], [KEY_C], at=T2 + 60, map="Invented Plain"),
        ],
    )


def _keys(report: dict[str, Any]) -> list[str]:
    return [p["key"] for p in report["players"]]


def test_list_faced_and_allied_constructed(make: Any) -> None:
    env = make(_record_book())
    assert set(_keys(env.json("crew", "list", "--faced"))) == {KEY_B, KEY_C}
    assert set(_keys(env.json("crew", "list", "--allied"))) == {KEY_A, KEY_B}
    assert _keys(env.json("crew", "list", "--faced", "--allied")) == [KEY_B]


def test_list_sorts_by_faced_and_allied_constructed(make: Any) -> None:
    env = make(_record_book())
    assert _keys(env.json("crew", "list", "--sort", "faced")) == [KEY_B, KEY_C, KEY_A, KEY_D]
    assert _keys(env.json("crew", "list", "--sort", "allied")) == [KEY_A, KEY_B, KEY_C, KEY_D]


def test_list_map_is_a_like_with_its_wildcards_escaped_constructed(make: Any) -> None:
    env = make(_record_book())
    assert set(_keys(env.json("crew", "list", "--map", "gulch"))) == {KEY_A, KEY_C}
    assert set(_keys(env.json("crew", "list", "--map", "100%_g"))) == {KEY_A, KEY_C}
    # `%` and `_` are text, not wildcards: only the map that holds them.
    assert set(_keys(env.json("crew", "list", "--map", "%"))) == {KEY_A, KEY_C}
    assert set(_keys(env.json("crew", "list", "--map", "_"))) == {KEY_A, KEY_C}
    assert _keys(env.json("crew", "list", "--map", "Invented_Plain")) == []
    assert _keys(env.json("crew", "list", "--map", "no such map")) == []


def test_list_since_takes_the_later_of_group_and_match_constructed(make: Any) -> None:
    env = make(_record_book())
    day = time.strftime("%Y-%m-%d", time.localtime(T1))
    got = set(_keys(env.json("crew", "list", "--since", day)))
    # KEY_A grouped at T1; KEY_C's latest match is at T2, after T1.
    assert got == {KEY_A, KEY_C}
    bad = env.run("crew", "list", "--since", "yesterday")
    assert bad.exit_code == 2 and "YYYY-MM-DD" in bad.output


def test_list_filters_in_text_and_json_say_the_same_constructed(make: Any) -> None:
    env = make(_record_book())
    report = env.json("crew", "list", "--faced", "--sort", "faced")
    text = env.run("crew", "list", "--faced", "--sort", "faced").stdout.splitlines()
    rows = [line for line in text if line.startswith("  Invented")]
    assert [r.split(":")[0].strip() for r in rows] == _keys(report)
    assert f"{len(report['players'])} of 4 players match." in text
    b = next(r for r in rows if r.lstrip().startswith(KEY_B))
    assert "in matches with you 1, against you 5" in b
    assert report["faced"] is True and report["sort"] == "faced"


# ─── crew show: the record with and against ──────────────────────────────────


def test_show_has_the_record_with_and_against_constructed(make: Any) -> None:
    env = make(_four(allied=4, faced=2, allied_won=3, faced_won=1, pvp_last=T2, pvp_map=MAP))
    report = env.json("crew", "show", KEY_A)
    lines = report["lines"]
    assert "  Matches with you: 4 (your side won 3)" in lines
    assert "  Matches against you: 2 (your side won 1)" in lines
    assert any(line.startswith("  Latest match: ") and MAP in line for line in lines)
    player_ = report["player"]
    assert (player_["allied"], player_["faced"], player_["allied_won"], player_["faced_won"]) == (
        4,
        2,
        3,
        1,
    )
    text = env.run("crew", "show", KEY_A).stdout.splitlines()
    assert text[: len(lines)] == lines


# ─── the SQL ─────────────────────────────────────────────────────────────────


def test_like_escapes_its_wildcards() -> None:
    assert crewdb._like("a%b_c\\d") == "%a\\%b\\_c\\\\d%"


def test_nothing_is_written_under_the_data_directory_but_the_crew_folder_constructed(
    make: Any,
) -> None:
    env = make(_four())
    env.run("crew", "matches")
    data = snapshot.user_data_dir()
    written = {f.relative_to(data).parts[0] for f in _every_file(data)}
    assert written <= {"crew"}, written
