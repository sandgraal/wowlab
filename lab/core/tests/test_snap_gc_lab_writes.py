"""`wowlab snap gc` keeps only what the loader check can need (M11-31T).

Graders for M11-31 (a), written before it (ADR-0013). The loader check of
`wowlab sv merge` compares from objects that gc decides to keep, so the rule
that narrows what gc keeps is graded by a test-writer first. Every grader of
new behaviour carries one `xfail(strict=True)` marker line, which M11-31
deletes and does not otherwise edit. Tests without a marker pass on main
today and must stay green: they are the controls that stop the narrowing
from removing what the check reads.

Every expectation comes from docs/LAB_PLAN.md §13.4 (the owner ruling
amending "A Lab write is not a loader failure", rule 2, and the conductor
ruling "gc keeps them", which hands the narrowing to M11-31) and the text of
M11-31 (a) in docs/BACKLOG.md:

- Rule 2 compares from W's result, "the store object whose hash is W's
  `after`", where W is the latest committed guard write to the `--into`
  character's `WowLab.lua` in the span of a comparison. The check can compare
  any character's file and walk back over older spans, so `snap gc` keeps the
  `after` of every committed write to a character's `WowLab.lua`, not only
  the latest, whether or not a snapshot still refers to it.
- M11-31 (a): "keep only what the loader check can need (the `after` of
  writes to a character's `WowLab.lua`)". Any other `after` a committed
  journal record names (content a `snap restore` or profile apply wrote from
  a snapshot the owner later deletes) is collected like any other object no
  snapshot refers to: a dry run lists it and a real run removes it.
- `snap gc --help` says what is kept.

Choices made here where the text leaves room:

- Keeping is per written path, not per journal record. When one `snap
  restore` writes a character's `WowLab.lua` and another file, gc keeps the
  first file's `after` and collects the second's.
- "A character's `WowLab.lua`" is the lab-addon's per-character file
  (`WowLabCharDB`, `SavedVariables/WowLab.lua` in a character folder), the
  file the loader check reads for any `--into` character. The check never
  reads the account-wide `WowLab.lua` (`WowLabDB`), so gc collects that
  file's `after`. It does the same for a client backup `WowLab.lua.bak`
  beside the character's file and for any other file in a character folder.
- The owner deletes a snapshot by removing its manifest file. No `snap`
  command deletes one, and `profile delete` only relabels.
- `--help` is graded on substance, not wording: it names `WowLab.lua` and a
  character, and still names snapshots and the one-hour grace period.

Conductor ruling on M11-31T, round 1 (2026-09-29, from the #141 reviews):
case-insensitive matching is intended. The kept set is every path the loader
check's `_character_sv` can return for a character's own lab file, a
per-character path of seven parts
(`WTF/Account/<account>/<realm or digits>/<character>/SavedVariables/WowLab.lua`)
with `WTF`, `Account`, `SavedVariables` and `WowLab.lua` compared case-folded.
The check falls back to a case-folded name when no `WowLab.lua` is spelled
exactly, and the journal records the spelling on disk, so a character's
`wowlab.lua` is kept like `WowLab.lua`. The rule is decided from the journal
record's path alone. Collection goes through the store's link-safe removal:
an object gc would now collect never goes through a linked shard.

Real fixtures (L8): the M11-03 capture (`fixtures/macos/forever`) copied into
`tmp_path` as `test_cli.py` builds it, including both characters'
`WowLab.lua`, the account-wide `WowLab.lua`, `DBM-Party-Vanilla.lua` and a
character's `macros-cache.txt`. The user data directory (store, journal,
locks) is redirected into `tmp_path` and the process table is a fake.
Constructed inputs, labelled in the test names: edited copies of those files
standing for a later session, a `WowLab.lua.bak` made from the real file, a
snapshot manifest removed by hand, and every store object's mtime set back
past the gc grace period. Round 1 adds the character's real file renamed to
`wowlab.lua`, and an object shard moved outside the store with a symbolic
link left in its place. Nothing reads or writes a real install.
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

import hashlib
import os
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from test_cli import (
    CHARACTER,
    _json,
    _plain,
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
    ACCT,
    CHAR_A,
    CHAR_B,
    DBM,
    DBM_NAME,
    DBM_VAR,
    FIXTURE_FLAVOR,
    LAB,
    LAB_A,
    LAB_ACCOUNT,
    LAB_B,
    REAL_A,
    REAL_B,
    REAL_DBM,
    _copy_within,
    _once,
    _out,
    _report,
    _snap,
)
from test_svmerge_loader import _merge_gear_into_wowlab_lua

from wowlab_core import cli, guard, snapshot
from wowlab_core.snapshot import GcReport, VerifyReport

REAL_ACCOUNT_LAB = (FIXTURE_FLAVOR / LAB_ACCOUNT).read_bytes()  # WowLabDB, account-wide
MACROS = f"{ACCT}/1/{CHARACTER}/macros-cache.txt"  # a character folder with no WowLab.lua
REAL_MACROS = (FIXTURE_FLAVOR / MACROS).read_bytes()
LAB_A_BAK = f"{LAB_A}.bak"  # constructed: the client's backup beside the character's file


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _loads(data: bytes, n: int, *, was: int) -> bytes:
    """Constructed: `data` as a session that left `probe.loads` at `n` writes it."""
    return _once(data, f'["loads"] = {was},'.encode(), f'["loads"] = {n},'.encode())


def _a(n: int) -> bytes:
    """The first character's real `WowLab.lua` (loads 4) with `loads` set to `n`."""
    return _loads(REAL_A, n, was=4)


