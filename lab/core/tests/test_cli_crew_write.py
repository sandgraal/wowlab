"""`wowlab crew forget`, `crew rate` and `crew note` (M19-03, docs/LAB_PLAN.md
§20.3) on constructed crew books. CONSTRUCTED INPUT: every book is built at
run time by `parser/_crew_build.py` (ADR-0036 term 3: no real crew record
enters the repository) inside a copy of the captured install
`fixtures/macos-70888/` in `tmp_path`. Every key is one of the fixed list of
invented keys, or assembled from one at run time; notes are invented.

Nothing here reads or writes a real install: `--root` names the copy, the
process table is a fake one (no client running, or a constructed one), and
the user data directory (the snapshot store and the journal) is redirected
into `tmp_path`.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psutil
import pytest
import typer
from typer.testing import CliRunner

from wowlab_core import cli, crewbook, crewedit, guard, luadata, snapshot, svmerge, ui
from wowlab_core.luaquote import lua_quote

sys.path.insert(0, str(Path(__file__).resolve().parent / "parser"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _crew_build import (
    ACCOUNT,
    FLAVOR,
    KEY_A,
    KEY_B,
    KEY_C,
    NOTE,
    account_file,
    book,
    install,
    player,
)
from _user_dirs import redirect_user_dirs

runner = CliRunner()
SV = f"WTF/Account/{ACCOUNT}/SavedVariables"
LIVE = f"{SV}/WowLab.lua"
BAK = f"{SV}/WowLab.lua.bak"
NOTE_B = "invented note: never on time"  # a second invented note


class FakeProcess:
    """The `psutil.Process` surface `wowlab_core.process` reads."""

    def __init__(self, pid: int, name: str, exe: str) -> None:
        self._pid, self._name, self._exe = pid, name, exe

    @property
    def pid(self) -> int:
        return self._pid

    def name(self) -> str:
        return self._name

    def exe(self) -> str:
        return self._exe

    def cmdline(self) -> list[str]:
        return [self._exe]

    def status(self) -> str:
        return "running"


def _use_table(monkeypatch: pytest.MonkeyPatch, *procs: FakeProcess) -> None:
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: iter(procs))


class Env:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.flavor = root / FLAVOR

    def run(self, *args: str, input: str | None = None) -> Any:
        extra = ["--root", str(self.root), "--flavor", FLAVOR]
        return runner.invoke(cli.app, [*args, *extra], input=input, terminal_width=10_000)

    def ok(self, *args: str) -> Any:
        result = self.run(*args)
        assert result.exit_code == 0, (result.stdout, result.stderr, result.exception)
        return result

    def report(self, *args: str) -> cli.CrewEditReport:
        return cli.CrewEditReport.model_validate_json(self.ok(*args, "--json").stdout)

    def plan(self, *args: str) -> cli.CrewEditReport:
        """A plan run: --json, no --yes, no answer (the declined shape)."""
        result = self.run(*args, "--json", input="")
        assert result.exit_code == 1, (result.stdout, result.stderr)
        assert result.stderr.endswith(f"{cli.CONFIRM_DECLINED}\n")
        return cli.CrewEditReport.model_validate_json(result.stdout)

    def live(self) -> bytes:
        return (self.flavor / LIVE).read_bytes()

    def bak(self) -> Path:
        return self.flavor / BAK


@pytest.fixture
def make(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    monkeypatch.delenv("WOWLAB_WOW_ROOT", raising=False)
    monkeypatch.delenv("WOWLAB_FLAVOR", raising=False)
    home = tmp_path / "home"
    home.mkdir()
    redirect_user_dirs(monkeypatch, home)
    _use_table(monkeypatch, FakeProcess(101, "python3", str(tmp_path / "elsewhere" / "python3")))
    made: list[Env] = []

    def build(live: bytes | None, backup: bytes | None = None) -> Env:
        """An install whose account `WowLab.lua` is `live` (a whole file,
        or the captured one for None) and whose `.bak` is `backup`."""
        folder = tmp_path / f"i{len(made)}"
        folder.mkdir()
        env = Env(install(folder, None))
        if live is not None:
            (env.flavor / LIVE).write_bytes(live)
        if backup is not None:
            env.bak().write_bytes(backup)
        made.append(env)
        return env

    yield build


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _three(**a: Any) -> bytes:
    """A book of three players (constructed); `a` changes KEY_A's entry."""
    return account_file(
        book(
            {KEY_A: player(**a), KEY_B: player(note=NOTE_B), KEY_C: player()},
            current=[KEY_A, KEY_B],
        )
    )


