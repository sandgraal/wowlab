"""addonsource: where each installed addon came from, as an addon manager
recorded it (M22-16; LAB_IDEAS **addon-provenance**, its first slice).

The one manager read so far is WowUp. It keeps the addons it installed in
`addons.json` under its own user-data folder, outside the install (on macOS
`~/Library/Application Support/WowUp/`, seen 2026-11-23; on Windows
`%APPDATA%\\WowUp\\` and on Linux `~/.config/WowUp/`, Electron's rule,
**[verify]**). The folder is Electron's `userData` rule per OS
(`wowup_folder`: `platformdirs`' roaming config folder, except on macOS,
where Electron ignores `XDG_CONFIG_HOME`); nothing about a flavor is known
here (L6). Two files are opened there, read-only (L1), each refused when it
is a link, not a regular file, or past a bound, and never waited on (a FIFO
is refused, not opened for reading): `addons.json`, and from
`preferences.json` only the `id` and `location` of each `wow_installations`
entry. `sensitive.json`, which holds WowUp's own keys, is never opened.

`addons.json` is electron-store JSON: one object keyed by WowUp's record
id, each value one addon (`docs/LAB_FORMATS.md` §13). The fields read are
`name`, `providerName` (Wago, CurseForge, GitHub, …; `Unknown` for an addon
WowUp found on disk but did not install **[verify]**), `externalId`,
`externalUrl`, `installedVersion`, `latestVersion`, `installedFolderList`
(else the comma-joined `installedFolders`), `clientType`,
`installationId`, `installedAt` and `isIgnored`. Everything else is left
alone, and `downloadUrl`, a server-issued link, is never read into a model.

WowUp keeps one list for every game folder it manages: each record names
its game folder by `installationId`, and `preferences.json` gives each
installation's `location` (on macOS the client's `.app` inside the flavor
folder; on Windows the `.exe` there, **[verify]**). `match_sources` joins
to one flavor's `Interface/AddOns/` folders only the records of the
installations whose location is inside that flavor folder (paths compared,
no folder name known, L6). A record of another installation is never a
claim on a folder here: it is counted against its own game folder, and a
folder of the same name gets a hint only. When the installations cannot be
read (no `preferences.json`, or a record whose installation is not listed),
records are joined by folder name alone, and the report says so.

Folder names are compared with ASCII letters folded (the rule
`wtfconfig.fold_name` and `addoncheck` use). A joined record is **stale**
(it no longer describes what is on disk) when none of its folders is here,
when one of them is missing, or when the present folders' TOCs carry a
`## Version:` and none agrees with the version WowUp recorded. "Agrees" is
`versions_agree`: equal once case is folded, a leading `v` dropped and
trailing `.0` groups dropped (`1.0` and `1.0.0`), or one found inside the
other between characters that are not a letter, a digit or a dot, where a
`-` followed by a letter also counts as part of the version
(`Pawn 2.13.16` and `2.13.16`, `DBM-Dungeons-r264` and `r264` agree;
`12.1.26-alpha` and `12.1.26` do not). The folders whose TOC
`## X-Wago-ID:` is the record's `externalId` (DBM-Core's is) are the ones
judged when there are any; otherwise every present folder is, and one
agreeing folder is enough (DBM's voice pack carries a commit id). A folder
no joined record claims is **unclaimed** (not in WowUp's list: installed
by hand, by another app, or by a manager wowlab does not read); that is
said only when WowUp's list was read and WowUp manages this game folder or
which one it manages cannot be told. The lab-addon is wowlab's own and is
never called unclaimed.
"""

from __future__ import annotations

import json
import os
import re
import stat
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal

import platformdirs
from pydantic import BaseModel, ConfigDict

from wowlab_core import layout
from wowlab_core.labaddon import ADDON_NAME as LAB_ADDON_NAME
from wowlab_core.layout import FsText
from wowlab_core.wtfconfig import fold_name

__all__ = [
    "MAX_PREFERENCES_BYTES",
    "MAX_RECORDS_BYTES",
    "PREFERENCES_FILE",
    "RECORDS_FILE",
    "WOWUP_APP",
    "AddonSources",
    "Claim",
    "FolderSource",
    "Installation",
    "ManagerFile",
    "ManagerScope",
    "OtherInstallation",
    "RecordMatch",
    "TocVersion",
    "WowUpRecord",
    "match_sources",
    "read_wowup",
    "versions_agree",
    "wowup_folder",
]

