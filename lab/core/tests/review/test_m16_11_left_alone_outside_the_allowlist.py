# Probe from review of m16/11-restore-left-alone; reproduces a whole restore's "left alone" list naming files outside the gate's allowlist, which the plan then tells the player to delete with --paths.
"""A whole restore lists, as left alone, the files added under the
snapshot's subtrees since it was taken, and the CLI heading says "name one
with --paths to delete it". Both `guard._Transaction._added_since` and
`cli._added_since` take the subtrees from `manifest.subtrees` and never hold
them to the gate's allowlist roots (`guard._ROOTS`). A snapshot whose
subtrees reach outside those roots (an empty `Logs/`, or the whole flavor
folder) then lists a file the gate would refuse to delete.

Constructed (L8): snapshots taken with `SnapshotStore.create` of the
captured tree copied into `tmp_path`, with chosen subtrees, and files added
after them. The user data directory is redirected and the process table is
a fake. Nothing reads or writes a real install. The listing only reads; this
is about what the plan tells the player, not about a write.
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_cli import (
    FLAVOR,
    _plain,
    flavor,  # noqa: F401
    idle,  # noqa: F401  (autouse: no client in the process table)
    ok,
    replayed,  # noqa: F401  (autouse: recorded game data only)
    root,  # noqa: F401
    user_data,  # noqa: F401
)

from wowlab_core import cli, guard, install, snapshot

ALLOWED_ADDED = "Fonts/PROBE_ADDED.TTF"  # positive control: under an allowlist root
OUTSIDE_ADDED = "ProbeAddedSince/new.log"  # added under a subtree outside the roots
LOGS_ADDED = "Logs/probe-new.log"


def _flavor(root: Path) -> install.Flavor:
    return next(f for f in install.discover(root).flavors if f.folder == FLAVOR)


def _put(flavor: Path, rel: str) -> None:
    path = flavor / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"constructed: added since the snapshot\n")


def _logs_snapshot(root: Path, flavor: Path) -> tuple[snapshot.Manifest, str]:
    """`WTF/`, `Fonts/` and an empty `Logs/`; then a log is added."""
    chosen = _flavor(root)
    logs = flavor / "Logs"
    if logs.exists():
        for child in sorted(logs.rglob("*"), reverse=True):
            child.rmdir() if child.is_dir() else child.unlink()
    logs.mkdir(exist_ok=True)
    manifest = snapshot.SnapshotStore().create(
        root,
        [f"{FLAVOR}/WTF", f"{FLAVOR}/Fonts", f"{FLAVOR}/Logs"],
        label="constructed",
        flavor_folder=FLAVOR,
        flavor_version=chosen.version,
    )
    _put(flavor, LOGS_ADDED)
    _put(flavor, ALLOWED_ADDED)
    return manifest, LOGS_ADDED


def _whole_flavor_snapshot(root: Path, flavor: Path) -> tuple[snapshot.Manifest, str]:
    """The whole flavor folder, every top-level entry outside the allowlist
    roots excluded (so the gate does not refuse what the snapshot holds);
    then a file is added in a new top-level folder."""
    chosen = _flavor(root)
    outside = sorted(
        f"{FLAVOR}/{child.name}" for child in flavor.iterdir() if child.name not in guard._ROOTS
    )
    assert f"{FLAVOR}/{OUTSIDE_ADDED.split('/')[0]}" not in outside
    manifest = snapshot.SnapshotStore().create(
        root,
        [FLAVOR],
        exclude=outside,
        label="constructed",
        flavor_folder=FLAVOR,
        flavor_version=chosen.version,
    )
    _put(flavor, OUTSIDE_ADDED)
    _put(flavor, ALLOWED_ADDED)
    return manifest, OUTSIDE_ADDED


SCENARIOS = {
    "constructed-empty-logs-subtree": _logs_snapshot,
    "constructed-whole-flavor-subtree": _whole_flavor_snapshot,
}


def _guard_left_alone(root: Path, manifest: snapshot.Manifest) -> tuple[str, ...]:
    with guard.transaction(_flavor(root), label="probe", dry_run=True) as tx:
        tx.restore(manifest.id)
        return tx.left_alone


def _cli_heading(manifest: snapshot.Manifest) -> list[str]:
    lines = _plain(ok("snap", "restore", manifest.id, "--dry-run").stdout).splitlines()
    at = [i for i, ln in enumerate(lines) if ln.startswith("  Left alone: ")]
    if not at:
        return []
    out: list[str] = []
    for ln in lines[at[0] + 1 :]:
        if not ln.startswith("    "):
            break
        out.append(ln.strip())
    return out


def _cli_helper(root: Path, manifest: snapshot.Manifest) -> tuple[str, ...]:
    # The list the CLI computes itself when it restores by name.
    return cli._added_since(snapshot.SnapshotStore(), manifest, root, FLAVOR)


def _outside_roots(paths: object) -> list[str]:
    return [p for p in paths if p.split("/")[0] not in guard._ROOTS]  # type: ignore[attr-defined]


@pytest.mark.parametrize("make", SCENARIOS.values(), ids=SCENARIOS.keys())
def test_positive_control_an_added_file_under_an_allowlist_root_is_listed_constructed(
    root: Path, flavor: Path, make: object
) -> None:
    manifest, _ = make(root, flavor)  # type: ignore[operator]
    assert ALLOWED_ADDED in _guard_left_alone(root, manifest)
    assert ALLOWED_ADDED in _cli_heading(manifest)
    assert ALLOWED_ADDED in _cli_helper(root, manifest)


@pytest.mark.parametrize("make", SCENARIOS.values(), ids=SCENARIOS.keys())
def test_left_alone_lists_nothing_outside_the_allowlist_roots_constructed(
    root: Path, flavor: Path, make: object
) -> None:
    manifest, added = make(root, flavor)  # type: ignore[operator]
    assert (flavor / added).is_file()
    found = {
        "guard tx.left_alone": _outside_roots(_guard_left_alone(root, manifest)),
        "cli heading": _outside_roots(_cli_heading(manifest)),
        "cli _added_since": _outside_roots(_cli_helper(root, manifest)),
    }
    assert found == {k: [] for k in found}, found
