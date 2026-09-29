"""`wowlab sv merge` loader check: `probe` and identical snapshots (M11-24T).

Graders for M11-24, written before it (ADR-0013; `svmerge.py` is load-bearing,
owner, 2026-09-29). Every grader of new behaviour carries one
`xfail(strict=True)` marker line, which M11-24 deletes and does not otherwise
edit. Tests without a marker pass on main today and must stay green: they are
the controls that keep the new skips from hiding a real loader failure.

Every expectation comes from docs/LAB_PLAN.md §13.4, the bullet
"2026-09-29, owner ruling for M11-24", read with the M11-09 rulings above it:

- (a) **`probe` stays ours.** When `WowLab.lua` itself is merged, the
  `probe` table of the `--into` file is kept whatever `--take` or `--key`
  says: it counts that character's logins.
- (b) **Walk past identical snapshots.** When the two newest snapshots of the
  file are byte-identical, the check compares against the newest snapshot
  whose bytes differ from them. The "no login between the two snapshots"
  pass applies only when no older differing snapshot exists.
- (c) **A Lab write is not a loader failure.** A comparison that spans a
  committed guard write to that `WowLab.lua` (a `snap restore`, a profile
  apply, an earlier `sv merge`; the journal records each) is skipped, with the
  note "the Lab wrote WowLab.lua after that snapshot; the loader was not
  re-checked".

Conductor rulings on M11-24T (2026-09-29), within the owner's ruling:

1. (a) is a rule of `wowlab sv merge` on a per-character `WowLab.lua`, not of
   the generic `wowlab_core.svmerge.merge`, which knows no file names. The
   M11-09T library graders still expect a plain `merge()` to list and take
   the probe like any other leaf.
2. The probe is not a conflict: its paths appear in neither `conflicts` nor
   `taken`, and `notes` holds one entry, exactly "probe kept from the target:
   it counts that character's logins (§13.4)", in text and `--json`. When the
   probe was the only difference, the merge exits 0, writes nothing and says
   so as the no-change path does.
3. The Lab-write note prints whenever a comparison was skipped, whether or
   not the check would have passed.
4. The (b) refusal names the older differing snapshot's id.

Real fixtures (L8): the two characters' `WowLab.lua` from the M11-03 capture
(`probe.loads` 4 and 2) and the account-wide `DBM-Party-Vanilla.lua`, in the
captured tree copied into `tmp_path` as `test_cli.py` builds it, with the user
data directory (store, journal, locks) redirected into `tmp_path` and a fake
process table. Constructed (labelled in the test names): edited copies of the
first character's real file standing for another session (`loads` set to 1,
2 or 5; one other value changed). Nothing reads or writes a real install.
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from test_cli import (
    SYNDICATOR,
    _edit_syndicator,
    _state,
    flavor,  # noqa: F401
    idle,  # noqa: F401  (autouse: no client in the process table)
    ok,
    replayed,  # noqa: F401  (autouse: recorded game data only)
    root,  # noqa: F401
    run,
    user_data,  # noqa: F401
)
from test_svmerge import (
    _EQUIPPED,
    _FILTER,
    _LOADS_5,
    CHAR_A,
    CHAR_B,
    DBM,
    LAB,
    LAB_A,
    LAB_B,
    NO_LOGIN_NOTE,
    PROBE_NOTE,
    REAL_A,
    REAL_B,
    REAL_DBM,
    _copy_within,
    _once,
    _out,
    _report,
    _snap,
    _under_probe,
)

from wowlab_core import guard, luadata

LAB_NOTE = "the Lab wrote WowLab.lua after that snapshot; the loader was not re-checked"
PROBE_A = b'\r\n["probe"] = {\r\n["loads"] = 4,\r\n},\r\n'  # the first character's, as written

_LOADS_1 = (b'["loads"] = 4,', b'["loads"] = 1,')
_LOADS_2 = (b'["loads"] = 4,', b'["loads"] = 2,')
_SHIFT = (b'"show_tooltips_on_shift"] = false', b'"show_tooltips_on_shift"] = true')


def _probe(data: bytes) -> object:
    return luadata.parse(data).to_python()["WowLabCharDB"]["probe"]


def _probe_kept(report: dict[str, Any]) -> None:
    """The probe is neither a conflict nor taken, and one note says it was kept."""
    assert not [c["path"] for c in report["conflicts"] if _under_probe(c["path"])]
    assert not [t["path"] for t in report["taken"] if _under_probe(t["path"])]
    assert report["notes"].count(PROBE_NOTE) == 1, report["notes"]


def _frozen(root: Path, user_data: Path) -> tuple[object, ...]:
    """The install, the store and the journal: what a refusal leaves alone."""
    return _state(root), _state(user_data), guard.history()


def _restore_lab(snapshot_id: str) -> None:
    """A committed guard write of the first character's `WowLab.lua`."""
    ok("snap", "restore", snapshot_id, "--paths", LAB_A, "--yes")