WOWUP_APP = "WowUp"  # the app's user-data folder name
RECORDS_FILE = "addons.json"
PREFERENCES_FILE = "preferences.json"
# The owner's files hold 6 records in 11.6 KB and 2.7 KB of settings; a
# file past its bound is not read.
MAX_RECORDS_BYTES = 16 * 1024 * 1024
MAX_PREFERENCES_BYTES = 1024 * 1024
# The TOC directive naming an addon's Wago project, per provider (the one
# seen: DBM-Core's `## X-Wago-ID: qv633o6b`, its record's `externalId`).
_PROJECT_DIRECTIVES = {"wago": "X-Wago-ID"}
_UNKNOWN_PROVIDER = "unknown"

Manager = Literal["WowUp"]
StaleWhy = Literal["no_folder_here", "folders_missing", "version_differs"]
FileState = Literal["read", "missing", "unreadable"]


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class WowUpRecord(_Model):
    """One addon in WowUp's `addons.json`, as written (no field is required)."""

    id: str  # the key the record is stored under
    name: str | None
    provider: str | None  # `providerName`: "Wago", "CurseForge", "GitHub", …
    external_id: str | None
    external_url: str | None
    installed_version: str | None
    latest_version: str | None  # as of WowUp's last check, not now
    folders: tuple[str, ...]  # `installedFolderList`, else `installedFolders` split
    client_type: int | None  # WowUp's own number for the game folder's kind
    installation_id: str | None  # WowUp's id of the game folder it installed into
    installed_at: str | None  # `installedAt` as written (ISO 8601 text)
    ignored: bool | None  # `isIgnored`: the player told WowUp to leave it alone


class Installation(_Model):
    """One `wow_installations` entry of WowUp's `preferences.json`."""

    id: str
    location: str  # as written: the client's `.app` or `.exe` in its flavor folder


class ManagerFile(_Model):
    """What wowlab found where a manager keeps its records."""

    manager: Manager
    path: str  # the records file wowlab looked for
    # `read`: parsed; `missing`: no such file; `unreadable`: there, but not
    # read (`error` says why)
    state: FileState
    error: str | None
    written_ns: int | None  # the file's modification time, when it was read
    records: tuple[WowUpRecord, ...]
    skipped: int  # entries that were not an object, so not records
    # The manager's own list of the game folders it manages.
    settings_path: str
    settings_state: FileState
    settings_error: str | None
    installations: tuple[Installation, ...]


class TocVersion(_Model):
    """One `## Version:` and the present folders whose TOCs carry it."""

    version: FsText
    folders: tuple[FsText, ...]


class RecordMatch(_Model):
    """One record, and how it stands against this flavor's addon folders."""

    manager: Manager
    record_id: str
    name: str | None
    provider: str | None  # as written
    # False for WowUp's `Unknown`: an addon it lists but did not install.
    provider_known: bool
    external_url: str | None
    installed_version: str | None
    latest_version: str | None
    # WowUp's own view when it last checked: its latest version differs from
    # the one it installed. None when either is not recorded. Shown to a
    # player only when the record is not ignored.
    update_available: bool | None
    ignored: bool | None
    # `this_folder`: the record's installation is this flavor folder;
    # `other_folder`: it is another game folder (`installation_folder`);
    # `unknown`: wowlab cannot tell, so the record is joined by name.
    installation: Literal["this_folder", "other_folder", "unknown"]
    installation_folder: str | None  # the other game folder, as WowUp's location gives it
    # Claimed folders present, as named on disk (empty for another folder's record).
    folders_here: tuple[FsText, ...]
    folders_missing: tuple[str, ...]  # claimed folders not present, as recorded
    # Another folder's record: folders here that only share a name with it.
    same_name_here: tuple[FsText, ...]
    toc_versions: tuple[TocVersion, ...]  # of the present folders judged
    # `agrees`: a judged folder's TOC version agrees with the recorded one;
    # `differs`: TOCs carry versions and none agrees; `unknown`: no TOC
    # carries one, no version was recorded, or the record is another folder's.
    version_check: Literal["agrees", "differs", "unknown"]
    stale: bool
    stale_why: tuple[StaleWhy, ...]


