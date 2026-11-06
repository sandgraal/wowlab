"""Account folders in a restore into another target: what is no account (M16-03).

The client keeps its own `WTF/Account/SavedVariables/` beside the account
folders (`docs/LAB_FILE_MAP.md`, M13-50). It is no account, so it is not
counted for the automatic mapping and is restored under its own name; no
account folder may be mapped onto it. `tx.restore(..., into=...)` returns
the mapping it used.

Constructed inputs on the synthetic installs of the M16-03T graders, loaded
by name, labelled in the test names (L8). Flavor folder names are test data.
"""

# ruff: noqa: F811  (fixtures imported from the graders are requested by name)
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from test_guard_restore_into_others import LEGACY, STRAY_BYTES, legacy_account, stray
from test_guard_restore_into_target import (
    DST_ACCOUNT,
    SOURCE_FOLDER,
    SRC_ACCOUNT,
    TARGET_FOLDER,
    _real_process_table_is_never_listed,  # noqa: F401
    _user_data_redirected,  # noqa: F401
    guard,  # noqa: F401
    idle,  # noqa: F401
    into,
    make_install,
    snapshot_of,
    source_files,
    store,  # noqa: F401
    strict_state,
    target_files,
)

from wowlab_core.snapshot import SnapshotStore

CLIENT_SV = "WTF/Account/SavedVariables/Blizzard_Constructed.lua"


def _installs(tmp_path: Path, store: SnapshotStore) -> tuple[Any, str, Path]:
    world = tmp_path / "world"
    files = {**source_files(SRC_ACCOUNT), CLIENT_SV: b"constructed = 'source'\n"}
    source = make_install(world / "Source WoW", SOURCE_FOLDER, files)
    snapshot_id = snapshot_of(store, source)
    target_tree = {**target_files(DST_ACCOUNT), CLIENT_SV: b"constructed = 'target'\n"}
    target = make_install(world / "Target WoW", TARGET_FOLDER, target_tree)
    return target, snapshot_id, world


def test_constructed_client_saved_variables_folder_is_no_account(
    guard: Any, tmp_path: Path, store: SnapshotStore, idle: None
) -> None:
    target, snapshot_id, _world = _installs(tmp_path, store)
    with guard.transaction(target, label="with the client's folder", store=store.path) as tx:
        mapped = tx.restore(snapshot_id, into=into(guard, target))
    assert mapped == ((SRC_ACCOUNT, DST_ACCOUNT),)
    assert (target.path / CLIENT_SV).read_bytes() == b"constructed = 'source'\n"
    bindings = f"WTF/Account/{DST_ACCOUNT}/bindings-cache.wtf"
    assert (target.path / bindings).read_bytes() == source_files(SRC_ACCOUNT)[
        f"WTF/Account/{SRC_ACCOUNT}/bindings-cache.wtf"
    ]


@pytest.mark.parametrize(
    "name", ["SavedVariables", "savedvariables"], ids=lambda n: f"constructed-{n}"
)
def test_constructed_account_mapped_onto_the_client_folder_is_refused(
    guard: Any, tmp_path: Path, store: SnapshotStore, idle: None, name: str
) -> None:
    target, snapshot_id, world = _installs(tmp_path, store)
    before = strict_state(world)
    with pytest.raises((guard.GuardError, ValueError)) as refused:
        named = into(guard, target, {SRC_ACCOUNT: name})
        with guard.transaction(target, label="onto the client folder", store=store.path) as tx:
            tx.restore(snapshot_id, into=named)
    assert "account" in str(refused.value).casefold()
    assert strict_state(world) == before


def test_constructed_restore_without_into_returns_no_mapping(
    guard: Any, tmp_path: Path, store: SnapshotStore, idle: None
) -> None:
    world = tmp_path / "world"
    source = make_install(world / "WoW", SOURCE_FOLDER, source_files(SRC_ACCOUNT))
    snapshot_id = snapshot_of(store, source)
    with guard.transaction(source, label="own flavor", store=store.path, dry_run=True) as tx:
        assert tx.restore(snapshot_id) == ()


