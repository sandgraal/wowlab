"""`wowlab sv merge` loader check across a Lab write to `WowLab.lua` (M11-24T2).

Graders for M11-24, written before it (ADR-0013; `svmerge.py` and the loader
check in `cli.py` are load-bearing, owner, 2026-09-29). Every grader of new
behaviour carries one `xfail(strict=True)` marker line, which M11-24 deletes
and does not otherwise edit. Tests without a marker pass on main today and
must stay green.

Every expectation comes from docs/LAB_PLAN.md §13.4, the bullet "2026-09-29,
owner ruling amending 'A Lab write is not a loader failure' (M11-24 security
review)", read with the M11-09 and M11-24 rulings above it. W is the latest
committed guard write to the `--into` character's `WowLab.lua` in the span of
a comparison, and "after" is the hash the journal records for what W wrote.

1. **No session since W.** The newer side (the disk, or a snapshot) is
   byte-identical to W's `after`: the merge passes with the note "the Lab wrote
   WowLab.lua after that snapshot; the loader was not re-checked".
2. **A session since W.** Otherwise the older side is the store object whose
   hash is W's `after`, and its `probe.loads` is compared with the newer side
   under the normal rules: lower refused, equal refused unless byte-identical,
   higher passes. Every Lab write to `WowLab.lua` keeps the bytes it wrote as a
   store object.
3. **W's result cannot be read** (the object is missing, damaged or
   unparseable, or W predates the rule): refused with exit 3, naming the
   journal record; `--force-loader-check` overrides. Never skipped.
4. A journal record or snapshot dated later than the current time is ignored
   for the check, with a note naming it, and does not turn the check off.

Choices made here where the ruling leaves room, confirmed by the conductor
on M11-24T2 (2026-09-29), with one more ruling: rule 1 wins over rule 3.
Byte-identical bytes prove no session ran, so an unreadable object for W's
`after` does not refuse when the newer side is W's own bytes.

- A pass under rule 2 does not print the rule-1 note: that note says the
  loader was not re-checked, and under rule 2 it was.
- The note naming an ignored future-dated record or snapshot is printed on a
  refusal too (in the refusal's output), as well as in `notes` when the merge
  goes ahead.
- `profile apply` is in the ruling's list of Lab writes, but §13.3 has it
  leave `WowLab.lua` alone, so it has no bytes of that file to keep. The
  profile grader below is a control of that, not a grader of rule 2.

Real fixtures (L8): the two characters' `WowLab.lua` from the M11-03 capture
(`probe.loads` 4 and 2) and the account-wide `DBM-Party-Vanilla.lua`, in the
captured tree copied into `tmp_path` as `test_cli.py` builds it, with the
user data directory (store, journal, locks) redirected into `tmp_path` and a
fake process table. Constructed (labelled in the test names): edited copies
of the first character's real file standing for other sessions (`loads` set
to another number, one value changed); a store object deleted, overwritten
or planted; a journal record or snapshot manifest in the temporary user data
edited to a future date. Nothing reads or writes a real install.
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

import hashlib
import json
import zlib
from pathlib import Path
from typing import Any

import pytest
from test_cli import (
    flavor,  # noqa: F401
    idle,  # noqa: F401  (autouse: no client in the process table)
    ok,
    replayed,  # noqa: F401  (autouse: recorded game data only)
    root,  # noqa: F401
    run,
    user_data,  # noqa: F401
)
from test_svmerge import (
    _FILTER,
    CHAR_A,
    CHAR_B,
    DBM,
    DBM_NAME,
    DBM_VAR,
    LAB,
    LAB_A,
    NO_LOGIN_NOTE,
    REAL_A,
    REAL_DBM,
    _copy_within,
    _once,
    _out,
    _report,
    _snap,
)
from test_svmerge_loader import (
    LAB_NOTE,
    _frozen,
    _merge_gear_into_wowlab_lua,
    _probe,
    _restore_lab,
)

from wowlab_core import guard, snapshot

FUTURE = "2099-01-01T00:00:00.000000Z"  # a clock that ran ahead
FUTURE_ID_PREFIX = "20990101T000000.000000Z"


def _loads(data: bytes, n: int, *, was: int = 4) -> bytes:
    """Constructed: `data` as a session that left `probe.loads` at `n` writes it."""
    return _once(data, f'["loads"] = {was},'.encode(), f'["loads"] = {n},'.encode())


def _a(n: int) -> bytes:
    """The first character's real file with `loads` set to `n` (4 is the real one)."""
    return REAL_A if n == 4 else _loads(REAL_A, n)


