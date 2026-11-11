"""`doctor` names the file a rollback left alone (M16-84).

M16-71 made a most recent journal record in `rollback_incomplete` a
`doctor` problem; it could only say "a file", since the paths were only in
the record's free-text note. The write gate now lists them on the record
(`HistoryRecord.paths_left`, graded in `test_guard_left_alone_m16_84.py`),
and the problem names each one, relative to the flavor folder and with the
flavor folder named once, grouped by what the file holds as doctor reads it
now: still wowlab's change, changed by another program since, already what
was there before, unknown, or never written by wowlab (a refused path,
which undo leaves alone), and whether it is there at all. Names are escaped
as they print, after `--redact` has seen them as stored; `wowlab undo
--expect <id>` stays last, except for a change that wrote nothing, where
the problem says there is nothing for undo to put back.

The install is the captured tree copied into `tmp_path` as `test_cli.py`
builds it, the user data directory is redirected and the process table is a
fake one (constructed: no client is running). Nothing reads or writes a real
install.

Constructed (L8, boundary): the failures. A real write-gate transaction
writes, another program (this test) then changes what it wrote, or the
system (an injected `PermissionError`) refuses the rollback, and the
caller's body raises. The journal is not edited.
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

import json
import logging
import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from test_cli import (
    ACCOUNT,
    FLAVOR,
    flavor,  # noqa: F401
    idle,  # noqa: F401  (autouse: no client in the process table)
    ok,
    replayed,  # noqa: F401  (autouse: recorded game data only)
    root,  # noqa: F401
    run,
    user_data,  # noqa: F401
)

from wowlab_core import cli, guard, install, redact

CONFIG = "WTF/Config.wtf"
SAVED = f"WTF/Account/{ACCOUNT}/SavedVariables/Constructed.lua"
CHANGED = b"constructed by wowlab\n"
OTHER = b"constructed by another program\n"
OTHER_AGAIN = b"constructed by another program, again\n"

THE_GAME = "such as the game when it saves your settings or addon data"
CAUSE = f"another program, {THE_GAME}"
LEAD = "the last change wowlab made failed, and putting the files back did not finish"
TAIL = (
    "To put back what was there before the change, close the game and run this; it puts "
    "back every file the change wrote or deleted, including anything the game has saved to "
    "them since, but the undo keeps a copy and running wowlab undo again next, before you "
    "play, brings them back: wowlab undo --expect "
)
NOTHING_LEAD = "the last change wowlab made failed before it changed any file"
NOTHING = "wowlab changed no file, so there is nothing for undo to put back."

# A file name holding U+202E, which reverses the text after it: the gate
# allows it (a real file may be named so), so it must print escaped.
BIDI = f"WTF/Account/{ACCOUNT}/SavedVariables/odd\u202eaul.lua"
BIDI_PRINTED = f"WTF/Account/{ACCOUNT}/SavedVariables/odd\\xe2\\x80\\xaeaul.lua"

# Folders only the journal names once the player has removed them: the
# transaction created them, so no snapshot holds them either.
JOURNAL_ACCOUNT = "Labjournalacct"
JOURNAL_REALM = "Labjournalrealm"
JOURNAL_CHARACTER = "Labjournalchar"
JOURNAL_ONLY = (
    f"WTF/Account/{JOURNAL_ACCOUNT}/{JOURNAL_REALM}/{JOURNAL_CHARACTER}/"
    "SavedVariables/Constructed.lua"
)


class _StopError(Exception):
    """The caller's failure that makes the gate roll back (constructed)."""


def _discovered(root: Path) -> Any:
    (discovered,) = install.read_install(root).flavors
    return discovered


def _fail(root: Path, body: Callable[[Any], None]) -> str:
    """Run `body(tx)` in a transaction that then fails. The record's id; it
    must be `rollback_incomplete`."""
    with pytest.raises(_StopError):  # noqa: SIM117 - the exit is what rolls back
        with guard.transaction(_discovered(root), label="constructed: fails") as tx:
            body(tx)
            raise _StopError
    last = guard.history()[-1]
    assert last.state == "rollback_incomplete"
    return last.id


