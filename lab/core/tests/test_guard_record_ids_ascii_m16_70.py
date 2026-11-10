"""Journal record ids are ASCII digits only (graders for M16-70, M16-70T).

The journal names each record `<8 digits>-<8 lowercase hex>.json`, and the
record's `id` is that name without `.json` (`docs/LAB_PLAN.md` §6.10:
sequence numbers unique per store, `undo()` acts on the most recent
record; §16.1 and the M14-03 amendment: `undo --expect <journal id>`, the
pin read from the plan's `undone`). The M16-13 security review
(2026-11-10) found the digits matched as Unicode digits, so a file named
with Arabic-Indic, fullwidth or other non-ASCII digits loads as a record:
`history()` lists it, `undo()` takes it as the most recent record, its
digits count toward the next sequence number, and `doctor` prints its id in
an `undo --expect` line. The ticket's rule: such a file is not loaded as a
record. It is treated like any other name that is not a record's (a temp
file of an interrupted journal write): not listed, not undone, not counted,
not printed.

Every place a journal id is read or accepted is graded through the public
surface only (`guard.history`, `guard.undo(expected_id=...)`, a new
transaction's record id, the CLI's `undo` plan and `undo --expect`, and
`doctor`), never through the listing's internals, which other work is
changing.

All inputs are constructed (hostile-input, L8): a real record written by a
real `addon install` is copied or moved to a name whose digits are not
ASCII. The install is the captured tree copied into `tmp_path` by
`test_cli.py`'s fixtures, the user data directory is redirected and the
process table is a fake one with no client. Nothing reads or writes a real
install. The `ascii-control` cases carry no marker: they pass today and
prove the planting itself works, so each xfail fails for the right reason.
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from test_cli import (
    _state,
    flavor,  # noqa: F401
    idle,  # noqa: F401  (autouse: no client in the process table)
    ok,
    replayed,  # noqa: F401  (autouse: recorded game data only)
    root,  # noqa: F401
    run,
    user_data,  # noqa: F401
)

from wowlab_core import guard, snapshot

XFAIL = pytest.mark.xfail(strict=True, raises=AssertionError, reason="M16-70 not implemented")

# The zero of each digit set; a planted id spells the sequence number 9.
NON_ASCII_ZEROS = [
    pytest.param(0x0660, id="constructed-arabic-indic"),
    pytest.param(0x06F0, id="constructed-extended-arabic-indic"),
    pytest.param(0x0966, id="constructed-devanagari"),
    pytest.param(0xFF10, id="constructed-fullwidth"),
    pytest.param(None, id="constructed-one-arabic-indic-digit-among-ascii"),
]
PLANTED_SEQUENCE = 9


def _digits(zero: int | None) -> str:
    """`00000009` spelled in the digit set whose zero is `zero`; None keeps
    seven ASCII zeros and spells only the last digit in Arabic-Indic."""
    if zero is None:
        return "0" * 7 + chr(0x0660 + PLANTED_SEQUENCE)
    return chr(zero) * 7 + chr(zero + PLANTED_SEQUENCE)


def _journal() -> Path:
    return snapshot.default_store_path() / "journal"


def _real_record() -> tuple[Path, dict[str, Any]]:
    """The one record a real `addon install` journaled, and its body."""
    ok("addon", "install", "lab", "--yes")
    records = guard.history()
    assert len(records) == 1
    path = _journal() / f"{records[0].id}.json"
    return path, json.loads(path.read_text(encoding="ascii"))


def _plant(record: dict[str, Any], digits: str, *, state: str | None = None) -> str:
    """A copy of `record` named and identified `<digits>-<8 hex>`; its id.
    `json.dumps` escapes non-ASCII, so the body stays ASCII as the journal's are."""
    uid = f"{digits}-abcdef12"
    body = dict(record, id=uid)
    if state is not None:
        body["state"] = state
    (_journal() / f"{uid}.json").write_text(json.dumps(body), encoding="ascii")
    assert (_journal() / f"{uid}.json").is_file()
    return uid


def _out(result: Any) -> str:
    return f"{result.stdout}\n{result.stderr}\n{result.exception!r}"


def _files(root: Path) -> dict[str, bytes | None]:
    """The install's files and bytes, without mtimes: what an undo restores."""
    return {k: v[0] for k, v in _state(root).items() if v[0] is not None}


def _ascii_names() -> set[str]:
    return {p.name for p in _journal().iterdir() if p.name.isascii()}


# ─── the positive control: an ASCII name is a record ────────────────────────


def test_an_ascii_named_copy_loads_as_the_newest_record_ascii_control(root: Path) -> None:
    """The planting mechanics on an ASCII name: the copy is listed last,
    `undo`'s plan names it, and `doctor` prints its `undo --expect` line."""
    _path, record = _real_record()
    uid = _plant(record, f"{PLANTED_SEQUENCE:08d}", state="open")
    assert [r.id for r in guard.history()] == [record["id"], uid]
    plan = run("undo", "--json", input="")
    assert json.loads(plan.stdout.lstrip())["undone"] == uid, _out(plan)
    assert f"undo --expect {uid}" in run("doctor").stdout


# ─── guard.history: not listed ──────────────────────────────────────────────


@XFAIL
@pytest.mark.parametrize("zero", NON_ASCII_ZEROS)
def test_a_non_ascii_digit_name_is_not_listed(root: Path, zero: int | None) -> None:
    _path, record = _real_record()
    uid = _plant(record, _digits(zero))
    ids = [r.id for r in guard.history()]
    assert uid not in ids
    assert ids == [record["id"]]
    assert all(i.isascii() for i in ids)


