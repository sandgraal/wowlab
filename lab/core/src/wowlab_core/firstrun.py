"""firstrun: the first run of `wowlab doctor` (docs/LAB_PLAN.md §15.2, M13-04).

A player's first run is a run of `wowlab doctor` when the snapshot store holds
no snapshot of the install it found (or, with no install found, no snapshot
at all). On that run the command says in plain words what wowlab will and
will not do (the trust model of ADR-0031), looks at what a first snapshot of
each flavor would hold, offers that snapshot, takes it into the Lab's own
store when the player accepts, runs `snap verify` on the store, and ends with
the line that says how to undo.

Everything here reads the install and nothing writes into it (L1). The scan
walks a flavor's snapshot subtrees with `lstat` and `scandir` only: a link,
and on Windows a junction or other directory link, is reported with the text
of its target and never followed, exactly as `snapshot` records it. A sync
service (OneDrive, Dropbox, Syncthing) is recognised by heuristics on folder
names and marker files in the folders above WTF, and on the text of a link's
target; it is reported, never refused. The snapshot is taken by
`snapshot_flavor`, the one sequence `wowlab snap create` uses too: refuse a
store that holds the install, take the store lock (which refuses a store
inside any install before creating anything), then `SnapshotStore.create`.

The text is this module's; the command prints every line through
`cli._safe`, since paths and link targets come from the install. Nothing here
prompts: the command passes in how to ask, or nothing when it must not
(`--json`, or input that is not a terminal).
"""

from __future__ import annotations

import math
import os
import stat
from collections.abc import Callable, Sequence
from pathlib import Path, PurePath
from typing import Literal

from pydantic import BaseModel, ConfigDict

from wowlab_core import guard, install, layout, snapshot
from wowlab_core.snapshot import VerifyReport

__all__ = [
    "FILE_SECONDS",
    "LARGE_BYTES",
    "LARGE_SECONDS",
    "SNAPSHOT_LABEL",
    "THROUGHPUT_BYTES",
    "FirstRunReport",
    "FirstSnapshot",
    "FlavorScan",
    "LinkFound",
    "SubtreeScan",
    "SyncHint",
    "Unreadable",
    "estimate_seconds",
    "flavor_label",
    "intro_lines",
    "is_first_run",
    "pointing_lines",
    "run",
    "scan_flavor",
    "scan_lines",
    "snapshot_flavor",
    "sync_hints",
    "undo_line",
]

SNAPSHOT_LABEL = "first run"
"""The label of the snapshot a first run takes, as `snap list` shows it."""

# The estimate is deliberately on the slow side: it assumes 25 MiB/s for
# `snap create` plus `snap verify` together, plus a fixed cost per file, so a
# slower disk, an antivirus scan or a cloud-synced folder still tends to
# finish within it. It is a rule of thumb, not a measurement.
THROUGHPUT_BYTES = 25 * 1024 * 1024
"""Bytes per second the estimate assumes for hashing, compressing and storing."""
FILE_SECONDS = 0.001
"""Seconds the estimate adds per file (opening, `lstat`, the manifest entry)."""
LARGE_BYTES = 1024**3
"""A snapshot at least this big is called large, and the text says so."""
LARGE_SECONDS = 60
"""A snapshot estimated to take at least this long is called large too."""

_WTF = "WTF"

# Folder names a sync client gives the folder it syncs, compared with case
# folded: OneDrive ("OneDrive", "OneDrive - <organisation>", and on macOS
# "OneDrive-Personal" under ~/Library/CloudStorage) and Dropbox ("Dropbox",
# "Dropbox (Personal)", "Dropbox-<team>" under CloudStorage). Syncthing has
# no folder name; it leaves a `.stfolder` marker (a folder, or a file in old
# versions) in the folder it syncs. Dropbox leaves a `.dropbox` file at the
# top of its folder; its app also keeps a `.dropbox` *folder* of settings in
# the home folder, which says nothing about what is synced, so only a file
# counts.
SyncService = Literal["OneDrive", "Dropbox", "Syncthing"]
_NAME_PREFIXES: tuple[tuple[SyncService, tuple[str, ...]], ...] = (
    ("OneDrive", ("onedrive",)),
    ("Dropbox", ("dropbox",)),
)
_MARKERS: tuple[tuple[SyncService, str, bool], ...] = (
    # (service, entry name, only a regular file counts)
    ("Syncthing", ".stfolder", False),
    ("Dropbox", ".dropbox", True),
)
# The names sync clients give the second copy when two machines changed one
# file: Dropbox "<name> (<who>'s conflicted copy <date>)", Syncthing
# "<name>.sync-conflict-<date>-<time>-<device>". OneDrive appends the
# computer's name, which cannot be told from an ordinary name.
_CONFLICT_MARKS = ("conflicted copy", ".sync-conflict-")