def _disk(flavor: Path) -> Path:
    return flavor / LAB_A


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _store() -> snapshot.SnapshotStore:
    """The store in the redirected user data directory."""
    return snapshot.SnapshotStore()


def _lab_writes() -> list[guard.HistoryRecord]:
    return [
        r
        for r in guard.history()
        if r.state == "committed" and any(p.path == LAB_A for p in r.paths)
    ]


def _after(record: guard.HistoryRecord) -> str:
    (change,) = [p for p in record.paths if p.path == LAB_A]
    assert change.after is not None
    return change.after


def _last_lab_write() -> guard.HistoryRecord:
    writes = _lab_writes()
    assert writes, "a committed guard write of the first character's WowLab.lua"
    return writes[-1]


def _copy_to(dst: str, *extra: str) -> Any:
    """Like `_copy_within`, to another key of the account file, so a second
    run after a written one still has something to write."""
    key = f'{DBM_VAR}["Labchard Labrealmg"]={DBM_VAR}["{dst}"]'
    return run("sv", "merge", DBM_NAME, "--into", CHAR_A, "--key", key, "--yes", *extra)


def _journal_file(user_data: Path, record_id: str) -> Path:
    return user_data / "store" / "journal" / f"{record_id}.json"


def _edit_record(user_data: Path, record_id: str, **changes: Any) -> None:
    """Constructed: rewrite one journal record in the temporary user data."""
    path = _journal_file(user_data, record_id)
    raw = json.loads(path.read_text("ascii"))
    for name, value in changes.items():
        if name == "after":
            for entry in raw["paths"]:
                if entry["path"] == LAB_A:
                    entry["after"] = value
        else:
            raw[name] = value
    path.write_text(json.dumps(raw, ensure_ascii=True, sort_keys=True, indent=1) + "\n", "ascii")


def _future_date_snapshot(snapshot_id: str) -> str:
    """Constructed: the snapshot's manifest as a clock that ran ahead would
    have written it (its id and `created_at` in 2099; entries, and so the
    fingerprint, unchanged). Returns the new id."""
    store = _store()
    manifest = store.show(snapshot_id)
    new_id = f"{FUTURE_ID_PREFIX}-{manifest.fingerprint}"
    moved = manifest.model_copy(update={"id": new_id, "created_at": FUTURE})
    (store.manifests_dir / f"{new_id}.json").write_bytes(snapshot.manifest_bytes(moved))
    (store.manifests_dir / f"{snapshot_id}.json").unlink()
    assert store.show(new_id).created_at == FUTURE
    return new_id


def _plant_object(data: bytes) -> str:
    """Constructed: put `data` into the store as an object (zlib, named by its
    SHA-256, as the store keeps objects)."""
    digest = _sha(data)
    path = _store().object_path(digest)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(zlib.compress(data))
    return digest


def _refused(result: Any) -> None:
    assert result.exit_code == 3, _out(result)
    assert "refused by the loader check" in result.stderr


# ─── the security reviewer's regression ─────────────────────────────────────


def _failed_once_no_snapshot(flavor: Path) -> None:
    """A session that loaded nothing wrote `loads = 1`; no snapshot since."""
    _disk(flavor).write_bytes(_a(1))


def _persisting_one_snapshot(flavor: Path) -> None:
    """The bug persists: a snapshot of the reset, then the next session
    writes the same bytes again."""
    _disk(flavor).write_bytes(_a(1))
    _snap("after the reset")
    _disk(flavor).write_bytes(_a(1))


def _persisting_snapshot_each_session(flavor: Path) -> None:
    """The bug persists and each session is snapshotted: two byte-identical
    snapshots of the reset (N, 1, 1)."""
    _disk(flavor).write_bytes(_a(1))
    _snap("after the reset")
    _disk(flavor).write_bytes(_a(1))
    _snap("after the next session")