def _merge_lab(*args: str) -> Any:
    return run("sv", "merge", LAB, "--into", CHAR_A, "--yes", "--json", *args)


# ─── (a) probe stays ours when WowLab.lua itself is merged ──────────────────


@pytest.mark.xfail(strict=True, reason="M11-24 not implemented")
def test_take_theirs_keeps_the_targets_probe_and_does_not_report_it_taken(flavor: Path) -> None:
    result = _merge_lab("--from", CHAR_B, "--take", "theirs")
    assert result.exit_code == 0, _out(result)
    report = _report(result)
    assert report["written"] is True
    out = (flavor / LAB_A).read_bytes()
    assert _probe(out) == {"loads": 4}, "the target's login counter, not the source's 2"
    assert PROBE_A in out, "the probe keeps its bytes"
    _probe_kept(report)
    # Everything else was taken: the gear block is the source's.
    merged = luadata.parse(out).to_python()["WowLabCharDB"]
    assert merged["gear"] == luadata.parse(REAL_B).to_python()["WowLabCharDB"]["gear"]


@pytest.mark.xfail(strict=True, reason="M11-24 not implemented")
def test_key_copy_of_the_whole_variable_keeps_the_targets_probe(flavor: Path) -> None:
    # Two-way `--key` copies a subtree whole (§13.4); the probe inside it is
    # the one part that stays ours.
    result = _merge_lab("--from", CHAR_B, "--key", "WowLabCharDB")
    assert result.exit_code == 0, _out(result)
    report = _report(result)
    assert report["written"] is True and report["conflicts"] == []
    out = (flavor / LAB_A).read_bytes()
    expected = luadata.parse(REAL_B).to_python()
    expected["WowLabCharDB"]["probe"] = {"loads": 4}
    assert luadata.parse(out).to_python() == expected
    assert PROBE_A in out
    assert b"\t" not in out and out.count(b"\n") == out.count(b"\r\n"), "the target's layout"
    _probe_kept(report)


@pytest.mark.xfail(strict=True, reason="M11-24 not implemented")
@pytest.mark.parametrize(
    "key",
    ["WowLabCharDB.probe", "WowLabCharDB.probe.loads", 'WowLabCharDB["probe"]["loads"]'],
    ids=["probe", "probe.loads", "probe-loads-bracketed"],
)
def test_key_naming_the_probe_changes_nothing(root: Path, flavor: Path, key: str) -> None:
    result = _merge_lab("--from", CHAR_B, "--key", key)
    assert result.exit_code == 0, _out(result)
    report = _report(result)
    assert report["written"] is False
    _probe_kept(report)
    assert (flavor / LAB_A).read_bytes() == REAL_A
    assert guard.history() == ()


