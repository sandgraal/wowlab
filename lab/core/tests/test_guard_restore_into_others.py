"""Graders for folders under `WTF/Account/` that are no account (M16-03, round 1).

Written by the test-writer, not the implementer (ADR-0013: `guard.py` is
load-bearing), from docs/LAB_PLAN.md §6.10 and its 2026-10-22 amendment
"Restore into another target" with the conductor's rulings R1, R2 and R3,
and from the M16-03T graders in `test_guard_restore_into_target.py`, whose
fixtures and helpers are loaded below, not copied.

The rule these graders hold `guard` to: a restore into a target writes into
the target's account folders only as the account mapping says, and never
merges two source folders into one target folder. A snapshot can hold a
folder under `WTF/Account/` that does not count as an account (no
`<digits>#<n>` name and no character folder below it, such as a leftover
`bindings-cache.wtf`). Such a folder is restored under its own name, which
is right only while that name is not one of the target's account folders
and not where the mapping sends an account. When it is either, in any
letter case (`_fold`), restoring it would write the snapshot's stray files
into a real account the mapping never named, or merge two source folders
into one target folder. That is refused, with the folder named and nothing
written or planned, in a dry run as in a real one.

Everything here is constructed: installs are trees under `tmp_path`, the
store is a directory under `tmp_path`, the user data directory is
redirected and the process table is injected. No test needs or touches a
real install. Flavor folder names are test data (L6).

The xfail markers name round 1 of M16-03; the implementer deletes them and
nothing else. The positive controls and the case-folded refusals carry no
marker: they pass today and must keep passing.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from wowlab_core import layout, profiles
from wowlab_core.snapshot import SnapshotStore


def _load_m16_03t_graders() -> types.ModuleType:
    """`test_guard_restore_into_target.py` as a module of its own, for its
    synthetic installs, fixtures and helpers."""
    path = Path(__file__).resolve().parent / "test_guard_restore_into_target.py"
    spec = importlib.util.spec_from_file_location("_m16_03_others_base_graders", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


T = _load_m16_03t_graders()
G = T.G

# The M10-11T fixtures, registered here under their own names: the user data
# directory and the process table are redirected for every test in this file.
_user_data_redirected = G._user_data_redirected
_real_process_table_is_never_listed = G._real_process_table_is_never_listed
guard = G.guard
idle = G.idle
store = T.store

Flavor = T.Flavor
strict_state = T.strict_state
changes = T.changes
record_for = T.record_for
make_install = T.make_install
snapshot_of = T.snapshot_of
source_files = T.source_files
target_files = T.target_files
into = T.into
assert_restored = T.assert_restored
assert_not_a_client_refusal = T.assert_not_a_client_refusal

SOURCE_FOLDER = T.SOURCE_FOLDER
TARGET_FOLDER = T.TARGET_FOLDER
SRC_ACCOUNT = T.SRC_ACCOUNT
DST_ACCOUNT = T.DST_ACCOUNT

# ─── test data (constructed) ─────────────────────────────────────────────────

LEGACY = "LEGACYACCT"  # an account folder name without the `<digits>#<n>` shape

# The stray folder's spelling in the snapshot when it differs from `LEGACY`
# on the target only by letter case.
LEGACY_FOLDED = LEGACY.lower()

DRY_RUN = pytest.mark.parametrize("dry_run", [True, False], ids=["dry-run", "real-run"])

STRAY_BYTES = b"bind Q constructed-stray-legacy-bindings\n"
SRC_BINDINGS_BYTES = source_files(SRC_ACCOUNT)[f"WTF/Account/{SRC_ACCOUNT}/bindings-cache.wtf"]


def stray(folder: str) -> str:
    """The one file the snapshot holds under a folder that is no account:
    no `<digits>#<n>` name and no character folder below it."""
    return f"WTF/Account/{folder}/bindings-cache.wtf"


def snapshot_files(folder: str) -> dict[str, bytes]:
    """The source: one real account plus a stray folder that is no account."""
    files = source_files(SRC_ACCOUNT)
    files[stray(folder)] = STRAY_BYTES
    return files


def legacy_account(folder: str) -> dict[str, bytes]:
    """A real account folder on the target without the `<digits>#<n>`
    shape: it counts as an account because it holds a character folder."""
    base = f"WTF/Account/{folder}"
    return {
        f"{base}/bindings-cache.wtf": b"bind Q target-legacy-account\n",
        f"{base}/macros-cache.txt": b"constructed: the legacy account's macros\n",
        f"{base}/Labrealm/Labchar/SavedVariables/Addon.lua": b'CharDB = {\n\t["c"] = "legacy",\n}\n',
    }


def files_under(root: Path, rel: str) -> dict[str, bytes]:
    base = root / rel
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(base.rglob("*"))
        if p.is_file()
    }


def assert_refused_naming(guard: Any, exc: BaseException, folder: str) -> None:
    assert_not_a_client_refusal(guard, exc)
    message = str(exc).casefold()
    assert folder.casefold() in message, f"the refusal does not name {folder!r}: {exc}"


# ─── the stray folder matches a target account the mapping does not name ────


def refused_unmapped(
    guard: Any, tmp_path: Path, store: SnapshotStore, spelling: str, dry_run: bool
) -> None:
    """The mapping names `SRC_ACCOUNT -> DST_ACCOUNT` only. Restoring the
    stray folder under its own name would write into the target's real
    `LEGACYACCT` account, which the mapping never named."""
    world = tmp_path / "world"
    source = make_install(world / "Source WoW", SOURCE_FOLDER, snapshot_files(spelling))
    snapshot_id = snapshot_of(store, source)
    target = make_install(
        world / "Target WoW",
        TARGET_FOLDER,
        {**target_files(DST_ACCOUNT), **legacy_account(LEGACY)},
    )
    legacy_before = files_under(target.path, f"WTF/Account/{LEGACY}")
    before = strict_state(world)

    with guard.transaction(
        target, label=f"stray vs unmapped {dry_run}", store=store.path, dry_run=dry_run
    ) as tx:
        with pytest.raises(guard.GuardError) as refused:
            tx.restore(snapshot_id, into=into(guard, target, {SRC_ACCOUNT: DST_ACCOUNT}))
        assert tx.plan == (), "nothing is planned for a refused restore"
    assert_refused_naming(guard, refused.value, LEGACY)
    assert files_under(target.path, f"WTF/Account/{LEGACY}") == legacy_before
    assert strict_state(world) == before, "a refused restore writes nothing"


# ─── the stray folder is where the automatic mapping sends the account ──────


def refused_automatic(
    guard: Any, tmp_path: Path, store: SnapshotStore, spelling: str, dry_run: bool
) -> None:
    """The target's only account is `LEGACYACCT`, so the automatic mapping
    sends `SRC_ACCOUNT` there; the stray folder restores to the same name.
    Two source folders would merge into one target folder (R2)."""
    world = tmp_path / "world"
    source = make_install(world / "Source WoW", SOURCE_FOLDER, snapshot_files(spelling))
    snapshot_id = snapshot_of(store, source)
    target = make_install(world / "Target WoW", TARGET_FOLDER, legacy_account(LEGACY))
    legacy_before = files_under(target.path, f"WTF/Account/{LEGACY}")
    before = strict_state(world)

    with guard.transaction(
        target, label=f"stray vs automatic {dry_run}", store=store.path, dry_run=dry_run
    ) as tx:
        with pytest.raises(guard.GuardError) as refused:
            tx.restore(snapshot_id, into=into(guard, target))
        assert tx.plan == (), "nothing is planned for a refused restore"
    assert_refused_naming(guard, refused.value, LEGACY)
    assert files_under(target.path, f"WTF/Account/{LEGACY}") == legacy_before
    assert strict_state(world) == before, "a refused restore writes nothing"

    # Nothing was merged: the snapshot still holds both files, apart.
    held = {e.path: e.sha256 for e in store.show(snapshot_id).entries}
    src_path = f"{SOURCE_FOLDER}/WTF/Account/{SRC_ACCOUNT}/bindings-cache.wtf"
    stray_path = f"{SOURCE_FOLDER}/{stray(spelling)}"
    assert src_path in held and stray_path in held
    assert held[src_path] != held[stray_path]
    assert store.read_file(snapshot_id, src_path) == SRC_BINDINGS_BYTES
    assert store.read_file(snapshot_id, stray_path) == STRAY_BYTES


@DRY_RUN
def test_constructed_stray_folder_matching_an_unmapped_target_account_is_refused(
    guard: Any, tmp_path: Path, store: SnapshotStore, idle: None, dry_run: bool
) -> None:
    refused_unmapped(guard, tmp_path, store, LEGACY, dry_run)


@DRY_RUN
def test_constructed_automatic_mapping_onto_the_stray_folders_name_is_refused(
    guard: Any, tmp_path: Path, store: SnapshotStore, idle: None, dry_run: bool
) -> None:
    refused_automatic(guard, tmp_path, store, LEGACY, dry_run)


# ─── the case-folded variant: refused today, kept refused ────────────────────
# The stray folder `legacyacct` against the target's `LEGACYACCT`. On
# `d0313e6` the gate's existing case-collision check already refuses these
# (it names both spellings), so they carry no marker: a strict xfail would
# XPASS. They hold the round-1 fix to keep refusing the folded spelling.


@DRY_RUN
def test_constructed_case_folded_stray_folder_matching_an_unmapped_target_account_is_refused(
    guard: Any, tmp_path: Path, store: SnapshotStore, idle: None, dry_run: bool
) -> None:
    refused_unmapped(guard, tmp_path, store, LEGACY_FOLDED, dry_run)


@DRY_RUN
def test_constructed_case_folded_automatic_mapping_onto_the_stray_folders_name_is_refused(
    guard: Any, tmp_path: Path, store: SnapshotStore, idle: None, dry_run: bool
) -> None:
    refused_automatic(guard, tmp_path, store, LEGACY_FOLDED, dry_run)


# ─── positive controls: these pass today and carry no marker ─────────────────

CLIENT_SV = "WTF/Account/SavedVariables/Blizzard_Constructed.lua"


@pytest.mark.parametrize("given", ["automatic", "given"], ids=lambda g: f"constructed-{g}-mapping")
def test_constructed_account_level_saved_variables_still_restore_under_their_own_name(
    guard: Any, tmp_path: Path, store: SnapshotStore, idle: None, given: str
) -> None:
    """`WTF/Account/SavedVariables/` is the client's own folder and no
    account; it goes back under its own name next to the mapped account."""
    world = tmp_path / "world"
    files = source_files(SRC_ACCOUNT)
    files[CLIENT_SV] = b'BlizzDB = {\n\t["from"] = "source",\n}\n'
    source = make_install(world / "Source WoW", SOURCE_FOLDER, files)
    snapshot_id = snapshot_of(store, source)
    target = make_install(
        world / "Target WoW",
        TARGET_FOLDER,
        {**target_files(DST_ACCOUNT), CLIENT_SV: b'BlizzDB = {\n\t["from"] = "target",\n}\n'},
    )
    accounts = {SRC_ACCOUNT: DST_ACCOUNT}

    with guard.transaction(target, label="client sv", store=store.path) as tx:
        tx.restore(
            snapshot_id, into=into(guard, target, None if given == "automatic" else accounts)
        )

    assert_restored(target, files, accounts)
    assert (target.path / CLIENT_SV).read_bytes() == files[CLIENT_SV]
    assert not (target.path / "WTF" / "Account" / SRC_ACCOUNT).exists()
    assert CLIENT_SV in set(changes(record_for(guard, store, "client sv").paths))


@pytest.mark.parametrize("given", ["automatic", "given"], ids=lambda g: f"constructed-{g}-mapping")
def test_constructed_stray_folder_matching_no_target_account_still_restores(
    guard: Any, tmp_path: Path, store: SnapshotStore, idle: None, given: str
) -> None:
    """A stray folder whose name is no target account and not where the
    mapping sends one is restored under its own name, as before."""
    world = tmp_path / "world"
    files = snapshot_files(LEGACY)
    source = make_install(world / "Source WoW", SOURCE_FOLDER, files)
    snapshot_id = snapshot_of(store, source)
    target = make_install(world / "Target WoW", TARGET_FOLDER, target_files(DST_ACCOUNT))
    accounts = {SRC_ACCOUNT: DST_ACCOUNT}

    with guard.transaction(target, label="stray elsewhere", store=store.path) as tx:
        tx.restore(
            snapshot_id, into=into(guard, target, None if given == "automatic" else accounts)
        )

    assert_restored(target, files, accounts)
    assert (target.path / stray(LEGACY)).read_bytes() == STRAY_BYTES
    assert (target.path / f"WTF/Account/{DST_ACCOUNT}/bindings-cache.wtf").read_bytes() == (
        SRC_BINDINGS_BYTES
    )
    assert not (target.path / "WTF" / "Account" / SRC_ACCOUNT).exists()
    assert stray(LEGACY) in set(changes(record_for(guard, store, "stray elsewhere").paths))


# ─── round 2: a snapshot that holds no account folder at all ─────────────────
# §6.10 as amended 2026-10-22: a snapshot with no account folder "restores
# without a mapping and creates no account folder". Its only folder under
# `WTF/Account/` may still be a stray one; the round-1 rule applies to it
# all the same, and the early return for an account-less snapshot must not
# skip it. `profile apply --into-flavor` reaches the same path through
# `profiles.plan_apply` and `profiles.apply`.

ACCOUNTLESS = {T.CONFIG: b'SET portal "US"\nSET constructed "source"\n'}


def accountless_files(folder: str) -> dict[str, bytes]:
    """The source: no account folder, one stray folder of account-level files."""
    return {**ACCOUNTLESS, stray(folder): STRAY_BYTES}


def accountless_world(
    tmp_path: Path, store: SnapshotStore, spelling: str, target_files_: dict[str, bytes]
) -> tuple[Path, Flavor, Flavor, str]:
    world = tmp_path / "world"
    source = make_install(world / "Source WoW", SOURCE_FOLDER, accountless_files(spelling))
    snapshot_id = snapshot_of(store, source)
    target = make_install(world / "Target WoW", TARGET_FOLDER, target_files_)
    return world, source, target, snapshot_id


def refused_accountless(
    guard: Any, tmp_path: Path, store: SnapshotStore, spelling: str, dry_run: bool
) -> None:
    """The target's `LEGACYACCT` is a real account. With no account folder in
    the snapshot there is nothing to map, so the stray folder would be
    written straight into it."""
    world, _source, target, snapshot_id = accountless_world(
        tmp_path, store, spelling, {**target_files(DST_ACCOUNT), **legacy_account(LEGACY)}
    )
    legacy_before = files_under(target.path, f"WTF/Account/{LEGACY}")
    before = strict_state(world)

    with guard.transaction(
        target, label=f"accountless {dry_run}", store=store.path, dry_run=dry_run
    ) as tx:
        with pytest.raises(guard.GuardError) as refused:
            tx.restore(snapshot_id, into=into(guard, target))
        assert tx.plan == (), "nothing is planned for a refused restore"
    assert_refused_naming(guard, refused.value, LEGACY)
    assert files_under(target.path, f"WTF/Account/{LEGACY}") == legacy_before
    assert strict_state(world) == before, "a refused restore writes nothing"


@DRY_RUN
@pytest.mark.xfail(strict=True, reason="M16-03 round 2: snapshot with no account folder")
def test_constructed_accountless_snapshot_stray_folder_matching_a_target_account_is_refused(
    guard: Any, tmp_path: Path, store: SnapshotStore, idle: None, dry_run: bool
) -> None:
    refused_accountless(guard, tmp_path, store, LEGACY, dry_run)


@DRY_RUN
def test_constructed_case_folded_accountless_snapshot_stray_folder_is_refused(
    guard: Any, tmp_path: Path, store: SnapshotStore, idle: None, dry_run: bool
) -> None:
    """`legacyacct` against the target's `LEGACYACCT`. On dbeeae8 the gate's
    case-collision check already refuses this, so it carries no marker (a
    strict xfail would XPASS); the round-2 fix must keep refusing it."""
    refused_accountless(guard, tmp_path, store, LEGACY_FOLDED, dry_run)


def test_constructed_accountless_snapshot_stray_folder_of_another_name_still_restores(
    guard: Any, tmp_path: Path, store: SnapshotStore, idle: None
) -> None:
    """Positive control: no target account has the stray folder's name, so
    it restores under its own name, with no mapping, and no other account
    folder is created or touched."""
    _world, _source, target, snapshot_id = accountless_world(
        tmp_path, store, LEGACY, target_files(DST_ACCOUNT)
    )
    dst_before = files_under(target.path, f"WTF/Account/{DST_ACCOUNT}")

    with guard.transaction(target, label="accountless elsewhere", store=store.path) as tx:
        tx.restore(snapshot_id, into=into(guard, target))

    assert (target.path / stray(LEGACY)).read_bytes() == STRAY_BYTES
    assert (target.path / T.CONFIG).read_bytes() == ACCOUNTLESS[T.CONFIG]
    assert files_under(target.path, f"WTF/Account/{DST_ACCOUNT}") == dst_before
    paths = set(changes(record_for(guard, store, "accountless elsewhere").paths))
    assert stray(LEGACY) in paths
    assert not any(DST_ACCOUNT in p for p in paths)


# ─── round 2 through `profile apply --into-flavor` ───────────────────────────
# A profile of account-level files: the `bindings` preset finds no account in
# a source whose only folder is a stray one (`layout` does not count it), so
# the profile names that folder as an explicit subtree, which `select`
# accepts (narrower than `WTF/Account/`).


def accountless_profile(store: SnapshotStore, source: Flavor) -> Any:
    lay = layout.Layout(source.path, install_root=source.path.parent)
    selection = profiles.select(
        lay, subtrees=["WTF/Config.wtf", f"WTF/Account/{stray_folder(source)}"]
    )
    return profiles.save(store, source.path.parent, source, "constructed-stray", selection)


def stray_folder(source: Flavor) -> str:
    (name,) = [p.name for p in (source.path / "WTF" / "Account").iterdir() if p.is_dir()]
    return name


@pytest.mark.xfail(strict=True, reason="M16-03 round 2: snapshot with no account folder")
def test_constructed_accountless_profile_plan_into_a_target_account_is_refused(
    guard: Any, tmp_path: Path, store: SnapshotStore, idle: None
) -> None:
    world = tmp_path / "world"
    source = make_install(world / "Source WoW", SOURCE_FOLDER, accountless_files(LEGACY))
    profile = accountless_profile(store, source)
    target = make_install(
        world / "Target WoW",
        TARGET_FOLDER,
        {**target_files(DST_ACCOUNT), **legacy_account(LEGACY)},
    )
    legacy_before = files_under(target.path, f"WTF/Account/{LEGACY}")
    before = strict_state(world)

    with pytest.raises(guard.GuardError) as refused:
        profiles.plan_apply(
            profile, target, target.path.parent, store=store, into=into(guard, target)
        )
    assert_refused_naming(guard, refused.value, LEGACY)
    assert files_under(target.path, f"WTF/Account/{LEGACY}") == legacy_before
    assert strict_state(world) == before, "a refused plan writes nothing"


def test_constructed_accountless_profile_apply_rechecks_at_the_gate(
    guard: Any, tmp_path: Path, store: SnapshotStore, idle: None
) -> None:
    """The plan is made while the target's `LEGACYACCT` is no account (no
    such folder yet); by the apply it is a real account (a character was
    created). The plan's own paths are unchanged, so only the gate's check
    at the apply stands between the stray file and that account.

    No marker: on dbeeae8 `apply` is already refused here, by the `paths=`
    spelling check (a named path under what is now a target account folder),
    so a strict xfail would XPASS. It holds the round-2 fix to keep the
    apply refused, whichever check refuses it."""
    world = tmp_path / "world"
    source = make_install(world / "Source WoW", SOURCE_FOLDER, accountless_files(LEGACY))
    profile = accountless_profile(store, source)
    target = make_install(world / "Target WoW", TARGET_FOLDER, target_files(DST_ACCOUNT))
    plan = profiles.plan_apply(
        profile, target, target.path.parent, store=store, into=into(guard, target)
    )
    assert stray(LEGACY) in {i.path for i in plan.plan}

    character = {
        k: v for k, v in legacy_account(LEGACY).items() if "/Labrealm/" in k
    }  # a character folder only: the plan's paths stay as they were
    G._put(target.path, character)
    legacy_before = files_under(target.path, f"WTF/Account/{LEGACY}")
    before = strict_state(world)

    with pytest.raises(guard.GuardError) as refused:
        profiles.apply(plan, target, store=store)
    assert_refused_naming(guard, refused.value, LEGACY)
    assert not (target.path / stray(LEGACY)).exists()
    assert files_under(target.path, f"WTF/Account/{LEGACY}") == legacy_before
    assert strict_state(world) == before, "a refused apply writes nothing"


def test_constructed_accountless_profile_of_another_name_still_applies_into(
    guard: Any, tmp_path: Path, store: SnapshotStore, idle: None
) -> None:
    """Positive control for both profile tests: no target account has the
    stray folder's name, so plan and apply write it under its own name."""
    world = tmp_path / "world"
    source = make_install(world / "Source WoW", SOURCE_FOLDER, accountless_files(LEGACY))
    profile = accountless_profile(store, source)
    target = make_install(world / "Target WoW", TARGET_FOLDER, target_files(DST_ACCOUNT))
    dst_before = files_under(target.path, f"WTF/Account/{DST_ACCOUNT}")

    plan = profiles.plan_apply(
        profile, target, target.path.parent, store=store, into=into(guard, target)
    )
    assert plan.accounts == ()
    profiles.apply(plan, target, store=store)

    assert (target.path / stray(LEGACY)).read_bytes() == STRAY_BYTES
    assert files_under(target.path, f"WTF/Account/{DST_ACCOUNT}") == dst_before