def _b(n: int) -> bytes:
    """The second character's real `WowLab.lua` (loads 2) with `loads` set to `n`."""
    return _loads(REAL_B, n, was=2)


def _store() -> snapshot.SnapshotStore:
    """The store in the redirected user data directory."""
    return snapshot.SnapshotStore()


def _objects(store: snapshot.SnapshotStore) -> set[str]:
    """Every object in the store by name (`objects/ab/cdef…`, §6.9)."""
    return {p.parent.name + p.name for p in store.objects_dir.glob("*/*") if p.is_file()}


def _unreferenced(store: snapshot.SnapshotStore) -> set[str]:
    """Objects no snapshot manifest refers to: what only the journal can name."""
    listed = {e.sha256 for m in store.list() for e in m.entries if e.sha256 is not None}
    return _objects(store) - listed


def _age_objects(store: snapshot.SnapshotStore) -> None:
    """Constructed: set every object's mtime back past the gc grace period, so
    only the keep rule decides what a real run removes."""
    old = time.time() - 2 * cli.GC_GRACE_SECONDS
    for path in store.objects_dir.glob("*/*"):
        os.utime(path, (old, old))


def _delete_snapshot(snapshot_id: str) -> None:
    """Constructed: the owner removes a snapshot's manifest by hand."""
    store = _store()
    (store.manifests_dir / f"{snapshot_id}.json").unlink()
    assert snapshot_id not in {m.id for m in store.list()}


def _last_record() -> guard.HistoryRecord:
    record = guard.history()[-1]
    assert record.state == "committed", record
    return record


def _written(record: guard.HistoryRecord) -> dict[str, str | None]:
    return {change.path: change.after for change in record.paths}


def _restore_from_a_deleted_snapshot(flavor: Path, later: dict[str, bytes]) -> dict[str, str]:
    """Snapshot the files, let a later session change each to `later`, `snap
    restore` them all in one transaction, then delete the snapshot the restore
    wrote from. Guard's pre-write snapshot holds the later bytes, so what the
    restore wrote is named only by its journal record. Returns that record's
    `after` for each path."""
    snapshot_id = _snap("before a later session")
    for path, data in later.items():
        assert (flavor / path).read_bytes() != data
        (flavor / path).write_bytes(data)
    paths = [arg for path in later for arg in ("--paths", path)]
    ok("snap", "restore", snapshot_id, *paths, "--yes")
    written = _written(_last_record())
    assert set(written) == set(later)
    _delete_snapshot(snapshot_id)
    out: dict[str, str] = {}
    for path, after in written.items():
        assert after is not None
        out[path] = after
    return out


def _merge_into(char: str, dst: str) -> Any:
    """A merge that would succeed, checked against `char`'s `WowLab.lua`: a
    `--key` copy inside the account file, to a key no earlier run wrote."""
    key = f'{DBM_VAR}["Labchard Labrealmg"]={DBM_VAR}["{dst}"]'
    return run("sv", "merge", DBM_NAME, "--into", char, "--key", key, "--yes", "--json")


def _passes(result: Any) -> None:
    """The loader check passed and the merge wrote; no rule-3 refusal."""
    assert result.exit_code == 0, _out(result)
    assert _report(result)["written"] is True


# ─── collected: what the loader check never reads ───────────────────────────