@pytest.mark.xfail(strict=True, reason="M11-24 not implemented")
def test_key_copy_within_the_file_onto_the_probe_changes_nothing(flavor: Path) -> None:
    # `schema` is 1, the value a failed load leaves in `loads`: copying it onto
    # the counter would fake exactly the reset the loader check looks for.
    result = _merge_lab("--key", "WowLabCharDB.schema=WowLabCharDB.probe.loads")
    assert result.exit_code == 0, _out(result)
    report = _report(result)
    assert report["written"] is False
    _probe_kept(report)
    assert (flavor / LAB_A).read_bytes() == REAL_A
    assert guard.history() == ()


@pytest.mark.xfail(strict=True, reason="M11-24 not implemented")
def test_three_way_keeps_the_targets_probe_when_only_theirs_changed_it_constructed(
    flavor: Path,
) -> None:
    # Theirs (a snapshot): loads 2 and another average. Base: the real file.
    # Ours (the disk): the real file with another currency filter. The probe
    # changed on theirs only, which the three-way rule would take; it stays.
    # The loader check passes: 2 then 4 across the snapshots, 4 on disk.
    target = flavor / LAB_A
    target.write_bytes(_once(_once(REAL_A, *_LOADS_2), *_EQUIPPED))
    theirs_id = _snap("theirs")
    target.write_bytes(REAL_A)
    base_id = _snap("base")
    ours = _once(REAL_A, *_FILTER)
    target.write_bytes(ours)
    result = _merge_lab("--from", theirs_id, "--base", base_id)
    assert result.exit_code == 0, _out(result)
    report = _report(result)
    assert report["mode"] == "three-way" and report["written"] is True
    assert report["conflicts"] == []
    _probe_kept(report)
    assert target.read_bytes() == _once(ours, *_EQUIPPED)


@pytest.mark.xfail(strict=True, reason="M11-24 not implemented")
def test_probe_as_the_only_difference_writes_nothing_and_says_so_constructed(
    root: Path, flavor: Path
) -> None:
    # The second character's file replaced by the first's with loads 2: the
    # two files differ only in the probe. No --take: nothing is left to resolve.
    (flavor / LAB_B).write_bytes(_once(REAL_A, *_LOADS_2))
    before = _state(root)
    result = _merge_lab("--from", CHAR_B)
    assert result.exit_code == 0, _out(result)
    report = _report(result)
    assert report["written"] is False
    assert report["conflicts"] == [] and report["taken"] == []
    assert report["notes"].count(PROBE_NOTE) == 1, report["notes"]
    text = run("sv", "merge", LAB, "--from", CHAR_B, "--into", CHAR_A, "--yes")
    assert text.exit_code == 0, _out(text)
    assert "Nothing to change" in _out(text), "the no-change path's wording"
    assert PROBE_NOTE in _out(text)
    assert _state(root) == before
    assert guard.history() == ()


# ─── (b) walk past byte-identical snapshots ─────────────────────────────────


def _two_identical_by_snapshot(flavor: Path) -> None:
    _snap("after the reset")
    _snap("again, no login since")


def _two_identical_by_unrelated_writes(flavor: Path) -> None:
    # Guard takes a snapshot before every write (ADR-0021): two restores of
    # another file leave two snapshots holding the same `WowLab.lua`. The
    # journal records `Syndicator.lua`, not `WowLab.lua`, so (c) does not apply.
    first = _snap("for the restores")
    for _ in range(2):
        _edit_syndicator(flavor, *_SHIFT)
        ok("snap", "restore", first, "--paths", SYNDICATOR, "--yes")


