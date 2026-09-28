"""Profiles: named sets of the client's local UI files (docs/LAB_PLAN.md §13.3, M11-08).

A profile is a labelled snapshot restricted to its subtrees, taken by
`snapshot` and put back by `guard`. This module reads the install and the
store; it writes nothing itself. `save` asks `snapshot` to store the
snapshot, `delete` asks it to relabel one, and `apply` hands every change to
`guard.transaction` (L1, L2, ADR-0021).

Which files a preset holds is data, in `profile_presets.toml` next to this
module; which accounts and characters exist is found by `layout` when the
profile is saved (L6). Action-bar contents and talents are not in these
files, so no profile holds them.

Identity: a profile is a snapshot taken with `purpose="profile"` whose label
is `profile:<name>`, optionally followed by ` presets=<p>,<p>`. Names are
unique in the store. `delete` relabels the snapshot `deleted-profile:<name>`:
snapshots are immutable and the relabel is the one change `snapshot` allows,
so the snapshot stays in the store (`wowlab snap list` shows it).

`apply` (owner decisions 2026-09-28, §13.3): it returns each of the
profile's subtrees to the saved bytes. A file the profile holds is written
back when it differs, and a regular file found under one of its subtrees
that it does not hold (added since the save) is deleted. A preset profile's
subtrees are the files and folders it joined to every account and character
folder found at save time, so a character folder created later is not
touched; an explicit `--subtree` is a whole folder, and files added anywhere
under it are deleted, including every file in a character folder created
under it since. Left alone, and reported: file-map Edit `no` files (as a
whole `snap restore` leaves them), the lab-addon (`[exclude]` in the data
file: never saved, restored or deleted by a profile), anything that is not a
regular file, anything the gate will not write or delete (an executable),
and an entry behind a symlinked or junctioned folder, which is never read. Every change is in the plan and `wowlab undo` reverses the
whole apply from the gate's pre-write snapshot. Folders emptied by a
deletion stay (the gate deletes files only).
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
import re
import stat
import tomllib
from collections.abc import Iterable, Mapping, Sequence
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from wowlab_core import guard, layout
from wowlab_core.snapshot import Entry, InvalidManifest, Manifest, SnapshotStore

__all__ = [
    "BEHIND_LINK_REASON",
    "DELETED_PREFIX",
    "LABEL_PREFIX",
    "LAB_ADDON_REASON",
    "LOGIN_NOTE",
    "MACROS_NOTE",
    "MAX_DELETES",
    "PRESETS_FILE",
    "PRESET_SCOPE_NOTE",
    "RESERVED_LABEL_PREFIXES",
    "SERVER_DELETE_NOTE",
    "SERVER_NOTE",
    "SERVER_SIDE_NOTE",
    "SUBTREE_SCOPE_NOTE",
    "ApplyPlan",
    "CacheFile",
    "Exclude",
    "LeftPath",
    "Preset",
    "PresetData",
    "Profile",
    "ProfileChangedError",
    "ProfileError",
    "ProfileExistsError",
    "ProfileListing",
    "ProfileNotFoundError",
    "Selection",
    "SkippedPath",
    "always_excluded",
    "apply",
    "check_name",
    "delete",
    "find",
    "is_always_excluded",
    "label_for",
    "listing",
    "parse_label",
    "parse_presets",
    "plan_apply",
    "presets",
    "save",
    "select",
]

PRESETS_FILE = "profile_presets.toml"
LABEL_PREFIX = "profile:"
DELETED_PREFIX = "deleted-profile:"
RESERVED_LABEL_PREFIXES = (LABEL_PREFIX, DELETED_PREFIX)
"""Labels only `profiles` writes; `wowlab snap create -m` refuses them."""
_PRESETS_TAG = " presets="
_NAME_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_CACHE_GLOB = "*-cache*"
_MACROS_FILE = "macros-cache.txt"
_TOO_BROAD = frozenset({"wtf", "wtf/account", "interface", "fonts"})

MAX_DELETES = 2000
"""The most files one apply may delete. The gate re-walks the install for
each delete, so a plan far beyond this costs minutes; above it the apply is
refused with the count (a guard listing cache is the follow-up)."""

SERVER_NOTE = (
    "the server may replace this at your next login (synchronize* CVars; see `wowlab doctor`)"
)
SERVER_DELETE_NOTE = (
    "the server may write this file again at your next login (synchronize* CVars; "
    "see `wowlab doctor`)"
)
LOGIN_NOTE = (
    "The result is proven only by starting the client and logging in: it reads Config.wtf "
    "at start and the other files at login, and the server may replace *-cache files at login."
)
SERVER_SIDE_NOTE = (
    "Action-bar contents and talents are not in these files (the server keeps them; not yet "
    "verified on this client), so no profile saves or restores them."
)
MACROS_NOTE = (
    "Action buttons are kept on the server and may point at a macro by its place in the list, "
    "so restoring macros can change what a button runs or leave it empty (not verified on this "
    "client); check your action bars after logging in."
)
PRESET_SCOPE_NOTE = (
    "A profile covers every account and every character folder that existed when it was "
    "saved, not only the character you play: files created in those places since the save "
    "are deleted. Character folders created since are not touched."
)
SUBTREE_SCOPE_NOTE = (
    "An explicit subtree: files added anywhere under it since the save are deleted, "
    "including every file in a character folder created under it since."
)
BEHIND_LINK_REASON = (
    "it sits behind a link (a folder on its path is a symlink or junction); wowlab never "
    "reads or writes through one"
)
LAB_ADDON_REASON = (
    "the lab-addon; a profile never saves, restores or deletes it (only `wowlab addon "
    "install|remove lab` changes it)"
)


class ProfileError(Exception):
    """Base class for every error this module raises (the CLI's exit 1)."""


class ProfileNotFoundError(ProfileError):
    """No profile, or more than one snapshot, carries the name."""


class ProfileExistsError(ProfileError):
    """`save` under a name a profile already has."""


class ProfileChangedError(ProfileError):
    """The files changed between the plan and the apply; the gate rolled back."""


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# ─── presets ─────────────────────────────────────────────────────────────────


class Exclude(_Frozen):
    """Paths never captured: relative to the flavor folder, to each account
    folder, and to each character folder."""

    flavor: tuple[str, ...] = ()
    account: tuple[str, ...] = ()
    character: tuple[str, ...] = ()


class Preset(_Frozen):
    """One preset as `profile_presets.toml` states it."""

    name: str
    summary: str
    flavor: tuple[str, ...] = ()
    """Paths relative to the flavor folder."""
    account: tuple[str, ...] = ()
    """Names inside each `WTF/Account/<ACCOUNT>/`."""
    character: tuple[str, ...] = ()
    """Names inside each character folder, of either shape."""
    exclude: Exclude = Field(default_factory=Exclude)


class PresetData(_Frozen):
    presets: dict[str, Preset]
    exclude: Exclude
    """Left out of every profile, presets and `--subtree` alike."""


def _check_relative(value: str, where: str) -> None:
    if (
        not value
        or value.startswith("/")
        or "\\" in value
        or any(p in ("", ".", "..") for p in value.split("/"))
    ):
        raise ValueError(f"{where}: {value!r} is not a plain relative path")


def _exclude_paths(exclude: Exclude) -> tuple[str, ...]:
    return (*exclude.flavor, *exclude.account, *exclude.character)


def parse_presets(text: str) -> PresetData:
    """Presets and the global exclusions from TOML text; every path is held
    to a plain relative path."""
    data = tomllib.loads(text)
    table = data.get("presets")
    if not isinstance(table, dict) or not table:
        raise ValueError("no [presets.<name>] tables")
    found: dict[str, Preset] = {}
    for name, body in table.items():
        if not isinstance(body, dict):
            raise ValueError(f"preset {name!r} is not a table")
        try:
            preset = Preset(name=name, **body)
        except ValidationError as exc:
            raise ValueError(f"preset {name!r}: {exc}") from exc
        for value in (
            *preset.flavor,
            *preset.account,
            *preset.character,
            *_exclude_paths(preset.exclude),
        ):
            _check_relative(value, f"preset {name!r}")
        found[name] = preset
    try:
        exclude = Exclude(**data.get("exclude", {}))
    except (TypeError, ValidationError) as exc:
        raise ValueError(f"[exclude]: {exc}") from exc
    for value in _exclude_paths(exclude):
        _check_relative(value, "[exclude]")
    return PresetData(presets=found, exclude=exclude)


@cache
def _data() -> PresetData:
    text = resources.files("wowlab_core").joinpath(PRESETS_FILE).read_text(encoding="utf-8")
    return parse_presets(text)


def presets() -> Mapping[str, Preset]:
    """The packaged presets (`profile_presets.toml`), parsed once per process."""
    return _data().presets


def always_excluded() -> Exclude:
    """What every profile leaves out: the lab-addon's code and its SavedVariables."""
    return _data().exclude


def _is_under(rel: str, ancestor: str) -> bool:
    return rel == ancestor or rel.startswith(ancestor + "/")


def is_always_excluded(rel: str) -> bool:
    """True when a flavor-relative path is one no profile ever touches: under
    an `[exclude].flavor` path, or under `WTF/Account/…` and ending in an
    `[exclude].account` or `.character` path (compared with case folded)."""
    folded = rel.casefold()
    exclude = always_excluded()
    if any(_is_under(folded, p.casefold()) for p in exclude.flavor):
        return True
    parts = folded.split("/")
    if len(parts) < 4 or parts[0] != "wtf" or parts[1] != "account":
        return False
    return any(folded.endswith("/" + p.casefold()) for p in (*exclude.account, *exclude.character))


# ─── selection ───────────────────────────────────────────────────────────────


class Selection(_Frozen):
    """What a profile captures, relative to the flavor folder."""

    subtrees: tuple[str, ...]
    excluded: tuple[str, ...]


class _Speller:
    """Spell a flavor-relative path as the disk does, one listing per folder.

    Each part is matched exactly, else by case-folded name when exactly one
    entry matches; a part below a folder that does not exist keeps the
    spelling given. Lists folders only; opens no file."""

    def __init__(self, flavor_path: Path) -> None:
        self._base = flavor_path
        self._listings: dict[Path, tuple[str, ...] | None] = {}

    def _names(self, folder: Path) -> tuple[str, ...] | None:
        if folder not in self._listings:
            try:
                self._listings[folder] = tuple(p.name for p in folder.iterdir())
            except OSError:
                self._listings[folder] = None
        return self._listings[folder]

    def __call__(self, rel: str) -> str:
        parts = rel.split("/")
        out: list[str] = []
        folder: Path | None = self._base
        for part in parts:
            names = None if folder is None else self._names(folder)
            chosen = part
            if names is not None and part not in names:
                folded = [n for n in names if n.casefold() == part.casefold()]
                if len(folded) == 1:
                    chosen = folded[0]
            out.append(chosen)
            folder = None if folder is None or names is None else folder / chosen
        return "/".join(out)


def _excluded_paths(
    spell: _Speller, accounts: Sequence[layout.Account], exclude: Exclude
) -> set[str]:
    found = {spell(p) for p in exclude.flavor}
    for acct in accounts:
        found.update(spell(f"{acct.path}/{n}") for n in exclude.account)
        for realm in acct.realms:
            for char in realm.characters:
                found.update(spell(f"{char.path}/{n}") for n in exclude.character)
    return found


def select(
    lay: layout.Layout, *, preset_names: Sequence[str] = (), subtrees: Sequence[str] = ()
) -> Selection:
    """Resolve presets (by name) and explicit subtrees against the install.

    A preset's account and character names are joined to every account and
    character folder `layout` finds now, present or not, so a file created
    there later is one added since the save. An explicit subtree is relative
    to the flavor folder, must lie under one of the snapshot subtrees
    (`WTF/`, `Interface/`, `Fonts/`) and be narrower than `WTF/Account/`
    (whole areas are `snap restore`'s job). The lab-addon is excluded from
    every selection. Reads directory listings only.
    """
    known = presets()
    unknown = [p for p in preset_names if p not in known]
    if unknown:
        raise ProfileError(f"no preset {unknown[0]!r} (presets: {', '.join(sorted(known))})")
    spell = _Speller(lay.flavor_path)
    wanted: set[str] = set()
    accounts = lay.accounts()
    excluded = _excluded_paths(spell, accounts, always_excluded())
    for name in dict.fromkeys(preset_names):
        preset = known[name]
        wanted.update(spell(p) for p in preset.flavor)
        excluded.update(_excluded_paths(spell, accounts, preset.exclude))
        for acct in accounts:
            wanted.update(spell(f"{acct.path}/{n}") for n in preset.account)
            for realm in acct.realms:
                for char in realm.characters:
                    wanted.update(spell(f"{char.path}/{n}") for n in preset.character)
    roots = {r.casefold() for r in lay.snapshot_subtrees()}
    for raw in subtrees:
        text = raw.replace("\\", "/").strip("/")
        try:
            _check_relative(text, "--subtree")
        except ValueError as exc:
            raise ProfileError(str(exc)) from exc
        if text.split("/", 1)[0].casefold() not in roots:
            raise ProfileError(
                f"--subtree {raw!r} is outside {', '.join(sorted(lay.snapshot_subtrees()))}"
            )
        if text.casefold() in _TOO_BROAD:
            raise ProfileError(
                f"--subtree {raw!r} is broader than one account folder; a profile is for "
                "named sets of files. For a whole area use `wowlab snap create` and "
                "`wowlab snap restore`"
            )
        if is_always_excluded(text):
            raise ProfileError(f"--subtree {raw!r} is {LAB_ADDON_REASON}")
        wanted.add(spell(text))
    if not wanted:
        raise ProfileError("a profile needs at least one preset or subtree")
    return Selection(subtrees=tuple(sorted(wanted)), excluded=tuple(sorted(excluded)))


# ─── identity ────────────────────────────────────────────────────────────────


def check_name(name: str) -> str:
    """A profile name: a letter or digit, then letters, digits, `.`, `_`, `-`; 64 at most."""
    if not _NAME_RE.match(name):
        raise ProfileError(
            f"{name!r} is not a profile name (a letter or digit, then letters, digits, "
            "'.', '_' or '-'; 64 characters at most)"
        )
    return name


def label_for(name: str, preset_names: Sequence[str] = ()) -> str:
    label = LABEL_PREFIX + check_name(name)
    if preset_names:
        label += _PRESETS_TAG + ",".join(dict.fromkeys(preset_names))
    return label


def parse_label(label: str) -> tuple[str, tuple[str, ...]] | None:
    """`(name, presets)` for a profile label, else None."""
    if not label.startswith(LABEL_PREFIX):
        return None
    rest = label.removeprefix(LABEL_PREFIX)
    name, sep, tail = rest.partition(" ")
    if not _NAME_RE.match(name):
        return None
    named: tuple[str, ...] = ()
    if sep:
        tag = _PRESETS_TAG.lstrip()
        if not tail.startswith(tag):
            return None
        named = tuple(p for p in tail.removeprefix(tag).split(",") if p)
    return name, named


class Profile(_Frozen):
    name: str
    presets: tuple[str, ...]
    """Empty for a profile saved with `--subtree`."""
    manifest: Manifest


class ProfileListing(_Frozen):
    """Every profile in the store, oldest first, and every manifest that did not load."""

    profiles: tuple[Profile, ...]
    invalid: tuple[InvalidManifest, ...]


def listing(store: SnapshotStore) -> ProfileListing:
    """Reads only; a store that does not exist has no profiles. A snapshot is
    a profile only when `save` made it: its manifest carries
    `purpose="profile"` and a profile label."""
    found = store.list_lenient()
    profiles: list[Profile] = []
    for m in found.manifests:
        parsed = parse_label(m.label)
        if parsed is not None and m.purpose == "profile":
            profiles.append(Profile(name=parsed[0], presets=parsed[1], manifest=m))
    return ProfileListing(profiles=tuple(profiles), invalid=found.invalid)


def _named(store: SnapshotStore, name: str) -> list[Profile]:
    matches = [p for p in listing(store).profiles if p.name == name]
    if not matches:
        raise ProfileNotFoundError(f"no profile named {name!r}")
    return matches


def find(store: SnapshotStore, name: str) -> Profile:
    """The one profile named `name`; none, or more than one, is `ProfileNotFoundError`."""
    matches = _named(store, name)
    if len(matches) > 1:
        ids = ", ".join(p.manifest.id for p in matches)
        raise ProfileNotFoundError(
            f"{len(matches)} snapshots are labelled as profile {name!r} ({ids}); "
            "`wowlab profile delete` relabels them all; then save the profile again"
        )
    return matches[0]


class _FlavorLike(Protocol):
    @property
    def folder(self) -> str: ...
    @property
    def path(self) -> Path: ...
    @property
    def version(self) -> str | None: ...


def save(
    store: SnapshotStore,
    install_root: Path,
    flavor: _FlavorLike,
    name: str,
    selection: Selection,
    *,
    preset_names: Sequence[str] = (),
    client_running: bool | None = None,
) -> Profile:
    """Snapshot the selection with `purpose="profile"` under the label
    `profile:<name>`.

    Refused when a profile of that name exists. Holds the store lock for
    the check and the create, as `wowlab snap create` does; reads the
    install only."""
    label = label_for(name, preset_names)
    root = Path(install_root)
    store.refuse_holding(root)
    with guard.store_lock(store.path, create=True):
        existing = [p for p in listing(store).profiles if p.name == name]
        if existing:
            raise ProfileExistsError(
                f"a profile named {name!r} exists (snapshot {existing[0].manifest.id}); "
                "delete it first or choose another name"
            )
        manifest = store.create(
            root,
            [f"{flavor.folder}/{s}" for s in selection.subtrees],
            exclude=[f"{flavor.folder}/{x}" for x in selection.excluded],
            label=label,
            flavor_folder=flavor.folder,
            flavor_version=flavor.version,
            client_running=client_running,
            purpose="profile",
        )
    return Profile(name=name, presets=tuple(dict.fromkeys(preset_names)), manifest=manifest)


def delete(store: SnapshotStore, name: str) -> tuple[Manifest, ...]:
    """Relabel the profile's snapshot `deleted-profile:<name>`; the snapshot stays.

    Every profile snapshot named `name` is relabelled: `save` never makes a
    second one, but a library caller could."""
    _named(store, name)  # a missing store or name is "not found", never a lock error
    with guard.store_lock(store.path):
        return tuple(
            store.set_label(
                p.manifest.id, DELETED_PREFIX + p.manifest.label.removeprefix(LABEL_PREFIX)
            )
            for p in _named(store, name)
        )


# ─── apply ───────────────────────────────────────────────────────────────────


class SkippedPath(_Frozen):
    """A file the apply leaves alone because its file-map Edit is `no`."""

    path: str  # relative to the flavor folder
    action: Literal["write", "delete"]  # what the apply would otherwise have done
    entry_id: str
    what: str


class LeftPath(_Frozen):
    """A path the apply leaves alone for another reason, and the reason: the
    lab-addon, not a regular file, or refused by the gate (an executable)."""

    path: str  # relative to the flavor folder
    reason: str


class CacheFile(_Frozen):
    """A `*-cache*` file the apply writes or deletes, with its §13.3 note."""

    path: str
    action: Literal["write", "delete"]
    note: str


class ApplyPlan(_Frozen):
    profile: str
    snapshot_id: str
    flavor_path: str
    plan: tuple[guard.PlanItem, ...]
    """What the gate will change, in order: writes, then deletes."""
    added: tuple[str, ...]
    """The deletes in `plan`: files added under the profile's subtrees since the save."""
    skipped: tuple[SkippedPath, ...]
    left: tuple[LeftPath, ...]
    cache_files: tuple[CacheFile, ...]
    notes: tuple[str, ...]
    """The scope of the profile, and the macros caveat when macros change."""


def _plain(markdown: str) -> str:
    return markdown.replace("`", "").replace("**", "")


def _base_name(path: str) -> str:
    return path.rsplit("/", 1)[-1].casefold()


def _edit_no(lay: layout.Layout, rel: str) -> layout.Classified | None:
    found = lay.classify(rel, is_dir=False)
    if isinstance(found, layout.Classified) and found.entry.edit == "no":
        return found
    return None


_Disk = Literal["same", "differs", "behind_link"]


def _is_link(path: Path, st: os.stat_result) -> bool:
    """A symlink, or on Windows a junction or other directory link."""
    if stat.S_ISLNK(st.st_mode):
        return True
    try:
        return stat.S_ISDIR(st.st_mode) and path.is_junction()
    except OSError:
        return True


def _differs(root: Path, flavor_folder: str, entry: Entry) -> _Disk:
    """Whether the disk still holds `entry`: `same`, `differs`, or
    `behind_link` when a folder between the flavor folder and the entry is a
    link (or a junction), in which case nothing is read there.

    Reads only, and never through a link: every parent from the flavor folder
    down is checked with `lstat`; a file is opened as `snapshot._capture`
    opens one (`O_RDONLY | O_NOFOLLOW | O_NONBLOCK` where the platform has
    them) and must be a regular file by `fstat`. A symlink entry is compared
    by its link text."""
    rel = entry.path.removeprefix(flavor_folder + "/").split("/")
    current = root / flavor_folder
    for part in rel[:-1]:
        current = current / part
        try:
            st = current.lstat()
        except FileNotFoundError:
            return "differs"  # the entry's folder is gone
        except OSError:
            return "differs"
        if _is_link(current, st):
            return "behind_link"
    path = current / rel[-1]
    try:
        st = path.lstat()
    except OSError:
        return "differs"
    if entry.kind == "symlink":
        try:
            same = stat.S_ISLNK(st.st_mode) and str(path.readlink()) == entry.target
        except OSError:
            return "differs"
        return "same" if same else "differs"
    if not stat.S_ISREG(st.st_mode):
        return "differs"
    try:
        fd = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
        )
    except OSError:
        return "differs"
    digest = hashlib.sha256()
    try:
        with os.fdopen(fd, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                return "differs"
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
    except OSError:
        return "differs"
    return "same" if digest.hexdigest() == entry.sha256 else "differs"


class _Restorer(Protocol):
    def restore(self, snapshot_id: str, paths: Iterable[str] | None = None) -> None: ...


def _restore_split(tx: _Restorer, snapshot_id: str, paths: list[str], left: list[LeftPath]) -> None:
    """Restore `paths` in a dry run, setting aside each one the gate refuses.

    A refused restore changes nothing (the gate checks every entry before it
    plans any), so the batch is halved until each refusal names one path."""
    if not paths:
        return
    try:
        tx.restore(snapshot_id, paths)
    except guard.PathNotAllowedError as exc:
        if len(paths) == 1:
            left.append(LeftPath(path=paths[0], reason=str(exc)))
            return
        mid = len(paths) // 2
        _restore_split(tx, snapshot_id, paths[:mid], left)
        _restore_split(tx, snapshot_id, paths[mid:], left)


def plan_apply(
    profile: Profile, flavor: _FlavorLike, install_root: Path, *, store: SnapshotStore
) -> ApplyPlan:
    """What `apply` would change, from a dry run of the gate (same checks,
    same locks, nothing written in the install).

    Raises `guard.GuardError` when the gate refuses the apply as a whole
    (the client running or unknown, a snapshot of another install), and
    `ProfileError` when more than `MAX_DELETES` files would be deleted. A
    single path the gate will not write or delete (an executable) does not
    stop the apply: it is left alone and listed in `left`."""
    manifest = profile.manifest
    prefix = flavor.folder + "/"
    root = Path(install_root)
    lay = layout.Layout(flavor.path, install_root=root)
    saved = {e.path for e in manifest.entries}
    left: list[LeftPath] = []
    skipped: list[SkippedPath] = []
    to_delete: list[str] = []
    for rel, kind in store.list_tree(root, manifest.subtrees, exclude=manifest.excluded):
        if rel in saved or not rel.startswith(prefix):
            continue
        frel = rel.removeprefix(prefix)
        if is_always_excluded(frel):
            left.append(LeftPath(path=frel, reason=LAB_ADDON_REASON))
        elif kind != "file":
            left.append(LeftPath(path=frel, reason="not a regular file; wowlab never removes it"))
        elif (found := _edit_no(lay, frel)) is not None:
            skipped.append(
                SkippedPath(
                    path=frel,
                    action="delete",
                    entry_id=found.entry.id,
                    what=_plain(found.entry.what),
                )
            )
        else:
            to_delete.append(frel)
    if len(to_delete) > MAX_DELETES:
        raise ProfileError(
            f"applying profile {profile.name} would delete {len(to_delete)} files added since "
            f"it was saved, more than {MAX_DELETES}; nothing was changed. For a whole area use "
            "`wowlab snap restore`, or save a narrower profile"
        )
    with guard.transaction(
        flavor, label=f"profile apply {profile.name}", store=store.path, dry_run=True
    ) as tx:
        try:
            tx.restore(manifest.id)
        except guard.PathNotAllowedError:
            # One entry the gate will not write (a changed executable or link):
            # restore the rest by name and set that one aside. An entry behind
            # a link is not read and not named; it is left alone.
            changed: list[str] = []
            for e in manifest.entries:
                if not e.path.startswith(prefix):
                    continue
                disk = _differs(root, flavor.folder, e)
                if disk == "behind_link":
                    left.append(
                        LeftPath(path=e.path.removeprefix(prefix), reason=BEHIND_LINK_REASON)
                    )
                elif disk == "differs":
                    changed.append(e.path.removeprefix(prefix))
            _restore_split(tx, manifest.id, changed, left)
        for frel in to_delete:
            try:
                tx.delete(frel)
            except guard.PathNotAllowedError as exc:
                left.append(LeftPath(path=frel, reason=str(exc)))
        whole = tx.plan
    kept: list[guard.PlanItem] = []
    for item in whole:
        if is_always_excluded(item.path):
            left.append(LeftPath(path=item.path, reason=LAB_ADDON_REASON))
        elif item.after is not None and (found := _edit_no(lay, item.path)) is not None:
            skipped.append(
                SkippedPath(
                    path=item.path,
                    action="write",
                    entry_id=found.entry.id,
                    what=_plain(found.entry.what),
                )
            )
        else:
            kept.append(item)
    notes = [SUBTREE_SCOPE_NOTE if not profile.presets else PRESET_SCOPE_NOTE]
    if any(_base_name(i.path) == _MACROS_FILE for i in kept):
        notes.append(MACROS_NOTE)
    return ApplyPlan(
        profile=profile.name,
        snapshot_id=manifest.id,
        flavor_path=str(flavor.path),
        plan=tuple(kept),
        added=tuple(i.path for i in kept if i.after is None),
        skipped=tuple(skipped),
        left=tuple(left),
        cache_files=tuple(
            CacheFile(
                path=i.path,
                action="delete" if i.after is None else "write",
                note=SERVER_DELETE_NOTE if i.after is None else SERVER_NOTE,
            )
            for i in kept
            if fnmatch.fnmatchcase(_base_name(i.path), _CACHE_GLOB)
        ),
        notes=tuple(notes),
    )


def apply(plan: ApplyPlan, flavor: _FlavorLike, *, store: SnapshotStore) -> str:
    """Make exactly the changes `plan` lists, in one gate transaction, and
    return its journal record id (`wowlab undo` reverses it).

    If the gate's own plan differs from `plan` (a file changed after the
    plan was made), `ProfileChangedError` is raised inside the transaction,
    so the gate rolls it back."""
    writes = [i.path for i in plan.plan if i.after is not None]
    deletes = [i.path for i in plan.plan if i.after is None]
    with guard.transaction(flavor, label=f"profile apply {plan.profile}", store=store.path) as tx:
        if writes:
            tx.restore(plan.snapshot_id, writes)
        for rel in deletes:
            tx.delete(rel)
        if tx.plan != plan.plan:
            raise ProfileChangedError(
                "the files changed after the plan was made, so the apply was rolled back; "
                "run the command again to see the new plan"
            )
    return guard.history(store=store.path)[-1].id