@XFAIL
@pytest.mark.parametrize("zero", NON_ASCII_ZEROS)
def test_a_journal_of_only_a_non_ascii_digit_name_is_empty(root: Path, zero: int | None) -> None:
    """The real record moved to the planted name: nothing is left to list."""
    path, record = _real_record()
    _plant(record, _digits(zero))
    path.unlink()
    assert [r.id for r in guard.history()] == []


# ─── a new record's sequence number: not counted ────────────────────────────


@XFAIL
@pytest.mark.parametrize("zero", NON_ASCII_ZEROS)
def test_a_non_ascii_digit_name_does_not_count_toward_the_next_sequence(
    root: Path, zero: int | None
) -> None:
    _path, record = _real_record()
    _plant(record, _digits(zero))
    before = _ascii_names()
    ok("addon", "remove", "lab", "--yes")
    new = _ascii_names() - before
    assert len(new) == 1, new
    (name,) = new
    sequence = int(record["id"].split("-")[0])
    assert name.startswith(f"{sequence + 1:08d}-"), name
    assert [r.id for r in guard.history()][-1] == name.removesuffix(".json")


# ─── guard.undo(expected_id=...): not the most recent record ────────────────


@XFAIL
@pytest.mark.parametrize("zero", NON_ASCII_ZEROS)
def test_guard_undo_refuses_a_non_ascii_digit_id(root: Path, zero: int | None) -> None:
    """The planted id names no record: `GuardError`, nothing changed."""
    _path, record = _real_record()
    uid = _plant(record, _digits(zero))
    state = _state(root)
    names = sorted(p.name for p in _journal().iterdir())
    refused: guard.GuardError | None = None
    try:
        guard.undo(expected_id=uid)
    except guard.GuardError as exc:
        refused = exc
    assert refused is not None, "undo() took the non-ASCII-digit record as a record"
    assert _state(root) == state
    assert sorted(p.name for p in _journal().iterdir()) == names


@XFAIL
@pytest.mark.parametrize("zero", NON_ASCII_ZEROS)
def test_guard_undo_takes_the_ascii_record_as_the_most_recent(root: Path, zero: int | None) -> None:
    """The real record is still the newest one, so pinning it undoes it."""
    before = _files(root)
    _path, record = _real_record()
    _plant(record, _digits(zero))
    try:
        guard.undo(expected_id=record["id"])
    except guard.GuardError as exc:
        raise AssertionError(f"the ASCII record was not the most recent: {exc}") from exc
    assert _files(root) == before


# ─── the CLI: `undo`'s plan and `undo --expect` ─────────────────────────────


@XFAIL
@pytest.mark.parametrize("zero", NON_ASCII_ZEROS)
def test_undo_plan_names_the_ascii_record(root: Path, zero: int | None) -> None:
    _path, record = _real_record()
    uid = _plant(record, _digits(zero))
    result = run("undo", "--json", input="")
    assert uid not in _out(result)
    assert json.loads(result.stdout.lstrip())["undone"] == record["id"], _out(result)


@XFAIL
@pytest.mark.parametrize("zero", NON_ASCII_ZEROS)
def test_undo_expect_refuses_a_non_ascii_digit_id(root: Path, zero: int | None) -> None:
    _path, record = _real_record()
    uid = _plant(record, _digits(zero))
    state = _state(root)
    names = sorted(p.name for p in _journal().iterdir())
    for args in (["--yes"], ["--yes", "--json"]):
        result = run("undo", "--expect", uid, *args)
        assert result.exit_code == 3, _out(result)
        assert "nothing was undone" in result.stderr, _out(result)
    assert _state(root) == state
    assert sorted(p.name for p in _journal().iterdir()) == names


@XFAIL
@pytest.mark.parametrize("zero", NON_ASCII_ZEROS)
def test_undo_expect_refuses_a_non_ascii_digit_id_on_an_otherwise_empty_journal(
    root: Path, zero: int | None
) -> None:
    """The real record moved to the planted name: the journal is empty."""
    path, record = _real_record()
    uid = _plant(record, _digits(zero))
    path.unlink()
    state = _state(root)
    result = run("undo", "--yes", "--expect", uid)
    assert result.exit_code == 3, _out(result)
    assert "the journal is empty" in result.stderr, _out(result)
    assert _state(root) == state


@XFAIL
@pytest.mark.parametrize("zero", NON_ASCII_ZEROS)
def test_undo_expect_accepts_the_ascii_record_beside_a_planted_name(
    root: Path, zero: int | None
) -> None:
    before = _files(root)
    _path, record = _real_record()
    _plant(record, _digits(zero))
    result = run("undo", "--yes", "--expect", record["id"], "--json")
    assert result.exit_code == 0, _out(result)
    assert json.loads(result.stdout)["undone"] == record["id"]
    assert _files(root) == before


# ─── doctor: never prints the id ────────────────────────────────────────────


@XFAIL
@pytest.mark.parametrize("zero", NON_ASCII_ZEROS)
@pytest.mark.parametrize("keep_real", [True, False], ids=["beside-a-record", "alone"])
def test_doctor_never_prints_a_non_ascii_digit_id(
    root: Path, zero: int | None, keep_real: bool
) -> None:
    """An interrupted (`open`) record under the planted name: `doctor`
    neither offers `undo --expect` for it nor names it, as text or JSON."""
    path, record = _real_record()
    uid = _plant(record, _digits(zero), state="open")
    if not keep_real:
        path.unlink()
    text = run("doctor")
    assert text.exception is None or isinstance(text.exception, SystemExit), _out(text)
    assert uid not in text.stdout
    assert "undo --expect" not in text.stdout
    as_json = run("doctor", "--json")
    assert as_json.exception is None or isinstance(as_json.exception, SystemExit), _out(as_json)
    assert uid not in as_json.stdout
    assert json.dumps(uid) not in as_json.stdout