def _fail_leaving(root: Path, flavor: Path, *rels: str) -> str:
    """A change that wrote `rels` and failed after another program rewrote
    each of them, so its rollback left them all. The record's id."""

    def body(tx: Any) -> None:
        for rel in rels:
            tx.write(rel, CHANGED)
        for rel in rels:
            (flavor / rel).write_bytes(OTHER)  # another program, mid-change

    record_id = _fail(root, body)
    assert sorted(guard.history()[-1].paths_left) == sorted(rels)
    return record_id


def _fail_refused_rollback(
    root: Path, flavor: Path, rel: str, *others: str, delete: bool = False
) -> str:
    """A change that wrote (or, with `delete`, deleted) `rel`, and wrote
    `others`, which another program then rewrote, and failed; the system
    refused the rollback's replace (or create) of `rel`, so it still holds
    the change. The record's id."""
    target = flavor / rel
    real_replace, real_rename = os.replace, os.rename

    def refusing(src: Any, dst: Any, *args: Any, **kwargs: Any) -> None:
        if Path(os.fspath(dst)) == target:
            raise PermissionError(13, "constructed: permission denied", str(target))
        real_replace(src, dst, *args, **kwargs)

    def refusing_rename(src: Any, dst: Any, *args: Any, **kwargs: Any) -> None:
        # How the gate creates a missing file on Windows.
        if Path(os.fspath(dst)) == target:
            raise PermissionError(13, "constructed: permission denied", str(target))
        real_rename(src, dst, *args, **kwargs)

    with pytest.MonkeyPatch.context() as patched:  # noqa: SIM117 - the exit is what rolls back
        with pytest.raises(_StopError):
            with guard.transaction(_discovered(root), label="constructed: fails") as tx:
                if delete:
                    tx.delete(rel)
                else:
                    tx.write(rel, CHANGED)
                for other in others:
                    tx.write(other, CHANGED)
                    (flavor / other).write_bytes(OTHER)
                patched.setattr(os, "replace", refusing)
                patched.setattr(os, "rename", refusing_rename)
                raise _StopError
    if delete:
        assert not target.exists(), "positive control: the rollback was refused"
    else:
        assert target.read_bytes() == CHANGED, "positive control: the rollback was refused"
    last = guard.history()[-1]
    assert last.state == "rollback_incomplete"
    return last.id


def _fail_with_a_refused_write(root: Path, flavor: Path, *, also: bool = True) -> str:
    """The change writes Config.wtf's neighbour (unless not `also`); another
    program changes Config.wtf after the pre-write snapshot, so the gate
    refuses to write it; the program changes it again before the exit.
    wowlab never wrote Config.wtf. The record's id."""
    config = flavor / CONFIG
    with pytest.raises(guard.GuardError):  # noqa: SIM117 - the exit is what rolls back
        with guard.transaction(_discovered(root), label="constructed: refused") as tx:
            if also:
                tx.write(SAVED, CHANGED)
            config.write_bytes(OTHER)
            with pytest.raises(guard.ChangedSinceSnapshotError):
                tx.write(CONFIG, CHANGED)
            config.write_bytes(OTHER_AGAIN)
    last = guard.history()[-1]
    assert last.state == "rollback_incomplete"
    assert CONFIG not in {p.path for p in last.paths}
    assert last.paths_left == (CONFIG,)
    return last.id


def _problem(text: str) -> str:
    """The text output's first problem line."""
    assert "Problems:\n" in text, text
    return text.split("Problems:\n", 1)[1].splitlines()[0].strip()


def _json(*args: str) -> dict[str, Any]:
    result = run("doctor", "--json", *args)
    assert result.exit_code == 1, result.output
    data: dict[str, Any] = json.loads(result.stdout)
    return data


def _json_problem(record_id: str, data: dict[str, Any]) -> str:
    (problem,) = [p for p in data["problems"] if f"(record {record_id})" in p]
    assert isinstance(problem, str)
    return problem


