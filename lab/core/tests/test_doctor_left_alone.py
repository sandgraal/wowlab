"""`doctor` names the file a rollback left alone (M16-84).

M16-71 made a most recent journal record in `rollback_incomplete` a
`doctor` problem; it could only say "a file", since the paths were only in
the record's free-text note. The write gate now lists them on the record
(`HistoryRecord.paths_left`, graded in `test_guard_left_alone_m16_84.py`),
and the problem names each one: relative to the flavor folder, escaped
(a real file name may hold a bidi mark, which the gate allows), redacted by
`--redact` in the text and in `--json`, with the `wowlab undo --expect <id>`
command still last.

The install is the captured tree copied into `tmp_path` as `test_cli.py`
builds it, the user data directory is redirected and the process table is a
fake one (constructed: no client is running). Nothing reads or writes a real
install.

Constructed (L8, boundary): the failure, as in
`test_doctor_rollback_unfinished.py`. A real write-gate transaction writes,
another program (this test) then rewrites what it wrote, and the caller's
body raises: the gate leaves the other program's bytes alone and ends the
record `rollback_incomplete`. The journal is not edited.
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from test_cli import (
    ACCOUNT,
    flavor,  # noqa: F401
    idle,  # noqa: F401  (autouse: no client in the process table)
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


def _fail_leaving(root: Path, flavor: Path, *rels: str) -> str:
    """A change that wrote `rels` and failed after another program rewrote
    each of them, so its rollback left them all. The record's id."""
    (discovered,) = install.read_install(root).flavors
    with pytest.raises(_StopError):  # noqa: SIM117 - the exit is what rolls back
        with guard.transaction(discovered, label="constructed: fails") as tx:
            for rel in rels:
                tx.write(rel, CHANGED)
            for rel in rels:
                (flavor / rel).write_bytes(OTHER)  # another program, mid-change
            raise _StopError
    last = guard.history()[-1]
    assert last.state == "rollback_incomplete"
    assert sorted(last.paths_left) == sorted(rels)
    return last.id


def _problem(text: str) -> str:
    """The text output's one problem, joined back onto one line."""
    assert "Problems:\n" in text, text
    (line,) = text.split("Problems:\n", 1)[1].splitlines()[:1]
    return line.strip()


def _json_problem(record_id: str, data: dict[str, object]) -> str:
    problems = data["problems"]
    assert isinstance(problems, list)
    (problem,) = [p for p in problems if f"--expect {record_id}" in p]
    assert isinstance(problem, str)
    return problem


# ─── the sentence ────────────────────────────────────────────────────────────


def test_the_problem_names_the_one_file_left_alone_constructed(root: Path, flavor: Path) -> None:
    record_id = _fail_leaving(root, flavor, CONFIG)
    expected = (
        "the last change wowlab made failed, and putting the files back did not finish "
        f"(record {record_id}): wowlab left WTF/Config.wtf as it was. It may have been changed "
        "by another program, such as the game, while wowlab was writing, or may still hold "
        "wowlab's change. To put back what was there before the change, run this; it replaces "
        "what is in that file now, but the undo keeps a copy and running wowlab undo again "
        f"brings it back: wowlab undo --expect {record_id}"
    )

    text = run("doctor")
    assert text.exit_code == 1, text.output
    assert _problem(text.stdout) == expected

    data = run("doctor", "--json")
    assert data.exit_code == 1
    report = json.loads(data.stdout)
    assert _json_problem(record_id, report) == expected
    assert report["store"]["last_transaction"]["paths_left"] == [CONFIG]


def test_the_problem_names_every_file_left_alone_constructed(root: Path, flavor: Path) -> None:
    record_id = _fail_leaving(root, flavor, SAVED, CONFIG)
    expected = (
        "the last change wowlab made failed, and putting the files back did not finish "
        f"(record {record_id}): wowlab left {SAVED} and {CONFIG} as they were. Each may have "
        "been changed by another program, such as the game, while wowlab was writing, or may "
        "still hold wowlab's change. To put back what was there before the change, run this; "
        "it replaces what is in those files now, but the undo keeps a copy and running wowlab "
        f"undo again brings it back: wowlab undo --expect {record_id}"
    )
    report = json.loads(run("doctor", "--json").stdout)
    assert _json_problem(record_id, report) == expected
    # Sorted, so the same record always prints the same line.
    assert report["store"]["last_transaction"]["paths_left"] == [SAVED, CONFIG]
    assert _problem(run("doctor").stdout) == expected


