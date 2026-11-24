"""The write commands, `doctor` and `snap export` keep the terms for wowlab's
crew database (M20-02, docs/LAB_PLAN.md §21.3 "Writing" and "It is the only
copy", §21.4 terms 4 and 5, ADR-0036 terms 3 to 5, ADR-0039).

CONSTRUCTED INPUT, every byte: each crew book is built at run time by
`parser/_crew_build.py` (ADR-0036 term 3: no real crew record enters the
repository) inside a copy of the captured install `fixtures/macos-70888/` in
`tmp_path`, with the four invented keys only; every database is built at run
time under a user data directory redirected into `tmp_path` (a home folder
named after an invented login), and none is committed.

What is pinned:

- `crew rate` and `crew note` change the database after the saved file in
  the same command, take its write lock before the file is written, and say
  so honestly when the database step fails after the file was written;
- for a player only the database keeps, they change the database alone:
  asked first, no install write and no journal record, pinned by `--expect`
  on the row's rating and note (refused, exit 3, when the row changed), the
  note rules as for the saved file, the row's changed-at time set so a held
  sync keeps it, and the UI's Confirm step offered with that pin;
- `doctor` gives each database's path, size and counts, never a row and
  never the install it was filled from; under `--redact` the account
  folder, the home folder and the login are replaced;
- `snap export` says the archive does not include the database.

Nothing here reads a real install or the real data directory.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import psutil
import pytest
import typer

from wowlab_core import cli, crewdb, guard, snapshot, ui

sys.path.insert(0, str(Path(__file__).resolve().parent / "parser"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _casc_build import tree_state
from _crew_build import (
    ACCOUNT,
    FLAVOR,
    GUILD,
    KEY_A,
    KEY_B,
    KEY_C,
    KEY_D,
    NOTE,
    T1,
    WHERE,
    account_file,
    book,
    player,
)
from _crew_build import install as build_install
from _user_dirs import redirect_user_dirs
from test_crewdb import Env, _Proc

# An invented login: the home folder (and so wowlab's data folder) is named
# after it, as on a real machine.
LOGIN = "inventedlogin"
NOTE_B = "invented note: only in the database"
FORBIDDEN = (
    KEY_A,
    KEY_B,
    KEY_C,
    KEY_D,
    "Inventeda",
    "Inventedb",
    "Inventedc",
    "Inventedd",
    "Fakerealm",
    "Otherfakerealm",
    GUILD,
    WHERE,
    NOTE,
    NOTE_B,
)


@pytest.fixture
def make(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[..., Env]]:
    monkeypatch.delenv("WOWLAB_WOW_ROOT", raising=False)
    monkeypatch.delenv("WOWLAB_FLAVOR", raising=False)
    home = tmp_path / "Users" / LOGIN
    home.mkdir(parents=True)
    redirect_user_dirs(monkeypatch, home)
    for name in ("USER", "LOGNAME", "USERNAME"):
        monkeypatch.setenv(name, LOGIN)
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
        assert not list(env.root.rglob("*.sqlite*"))


def _three() -> bytes:
    return book(
        {
            KEY_A: player(rating="bad", note=NOTE, met=3),
            KEY_B: player(rating="good", met=9),
            KEY_C: player(met=1),
        },
        current=[KEY_A],
    )


def _dropped(env: Env) -> None:
    """The database keeps A, B and C; the game's list now holds A alone."""
    assert env.sync().state == "synced"
    env.save(book({KEY_A: player(rating="bad", note=NOTE, met=4)}))
    assert env.sync().state == "synced"
    rows = env.rows()
    assert not rows[KEY_B].in_game_list and not rows[KEY_C].in_game_list


def _own(env: Env, key: str) -> tuple[int, int]:
    """Whether the row's rating, and its note, is the database's own."""
    conn = sqlite3.connect(env.db())
    try:
        rating, note = conn.execute(
            "SELECT rating_own, note_own FROM players WHERE player_key = ?", (key,)
        ).fetchone()
    finally:
        conn.close()
    return int(rating), int(note)


def _report(result: Any) -> cli.CrewEditReport:
    first, _ = json.JSONDecoder().raw_decode(result.stdout.lstrip())
    return cli.CrewEditReport.model_validate(first)


def _pin(report: cli.CrewEditReport, command: str) -> str:
    return ":".join(str(getattr(report, f)) for f in cli.EXPECT_FIELDS[("crew", command)])