RECLAIMED = [
    pytest.param(
        DBM,
        REAL_DBM,
        REAL_DBM.replace(b'["Enabled"] = true,', b'["Enabled"] = false,', 1),
        id="account-addon-file",
    ),
    pytest.param(
        LAB_ACCOUNT,
        REAL_ACCOUNT_LAB,
        _once(REAL_ACCOUNT_LAB, b'["schema"] = 1,', b'["schema"] = 2,'),
        id="account-wide-wowlab-lua",
    ),
    pytest.param(LAB_A_BAK, _a(3), _a(2), id="wowlab-lua-bak-beside-the-characters-file"),
    pytest.param(
        MACROS,
        REAL_MACROS,
        _once(REAL_MACROS, b"Arcane Intellect", b"Frost Armor"),
        id="another-file-in-a-character-folder",
    ),
]


@pytest.mark.xfail(strict=True, reason="M11-31 not implemented")
@pytest.mark.parametrize(("target", "original", "later"), RECLAIMED)
def test_gc_collects_what_a_restore_from_a_deleted_snapshot_wrote_and_keeps_the_characters_wowlab_lua_constructed(
    flavor: Path, target: str, original: bytes, later: bytes
) -> None:
    if target == LAB_A_BAK:
        (flavor / target).write_bytes(original)  # constructed: the client's backup
    assert (flavor / target).read_bytes() == original
    # One restore writes the first character's WowLab.lua (4, over a login's 5)
    # and the target file, from a snapshot the owner then deletes.
    written = _restore_from_a_deleted_snapshot(flavor, {LAB_A: _a(5), target: later})
    assert written == {LAB_A: _sha(REAL_A), target: _sha(original)}
    store = _store()
    assert _unreferenced(store) == {_sha(REAL_A), _sha(original)}, "only the journal names them"
    _age_objects(store)

    dry = _json(GcReport, "snap", "gc", "--dry-run")
    assert dry.unreferenced == (_sha(original),), "the dry run lists the target's after only"
    assert store.object_path(_sha(original)).is_file(), "a dry run removes nothing"
    done = _json(GcReport, "snap", "gc", "--yes")
    assert done.removed == (_sha(original),)
    assert not store.object_path(_sha(original)).exists()
    assert store.object_path(_sha(REAL_A)).is_file(), "the loader check compares from it"

    # A session since the restore raised loads to 5: rule 2 reads the kept
    # object (loads 4), so the next merge passes rather than refusing (rule 3).
    (flavor / LAB_A).write_bytes(_once(_a(5), *_FILTER))
    _passes(_copy_within("--json"))


@pytest.mark.xfail(strict=True, reason="M11-31 not implemented")
def test_gc_collects_what_a_profile_apply_wrote_from_a_deleted_profile_snapshot_constructed(
    flavor: Path,
) -> None:
    # §13.3: a profile never holds WowLab.lua, so nothing a profile apply
    # writes is anything the loader check reads.
    ok("profile", "save", "addons", "--preset", "addons")
    (saved,) = [m for m in _store().list() if m.purpose == "profile"]
    later = REAL_DBM.replace(b'["Enabled"] = true,', b'["Enabled"] = false,', 1)
    (flavor / DBM).write_bytes(later)
    ok("profile", "apply", "addons", "--yes")
    assert (flavor / DBM).read_bytes() == REAL_DBM, "the apply wrote the saved bytes"
    assert _written(_last_record()) == {DBM: _sha(REAL_DBM)}
    _delete_snapshot(saved.id)
    store = _store()
    assert _unreferenced(store) == {_sha(REAL_DBM)}, "only the journal names it"
    _age_objects(store)

    dry = _json(GcReport, "snap", "gc", "--dry-run")
    assert dry.unreferenced == (_sha(REAL_DBM),)
    done = _json(GcReport, "snap", "gc", "--yes")
    assert done.removed == (_sha(REAL_DBM),)
    assert not store.object_path(_sha(REAL_DBM)).exists()


# ─── kept: what the loader check compares from (controls, green today) ──────


def test_gc_keeps_what_a_restore_from_a_deleted_snapshot_wrote_into_each_characters_wowlab_lua_constructed(
    flavor: Path,
) -> None:
    written = _restore_from_a_deleted_snapshot(flavor, {LAB_A: _a(5), LAB_B: _b(3)})
    assert written == {LAB_A: _sha(REAL_A), LAB_B: _sha(REAL_B)}
    store = _store()
    assert _unreferenced(store) == set(written.values()), "only the journal names them"
    _age_objects(store)

    assert _json(GcReport, "snap", "gc", "--dry-run").unreferenced == ()
    assert _json(GcReport, "snap", "gc", "--yes").removed == ()
    for digest in written.values():
        assert store.object_path(digest).is_file(), "the loader check compares from it"

    # A session since the restore on each character raised loads (4 -> 5,
    # 2 -> 4): rule 2 reads each character's kept object.
    (flavor / LAB_A).write_bytes(_once(_a(5), *_FILTER))
    (flavor / LAB_B).write_bytes(_b(4))
    _passes(_merge_into(CHAR_A, "Labcharb Labrealmf"))
    _passes(_merge_into(CHAR_B, "Labcharb Labrealmd"))