def _store_text(store: Path) -> bytes:
    """Every byte of the snapshot store outside its object files (the
    journal, the manifests, the locks), where no name or note may appear:
    the objects are the snapshot's copies of the files, which hold them."""
    out = b""
    for path in sorted(store.rglob("*")):
        if path.is_file() and "objects" not in path.relative_to(store).parts:
            out += path.read_bytes()
    return out


# ─── forget ──────────────────────────────────────────────────────────────────


def test_forget_plan_writes_nothing_and_says_what_it_does_not_reach_constructed(
    make: Any,
) -> None:
    data = _three(rating="bad", note=NOTE)
    env = make(data, backup=data)
    result = env.run("crew", "forget", KEY_A, input="")
    assert result.exit_code == 1, (result.stdout, result.stderr)
    out = " ".join(result.stdout.split())
    assert f"Forget {KEY_A} from the crew book of game account {ACCOUNT} in {FLAVOR}" in out
    assert f"replace {LIVE}" in out and f"replace {BAK}" in out
    assert (
        "This command first saves a copy that still holds this player, so `wowlab undo` can" in out
    )
    assert "wowlab cannot remove a snapshot yet" in out
    assert "Archives you made with `snap export` keep theirs." in out
    assert str(snapshot.default_store_path()) in out
    assert env.live() == data and env.bak().read_bytes() == data
    assert guard.history() == ()


def test_forget_removes_the_player_from_both_files_and_keeps_every_other_byte_constructed(
    make: Any,
) -> None:
    data = _three(rating="bad", note=NOTE)
    env = make(data, backup=data)
    rep = env.report("crew", "forget", KEY_A, "--yes")
    expected = account_file(book({KEY_B: player(note=NOTE_B), KEY_C: player()}, current=[KEY_B]))
    # The whole file, byte for byte: the captured part, and the book with
    # only the one entry and its place in `current` gone (L4).
    assert env.live() == expected and env.bak().read_bytes() == expected
    for text in (KEY_A, NOTE):
        assert text.encode() not in env.live() and text.encode() not in env.bak().read_bytes()
    assert rep.written and rep.backup == "changed" and rep.players == [KEY_A]
    assert rep.target_sha256 == _sha(data) and rep.written_sha256 == _sha(expected)
    assert rep.backup_sha256 == _sha(data) and rep.backup_written_sha256 == _sha(expected)
    assert rep.not_reached is not None and "first saved a copy" in rep.not_reached
    (record,) = guard.history()
    assert record.id == rep.transaction and record.label == "crew forget (1 player)"
    assert [(p.path, p.before, p.after) for p in record.paths] == [
        (LIVE, _sha(data), _sha(expected)),
        (BAK, _sha(data), _sha(expected)),
    ]
    undone = runner.invoke(cli.app, ["undo", "--yes"])
    assert undone.exit_code == 0, (undone.stdout, undone.stderr)
    assert env.live() == data and env.bak().read_bytes() == data, "undo brings them back"


def test_forget_in_text_says_it_is_done_and_what_it_does_not_reach_constructed(
    make: Any,
) -> None:
    env = make(_three())
    out = " ".join(env.ok("crew", "forget", KEY_A, "--yes").stdout.split())
    assert f"Forgot {KEY_A}: neither WowLab.lua nor WowLab.lua.bak holds the name now." in out
    assert "This command first saved a copy that still holds this player" in out


def test_forget_matches_a_typed_name_loosely_and_shows_the_stored_key_constructed(
    make: Any,
) -> None:
    env = make(_three())
    rep = env.report("crew", "forget", KEY_A.upper().replace("-", " - "), "--yes")
    assert rep.players == [KEY_A]
    assert KEY_A.encode() not in env.live()


def test_the_backup_absent_is_left_absent_constructed(make: Any) -> None:
    env = make(_three())
    rep = env.report("crew", "forget", KEY_A, "--yes")
    assert rep.backup == "absent" and rep.backup_file is None
    assert rep.backup_sha256 == "absent" == rep.backup_written_sha256
    assert not env.bak().exists()
    (record,) = guard.history()
    assert [p.path for p in record.paths] == [LIVE]