# Windows attribute bits of a cloud file whose content is not on the disk
# (OFFLINE, RECALL_ON_OPEN, RECALL_ON_DATA_ACCESS), and the macOS flag of a
# "dataless" file (SF_DATALESS). Reading such a file makes the sync client
# download it, or fails when it cannot.
_WINDOWS_PLACEHOLDER = 0x1000 | 0x40000 | 0x400000
_MACOS_DATALESS = 0x40000000
# A folder a cloud client has not put on the disk: listing it (Windows
# RECALL_ON_OPEN, RECALL_ON_DATA_ACCESS; macOS SF_DATALESS) makes the client
# download it, so the scan does not descend into one.
_WINDOWS_ONLINE_ONLY_DIR = 0x40000 | 0x400000


class _Out(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SubtreeScan(_Out):
    """One snapshot subtree of a flavor (relative to the flavor folder)."""

    path: str
    present: bool
    link: bool  # the subtree itself is a link: recorded, never followed
    files: int
    bytes: int


class LinkFound(_Out):
    """A link a snapshot records and never follows."""

    path: str  # relative to the flavor folder
    kind: Literal["symlink", "junction"]  # junction: any Windows directory link
    target: str | None  # the link's own text; None when it cannot be read


class SyncHint(_Out):
    """Why wowlab thinks a sync service holds the WTF folder."""

    service: SyncService
    path: str  # the folder (or link, relative to the flavor folder) that gave it away
    evidence: str  # e.g. 'its name is "Dropbox"', 'it holds .stfolder'
    link_target: str | None = None  # set when the hint is a link's target text


class Unreadable(_Out):
    path: str  # relative to the flavor folder
    reason: str


class FlavorScan(_Out):
    """What a first snapshot of one flavor would hold, before it is taken."""

    folder: str
    product: str
    version: str | None
    subtrees: list[SubtreeScan]
    files: int
    bytes: int
    estimated_seconds: int
    large: bool
    links: list[LinkFound]
    sync: list[SyncHint]
    conflict_files: list[str]  # relative to the flavor folder
    placeholders: int  # files whose content a sync service has not put on the disk
    online_only_folders: list[str]  # folders a sync service keeps online-only; not listed
    unreadable: list[Unreadable]


class FirstSnapshot(_Out):
    id: str
    label: str
    flavor_folder: str
    subtrees: list[str]  # relative to the install root, as the manifest has them
    files: int
    links: int
    bytes: int


Outcome = Literal[
    "taken",  # the snapshot was taken and the store verified
    "failed",  # accepted, but the snapshot could not be taken or the store did not verify
    "declined",  # the player was asked and said no
    "not_asked",  # nobody could be asked (--json, or input is not a terminal) and no --yes
    "choose_flavor",  # --yes with several flavors and no --flavor
    "no_install",
    "no_flavors",
]


class FirstRunReport(_Out):
    """`wowlab doctor --json`, field `first_run`, on a first run."""

    store_path: str
    flavors: list[FlavorScan]
    chosen: str | None
    client_running: bool | None  # of the chosen flavor; None: cannot tell, or none chosen
    outcome: Outcome
    snapshot: FirstSnapshot | None
    verify: VerifyReport | None
    error: str | None
    refused: bool  # the error is the write gate's refusal (exit 3)
    not_snapshotted: list[str]  # the other flavor folders, still without a snapshot
    undo: str  # the last line of the text output


# ─── first run? ──────────────────────────────────────────────────────────────


def _same_root(recorded: str, root: Path) -> bool:
    candidate = Path(recorded)
    if candidate == root.absolute():
        return True
    try:
        return candidate.samefile(root)
    except (OSError, ValueError):
        return False


def is_first_run(store: snapshot.SnapshotStore, inst: install.Install | None) -> bool:
    """True when the store holds no snapshot of `inst` (with `inst` None: no
    snapshot at all). Any snapshot counts: one taken by `snap create`, by a
    profile save, or by the write gate before a change. Reads the store
    only, and creates nothing."""
    manifests = store.list_lenient().manifests
    if inst is None:
        return not manifests
    return not any(_same_root(m.install_root, Path(inst.root)) for m in manifests)


# ─── text ────────────────────────────────────────────────────────────────────


def intro_lines(*, offline: bool, store_path: Path) -> list[str]:
    """What wowlab will and will not do, in plain words (ADR-0031's trust
    model). Printed before `doctor` makes its network request."""
    if offline:
        network = (
            "With --offline, as now, this check sends no request at all. Without it, it "
            "downloads wago.tools' public list of game versions and looks for your "
            "installed versions in it; nothing from your install is sent."
        )
    else:
        network = (
            "This check downloads wago.tools' public list of game versions and looks for "
            "your installed versions in it; nothing from your install is sent. Run it with "
            "--offline to skip that."
        )
    return [
        "First run: wowlab has no snapshot of this install yet. What it will and will not do:",
        "  - It reads your install and writes nothing there: no temporary, cache or lock "
        "file. If a sync service (OneDrive, Dropbox, iCloud Drive) keeps some of your "
        "files online-only, reading one makes the service download it.",
        "  - Only snap restore, undo, profile apply, sv merge, addon install and addon remove "
        "change files in your install. Each one shows what it will change and asks first "
        "(unless you pass --yes), refuses while the game is running, and takes a snapshot "
        "before it writes, so `wowlab undo` can put things back.",
        "  - It never touches the game's own data (Data/) or its programs, never reads or "
        "changes the game's memory, and never runs code from your files: SavedVariables "
        "are read as data.",
        f"  - Snapshots are kept in wowlab's own store, {store_path}, never in your install.",
        "  - Nothing is uploaded. The only network traffic is to wago.tools, a community-run "
        "public site of game data. " + network,
    ]


def pointing_lines() -> list[str]:
    """How to name the install when discovery did not find it."""
    return [
        "  To point wowlab at your install, name the folder that holds .build.info (the "
        "game's install folder; the flavor folders, named like _name_, are inside it). "
        "The Battle.net launcher shows it: the game's settings menu, Show in Finder or "
        "Show in Explorer. On a Mac, .build.info is a hidden file (Cmd-Shift-. shows "
        "hidden files in Finder).",
        '    for one command:  wowlab doctor --root "<that folder>"',
        "    for every command, set WOWLAB_WOW_ROOT to that folder:",
        '      macOS or Linux:  export WOWLAB_WOW_ROOT="<that folder>"',
        '      Windows PowerShell:  $env:WOWLAB_WOW_ROOT = "<that folder>"  (or setx '
        'WOWLAB_WOW_ROOT "<that folder>" to keep it, then open a new terminal)',
        "  --root wins over WOWLAB_WOW_ROOT. Without either, wowlab looks only where the game "
        "installs by default.",
    ]


def _size(n: int) -> str:
    """`n` bytes in decimal units, as file browsers on macOS show them."""
    if n < 1000:
        return f"{n} byte" + ("" if n == 1 else "s")
    value = float(n)
    for unit in ("kB", "MB", "GB", "TB"):
        value /= 1000
        if value < 1000 or unit == "TB":
            return f"{value:.1f} {unit}"
    raise AssertionError("unreachable")  # pragma: no cover


def _duration(seconds: int) -> str:
    if seconds < 60:
        return f"about {max(seconds, 1)} second" + ("" if seconds <= 1 else "s")
    minutes = math.ceil(seconds / 60)
    return f"about {minutes} minute" + ("" if minutes == 1 else "s")


def _count(n: int, one: str, many: str) -> str:
    return f"{n:,} {one if n == 1 else many}"


def _files(n: int) -> str:
    return _count(n, "file", "files")


def _listing(names: Sequence[str], limit: int = 10) -> str:
    shown = ", ".join(names[:limit])
    more = len(names) - limit
    return shown + (f", and {more} more" if more > 0 else "")


def _and(names: Sequence[str]) -> str:
    return ", ".join(names[:-1]) + " and " + names[-1] if len(names) > 1 else "".join(names)


def flavor_label(folder: str, product: str, version: str | None) -> str:
    """A flavor folder with its product code and version, as the scan and the
    flavor prompt show it."""
    return f"{folder} ({product or 'no product'}, {version or 'no version in .build.info'})"


def _sync_lines(scan: FlavorScan, wtf_is_link: bool) -> list[str]:
    lines: list[str] = []
    for hint in scan.sync:
        if hint.link_target is not None:
            if hint.path.casefold() == _WTF.casefold():
                behind = "your settings and SavedVariables are"
            else:
                behind = "what is behind it is"
            lines.append(
                f"  {hint.path} is a link into a folder {hint.service} appears to sync "
                f"({hint.link_target}). wowlab does not follow it, so {behind} not in this "
                "snapshot; back up the folder it points to yourself."
            )
            continue
        where = f"The folders above {_WTF}" if wtf_is_link else _WTF
        verb = "look" if wtf_is_link else "looks"
        what = (
            f"{_WTF} itself is a link and is not followed (see below)."
            if wtf_is_link
            else "wowlab still reads and snapshots it."
        )
        lines.append(
            f"  {where} {verb} synced by {hint.service}: {hint.path} ({hint.evidence}). "
            f"{what} A sync service can leave online-only placeholders and conflict copies, "
            "and it copies to your other computers whatever a later restore puts back."
        )
    return lines


def scan_lines(scan: FlavorScan, number: int, count: int) -> list[str]:
    """The scan of one flavor as text: size and estimate first, then every
    finding (a large snapshot, a sync service, conflict copies, placeholders,
    online-only folders, links, folders that could not be listed)."""
    which = f"Flavor {number} of {count}: " if count > 1 else "Flavor "
    names = _and([s.path for s in scan.subtrees])
    lines = [
        f"{which}{flavor_label(scan.folder, scan.product, scan.version)}: a snapshot would "
        f"hold {names}: {_files(scan.files)}, {_size(scan.bytes)}; it is estimated to take "
        f"{_duration(scan.estimated_seconds)}.",
    ]
    for s in scan.subtrees:
        if s.link:
            detail = "a link, not followed"
        elif s.present:
            detail = f"{_files(s.files)}, {_size(s.bytes)}"
        else:
            detail = "not there"
        lines.append(f"  {s.path}: {detail}")
    if scan.large:
        lines.append(
            f"  This is large: the snapshot is estimated to take "
            f"{_duration(scan.estimated_seconds)} and needs room for it in wowlab's store "
            "(compressed; a file already stored is not stored again)."
        )
    wtf_is_link = any(s.link and s.path.casefold() == _WTF.casefold() for s in scan.subtrees)
    lines.extend(_sync_lines(scan, wtf_is_link))
    if scan.conflict_files:
        n = len(scan.conflict_files)
        lines.append(
            f"  {_count(n, 'conflict copy', 'conflict copies')} left by a sync service "
            f"(snapshotted like any other file; wowlab does not merge "
            f"{'it' if n == 1 else 'them'}): {_listing(scan.conflict_files)}"
        )
    if scan.placeholders:
        lines.append(
            f"  {_files(scan.placeholders)} online-only: their content is not on this disk, "
            "and reading them for a snapshot makes the sync service download them. The "
            "estimate does not include that download, and the snapshot fails for any file "
            "the service cannot fetch."
        )
    if scan.online_only_folders:
        n = len(scan.online_only_folders)
        lines.append(
            f"  {_count(n, 'online-only folder', 'online-only folders')}, not listed (not "
            "counted above; a snapshot would make the sync service download "
            f"{'it' if n == 1 else 'them'}): {_listing(scan.online_only_folders)}"
        )
    if scan.links:
        lines.append(
            f"  {_count(len(scan.links), 'link', 'links')}, never followed: a snapshot "
            "records each link and the text of its target, not the files behind it:"
        )
        for link in scan.links:
            target = link.target if link.target is not None else "(target unreadable)"
            lines.append(f"    {link.path} -> {target} ({link.kind})")
            if link.path.casefold() == _WTF.casefold():
                lines.append(
                    f"    {_WTF} itself is a link, so the settings and SavedVariables behind "
                    "it are not in the snapshot."
                )
    for bad in scan.unreadable:
        lines.append(
            f"  could not list {bad.path} ({bad.reason}); a snapshot stops there until it can"
        )
    return lines


def _client_lines(running: bool | None, clients: Sequence[str] = ()) -> list[str]:
    stale = (
        "A snapshot only reads, so it can still be taken now, as `wowlab snap create` "
        "takes one, but SavedVariables on disk are from your last logout or /reload, and "
        "the game writes over them when you next log out, /reload or exit. On Windows, a "
        "file the game holds open may not be readable; if the snapshot fails, close the "
        "game and run it again. Commands that change files refuse until the game is closed."
    )
    which = f" ({'; '.join(clients)})" if clients else ""
    if running is True:
        return [f"The game is running{which}. {stale}"]
    if running is None:
        return [f"wowlab cannot tell whether the game is running{which}. {stale}"]
    return []


def undo_line(taken: FirstSnapshot | None, scan: FlavorScan | None = None) -> str:
    """The last line of a first run: how to undo.

    With a snapshot, `scan` is the scan of its flavor: the line names only
    the subtrees the scan found present and not links, says so when WTF is a
    link, and mentions SavedVariables only when WTF itself is held. The
    restore sentence says what a whole-snapshot `snap restore` does: it
    writes back what the snapshot holds and removes nothing added since."""
    if taken is None:
        return (
            "To undo: this run changed nothing in your install, so there is nothing to "
            "undo. Every change wowlab makes later asks first (unless you pass --yes) and "
            "takes a snapshot, and `wowlab undo` reverses it."
        )
    if scan is None:
        raise ValueError("the undo line for a snapshot needs the scan of its flavor")
    held = [s.path for s in scan.subtrees if s.present and not s.link]
    wtf_link = next(
        (s.path for s in scan.subtrees if s.link and s.path.casefold() == _WTF.casefold()), None
    )
    wtf_held = any(name.casefold() == _WTF.casefold() for name in held)
    if held:
        were = "as it was" if len(held) == 1 else "as they were"
        holds = f"Snapshot {taken.id} holds {_and(held)} {were} on disk" + (
            " (SavedVariables as of your last logout or /reload)." if wtf_held else "."
        )
    else:
        holds = f"Snapshot {taken.id} holds no folder's files."
    if wtf_link is not None:
        holds += (
            f" {wtf_link} is a link (recorded, not followed; its settings and SavedVariables "
            "are not in this snapshot)."
        )
    return (
        f"To undo: this run changed nothing in your install. {holds} "
        f"`wowlab snap restore {taken.id} --dry-run` shows what restoring it would change. "
        "A restore writes back the files it holds that changed since (client-written "
        "backups such as .lua.bak are skipped unless named with --paths); it does not "
        "remove files added since. It asks first (unless you pass --yes). To reverse a "
        "later change wowlab makes, run `wowlab undo`."
    )


def _verify_lines(report: snapshot.VerifyReport) -> list[str]:
    lines = [
        "Verified the store (wowlab snap verify): "
        f"{_count(report.objects_checked, 'object', 'objects')} and "
        f"{_count(report.manifests_checked, 'manifest', 'manifests')} checked."
    ]
    lines.extend(f"  corrupt object: {name}" for name in report.corrupt_objects)
    lines.extend(
        f"  missing object {miss.sha256[:12]} for {miss.path} in {miss.snapshot_id}"
        for miss in report.missing_objects
    )
    lines.extend(f"  damaged manifest {bad.name}: {bad.reason}" for bad in report.invalid_manifests)
    lines.append("  all sound" if report.ok else "  the store has problems; see above")
    return lines


# ─── the scan ────────────────────────────────────────────────────────────────


def estimate_seconds(total_bytes: int, files: int) -> int:
    """A deliberately slow estimate of `snap create` plus `snap verify`."""
    return math.ceil(total_bytes / THROUGHPUT_BYTES + files * FILE_SECONDS)


def _is_placeholder(st: os.stat_result) -> bool:
    """A file whose content a sync service has not put on this disk."""
    if int(getattr(st, "st_file_attributes", 0)) & _WINDOWS_PLACEHOLDER:
        return True
    return bool(int(getattr(st, "st_flags", 0)) & _MACOS_DATALESS)


def _is_online_only_dir(st: os.stat_result) -> bool:
    """A folder whose listing a sync service has not put on this disk."""
    if int(getattr(st, "st_file_attributes", 0)) & _WINDOWS_ONLINE_ONLY_DIR:
        return True
    return bool(int(getattr(st, "st_flags", 0)) & _MACOS_DATALESS)


def _link_target(path: Path) -> str | None:
    try:
        return str(path.readlink())
    except (OSError, ValueError):
        return None


def _is_conflict(name: str) -> bool:
    folded = name.casefold()
    return any(mark in folded for mark in _CONFLICT_MARKS)


class _Walk:
    def __init__(self) -> None:
        self.files = 0
        self.bytes = 0
        self.links: list[LinkFound] = []
        self.conflicts: list[str] = []
        self.placeholders = 0
        self.online_only: list[str] = []
        self.unreadable: list[Unreadable] = []

    def link(self, rel: str, path: Path, *, symlink: bool) -> None:
        kind: Literal["symlink", "junction"] = "symlink" if symlink else "junction"
        self.links.append(LinkFound(path=rel, kind=kind, target=_link_target(path)))

    def file(self, rel: str, name: str, st: os.stat_result) -> None:
        self.files += 1
        self.bytes += st.st_size
        if _is_conflict(name):
            self.conflicts.append(rel)
        if _is_placeholder(st):
            self.placeholders += 1

    def tree(self, rel: str, start: Path) -> None:
        """Every entry under the directory `start`, links never followed."""
        stack = [(rel, start)]
        while stack:
            rel_dir, abs_dir = stack.pop()
            try:
                with os.scandir(abs_dir) as it:
                    children = sorted(it, key=lambda d: d.name)
            except FileNotFoundError:
                continue  # removed while we looked
            except OSError as exc:
                self.unreadable.append(Unreadable(path=rel_dir, reason=exc.strerror or str(exc)))
                continue
            for child in children:
                child_rel = f"{rel_dir}/{child.name}"
                path = Path(child.path)
                try:
                    if child.is_symlink():
                        self.link(child_rel, path, symlink=True)
                    elif child.is_dir(follow_symlinks=False):
                        if snapshot._is_link_like_dir(child):
                            self.link(child_rel, path, symlink=False)
                        elif _is_online_only_dir(child.stat(follow_symlinks=False)):
                            # Listing it would make the sync service download it.
                            self.online_only.append(child_rel)
                        else:
                            stack.append((child_rel, path))
                    elif child.is_file(follow_symlinks=False):
                        self.file(child_rel, child.name, child.stat(follow_symlinks=False))
                except FileNotFoundError:
                    continue
                except OSError as exc:
                    self.unreadable.append(
                        Unreadable(path=child_rel, reason=exc.strerror or str(exc))
                    )


def _spellings(path: Path) -> list[Path]:
    """`path` as given (made absolute) and with the folders above it resolved;
    `path` itself is never resolved, so a link there is not followed."""
    given = path.absolute()
    try:
        resolved = given.parent.resolve() / given.name
    except (OSError, RuntimeError):
        return [given]
    return [given] if resolved == given else [given, resolved]


def _ancestors(path: Path) -> list[Path]:
    """Every folder above `path`, in both of its spellings, nearest first."""
    out: list[Path] = []
    for p in _spellings(path):
        for q in p.parents:
            if q not in out:
                out.append(q)
    return out


def _name_service(name: str) -> SyncService | None:
    folded = name.casefold()
    for service, prefixes in _NAME_PREFIXES:
        for prefix in prefixes:
            if folded == prefix or folded.startswith((prefix + " ", prefix + "-", prefix + "(")):
                return service
    return None


def _has_entry(folder: Path, name: str, *, regular: bool) -> bool:
    try:
        st = (folder / name).lstat()
    except (OSError, ValueError):
        return False
    return stat.S_ISREG(st.st_mode) or not regular


def _is_real_dir(path: Path) -> bool:
    """A directory that is not a link or junction: one it is safe to look inside."""
    try:
        st = path.lstat()
    except (OSError, ValueError):
        return False
    return stat.S_ISDIR(st.st_mode) and not snapshot._is_link_like_dir(path)


def sync_hints(wtf: Path, links: Sequence[LinkFound] = ()) -> list[SyncHint]:
    """Signs that a sync service holds `wtf` (a flavor's WTF folder, which
    need not exist): a folder named like a sync client's, at or above it, or
    a sync client's marker file in one; and a link in the snapshot subtrees
    whose target text names such a folder (the target is never looked at).
    One hint per service for the folders, the nearest first, then one per
    link that names such a folder. Reads names and `lstat`s only,
    and never looks inside `wtf` when it is a link."""
    found: dict[SyncService, SyncHint] = {}
    # WTF itself is looked inside only when it is a real folder (not absent,
    # not a link or junction); the folders above it are the install's own.
    leaf = [p for p in _spellings(wtf) if _is_real_dir(p)][:1]
    for folder in leaf + _ancestors(wtf):
        service = _name_service(folder.name)
        if service is not None and service not in found:
            found[service] = SyncHint(
                service=service, path=str(folder), evidence=f'its name is "{folder.name}"'
            )
        for marker_service, marker, regular in _MARKERS:
            if marker_service not in found and _has_entry(folder, marker, regular=regular):
                found[marker_service] = SyncHint(
                    service=marker_service, path=str(folder), evidence=f"it holds {marker}"
                )
    hints = list(found.values())
    for link in links:  # one hint per link, whatever the folders above say
        if link.target is None:
            continue
        for part in PurePath(link.target).parts:
            service = _name_service(part)
            if service is not None:
                hints.append(
                    SyncHint(
                        service=service,
                        path=link.path,
                        evidence=f'a link whose target passes through "{part}"',
                        link_target=link.target,
                    )
                )
                break
    return hints


def scan_flavor(flavor: install.Flavor, lay: layout.Layout) -> FlavorScan:
    """What a snapshot of `flavor` would hold (`snap create`'s subtrees): files,
    bytes and an estimate, and every link, conflict copy, placeholder and
    unlistable folder in them. Reads only; follows no link."""
    walk = _Walk()
    subtrees: list[SubtreeScan] = []
    for name in lay.snapshot_subtrees():
        start = Path(flavor.path) / name
        files, size = walk.files, walk.bytes
        try:
            st = start.lstat()
        except FileNotFoundError:
            subtrees.append(SubtreeScan(path=name, present=False, link=False, files=0, bytes=0))
            continue
        except OSError as exc:
            walk.unreadable.append(Unreadable(path=name, reason=exc.strerror or str(exc)))
            subtrees.append(SubtreeScan(path=name, present=True, link=False, files=0, bytes=0))
            continue
        is_link = stat.S_ISLNK(st.st_mode) or (
            stat.S_ISDIR(st.st_mode) and snapshot._is_link_like_dir(start)
        )
        if is_link:
            walk.link(name, start, symlink=stat.S_ISLNK(st.st_mode))
        elif stat.S_ISDIR(st.st_mode) and _is_online_only_dir(st):
            walk.online_only.append(name)
        elif stat.S_ISDIR(st.st_mode):
            walk.tree(name, start)
        elif stat.S_ISREG(st.st_mode):
            walk.file(name, name, st)
        subtrees.append(
            SubtreeScan(
                path=name,
                present=True,
                link=is_link,
                files=walk.files - files,
                bytes=walk.bytes - size,
            )
        )
    wtf = next((s.path for s in subtrees if s.path.casefold() == _WTF.casefold()), _WTF)
    seconds = estimate_seconds(walk.bytes, walk.files)
    return FlavorScan(
        folder=flavor.folder,
        product=flavor.product,
        version=flavor.version,
        subtrees=subtrees,
        files=walk.files,
        bytes=walk.bytes,
        estimated_seconds=seconds,
        large=walk.bytes >= LARGE_BYTES or seconds >= LARGE_SECONDS,
        links=walk.links,
        sync=sync_hints(Path(flavor.path) / wtf, walk.links),
        conflict_files=walk.conflicts,
        placeholders=walk.placeholders,
        online_only_folders=walk.online_only,
        unreadable=walk.unreadable,
    )


# ─── the snapshot ────────────────────────────────────────────────────────────


def snapshot_flavor(
    store: snapshot.SnapshotStore,
    inst: install.Install,
    flavor: install.Flavor,
    lay: layout.Layout,
    *,
    label: str = "",
    screenshots: bool = False,
    client_running: bool | None = None,
) -> snapshot.Manifest:
    """Snapshot a flavor's WTF/, Interface/ and Fonts/ into `store`, as
    `wowlab snap create` does: the install is only read (L1).

    A store that is an ancestor of the install is outside every install, so
    the gate would create it and its `lock` before `create` refused the
    overlap: it is refused first, with nothing created (M10-19). The first
    snapshot makes the store; the gate refuses one inside any install before
    creating anything, then holds its lock like every later one.
    Raises what `SnapshotStore.create` and `guard.store_lock` raise."""
    subtrees = [f"{flavor.folder}/{s}" for s in lay.snapshot_subtrees(screenshots=screenshots)]
    store.refuse_holding(Path(inst.root))
    with guard.store_lock(store.path, create=True):
        return store.create(
            Path(inst.root),
            subtrees,
            label=label,
            flavor_folder=flavor.folder,
            flavor_version=flavor.version,
            client_running=client_running,
        )


def _first_snapshot(
    manifest: snapshot.Manifest, flavor: install.Flavor, lay: layout.Layout
) -> FirstSnapshot:
    # In the order the scan lists them (WTF first), not the manifest's sorted one.
    order = [f"{flavor.folder}/{s}" for s in lay.snapshot_subtrees()]
    ranked = sorted(manifest.subtrees, key=lambda s: order.index(s) if s in order else len(order))
    return FirstSnapshot(
        id=manifest.id,
        label=manifest.label,
        flavor_folder=flavor.folder,
        subtrees=ranked,
        files=sum(1 for e in manifest.entries if e.kind == "file"),
        links=sum(1 for e in manifest.entries if e.kind == "symlink"),
        bytes=sum(e.size for e in manifest.entries if e.kind == "file"),
    )


# ─── the flow ────────────────────────────────────────────────────────────────


def _either(states: Sequence[bool | None]) -> bool | None:
    """Running beats cannot-tell beats not running, as the write gate reads them."""
    if any(s is True for s in states):
        return True
    if any(s is None for s in states):
        return None
    return False


def run(
    inst: install.Install | None,
    *,
    store: snapshot.SnapshotStore,
    chosen: install.Flavor | None,
    yes: bool,
    client_running: Callable[[install.Flavor], bool | None],
    say: Callable[[str], None],
    choose: Callable[[Sequence[str]], int | None] | None,
    confirm: Callable[[str], bool] | None,
    clients: Callable[[], Sequence[str]] = tuple,
) -> FirstRunReport:
    """The first-run section of `doctor`, after its report.

    `chosen` is the flavor `--flavor` named, if any. `choose` asks the player
    to pick one of several flavors (it is given one label per flavor and
    returns an index, or None for none) and `confirm` asks a yes/no
    question; the command passes None for both when it must not ask
    (`--json`, input that is not a terminal). `--yes` accepts the offer;
    with several flavors it still needs `--flavor`. `client_running` is the
    probe `snap create` uses; it is asked again right before the snapshot,
    since a prompt can wait while the game starts. `clients` names the
    client processes found, for the text. Every line goes through `say`,
    the undo line last."""
    others: list[str] = []

    def report(
        outcome: Outcome,
        *,
        scans: Sequence[FlavorScan] = (),
        flavor: install.Flavor | None = None,
        running: bool | None = None,
        taken: FirstSnapshot | None = None,
        verified: snapshot.VerifyReport | None = None,
        error: str | None = None,
        refused: bool = False,
    ) -> FirstRunReport:
        scanned = None
        if flavor is not None:
            scanned = next((s for s in scans if s.folder == flavor.folder), None)
        line = undo_line(taken, scanned)
        say(line)
        return FirstRunReport(
            store_path=str(store.path),
            flavors=list(scans),
            chosen=flavor.folder if flavor is not None else None,
            client_running=running,
            outcome=outcome,
            snapshot=taken,
            verify=verified,
            error=error,
            refused=refused,
            not_snapshotted=others,
            undo=line,
        )

    if inst is None:
        return report("no_install")
    if not inst.flavors:
        say(f"{inst.root} has no flavor folders, so there is nothing to snapshot yet.")
        return report("no_flavors")

    scans = [scan_flavor(f, layout.Layout.for_flavor(f, inst.root)) for f in inst.flavors]
    others.extend(f.folder for f in inst.flavors)
    for n, scan in enumerate(scans, 1):
        for line in scan_lines(scan, n, len(scans)):
            say(line)

    def estimate(flavor: install.Flavor) -> str:
        return _duration(next(s for s in scans if s.folder == flavor.folder).estimated_seconds)

    def label(flavor: install.Flavor) -> str:
        return flavor_label(flavor.folder, flavor.product, flavor.version)

    accepted = yes
    flavor = chosen
    if flavor is None and len(inst.flavors) == 1:
        flavor = inst.flavors[0]
    if flavor is None:
        folders = [f.folder for f in inst.flavors]
        if choose is None:
            if yes:
                say(
                    f"{len(folders)} flavors are installed ({', '.join(folders)}); --yes "
                    "needs --flavor to say which one to snapshot: wowlab doctor --yes "
                    "--flavor <folder>"
                )
                return report("choose_flavor", scans=scans)
            say(
                "To take a first snapshot, choose a flavor: wowlab doctor --yes --flavor "
                "<folder> (or wowlab snap create --flavor <folder>)."
            )
            return report("not_asked", scans=scans)
        # Said before the question, so the player chooses knowing it.
        any_running = _either([client_running(f) for f in inst.flavors])
        for line in _client_lines(any_running, clients()):
            say(line)
        picked = choose([label(f) for f in inst.flavors])
        if picked is None:
            say("No flavor chosen; no snapshot was taken.")
            return report("declined", scans=scans)
        flavor = inst.flavors[picked]
        # Choosing a flavor is the answer, unless the game runs or may run:
        # then the yes/no question below is asked too.
        accepted = yes or any_running is False
        running = client_running(flavor)
    else:
        running = client_running(flavor)
        for line in _client_lines(running, clients() if running is not False else ()):
            say(line)
    if not accepted:
        if confirm is None:
            say(
                "To take a first snapshot: wowlab doctor --yes"
                + (" --flavor <folder>" if len(inst.flavors) > 1 else "")
                + " (or wowlab snap create)."
            )
            return report("not_asked", scans=scans, flavor=flavor, running=running)
        anyway = " anyway" if running is not False else ""
        question = (
            f"Take a first snapshot of {label(flavor)} into wowlab's store{anyway} "
            f"({estimate(flavor)})?"
        )
        if not confirm(question):
            say("No snapshot was taken.")
            return report("declined", scans=scans, flavor=flavor, running=running)

    # Asked again right before the capture, as `snap create` asks: a prompt
    # can wait while the game is started.
    now = client_running(flavor)
    if now is not False and running is False:
        for line in _client_lines(now, clients()):
            say(line)
    running = now
    say(f"Taking a snapshot of {flavor.folder} into {store.path} ...")
    lay = layout.Layout.for_flavor(flavor, inst.root)
    try:
        manifest = snapshot_flavor(
            store, inst, flavor, lay, label=SNAPSHOT_LABEL, client_running=running
        )
    except guard.GuardError as exc:
        say(f"The snapshot was refused by the write gate: {exc}")
        return report(
            "failed", scans=scans, flavor=flavor, running=running, error=str(exc), refused=True
        )
    except (snapshot.SnapshotError, OSError) as exc:
        say(f"The snapshot could not be taken: {exc}")
        return report("failed", scans=scans, flavor=flavor, running=running, error=str(exc))
    taken = _first_snapshot(manifest, flavor, lay)
    others.remove(flavor.folder)
    links = f", {_count(taken.links, 'link', 'links')} recorded, not followed"
    say(
        f"Snapshot {taken.id} ({taken.label}): {_files(taken.files)}, {_size(taken.bytes)}"
        + (links if taken.links else "")
    )
    try:
        verified = store.verify()
    except (snapshot.SnapshotError, OSError) as exc:
        say(f"The store could not be verified: {exc}")
        return report(
            "failed", scans=scans, flavor=flavor, running=running, taken=taken, error=str(exc)
        )
    for line in _verify_lines(verified):
        say(line)
    for folder in others:
        say(f"{folder} has no snapshot yet: wowlab snap create --flavor {folder}")
    return report(
        "taken" if verified.ok else "failed",
        scans=scans,
        flavor=flavor,
        running=running,
        taken=taken,
        verified=verified,
        error=None if verified.ok else "the snapshot store did not verify (wowlab snap verify)",
    )