def _left(data: dict[str, Any]) -> list[tuple[str, str, bool | None]]:
    return [
        (p["path"], p["now"], p["exists"]) for p in data["store"]["last_transaction"]["paths_left"]
    ]


def _both(record_id: str) -> tuple[str, str]:
    """The problem as the text output prints it and as `--json` holds it."""
    text = run("doctor")
    assert text.exit_code == 1, text.output
    return _problem(text.stdout), _json_problem(record_id, _json())


def _holds_non_utf8_names(where: Path) -> str | None:
    """None when the file system under `where` keeps a name holding the
    byte 0xFF as given; else why it does not (APFS refuses one, Windows
    names are UTF-16). A real probe: one file is created and listed."""
    folder = os.fsencode(where)
    probe = os.path.join(folder, b"probe-\xff")  # noqa: PTH118 - a bytes name
    try:
        fd = os.open(probe, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except (OSError, UnicodeError) as exc:
        return f"this file system cannot hold a non-UTF-8 name ({type(exc).__name__})"
    os.close(fd)
    try:
        if b"probe-\xff" not in os.listdir(folder):  # noqa: PTH208 - bytes names
            return "this file system stores a non-UTF-8 name under another name"
    finally:
        os.unlink(probe)  # noqa: PTH108 - a bytes name
    return None


# ─── the sentences ───────────────────────────────────────────────────────────


def test_a_file_another_program_changed_since_constructed(root: Path, flavor: Path) -> None:
    record_id = _fail_leaving(root, flavor, CONFIG)
    expected = (
        f"{LEAD} (record {record_id}): in {FLAVOR}, {CAUSE}, has changed WTF/Config.wtf "
        f"since wowlab wrote it. {TAIL}{record_id}"
    )
    assert _both(record_id) == (expected, expected)
    data = _json()
    assert _left(data) == [(CONFIG, "changed_since", True)]
    assert data["store"]["last_transaction"]["flavor_folder"] == FLAVOR


def test_two_files_another_program_changed_since_constructed(root: Path, flavor: Path) -> None:
    record_id = _fail_leaving(root, flavor, SAVED, CONFIG)
    expected = (
        f"{LEAD} (record {record_id}): in {FLAVOR}, {CAUSE}, has changed {SAVED} and {CONFIG} "
        f"since wowlab wrote them. {TAIL}{record_id}"
    )
    assert _both(record_id) == (expected, expected)
    # Sorted, so the same record always prints the same line.
    assert _left(_json()) == [(SAVED, "changed_since", True), (CONFIG, "changed_since", True)]


def test_a_file_another_program_deleted_since_constructed(root: Path, flavor: Path) -> None:
    def body(tx: Any) -> None:
        tx.write(CONFIG, CHANGED)
        (flavor / CONFIG).unlink()  # another program, mid-change

    record_id = _fail(root, body)
    expected = (
        f"{LEAD} (record {record_id}): in {FLAVOR}, {CAUSE}, has deleted WTF/Config.wtf "
        f"since wowlab wrote it. {TAIL}{record_id}"
    )
    assert _both(record_id) == (expected, expected)
    assert _left(_json()) == [(CONFIG, "changed_since", False)]


def test_a_file_the_system_refused_to_put_back_still_holds_the_change_constructed(
    root: Path, flavor: Path
) -> None:
    record_id = _fail_refused_rollback(root, flavor, CONFIG)
    expected = (
        f"{LEAD} (record {record_id}): in {FLAVOR}, WTF/Config.wtf still holds wowlab's "
        f"change. {TAIL}{record_id}"
    )
    assert _both(record_id) == (expected, expected)
    assert _left(_json()) == [(CONFIG, "wowlab_change", True)]


def test_a_deleted_file_the_system_refused_to_put_back_is_still_deleted_constructed(
    root: Path, flavor: Path
) -> None:
    record_id = _fail_refused_rollback(root, flavor, CONFIG, delete=True)
    expected = (
        f"{LEAD} (record {record_id}): in {FLAVOR}, WTF/Config.wtf is still deleted, as "
        f"wowlab's change left it. {TAIL}{record_id}"
    )
    assert _both(record_id) == (expected, expected)
    assert _left(_json()) == [(CONFIG, "wowlab_change", False)]


def test_files_in_different_states_are_named_in_groups_constructed(
    root: Path, flavor: Path
) -> None:
    record_id = _fail_refused_rollback(root, flavor, CONFIG, SAVED)
    expected = (
        f"{LEAD} (record {record_id}): in {FLAVOR}, WTF/Config.wtf still holds wowlab's "
        f"change; {CAUSE}, has changed {SAVED} since wowlab wrote it. {TAIL}{record_id}"
    )
    assert _both(record_id) == (expected, expected)


def test_a_file_already_back_as_before_constructed(root: Path, flavor: Path) -> None:
    before = (flavor / CONFIG).read_bytes()
    record_id = _fail_leaving(root, flavor, CONFIG)
    (flavor / CONFIG).write_bytes(before)  # the other program put it back since
    expected = (
        f"{LEAD} (record {record_id}): in {FLAVOR}, WTF/Config.wtf already holds what was "
        f"there before the change. {TAIL}{record_id}"
    )
    assert _both(record_id) == (expected, expected)
    assert _left(_json()) == [(CONFIG, "before_change", True)]


def test_a_created_file_since_removed_is_gone_again_constructed(root: Path, flavor: Path) -> None:
    """wowlab created the file; another program changed it, so the rollback
    left it; then the file was removed: it is gone again, as before."""
    record_id = _fail_leaving(root, flavor, SAVED)
    (flavor / SAVED).unlink()
    expected = (
        f"{LEAD} (record {record_id}): in {FLAVOR}, {SAVED} is gone again, as before the "
        f"change. {TAIL}{record_id}"
    )
    assert _both(record_id) == (expected, expected)
    assert _left(_json()) == [(SAVED, "before_change", False)]


def test_a_file_doctor_cannot_read_constructed(
    root: Path, flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record_id = _fail_leaving(root, flavor, CONFIG)
    monkeypatch.setattr(cli, "_hash_now", lambda root, path: (False, None))
    expected = (
        f"{LEAD} (record {record_id}): in {FLAVOR}, wowlab could not put back WTF/Config.wtf, "
        f"which holds either wowlab's change or what {CAUSE}, has written to it since. "
        f"{TAIL}{record_id}"
    )
    assert _both(record_id) == (expected, expected)
    assert _left(_json()) == [(CONFIG, "unknown", None)]


def test_a_record_of_a_flavor_doctor_did_not_find_is_not_read_constructed(
    root: Path, flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The journal's flavor path is never opened: a record whose flavor is
    not one of the install's is said as unknown."""
    record_id = _fail_leaving(root, flavor, CONFIG)
    real = guard.history

    def elsewhere(**kwargs: Any) -> Any:
        *rest, last = real(**kwargs)
        moved = last.model_copy(update={"flavor_path": str(root.parent / "elsewhere" / FLAVOR)})
        return (*rest, moved)

    monkeypatch.setattr(guard, "history", elsewhere)
    monkeypatch.setattr(cli, "_hash_now", lambda root, path: pytest.fail("read the install"))
    assert _left(_json()) == [(CONFIG, "unknown", None)]
    assert "wowlab could not put back WTF/Config.wtf" in _json_problem(record_id, _json())


@pytest.mark.skipif(os.name == "nt", reason="a symbolic link needs a privilege on Windows")
def test_an_install_named_through_a_link_is_still_read_constructed(
    root: Path, flavor: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gate journals the resolved flavor folder; doctor finds the
    install by the name it was given (here a link to it, as a temp folder
    is on macOS) and still reads the file."""
    link = tmp_path / "linked install"
    link.symlink_to(root, target_is_directory=True)
    monkeypatch.setenv(install.ENV_ROOT, str(link))
    record_id = _fail_leaving(link, link / FLAVOR, CONFIG)
    assert guard.history()[-1].flavor_path == str(flavor.resolve())
    assert _left(_json()) == [(CONFIG, "changed_since", True)]
    assert "has changed WTF/Config.wtf" in _json_problem(record_id, _json())


def test_a_file_wowlab_never_wrote_is_left_by_undo_constructed(root: Path, flavor: Path) -> None:
    """The gate refused to write Config.wtf (another program changed it
    first), and the program changed it again: the record lists it, the
    problem says wowlab did not write it and undo leaves it, and undo does."""
    record_id = _fail_with_a_refused_write(root, flavor)
    expected = (
        f"{LEAD} (record {record_id}): in {FLAVOR}, wowlab did not write WTF/Config.wtf, "
        "which another program changed while wowlab was working, and undo leaves it as it is. "
        f"{TAIL}{record_id}"
    )
    assert _both(record_id) == (expected, expected)
    assert _left(_json()) == [(CONFIG, "not_written", None)]

    ok("undo", "--expect", record_id, "--yes")
    assert (flavor / CONFIG).read_bytes() == OTHER_AGAIN, "undo left it as it is"


def test_a_change_that_wrote_nothing_offers_no_undo_constructed(root: Path, flavor: Path) -> None:
    """The change's only write was refused: it wrote nothing, so the problem
    says there is nothing for undo to put back instead of the command."""
    record_id = _fail_with_a_refused_write(root, flavor, also=False)
    assert guard.history()[-1].paths == ()
    expected = (
        f"{NOTHING_LEAD} (record {record_id}): in {FLAVOR}, wowlab did not write "
        "WTF/Config.wtf, which another program changed while wowlab was working, and undo "
        f"leaves it as it is. {NOTHING}"
    )
    assert _both(record_id) == (expected, expected)
    assert "wowlab undo" not in expected
    data = _json()
    assert data["store"]["last_transaction"]["paths"] == 0
    assert _left(data) == [(CONFIG, "not_written", None)]


def test_a_change_that_wrote_nothing_and_names_no_file_constructed() -> None:
    problem = cli._rollback_unfinished("00000001-0123abcd", [], "_x_", paths=0)
    assert problem == (
        f"{NOTHING_LEAD} (record 00000001-0123abcd): another program, {THE_GAME}, changed a "
        f"file while wowlab was working. {NOTHING}"
    )


def _left_path(path: str, now: str, exists: bool | None) -> Any:
    return cli.LeftPath.model_validate({"path": path, "now": now, "exists": exists})


def test_three_files_are_listed_with_commas_constructed() -> None:
    left = [_left_path(p, "changed_since", True) for p in ("WTF/a", "WTF/b", "WTF/c")]
    problem = cli._rollback_unfinished("00000001-0123abcd", left, "_x_")
    assert f"in _x_, {CAUSE}, has changed WTF/a, WTF/b and WTF/c since wowlab wrote them." in (
        problem
    )
    assert problem.endswith(": wowlab undo --expect 00000001-0123abcd")


@pytest.mark.parametrize(
    ("now", "exists", "said"),
    [
        pytest.param(
            "wowlab_change",
            True,
            "WTF/a and WTF/b still hold wowlab's change",
            id="constructed-still-holds",
        ),
        pytest.param(
            "wowlab_change",
            False,
            "WTF/a and WTF/b are still deleted, as wowlab's change left them",
            id="constructed-still-deleted",
        ),
        pytest.param(
            "changed_since",
            False,
            f"{CAUSE}, has deleted WTF/a and WTF/b since wowlab wrote them",
            id="constructed-deleted-since",
        ),
        pytest.param(
            "before_change",
            True,
            "WTF/a and WTF/b already hold what was there before the change",
            id="constructed-already-back",
        ),
        pytest.param(
            "before_change",
            False,
            "WTF/a and WTF/b are gone again, as before the change",
            id="constructed-gone-again",
        ),
        pytest.param(
            "unknown",
            None,
            "wowlab could not put back WTF/a and WTF/b, which hold either wowlab's change or "
            f"what {CAUSE}, has written to them since",
            id="constructed-unknown",
        ),
        pytest.param(
            "not_written",
            None,
            "wowlab did not write WTF/a and WTF/b, which another program changed while wowlab "
            "was working, and undo leaves them as they are",
            id="constructed-not-written",
        ),
    ],
)
def test_two_files_are_said_in_the_plural_constructed(
    now: str, exists: bool | None, said: str
) -> None:
    left = [_left_path(p, now, exists) for p in ("WTF/a", "WTF/b")]
    problem = cli._rollback_unfinished("00000001-0123abcd", left, "_x_")
    assert f"(record 00000001-0123abcd): in _x_, {said}. " in problem


def test_a_record_that_lists_no_file_keeps_a_sentence_without_names_constructed() -> None:
    """A record written before the journal listed the files (or one whose
    rollback left only a folder) names none; its tail is the same."""
    record_id = "00000001-0123abcd"
    problem = cli._rollback_unfinished(record_id)
    assert problem == cli._rollback_unfinished(record_id, [], "_x_")
    assert problem == (
        f"{LEAD} (record {record_id}): {CAUSE}, may have changed a file while wowlab was "
        f"writing or since, or a file may still hold wowlab's change. {TAIL}{record_id}"
    )
    assert "brings it back" not in problem


def test_no_timing_that_holds_for_only_some_files_is_stated_constructed() -> None:
    """SavedVariables are written at logout or /reload, Config.wtf on exit
    and on some setting changes: the problem names neither timing."""
    every = [
        cli._rollback_unfinished("00000001-0123abcd"),
        cli._rollback_unfinished("00000001-0123abcd", [], "", paths=0),
        *(
            cli._rollback_unfinished("00000001-0123abcd", [_left_path("WTF/a", n, e)], "_x_")
            for n, e in (("changed_since", True), ("changed_since", False), ("unknown", None))
        ),
    ]
    for problem in every:
        assert "logout" not in problem and "/reload" not in problem, problem


# ─── escaped as printed ──────────────────────────────────────────────────────


def test_a_bidi_mark_in_a_left_file_name_prints_escaped_constructed(
    root: Path, flavor: Path
) -> None:
    record_id = _fail_leaving(root, flavor, BIDI)

    text = run("doctor")
    assert "\u202e" not in text.stdout
    problem = _problem(text.stdout)
    assert f"has changed {BIDI_PRINTED} since" in problem
    assert problem.endswith(f": wowlab undo --expect {record_id}")

    result = run("doctor", "--json")
    assert "\u202e" not in result.stdout, "--json is ASCII"
    report = json.loads(result.stdout)
    # The model holds the name as stored; the output escapes it.
    assert f"has changed {BIDI} since" in _json_problem(record_id, report)
    assert _left(report) == [(BIDI, "changed_since", True)]


@pytest.mark.parametrize(
    "name",
    [
        pytest.param("WTF/a\x1b]8;;http://x\x07b", id="constructed-osc-hyperlink"),
        pytest.param("WTF/a\x1b[2Jb", id="constructed-clear-screen"),
        pytest.param("WTF/a\u2028b", id="constructed-line-separator"),
        pytest.param("WTF/a\u200fb", id="constructed-rtl-mark"),
    ],
)
def test_control_and_format_characters_in_a_name_are_escaped_as_printed_constructed(
    name: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """The journal never holds a control character (the gate refuses one
    in a path); the printed line escapes them anyway."""
    problem = cli._rollback_unfinished(
        "00000001-0123abcd", [_left_path(name, "unknown", None)], "_x_"
    )
    cli._say(problem)
    printed = capsys.readouterr().out
    assert not any(c in printed for c in "\x1b\x07\u2028\u200f")
    assert printed.endswith(": wowlab undo --expect 00000001-0123abcd\n")


# ─── redacted ────────────────────────────────────────────────────────────────


def test_redact_hides_the_account_in_a_left_file_constructed(root: Path, flavor: Path) -> None:
    record_id = _fail_leaving(root, flavor, SAVED)
    redacted_path = f"WTF/Account/{redact.ACCOUNT}/SavedVariables/Constructed.lua"

    text = run("doctor", "--redact")
    assert text.exit_code == 1, text.output
    assert ACCOUNT not in text.stdout
    problem = _problem(text.stdout)
    assert f"in {FLAVOR}, {CAUSE}, has changed {redacted_path} since" in problem
    # The record id names nobody and stays: the command is still the line's end.
    assert problem.endswith(f": wowlab undo --expect {record_id}")

    result = run("doctor", "--json", "--redact")
    assert ACCOUNT not in result.stdout
    report = json.loads(result.stdout)
    assert _left(report) == [(redacted_path, "changed_since", True)]
    assert f"has changed {redacted_path} since" in _json_problem(record_id, report)


def test_redact_hides_names_only_the_journal_holds_constructed(root: Path, flavor: Path) -> None:
    """The transaction created the account, realm and character folders;
    after the failure the player removed them. No snapshot and no folder
    in the install names them any more, only the journal: they are still
    redacted."""
    _fail_leaving(root, flavor, JOURNAL_ONLY)
    shutil.rmtree(flavor / "WTF" / "Account" / JOURNAL_ACCOUNT)
    names = (JOURNAL_ACCOUNT, JOURNAL_REALM, JOURNAL_CHARACTER)

    plain = run("doctor")
    assert all(n in plain.stdout for n in names), "positive control: doctor prints them"

    for args in (("--redact",), ("--json", "--redact")):
        result = run("doctor", *args)
        assert result.exit_code == 1, result.output
        leaked = [n for n in names if n.casefold() in result.stdout.casefold()]
        assert not leaked, (args, leaked)
        assert (
            f"WTF/Account/{redact.ACCOUNT}/{redact.REALM}/{redact.CHARACTER}/"
            "SavedVariables/Constructed.lua"
        ) in result.stdout


@pytest.mark.parametrize(
    "raw",
    [
        # U+200E, which `_safe` escapes: redaction must see the name as stored.
        # Every file system holds it.
        pytest.param("Labmarkacct\u200eone".encode(), id="constructed-lrm-in-the-name"),
        # A byte that is not UTF-8, carried as a lone surrogate; only where
        # the file system holds such a name (probed, not assumed).
        pytest.param(b"Labbyteacct\xffone", id="constructed-not-utf8"),
    ],
)
def test_redact_hides_an_account_whose_name_prints_escaped_constructed(
    root: Path, flavor: Path, tmp_path: Path, raw: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    try:
        account = raw.decode("utf-8")
    except UnicodeDecodeError:
        why = _holds_non_utf8_names(tmp_path)
        if why is not None:
            pytest.skip(why)
        account = os.fsdecode(raw)
    # The gate logs the path it left; a lone surrogate in a captured log
    # line cannot cross to the pytest-xdist controller, so the library's log
    # stays out of pytest's capture here.
    monkeypatch.setattr(logging.getLogger("wowlab_core"), "propagate", False)
    rel = f"WTF/Account/{account}/SavedVariables/Constructed.lua"
    record_id = _fail_leaving(root, flavor, rel)
    stem = account[:11]  # the ASCII part before the escaped character
    redacted_path = f"WTF/Account/{redact.ACCOUNT}/SavedVariables/Constructed.lua"

    plain = run("doctor")
    assert stem in plain.stdout, "positive control: doctor prints the name"

    text = run("doctor", "--redact")
    assert stem.casefold() not in text.stdout.casefold(), text.stdout
    assert f"has changed {redacted_path} since" in _problem(text.stdout)

    result = run("doctor", "--json", "--redact")
    assert stem.casefold() not in result.stdout.casefold()
    report = json.loads(result.stdout)
    assert _left(report) == [(redacted_path, "changed_since", True)]
    assert redacted_path in _json_problem(record_id, report)