def test_gc_keeps_every_sv_merge_of_a_characters_wowlab_lua_not_only_the_latest_constructed(
    flavor: Path,
) -> None:
    # W1: a merge of the file (loads 4); a session since raises it to 5; W2:
    # another merge of the file. Guard's snapshot before W2 holds the session,
    # so W1's bytes are named only by its journal record. The pair of newest
    # snapshots (before W1, before W2) spans W1, so the next check compares
    # from W1's result: an older write, not the latest.
    _snap("loads 4")
    _merge_gear_into_wowlab_lua()
    w1 = _written(_last_record())[LAB_A]
    assert w1 is not None
    (flavor / LAB_A).write_bytes(_loads((flavor / LAB_A).read_bytes(), 5, was=4))
    ok("sv", "merge", LAB, "--from", CHAR_B, "--into", CHAR_A, "--take", "theirs", "--yes")
    w2 = _written(_last_record())[LAB_A]
    assert w2 is not None and w2 != w1
    store = _store()
    assert _unreferenced(store) == {w1, w2}, "only the journal names them"
    _age_objects(store)

    assert _json(GcReport, "snap", "gc", "--yes").removed == ()
    assert store.object_path(w1).is_file(), "an older Lab write is still compared from"
    assert store.object_path(w2).is_file()
    _passes(_copy_within("--json"))


# ─── --help says what is kept ───────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="M11-31 not implemented")
def test_snap_gc_help_says_what_is_kept() -> None:
    text = " ".join(_plain(ok("snap", "gc", "--help").stdout).split())
    assert "WowLab.lua" in text, text
    assert "character" in text, text
    assert "snapshot" in text, text  # an object a snapshot refers to
    assert "hour" in text, text  # the grace period


# ─── round 1 (#141 reviews): a case variant and a linked shard ──────────────


LAB_A_FOLDED = f"{LAB_A.removesuffix(LAB)}wowlab.lua"  # constructed: the file renamed

posix_symlinks = pytest.mark.skipif(
    sys.platform == "win32" or not hasattr(os, "symlink"),
    reason="POSIX symbolic links (creating them needs privileges on Windows)",
)


def test_gc_keeps_what_a_restore_wrote_into_a_characters_lab_file_spelled_wowlab_lua_constructed(
    flavor: Path,
) -> None:
    """Exists to fail an exact-case keep rule (conductor ruling, M11-31T
    round 1). When no `WowLab.lua` is spelled exactly, the loader check finds a
    character's `wowlab.lua` by its case-folded name. The journal records the
    spelling on disk, so gc must keep that write's `after` too. Green on main,
    which keeps every `after`."""
    lab_dir = (flavor / LAB_A).parent
    (flavor / LAB_A).rename(flavor / LAB_A_FOLDED)
    assert sorted(p.name for p in lab_dir.iterdir()) == ["wowlab.lua"]
    written = _restore_from_a_deleted_snapshot(flavor, {LAB_A_FOLDED: _a(5)})
    assert written == {LAB_A_FOLDED: _sha(REAL_A)}, "the journal records the spelling on disk"
    store = _store()
    assert _unreferenced(store) == {_sha(REAL_A)}, "only the journal names it"
    _age_objects(store)

    assert _json(GcReport, "snap", "gc", "--dry-run").unreferenced == ()
    assert _json(GcReport, "snap", "gc", "--yes").removed == ()
    assert store.object_path(_sha(REAL_A)).is_file(), "the loader check compares from it"

    # A session since the restore raised loads to 5 and changed a value. Guard's
    # snapshot holds 5 too, so only rule 2, from the kept object (4), passes it;
    # without the object the merge is refused (rule 3).
    (flavor / LAB_A_FOLDED).write_bytes(_once(_a(5), *_FILTER))
    assert sorted(p.name for p in lab_dir.iterdir()) == ["wowlab.lua"]
    _passes(_copy_within("--json"))