class Claim(_Model):
    manager: Manager
    record_id: str


class FolderSource(_Model):
    """One folder under `Interface/AddOns/` and the records that claim it."""

    folder: FsText
    claims: tuple[Claim, ...]
    # Records of another game folder that list a folder of this name: a
    # hint, never a claim.
    same_name_as: tuple[Claim, ...]
    # True: WowUp's list was read, it manages this game folder (or which it
    # manages cannot be told) and no record claims this folder. None: no
    # list was read, or WowUp does not manage this game folder. Never True
    # for the lab-addon.
    unclaimed: bool | None
    lab_addon: bool


class OtherInstallation(_Model):
    installation_id: str
    folder: str  # the folder holding WowUp's `location` for it
    records: int


class ManagerScope(_Model):
    """Whether one manager manages this flavor folder."""

    manager: Manager
    # True: an installation's location is inside this flavor folder;
    # False: its installations are read and none is; None: the records
    # file was not read, or its installations could not be.
    manages: bool | None
    installation_ids: tuple[str, ...]  # this flavor folder's installations
    others: tuple[OtherInstallation, ...]  # the installations with records elsewhere
    # Records joined by folder name alone, because their installation is
    # not known.
    by_name: int


class AddonSources(_Model):
    files: tuple[ManagerFile, ...]
    scopes: tuple[ManagerScope, ...]  # one per file, in file order
    records: tuple[RecordMatch, ...]  # in file order
    folders: tuple[FolderSource, ...]  # in the order of the addons given


# ─── finding and reading WowUp's files ───────────────────────────────────────


def wowup_folder(platform: str | None = None) -> Path:
    """WowUp's user-data folder on this machine: Electron's `userData`
    folder for an app named WowUp. On macOS that is
    `~/Library/Application Support/WowUp` whatever `XDG_CONFIG_HOME` says
    (Electron ignores it there, where `platformdirs` follows it); elsewhere
    it is `platformdirs`' roaming config folder, `%APPDATA%\\WowUp` on
    Windows and `$XDG_CONFIG_HOME/WowUp` (else `~/.config/WowUp`) on Linux.
    `platform` (`sys.platform` by default) decides only whether the macOS
    rule applies; any other value asks `platformdirs` about this machine."""
    platform = sys.platform if platform is None else platform
    if platform == "darwin":
        return Path.home() / "Library" / "Application Support" / WOWUP_APP
    return platformdirs.user_config_path(WOWUP_APP, appauthor=False, roaming=True)


class _UnreadableError(Exception):
    pass


def _read_bounded(path: Path, limit: int) -> tuple[bytes, int]:
    """The bytes of the regular file `path` and its modification time.
    Raises FileNotFoundError when there is nothing there, and `_UnreadableError`
    for a link, anything that is not a regular file (a FIFO is never opened
    for reading, so nothing waits), a file past `limit`, or an error."""
    try:
        seen = path.lstat()
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise _UnreadableError(exc.strerror or type(exc).__name__) from exc
    if stat.S_ISLNK(seen.st_mode):
        raise _UnreadableError("a link, not followed")
    if not stat.S_ISREG(seen.st_mode):
        raise _UnreadableError("not a regular file")
    if seen.st_size > limit:
        raise _UnreadableError(f"larger than {limit} bytes")
    try:
        fd = os.open(
            path,
            os.O_RDONLY
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
            | getattr(os, "O_CLOEXEC", 0),
        )
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise _UnreadableError(exc.strerror or type(exc).__name__) from exc
    with os.fdopen(fd, "rb") as handle:
        facts = os.fstat(handle.fileno())
        if not stat.S_ISREG(facts.st_mode) or (facts.st_dev, facts.st_ino) != (
            seen.st_dev,
            seen.st_ino,
        ):
            raise _UnreadableError("changed while it was opened")
        if facts.st_size > limit:
            raise _UnreadableError(f"larger than {limit} bytes")
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise _UnreadableError(f"larger than {limit} bytes")
    return data, facts.st_mtime_ns