def test_constructed_mapping_changed_after_validation_is_checked_again(
    guard: Any, tmp_path: Path, store: SnapshotStore, idle: None
) -> None:
    """`Target` is frozen, but its dict is the caller's: the restore checks the
    mapping again rather than trusting the check made when it was built."""
    target, snapshot_id, world = _installs(tmp_path, store)
    named = into(guard, target, {SRC_ACCOUNT: DST_ACCOUNT})
    named.accounts[SRC_ACCOUNT] = "../.."
    before = strict_state(world)
    with (
        guard.transaction(target, label="changed mapping", store=store.path) as tx,
        pytest.raises(guard.GuardError),
    ):
        tx.restore(snapshot_id, into=named)
    assert strict_state(world) == before


# ─── a non-account folder that clashes with a target account: the way forward ─


def _clash(tmp_path: Path, store: SnapshotStore, *, with_account: bool) -> tuple[Any, str, Path]:
    """A snapshot holding a folder that is no account (`LEGACYACCT`, files
    only), and a target where that name is a real account."""
    world = tmp_path / "world"
    files = source_files(SRC_ACCOUNT) if with_account else source_files()
    files[stray(LEGACY)] = STRAY_BYTES
    source = make_install(world / "Source WoW", SOURCE_FOLDER, files)
    snapshot_id = snapshot_of(store, source)
    target = make_install(
        world / "Target WoW",
        TARGET_FOLDER,
        {**target_files(DST_ACCOUNT), **legacy_account(LEGACY)},
    )
    return target, snapshot_id, world


@pytest.mark.parametrize(
    "with_account", [True, False], ids=["constructed-mapped", "constructed-accountless"]
)
def test_constructed_clash_refusal_says_what_to_do_and_paths_avoid_it(
    guard: Any, tmp_path: Path, store: SnapshotStore, idle: None, with_account: bool
) -> None:
    target, snapshot_id, world = _clash(tmp_path, store, with_account=with_account)
    accounts = {SRC_ACCOUNT: DST_ACCOUNT} if with_account else None
    legacy = target.path / "WTF" / "Account" / LEGACY
    legacy_before = {p: p.read_bytes() for p in legacy.rglob("*") if p.is_file()}
    before = strict_state(world)

    # The whole restore, and a named path below that folder, are refused; the
    # whole restore's refusal says what the owner can do.
    for paths in (None, [stray(LEGACY)]):
        with (
            guard.transaction(target, label="clash", store=store.path) as tx,
            pytest.raises(guard.GuardError) as refused,
        ):
            tx.restore(snapshot_id, paths=paths, into=into(guard, target, accounts))
        assert LEGACY in str(refused.value)
        if paths is None:
            assert "--paths" in str(refused.value), str(refused.value)
            assert "rename or remove" in str(refused.value), str(refused.value)
        assert strict_state(world) == before

    # Naming files that do not lie below that folder restores them.
    named = ["WTF/Config.wtf"]
    if with_account:
        named.append(f"WTF/Account/{SRC_ACCOUNT}/bindings-cache.wtf")
    with guard.transaction(target, label="around the clash", store=store.path) as tx:
        tx.restore(snapshot_id, paths=named, into=into(guard, target, accounts))
    assert (target.path / "WTF/Config.wtf").read_bytes() == source_files()["WTF/Config.wtf"]
    if with_account:
        landed = target.path / f"WTF/Account/{DST_ACCOUNT}/bindings-cache.wtf"
        assert (
            landed.read_bytes()
            == source_files(SRC_ACCOUNT)[f"WTF/Account/{SRC_ACCOUNT}/bindings-cache.wtf"]
        )
    assert {p: p.read_bytes() for p in legacy.rglob("*") if p.is_file()} == legacy_before