LAB_A_IN_FOLDED_DIR = LAB_A.replace("/SavedVariables/", "/savedvariables/")  # constructed


def test_gc_keeps_what_a_restore_wrote_into_a_characters_lab_file_under_savedvariables_in_lower_case_constructed(
    flavor: Path,
) -> None:
    """Exists to fail a rule that folds only the file name (conductor ruling,
    M11-31T round 1; review of #141, round 2). The ruling folds `WTF`,
    `Account` and `SavedVariables` too. Layout and the loader check still find
    the character's `WowLab.lua` in a folder spelled `savedvariables`, and the
    journal records that spelling, so gc must keep that write's `after`.
    Green on main, which keeps every `after`."""
    sv_dir = (flavor / LAB_A).parent
    sv_dir.rename(sv_dir.with_name("savedvariables"))
    assert "savedvariables" in {p.name for p in sv_dir.parent.iterdir()}
    assert "SavedVariables" not in {p.name for p in sv_dir.parent.iterdir()}
    written = _restore_from_a_deleted_snapshot(flavor, {LAB_A_IN_FOLDED_DIR: _a(5)})
    assert written == {LAB_A_IN_FOLDED_DIR: _sha(REAL_A)}, "the journal records the spelling"
    store = _store()
    assert _unreferenced(store) == {_sha(REAL_A)}, "only the journal names it"
    _age_objects(store)

    assert _json(GcReport, "snap", "gc", "--dry-run").unreferenced == ()
    assert _json(GcReport, "snap", "gc", "--yes").removed == ()
    assert store.object_path(_sha(REAL_A)).is_file(), "the loader check compares from it"

    # As above: a session since the restore; only rule 2, from the kept
    # object, passes the merge.
    (flavor / LAB_A_IN_FOLDED_DIR).write_bytes(_once(_a(5), *_FILTER))
    _passes(_copy_within("--json"))


@posix_symlinks
@pytest.mark.xfail(strict=True, reason="M11-31 not implemented")
def test_gc_never_removes_an_object_the_journal_alone_names_through_a_symlinked_shard_constructed(
    flavor: Path, tmp_path: Path
) -> None:
    """Collection goes through the store's link-safe removal (§6.9, M11-15),
    never a delete of its own. One restore, from a snapshot then deleted,
    writes the account-wide `WowLab.lua` and the account-wide DBM file: two
    objects gc now collects, in different shards. The first one's shard is
    moved outside the store with a symbolic link left in its place. The DBM
    object's shard stays real, so the dry run finds it and the real run
    happens while the link is there. That run removes the DBM object only,
    names the link in `skipped`, leaves every file behind the link as it was
    and leaves the link itself alone. With the shard put back, the store
    verifies and gc collects the first object. Main keeps every `after`, so
    it removes nothing, and that is where it fails (review of #141, round 2)."""
    later = {
        LAB_ACCOUNT: _once(REAL_ACCOUNT_LAB, b'["schema"] = 1,', b'["schema"] = 2,'),
        DBM: REAL_DBM.replace(b'["Enabled"] = true,', b'["Enabled"] = false,', 1),
    }
    written = _restore_from_a_deleted_snapshot(flavor, later)
    digest, other = written[LAB_ACCOUNT], written[DBM]
    assert (digest, other) == (_sha(REAL_ACCOUNT_LAB), _sha(REAL_DBM))
    assert other[:2] != digest[:2], "the DBM object sits in a shard that stays real"
    store = _store()
    assert _unreferenced(store) == {digest, other}, "only the journal names them"
    _age_objects(store)
    shard = store.objects_dir / digest[:2]
    outside = tmp_path / "outside" / digest[:2]
    outside.parent.mkdir()
    shard.rename(outside)
    shard.symlink_to(outside, target_is_directory=True)
    behind = {p.name: p.read_bytes() for p in outside.iterdir()}
    assert digest[2:] in behind, "the object sits behind the link"

    report = _json(GcReport, "snap", "gc", "--yes")
    assert f"objects/{digest[:2]}" in report.skipped, report
    assert report.removed == (other,), "the real run happened, and removed only the DBM object"
    assert {p.name: p.read_bytes() for p in outside.iterdir()} == behind, "nothing behind it"
    assert shard.is_symlink() and shard.readlink() == outside, "the link is left as it is"

    shard.unlink()
    outside.rename(shard)
    assert _json(VerifyReport, "snap", "verify").ok
    done = _json(GcReport, "snap", "gc", "--yes")
    assert done.removed == (digest,)
    assert not store.object_path(digest).exists()
