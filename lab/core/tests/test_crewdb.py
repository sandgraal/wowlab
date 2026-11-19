"""`wowlab_core.crewdb` and `wowlab crew sync` (M20-01, docs/LAB_PLAN.md
§21.3, ADR-0039). CONSTRUCTED INPUT, every byte: each crew book is built at
run time by `parser/_crew_build.py` (ADR-0036 term 3: no real crew record
enters the repository) inside a copy of the captured install
`fixtures/macos-70888/` in `tmp_path`, with the four invented keys only;
every database is built at run time under a user data directory redirected
into `tmp_path`, and none is committed.

Nothing here reads a real install or the real data directory. Journal
records are made by the write gate itself, on the copy, with a fake process
table (no client running).
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import sqlite3
import stat
import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import psutil
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, crewbook, crewdb, crewedit, guard, install, snapshot

sys.path.insert(0, str(Path(__file__).resolve().parent / "parser"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _crew_build import (
    ACCOUNT,
    FLAVOR,
    KEY_A,
    KEY_B,
    KEY_C,
    KEY_D,
    NOTE,
    T0,
    T1,
    account_file,
    book,
    many_keys,
    player,
)
from _crew_build import (
    install as build_install,
)
from _user_dirs import redirect_user_dirs

runner = CliRunner()
LIVE = f"WTF/Account/{ACCOUNT}/SavedVariables/WowLab.lua"
SRC = Path(crewdb.__file__)
POSIX = os.name == "posix"


class _Proc:
    """The `psutil.Process` surface the write gate's client check reads: a
    process that is not the game."""

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
        self.flavor = root / FLAVOR
        self.file = self.flavor / LIVE

    # the saved file, as a game session would leave it
    def save(self, crew: bytes | None) -> None:
        self.file.write_bytes(account_file(crew))

    def data(self) -> bytes:
        return self.file.read_bytes()

    def book(self) -> crewbook.CrewBook:
        return crewbook.parse_book(self.data())

    def target(self, flavor_folder: str = FLAVOR) -> crewdb.Target:
        return crewdb.Target(
            install_root=os.path.realpath(self.root),
            flavor_folder=flavor_folder,
            flavor_path=str(self.root / flavor_folder),
            account=ACCOUNT,
            file=LIVE,
        )

    def sync(self, **kw: Any) -> crewdb.SyncOutcome:
        return crewdb.sync(self.target(), self.data(), self.book(), **kw)

    def rows(self) -> dict[str, crewdb.CrewRow]:
        return {r.key: r for r in crewdb.read(FLAVOR, ACCOUNT)}

    def db(self) -> Path:
        return crewdb.database_path(FLAVOR, ACCOUNT)

    def db_sha(self) -> str:
        return hashlib.sha256(self.db().read_bytes()).hexdigest()

    # the write gate, as `snap restore`, `sv merge` and the crew commands use it
    def gate(self, label: str, data: bytes) -> str:
        (flavor,) = install.discover(self.root).flavors
        with guard.transaction(flavor, label=label) as tx:
            tx.write(LIVE, data)
        return guard.history()[-1].id

    # the CLI
    def run(self, *args: str, input: str | None = None) -> Any:
        extra = ["--root", str(self.root), "--flavor", FLAVOR]
        return runner.invoke(cli.app, [*args, *extra], input=input, terminal_width=10_000)

    def run_bare(self, *args: str, input: str | None = None) -> Any:
        return runner.invoke(cli.app, list(args), input=input, terminal_width=10_000)


@pytest.fixture
def make(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[..., Env]]:
    monkeypatch.delenv("WOWLAB_WOW_ROOT", raising=False)
    monkeypatch.delenv("WOWLAB_FLAVOR", raising=False)
    home = tmp_path / "home"
    home.mkdir()
    redirect_user_dirs(monkeypatch, home)
    procs = [_Proc(str(tmp_path / "elsewhere" / "python3"))]
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: iter(procs))
    made: list[Env] = []

    def build(crew: bytes | None, name: str | None = None) -> Env:
        folder = tmp_path / (name or f"i{len(made)}")
        folder.mkdir()
        env = Env(build_install(folder, crew))
        made.append(env)
        return env

    yield build
    # Nothing a sync does reaches outside the user data directory.
    for env in made:
        assert not list(env.root.rglob("*.sqlite*"))


def _three(**a: Any) -> bytes:
    return book(
        {
            KEY_A: player(rating="bad", note=NOTE, met=a.get("met_a", 3), last=T1),
            KEY_B: player(rating="good", met=9, last=T0 + 10, where=None),
            KEY_C: player(met=1, last=T0),
        },
        current=[KEY_A],
    )


def _meta(path: Path) -> dict[str, Any]:
    conn = sqlite3.connect(path)
    try:
        return dict(conn.execute("SELECT name, value FROM meta").fetchall())
    finally:
        conn.close()


# ─── the file ────────────────────────────────────────────────────────────────


def test_the_first_sync_makes_the_database_of_section_21_3_constructed(make: Any) -> None:
    env = make(_three())
    assert not os.path.lexists(env.db())
    outcome = env.sync()
    assert outcome.state == "synced" and outcome.plan is not None
    assert outcome.plan.players_added == 3
    path = env.db()
    assert path == snapshot.user_data_dir() / "crew" / FLAVOR / f"{ACCOUNT}.sqlite"
    conn = sqlite3.connect(path)
    try:
        assert conn.execute("PRAGMA application_id").fetchone()[0] == crewdb.APPLICATION_ID
        assert conn.execute("PRAGMA user_version").fetchone()[0] == crewdb.SCHEMA == 1
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        objects = dict(conn.execute("SELECT name, sql FROM sqlite_master").fetchall())
    finally:
        conn.close()
    for table in ("players", "matches", "match_players", "meta"):
        assert "STRICT" in objects[table], table
    for index in (
        "players_last",
        "players_rating",
        "players_faced",
        "players_allied",
        "matches_at_map",
        "match_players_match",
        "match_players_key",
    ):
        assert index in objects, index
    assert "UNIQUE" in objects["matches_at_map"]
    meta = _meta(path)
    assert meta["account"] == ACCOUNT and meta["flavor_folder"] == FLAVOR
    assert meta["install_root"] == os.path.realpath(env.root)
    assert meta["book_started"] == T0 and meta["schema"] == 1
    assert meta["book_sha256"] == hashlib.sha256(env.data()).hexdigest()


def test_the_application_id_in_the_statement_is_the_constant() -> None:
    assert f"PRAGMA application_id = {crewdb.APPLICATION_ID}" == crewdb._SQL_SET_APP_ID
    assert 0 < crewdb.APPLICATION_ID < 2**31
    assert crewdb.NOTE_MAX_BYTES == crewedit.NOTE_MAX_BYTES


def test_secure_delete_is_on_and_there_is_no_write_ahead_log_constructed(make: Any) -> None:
    env = make(_three())
    env.sync()
    conn = crewdb._open(env.db(), create=False)
    assert conn is not None
    try:
        assert conn.execute("PRAGMA secure_delete").fetchone()[0] == 1
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    finally:
        conn.close()
    names = {p.name for p in env.db().parent.iterdir()}
    assert not any(n.endswith(("-wal", "-shm", "-journal")) for n in names), names


@pytest.mark.skipif(not POSIX, reason="file modes are a POSIX matter")
def test_file_modes_under_a_loose_umask_constructed(make: Any) -> None:
    env = make(_three())
    old = os.umask(0)
    try:
        env.sync()
        env.save(_three(met_a=5))
        env.sync()  # the second sync keeps today's copy first
    finally:
        os.umask(old)
    crew = snapshot.user_data_dir() / "crew"
    assert stat.S_IMODE(os.lstat(crew).st_mode) == 0o700
    assert stat.S_IMODE(os.lstat(crew / FLAVOR).st_mode) == 0o700
    (copy,) = crewdb.copies(FLAVOR, ACCOUNT)
    for f in (env.db(), copy):
        assert stat.S_IMODE(os.lstat(f).st_mode) == 0o600, f
    assert sorted(p.name for p in (crew / FLAVOR).iterdir()) == sorted([env.db().name, copy.name])


@pytest.mark.skipif(not POSIX, reason="file modes are a POSIX matter")
def test_a_folder_made_wider_is_set_back_to_0700_constructed(make: Any) -> None:
    env = make(_three())
    env.sync()
    env.db().parent.chmod(0o755)
    env.db().chmod(0o644)
    env.save(_three(met_a=4))
    env.sync()
    assert stat.S_IMODE(os.lstat(env.db().parent).st_mode) == 0o700


def test_the_three_newest_daily_copies_are_kept_constructed(make: Any) -> None:
    env = make(_three())
    env.sync()
    folder = env.db().parent
    for day in ("2026-01-01", "2026-01-02", "2026-01-03", "2026-01-04"):
        (folder / f"{env.db().name}.daily-{day}").write_bytes(env.db().read_bytes())
    env.save(_three(met_a=4))
    env.sync()
    kept = [p.name.rsplit("-", 3)[-3:] for p in crewdb.copies(FLAVOR, ACCOUNT)]
    days = ["-".join(k) for k in kept]
    assert len(days) == crewdb.DAILY_COPIES == 3
    assert days == ["2026-01-03", "2026-01-04", time.strftime("%Y-%m-%d")]
    # Today's copy is the database before today's first change.
    today = folder / f"{env.db().name}.daily-{time.strftime('%Y-%m-%d')}"
    conn = sqlite3.connect(today)
    try:
        met = conn.execute("SELECT met FROM players WHERE player_key = ?", (KEY_A,)).fetchone()[0]
    finally:
        conn.close()
    assert met == 3 and env.rows()[KEY_A].met == 4


def test_a_copy_is_kept_before_a_schema_change_constructed(
    make: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = make(_three())
    env.sync()
    before = env.db().read_bytes()
    monkeypatch.setattr(crewdb, "SCHEMA", 2)
    monkeypatch.setitem(crewdb._MIGRATIONS, 1, ("CREATE TABLE later (x INTEGER) STRICT",))
    monkeypatch.setitem(crewdb._SQL_SET_VERSION, 2, "PRAGMA user_version = 2")
    assert len(env.rows()) == 3
    copy = env.db().with_name(env.db().name + ".before-schema-1")
    conn = sqlite3.connect(copy)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    finally:
        conn.close()
    assert len(copy.read_bytes()) == len(before)
    if POSIX:
        assert stat.S_IMODE(os.lstat(copy).st_mode) == 0o600
    conn = sqlite3.connect(env.db())
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    finally:
        conn.close()


def test_a_database_from_a_newer_wowlab_is_refused_and_not_written_constructed(
    make: Any,
) -> None:
    env = make(_three())
    env.sync()
    conn = sqlite3.connect(env.db())
    conn.execute("PRAGMA user_version = 2")
    conn.close()
    env.save(_three(met_a=8))
    before = env.db().read_bytes()
    for args in (("crew", "list"), ("crew", "sync"), ("crew", "show", KEY_A)):
        result = env.run(*args)
        assert result.exit_code == 1, args
        assert "written by a newer wowlab" in result.stderr and "update wowlab" in result.stderr
    assert env.db().read_bytes() == before
    assert len(crewdb.copies(FLAVOR, ACCOUNT)) == 0


def test_a_file_that_is_not_a_crew_database_is_refused_constructed(make: Any) -> None:
    env = make(_three())
    env.db().parent.mkdir(parents=True)
    conn = sqlite3.connect(env.db())
    conn.execute("CREATE TABLE other (x INTEGER)")
    conn.close()
    before = env.db().read_bytes()
    result = env.run("crew", "list")
    assert result.exit_code == 1 and "is not a crew database wowlab made" in result.stderr
    assert env.db().read_bytes() == before


# ─── the path ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("flavor_folder", "account"),
    [("..", ACCOUNT), (FLAVOR, ".."), (FLAVOR, "a/b"), ("a\\b", ACCOUNT), ("", ACCOUNT)],
)
def test_a_name_that_is_not_one_folder_is_refused_constructed(
    make: Any, flavor_folder: str, account: str
) -> None:
    make(None)
    with pytest.raises(crewdb.CrewDbError, match="cannot be part of a path"):
        crewdb.database_path(flavor_folder, account)


def test_the_path_is_refused_when_it_would_resolve_outside_crew_constructed(
    make: Any, tmp_path: Path
) -> None:
    env = make(_three())
    outside = tmp_path / "outside"
    outside.mkdir()
    crew = snapshot.user_data_dir() / "crew"
    crew.mkdir(parents=True)
    (crew / FLAVOR).symlink_to(outside, target_is_directory=True)
    with pytest.raises(crewdb.CrewDbError, match="would lead outside wowlab's crew folder"):
        crewdb.database_path(FLAVOR, ACCOUNT)
    result = env.run("crew", "list")
    assert result.exit_code == 1 and "outside wowlab's crew folder" in result.stderr
    assert list(outside.iterdir()) == []


def test_a_database_file_that_is_a_link_is_refused_constructed(make: Any, tmp_path: Path) -> None:
    env = make(_three())
    crew = snapshot.user_data_dir() / "crew" / FLAVOR
    crew.mkdir(parents=True)
    target = tmp_path / "elsewhere.sqlite"
    (crew / f"{ACCOUNT}.sqlite").symlink_to(target)
    with pytest.raises(crewdb.CrewDbError, match="would lead outside"):
        env.sync()
    assert not target.exists()


# ─── the sync rules ──────────────────────────────────────────────────────────


def test_rule_1_a_player_only_in_the_database_stays_and_is_marked_constructed(
    make: Any,
) -> None:
    env = make(_three())
    env.sync()
    env.save(book({KEY_A: player(met=3, last=T1), KEY_D: player(met=1, last=T1)}))
    outcome = env.sync()
    assert outcome.plan is not None and outcome.plan.players_added == 1
    assert outcome.plan.left_game_list == 2
    rows = env.rows()
    assert set(rows) == {KEY_A, KEY_B, KEY_C, KEY_D}
    assert {k for k, r in rows.items() if not r.in_game_list} == {KEY_B, KEY_C}
    assert rows[KEY_B].rating == "good" and rows[KEY_B].met == 9  # kept as it was


def test_rule_2_rating_and_note_come_from_the_saved_file_constructed(make: Any) -> None:
    env = make(_three())
    env.sync()
    env.save(
        book(
            {
                KEY_A: player(met=3, last=T1),  # rating and note taken away in game
                KEY_B: player(rating="bad", note="invented: changed", met=9, last=T0 + 10),
                KEY_C: player(met=1, last=T0),
            }
        )
    )
    outcome = env.sync()
    assert outcome.plan is not None
    assert (outcome.plan.ratings_changed, outcome.plan.notes_changed) == (2, 2)
    rows = env.rows()
    assert rows[KEY_A].rating is None and rows[KEY_A].note is None
    assert rows[KEY_B].rating == "bad" and rows[KEY_B].note == "invented: changed"


def test_rule_2_counters_add_the_difference_constructed(make: Any) -> None:
    env = make(_three())
    env.sync()
    env.save(_three(met_a=5))
    env.sync()
    assert env.rows()[KEY_A].met == 5
    env.save(_three(met_a=7))
    env.sync()
    assert env.rows()[KEY_A].met == 7


def test_rule_2_a_lower_count_adds_the_whole_new_value_constructed(make: Any) -> None:
    env = make(_three(met_a=6))
    env.sync()
    env.save(_three(met_a=2))  # the game dropped them and started again
    env.sync()
    assert env.rows()[KEY_A].met == 8


def test_rule_2_first_takes_the_earlier_and_last_the_later_constructed(make: Any) -> None:
    env = make(book({KEY_A: player(first=T0, last=T0 + 100, met=1)}))
    env.sync()
    env.save(book({KEY_A: player(first=T0 + 50, last=T0 + 50, met=1)}))
    env.sync()
    row = env.rows()[KEY_A]
    assert (row.first, row.last) == (T0, T0 + 100)
    env.save(book({KEY_A: player(first=T0 - 5, last=T1, met=2)}))
    env.sync()
    row = env.rows()[KEY_A]
    assert (row.first, row.last) == (T0 - 5, T1)


def test_a_player_dropped_by_the_game_and_met_again_keeps_the_earlier_total_constructed(
    make: Any,
) -> None:
    env = make(book({KEY_A: player(met=5, last=T0), KEY_B: player(met=1, last=T0)}))
    env.sync()
    env.save(book({KEY_B: player(met=2, last=T0 + 1)}))  # A dropped from the game's list
    env.sync()
    row = env.rows()[KEY_A]
    assert row.met == 5 and not row.in_game_list
    env.save(book({KEY_A: player(met=2, last=T1), KEY_B: player(met=2, last=T0 + 1)}))
    env.sync()
    row = env.rows()[KEY_A]
    assert row.met == 7 and row.in_game_list and row.last == T1


def test_a_sync_run_twice_changes_nothing_constructed(make: Any) -> None:
    env = make(_three())
    assert env.sync().state == "synced"
    first = env.db_sha()
    outcome = env.sync()
    assert outcome.state == "unchanged"
    assert env.db_sha() == first
    assert crewdb.copies(FLAVOR, ACCOUNT) == []
    # The CLI's own sync too.
    assert env.run("crew", "list").exit_code == 0
    assert env.db_sha() == first


def test_an_interrupted_sync_leaves_the_old_state_constructed(
    make: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = make(_three())
    env.sync()
    rows, meta = env.rows(), _meta(env.db())
    env.save(book({KEY_A: player(met=9, last=T1), KEY_D: player(met=1, last=T1)}))
    real = crewdb._write

    def half(conn: sqlite3.Connection, *a: Any) -> None:
        real(conn, *a)
        raise KeyboardInterrupt  # killed after every statement, before the commit

    monkeypatch.setattr(crewdb, "_write", half)
    with pytest.raises(KeyboardInterrupt):
        env.sync()
    monkeypatch.setattr(crewdb, "_write", real)
    assert env.rows() == rows and _meta(env.db()) == meta
    assert not os.path.lexists(str(env.db()) + "-journal")


def test_two_syncs_at_once_the_second_answers_from_the_database_constructed(
    make: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = make(_three())
    env.sync()
    env.save(_three(met_a=5))
    monkeypatch.setattr(crewdb, "LOCK_WAIT_SECONDS", 0.2)
    other = sqlite3.connect(env.db(), isolation_level=None)
    other.execute("BEGIN IMMEDIATE")  # the first sync, holding the write lock
    try:
        outcome = env.sync()
        assert outcome.state == "skipped"
        assert "did not sync after waiting 0.2 seconds" in outcome.words
        result = env.run("crew", "list")
        assert result.exit_code == 0, result.output
        assert "did not sync after waiting" in result.stdout
        assert "grouped 3 times" in result.stdout  # the database as it is
        as_json = cli.CrewReport.model_validate_json(env.run("crew", "list", "--json").stdout)
        assert as_json.sync is not None and as_json.sync.state == "skipped"
        assert len(as_json.players) == 3
    finally:
        other.execute("ROLLBACK")
        other.close()
    assert env.sync().state == "synced" and env.rows()[KEY_A].met == 5


def test_an_unknown_schema_book_syncs_nothing_and_says_so_constructed(make: Any) -> None:
    env = make(book({KEY_A: player()}, schema=7))
    for args in (("crew", "sync"), ("crew", "list")):
        result = env.run(*args)
        assert result.exit_code == 1 and "book version 7" in result.stderr
    assert not os.path.lexists(env.db())


def test_an_unreadable_book_syncs_nothing_and_says_so_constructed(make: Any) -> None:
    env = make(_three())
    env.sync()
    before = env.db_sha()
    env.file.write_bytes(
        account_file(None) + crewbook.CREW_VARIABLE.encode("ascii") + b" = {\r\n"
    )  # cut short
    for args in (("crew", "sync"), ("crew", "list")):
        result = env.run(*args)
        assert result.exit_code == 1, (args, result.output)
    assert env.db_sha() == before


def test_a_book_never_turned_on_makes_no_database_constructed(make: Any) -> None:
    env = make(None)
    for args in (("crew", "list"), ("crew", "sync"), ("crew", "show", KEY_A)):
        assert env.run(*args).exit_code == 0, args
    assert not os.path.lexists(snapshot.user_data_dir() / "crew")


def test_a_book_turned_off_still_syncs_what_it_kept_constructed(make: Any) -> None:
    env = make(book({KEY_A: player(), KEY_B: player()}, on=False))
    assert env.sync().state == "synced" and set(env.rows()) == {KEY_A, KEY_B}


# ─── what a sync will not take: the whole-book check ────────────────────────


def _over(text_bytes: int) -> str:
    return "x" * text_bytes


_PLANTED = "Inventedplanted"  # a value the refusal must never quote


@pytest.mark.parametrize(
    ("label", "players", "top", "kind"),
    [
        ("key_too_long", {KEY_A + _PLANTED + _over(250): player()}, {}, "a player's name"),
        ("key_ends_in_dash", {_PLANTED + "-": player()}, {}, "a player's name"),
        ("key_has_no_dash", {_PLANTED: player()}, {}, "a player's name"),
        ("class_too_long", {KEY_A: player(**{"class": _PLANTED + _over(256)})}, {}, "class"),
        ("role_too_long", {KEY_A: player(role=_PLANTED + _over(256))}, {}, "role"),
        ("guild_too_long", {KEY_A: player(guild=_PLANTED + _over(256))}, {}, "guild"),
        ("where_too_long", {KEY_A: player(where=_PLANTED + _over(256))}, {}, "instance name"),
        ("note_too_long", {KEY_A: player(note=_PLANTED + _over(200))}, {}, "note"),
        ("met_too_large", {KEY_A: player(met=crewdb.COUNTER_MAX + 1)}, {}, "count of groups"),
        ("met_negative", {KEY_A: player(met=-1)}, {}, "a player's entry"),
        ("met_fraction", {KEY_A: player(met=1.5)}, {}, "a player's entry"),
        ("level_too_large", {KEY_A: player(level=crewdb.LEVEL_MAX + 1)}, {}, "level"),
        ("first_too_early", {KEY_A: player(first=crewdb.TIME_MIN - 1)}, {}, "first-grouped"),
        ("last_too_late", {KEY_A: player(last=crewdb.TIME_MAX + 1)}, {}, "last-grouped"),
        ("rating_unknown", {KEY_A: player(rating=_PLANTED)}, {}, "a player's entry"),
        ("started_too_early", {KEY_A: player()}, {"started": 5}, "start time"),
        (
            "skipped_too_large",
            {KEY_A: player()},
            {"skipped": crewdb.COUNTER_MAX + 1},
            "count of skipped reads",
        ),
    ],
)
def test_one_field_out_of_range_syncs_nothing_constructed(
    make: Any, label: str, players: dict[str, Any], top: dict[str, Any], kind: str
) -> None:
    env = make(_three())
    env.sync()
    before = env.db_sha()
    env.save(book(players, **top))
    outcome = env.sync()
    assert outcome.state == "refused", label
    assert kind in outcome.words, (label, outcome.words)
    assert _PLANTED not in outcome.words and "Nothing was synced" in outcome.words
    assert env.db_sha() == before
    result = env.run("crew", "sync")
    assert result.exit_code == 1 and _PLANTED not in result.output


def test_more_than_20000_players_syncs_nothing_constructed(make: Any) -> None:
    keys = many_keys(crewdb.MAX_PLAYERS + 1)
    env = make(book({k: player(guild=None, where=None) for k in keys}))
    outcome = env.sync()
    assert outcome.state == "refused" and "the number of players" in outcome.words
    assert not os.path.lexists(env.db())


def test_text_that_is_not_valid_unicode_syncs_nothing_constructed(make: Any) -> None:
    env = make(None)
    crew = book({KEY_A: player(guild="Inventedguild")})
    env.save(crew.replace(b"Inventedguild", b"Invented\xffguild"))
    outcome = env.sync()
    assert outcome.state == "refused" and "guild" in outcome.words


def test_a_refused_first_sync_creates_nothing_constructed(make: Any) -> None:
    env = make(book({KEY_A: player(note=_over(201))}))
    assert env.sync().state == "refused"
    assert not os.path.lexists(snapshot.user_data_dir() / "crew")


# ─── what a sync will not take: the hold ─────────────────────────────────────


def _older(env: Env) -> bytes:
    """An older saved file: what a restore would put back."""
    return account_file(_three(met_a=1))


def test_a_restored_book_is_held_until_a_sync_by_hand_constructed(make: Any) -> None:
    env = make(_three(met_a=4))
    env.sync()
    before = env.db_sha()
    env.gate("restore 20261119-000000-abcd (WTF)", _older(env))
    outcome = env.sync()
    assert outcome.state == "held" and outcome.held == ["put_back"]
    assert env.db_sha() == before
    result = env.run("crew", "list")
    assert result.exit_code == 0
    assert "wowlab put WowLab.lua back since the last sync" in result.stdout
    assert "grouped 4 times" in result.stdout  # answered from the database as it was
    report = cli.CrewReport.model_validate_json(env.run("crew", "list", "--json").stdout)
    assert report.sync is not None and report.sync.state == "held"
    assert report.sync.words in result.stdout.replace("\n", " ") or report.sync.words in (
        result.stdout
    )
    # By hand: a plan, a question, and no on a missing answer.
    declined = env.run("crew", "sync", input="")
    assert declined.exit_code == 1 and cli.CONFIRM_DECLINED in declined.stderr
    assert "A sync would add 0 players and 0 matches, update" in declined.stdout
    assert env.db_sha() == before
    done = env.run("crew", "sync", "--yes")
    assert done.exit_code == 0, done.output
    assert env.rows()[KEY_A].met == 4  # never lowered
    assert env.sync().state == "unchanged"  # and the next one runs by itself


def test_a_restored_book_after_a_later_session_is_still_held_constructed(make: Any) -> None:
    env = make(_three(met_a=4))
    env.sync()
    env.gate("restore 20261119-000000-abcd (WTF)", _older(env))
    env.save(_three(met_a=2))  # a session played after the restore
    assert env.sync().state == "held"
    assert env.run("crew", "sync", "--yes").exit_code == 0
    assert env.rows()[KEY_A].met == 4


def test_a_merged_file_is_held_constructed(make: Any) -> None:
    env = make(_three())
    env.sync()
    env.gate(f"sv merge {LIVE}", account_file(_three(met_a=9)))
    outcome = env.sync()
    assert outcome.state == "held" and outcome.held == ["put_back"]


def test_an_imported_foreign_book_is_held_constructed(make: Any) -> None:
    env = make(_three())
    env.sync()
    foreign = book({KEY_D: player(met=2)}, started=T0 + 999)  # another book entirely
    env.gate("restore 20261119-000000-ffff (WTF)", account_file(foreign))
    outcome = env.sync()
    assert outcome.state == "held" and outcome.held == ["other_book", "put_back"]
    assert KEY_D not in env.rows()
    assert env.run("crew", "sync", "--yes").exit_code == 0
    assert KEY_D in env.rows()
    assert env.sync().state == "unchanged"


def test_a_new_book_after_the_old_was_lost_is_held_once_then_syncs_constructed(
    make: Any,
) -> None:
    env = make(_three())
    env.sync()
    env.save(book({KEY_D: player(met=1)}, started=T1))  # the addon started a new book
    outcome = env.sync()
    assert outcome.state == "held" and outcome.held == ["other_book"]
    assert "is not the one wowlab's crew database was filled from" in outcome.words
    done = env.run("crew", "sync", "--yes")
    assert done.exit_code == 0, done.output
    env.save(book({KEY_D: player(met=2)}, started=T1))
    assert env.sync().state == "synced"
    assert env.rows()[KEY_D].met == 2 and env.rows()[KEY_A].met == 3


def test_a_crew_forget_of_the_most_recent_player_does_not_hold_constructed(
    make: Any,
) -> None:
    env = make(_three())
    env.sync()
    forgot = env.run("crew", "forget", KEY_A, "--yes")
    assert forgot.exit_code == 0, forgot.output
    outcome = env.sync()
    assert outcome.state == "synced", outcome.words
    assert not env.rows()[KEY_A].in_game_list
    assert _meta(env.db())["journal_record"] == guard.history()[-1].id


def test_an_undo_of_a_crew_forget_is_held_constructed(make: Any) -> None:
    env = make(_three())
    env.sync()
    assert env.run("crew", "forget", KEY_A, "--yes").exit_code == 0
    assert env.sync().state == "synced"
    undone = env.run_bare("undo", "--yes")
    assert undone.exit_code == 0, undone.output
    outcome = env.sync()
    assert outcome.state == "held" and outcome.held == ["put_back"]


def test_an_undo_of_a_crew_rate_is_held_constructed(make: Any) -> None:
    env = make(_three())
    env.sync()
    assert env.run("crew", "rate", KEY_C, "good", "--yes").exit_code == 0
    assert env.sync().state == "synced" and env.rows()[KEY_C].rating == "good"
    assert env.run_bare("undo", "--yes").exit_code == 0
    assert env.sync().state == "held"


def test_crew_writes_do_not_hold_constructed(make: Any) -> None:
    env = make(_three())
    env.sync()
    assert env.run("crew", "note", KEY_B, "invented: steady", "--yes").exit_code == 0
    assert env.sync().state == "synced" and env.rows()[KEY_B].note == "invented: steady"


def test_a_journal_that_cannot_be_read_holds_without_a_crash_constructed(make: Any) -> None:
    env = make(_three())
    env.sync()
    journal = snapshot.default_store_path() / "journal"
    journal.mkdir(parents=True, exist_ok=True)
    (journal / "00000099-deadbeef.json").write_bytes(b"{ not a record")
    env.save(_three(met_a=5))
    outcome = env.sync()
    assert outcome.state == "held" and outcome.held == ["journal_unreadable"]
    result = env.run("crew", "list")
    assert result.exit_code == 0 and "cannot read its own record of changes" in result.stdout
    assert env.run("crew", "sync", "--yes").exit_code == 0
    assert env.rows()[KEY_A].met == 5


def test_a_journal_without_the_kept_record_holds_without_a_crash_constructed(
    make: Any,
) -> None:
    env = make(_three())
    record = env.gate("crew rate (1 player)", env.data().replace(b"good", b"bad", 1))
    env.sync()
    assert _meta(env.db())["journal_record"] == record
    for f in (snapshot.default_store_path() / "journal").iterdir():
        f.unlink()
    outcome = env.sync()
    assert outcome.state == "unchanged" or outcome.state == "held"
    env.save(_three(met_a=5))
    outcome = env.sync()
    assert outcome.state == "held" and outcome.held == ["journal_record_gone"]


def test_a_held_sync_by_hand_keeps_a_rating_changed_in_the_database_constructed(
    make: Any,
) -> None:
    env = make(_three())
    env.sync()
    # A rating changed in the database alone (what M20-02's desk write does):
    # its changed-at time is later than its last agreement with the file.
    conn = sqlite3.connect(env.db())
    conn.execute(
        "UPDATE players SET rating = 'good', marks_changed = marks_agreed + 1 WHERE player_key = ?",
        (KEY_A,),
    )
    conn.execute("UPDATE players SET marks_changed = marks_agreed WHERE player_key = ?", (KEY_C,))
    conn.commit()
    conn.close()
    restored = book(
        {
            KEY_A: player(rating="bad", met=1, last=T0),
            KEY_B: player(met=9, last=T0),
            KEY_C: player(rating="bad", met=1, last=T0),
        }
    )
    env.gate("restore 20261119-000000-abcd (WTF)", account_file(restored))
    assert env.run("crew", "sync", "--yes").exit_code == 0
    rows = env.rows()
    assert rows[KEY_A].rating == "good"  # the database's own change is kept
    assert rows[KEY_C].rating == "bad"  # unchanged since it agreed: replaced
    assert rows[KEY_B].rating is None and rows[KEY_B].met == 9


# ─── another root, a renamed flavor folder ───────────────────────────────────


def test_a_second_install_root_is_refused_until_adopted_constructed(make: Any) -> None:
    first = make(_three(), name="first")
    first.sync()
    second = make(_three(met_a=8), name="second")
    before = first.db_sha()
    outcome = second.sync()
    assert outcome.state == "elsewhere"
    assert "crew sync --adopt" in outcome.words and os.path.realpath(first.root) in outcome.words
    assert first.db_sha() == before
    listed = second.run("crew", "list")
    assert listed.exit_code == 0 and "run `wowlab crew sync --adopt`" in listed.stdout
    assert second.run("crew", "sync").exit_code == 1
    assert first.db_sha() == before
    adopted = second.run("crew", "sync", "--adopt", "--yes")
    assert adopted.exit_code == 0, adopted.output
    assert _meta(second.db())["install_root"] == os.path.realpath(second.root)
    assert first.sync().state == "elsewhere"  # and now the first root is the other one


def test_adopt_takes_no_path_constructed(make: Any, tmp_path: Path) -> None:
    env = make(_three())
    result = env.run("crew", "sync", "--adopt", str(tmp_path / "x.sqlite"))
    assert result.exit_code == 2


def _rename_flavor(env: Env, new: str) -> None:
    env.flavor.rename(env.root / new)


def test_a_renamed_flavor_folder_is_refused_until_adopted_constructed(make: Any) -> None:
    env = make(_three())
    env.sync()
    env.save(_three(met_a=5))
    env.sync()  # a daily copy now sits beside it
    old, old_copies = env.db(), crewdb.copies(FLAVOR, ACCOUNT)
    assert len(old_copies) == 1
    data, parsed = env.data(), env.book()
    _rename_flavor(env, "_renamed_")
    target = env.target("_renamed_")
    outcome = crewdb.sync(target, data, parsed)
    assert outcome.state == "elsewhere" and str(old) in outcome.words
    assert not os.path.lexists(crewdb.database_path("_renamed_", ACCOUNT))
    adopted = crewdb.adopt(target)
    assert adopted.moved_from == str(old) and adopted.was_flavor_folder == FLAVOR
    new = crewdb.database_path("_renamed_", ACCOUNT)
    assert new.exists() and not old.exists()
    assert crewdb.copies(FLAVOR, ACCOUNT) == [] and len(crewdb.copies("_renamed_", ACCOUNT)) == 1
    assert crewdb.sync(target, data, parsed).state == "unchanged"
    assert {r.key for r in crewdb.read("_renamed_", ACCOUNT)} == {KEY_A, KEY_B, KEY_C}


def test_adopt_never_merges_and_takes_only_wowlabs_files_constructed(make: Any) -> None:
    env = make(_three())
    env.sync()
    data, parsed = env.data(), env.book()
    _rename_flavor(env, "_renamed_")
    target = env.target("_renamed_")
    # A second candidate: never merged, so neither is adopted.
    second = crewdb.crew_root() / "_also_" / f"{ACCOUNT}.sqlite"
    second.parent.mkdir()
    second.write_bytes(crewdb.database_path(FLAVOR, ACCOUNT).read_bytes())
    with pytest.raises(crewdb.CrewDbError, match="never merges two"):
        crewdb.adopt(target)
    # A SQLite file without the application_id is not a candidate at all.
    second.unlink()
    conn = sqlite3.connect(second)
    conn.execute("CREATE TABLE meta (name TEXT, value ANY)")
    conn.close()
    adopted = crewdb.adopt(target)
    assert adopted.moved_from == str(crewdb.database_path(FLAVOR, ACCOUNT))
    assert second.exists()
    # The database at the new place is never merged with another.
    with pytest.raises(crewdb.CrewDbError, match="already this install's"):
        crewdb.adopt(target)
    assert crewdb.sync(target, data, parsed).state == "unchanged"


def test_adopt_with_nothing_to_adopt_changes_nothing_constructed(make: Any) -> None:
    env = make(_three())
    result = env.run("crew", "sync", "--adopt", "--yes")
    assert result.exit_code == 1 and "nothing to adopt" in result.stderr
    assert not os.path.lexists(env.db())


# ─── reading ─────────────────────────────────────────────────────────────────


def test_list_and_show_mark_a_player_the_game_no_longer_lists_constructed(
    make: Any,
) -> None:
    env = make(_three())
    env.sync()
    env.save(book({KEY_A: player(met=3, last=T1)}))
    text = env.run("crew", "list")
    assert text.exit_code == 0
    report = cli.CrewReport.model_validate_json(env.run("crew", "list", "--json").stdout)
    assert report.total == 3
    for p in report.players:
        (line,) = [x for x in text.stdout.splitlines() if x.startswith(f"  {p.key}:")]
        assert ("not in the game's list" in line) == (not p.in_game_list), p.key
    assert {p.key for p in report.players if not p.in_game_list} == {KEY_B, KEY_C}
    shown = env.run("crew", "show", KEY_B)
    assert shown.exit_code == 0 and "In the game's list: no" in shown.stdout
    as_json = cli.CrewShowReport.model_validate_json(
        env.run("crew", "show", KEY_B, "--json").stdout
    )
    assert as_json.player is not None and as_json.player.in_game_list is False
    assert shown.stdout.splitlines()[: len(as_json.lines)] == as_json.lines
    raw = json.loads(env.run("crew", "list", "--json").stdout)
    # Fields are added, none renamed: every field of the book's record is there.
    assert set(crewbook.CrewPlayer.model_fields) <= set(raw["players"][0])


def _hostile(base: str) -> str:
    """`base` with ESC, CSI, U+009B, U+202E, `|`, markup and a line break,
    assembled at run time."""
    name, realm = base.split("-")
    return name + "\x1b[31m" + chr(0x9B) + "2J" + chr(0x202E) + "|cff<b>x</b>\n-" + realm


def test_hostile_name_guild_and_note_are_stored_and_shown_escaped_constructed(
    make: Any,
) -> None:
    key, guild, note = _hostile(KEY_A), _hostile(KEY_B), _hostile(KEY_C)[:150]
    env = make(book({key: player(guild=guild, note=note)}))
    env.sync()
    conn = sqlite3.connect(env.db())
    try:
        stored = conn.execute("SELECT player_key, guild, note FROM players").fetchall()
    finally:
        conn.close()
    assert stored == [(key, guild, note)]  # kept as they are, bound, never in a statement
    for args in (("crew", "list"), ("crew", "show", key)):
        result = env.run(*args)
        assert result.exit_code == 0, result.output
        assert "\x1b" not in result.stdout and chr(0x9B) not in result.stdout
        assert chr(0x202E) not in result.stdout and "\\x1b[31m" in result.stdout
    raw = env.run("crew", "show", key, "--json").stdout
    assert raw.isascii() and json.loads(raw)["player"]["guild"] == guild


def test_fifty_thousand_players_are_listed_in_time_constructed(make: Any) -> None:
    """The stated time: `crew list --json` and `crew list` of 50,000 players
    kept in the database, each within 30 seconds, a bound loose enough for
    a slow CI runner (measured 2026-11-19 on an Apple Silicon Mac, in the
    test runner: 3.3 s with --json, 1.4 s as text; printed with -s)."""
    env = make(book({KEY_A: player()}))
    env.sync()
    conn = crewdb._open(env.db(), create=False)
    assert conn is not None
    now = time.time_ns()
    rows = [
        crewdb._Row(
            k,
            *k.split("-", 1),
            "MAGE",
            60,
            None,
            None,
            1,
            T0,
            T0,
            None,
            None,
            None,
            None,
            0,
            0,
            now,
            now,
        )
        for k in many_keys(50_000 - 1)  # and the book's own player
    ]
    try:
        with crewdb._transaction(conn):
            crewdb._write(conn, rows, [], {})
    finally:
        conn.close()
    start = time.perf_counter()
    result = env.run("crew", "list", "--json")
    as_json = time.perf_counter() - start
    assert result.exit_code == 0
    assert len(json.loads(result.stdout)["players"]) == 50_000
    start = time.perf_counter()
    text = env.run("crew", "list")
    as_text = time.perf_counter() - start
    assert text.exit_code == 0 and text.stdout.count("\n") > 50_000
    print(f"\n50,000 players: crew list --json {as_json:.2f} s, crew list {as_text:.2f} s")
    assert as_json < 30 and as_text < 30


# ─── SQL: constant statements only ───────────────────────────────────────────

_SQL_WORDS = ("SELECT", "INSERT", "UPDATE", "DELETE", "CREATE", "PRAGMA", "BEGIN", "DROP")
_CALLS = frozenset({"execute", "executemany", "executescript"})


def _constants(tree: ast.Module) -> tuple[set[str], set[str]]:
    """Module-level names bound to a plain string, and to a tuple or dict
    of plain strings."""
    plain: set[str] = set()
    groups: set[str] = set()
    for node in tree.body:
        target, value = None, None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        elif isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        if not isinstance(target, ast.Name) or value is None:
            continue
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            plain.add(target.id)
        elif (
            (
                isinstance(value, ast.Tuple)
                and all(
                    isinstance(e, ast.Constant) and isinstance(e.value, str) for e in value.elts
                )
            )
            or (
                isinstance(value, ast.Dict)
                and all(
                    isinstance(v, ast.Constant) and isinstance(v.value, str) for v in value.values
                )
            )
            or (isinstance(value, ast.Dict) and not value.keys)
        ):
            groups.add(target.id)
    return plain, groups


def _starts_sql(text: str) -> bool:
    words = text.upper().split()
    return bool(words) and words[0] in _SQL_WORDS


def sql_problems(source: str) -> list[str]:
    """Every statement in `source` not given as a constant: an `execute`
    whose first argument is not a module-level constant string, a member of
    a module-level tuple or dict of them, or the variable of a `for` loop
    over one that the call sits in; and any f-string, `%` or `+` that builds
    text starting with an SQL word."""
    tree = ast.parse(source)
    plain, groups = _constants(tree)
    in_loop: set[int] = set()  # calls whose first argument is a loop over constants
    for node in ast.walk(tree):
        if not (isinstance(node, ast.For) and isinstance(node.target, ast.Name)):
            continue
        it = node.iter
        over = (isinstance(it, ast.Name) and it.id in groups) or (
            isinstance(it, ast.Subscript)
            and isinstance(it.value, ast.Name)
            and it.value.id in groups
        )
        if not over:
            continue
        for inner in ast.walk(node):
            if (
                isinstance(inner, ast.Call)
                and inner.args
                and isinstance(inner.args[0], ast.Name)
                and inner.args[0].id == node.target.id
            ):
                in_loop.add(id(inner))
    problems: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            first = node.values[0] if node.values else None
            if isinstance(first, ast.Constant) and _starts_sql(str(first.value)):
                problems.append(f"line {node.lineno}: an f-string holds SQL")
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Mod, ast.Add)):
            left = node.left
            if (
                isinstance(left, ast.Constant)
                and isinstance(left.value, str)
                and _starts_sql(left.value)
            ):
                problems.append(f"line {node.lineno}: SQL built with % or +")
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _CALLS
        ):
            if not node.args:
                problems.append(f"line {node.lineno}: a statement given by keyword")
                continue
            arg = node.args[0]
            ok = (
                (isinstance(arg, ast.Name) and arg.id in plain)
                or id(node) in in_loop
                or (
                    isinstance(arg, ast.Subscript)
                    and isinstance(arg.value, ast.Name)
                    and arg.value.id in groups
                )
            )
            if not ok:
                problems.append(f"line {node.lineno}: {ast.unparse(arg)} is not a constant")
    return problems


def test_every_statement_is_a_constant_with_bound_parameters() -> None:
    assert sql_problems(SRC.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize(
    "planted",
    [
        'conn.execute(f"SELECT * FROM players WHERE player_key = {key}")',
        'conn.execute("SELECT * FROM players WHERE player_key = %s" % key)',
        'conn.execute("SELECT * FROM players WHERE player_key = " + key)',
        'Q = "SELECT 1"\nconn.execute(Q + key)',
        "conn.execute(statement)",
    ],
)
def test_the_static_check_fails_on_a_built_statement_constructed(planted: str) -> None:
    source = SRC.read_text(encoding="utf-8") + "\n\ndef _planted(conn, key, statement):\n"
    source += "\n".join("    " + line for line in planted.splitlines()) + "\n"
    assert sql_problems(source) != []


# ─── module boundaries ───────────────────────────────────────────────────────


def test_only_the_crew_commands_import_crewdb() -> None:
    """Every command module but the crew commands' loads without `crewdb`
    (the lazy CLI: no other command imports it)."""
    import subprocess

    code = (
        "import importlib, pkgutil, sys\n"
        "import wowlab_core.cli as cli\n"
        "for m in pkgutil.iter_modules(cli.__path__):\n"
        "    if m.name != '_cmd_crew':\n"
        "        importlib.import_module('wowlab_core.cli.' + m.name)\n"
        "import wowlab_core.ui\n"
        "print('wowlab_core.crewdb' in sys.modules)\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=120, check=True
    )
    assert done.stdout.strip() == "False", done.stderr


def test_crewdb_imports_no_network_module_and_nothing_of_db2lake() -> None:
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
            names |= {f"{node.module}.{a.name}" for a in node.names}
    banned = ("socket", "http", "urllib", "requests", "ssl", "gamedata", "db2lake", "asyncio")
    assert not [n for n in names if any(part in banned for part in n.split("."))], names


def test_crew_help_names_the_database() -> None:
    result = runner.invoke(cli.app, ["crew", "--help"], terminal_width=200)
    assert "wowlab's own crew database" in " ".join(result.stdout.split())
    sync_help = runner.invoke(cli.app, ["crew", "sync", "--help"], terminal_width=200)
    assert sync_help.exit_code == 0 and "--adopt" in sync_help.stdout


def test_a_database_file_with_a_second_hard_link_is_refused_constructed(
    make: Any, tmp_path: Path
) -> None:
    env = make(_three())
    env.sync()
    other = tmp_path / "other-name"
    os.link(env.db(), other)
    before = other.read_bytes()
    env.save(_three(met_a=6))
    with pytest.raises(crewdb.CrewDbError, match="is not a crew database wowlab made"):
        env.sync()
    assert other.read_bytes() == before