def _json(data: bytes) -> Any:
    try:
        return json.loads(data.decode("utf-8-sig"))
    except UnicodeDecodeError as exc:
        raise _UnreadableError("not UTF-8 text") from exc
    except RecursionError as exc:
        raise _UnreadableError("nested too deeply") from exc
    except ValueError as exc:
        raise _UnreadableError(f"not JSON: {exc}") from exc


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _folders(entry: dict[str, Any]) -> tuple[str, ...]:
    listed = entry.get("installedFolderList")
    if isinstance(listed, list):
        names = [n.strip() for n in listed if isinstance(n, str)]
    else:
        joined = entry.get("installedFolders")
        names = [n.strip() for n in joined.split(",")] if isinstance(joined, str) else []
    return tuple(dict.fromkeys(n for n in names if n))


def _record(key: str, entry: dict[str, Any]) -> WowUpRecord:
    client = entry.get("clientType")
    ignored = entry.get("isIgnored")
    return WowUpRecord(
        id=key,
        name=_text(entry.get("name")),
        provider=_text(entry.get("providerName")),
        external_id=_text(entry.get("externalId")),
        external_url=_text(entry.get("externalUrl")),
        installed_version=_text(entry.get("installedVersion")),
        latest_version=_text(entry.get("latestVersion")),
        folders=_folders(entry),
        client_type=client if isinstance(client, int) and not isinstance(client, bool) else None,
        installation_id=_text(entry.get("installationId")),
        installed_at=_text(entry.get("installedAt")),
        ignored=ignored if isinstance(ignored, bool) else None,
    )


def _installations(
    folder: Path,
) -> tuple[FileState, str | None, tuple[Installation, ...]]:
    path = folder / PREFERENCES_FILE
    try:
        data, _ = _read_bounded(path, MAX_PREFERENCES_BYTES)
        parsed = _json(data)
    except FileNotFoundError:
        return "missing", None, ()
    except _UnreadableError as exc:
        return "unreadable", str(exc), ()
    listed = parsed.get("wow_installations") if isinstance(parsed, dict) else None
    if not isinstance(listed, list):
        return "unreadable", "no list of game folders in it", ()
    found = []
    for entry in listed:
        if not isinstance(entry, dict):
            continue
        key, location = _text(entry.get("id")), _text(entry.get("location"))
        if key and location:
            found.append(Installation(id=key, location=location))
    return "read", None, tuple(found)


def read_wowup(folder: Path | None = None) -> ManagerFile:
    """WowUp's `addons.json` in `folder` (default: `wowup_folder()`), and
    its game folders from `preferences.json` there, read only. A missing
    file is `missing`; a file that is a link, is not a regular file, is past
    its bound, is not JSON or does not hold what it should is `unreadable`,
    with why."""
    base = wowup_folder() if folder is None else folder
    path = base / RECORDS_FILE
    settings_state, settings_error, installations = _installations(base)

    def made(
        state: FileState,
        error: str | None = None,
        written_ns: int | None = None,
        records: tuple[WowUpRecord, ...] = (),
        skipped: int = 0,
    ) -> ManagerFile:
        return ManagerFile(
            manager="WowUp",
            path=str(path),
            state=state,
            error=error,
            written_ns=written_ns,
            records=records,
            skipped=skipped,
            settings_path=str(base / PREFERENCES_FILE),
            settings_state=settings_state,
            settings_error=settings_error,
            installations=installations,
        )

    try:
        data, written_ns = _read_bounded(path, MAX_RECORDS_BYTES)
        parsed = _json(data)
    except FileNotFoundError:
        return made("missing")
    except _UnreadableError as exc:
        return made("unreadable", str(exc))
    if not isinstance(parsed, dict):
        return made("unreadable", "not a JSON object of addon records")
    records = []
    skipped = 0
    for key, entry in parsed.items():
        # electron-store may keep its own bookkeeping under `__internal__`
        # [verify]; that is no addon.
        if isinstance(entry, dict) and not key.startswith("__"):
            records.append(_record(key, entry))
        else:
            skipped += 1
    return made("read", None, written_ns, tuple(records), skipped)


# ─── whether a TOC's version agrees with the recorded one ────────────────────

_UI_ESCAPE = re.compile(r"\|c[0-9A-Fa-f]{8}|\|r")
_VERSION_PART = re.compile(r"[0-9A-Za-z.]")
_TRAILING_ZEROS = re.compile(r"(?<=\d)(?:\.0+)+$")


def _plain(version: str) -> str:
    text = _UI_ESCAPE.sub("", version).strip().casefold()
    text = text[1:] if text[:1] == "v" and text[1:2].isdigit() else text
    return _TRAILING_ZEROS.sub("", text)


