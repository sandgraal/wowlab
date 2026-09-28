"""Profiles: named sets of the client's local UI files (docs/LAB_PLAN.md §13.3, M11-08).

A profile is a labelled snapshot restricted to its subtrees, taken by
`snapshot` and put back by `guard`. This module reads the install and the
store; it writes nothing itself. `save` asks `snapshot` to store the
snapshot, `delete` asks it to relabel one, and `apply` hands every change to
`guard.transaction` (L1, L2, ADR-0021).

Which files a preset holds is data, in `profile_presets.toml` next to this
module; which accounts and characters exist is found by `layout` when the
profile is saved (L6). Action-bar contents and talents are kept on the
server and are in no profile.

Identity: a profile is the snapshot whose label is `profile:<name>`,
optionally followed by ` presets=<p>,<p>` (informational). Names are unique
in the store. `delete` relabels the snapshot `deleted-profile:<name>`:
snapshots are immutable and the relabel is the one change `snapshot`
allows, so the snapshot stays in the store (`wowlab snap list` shows it).

`apply` (decided 2026-09-28, §13.3): it returns each of the profile's
subtrees to the saved bytes. A file the profile holds is written back when
it differs, and a regular file found under one of the profile's subtrees
that the profile does not hold (a file added since the profile was saved)
is deleted. A subtree is one the profile recorded, so a character folder
created after the save is never touched. Files whose file-map Edit is `no`
are left alone either way, as a whole `snap restore` leaves them, and so is
anything that is not a regular file and anything the gate would not delete
(an executable); both are reported. Every deletion is in the plan, and
`wowlab undo` reverses the whole apply from the gate's pre-write snapshot.
Folders left empty by a deletion stay (the gate deletes files only).
"""

from __future__ import annotations

import fnmatch
import re
import tomllib
from collections.abc import Mapping, Sequence
from functools import cache
from importlib import resources
from pathlib import Path, PurePosixPath
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from wowlab_core import guard, layout
from wowlab_core.snapshot import InvalidManifest, Manifest, SnapshotStore