def test_a_backup_without_the_player_is_left_alone_constructed(make: Any) -> None:
    older = account_file(book({KEY_C: player()}))
    env = make(_three(), backup=older)
    rep = env.report("crew", "forget", KEY_A, "--yes")
    assert rep.backup == "left_alone" and rep.backup_written_sha256 == _sha(older)
    assert env.bak().read_bytes() == older
    assert [p.path for p in guard.history()[0].paths] == [LIVE]


def test_a_player_only_the_backup_holds_is_forgotten_there_constructed(make: Any) -> None:
    older = _three()
    env = make(account_file(book({KEY_B: player()})), backup=older)
    rep = env.report("crew", "forget", KEY_A, "--yes")
    assert rep.backup == "changed"
    assert KEY_A.encode() not in env.bak().read_bytes()
    assert [p.path for p in guard.history()[0].paths] == [BAK]


@pytest.mark.parametrize(
    "backup",
    [
        pytest.param(lambda: account_file(None) + b"\r\nX = function() end\r\n", id="not-data"),
        pytest.param(lambda: account_file(book({KEY_A: player()}, schema=7)), id="schema-7"),
        pytest.param(
            lambda: account_file(None) + crewbook.CREW_VARIABLE.encode() + b' = "x"\r\n',
            id="not-a-table",
        ),
    ],
)
def test_a_backup_that_cannot_be_read_refuses_and_changes_nothing_constructed(
    make: Any, backup: Any
) -> None:
    data, old = _three(), backup()
    env = make(data, backup=old)
    result = env.run("crew", "forget", KEY_A, "--yes")
    assert result.exit_code == 1, (result.stdout, result.stderr)
    assert BAK in result.stderr and "nothing was changed" in result.stderr
    assert env.live() == data and env.bak().read_bytes() == old
    assert guard.history() == ()


def test_forget_all_empties_both_lists_and_keeps_the_rest_of_the_book_constructed(
    make: Any,
) -> None:
    def full(on: bool) -> bytes:
        return account_file(
            book(
                {KEY_A: player(rating="good"), KEY_B: player(note=NOTE), KEY_C: player()},
                on=on,
                current=[KEY_A, KEY_C],
            )
        )

    env = make(full(False), backup=full(False))
    rep = env.report("crew", "forget", "--all", "--yes")
    empty = account_file(book({}, on=False))
    assert env.live() == empty and env.bak().read_bytes() == empty, "on stays false"
    assert sorted(rep.players) == sorted([KEY_A, KEY_B, KEY_C])
    assert guard.history()[0].label == "crew forget (3 players)"
    again = env.ok("crew", "forget", "--all", "--yes")
    assert "nothing to forget" in again.stdout
    assert len(guard.history()) == 1


def test_forget_refuses_when_the_name_is_still_elsewhere_in_the_file_constructed(
    make: Any,
) -> None:
    data = account_file(book({KEY_A: player(), KEY_B: player(note=f"same group as {KEY_A}")}))
    env = make(data)
    result = env.run("crew", "forget", KEY_A, "--yes")
    assert result.exit_code == 1 and "Nothing was changed." in result.stderr
    assert env.live() == data and guard.history() == ()


def test_forget_with_a_comment_beside_the_entry_leaves_no_trace_constructed(make: Any) -> None:
    """A comment after the entry (constructed: the client writes none in a
    keyed table) goes with it, whatever it says."""
    data = _three()
    at = data.index(b"},", data.index(f'["{KEY_A}"] = {{'.encode()))
    data = data[: at + 2] + f" -- {KEY_A}".encode() + data[at + 2 :]
    env = make(data)
    env.report("crew", "forget", KEY_A, "--yes")
    assert KEY_A.encode() not in env.live()
    assert crewbook.parse_book(env.live()).players[0].key == KEY_B


# ─── rate and note ───────────────────────────────────────────────────────────