def _boundary_before(long: str, at: int) -> bool:
    return at == 0 or not _VERSION_PART.fullmatch(long[at - 1])


def _boundary_after(long: str, end: int) -> bool:
    if end >= len(long):
        return True
    if _VERSION_PART.fullmatch(long[end]):
        return False
    # `-alpha`, `-beta2`: a pre-release label is part of the version.
    return not (long[end] == "-" and long[end + 1 : end + 2].isalpha())


def _found_inside(short: str, long: str) -> bool:
    start = long.find(short)
    while start != -1:
        if _boundary_before(long, start) and _boundary_after(long, start + len(short)):
            return True
        start = long.find(short, start + 1)
    return False


def versions_agree(toc_version: str, recorded: str) -> bool:
    """Whether a TOC's `## Version:` and a manager's recorded version name
    the same release (the rule in the module docstring)."""
    a, b = _plain(toc_version), _plain(recorded)
    if not a or not b:
        return False
    if a == b:
        return True
    short, long = sorted((a, b), key=len)
    return _found_inside(short, long)


# ─── joining records to the addon folders ────────────────────────────────────


def _toc_values(addon: layout.Addon, key: str) -> tuple[str, ...]:
    found: list[str] = []
    for toc in addon.tocs:
        if not toc.matches_folder or toc.document is None:
            continue
        value = toc.document.get(key)
        if value is not None and value.strip():
            found.append(value.strip())
    return tuple(dict.fromkeys(found))


def _judged(record: WowUpRecord, here: list[layout.Addon]) -> list[layout.Addon]:
    """The folders whose versions are judged: those whose TOC names the
    record's project, when any does; else all of them."""
    directive = _PROJECT_DIRECTIVES.get((record.provider or "").casefold())
    if directive is None or not record.external_id:
        return here
    own = [a for a in here if record.external_id in _toc_values(a, directive)]
    return own or here


def _versions(addons: list[layout.Addon]) -> tuple[TocVersion, ...]:
    held: dict[str, list[str]] = {}
    for addon in addons:
        for version in _toc_values(addon, "Version"):
            held.setdefault(version, []).append(addon.name)
    return tuple(TocVersion(version=v, folders=tuple(f)) for v, f in held.items())


def _inside(location: str, flavor: Path) -> bool:
    """Whether WowUp's `location` is the flavor folder or inside it."""
    try:
        where = Path(os.path.realpath(location))
        root = Path(os.path.realpath(flavor))
    except (OSError, ValueError):
        return False
    if sys.platform in ("darwin", "win32"):
        # Both file systems compare names with case folded by default.
        where = Path(str(where).casefold())
        root = Path(str(root).casefold())
    return where == root or root in where.parents


def _location_folder(location: str) -> str:
    """The folder holding the client WowUp names: the flavor folder."""
    return str(Path(location).parent)


def _match(
    manager: Manager,
    record: WowUpRecord,
    by_name: dict[str, layout.Addon],
    where: Literal["this_folder", "other_folder", "unknown"],
    other: str | None,
) -> RecordMatch:
    here: list[layout.Addon] = []
    missing: list[str] = []
    for name in record.folders:
        addon = by_name.get(fold_name(name))
        if addon is None:
            missing.append(name)
        elif addon not in here:
            here.append(addon)
    provider_known = bool(record.provider) and (
        (record.provider or "").strip().casefold() != _UNKNOWN_PROVIDER
    )
    update = (
        None
        if record.installed_version is None or record.latest_version is None
        else record.installed_version.strip() != record.latest_version.strip()
    )
    common: dict[str, Any] = {
        "manager": manager,
        "record_id": record.id,
        "name": record.name,
        "provider": record.provider,
        "provider_known": provider_known,
        "external_url": record.external_url,
        "installed_version": record.installed_version,
        "latest_version": record.latest_version,
        "update_available": update,
        "ignored": record.ignored,
        "installation": where,
        "installation_folder": other,
    }
    if where == "other_folder":
        return RecordMatch(
            **common,
            folders_here=(),
            folders_missing=(),
            same_name_here=tuple(a.name for a in here),
            toc_versions=(),
            version_check="unknown",
            stale=False,
            stale_why=(),
        )
    judged = _judged(record, here)
    versions = _versions(judged)
    recorded = record.installed_version
    if recorded is None or not recorded.strip() or not versions:
        check: Literal["agrees", "differs", "unknown"] = "unknown"
    elif any(versions_agree(v.version, recorded) for v in versions):
        check = "agrees"
    else:
        check = "differs"
    why: list[StaleWhy] = []
    if record.folders and not here:
        why.append("no_folder_here")
    elif missing:
        why.append("folders_missing")
    if check == "differs":
        why.append("version_differs")
    return RecordMatch(
        **common,
        folders_here=tuple(a.name for a in here),
        folders_missing=tuple(missing),
        same_name_here=(),
        toc_versions=versions,
        version_check=check,
        stale=bool(why),
        stale_why=tuple(why),
    )


