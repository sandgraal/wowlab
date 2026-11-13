"""lastbackup: a flavor's newest backup, and whether the game updated since
(docs/LAB_PLAN.md §19 CW-3, M17-04).

A *backup* is a snapshot in wowlab's store of the flavor's whole set of
folders, taken from this install on this computer: one the player took
(`snap create`, the UI's Take a backup now), the first run's, or the one
the write gate takes before a change. A saved profile (restricted to its own paths)
and a snapshot added by `snap import` are not. The game *updated* since the
newest backup when the client's version, as discovery reads it from
`.build.info`, differs from the version that backup recorded; when either is
not known, wowlab cannot tell and says nothing.

`char list --summary` (the UI's Account tab) and `doctor`'s next steps both
read this, so the two never disagree. Reads the store only, never the
install; creates nothing.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from pathlib import Path

from wowlab_core import firstrun, snapshot

# The kinds that are not a backup of the whole flavor (see the docstring).
_NOT_BACKUPS: frozenset[snapshot.SnapshotKind] = frozenset({"profile", "imported"})


def latest_backup(
    manifests: Sequence[snapshot.Manifest],
    root: Path,
    flavor_folder: str,
    imported_ids: Collection[str] = (),
) -> snapshot.Manifest | None:
    """The newest backup of `flavor_folder` in the install at `root` among
    `manifests`, or None when there is none."""
    mine = [
        m
        for m in manifests
        if m.flavor_folder == flavor_folder
        and snapshot.kind_of(m, (), imported_ids) not in _NOT_BACKUPS
        and firstrun._same_root(m.install_root, root)
    ]
    return max(mine, key=lambda m: (m.created_at, m.id), default=None)


def game_updated(latest: snapshot.Manifest | None, installed_version: str | None) -> bool:
    """Whether the client's version differs from the one `latest` recorded.
    False with no backup, or when either version is not known."""
    if latest is None or latest.flavor_version is None or installed_version is None:
        return False
    return latest.flavor_version != installed_version


def store_listing(
    store: snapshot.SnapshotStore,
) -> tuple[Sequence[snapshot.Manifest], frozenset[str]]:
    """Every manifest that loads, and the ids `snap import` added. Raises
    `snapshot.SnapshotError` or `OSError` when the store cannot be listed."""
    return store.list_lenient().manifests, store.imported_ids()