@pytest.mark.xfail(strict=True, reason="M11-24 not implemented")
@pytest.mark.parametrize(
    "identical",
    [_two_identical_by_snapshot, _two_identical_by_unrelated_writes],
    ids=["snap-create-twice", "two-unrelated-guard-writes"],
)
def test_reset_then_two_identical_snapshots_is_refused_constructed(
    root: Path, flavor: Path, user_data: Path, identical: Any
) -> None:
    older = _snap("loads 4")
    (flavor / LAB_A).write_bytes(_once(REAL_A, *_LOADS_1))  # a session that loaded nothing
    identical(flavor)
    before = _frozen(root, user_data)
    result = _copy_within()
    assert result.exit_code == 3, _out(result)
    assert "refused by the loader check" in result.stderr
    assert "went down" in result.stderr
    assert older in result.stderr, "the older snapshot that shows the drop is the one read"
    assert _frozen(root, user_data) == before
    assert (flavor / DBM).read_bytes() == REAL_DBM


@pytest.mark.xfail(strict=True, reason="M11-24 not implemented")
def test_walk_back_to_a_lower_count_passes_without_the_no_login_note(flavor: Path) -> None:
    # 2, then 4 twice (the second character's real file stands in for the
    # earlier session, as in the M11-09T graders): loads went up.
    (flavor / LAB_A).write_bytes(REAL_B)
    _snap("loads 2")
    (flavor / LAB_A).write_bytes(REAL_A)
    _snap("loads 4")
    _snap("loads 4, no login since")
    result = _copy_within("--json")
    assert result.exit_code == 0, _out(result)
    report = _report(result)
    assert report["written"] is True
    assert not any(NO_LOGIN_NOTE in note for note in report["notes"]), report["notes"]
    assert (flavor / DBM).read_bytes() != REAL_DBM


@pytest.mark.xfail(strict=True, reason="M11-24 not implemented")
def test_walk_back_to_an_equal_count_with_other_bytes_is_refused_constructed(
    root: Path, flavor: Path, user_data: Path
) -> None:
    # 4 (another filter), then 4 twice: the client wrote and the counter did
    # not go up, what a persisting loader bug leaves (§13.4, "loads equal").
    (flavor / LAB_A).write_bytes(_once(REAL_A, *_FILTER))
    older = _snap("loads 4, filter 2")
    (flavor / LAB_A).write_bytes(REAL_A)
    _snap("loads 4")
    _snap("loads 4, no login since")
    before = _frozen(root, user_data)
    result = _copy_within()
    assert result.exit_code == 3, _out(result)
    assert "refused by the loader check" in result.stderr
    assert "loads" in result.stderr and older in result.stderr
    assert _frozen(root, user_data) == before


def test_identical_snapshots_with_no_older_differing_one_keep_the_note(flavor: Path) -> None:
    # Control, green today: three snapshots, one set of bytes. Nothing older
    # differs, so the "no login" pass still applies.
    for label in ("first", "second", "third"):
        _snap(label)
    result = _copy_within("--json")
    assert result.exit_code == 0, _out(result)
    report = _report(result)
    assert report["written"] is True
    assert any(NO_LOGIN_NOTE in note for note in report["notes"]), report["notes"]


# ─── (c) a Lab write to WowLab.lua is not a loader failure ──────────────────


def _restore_after_a_login(flavor: Path) -> str:
    """Snapshot at 4, a login raises it to 5, then `snap restore` puts the 4
    back through guard (whose pre-write snapshot holds the 5). Returns the
    restored snapshot's id."""
    four = _snap("loads 4")
    (flavor / LAB_A).write_bytes(_once(REAL_A, *_LOADS_5))
    _restore_lab(four)
    assert (flavor / LAB_A).read_bytes() == REAL_A
    return four


def _nothing_after(flavor: Path) -> None:
    """The disk (4) against the guard's snapshot (5) spans the restore."""


def _one_snapshot_after(flavor: Path) -> None:
    """The guard's snapshot (5) against a later one (4) spans the restore."""
    _snap("after the restore")


def _two_snapshots_after(flavor: Path) -> None:
    """Two identical snapshots (4); walking back reaches the guard's (5)."""
    _snap("after the restore")
    _snap("after the restore, again")


