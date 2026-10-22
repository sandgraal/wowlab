"""Graders for restoring a snapshot into a named target (M16-03T).

Written before M16-03, from docs/LAB_PLAN.md §18.1 item 1 (with §6.9 and
§6.10, and the outside evaluation's §4(a) item 2, which words the §6.10
amendment the wave makes), ADR-0021, and invariants L1, L2 and L6. Every
grader carries one marker line that M16-03 deletes; nothing else in this
file is the implementer's to change.

Everything here is constructed. The synthetic install, the fixtures and the
helpers are the ones in `test_guard.py` (loaded below, not copied): installs
are trees under `tmp_path`, the store is a directory under `tmp_path`, the
user data directory is redirected and the process table is injected. No
test needs or touches a real install.

The launch rename is the case this exists for: a snapshot taken of one
flavor folder must go back into a folder of another name after the client
renames it (`docs/GLOSSARY.md`, "flavor folder"). Flavor folder names appear
here only as test data (L6): every assertion reads them from the `Flavor`
objects the tests build, never from a literal.

The seam these graders hold `guard` to
--------------------------------------
- `guard.Target` is a public, frozen Pydantic model with no public methods
  (the surface grader in `test_guard.py` requires that of any public class
  beyond the API). It is built by keyword: `Target(flavor=<flavor-like>)`
  or `Target(flavor=<flavor-like>, accounts={<source folder>: <target
  folder>, ...})`. `flavor` is anything with a `path` (the flavor folder),
  as `transaction()` takes it. `accounts` maps account folder names under
  `WTF/Account/` and accepts a `dict[str, str]`.
- `tx.restore(snapshot_id, paths=None, *, into=None)`. The transaction is
  opened on the *target* flavor: that is what takes the target install's
  lock, asks the client check about the target, and takes the target's
  pre-write snapshot (§18.1: "The target gets its own pre-write snapshot.
  The allowlist, link and client checks apply to the target as they do to
  any write."). `into.flavor` must name the transaction's own flavor; a
  `Target` naming any other flavor, or an `into` that is not a `Target`, is
  a `GuardError` with nothing written.
- With `into`, every entry the snapshot holds under its own flavor folder
  (`Manifest.flavor_folder`) is written to the same path relative to the
  target's flavor folder, with `WTF/Account/<source>/` replaced by
  `WTF/Account/<target>/` when the account folders are mapped. Every
  journal path, plan path and history path is the target-relative path.
  The snapshot's own source flavor need not exist on disk any more.
- Account folders: the source side is the set of account folders the
  snapshot holds entries under (`WTF/Account/<name>/...`); the target side
  is the set of folders under the target's `WTF/Account/`. When each side
  has exactly one, they are mapped without being asked. When either side
  has more than one and `accounts` is not given, the restore is a
  `GuardError` whose message mentions accounts, with nothing written. A
  mapping value that is not one plain folder name is refused with nothing
  written.
- Without `into`, a snapshot of another flavor folder or another install
  root is refused as before (a `GuardError`, not a client refusal), and the
  message names the option: the word `into`.

Account folder names here have the `<digits>#<n>` shape, so they are
account folders by any rule `layout` applies (M13-50). Paths given to
`paths=` are spelled the same on both sides (`WTF/Config.wtf`), so these
graders take no position on which side's spelling `paths=` uses for an
account path.
"""

from __future__ import annotations

import importlib.util
import re
import shutil
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from wowlab_core.snapshot import SnapshotStore