@pytest.mark.xfail(strict=True, reason="M11-24 not implemented")
@pytest.mark.parametrize(
    "failure",
    [_failed_once_no_snapshot, _persisting_one_snapshot, _persisting_snapshot_each_session],
    ids=["failed-once-no-snapshot", "persisting-one-snapshot", "persisting-snapshot-each-session"],
)
def test_failed_load_after_a_merge_of_wowlab_lua_is_refused_constructed(
    root: Path, flavor: Path, user_data: Path, failure: Any
) -> None:
    # Snapshot at 4, a login raises it to 5, snapshot, then `sv merge` of the
    # file itself (probe kept at 5). Guard's pre-write snapshot holds the 5,
    # so the comparison after the merge spans the next login too. A session
    # whose load failed then writes loads 1: the old skip passed this.
    _snap("loads 4")
    _disk(flavor).write_bytes(_a(5))
    _snap("loads 5")
    ok("sv", "merge", LAB, "--from", CHAR_B, "--into", CHAR_A, "--take", "theirs", "--yes")
    assert _probe(_disk(flavor).read_bytes()) == {"loads": 5}, "the target's probe is kept"
    failure(flavor)
    before = _frozen(root, user_data)
    result = _copy_within()
    _refused(result)
    assert "loads" in result.stderr
    assert _frozen(root, user_data) == before, "install, store and journal unchanged"
    assert (flavor / DBM).read_bytes() == REAL_DBM


# ─── rule 1: no session since the Lab write ─────────────────────────────────


def _restore_over_a_login(flavor: Path) -> None:
    """Snapshot at 4, a login raises it to 5, `snap restore` puts the 4 back
    (guard's pre-write snapshot holds the 5)."""
    four = _snap("loads 4")
    _disk(flavor).write_bytes(_a(5))
    _restore_lab(four)
    assert _disk(flavor).read_bytes() == REAL_A


def _merge_of_the_file(flavor: Path) -> None:
    """Snapshot at 4, then `sv merge` of the file itself (another gear block,
    `loads` still 4; guard's pre-write snapshot holds the real file)."""
    _snap("loads 4")
    _merge_gear_into_wowlab_lua()
    assert _disk(flavor).read_bytes() != REAL_A


@pytest.mark.xfail(strict=True, reason="M11-24 not implemented")
@pytest.mark.parametrize(
    "lab_write", [_restore_over_a_login, _merge_of_the_file], ids=["snap-restore", "sv-merge"]
)
def test_rule1_lab_write_then_merge_passes_with_the_note_in_text_and_json_constructed(
    flavor: Path, lab_write: Any
) -> None:
    lab_write(flavor)
    written = _disk(flavor).read_bytes()
    assert _after(_last_lab_write()) == _sha(written), "the journal's after is what is on disk"
    text = _copy_to("Labcharb Labrealmf")
    assert text.exit_code == 0, _out(text)
    assert LAB_NOTE in _out(text)
    assert (flavor / DBM).read_bytes() != REAL_DBM
    # Still no session since the write: the text run's own guard write is of
    # the account file, and its pre-write snapshot holds W's bytes.
    result = _copy_to("Labcharb Labrealmd", "--json")
    assert result.exit_code == 0, _out(result)
    report = _report(result)
    assert report["written"] is True
    assert any(LAB_NOTE in note for note in report["notes"]), report["notes"]
    assert not any("did not load" in note for note in report["notes"]), report["notes"]


# ─── rule 2: a session since the Lab write ──────────────────────────────────


@pytest.mark.xfail(strict=True, reason="M11-24 not implemented")
def test_rule2_session_after_a_restore_that_raised_loads_passes_constructed(
    flavor: Path,
) -> None:
    # The restore wrote 4; a login then raised it to 5 and changed a filter,
    # and a snapshot was taken. The pair guard (5) -> later (5, other bytes)
    # spans the restore: compared from W's result (4), loads went up.
    _restore_over_a_login(flavor)
    _disk(flavor).write_bytes(_once(_a(5), *_FILTER))
    _snap("after a login")
    result = _copy_within("--json")
    assert result.exit_code == 0, _out(result)
    report = _report(result)
    assert report["written"] is True
    assert not any(LAB_NOTE in note for note in report["notes"]), "rule 2 re-checked the loader"
    assert not any(NO_LOGIN_NOTE in note for note in report["notes"]), report["notes"]
    assert (flavor / DBM).read_bytes() != REAL_DBM