def _scope(
    file: ManagerFile, flavor: Path | None
) -> tuple[ManagerScope, dict[str, Literal["this_folder", "other_folder"]], dict[str, str]]:
    """The scope of one file for `flavor`, each known installation's place
    (here or elsewhere) and each other installation's folder."""
    place: dict[str, Literal["this_folder", "other_folder"]] = {}
    folder_of: dict[str, str] = {}
    if file.settings_state == "read" and flavor is not None:
        for inst in file.installations:
            mine = _inside(inst.location, flavor)
            place[inst.id] = "this_folder" if mine else "other_folder"
            if not mine:
                folder_of[inst.id] = _location_folder(inst.location)
    if file.state != "read" or file.settings_state != "read" or flavor is None:
        manages: bool | None = None
    else:
        manages = any(p == "this_folder" for p in place.values())
    counts: dict[str, int] = {}
    by_name = 0
    for record in file.records:
        key = record.installation_id
        if key is not None and place.get(key) == "other_folder":
            counts[key] = counts.get(key, 0) + 1
        elif key is None or key not in place:
            by_name += 1
    others = tuple(
        OtherInstallation(installation_id=k, folder=folder_of[k], records=n)
        for k, n in counts.items()
    )
    scope = ManagerScope(
        manager=file.manager,
        manages=manages,
        installation_ids=tuple(k for k, p in place.items() if p == "this_folder"),
        others=others,
        by_name=by_name,
    )
    return scope, place, folder_of


def match_sources(
    addons: Sequence[layout.Addon],
    files: Sequence[ManagerFile],
    flavor: Path | None = None,
) -> AddonSources:
    """Join the records of every file read to `addons`, the folders under
    `Interface/AddOns/` of the flavor folder `flavor`: those of its own
    installations, and those whose installation is not known by folder
    name; another game folder's records give hints only."""
    by_name: dict[str, layout.Addon] = {}
    for addon in addons:
        by_name.setdefault(fold_name(addon.name), addon)
    matches: list[RecordMatch] = []
    scopes: list[ManagerScope] = []
    claims: dict[str, list[Claim]] = {}
    hints: dict[str, list[Claim]] = {}
    judged = False
    for file in files:
        scope, place, folder_of = _scope(file, flavor)
        scopes.append(scope)
        if file.state == "read" and scope.manages is not False:
            judged = True
        for record in file.records:
            key = record.installation_id
            where: Literal["this_folder", "other_folder", "unknown"] = (
                place[key] if key is not None and key in place else "unknown"
            )
            match = _match(file.manager, record, by_name, where, folder_of.get(key or ""))
            matches.append(match)
            claim = Claim(manager=file.manager, record_id=record.id)
            for folder in match.folders_here:
                claims.setdefault(fold_name(folder), []).append(claim)
            for folder in match.same_name_here:
                hints.setdefault(fold_name(folder), []).append(claim)
    lab = fold_name(LAB_ADDON_NAME)
    folders = []
    for addon in addons:
        key = fold_name(addon.name)
        held = tuple(claims.get(key, ()))
        is_lab = key == lab
        unclaimed = None if not judged else (not held and not is_lab)
        folders.append(
            FolderSource(
                folder=addon.name,
                claims=held,
                same_name_as=tuple(hints.get(key, ())),
                unclaimed=unclaimed,
                lab_addon=is_lab,
            )
        )
    return AddonSources(
        files=tuple(files),
        scopes=tuple(scopes),
        records=tuple(matches),
        folders=tuple(folders),
    )