__all__ = [
    "DELETED_PREFIX",
    "LABEL_PREFIX",
    "LOGIN_NOTE",
    "PRESETS_FILE",
    "SERVER_NOTE",
    "SERVER_SIDE_NOTE",
    "ApplyPlan",
    "CacheFile",
    "LeftPath",
    "Preset",
    "Profile",
    "ProfileChangedError",
    "ProfileError",
    "ProfileExistsError",
    "ProfileListing",
    "ProfileNotFoundError",
    "Selection",
    "SkippedPath",
    "apply",
    "check_name",
    "delete",
    "find",
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
_PRESETS_TAG = " presets="
_NAME_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_CACHE_GLOB = "*-cache*"

SERVER_NOTE = (
    "the server may replace this at your next login (synchronize* CVars; see `wowlab doctor`)"
)
LOGIN_NOTE = (
    "The result is proven only by logging in: the client reads these files at login, "
    "and the server may replace *-cache files then."
)
SERVER_SIDE_NOTE = (
    "Action-bar contents and talents are kept on the server and are never in a profile."
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


class _Exclude(_Frozen):
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
    exclude: _Exclude = Field(default_factory=_Exclude)


def _check_relative(value: str, where: str) -> None:
    parts = PurePosixPath(value).parts
    if (
        not value
        or value.startswith("/")
        or "\\" in value
        or any(p in ("", ".", "..") for p in value.split("/"))
        or not parts
    ):
        raise ValueError(f"{where}: {value!r} is not a plain relative path")


def parse_presets(text: str) -> dict[str, Preset]:
    """Presets from TOML text; every path is held to a plain relative path."""
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
            *preset.exclude.account,
            *preset.exclude.character,
        ):
            _check_relative(value, f"preset {name!r}")
        found[name] = preset
    return found


@cache
def presets() -> Mapping[str, Preset]:
    """The packaged presets (`profile_presets.toml`), parsed once per process."""
    text = resources.files("wowlab_core").joinpath(PRESETS_FILE).read_text(encoding="utf-8")
    return parse_presets(text)


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


def select(
    lay: layout.Layout, *, preset_names: Sequence[str] = (), subtrees: Sequence[str] = ()
) -> Selection:
    """Resolve presets (by name) and explicit subtrees against the install.

    A preset's account and character names are joined to every account and
    character folder `layout` finds now, present or not, so a file created
    there later is one added since the save. An explicit subtree is relative
    to the flavor folder and must lie under one of the snapshot subtrees
    (`WTF/`, `Interface/`, `Fonts/`). Reads directory listings only.
    """
    known = presets()
    unknown = [p for p in preset_names if p not in known]
    if unknown:
        raise ProfileError(f"no preset {unknown[0]!r} (presets: {', '.join(sorted(known))})")
    spell = _Speller(lay.flavor_path)
    wanted: set[str] = set()
    excluded: set[str] = set()
    accounts = lay.accounts() if preset_names else ()
    for name in dict.fromkeys(preset_names):
        preset = known[name]
        wanted.update(spell(p) for p in preset.flavor)
        for acct in accounts:
            wanted.update(spell(f"{acct.path}/{n}") for n in preset.account)
            excluded.update(spell(f"{acct.path}/{n}") for n in preset.exclude.account)
            for realm in acct.realms:
                for char in realm.characters:
                    wanted.update(spell(f"{char.path}/{n}") for n in preset.character)
                    excluded.update(spell(f"{char.path}/{n}") for n in preset.exclude.character)
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
    manifest: Manifest


class ProfileListing(_Frozen):
    """Every profile in the store, oldest first, and every manifest that did not load."""

    profiles: tuple[Profile, ...]
    invalid: tuple[InvalidManifest, ...]


def listing(store: SnapshotStore) -> ProfileListing:
    """Reads only; a store that does not exist has no profiles."""
    found = store.list_lenient()
    profiles: list[Profile] = []
    for m in found.manifests:
        parsed = parse_label(m.label)
        if parsed is not None:
            profiles.append(Profile(name=parsed[0], presets=parsed[1], manifest=m))
    return ProfileListing(profiles=tuple(profiles), invalid=found.invalid)


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
    """Snapshot the selection under the label `profile:<name>`.

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
        )
    return Profile(name=name, presets=tuple(dict.fromkeys(preset_names)), manifest=manifest)


def _named(store: SnapshotStore, name: str) -> list[Profile]:
    matches = [p for p in listing(store).profiles if p.name == name]
    if not matches:
        raise ProfileNotFoundError(f"no profile named {name!r}")
    return matches


def delete(store: SnapshotStore, name: str) -> tuple[Manifest, ...]:
    """Relabel the profile's snapshot `deleted-profile:<name>`; the snapshot stays.

    Every snapshot labelled as profile `name` is relabelled: `save` never
    makes a second one, but a `wowlab snap create -m "profile:<name>"` can."""
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
    """Something added since the save that the apply cannot delete."""

    path: str  # relative to the flavor folder
    reason: str


class CacheFile(_Frozen):
    """A `*-cache*` file the apply writes or deletes, with the §13.3 note."""

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


def _plain(markdown: str) -> str:
    return markdown.replace("`", "").replace("**", "")


def _is_cache_file(path: str) -> bool:
    return fnmatch.fnmatchcase(path.rsplit("/", 1)[-1].casefold(), _CACHE_GLOB)


def plan_apply(
    profile: Profile, flavor: _FlavorLike, install_root: Path, *, store: SnapshotStore
) -> ApplyPlan:
    """What `apply` would change, from a dry run of the gate (same checks,
    same locks, nothing written in the install). Raises `guard.GuardError`
    when the gate refuses: the client running or unknown, a snapshot of
    another install, a path outside the allowlist."""
    manifest = profile.manifest
    prefix = flavor.folder + "/"
    root = Path(install_root)
    saved = {e.path for e in manifest.entries}
    extra = [
        (rel, kind)
        for rel, kind in store.list_tree(root, manifest.subtrees, exclude=manifest.excluded)
        if rel not in saved and rel.startswith(prefix)
    ]
    left: list[LeftPath] = [
        LeftPath(
            path=rel.removeprefix(prefix), reason="not a regular file; wowlab never removes it"
        )
        for rel, kind in extra
        if kind != "file"
    ]
    with guard.transaction(
        flavor, label=f"profile apply {profile.name}", store=store.path, dry_run=True
    ) as tx:
        tx.restore(manifest.id)
        for rel, kind in extra:
            if kind != "file":
                continue
            try:
                tx.delete(rel.removeprefix(prefix))
            except guard.PathNotAllowedError as exc:
                left.append(LeftPath(path=rel.removeprefix(prefix), reason=str(exc)))
        whole = tx.plan
    lay = layout.Layout(flavor.path, install_root=root)
    kept: list[guard.PlanItem] = []
    skipped: list[SkippedPath] = []
    for item in whole:
        action: Literal["write", "delete"] = "delete" if item.after is None else "write"
        found = lay.classify(item.path, is_dir=False)
        if isinstance(found, layout.Classified) and found.entry.edit == "no":
            skipped.append(
                SkippedPath(
                    path=item.path,
                    action=action,
                    entry_id=found.entry.id,
                    what=_plain(found.entry.what),
                )
            )
        else:
            kept.append(item)
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
                path=i.path, action="delete" if i.after is None else "write", note=SERVER_NOTE
            )
            for i in kept
            if _is_cache_file(i.path)
        ),
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