def test_rate_note_and_their_removal_are_the_slash_command_edits_constructed(
    make: Any,
) -> None:
    env = make(_three())
    rated = env.report("crew", "rate", KEY_A, "good", "--yes")
    assert env.live() == _three(rating="good")
    assert (rated.field, rated.before, rated.after) == ("rating", None, "good")
    env.ok("crew", "note", KEY_A, f"  {NOTE}  ", "--yes")
    assert env.live() == _three(rating="good", note=NOTE)
    env.ok("crew", "rate", KEY_A, "bad", "--yes")
    assert env.live() == _three(rating="bad", note=NOTE)
    env.ok("crew", "note", KEY_A, NOTE_B, "--yes")
    assert env.live() == _three(rating="bad", note=NOTE_B)
    env.ok("crew", "rate", KEY_A, "none", "--yes")
    env.ok("crew", "note", KEY_A, "", "--yes")
    assert env.live() == _three()
    assert [r.label for r in guard.history()] == [
        "crew rate (1 player)",
        "crew note (1 player)",
        "crew rate (1 player)",
        "crew note (1 player)",
        "crew rate (1 player)",
        "crew note (1 player)",
    ]
    undone = runner.invoke(cli.app, ["undo", "--yes"])
    assert undone.exit_code == 0, (undone.stdout, undone.stderr)
    assert env.live() == _three(note=NOTE_B)


def test_a_note_is_written_with_the_shared_quoting_helper_constructed(make: Any) -> None:
    text = 'say "hi" \\ to the tank, é and 漢字'
    env = make(_three())
    env.ok("crew", "note", KEY_A, text, "--yes")
    assert lua_quote(text.encode()) in env.live()
    (read,) = [p for p in crewbook.parse_book(env.live()).players if p.key == KEY_A]
    assert read.note == text


def test_the_same_rating_again_changes_nothing_constructed(make: Any) -> None:
    data = _three(rating="good")
    env = make(data)
    result = env.ok("crew", "rate", KEY_A, "good")
    assert "already good: nothing was changed" in result.stdout
    assert env.live() == data and guard.history() == ()


def test_rate_and_note_plans_write_nothing_constructed(make: Any) -> None:
    data = _three(note=NOTE)
    env = make(data)
    plan = env.run("crew", "note", KEY_A, NOTE_B, input="")
    assert plan.exit_code == 1
    assert f'"{NOTE}" -> "{NOTE_B}"' in plan.stdout and f"replace  {LIVE}" in plan.stdout
    assert env.live() == data and guard.history() == ()


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("x" * 201, id="201-bytes"),
        pytest.param("漢" * 67, id="201-bytes-of-cjk"),
        pytest.param("tab\there", id="tab"),
        pytest.param("line\nbreak", id="line-break"),
        pytest.param("del\x7f", id="del"),
        pytest.param("c1\x85", id="c1"),
        pytest.param("esc\x1b[31m", id="esc"),
        pytest.param("|cffff0000red|r", id="pipe"),
        pytest.param("bad \udcff byte", id="not-utf8"),
    ],
)
def test_a_note_over_the_limit_or_with_a_control_character_or_pipe_is_refused_constructed(
    make: Any, text: str
) -> None:
    data = _three()
    env = make(data)
    result = env.run("crew", "note", KEY_A, text, "--yes")
    assert result.exit_code == 2, (result.stdout, result.stderr)
    assert "nothing was changed" in result.stderr
    assert env.live() == data and guard.history() == ()


def test_a_note_of_exactly_200_bytes_after_trimming_is_kept_constructed(make: Any) -> None:
    text = "漢" * 66 + "xy"  # 198 + 2 bytes
    assert len(text.encode()) == crewedit.NOTE_MAX_BYTES
    env = make(_three())
    env.ok("crew", "note", KEY_A, f"   {text} ", "--yes")
    assert env.live() == _three(note=text)


def test_an_unread_entry_is_not_rated_constructed(make: Any) -> None:
    data = account_file(book({KEY_A: player(level="sixty"), KEY_B: player()}))
    env = make(data)
    result = env.run("crew", "rate", KEY_A, "good", "--yes")
    assert result.exit_code == 1 and "could not be read" in result.stderr
    assert env.live() == data


# ─── refusals ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "args",
    [
        ("crew", "forget", KEY_A),
        ("crew", "forget", "--all"),
        ("crew", "rate", KEY_A, "bad"),
        ("crew", "note", KEY_A, NOTE),
    ],
)
def test_refused_while_the_client_runs_constructed(
    make: Any, monkeypatch: pytest.MonkeyPatch, args: tuple[str, ...]
) -> None:
    data = _three()
    env = make(data, backup=data)
    exe = env.flavor / "World of Warcraft Classic.app" / "Contents" / "MacOS" / "World of Warcraft"
    _use_table(monkeypatch, FakeProcess(7300, "World of Warcraft", str(exe)))
    result = env.run(*args, "--yes")
    assert result.exit_code == 3, (result.stdout, result.stderr)
    assert "write gate" in result.stderr
    assert env.live() == data and env.bak().read_bytes() == data
    assert guard.history() == ()