def test_rule2_session_after_a_restore_that_raised_loads_passes_on_disk_constructed(
    flavor: Path,
) -> None:
    # Control, green today: the same session with no snapshot after it. The
    # disk (5, other bytes) against W's result (4) goes up.
    _restore_over_a_login(flavor)
    _disk(flavor).write_bytes(_once(_a(5), *_FILTER))
    result = _copy_within("--json")
    assert result.exit_code == 0, _out(result)
    report = _report(result)
    assert report["written"] is True
    assert not any(LAB_NOTE in note for note in report["notes"]), "rule 2 re-checked the loader"


@pytest.mark.xfail(strict=True, reason="M11-24 not implemented")
def test_rule2_sv_merge_then_a_session_that_raised_loads_passes_from_the_kept_object_constructed(
    flavor: Path,
) -> None:
    _merge_of_the_file(flavor)
    merged = _disk(flavor).read_bytes()
    record = _last_lab_write()
    assert _store().read_object(_after(record)) == merged, "the merge kept the bytes it wrote"
    _disk(flavor).write_bytes(_loads(merged, 5))
    result = _copy_within("--json")
    assert result.exit_code == 0, _out(result)
    report = _report(result)
    assert report["written"] is True
    assert not any(LAB_NOTE in note for note in report["notes"]), "rule 2 re-checked the loader"


@pytest.mark.xfail(strict=True, reason="M11-24 not implemented")
def test_rule2_persisting_reset_after_a_restore_is_refused_constructed(
    root: Path, flavor: Path, user_data: Path
) -> None:
    # The restore wrote 4; every session since loaded nothing and wrote the
    # same `loads = 1` file, snapshotted twice. The two newest snapshots are
    # identical, so the check walks back to guard's (5); that comparison spans
    # the restore and is made from W's result (4): it went down.
    _restore_over_a_login(flavor)
    _disk(flavor).write_bytes(_a(1))
    _snap("after a session")
    _disk(flavor).write_bytes(_a(1))
    _snap("after the next session")
    before = _frozen(root, user_data)
    result = _copy_within()
    _refused(result)
    assert "loads" in result.stderr
    assert _frozen(root, user_data) == before
    assert (flavor / DBM).read_bytes() == REAL_DBM


@pytest.mark.xfail(strict=True, reason="M11-24 not implemented")
@pytest.mark.parametrize(
    "session",
    [
        pytest.param(lambda merged: _once(merged, *_FILTER), id="one-value-changed"),
        pytest.param(lambda merged: merged + b"\r\n", id="whitespace-only-constructed"),
    ],
)
def test_rule2_equal_loads_with_other_bytes_after_an_sv_merge_is_refused_constructed(
    root: Path, flavor: Path, user_data: Path, session: Any
) -> None:
    # The merge wrote loads 4. A session since left loads at 4 and wrote other
    # bytes: equal is refused unless byte-identical. (Against guard's snapshot,
    # the real file at 4, the M11-09 disk rule would pass it.)
    _merge_of_the_file(flavor)
    merged = _disk(flavor).read_bytes()
    _disk(flavor).write_bytes(session(merged))
    before = _frozen(root, user_data)
    result = _copy_within()
    _refused(result)
    assert "loads" in result.stderr
    assert _frozen(root, user_data) == before


# ─── rule 3: W's result cannot be read ──────────────────────────────────────


def _object_missing(user_data: Path, record: guard.HistoryRecord) -> None:
    """Deleted: also what a write from before the rule leaves (no object)."""
    _store().object_path(_after(record)).unlink()


def _object_damaged(user_data: Path, record: guard.HistoryRecord) -> None:
    path = _store().object_path(_after(record))
    path.unlink()
    path.write_bytes(zlib.compress(b"\r\nWowLabCharDB = {\r\n}\r\n"))


def _object_unparseable(user_data: Path, record: guard.HistoryRecord) -> None:
    """The record's after names a sound object whose bytes are not data."""
    digest = _plant_object(b"\r\nWowLabCharDB = function() end\r\n")
    _edit_record(user_data, record.id, after=digest)