def _load_m10_11t_graders() -> types.ModuleType:
    """`test_guard.py` as a module of its own, for its synthetic install,
    fixtures and helpers (the M10-16T graders load it the same way)."""
    path = Path(__file__).resolve().parent / "test_guard.py"
    spec = importlib.util.spec_from_file_location("_m16_03t_base_graders", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


G = _load_m10_11t_graders()

# The M10-11T fixtures, registered here under their own names: the user data
# directory and the process table are redirected for every test in this file.
_user_data_redirected = G._user_data_redirected
_real_process_table_is_never_listed = G._real_process_table_is_never_listed
guard = G.guard
idle = G.idle

Flavor = G.Flavor
FakeProcess = G.FakeProcess
DENIED = G.DENIED
CONFIG = G.CONFIG
TOC = G.TOC
FONT = G.FONT
OUTSIDE_TARGET = G.OUTSIDE_TARGET
content = G.content
strict_state = G.strict_state
record_for = G.record_for
changes = G.changes
sha = G.sha
symlink_or_skip = G.symlink_or_skip
forge_manifest = G.forge_manifest
use_probe = G.use_probe
table = G.table
bystanders = G.bystanders

# ─── test data (constructed) ─────────────────────────────────────────────────

# Flavor folder names: test data only (L6). The first pair is the rename the
# Forever launch is expected to make; the others prove nothing is special
# about it.
RENAMES: tuple[tuple[str, str, str], ...] = (
    ("constructed-beta-to-launch", "_classic_beta_", "_classic_"),
    ("constructed-ptr-to-xptr", "_ptr_", "_xptr_"),
    ("constructed-unlisted-names", "_alpha_lab_", "_omega_lab_"),
)
SOURCE_FOLDER = "_classic_beta_"
TARGET_FOLDER = "_classic_"

SRC_ACCOUNT = "11111111#1"
DST_ACCOUNT = "22222222#1"
SRC_ACCOUNT_2 = "11111111#2"
DST_ACCOUNT_2 = "22222222#2"


def account_files(account: str, tag: bytes) -> dict[str, bytes]:
    base = f"WTF/Account/{account}"
    return {
        f"{base}/bindings-cache.wtf": b"bind W MOVEFORWARD\nbind Q " + tag + b"\n",
        f"{base}/SavedVariables/Addon.lua": b'AddonDB = {\n\t["who"] = "' + tag + b'",\n}\n',
        f"{base}/Labrealm/Labchar/SavedVariables/Addon.lua": b'CharDB = {\n\t["c"] = "'
        + tag
        + b'",\n}\n',
    }


def source_files(*accounts: str) -> dict[str, bytes]:
    """What the source flavor held when the snapshot was taken."""
    files: dict[str, bytes] = {
        CONFIG: b'SET portal "US"\nSET constructed "source"\n',
        TOC: b"## Interface: 0\n## Title: Thing (source)\nThing.lua\n",
        FONT: b"\x00\x01\x00\x00constructed-source-font",
    }
    for account in accounts:
        files.update(account_files(account, b"source " + account.encode()))
    return files


def target_files(*accounts: str) -> dict[str, bytes]:
    """What the target held before the restore: a later client session."""
    files: dict[str, bytes] = {
        CONFIG: b'SET portal "US"\nSET constructed "target"\n',
        TOC: b"## Interface: 0\n## Title: Thing (target)\nThing.lua\n",
        FONT: b"\x00\x01\x00\x00constructed-source-font",
        "WTF/target-only.wtf": b"constructed: only the target has this\n",
    }
    for account in accounts:
        base = f"WTF/Account/{account}"
        files[f"{base}/bindings-cache.wtf"] = b"bind W MOVEFORWARD\nbind Q target\n"
        files[f"{base}/macros-cache.txt"] = b"constructed: target-only macro\n"
    return files


def make_install(root: Path, folder: str, files: dict[str, bytes]) -> Flavor:
    """A synthetic install root with one flavor folder holding `files`."""
    G._put(root, G.ROOT_FILES)
    add_flavor(root, folder, files)
    return flavor_of(root, folder)


def add_flavor(root: Path, folder: str, files: dict[str, bytes]) -> Flavor:
    G._put(root / folder, G.FLAVOR_ONLY_FILES)
    G._put(root / folder, {".flavor.info": b"Product Flavor!STRING:0\nconstructed\n"})
    G._put(root / folder, files)
    return flavor_of(root, folder)


def flavor_of(root: Path, folder: str) -> Flavor:
    return Flavor(
        folder=folder, product="constructed", version=None, build=None, build_key=None,
        path=root / folder,
    )  # fmt: skip


def snapshot_of(store: SnapshotStore, flavor: Flavor, label: str = "constructed: source") -> str:
    """A user snapshot of the flavor's default subtrees, rooted at the install
    root and naming its flavor folder, as `snap create` takes one (§6.9)."""
    manifest = store.create(
        flavor.path.parent,
        [f"{flavor.folder}/{s}" for s in ("WTF", "Interface", "Fonts")],
        label=label,
        flavor_folder=flavor.folder,
    )
    return manifest.id


def mapped(rel: str, accounts: dict[str, str]) -> str:
    for src, dst in accounts.items():
        prefix = f"WTF/Account/{src}/"
        if rel.startswith(prefix):
            return f"WTF/Account/{dst}/" + rel.removeprefix(prefix)
    return rel


def files_only(root: Path) -> dict[str, bytes | str]:
    return {k: v for k, v in content(root).items() if v != "<dir>"}


def into(guard: Any, flavor: Flavor, accounts: dict[str, str] | None = None) -> Any:
    """The one place the seam's `Target` is built."""
    if accounts is None:
        return guard.Target(flavor=flavor)
    return guard.Target(flavor=flavor, accounts=accounts)


def assert_restored(target: Flavor, files: dict[str, bytes], accounts: dict[str, str]) -> None:
    for rel, data in files.items():
        landed = target.path / mapped(rel, accounts)
        assert landed.read_bytes() == data, f"{rel} did not land at {mapped(rel, accounts)}"


def assert_not_a_client_refusal(guard: Any, exc: BaseException) -> None:
    assert isinstance(exc, guard.GuardError), f"{type(exc).__name__}: {exc}"
    assert not isinstance(exc, guard.ClientRunningError), f"a client refusal: {exc}"


def same_path(recorded: str, expected: Path) -> bool:
    return Path(recorded).resolve() == expected.resolve()


@pytest.fixture
def store(tmp_path: Path) -> SnapshotStore:
    return SnapshotStore(tmp_path / "store")


@pytest.fixture
def renamed(tmp_path: Path, store: SnapshotStore) -> tuple[Path, Flavor, str]:
    """The launch rename: a snapshot of the source folder, then the folder
    renamed and changed by a later session. Returns (world, target, id)."""
    world = tmp_path / "world"
    G._put(world / "outside", {"target.txt": OUTSIDE_TARGET})
    source = make_install(world / "World of Warcraft", SOURCE_FOLDER, source_files(SRC_ACCOUNT))
    snapshot_id = snapshot_of(store, source)
    target_path = source.path.parent / TARGET_FOLDER
    source.path.rename(target_path)
    G._put(target_path, target_files(SRC_ACCOUNT))
    return world, flavor_of(source.path.parent, TARGET_FOLDER), snapshot_id


@pytest.fixture
def two_roots(tmp_path: Path, store: SnapshotStore) -> tuple[Path, Flavor, Flavor, str]:
    """A source install and another install root with the same folder name.
    Returns (world, source, target, id)."""
    world = tmp_path / "world"
    G._put(world / "outside", {"target.txt": OUTSIDE_TARGET})
    source = make_install(world / "Source WoW", SOURCE_FOLDER, source_files(SRC_ACCOUNT))
    snapshot_id = snapshot_of(store, source)
    target = make_install(world / "Target WoW", SOURCE_FOLDER, target_files(DST_ACCOUNT))
    return world, source, target, snapshot_id


# ─── the renamed flavor folder ───────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="M16-03 not implemented")
@pytest.mark.parametrize(
    ("old", "new"), [pytest.param(old, new, id=name) for name, old, new in RENAMES]
)
def test_constructed_restore_into_a_renamed_flavor_folder(
    guard: Any, tmp_path: Path, store: SnapshotStore, idle: None, old: str, new: str
) -> None:
    root = tmp_path / "World of Warcraft"
    source = make_install(root, old, source_files(SRC_ACCOUNT))
    snapshot_id = snapshot_of(store, source)
    source.path.rename(root / new)
    G._put(root / new, target_files(SRC_ACCOUNT))
    target = flavor_of(root, new)
    later = files_only(target.path)

    with guard.transaction(target, label="after the rename", store=store.path) as tx:
        tx.restore(snapshot_id, into=into(guard, target))

    files = source_files(SRC_ACCOUNT)
    assert_restored(target, files, {})
    # A file the snapshot does not hold is left as the later session made it.
    rel = "WTF/target-only.wtf"
    assert (target.path / rel).read_bytes() == later[rel]
    # Nothing is created under the old name: the folder the client renamed
    # stays gone.
    assert sorted(p.name for p in root.iterdir() if p.is_dir()) == sorted(["Data", target.folder])

    record = record_for(guard, store, "after the rename")
    assert same_path(record.flavor_path, target.path)
    changed = {rel for rel, data in files.items() if later.get(rel) != data}
    assert set(changes(record.paths)) == changed
    for item in record.paths:
        assert not item.path.startswith(source.folder), "journal paths are target-relative"


# ─── another install root ────────────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="M16-03 not implemented")
@pytest.mark.parametrize(
    "folder", ["same-folder", "other-folder"], ids=lambda f: f"constructed-{f}"
)
@pytest.mark.parametrize(
    "source_state", ["source-kept", "source-removed"], ids=lambda s: f"constructed-{s}"
)
def test_constructed_restore_into_another_install_root(
    guard: Any, tmp_path: Path, store: SnapshotStore, idle: None, folder: str, source_state: str
) -> None:
    world = tmp_path / "world"
    source = make_install(world / "Source WoW", SOURCE_FOLDER, source_files(SRC_ACCOUNT))
    snapshot_id = snapshot_of(store, source)
    target_folder = SOURCE_FOLDER if folder == "same-folder" else TARGET_FOLDER
    target = make_install(world / "Target WoW", target_folder, target_files(SRC_ACCOUNT))
    if source_state == "source-removed":
        # A snapshot outlives its install (another machine, a reinstall):
        # the restore reads only the store.
        shutil.rmtree(world / "Source WoW")
        source_before = None
    else:
        source_before = strict_state(world / "Source WoW")

    with guard.transaction(target, label="other root", store=store.path) as tx:
        tx.restore(snapshot_id, into=into(guard, target))

    assert_restored(target, source_files(SRC_ACCOUNT), {})
    if source_before is not None:
        assert strict_state(world / "Source WoW") == source_before, "the source is only read"
    else:
        assert not (world / "Source WoW").exists()
    record = record_for(guard, store, "other root")
    assert same_path(record.flavor_path, target.path)


# ─── account folders ─────────────────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="M16-03 not implemented")
def test_constructed_single_account_folders_are_mapped_without_being_asked(
    guard: Any, two_roots: tuple[Path, Flavor, Flavor, str], store: SnapshotStore, idle: None
) -> None:
    _world, _source, target, snapshot_id = two_roots
    macros = f"WTF/Account/{DST_ACCOUNT}/macros-cache.txt"
    macros_before = (target.path / macros).read_bytes()

    with guard.transaction(target, label="one to one", store=store.path) as tx:
        tx.restore(snapshot_id, into=into(guard, target))

    accounts = {SRC_ACCOUNT: DST_ACCOUNT}
    assert_restored(target, source_files(SRC_ACCOUNT), accounts)
    assert not (target.path / "WTF" / "Account" / SRC_ACCOUNT).exists(), (
        "the source's account folder is mapped, never created beside the target's"
    )
    assert (target.path / macros).read_bytes() == macros_before
    paths = set(changes(record_for(guard, store, "one to one").paths))
    assert f"WTF/Account/{DST_ACCOUNT}/bindings-cache.wtf" in paths
    assert not any(SRC_ACCOUNT in p for p in paths)


AMBIGUOUS: tuple[tuple[str, tuple[str, ...], tuple[str, ...], dict[str, str] | None], ...] = (
    (
        "constructed-two-source-one-target",
        (SRC_ACCOUNT, SRC_ACCOUNT_2),
        (DST_ACCOUNT,),
        None,
    ),
    (
        "constructed-one-source-two-target",
        (SRC_ACCOUNT,),
        (DST_ACCOUNT, DST_ACCOUNT_2),
        {SRC_ACCOUNT: DST_ACCOUNT_2},
    ),
    (
        "constructed-two-source-two-target",
        (SRC_ACCOUNT, SRC_ACCOUNT_2),
        (DST_ACCOUNT, DST_ACCOUNT_2),
        # Crosswise, so a restore that ignored the mapping would show.
        {SRC_ACCOUNT: DST_ACCOUNT_2, SRC_ACCOUNT_2: DST_ACCOUNT},
    ),
)


@pytest.mark.xfail(strict=True, reason="M16-03 not implemented")
@pytest.mark.parametrize(
    ("src_accounts", "dst_accounts", "mapping"),
    [pytest.param(s, d, m, id=name) for name, s, d, m in AMBIGUOUS],
)
def test_constructed_account_folders_are_refused_without_a_mapping_when_either_side_has_more(
    guard: Any,
    tmp_path: Path,
    store: SnapshotStore,
    idle: None,
    src_accounts: tuple[str, ...],
    dst_accounts: tuple[str, ...],
    mapping: dict[str, str] | None,
) -> None:
    world = tmp_path / "world"
    source = make_install(world / "Source WoW", SOURCE_FOLDER, source_files(*src_accounts))
    snapshot_id = snapshot_of(store, source)
    target = make_install(world / "Target WoW", TARGET_FOLDER, target_files(*dst_accounts))
    target_into = into(guard, target)
    before = strict_state(world)

    with (
        guard.transaction(target, label="ambiguous", store=store.path) as tx,
        pytest.raises(guard.GuardError) as refused,
    ):
        tx.restore(snapshot_id, into=target_into)
    assert_not_a_client_refusal(guard, refused.value)
    assert "account" in str(refused.value).casefold(), str(refused.value)
    assert strict_state(world) == before, "a refused mapping writes nothing"

    if mapping is None:
        return
    # Positive control: the same restore with the mapping given.
    with guard.transaction(target, label="mapped", store=store.path) as tx:
        tx.restore(snapshot_id, into=into(guard, target, mapping))
    assert_restored(target, source_files(*src_accounts), mapping)
    for account in src_accounts:
        assert not (target.path / "WTF" / "Account" / account).exists()


HOSTILE_ACCOUNT_VALUES: tuple[tuple[str, str], ...] = (
    ("constructed-empty", ""),
    ("constructed-dot", "."),
    ("constructed-dot-dot", ".."),
    ("constructed-traversal-out-of-the-install", "../../../.."),
    ("constructed-traversal-to-the-flavor-root", "../.."),
    ("constructed-two-components", "nested/deeper"),
    ("constructed-absolute", "/constructed-absolute"),
)


@pytest.mark.xfail(strict=True, reason="M16-03 not implemented")
@pytest.mark.parametrize("value", [pytest.param(v, id=name) for name, v in HOSTILE_ACCOUNT_VALUES])
def test_constructed_account_mapping_that_is_not_one_folder_name_is_refused(
    guard: Any,
    two_roots: tuple[Path, Flavor, Flavor, str],
    store: SnapshotStore,
    idle: None,
    value: str,
) -> None:
    world, _source, target, snapshot_id = two_roots
    hostile = into(guard, target, {SRC_ACCOUNT: value})
    before = strict_state(world)

    with (
        guard.transaction(target, label="hostile mapping", store=store.path) as tx,
        pytest.raises(guard.GuardError) as refused,
    ):
        tx.restore(snapshot_id, into=hostile)
    assert_not_a_client_refusal(guard, refused.value)
    assert strict_state(world) == before

    # Positive control: a plain folder name is accepted.
    with guard.transaction(target, label="plain mapping", store=store.path) as tx:
        tx.restore(snapshot_id, into=into(guard, target, {SRC_ACCOUNT: DST_ACCOUNT}))
    assert_restored(target, source_files(SRC_ACCOUNT), {SRC_ACCOUNT: DST_ACCOUNT})


# ─── without `into` ──────────────────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="M16-03 not implemented")
@pytest.mark.parametrize("paths", [None, [CONFIG]], ids=["constructed-whole", "constructed-named"])
@pytest.mark.parametrize("where", ["renamed", "other-root"], ids=lambda w: f"constructed-{w}")
def test_constructed_foreign_snapshot_without_into_is_refused_naming_the_option(
    guard: Any,
    renamed: tuple[Path, Flavor, str],
    two_roots: tuple[Path, Flavor, Flavor, str],
    store: SnapshotStore,
    idle: None,
    where: str,
    paths: list[str] | None,
) -> None:
    if where == "renamed":
        world, target, snapshot_id = renamed
    else:
        world, _, target, snapshot_id = two_roots
    target_into = into(guard, target)
    before = strict_state(world)

    with (
        guard.transaction(target, label="no into", store=store.path) as tx,
        pytest.raises(guard.GuardError) as refused,
    ):
        tx.restore(snapshot_id, paths=paths)
    assert_not_a_client_refusal(guard, refused.value)
    assert re.search(r"\binto\b", str(refused.value)), (
        f"the refusal must name the option: {refused.value}"
    )
    assert strict_state(world) == before

    # Positive control: naming the target is the whole difference.
    with guard.transaction(target, label="with into", store=store.path) as tx:
        tx.restore(snapshot_id, paths=paths, into=target_into)
    assert (target.path / CONFIG).read_bytes() == source_files()[CONFIG]


@pytest.mark.xfail(strict=True, reason="M16-03 not implemented")
@pytest.mark.parametrize(
    "wrong", ["another-flavor", "another-root", "not-a-target"], ids=lambda w: f"constructed-{w}"
)
def test_constructed_into_must_name_the_transactions_own_flavor(
    guard: Any,
    two_roots: tuple[Path, Flavor, Flavor, str],
    store: SnapshotStore,
    idle: None,
    wrong: str,
) -> None:
    """The player names the target, and the gate writes only where it took
    the lock, asked the client check and took the snapshot."""
    world, source, target, snapshot_id = two_roots
    elsewhere = add_flavor(target.path.parent, TARGET_FOLDER, target_files(DST_ACCOUNT))
    named: Any
    if wrong == "another-flavor":
        named = into(guard, elsewhere)
    elif wrong == "another-root":
        named = into(guard, source)
    else:
        named = str(target.path)
    good = into(guard, target)
    before = strict_state(world)

    with (
        guard.transaction(target, label="wrong into", store=store.path) as tx,
        pytest.raises(guard.GuardError) as refused,
    ):
        tx.restore(snapshot_id, into=named)
    assert_not_a_client_refusal(guard, refused.value)
    assert strict_state(world) == before

    with guard.transaction(target, label="right into", store=store.path) as tx:
        tx.restore(snapshot_id, into=good)
    assert (target.path / CONFIG).read_bytes() == source_files()[CONFIG]


# ─── the target's own pre-write snapshot ─────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="M16-03 not implemented")
def test_constructed_target_gets_its_own_pre_write_snapshot(
    guard: Any, tmp_path: Path, store: SnapshotStore, idle: None
) -> None:
    """Both flavors of one install present: settings carried from one to the
    other. The pre-write snapshot is the target's, holds the target's bytes,
    and holds nothing of the source folder."""
    root = tmp_path / "World of Warcraft"
    source = make_install(root, SOURCE_FOLDER, source_files(SRC_ACCOUNT))
    target = add_flavor(root, TARGET_FOLDER, target_files(SRC_ACCOUNT))
    snapshot_id = snapshot_of(store, source)
    source_before = strict_state(source.path)
    target_before = files_only(target.path)

    with guard.transaction(target, label="carry over", store=store.path) as tx:
        tx.restore(snapshot_id, into=into(guard, target))

    assert_restored(target, source_files(SRC_ACCOUNT), {})
    assert strict_state(source.path) == source_before

    record = record_for(guard, store, "carry over")
    assert record.snapshot_id != snapshot_id
    pre = store.show(record.snapshot_id)
    assert pre.flavor_folder == target.folder
    assert same_path(pre.install_root, root)
    held = {e.path: e for e in pre.entries}
    assert not any(p.startswith(source.folder + "/") for p in held), (
        "the target's pre-write snapshot is of the target alone"
    )
    config = held[f"{target.folder}/{CONFIG}"]
    assert config.sha256 == sha(target_before[CONFIG])  # type: ignore[arg-type]

    # That snapshot is of the target's own flavor, so it goes back without
    # `into`: the change is recoverable the ordinary way.
    with guard.transaction(target, label="put back", store=store.path) as tx:
        tx.restore(record.snapshot_id, paths=[CONFIG])
    assert (target.path / CONFIG).read_bytes() == target_before[CONFIG]


@pytest.mark.xfail(strict=True, reason="M16-03 not implemented")
def test_constructed_dry_run_into_a_target_plans_target_paths_and_writes_nothing(
    guard: Any, two_roots: tuple[Path, Flavor, Flavor, str], store: SnapshotStore, idle: None
) -> None:
    world, _source, target, snapshot_id = two_roots
    before = strict_state(world)
    history = list(guard.history(store=store.path))

    with guard.transaction(target, label="plan", store=store.path, dry_run=True) as tx:
        tx.restore(snapshot_id, into=into(guard, target))
        planned = {item.path for item in tx.plan}

    accounts = {SRC_ACCOUNT: DST_ACCOUNT}
    expected = {
        mapped(rel, accounts)
        for rel, data in source_files(SRC_ACCOUNT).items()
        if not (target.path / mapped(rel, accounts)).is_file()
        or (target.path / mapped(rel, accounts)).read_bytes() != data
    }
    assert planned == expected
    assert strict_state(world) == before
    assert list(guard.history(store=store.path)) == history


# ─── the allowlist and links, at the target ──────────────────────────────────

FORGED: tuple[tuple[str, str, bool], ...] = (
    *((f"constructed-{name}", rel, False) for name, rel in G.HOSTILE_ENTRIES),
    *((f"constructed-{name}", rel, True) for name, rel in G.RAW_TARGETS),
)


@pytest.mark.xfail(strict=True, reason="M16-03 not implemented")
@pytest.mark.parametrize(
    ("hostile_rel", "raw"), [pytest.param(rel, raw, id=name) for name, rel, raw in FORGED]
)
def test_constructed_entries_the_gate_refuses_are_refused_at_the_target(
    guard: Any,
    two_roots: tuple[Path, Flavor, Flavor, str],
    store: SnapshotStore,
    idle: None,
    hostile_rel: str,
    raw: bool,
) -> None:
    """The snapshot is untrusted input at restore time (§6.10): mapping its
    paths into another folder never lets an entry past the allowlist."""
    world, _source, target, snapshot_id = two_roots
    forged_id = forge_manifest(store, store.show(snapshot_id), hostile_rel, raw=raw)
    target_into = into(guard, target)
    before = strict_state(world)

    with (
        guard.transaction(target, label="forged", store=store.path) as tx,
        pytest.raises(guard.GuardError) as refused,
    ):
        tx.restore(forged_id, into=target_into)
    assert_not_a_client_refusal(guard, refused.value)
    if not raw:
        assert isinstance(refused.value, guard.PathNotAllowedError), repr(refused.value)
    assert content(world) == {k: v[0] for k, v in before.items()}, "nothing of it lands"

    # Positive control: the unforged snapshot restores into the same target.
    with guard.transaction(target, label="legit", store=store.path) as tx:
        tx.restore(snapshot_id, paths=[CONFIG], into=target_into)
    assert (target.path / CONFIG).read_bytes() == source_files()[CONFIG]


LINK_CASES = ("addon-folder-link", "config-file-link", "account-folder-link")


@pytest.mark.xfail(strict=True, reason="M16-03 not implemented")
@pytest.mark.parametrize("case", LINK_CASES, ids=lambda c: f"constructed-{c}")
def test_constructed_links_at_the_target_are_refused(
    guard: Any,
    two_roots: tuple[Path, Flavor, Flavor, str],
    store: SnapshotStore,
    idle: None,
    case: str,
) -> None:
    world, _source, target, snapshot_id = two_roots
    outside = world / "outside"
    accounts: dict[str, str] | None = None
    if case == "addon-folder-link":
        addon = target.path / "Interface/AddOns/Thing"
        shutil.rmtree(addon)
        symlink_or_skip(addon, outside, is_dir=True)
    elif case == "config-file-link":
        (target.path / CONFIG).unlink()
        symlink_or_skip(target.path / CONFIG, outside / "target.txt", is_dir=False)
    else:
        account = target.path / "WTF/Account" / DST_ACCOUNT
        shutil.rmtree(account)
        symlink_or_skip(account, outside, is_dir=True)
        accounts = {SRC_ACCOUNT: DST_ACCOUNT}
    named = into(guard, target, accounts)
    before = content(world)

    with (
        pytest.raises(guard.GuardError) as refused,
        guard.transaction(target, label="through a link", store=store.path) as tx,
    ):
        tx.restore(snapshot_id, into=named)
    assert_not_a_client_refusal(guard, refused.value)
    if case != "account-folder-link":
        assert isinstance(refused.value, guard.PathNotAllowedError), repr(refused.value)
    assert content(world) == before
    assert (outside / "target.txt").read_bytes() == OUTSIDE_TARGET
    assert sorted(p.name for p in outside.iterdir()) == ["target.txt"]


@pytest.mark.xfail(strict=True, reason="M16-03 not implemented")
def test_constructed_restore_into_a_target_never_creates_a_link(
    guard: Any, two_roots: tuple[Path, Flavor, Flavor, str], store: SnapshotStore, idle: None
) -> None:
    world, _source, target, snapshot_id = two_roots
    forged_id = forge_manifest(
        store, store.show(snapshot_id), "WTF/escape", link_target="../../../outside"
    )
    target_into = into(guard, target)
    before = content(world)

    with (
        pytest.raises(guard.GuardError) as refused,
        guard.transaction(target, label="link entry", store=store.path) as tx,
    ):
        tx.restore(forged_id, into=target_into)
    assert_not_a_client_refusal(guard, refused.value)
    assert content(world) == before
    assert not (target.path / "WTF/escape").is_symlink()

    with guard.transaction(target, label="legit", store=store.path) as tx:
        tx.restore(snapshot_id, paths=[CONFIG], into=target_into)
    assert (target.path / CONFIG).read_bytes() == source_files()[CONFIG]


# ─── the client check, at the target ─────────────────────────────────────────

UNLISTED = "ForeverClient.exe"  # a client name `process` does not list


def _target_running(kind: str, target: Flavor) -> FakeProcess:
    if kind == "running-from-the-target-root":
        return FakeProcess(7201, name=UNLISTED, exe=str(target.path.parent / UNLISTED))
    if kind == "running-from-the-target-flavor-folder":
        return FakeProcess(7202, name=UNLISTED, exe=str(target.path / UNLISTED))
    return FakeProcess(7203, name=DENIED, exe=DENIED, cmdline=DENIED)  # unknown


@pytest.mark.xfail(strict=True, reason="M16-03 not implemented")
@pytest.mark.parametrize(
    "kind",
    [
        "running-from-the-target-root",
        "running-from-the-target-flavor-folder",
        "unknown-counts-as-running",
    ],
    ids=lambda k: f"constructed-{k}",
)
@pytest.mark.parametrize(
    "source_state", ["source-idle", "source-running"], ids=lambda s: f"constructed-{s}"
)
def test_constructed_client_running_from_the_target_refuses_whatever_the_source_does(
    guard: Any,
    tmp_path: Path,
    two_roots: tuple[Path, Flavor, Flavor, str],
    store: SnapshotStore,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    source_state: str,
) -> None:
    world, source, target, snapshot_id = two_roots
    target_into = into(guard, target)
    procs = [*bystanders(tmp_path), _target_running(kind, target)]
    if source_state == "source-running":
        procs.append(FakeProcess(7101, name=UNLISTED, exe=str(source.path / UNLISTED)))
    use_probe(monkeypatch, table(*procs))
    before = strict_state(world)
    entered = False

    with (
        pytest.raises(guard.ClientRunningError),
        guard.transaction(target, label="client up", store=store.path) as tx,
    ):
        entered = True
        tx.restore(snapshot_id, into=target_into)
    assert not entered
    assert strict_state(world) == before
    assert list(guard.history(store=store.path)) == []

    # Positive control: with no client anywhere the same restore goes through.
    use_probe(monkeypatch, table(*bystanders(tmp_path)))
    with guard.transaction(target, label="client down", store=store.path) as tx:
        tx.restore(snapshot_id, into=target_into)
    assert (target.path / CONFIG).read_bytes() == source_files()[CONFIG]


# ─── the target's lock, and the store against the target ─────────────────────


@pytest.mark.xfail(strict=True, reason="M16-03 not implemented")
def test_constructed_the_target_install_lock_is_held_through_the_restore(
    guard: Any,
    tmp_path: Path,
    two_roots: tuple[Path, Flavor, Flavor, str],
    store: SnapshotStore,
    idle: None,
) -> None:
    _world, _source, target, snapshot_id = two_roots
    other_store = tmp_path / "other-store"
    with guard.transaction(target, label="locked", store=store.path) as tx:
        tx.restore(snapshot_id, into=into(guard, target))
        # Another transaction on the target, through another store, meets
        # the target install's lock.
        with (
            pytest.raises(guard.GuardBusyError),
            guard.transaction(target, label="second", store=other_store),
        ):
            pass
    assert (target.path / CONFIG).read_bytes() == source_files()[CONFIG]
    # Released on exit.
    with guard.transaction(target, label="after", store=other_store, dry_run=True):
        pass


@pytest.mark.xfail(strict=True, reason="M16-03 not implemented")
@pytest.mark.parametrize(
    "overlap", ["store-inside-the-target-root", "target-root-inside-the-store"],
    ids=lambda o: f"constructed-{o}",
)  # fmt: skip
def test_constructed_target_root_that_overlaps_the_store_is_refused(
    guard: Any, tmp_path: Path, idle: None, overlap: str
) -> None:
    world = tmp_path / "world"
    source = make_install(world / "Source WoW", SOURCE_FOLDER, source_files(SRC_ACCOUNT))
    if overlap == "store-inside-the-target-root":
        target_root = world / "Target WoW"
        store = SnapshotStore(target_root / "wowlab-store")
    else:
        store = SnapshotStore(world / "store")
        target_root = store.path / "Target WoW"
    # The snapshot is taken before the target install exists around (or
    # inside) the store, as when an install is put there later.
    snapshot_id = snapshot_of(store, source)
    target = make_install(target_root, SOURCE_FOLDER, target_files(SRC_ACCOUNT))
    target_into = into(guard, target)
    before = content(world)

    with (
        pytest.raises(guard.GuardError) as refused,
        guard.transaction(target, label="overlap", store=store.path) as tx,
    ):
        tx.restore(snapshot_id, into=target_into)
    assert_not_a_client_refusal(guard, refused.value)
    assert content(world) == before

    # Positive control: the same source and a target clear of the store.
    clear_store = SnapshotStore(tmp_path / "clear-store")
    clear_id = snapshot_of(clear_store, source)
    clear = make_install(tmp_path / "Clear WoW", TARGET_FOLDER, target_files(SRC_ACCOUNT))
    with guard.transaction(clear, label="clear", store=clear_store.path) as tx:
        tx.restore(clear_id, into=into(guard, clear))
    assert (clear.path / CONFIG).read_bytes() == source_files()[CONFIG]


# ─── undo ────────────────────────────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="M16-03 not implemented")
@pytest.mark.parametrize("where", ["renamed", "other-root"], ids=lambda w: f"constructed-{w}")
def test_constructed_undo_of_a_restore_into_another_target(
    guard: Any,
    renamed: tuple[Path, Flavor, str],
    two_roots: tuple[Path, Flavor, Flavor, str],
    store: SnapshotStore,
    idle: None,
    where: str,
) -> None:
    if where == "renamed":
        _world, target, snapshot_id = renamed
        source_root: Path | None = None
    else:
        _world, source, target, snapshot_id = two_roots
        source_root = source.path.parent
    source_before = None if source_root is None else strict_state(source_root)
    before = files_only(target.path)

    with guard.transaction(target, label="transplant", store=store.path) as tx:
        tx.restore(snapshot_id, into=into(guard, target))
    assert files_only(target.path) != before

    guard.undo(store=store.path)

    assert files_only(target.path) == before
    if source_root is not None:
        assert strict_state(source_root) == source_before
    records = list(guard.history(store=store.path))
    assert records[-2].label == "transplant"
    assert same_path(records[-1].flavor_path, target.path), "the undo is of the target"
    assert set(changes(records[-1].paths)) == set(changes(records[-2].paths))