@pytest.mark.parametrize("command", ["forget", "rate", "note"])
def test_two_matching_keys_refuse_and_list_the_stored_keys_constructed(
    make: Any, command: str
) -> None:
    other = KEY_A.lower()  # a second key that folds the same (assembled at run time)
    data = account_file(book({KEY_A: player(), other: player()}))
    env = make(data)
    extra = {"forget": (), "rate": ("good",), "note": (NOTE,)}[command]
    result = env.run("crew", command, KEY_A.upper(), *extra, "--yes")
    assert result.exit_code == 2, (result.stdout, result.stderr)
    assert KEY_A in result.stderr and other in result.stderr
    assert env.live() == data and guard.history() == ()


@pytest.mark.parametrize("command", ["forget", "rate", "note"])
def test_an_unknown_schema_is_refused_constructed(make: Any, command: str) -> None:
    data = account_file(book({KEY_A: player()}, schema=2))
    env = make(data)
    extra = {"forget": (), "rate": ("good",), "note": (NOTE,)}[command]
    result = env.run("crew", command, KEY_A, *extra, "--yes")
    assert result.exit_code == 1, (result.stdout, result.stderr)
    assert "book version 2" in result.stderr
    assert env.live() == data and guard.history() == ()


@pytest.mark.parametrize(
    "args", [("forget", KEY_A), ("forget", "--all"), ("rate", KEY_A, "good"), ("note", KEY_A, NOTE)]
)
def test_no_command_creates_the_book_constructed(make: Any, args: tuple[str, ...]) -> None:
    env = make(None)
    data = env.live()
    result = env.run("crew", *args, "--yes")
    assert result.exit_code == 1 and "never starts a crew book" in result.stderr
    assert env.live() == data and guard.history() == ()


def test_a_player_not_in_the_book_is_an_error_constructed(make: Any) -> None:
    env = make(_three())
    result = env.run("crew", "forget", "Nobody-Fakerealm", "--yes")
    assert result.exit_code == 1 and "no player" in result.stderr


def test_forget_needs_a_name_or_all_but_not_both(make: Any) -> None:
    env = make(_three())
    assert env.run("crew", "forget", "--yes").exit_code == 2
    assert env.run("crew", "forget", KEY_A, "--all", "--yes").exit_code == 2


def test_rate_takes_good_bad_or_none(make: Any) -> None:
    env = make(_three())
    assert env.run("crew", "rate", KEY_A, "great", "--yes").exit_code == 2


# ─── --expect ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "args",
    [("forget", KEY_A), ("forget", "--all"), ("rate", KEY_A, "bad"), ("note", KEY_A, NOTE)],
)
def test_the_plan_s_pin_lets_the_confirmed_run_write_constructed(
    make: Any, args: tuple[str, ...]
) -> None:
    data = _three()
    env = make(data, backup=data)
    plan = env.plan("crew", *args)
    fields = cli.EXPECT_FIELDS[("crew", args[0])]
    pin = ":".join(str(getattr(plan, f)) for f in fields)
    done = env.report("crew", *args, "--yes", f"--expect={pin}")
    assert done.written and done.written_sha256 == plan.written_sha256
    assert _sha(env.live()) == plan.written_sha256


@pytest.mark.parametrize(
    "args",
    [("forget", KEY_A), ("forget", "--all"), ("rate", KEY_A, "bad"), ("note", KEY_A, NOTE)],
)
def test_a_file_changed_between_plan_and_confirm_is_refused_constructed(
    make: Any, args: tuple[str, ...]
) -> None:
    data = _three()
    env = make(data)
    plan = env.plan("crew", *args)
    pin = ":".join(str(getattr(plan, f)) for f in cli.EXPECT_FIELDS[("crew", args[0])])
    # A session played in between (constructed): KEY_B's entry now holds a rating.
    changed = _three().replace(
        f'["note"] = "{NOTE_B}",'.encode(),
        f'["note"] = "{NOTE_B}",\r\n["rating"] = "bad",'.encode(),
    )
    assert changed != data
    (env.flavor / LIVE).write_bytes(changed)
    result = env.run("crew", *args, "--yes", f"--expect={pin}")
    assert result.exit_code == 3, (result.stdout, result.stderr)
    assert env.live() == changed and guard.history() == ()