@pytest.mark.xfail(strict=True, reason="M11-24 not implemented")
@pytest.mark.parametrize(
    "damage",
    [_object_missing, _object_damaged, _object_unparseable],
    ids=[
        "object-missing-or-predates-the-rule-constructed",
        "object-damaged-constructed",
        "object-unparseable-constructed",
    ],
)
def test_rule3_unreadable_result_of_the_lab_write_is_refused_naming_the_record(
    root: Path, flavor: Path, user_data: Path, damage: Any
) -> None:
    # `sv merge` wrote loads 4; a session since raised it to 5. Rule 2 needs
    # W's result, which cannot be read: refused, never skipped.
    _merge_of_the_file(flavor)
    merged = _disk(flavor).read_bytes()
    record = _last_lab_write()
    assert _store().object_path(_after(record)).is_file(), "the merge kept the bytes it wrote"
    _disk(flavor).write_bytes(_loads(merged, 5))
    damage(user_data, record)
    before = _frozen(root, user_data)
    result = _copy_within()
    _refused(result)
    assert record.id in result.stderr, "the refusal names the journal record"
    assert _frozen(root, user_data) == before
    assert (flavor / DBM).read_bytes() == REAL_DBM

    forced = _copy_within("--force-loader-check", "--json")
    assert forced.exit_code == 0, _out(forced)
    report = _report(forced)
    assert report["written"] is True
    assert any(record.id in note for note in report["notes"]), report["notes"]
    assert (flavor / DBM).read_bytes() != REAL_DBM


@pytest.mark.xfail(strict=True, reason="M11-24 not implemented")
@pytest.mark.parametrize(
    "damage",
    [_object_missing, _object_damaged],
    ids=["object-missing-constructed", "object-damaged-constructed"],
)
def test_rule1_wins_over_rule3_when_no_session_ran_since_the_lab_write(
    flavor: Path, user_data: Path, damage: Any
) -> None:
    # Conductor ruling on M11-24T2 (5): the disk is byte-identical to W's
    # `after`, which proves no session ran, so nothing needs reading and an
    # unreadable object does not refuse. The write is an `sv merge` of the
    # file: a restore's `after` is the restored snapshot's own object, and
    # removing it would also break the snapshot comparison the check reads.
    _merge_of_the_file(flavor)
    record = _last_lab_write()
    assert _store().object_path(_after(record)).is_file(), "the merge kept the bytes it wrote"
    damage(user_data, record)
    assert _sha(_disk(flavor).read_bytes()) == _after(record), "no session since W"
    result = _copy_within("--json")
    assert result.exit_code == 0, _out(result)
    report = _report(result)
    assert report["written"] is True
    assert any(LAB_NOTE in note for note in report["notes"]), report["notes"]
    assert not any("--force-loader-check" in note for note in report["notes"]), report["notes"]
    assert (flavor / DBM).read_bytes() != REAL_DBM


# ─── every Lab write to WowLab.lua keeps the bytes it wrote ─────────────────


@pytest.mark.xfail(strict=True, reason="M11-24 not implemented")
@pytest.mark.parametrize(
    "args",
    [("--take", "theirs"), ("--key", "WowLabCharDB.gear")],
    ids=["take-theirs", "key-copy"],
)
def test_sv_merge_of_wowlab_lua_keeps_the_written_bytes_as_a_store_object(
    flavor: Path, args: tuple[str, ...]
) -> None:
    ok("sv", "merge", LAB, "--from", CHAR_B, "--into", CHAR_A, *args, "--yes")
    written = _disk(flavor).read_bytes()
    assert written != REAL_A
    record = _last_lab_write()
    assert _after(record) == _sha(written)
    assert _store().read_object(_after(record), size=len(written)) == written


def test_snap_restore_of_wowlab_lua_leaves_the_written_bytes_in_the_store(flavor: Path) -> None:
    # Control, green today: a restore writes a snapshot's bytes, which the
    # store already holds.
    _restore_over_a_login(flavor)
    record = _last_lab_write()
    assert _after(record) == _sha(REAL_A)
    assert _store().read_object(_after(record), size=len(REAL_A)) == REAL_A