@pytest.mark.xfail(strict=True, reason="M11-24 not implemented")
@pytest.mark.parametrize(
    "after",
    [_nothing_after, _one_snapshot_after, _two_snapshots_after],
    ids=["merge-at-once", "one-snapshot-then-merge", "two-snapshots-then-merge"],
)
def test_restore_of_wowlab_lua_then_merge_is_not_blamed_on_the_loader_constructed(
    flavor: Path, after: Any
) -> None:
    _restore_after_a_login(flavor)
    after(flavor)
    result = _copy_within("--json")
    assert result.exit_code == 0, _out(result)
    report = _report(result)
    assert report["written"] is True
    assert any(LAB_NOTE in note for note in report["notes"]), report["notes"]
    assert not any("did not load" in note for note in report["notes"]), report["notes"]
    assert (flavor / DBM).read_bytes() != REAL_DBM


@pytest.mark.xfail(strict=True, reason="M11-24 not implemented")
def test_restore_then_merge_prints_the_note_in_text_output_constructed(flavor: Path) -> None:
    _restore_after_a_login(flavor)
    result = _copy_within()
    assert result.exit_code == 0, _out(result)
    assert LAB_NOTE in _out(result)
    assert (flavor / DBM).read_bytes() != REAL_DBM


def _merge_gear_into_wowlab_lua() -> None:
    """An earlier `sv merge` of the first character's `WowLab.lua` (another
    gear block, `loads` still 4): a committed guard write to that file."""
    ok(
        "sv",
        "merge",
        LAB,
        "--from",
        CHAR_B,
        "--into",
        CHAR_A,
        "--key",
        "WowLabCharDB.gear",
        "--yes",
    )


@pytest.mark.xfail(strict=True, reason="M11-24 not implemented")
@pytest.mark.parametrize(
    "then_snapshot",
    [False, True],
    ids=["would-have-passed-disk-equal-loads", "would-have-refused-pair-equal-loads"],
)
def test_earlier_sv_merge_of_wowlab_lua_skips_the_comparison_with_the_note(
    flavor: Path, then_snapshot: bool
) -> None:
    # Snapshot at 4, then a merge of the file itself. Without --then_snapshot
    # the disk (4, other bytes) against the guard's snapshot (4) would pass
    # today with no note; with it the pair guard (4) -> later (4, other bytes)
    # would be refused as "did not go up". Both span the Lab write: skipped,
    # with the note (conductor ruling 3).
    _snap("loads 4")
    _merge_gear_into_wowlab_lua()
    if then_snapshot:
        _snap("after the merge")
    result = _copy_within("--json")
    assert result.exit_code == 0, _out(result)
    report = _report(result)
    assert report["written"] is True
    assert any(LAB_NOTE in note for note in report["notes"]), report["notes"]
    assert (flavor / DBM).read_bytes() != REAL_DBM


def _reset_on_disk(flavor: Path) -> None:
    """After the restore: a snapshot at 4, then a session that loaded nothing."""
    _snap("after the restore")
    (flavor / LAB_A).write_bytes(_once(REAL_A, *_LOADS_1))


def _reset_then_snapshot(flavor: Path) -> None:
    _reset_on_disk(flavor)
    _snap("after the reset")


@pytest.mark.parametrize(
    "failure",
    [_reset_on_disk, _reset_then_snapshot],
    ids=["disk-below-a-later-snapshot", "later-pair-goes-down"],
)
def test_loader_failure_after_a_restore_is_still_refused_constructed(
    root: Path, flavor: Path, user_data: Path, failure: Any
) -> None:
    # Control, green today: the restore is older than the comparison that
    # shows the reset, so nothing skips it.
    _restore_after_a_login(flavor)
    failure(flavor)
    before = _frozen(root, user_data)
    result = _copy_within()
    assert result.exit_code == 3, _out(result)
    assert "refused by the loader check" in result.stderr
    assert "went down" in result.stderr
    assert _frozen(root, user_data) == before