def test_a_backup_changed_between_plan_and_confirm_is_refused_constructed(make: Any) -> None:
    data = _three()
    env = make(data, backup=account_file(book({KEY_C: player()})))
    plan = env.plan("crew", "forget", KEY_A)
    pin = ":".join(str(getattr(plan, f)) for f in cli.EXPECT_FIELDS[("crew", "forget")])
    assert plan.backup == "left_alone"
    env.bak().write_bytes(data)  # the backup now holds KEY_A too
    result = env.run("crew", "forget", KEY_A, "--yes", f"--expect={pin}")
    assert result.exit_code == 3
    assert env.live() == data and env.bak().read_bytes() == data


@pytest.mark.parametrize(
    ("command", "pin"),
    [
        ("forget", "a" * 64 + ":" + "b" * 64),
        ("forget", "absent:" + "a" * 64 + ":absent:absent"),
        ("rate", "a" * 64 + ":absent"),
        ("rate", "A" * 64 + ":" + "b" * 64),
        ("note", "a" * 64),
    ],
)
def test_a_pin_that_is_not_the_report_s_is_a_usage_error(make: Any, command: str, pin: str) -> None:
    env = make(_three())
    extra = {"forget": (KEY_A,), "rate": (KEY_A, "good"), "note": (KEY_A, NOTE)}[command]
    result = env.run("crew", command, *extra, "--yes", f"--expect={pin}")
    assert result.exit_code == 2, (result.stdout, result.stderr)


def test_the_expect_fields_are_fields_of_the_report() -> None:
    assert cli.EXPECT_FIELDS[("crew", "forget")] == (
        "target_sha256",
        "written_sha256",
        "backup_sha256",
        "backup_written_sha256",
    )
    for command in ("rate", "note"):
        assert cli.EXPECT_FIELDS[("crew", command)] == ("target_sha256", "written_sha256")
    for fields in (cli.EXPECT_FIELDS[("crew", c)] for c in ("forget", "rate", "note")):
        assert set(fields) <= set(cli.CrewEditReport.model_fields)


# ─── what may never name a player or a note ──────────────────────────────────


def test_journal_snapshot_message_and_snap_list_name_no_player_and_no_note_constructed(
    make: Any,
) -> None:
    data = _three(note=NOTE)
    env = make(data, backup=data)
    env.ok("crew", "rate", KEY_B, "bad", "--yes")
    env.ok("crew", "note", KEY_C, NOTE_B + " again", "--yes")
    env.ok("crew", "forget", KEY_A, "--yes")
    env.ok("crew", "forget", "--all", "--yes")
    records = guard.history()
    assert [r.label for r in records] == [
        "crew rate (1 player)",
        "crew note (1 player)",
        "crew forget (1 player)",
        "crew forget (2 players)",
    ]
    secrets = [KEY_A, KEY_B, KEY_C, NOTE, NOTE_B, "Fakerealm", "Invented"]
    listed = ""
    for args in (["snap", "list"], ["snap", "list", "--json"]):
        result = runner.invoke(cli.app, args, terminal_width=10_000)
        assert result.exit_code == 0, (result.stdout, result.stderr)
        listed += result.stdout
    stored = _store_text(snapshot.default_store_path())
    assert b"crew forget (1 player)" in stored, "the store holds the labels"
    store = snapshot.SnapshotStore()
    messages = " ".join(m.label for m in store.list())
    for text in secrets:
        assert text not in listed, text
        assert text.encode() not in stored, text
        assert text not in messages, text
        assert all(text not in r.label for r in records), text


def test_hostile_keys_reach_the_terminal_escaped_constructed(make: Any) -> None:
    hostile = KEY_A.replace("-", "\x1b[31m" + chr(0x202E) + "-")
    env = make(account_file(book({hostile: player(), KEY_B: player()})))
    result = env.run("crew", "forget", hostile, input="")
    assert result.exit_code == 1
    assert "\\x1b[31m" in result.stdout and "\x1b" not in result.stdout
    assert chr(0x202E) not in result.stdout


# ─── the UI, the help, the command tree ──────────────────────────────────────


def test_the_write_commands_are_in_the_ui_s_confirm_step_with_their_pin() -> None:
    leaves = ui._read_tree(typer.main.get_command(cli.app))
    for command in ("forget", "rate", "note"):
        leaf = leaves[("crew", command)]
        assert leaf.excluded is None and leaf.confirm == "declined", command
        assert leaf.pin == cli.EXPECT_FIELDS[("crew", command)]