def test_three_files_are_listed_with_commas_constructed() -> None:
    problem = cli._rollback_unfinished("00000001-0123abcd", ["WTF/a", "WTF/b", "WTF/c"])
    assert "wowlab left WTF/a, WTF/b and WTF/c as they were. Each may" in problem
    assert problem.endswith(": wowlab undo --expect 00000001-0123abcd")


def test_a_record_that_lists_no_file_keeps_the_sentence_without_names_constructed() -> None:
    """A record written before the journal listed the files (or one whose
    rollback left only a folder) names none: the M16-71 sentence."""
    record_id = "00000001-0123abcd"
    assert cli._rollback_unfinished(record_id, []) == cli._rollback_unfinished(record_id)
    assert "a file may have been changed" in cli._rollback_unfinished(record_id)
    assert "wowlab left" not in cli._rollback_unfinished(record_id)


# ─── escaped ─────────────────────────────────────────────────────────────────


def test_a_bidi_mark_in_a_left_file_name_prints_escaped_constructed(
    root: Path, flavor: Path
) -> None:
    record_id = _fail_leaving(root, flavor, BIDI)

    text = run("doctor")
    assert "\u202e" not in text.stdout
    problem = _problem(text.stdout)
    assert f"wowlab left {BIDI_PRINTED} as it was." in problem
    assert problem.endswith(f": wowlab undo --expect {record_id}")

    report = json.loads(run("doctor", "--json").stdout)
    assert "\u202e" not in _json_problem(record_id, report)
    assert f"wowlab left {BIDI_PRINTED} as it was." in _json_problem(record_id, report)
    # The list itself is data: the name as stored.
    assert report["store"]["last_transaction"]["paths_left"] == [BIDI]


@pytest.mark.parametrize(
    "name",
    [
        pytest.param("WTF/a\x1b]8;;http://x\x07b", id="constructed-osc-hyperlink"),
        pytest.param("WTF/a\x1b[2Jb", id="constructed-clear-screen"),
        pytest.param("WTF/a\u2028b", id="constructed-line-separator"),
        pytest.param("WTF/a\u200fb", id="constructed-rtl-mark"),
    ],
)
def test_control_and_format_characters_in_a_name_are_escaped_constructed(name: str) -> None:
    """The journal never holds these (the gate refuses a control character
    in a path); the sentence escapes them anyway."""
    problem = cli._rollback_unfinished("00000001-0123abcd", [name])
    assert problem == cli._safe(problem)
    assert not any(c in problem for c in "\x1b\x07\u2028\u200f")


# ─── redacted ────────────────────────────────────────────────────────────────


def test_redact_hides_the_account_in_a_left_file_constructed(root: Path, flavor: Path) -> None:
    record_id = _fail_leaving(root, flavor, SAVED)
    redacted_path = f"WTF/Account/{redact.ACCOUNT}/SavedVariables/Constructed.lua"

    text = run("doctor", "--redact")
    assert text.exit_code == 1, text.output
    assert ACCOUNT not in text.stdout
    problem = _problem(text.stdout)
    assert f"wowlab left {redacted_path} as it was." in problem
    # The record id names nobody and stays: the command is still the line's end.
    assert problem.endswith(f": wowlab undo --expect {record_id}")

    data = run("doctor", "--json", "--redact")
    assert data.exit_code == 1
    assert ACCOUNT not in data.stdout
    report = json.loads(data.stdout)
    assert report["store"]["last_transaction"]["paths_left"] == [redacted_path]
    (problem,) = [p for p in report["problems"] if "wowlab left" in p]
    assert f"wowlab left {redacted_path} as it was." in problem


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