def test_profile_apply_never_writes_wowlab_lua_constructed(flavor: Path) -> None:
    # Control, green today: §13.3 has every profile leave the lab-addon's
    # WowLab.lua alone, so an apply has no bytes of it to keep, and the loader
    # check sees no Lab write of it. The apply below does write (the account
    # file it restores), and the WowLab.lua edited since the save stays.
    ok("profile", "save", "addons", "--preset", "addons")
    _disk(flavor).write_bytes(_a(5))
    (flavor / DBM).write_bytes(REAL_DBM.replace(b'["Enabled"] = true,', b'["Enabled"] = false,', 1))
    ok("profile", "apply", "addons", "--yes")
    assert (flavor / DBM).read_bytes() == REAL_DBM, "the apply wrote"
    assert _disk(flavor).read_bytes() == _a(5)
    assert guard.history(), "a committed guard write"
    assert _lab_writes() == []


# ─── future-dated journal records and snapshots are ignored ─────────────────


@pytest.mark.xfail(strict=True, reason="M11-24 not implemented")
def test_future_dated_journal_record_is_ignored_and_does_not_turn_the_check_off_constructed(
    root: Path, flavor: Path, user_data: Path
) -> None:
    # A first login (loads 1) snapshotted; a login raises it to 2; the owner
    # restores the 1 (W wrote the loads-1 bytes). A login raises it to 2 again,
    # snapshotted; then a session loads nothing and writes the loads-1 bytes.
    # W is older than that snapshot, so the disk (1) against it (2) went down.
    # W's record then says 2099: counted as the latest write, the disk would
    # match its `after` and pass under rule 1.
    _disk(flavor).write_bytes(_a(1))
    first = _snap("loads 1")
    _disk(flavor).write_bytes(_a(2))
    _restore_lab(first)
    record = _last_lab_write()
    assert _after(record) == _sha(_a(1))
    _disk(flavor).write_bytes(_a(2))
    _snap("loads 2")
    _disk(flavor).write_bytes(_a(1))
    _edit_record(user_data, record.id, created_at=FUTURE)
    assert guard.history()[-1].created_at == FUTURE

    before = _frozen(root, user_data)
    result = _copy_within()
    _refused(result)
    assert "loads on disk (1) went down from the newest snapshot (2)" in result.stderr
    assert record.id in _out(result), "the note names the ignored record"
    assert _frozen(root, user_data) == before

    forced = _copy_within("--force-loader-check", "--json")
    assert forced.exit_code == 0, _out(forced)
    notes = _report(forced)["notes"]
    assert any(record.id in n and "--force-loader-check" not in n for n in notes), notes


def _snapshot_ahead_then_two_logins(flavor: Path) -> str:
    """A snapshot at 4 taken while the clock ran ahead (2099), then two logins
    (5, 6), each snapshotted with the clock right. Returns the 2099 id."""
    ahead = _future_date_snapshot(_snap("loads 4, clock ahead"))
    _disk(flavor).write_bytes(_a(5))
    _snap("loads 5")
    _disk(flavor).write_bytes(_a(6))
    _snap("loads 6")
    return ahead


@pytest.mark.xfail(strict=True, reason="M11-24 not implemented")
def test_future_dated_snapshot_is_ignored_with_a_note_constructed(flavor: Path) -> None:
    # Sorted by date, the 2099 snapshot (4) would be the newest and the pair
    # 6 -> 4 would read as a loader failure. Ignored, the pair is 5 -> 6.
    ahead = _snapshot_ahead_then_two_logins(flavor)
    text = _copy_to("Labcharb Labrealmf")
    assert text.exit_code == 0, _out(text)
    assert ahead in _out(text)
    result = _copy_to("Labcharb Labrealmd", "--json")
    assert result.exit_code == 0, _out(result)
    report = _report(result)
    assert report["written"] is True
    assert any(ahead in note for note in report["notes"]), report["notes"]


@pytest.mark.xfail(strict=True, reason="M11-24 not implemented")
def test_future_dated_snapshot_does_not_turn_the_check_off_constructed(
    root: Path, flavor: Path, user_data: Path
) -> None:
    # The same store, then a session that loaded nothing: the disk (1) is
    # compared with the newest snapshot by the right clock (6), not the 2099 one.
    ahead = _snapshot_ahead_then_two_logins(flavor)
    _disk(flavor).write_bytes(_a(1))
    before = _frozen(root, user_data)
    result = _copy_within()
    _refused(result)
    assert "loads on disk (1) went down from the newest snapshot (6)" in result.stderr
    assert ahead in _out(result), "the note names the ignored snapshot"
    assert _frozen(root, user_data) == before
