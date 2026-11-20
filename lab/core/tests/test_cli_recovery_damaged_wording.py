"""`undo`, plain `doctor` and `doctor`'s first-run check say M19-13's
sentence of a damaged snapshot record, never the loader's words (M19-15).

Before this, the three passed on the manifest loader's message, which quotes
a fragment of the record: `undo --yes` through the write gate's refusal
(`guard.py` is not changed; the command's handler rewords it), `doctor` in
its store section, text and `--json`, and the first run's verify lines and
report. `doctor --redact` already withheld it and is left as it was. A
marker (two name-realm-shaped keys, ESC, U+202E) is planted in a
wrong-typed and in a non-UTF-8 manifest and searched for in stdout and
stderr, text and `--json`. Exit codes and every other line stay as ADR-0037
froze them (its note of 2026-11-20).

Real fixtures (L8): the M11-03 capture copied into `tmp_path` as
`test_cli.py` builds it, with the user data directory redirected into
`tmp_path` and a fake process table. Constructed, labelled in the test
names: the damaged manifests and the keys planted in them
(`Inventeda-Fakerealm`, `Inventedb-Fakerealm`), which no real install holds.
Nothing reads or writes a real install.
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from test_cli import (
    _create,
    _state,
    flavor,  # noqa: F401
    idle,  # noqa: F401  (autouse: no client in the process table)
    replayed,  # noqa: F401  (autouse: recorded game data only)
    root,  # noqa: F401
    run,
    user_data,  # noqa: F401
)
from test_cli_snap_rm import _player_copy_problems, _restore_a_change, _three

import wowlab_core
from wowlab_core import cli, guard, snapshot
from wowlab_core.cli import _cmd_doctor, _cmd_restore

KEY_A = "Inventeda-Fakerealm"  # constructed name-realm-shaped keys
KEY_B = "Inventedb-Fakerealm"
WORD = "PLANTEDM1915WORD"
ESC = "\x1b[31m"
RLO = "\u202e"
PLANTED = f"{WORD} {KEY_A} {ESC} {RLO}"

FORBIDDEN = (
    WORD,
    KEY_A,
    KEY_B,
    "Inventeda",
    "Inventedb",
    "\x1b",
    "\\x1b",
    "\\u001b",
    "\\u001B",
    RLO,
    "\\u202e",
    "\\u202E",
    "\\xe2\\x80\\xae",
    "validation",
    "pydantic",
    "json_invalid",
    "input_value",
    "Expecting",
    "utf-8",
    "codec",
)


def _wrong_typed(good: bytes) -> bytes:
    data = json.loads(good)
    data["entries"] = PLANTED
    data["created_at"] = [PLANTED, {KEY_B: 1}]
    data[KEY_A] = PLANTED
    return json.dumps(data, ensure_ascii=False).encode()


def _not_utf8(good: bytes) -> bytes:
    data = json.loads(good)
    data["label"] = f"{PLANTED} {KEY_B}"
    text = json.dumps(data, ensure_ascii=False).encode()
    at = text.index(WORD.encode())
    return text[:at] + b"\xff\xfe" + text[at:]


DAMAGE: dict[str, Callable[[bytes], bytes]] = {
    "constructed-wrong-typed": _wrong_typed,
    "constructed-not-utf8": _not_utf8,
}


def _damage(user_data: Path, sid: str, how: str) -> None:
    path = user_data / "store" / "manifests" / f"{sid}.json"
    path.write_bytes(DAMAGE[how](path.read_bytes()))
    with pytest.raises(snapshot.ManifestIntegrityError) as loader:
        snapshot.SnapshotStore(user_data / "store").show(sid)
    # The control: the loader's own words do quote the plant.
    assert any(raw in str(loader.value) for raw in FORBIDDEN), loader.value


def _said(result: Any) -> str:
    return "\n".join(part for part in (result.stdout, result.stderr) if part)


def _quotes_nothing(result: Any) -> None:
    text = _said(result)
    for raw in FORBIDDEN:
        assert raw not in text, (raw, text)


def _unreadable(sid: str) -> str:
    return (
        f"snapshot {sid} is damaged: its record in wowlab's snapshot folder cannot be read. "
        f"`wowlab snap rm {sid} --break-undo` removes it."
    )


# ─── undo ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("how", list(DAMAGE))
def test_undo_of_a_change_whose_snapshot_is_damaged_quotes_nothing(
    root: Path, flavor: Path, user_data: Path, how: str
) -> None:
    a, _, _ = _three(flavor)
    record = _restore_a_change(flavor, a)
    pre = record.snapshot_id
    assert pre is not None
    _damage(user_data, pre, how)
    said = f"refused by the write gate: cannot read the pre-write snapshot {pre!r}: " + (
        _unreadable(pre)
    )
    install_before = _state(root)
    journal_before = guard.history()
    text = run("undo", "--yes")
    assert text.exit_code == 3, _said(text)  # refused by the write gate, as before
    _quotes_nothing(text)
    assert said in " ".join(text.stderr.split())
    assert not _player_copy_problems(root, text.stderr)
    as_json = run("undo", "--yes", "--json")
    assert as_json.exit_code == 3, _said(as_json)
    _quotes_nothing(as_json)
    error = json.loads(as_json.stdout)
    assert error["message"] == said  # text and JSON agree
    assert error["kind"] == "GuardError" and error["exit_code"] == 3
    assert _state(root) == install_before  # nothing written
    assert guard.history() == journal_before  # nothing journaled


def test_undo_of_a_missing_snapshot_keeps_its_words(
    root: Path, flavor: Path, user_data: Path
) -> None:
    # Only a damaged record is reworded: a missing one is refused as before.
    a, _, _ = _three(flavor)
    record = _restore_a_change(flavor, a)
    pre = record.snapshot_id
    assert pre is not None
    (user_data / "store" / "manifests" / f"{pre}.json").unlink()
    result = run("undo", "--yes")
    assert result.exit_code == 3
    message = " ".join(result.stderr.split())
    assert f"cannot read the pre-write snapshot {pre!r}: no snapshot matches {pre!r}" in message
    assert "is damaged" not in message


# ─── doctor ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("how", list(DAMAGE))
def test_doctor_names_a_damaged_snapshot_in_the_fixed_sentence(
    root: Path, user_data: Path, how: str
) -> None:
    _create("good")  # so this is not a first run
    bad = _create("bad")
    _damage(user_data, bad.id, how)
    text = run("doctor")
    assert text.exit_code == 1, _said(text)  # a damaged manifest is a problem, as before
    _quotes_nothing(text)
    plain = " ".join(text.stdout.split())
    assert f"damaged: {bad.id}.json: {_unreadable(bad.id)}" in plain
    assert "1 snapshot(s) that load; 1 damaged" in plain
    assert not _player_copy_problems(root, text.stdout)
    as_json = run("doctor", "--json")
    assert as_json.exit_code == 1, _said(as_json)
    _quotes_nothing(as_json)
    report = cli.DoctorReport.model_validate_json(as_json.stdout)
    assert [(d.name, d.reason) for d in report.store.damaged] == [
        (f"{bad.id}.json", _unreadable(bad.id))
    ]
    assert report.first_run is None
    assert "1 damaged snapshot manifest(s)" in report.problems


def test_doctor_redact_still_withholds_as_before_constructed(root: Path, user_data: Path) -> None:
    # `--redact` is left alone: it still withholds the loader's words itself.
    _create("good")
    bad = _create("bad")
    _damage(user_data, bad.id, "constructed-wrong-typed")
    result = run("doctor", "--redact", "--json")
    assert result.exit_code == 1, _said(result)
    for raw in (WORD, KEY_A, KEY_B, "\x1b", RLO):
        assert raw not in _said(result), raw
    report = cli.DoctorReport.model_validate_json(result.stdout)
    (damaged,) = report.store.damaged
    assert "is damaged" not in damaged.reason
    assert "type=extra_forbidden" in damaged.reason  # its withheld form, as before


# ─── doctor's first-run check ────────────────────────────────────────────────


@pytest.mark.parametrize("how", list(DAMAGE))
def test_the_first_run_check_names_a_damaged_snapshot_in_the_fixed_sentence(
    root: Path, user_data: Path, how: str
) -> None:
    # The only snapshot is damaged, so the store holds none that loads: a
    # first run, whose verify finds the damaged record.
    bad = _create("bad")
    _damage(user_data, bad.id, how)
    text = run("doctor", "--yes")
    assert text.exit_code == 1, _said(text)  # the store did not verify, as before
    _quotes_nothing(text)
    plain = " ".join(text.stdout.split())
    assert f"damaged manifest {bad.id}.json: {_unreadable(bad.id)}" in plain
    assert "the store has problems; see above" in plain
    assert not _player_copy_problems(root, text.stdout)


@pytest.mark.parametrize("how", list(DAMAGE))
def test_the_first_run_check_names_a_damaged_snapshot_in_json(
    root: Path, user_data: Path, how: str
) -> None:
    # A run of its own: the text run above took the first snapshot.
    bad = _create("bad")
    _damage(user_data, bad.id, how)
    as_json = run("doctor", "--yes", "--json")
    assert as_json.exit_code == 1, _said(as_json)
    _quotes_nothing(as_json)
    report = cli.DoctorReport.model_validate_json(as_json.stdout)
    assert report.first_run is not None and report.first_run.verify is not None
    assert report.first_run.outcome == "failed"
    reasons = [(d.name, d.reason) for d in report.first_run.verify.invalid_manifests]
    assert reasons == [(f"{bad.id}.json", _unreadable(bad.id))]
    assert [(d.name, d.reason) for d in report.store.damaged] == reasons


def test_the_sentence_is_still_written_once() -> None:
    assert _cmd_doctor._damaged_words is _cmd_restore._damaged_words is cli._damaged_words
    assert _cmd_doctor._damaged_records is cli._damaged_records
    src = Path(wowlab_core.__file__).parent
    phrase = "is damaged: its record in wowlab's snapshot folder cannot be read."
    holders = [
        p.relative_to(src).as_posix()
        for p in sorted(src.rglob("*.py"))
        if phrase in p.read_text(encoding="utf-8")
    ]
    assert holders == ["cli/_common.py"]


def test_the_write_gate_holds_no_copy_of_the_sentence() -> None:
    # The undo's words are changed in the command, not in the write gate.
    gate = (Path(wowlab_core.__file__).parent / "guard.py").read_text(encoding="utf-8")
    assert "_damaged_words" not in gate