def test_the_ui_offers_confirm_on_a_forget_plan_with_no_backup_constructed(make: Any) -> None:
    env = make(_three())
    plan = env.plan("crew", "forget", KEY_A)
    leaf = ui._read_tree(typer.main.get_command(cli.app))[("crew", "forget")]
    offer = ui._declined_offer(leaf, json.loads(plan.model_dump_json()))
    assert offer.pin == f"{plan.target_sha256}:{plan.written_sha256}:absent:absent"


@pytest.mark.parametrize("leaf", ["forget", "rate", "note"])
def test_help_names_no_project_vocabulary(leaf: str) -> None:
    result = runner.invoke(cli.app, ["crew", leaf, "--help"], terminal_width=200)
    assert result.exit_code == 0
    assert not re.search(
        r"M1\d-\d+|ADR-\d+|\bL[1-8]\b|\[verify\]|docs/|Crew\w*Report", result.stdout
    )


def test_the_command_reference_lists_the_three_with_their_model() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
    import gen_command_reference as gen

    named = {n: gen.json_models(c) for n, c in gen.iter_leaves(gen.root_command())}
    for leaf in ("forget", "rate", "note"):
        assert named.get(f"wowlab crew {leaf}") == [cli.CrewEditReport.__name__]


# ─── the shared quoting helper ───────────────────────────────────────────────


def test_the_quoting_helper_moved_out_of_svmerge() -> None:
    assert not hasattr(svmerge, "_lua_quote")
    assert svmerge.lua_quote is lua_quote


def test_the_quoting_helper_loads_back_as_every_byte() -> None:
    data = bytes(range(256))
    doc = luadata.parse(b"X = " + lua_quote(data) + b"\n")
    (assignment,) = doc.assignments
    assert isinstance(assignment.value, luadata.LuaString)
    assert assignment.value.data == data


# ─── addon remove's note ─────────────────────────────────────────────────────


def test_addon_remove_says_the_crew_book_stays_and_how_to_empty_it() -> None:
    from wowlab_core import addoninstall

    note = addoninstall.SAVED_VARIABLES_NOTE
    assert "the crew book if you turned it on" in note
    assert "`wowlab crew forget --all` empties the crew book in both files." in note
    result = runner.invoke(cli.app, ["addon", "remove", "--help"], terminal_width=200)
    assert "`wowlab crew forget --all` empties the crew book." in " ".join(result.stdout.split())


# ─── crewedit on its own ─────────────────────────────────────────────────────


def test_forget_takes_a_key_out_of_a_current_list_too_constructed() -> None:
    """`current` as a list of keys (constructed: the addon writes a set)."""
    data = account_file(book({KEY_A: player(), KEY_B: player()}))
    listed = data.replace(
        b'["current"] = {\r\n}', f'["current"] = {{\r\n"{KEY_A}",\r\n"{KEY_B}",\r\n}}'.encode()
    )
    assert listed != data
    out = crewedit.forget(listed, [KEY_A])
    assert KEY_A.encode() not in out
    loaded = luadata.parse(out).to_python()[crewbook.CREW_VARIABLE]
    assert isinstance(loaded, dict) and loaded["current"] == [KEY_B]


def test_a_field_held_twice_keeps_only_the_one_written_constructed() -> None:
    data = _three(note=NOTE)
    twice = data.replace(
        f'["note"] = "{NOTE}",'.encode(), f'["note"] = "x",\r\n["note"] = "{NOTE}",'.encode()
    )
    out = crewedit.set_field(twice, KEY_A, "note", NOTE_B)
    assert out == _three(note=NOTE_B)


def test_an_entry_with_no_trailing_comma_takes_a_new_field_constructed() -> None:
    data = _three()
    at = data.index(b'["where_id"] = 2291,', data.index(f'["{KEY_A}"] = {{'.encode()))
    bare = data[:at] + b'["where_id"] = 2291' + data[at + len(b'["where_id"] = 2291,') :]
    out = crewedit.set_field(bare, KEY_A, "rating", "good")
    assert out == _three(rating="good")


def test_a_book_set_twice_is_not_changed_constructed() -> None:
    data = _three() + book({KEY_C: player()})
    with pytest.raises(crewedit.CrewEditError):
        crewedit.forget(data, [KEY_A])