# ─── rate and note: the saved file, then the database ────────────────────────


def test_rate_and_note_reach_the_database_in_the_same_command_constructed(make: Any) -> None:
    env = make(_three())
    env.sync()
    done = env.run("crew", "rate", KEY_C, "bad", "--yes", "--json")
    assert done.exit_code == 0, done.output
    report = _report(done)
    assert report.written and report.database_updated and not report.database_only
    assert report.row_sha256 == "absent"
    assert env.rows()[KEY_C].rating == "bad"  # no sync in between
    # Written through the file: never the database's own (an undo of this
    # write, synced by hand, takes the file's rating again).
    assert _own(env, KEY_C) == (0, 0)
    assert env.run("crew", "note", KEY_C, "invented: at the desk", "--yes").exit_code == 0
    assert env.rows()[KEY_C].note == "invented: at the desk"
    assert env.sync().state == "synced"
    row = env.rows()[KEY_C]
    assert (row.rating, row.note) == ("bad", "invented: at the desk")


def test_without_a_database_rate_writes_the_file_alone_constructed(make: Any) -> None:
    env = make(_three())
    report = _report(env.run("crew", "rate", KEY_C, "good", "--yes", "--json"))
    assert report.written and not report.database_updated
    assert not env.db().exists()


def test_a_rate_waits_for_the_database_lock_before_it_writes_constructed(
    make: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = make(_three())
    env.sync()
    before, records = env.data(), guard.history()
    monkeypatch.setattr(crewdb, "LOCK_WAIT_SECONDS", 0.2)
    other = sqlite3.connect(env.db(), isolation_level=None)
    other.execute("BEGIN IMMEDIATE")  # a sync holding the write lock
    try:
        result = env.run("crew", "rate", KEY_C, "bad", "--yes")
    finally:
        other.execute("ROLLBACK")
        other.close()
    assert result.exit_code == 1
    flat = " ".join(result.stderr.split())
    assert "Another wowlab is using the crew database" in flat and "the rate did not start" in flat
    assert env.data() == before and guard.history() == records  # nothing written
    assert env.rows()[KEY_C].rating is None


def test_a_note_that_fails_in_the_database_says_so_honestly_constructed(
    make: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = make(_three())
    env.sync()

    def broken(self: crewdb.Marking) -> bool:
        raise crewdb.CrewDbError("The crew database is broken (constructed).")

    with monkeypatch.context() as patched:
        patched.setattr(crewdb.Marking, "finish", broken)
        result = env.run("crew", "note", KEY_C, "invented: steady", "--yes", "--json")
    assert result.exit_code == 1
    flat = " ".join(result.stderr.split())
    assert "was changed and journaled as" in flat and "still holds the old note" in flat
    assert "`wowlab crew sync` brings the new one in." in flat
    report = _report(result)
    assert report.written is True and report.database_updated is False
    assert b"invented: steady" in env.data()
    assert env.sync().state == "synced" and env.rows()[KEY_C].note == "invented: steady"


# ─── a player only the database keeps ────────────────────────────────────────


def test_a_write_to_the_database_alone_touches_nothing_in_the_install_constructed(
    make: Any,
) -> None:
    env = make(_three())
    _dropped(env)
    tree, records = tree_state(env.root), guard.history()
    snaps = [m.id for m in snapshot.SnapshotStore().list()]
    declined = env.run("crew", "rate", KEY_B, "bad", input="")
    assert declined.exit_code == 1
    flat = " ".join(declined.stdout.split())
    assert f"Change {KEY_B}'s rating in wowlab's crew database" in flat
    assert "good -> bad" in flat
    assert "no game file is written, and this cannot be undone" in flat
    assert "The game will not warn you about a player it no longer lists." in flat
    assert env.rows()[KEY_B].rating == "good"  # declined: nothing changed
    done = env.run("crew", "rate", KEY_B, "bad", input="y\n")
    assert done.exit_code == 0, done.output
    assert "in wowlab's crew database. This cannot be undone." in " ".join(done.stdout.split())
    assert env.rows()[KEY_B].rating == "bad"
    assert tree_state(env.root) == tree  # nothing in the install
    assert guard.history() == records  # no journal record
    assert [m.id for m in snapshot.SnapshotStore().list()] == snaps  # no snapshot
    assert _own(env, KEY_B) == (1, 0)  # the rating is the database's own
    report = _report(env.run("crew", "note", KEY_B, NOTE_B, "--yes", "--json"))
    assert report.database_only and report.written and report.database_updated
    assert report.transaction is None and report.written_sha256 == "absent"
    assert env.rows()[KEY_B].note == NOTE_B
    assert tree_state(env.root) == tree and guard.history() == records


def test_the_plan_s_pin_lets_the_confirmed_run_write_the_database_constructed(
    make: Any,
) -> None:
    env = make(_three())
    _dropped(env)
    plan = _report(env.run("crew", "rate", KEY_C, "good", "--json", input=""))
    assert plan.database_only and not plan.written and plan.changed
    assert plan.row_sha256 not in (None, "absent")
    done = env.run("crew", "rate", KEY_C, "good", "--yes", f"--expect={_pin(plan, 'rate')}")
    assert done.exit_code == 0, done.output
    assert env.rows()[KEY_C].rating == "good"


def test_a_row_changed_between_plan_and_confirm_is_refused_constructed(make: Any) -> None:
    env = make(_three())
    _dropped(env)
    plan = _report(env.run("crew", "rate", KEY_C, "good", "--json", input=""))
    # Between plan and confirm, another run notes the player (the row changes).
    assert env.run("crew", "note", KEY_C, NOTE_B, "--yes").exit_code == 0
    refused = env.run("crew", "rate", KEY_C, "good", "--yes", f"--expect={_pin(plan, 'rate')}")
    assert refused.exit_code == 3, refused.output
    assert env.rows()[KEY_C].rating is None


def test_the_database_refuses_a_row_changed_since_it_was_read_constructed(make: Any) -> None:
    """The check under the database's own lock, for a change that lands
    after the command read the row (no `--expect` needed)."""
    env = make(_three())
    _dropped(env)
    with pytest.raises(crewdb.RowChanged):
        crewdb.mark_only(FLAVOR, ACCOUNT, KEY_C, "rating", "bad", expect=("good", None))
    assert env.rows()[KEY_C].rating is None
    with pytest.raises(crewdb.RowChanged):
        crewdb.mark_only(FLAVOR, ACCOUNT, KEY_D, "rating", "bad", expect=(None, None))
    crewdb.mark_only(FLAVOR, ACCOUNT, KEY_C, "rating", "bad", expect=(None, None))
    assert env.rows()[KEY_C].rating == "bad"


@pytest.mark.parametrize(
    "text",
    [
        "x" * 2001,
        "a\tb",
        "a|cff00ff00b",
        "a" + chr(0x200B) + "b",  # Cf
        "a" + chr(0x2028) + "b",  # Zl
        "a" + chr(0x2029) + "b",  # Zp
        "a" + chr(0x85) + "b",  # C1
    ],
    ids=["over-2000-bytes", "control", "bar", "cf", "zl", "zp", "c1"],
)
def test_a_database_only_note_keeps_the_note_rules_constructed(make: Any, text: str) -> None:
    env = make(_three())
    _dropped(env)
    result = env.run("crew", "note", KEY_C, text, "--yes")
    assert result.exit_code == 2, result.output
    assert env.rows()[KEY_C].note is None
    with pytest.raises(Exception, match="note"):
        crewdb.mark_only(FLAVOR, ACCOUNT, KEY_C, "note", text, expect=(None, None))
    assert env.rows()[KEY_C].note is None


def test_a_database_only_note_of_2000_bytes_is_kept_whole_constructed(make: Any) -> None:
    env = make(_three())
    _dropped(env)
    text = "é" * 1000  # 2,000 bytes (M20-14: the limit was 200)
    assert env.run("crew", "note", KEY_C, text, "--yes").exit_code == 0
    assert env.rows()[KEY_C].note == text


def test_a_held_sync_keeps_a_database_only_rating_constructed(make: Any) -> None:
    env = make(_three())
    _dropped(env)
    assert env.run("crew", "rate", KEY_B, "bad", "--yes").exit_code == 0
    # An older book put back by wowlab, holding B rated good again.
    env.gate(
        "restore 20261119-000000-abcd (WTF)",
        account_file(book({KEY_A: player(met=1), KEY_B: player(rating="good", met=9)})),
    )
    assert env.sync().state == "held"
    assert env.run("crew", "sync", input="y\n").exit_code == 0
    assert env.rows()[KEY_B].rating == "bad"  # the database's own change is kept


def test_another_book_held_keeps_a_database_only_rating_and_note_constructed(
    make: Any,
) -> None:
    """The other hold (another book: a new `started`), confirmed by hand: a
    rating and note the database holds as its own are kept even where the
    other book has its own, as from a book put back (§21.3)."""
    env = make(_three())
    _dropped(env)
    assert env.run("crew", "rate", KEY_B, "bad", "--yes").exit_code == 0
    assert env.run("crew", "note", KEY_B, NOTE_B, "--yes").exit_code == 0
    other = {KEY_A: player(met=1), KEY_B: player(rating="good", note="invented: other", met=2)}
    env.save(book(other, started=T1))
    assert env.sync().held == ["other_book"]
    assert env.run("crew", "sync", input="y\n").exit_code == 0
    assert (env.rows()[KEY_B].rating, env.rows()[KEY_B].note) == ("bad", NOTE_B)


def test_the_ui_offers_confirm_on_a_database_only_plan_with_its_pin_constructed(
    make: Any,
) -> None:
    env = make(_three())
    _dropped(env)
    plan = env.run("crew", "note", KEY_C, NOTE_B, "--json", input="")
    assert plan.exit_code == 1
    report = _report(plan)
    leaf = ui._read_tree(typer.main.get_command(cli.app))[("crew", "note")]
    assert leaf.pin == cli.EXPECT_FIELDS[("crew", "note")]
    offer = ui._declined_offer(leaf, json.loads(plan.stdout))
    assert offer.pin == f"{report.target_sha256}:absent:{report.row_sha256}"
    assert offer.note is not None and "crew database" in offer.note
    done = env.run("crew", "note", KEY_C, NOTE_B, "--json", "--yes", f"--expect={offer.pin}")
    assert done.exit_code == 0, done.output
    assert env.rows()[KEY_C].note == NOTE_B


def test_a_pin_of_the_saved_file_does_not_confirm_a_database_write_constructed(
    make: Any,
) -> None:
    env = make(_three())
    plan = _report(env.run("crew", "rate", KEY_C, "bad", "--json", input=""))
    assert not plan.database_only and plan.row_sha256 == "absent"
    _dropped(env)
    refused = env.run("crew", "rate", KEY_C, "bad", "--yes", f"--expect={_pin(plan, 'rate')}")
    assert refused.exit_code == 3


# ─── fix round 1: agreement, and a desk mark on a player who returns ─────────


def test_an_undone_desk_note_is_taken_back_by_a_sync_by_hand_constructed(make: Any) -> None:
    env = make(_three())
    env.sync()
    assert env.run("crew", "note", KEY_C, "invented: at the desk", "--yes").exit_code == 0
    assert env.run_bare("undo", "--yes").exit_code == 0  # the file holds no note again
    assert env.run("crew", "sync", input="y\n").exit_code == 0
    assert env.rows()[KEY_C].note is None
    assert env.sync().state == "unchanged"


def test_a_desk_rate_keeps_a_note_only_the_database_holds_its_own_constructed(
    make: Any,
) -> None:
    """The file and the row agree on the rating but not on the note the
    database kept for a returned player: the row does not agree, and the
    next sync keeps that note."""
    env = make(_three())
    _dropped(env)
    assert env.run("crew", "note", KEY_B, NOTE_B, "--yes").exit_code == 0
    env.save(book({KEY_A: player(met=4), KEY_B: player(met=10)}))
    assert env.sync().state == "synced"
    assert env.run("crew", "rate", KEY_B, "good", "--yes").exit_code == 0
    assert _own(env, KEY_B) == (0, 1)  # the rating through the file, the note its own
    env.save(book({KEY_A: player(met=5), KEY_B: player(rating="good", met=11)}))
    assert env.sync().state == "synced"
    row = env.rows()[KEY_B]
    assert (row.rating, row.note) == ("good", NOTE_B)


def _desk_marks(env: Env, *, note: bool = True) -> None:
    """B, dropped by the game, rated bad (and noted) at the desk."""
    _dropped(env)
    assert env.run("crew", "rate", KEY_B, "bad", "--yes").exit_code == 0
    if note:
        assert env.run("crew", "note", KEY_B, NOTE_B, "--yes").exit_code == 0


def _returns(env: Env, **marks: Any) -> crewdb.SyncOutcome:
    """The game's list holds B again, with a fresh entry carrying `marks`."""
    env.save(book({KEY_A: player(met=4), KEY_B: player(met=1, **marks)}))
    return env.sync()


KEPT_BOTH = (
    "It kept your rating for 1 player and your note for 1 player the game's list had dropped "
    "and holds again, which the game itself does not show."
)


@pytest.mark.parametrize(
    ("marks", "want", "kept"),
    [
        ({}, ("bad", NOTE_B), (1, 1)),
        ({"rating": "good"}, ("good", NOTE_B), (0, 1)),
        ({"note": "invented: met again"}, ("bad", "invented: met again"), (1, 0)),
        (
            {"rating": "good", "note": "invented: met again"},
            ("good", "invented: met again"),
            (0, 0),
        ),
    ],
    ids=["no-marks", "rated-in-game", "noted-in-game", "both-in-game"],
)
def test_a_desk_mark_survives_the_player_s_return_unless_given_again_in_game_constructed(
    make: Any, marks: dict[str, str], want: tuple[str, str], kept: tuple[int, int]
) -> None:
    env = make(_three())
    _desk_marks(env)
    outcome = _returns(env, **marks)
    assert outcome.state == "synced" and outcome.plan is not None
    assert (outcome.plan.ratings_kept, outcome.plan.notes_kept) == kept
    row = env.rows()[KEY_B]
    assert row.in_game_list and (row.rating, row.note) == want
    if kept == (1, 1):
        assert outcome.words.endswith(KEPT_BOTH)
    if kept == (0, 0):
        assert "It kept" not in outcome.words
        assert _own(env, KEY_B) == (0, 0)  # the row agrees with the file again
    # Kept for good, not for one sync: a later session leaves it as it is.
    env.save(book({KEY_A: player(met=6), KEY_B: player(met=3, **marks)}))
    later = env.sync()
    assert later.state == "synced" and later.plan is not None
    assert (later.plan.ratings_kept, later.plan.notes_kept) == (0, 0)
    assert (env.rows()[KEY_B].rating, env.rows()[KEY_B].note) == want
    for word in (KEY_B, NOTE_B, "Inventedb"):
        assert word not in outcome.words


def test_only_a_kept_rating_is_said_constructed(make: Any) -> None:
    env = make(_three())
    _desk_marks(env, note=False)
    outcome = _returns(env)
    assert outcome.plan is not None and (outcome.plan.ratings_kept, outcome.plan.notes_kept) == (
        1,
        0,
    )
    assert outcome.words.endswith(
        "It kept your rating for 1 player the game's list had dropped and holds again, which "
        "the game itself does not show."
    )


def test_crew_list_says_what_was_kept_constructed(make: Any) -> None:
    env = make(_three())
    _desk_marks(env)
    env.save(book({KEY_A: player(met=4), KEY_B: player(met=1)}))
    listed = env.run("crew", "list")
    assert listed.exit_code == 0, listed.output
    assert KEPT_BOTH in " ".join(listed.stdout.split())


def test_crew_sync_json_says_what_was_kept_constructed(make: Any) -> None:
    env2 = make(_three())
    _desk_marks(env2)
    env2.save(book({KEY_A: player(met=4), KEY_B: player(met=1)}))
    as_json = env2.run("crew", "sync", "--json")
    assert as_json.exit_code == 0, as_json.output
    report = cli.CrewSyncReport.model_validate_json(as_json.stdout)
    assert report.sync is not None and report.sync.plan is not None
    assert (report.sync.plan.ratings_kept, report.sync.plan.notes_kept) == (1, 1)
    assert KEPT_BOTH in report.sync.words


def test_a_kept_desk_rating_can_be_cleared_at_the_desk_constructed(make: Any) -> None:
    env = make(_three())
    _desk_marks(env)
    _returns(env)
    tree, records = tree_state(env.root), guard.history()
    plan = env.run("crew", "rate", KEY_B, "none", input="")
    assert plan.exit_code == 1
    flat = " ".join(plan.stdout.split())
    assert "bad -> none" in flat and "kept only in wowlab's crew database" in flat
    report = _report(env.run("crew", "rate", KEY_B, "none", "--json", input=""))
    assert report.database_only and report.row_sha256 not in (None, "absent")
    done = env.run("crew", "rate", KEY_B, "none", "--yes", f"--expect={_pin(report, 'rate')}")
    assert done.exit_code == 0, done.output
    assert env.rows()[KEY_B].rating is None
    assert tree_state(env.root) == tree and guard.history() == records
    env.save(book({KEY_A: player(met=6), KEY_B: player(met=3)}))
    env.sync()
    assert (env.rows()[KEY_B].rating, env.rows()[KEY_B].note) == (None, NOTE_B)


# ─── doctor ──────────────────────────────────────────────────────────────────


def _doctor(env: Env, *args: str) -> str:
    if "--redact" not in args:
        snap = env.run_bare("snap", "create", "-m", "first", "--root", str(env.root))
        assert snap.exit_code == 0, snap.output
    result = env.run_bare("doctor", "--offline", "--root", str(env.root), *args)
    assert result.exit_code in (0, 1), result.output
    return str(result.stdout) if "--json" in args else result.stdout + result.stderr


def _summaries(text: str) -> list[cli.CrewDatabaseSummary]:
    report = cli.DoctorReport.model_validate_json(text)
    return [d for f in report.flavors for d in f.crew_databases]


def test_doctor_gives_the_database_s_path_size_and_counts_constructed(make: Any) -> None:
    env = make(_three())
    _dropped(env)
    text = " ".join(_doctor(env).split())
    path = str(env.db())
    assert (
        f"Crew database (game account {ACCOUNT}): 3 players, 2 of them no longer in the "
        "game's list, 0 matches;" in text
    ), text
    assert f"at {path}, with 1 copy wowlab keeps beside it." in text
    assert "It is the only copy of what the game's list has dropped." in text
    (summary,) = _summaries(_doctor(env, "--json"))
    assert summary.path == path and summary.size == env.db().stat().st_size > 0
    assert (summary.players, summary.not_in_game_list, summary.matches) == (3, 2, 0)
    assert summary.copies == 1 and summary.reason is None


@pytest.mark.parametrize("form", [(), ("--json",)], ids=["text", "json"])
def test_doctor_redact_names_no_row_no_account_no_login_constructed(
    make: Any, form: tuple[str, ...]
) -> None:
    env = make(_three())
    _dropped(env)
    assert env.run("crew", "note", KEY_B, NOTE_B, "--yes").exit_code == 0
    out = _doctor(env, "--redact", *form)
    assert "Crew database (game account <ACCOUNT>)" in " ".join(out.split()) or form
    # The install's own root is doctor's first line (outside the home folder
    # here, so it stays); the database's lines never carry it.
    for word in (*FORBIDDEN, LOGIN, ACCOUNT):
        assert word.casefold() not in out.casefold(), word
    lines = [line for line in out.splitlines() if "Crew database" in line or "crew" in line]
    assert all(str(env.root) not in line for line in lines)
    if form:
        (summary,) = _summaries(out)
        assert summary.account == "<ACCOUNT>"
        assert summary.path.startswith("<HOME>") and summary.path.endswith("<ACCOUNT>.sqlite")


def test_plain_doctor_names_no_row_and_no_install_it_was_filled_from_constructed(
    make: Any,
) -> None:
    env = make(_three())
    _dropped(env)
    raw = _doctor(env, "--json")
    for word in FORBIDDEN:
        assert word not in raw, word
    assert set(cli.CrewDatabaseSummary.model_fields) == {
        "account",
        "path",
        "size",
        "copies",
        "players",
        "not_in_game_list",
        "matches",
        "reason",
        "world_encounters",
        "world_opponents",
        "world_logs",
    }


def test_doctor_says_a_database_it_cannot_read_without_failing_constructed(make: Any) -> None:
    env = make(_three())
    env.sync()
    env.db().write_bytes(b"not a database, constructed")
    (summary,) = _summaries(_doctor(env, "--json"))
    assert summary.players is None and summary.reason is not None
    text = " ".join(_doctor(env).split())
    assert "could not be read" in text


def test_doctor_without_a_database_says_nothing_of_one_constructed(make: Any) -> None:
    env = make(_three())
    assert "Crew database" not in _doctor(env)
    assert not crewdb.crew_root().exists()  # doctor made nothing


# ─── snap export ─────────────────────────────────────────────────────────────

EXPORT_SENTENCE = (
    "It does not include wowlab's crew database for this game account, which stays on this "
    "computer only."
)


@pytest.mark.parametrize("synced", [True, False], ids=["database", "no-database"])
def test_snap_export_says_it_does_not_include_the_database_constructed(
    make: Any, tmp_path: Path, synced: bool
) -> None:
    env = make(_three())
    if synced:
        env.sync()
    made = env.run_bare("snap", "create", "-m", "x", "--root", str(env.root), "--json")
    assert made.exit_code == 0, made.output
    snap_id = json.loads(made.stdout)["id"]
    out = tmp_path / "exported.zip"
    result = env.run_bare("snap", "export", snap_id, str(out))
    assert result.exit_code == 0, result.output
    flat = " ".join(result.stdout.split())
    assert (EXPORT_SENTENCE in flat) is synced
    for word in FORBIDDEN:
        assert word not in result.stdout


# ─── fix round 2: the rating and the note are each the database's own ───────


def test_an_undone_rating_on_a_player_with_a_kept_note_is_taken_back_constructed(
    make: Any,
) -> None:
    """The security review's sequence: C drops out, gets a desk note,
    returns (the note kept); a rating through the file, undone, synced by
    hand: the file's (none) comes back, and stays."""
    env = make(_three())
    _dropped(env)
    assert env.run("crew", "note", KEY_C, NOTE_B, "--yes").exit_code == 0
    env.save(book({KEY_A: player(met=4), KEY_C: player(met=2)}))
    assert env.sync().state == "synced"
    assert (env.rows()[KEY_C].rating, env.rows()[KEY_C].note) == (None, NOTE_B)
    assert env.run("crew", "rate", KEY_C, "bad", "--yes").exit_code == 0
    assert _own(env, KEY_C) == (0, 1)
    assert env.run_bare("undo", "--yes").exit_code == 0
    assert env.run("crew", "sync", input="y\n").exit_code == 0
    assert (env.rows()[KEY_C].rating, env.rows()[KEY_C].note) == (None, NOTE_B)
    env.save(book({KEY_A: player(met=5), KEY_C: player(met=3)}))
    assert env.sync().state == "synced"
    assert (env.rows()[KEY_C].rating, env.rows()[KEY_C].note) == (None, NOTE_B)


def test_a_field_set_in_game_follows_the_file_beside_a_kept_one_constructed(
    make: Any,
) -> None:
    """B returns with a note written in game beside a kept desk rating: the
    note follows the file from then on (changed, then cleared), the rating
    stays kept."""
    env = make(_three())
    _dropped(env)
    assert env.run("crew", "rate", KEY_B, "bad", "--yes").exit_code == 0
    for note in ("invented: game", "invented: game again", None):
        marks = {"note": note} if note is not None else {}
        env.save(book({KEY_A: player(met=4), KEY_B: player(met=10, **marks)}))
        assert env.sync().state == "synced"
        assert (env.rows()[KEY_B].rating, env.rows()[KEY_B].note) == ("bad", note)
        assert _own(env, KEY_B) == (1, 0)


def test_a_kept_field_given_the_same_value_in_game_agrees_again_constructed(
    make: Any,
) -> None:
    env = make(_three())
    _desk_marks(env)
    _returns(env, rating="bad")  # rated the same in game
    assert _own(env, KEY_B) == (0, 1)
    _returns(env)  # the rating then cleared in game: the file's wins
    assert (env.rows()[KEY_B].rating, env.rows()[KEY_B].note) == (None, NOTE_B)


# A database as schema 1 (M20-01, on main before this change) made it: the
# players table without the two per-field columns, at user_version 1.
_SCHEMA_1_PLAYERS = (
    "CREATE TABLE players ("
    " player_key TEXT PRIMARY KEY NOT NULL, name TEXT NOT NULL, realm TEXT NOT NULL,"
    " class TEXT, level INTEGER, role TEXT, guild TEXT, met INTEGER NOT NULL DEFAULT 0,"
    " first_at INTEGER, last_at INTEGER, where_name TEXT, where_id INTEGER,"
    " rating TEXT CHECK (rating IN ('good', 'bad')), note TEXT,"
    " allied INTEGER NOT NULL DEFAULT 0, faced INTEGER NOT NULL DEFAULT 0,"
    " allied_won INTEGER NOT NULL DEFAULT 0, faced_won INTEGER NOT NULL DEFAULT 0,"
    " pvp_last INTEGER, pvp_map TEXT,"
    " pvp_side TEXT CHECK (pvp_side IN ('with', 'against')),"
    " met_seen INTEGER NOT NULL DEFAULT 0, allied_seen INTEGER NOT NULL DEFAULT 0,"
    " faced_seen INTEGER NOT NULL DEFAULT 0, allied_won_seen INTEGER NOT NULL DEFAULT 0,"
    " faced_won_seen INTEGER NOT NULL DEFAULT 0, in_book INTEGER NOT NULL DEFAULT 0,"
    " marks_changed INTEGER NOT NULL, marks_agreed INTEGER NOT NULL) STRICT"
)


def _as_schema_1(env: Env) -> None:
    """Rewrite the database as schema 1 made it, rows kept: the players
    table without the per-field columns, user_version 1."""
    conn = sqlite3.connect(env.db(), isolation_level=None)
    try:
        # As every wowlab opens it: the rewrite leaves no row's bytes in a
        # free page (the dropped table's pages are zeroed).
        conn.execute("PRAGMA secure_delete = ON")
        cols = (
            "player_key, name, realm, class, level, role, guild, met, first_at, last_at,"
            " where_name, where_id, rating, note, allied, faced, allied_won, faced_won,"
            " pvp_last, pvp_map, pvp_side, met_seen, allied_seen, faced_seen,"
            " allied_won_seen, faced_won_seen, in_book, marks_changed, marks_agreed"
        )
        conn.execute("BEGIN")
        conn.execute("ALTER TABLE players RENAME TO players_new")
        conn.execute(_SCHEMA_1_PLAYERS)
        conn.execute(f"INSERT INTO players ({cols}) SELECT {cols} FROM players_new")
        conn.execute("DROP TABLE players_new")
        # Nor had schema 1 the shuffle mark M20-12 added to matches.
        conn.execute("ALTER TABLE matches DROP COLUMN shuffle")
        # Nor the world tables of M20-18, nor M21-02's log_names.
        for table in ("world_encounter_players", "world_encounters", "world_logs", "log_names"):
            conn.execute(f"DROP TABLE {table}")
        for index in ("last_at", "rating", "faced", "allied"):
            name = "last" if index == "last_at" else index
            conn.execute(f"CREATE INDEX players_{name} ON players ({index})")
        conn.execute("PRAGMA user_version = 1")
        conn.execute("COMMIT")
        names = [r[1] for r in conn.execute("PRAGMA table_info(players)")]
    finally:
        conn.close()
    assert "rating_own" not in names and "note_own" not in names


def test_a_schema_1_database_is_migrated_and_its_own_marks_survive_constructed(
    make: Any,
) -> None:
    """A database made by M20-01 (schema 1): B, dropped, rated and noted at
    the desk (the row its own, row-wide); A agrees. Opened by this wowlab:
    a copy is kept, the columns are added, B's two fields start as its own
    and A's as not. At the next sync B returns with the same note given in
    game and no rating: the note agrees again (no longer its own) and the
    rating is kept; A follows the file."""
    env = make(_three())
    _desk_marks(env)
    _as_schema_1(env)
    rows = env.rows()  # the first open migrates
    assert (rows[KEY_B].rating, rows[KEY_B].note) == ("bad", NOTE_B)
    pre = env.db().with_name(env.db().name + ".before-schema-1")
    assert pre.exists()
    conn = sqlite3.connect(pre)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    finally:
        conn.close()
    conn = sqlite3.connect(env.db())
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == crewdb.SCHEMA == 7
    finally:
        conn.close()
    assert _own(env, KEY_B) == (1, 1) and _own(env, KEY_A) == (0, 0)
    outcome = _returns(env, note=NOTE_B)
    assert outcome.state == "synced" and outcome.plan is not None
    assert (outcome.plan.ratings_kept, outcome.plan.notes_kept) == (1, 0)
    assert (env.rows()[KEY_B].rating, env.rows()[KEY_B].note) == ("bad", NOTE_B)
    assert _own(env, KEY_B) == (1, 0)


def test_a_forget_reaches_a_schema_1_database_and_its_copy_constructed(make: Any) -> None:
    env = make(_three())
    _desk_marks(env)
    _as_schema_1(env)
    env.rows()  # migrates, keeping the schema 1 copy beside it
    assert len(crewdb.copies(FLAVOR, ACCOUNT)) >= 2
    done = env.run("crew", "forget", KEY_B, input="y\n")
    assert done.exit_code == 0, done.output
    folder = env.db().parent
    for f in folder.iterdir():
        assert KEY_B.encode() not in f.read_bytes(), f.name
        assert not f.name.endswith("-journal"), f.name
