"""Characters kept only in a backup (M18-06, docs/design/COMPANION.md).

A character whose lab-addon record is no longer in the install (a closed
beta's flavor folder deleted, a character folder removed) may still be held
by a snapshot in wowlab's store. `find` names each such character once: the
newest copy of its `WowLab.lua` that a snapshot of this install holds, with
that snapshot's id, time and recorded client version.

Which snapshots count: every snapshot of the install at `root` (the same
install folder, as `lastbackup` decides it) that `snap import` did not add.
A snapshot taken elsewhere is another computer's install, and its
characters are not this one's.

A character is *in the install* when its folder
(`<flavor>/WTF/Account/<account>/<realm or digits folder>/<character>/`)
exists there in any form: it then has a card of its own, so it is not kept.
A folder whose existence cannot be checked (a refused `lstat`) counts as in
the install: wowlab does not claim a character is gone when it cannot tell.

Which copy is the newest: the one with the latest save time (the file's
modification time, as the manifest records it), then the newest snapshot
holding it. Within one snapshot the file is the one `labaddon.choose_lab_file`
chooses among a character folder's case variants.

Reads only. The store is read through manifests the caller lists and
`SnapshotStore.read_object`; the install is only `lstat`-ed, never opened,
and nothing is restored, copied or written anywhere.
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Sequence
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from wowlab_core import charhistory, firstrun, labaddon, longpath, luadata, snapshot

__all__ = ["KeptCopy", "counts", "find", "in_install", "read_copy"]

# A character's lab file as a manifest names it, relative to the install
# root: `<flavor>/WTF/Account/<account>/<realm>/<character>/SavedVariables/
# WowLab.lua`. The three fixed folder names are the client's, matched with
# case folded as the layout's walk matches them; nothing about a flavor.
_PARTS = 8
_WTF = "wtf"
_ACCOUNT = "account"
_SAVED_VARIABLES = "savedvariables"


class KeptCopy(BaseModel):
    """The newest copy of one character's `WowLab.lua` in the store, for a
    character the install no longer holds. `path` is relative to the install
    root (as the manifest names it), `file` relative to the flavor folder.
    `saved_at_ns` is the file's modification time at the snapshot: the
    client's last save of it. `snapshots_holding` counts every snapshot of
    this install that holds a copy (any save)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    flavor_folder: str
    account: str
    realm_folder: str
    character_folder: str
    shape: Literal["realm_name", "numeric_folder"]
    path: str
    file: str
    snapshot_id: str
    created_at: str  # UTC, ISO 8601, as the manifest records it
    snapshot_version: str | None  # the client version the snapshot recorded
    saved_at_ns: int
    kind: Literal["file", "symlink"]
    sha256: str | None
    size: int
    snapshots_holding: int


def _key(path: str) -> tuple[str, str, str, str] | None:
    """(flavor, account, realm folder, character folder) when `path` is a
    character's lab file, else None."""
    parts = path.split("/")
    if len(parts) != _PARTS:
        return None
    flavor, wtf, account_dir, account, realm, character, sv, name = parts
    if (
        wtf.casefold() != _WTF
        or account_dir.casefold() != _ACCOUNT
        or sv.casefold() != _SAVED_VARIABLES
        or not labaddon.is_lab_file_name(name)
    ):
        return None
    return flavor, account, realm, character


def in_install(root: Path, path: str) -> bool:
    """Whether the character folder of the lab file at `path` (relative to
    `root`, as a manifest names it) exists in the install. Only `lstat`:
    nothing is opened or followed. A refused check counts as present, and
    so does a path with a part that could name another place on Windows
    (a backslash or a colon, which a drive letter needs): it is never
    looked up."""
    parts = path.split("/")[:-2]
    if any("\\" in part or ":" in part for part in parts):
        return True
    folder = root.joinpath(*parts)
    try:
        longpath.lstat(folder)
    except (FileNotFoundError, NotADirectoryError):
        return False
    except OSError:
        return True
    return True


def counts(manifest: snapshot.Manifest, root: Path, imported_ids: Collection[str] = ()) -> bool:
    """Whether `manifest` is a snapshot of the install at `root` that
    `snap import` did not add: the snapshots `find` reads."""
    return manifest.id not in imported_ids and firstrun._same_root(manifest.install_root, root)


def find(
    manifests: Sequence[snapshot.Manifest],
    root: Path,
    imported_ids: Collection[str] = (),
    *,
    present: Callable[[str], bool] | None = None,
) -> list[KeptCopy]:
    """Each character held by a snapshot of the install at `root` among
    `manifests` and not in that install, newest save first. `present` is
    given a lab file's path (relative to `root`) and says whether its
    character is in the install (`in_install` when None)."""
    is_present = present if present is not None else (lambda p: in_install(root, p))
    found: dict[tuple[str, str, str, str], list[tuple[snapshot.Manifest, snapshot.Entry]]] = {}
    for manifest in manifests:
        if not counts(manifest, root, imported_ids):
            continue
        mine: dict[tuple[str, str, str, str], dict[str, snapshot.Entry]] = {}
        for entry in manifest.entries:
            key = _key(entry.path)
            if key is not None:
                mine.setdefault(key, {})[entry.path] = entry
        for key, by_path in mine.items():
            try:
                chosen = labaddon.choose_lab_file(list(by_path))
            except labaddon.LabAddonError:
                continue  # which copy the client read is not known
            if chosen is not None:
                found.setdefault(key, []).append((manifest, by_path[chosen]))
    kept: list[KeptCopy] = []
    for (flavor, account, realm, character), copies in found.items():
        manifest, entry = max(copies, key=lambda c: (c[1].mtime_ns, c[0].created_at, c[0].id))
        if is_present(entry.path):
            continue
        numeric = realm.isascii() and realm.isdigit()
        kept.append(
            KeptCopy(
                flavor_folder=flavor,
                account=account,
                realm_folder=realm,
                character_folder=character,
                shape="numeric_folder" if numeric else "realm_name",
                path=entry.path,
                file=entry.path.split("/", 1)[1],
                snapshot_id=manifest.id,
                created_at=manifest.created_at,
                snapshot_version=manifest.flavor_version,
                saved_at_ns=entry.mtime_ns,
                kind=entry.kind,
                sha256=entry.sha256,
                size=entry.size,
                snapshots_holding=len(copies),
            )
        )
    kept.sort(
        key=lambda k: (
            -k.saved_at_ns,
            k.flavor_folder,
            k.account,
            k.realm_folder,
            k.character_folder,
        )
    )
    return kept


def read_copy(store: snapshot.SnapshotStore, copy: KeptCopy) -> labaddon.CharDB | str:
    """The record `copy` holds, or why it cannot be read, in the words
    `char history` uses for an unreadable copy."""
    if copy.kind != "file" or copy.sha256 is None:
        return "the snapshot holds a link at this path, not the file"
    if copy.size > luadata.MAX_FILE_BYTES:
        return (
            f"the copy holds {copy.size} bytes, more than {luadata.MAX_FILE_BYTES}, "
            "so it was not read"
        )
    try:
        data = store.read_object(copy.sha256, size=copy.size)
    except snapshot.ObjectCorruptError as exc:
        return f"its stored copy cannot be used: {exc}"
    except OSError as exc:
        return f"it cannot be read: {exc.strerror or exc}"
    return charhistory.parse_words(data)
