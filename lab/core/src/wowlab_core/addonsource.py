"""addonsource: where each installed addon came from, as an addon manager
recorded it (M22-16; LAB_IDEAS **addon-provenance**, its first slice).

The one manager read so far is WowUp. It keeps the addons it installed in
`addons.json` under its own user-data folder, outside the install (on macOS
`~/Library/Application Support/WowUp/`, seen 2026-11-23; on Windows
`%APPDATA%\\WowUp\\` and on Linux `~/.config/WowUp/`, Electron's rule,
**[verify]**). The folder is Electron's `userData` rule per OS
(`wowup_folder`: `platformdirs`' roaming config folder, except on macOS,
where Electron ignores `XDG_CONFIG_HOME`); nothing about a flavor is known
here (L6). Only `addons.json` is opened, read-only (L1).
`sensitive.json`, which holds WowUp's own keys, is never opened.

The file is electron-store JSON: one object keyed by WowUp's record id,
each value one installed addon (`docs/LAB_FORMATS.md` §13). The fields read
are `providerName` (Wago, CurseForge, GitHub, …), `externalId`,
`externalUrl`, `installedVersion`, `latestVersion`, `installedFolderList`
(else the comma-joined `installedFolders`), `clientType`, `installationId`,
`installedAt` and `isIgnored`. Everything else is left alone, and
`downloadUrl`, a server-issued link, is never read into a model.

`match_sources` joins the records to the folders under
`Interface/AddOns/` by folder name, ASCII letters folded (the rule
`wtfconfig.fold_name` and `addoncheck` use). WowUp keeps one list for
every game folder it manages (`installationId`, `clientType`); which game
folder a record belongs to is in WowUp's own preferences, which are not
read, so a record of another game folder can match a folder of the same
name here. A record is **stale** (it no longer describes what is on disk)
when none of its folders is here, when one of them is missing, or when the
present folders' TOCs carry a `## Version:` and none agrees with the
version WowUp recorded. "Agrees" is equal (case folded, a leading `v`
dropped), or one found inside the other between characters that are not a
letter, a digit or a dot (`Pawn 2.13.16` and `2.13.16`,
`DBM-Dungeons-r264` and `r264`). An addon's folders may carry different
versions (DBM's voice pack carries a commit id), so one agreeing folder is
enough. A folder that no record read claims is **unclaimed**: installed by
hand, by another app, or by a manager wowlab does not read; that is said
only when at least one manager's file was read. The lab-addon is wowlab's
own and is never called unclaimed.
"""

from __future__ import annotations

import json
import os
import re
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
    "MAX_RECORDS_BYTES",
    "RECORDS_FILE",
    "WOWUP_APP",
    "AddonSources",
    "Claim",
    "FolderSource",
    "ManagerFile",
    "RecordMatch",
    "WowUpRecord",
    "match_sources",
    "read_wowup",
    "versions_agree",
    "wowup_folder",
]

WOWUP_APP = "WowUp"  # the app's user-data folder name
RECORDS_FILE = "addons.json"
# The owner's file holds 6 records in 11.6 KB; a file past this is not read.
MAX_RECORDS_BYTES = 16 * 1024 * 1024

Manager = Literal["WowUp"]
StaleWhy = Literal["no_folder_here", "folders_missing", "version_differs"]


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


class ManagerFile(_Model):
    """What wowlab found where a manager keeps its records."""

    manager: Manager
    path: str  # the file wowlab looked for
    # `read`: parsed; `missing`: no such file; `unreadable`: there, but not
    # read (`error` says why)
    state: Literal["read", "missing", "unreadable"]
    error: str | None
    written_ns: int | None  # the file's modification time, when it was read
    records: tuple[WowUpRecord, ...]
    skipped: int  # entries that were not an object, so not records


class RecordMatch(_Model):
    """One record joined to this flavor's addon folders."""

    manager: Manager
    record_id: str
    name: str | None
    provider: str | None
    external_url: str | None
    installed_version: str | None
    latest_version: str | None
    # WowUp's own view when it last checked: its latest version differs from
    # the one it installed. None when either is not recorded.
    update_available: bool | None
    ignored: bool | None
    folders_here: tuple[FsText, ...]  # claimed folders present, as named on disk
    folders_missing: tuple[str, ...]  # claimed folders not present, as recorded
    # Each distinct `## Version:` of a TOC named after a present folder.
    toc_versions: tuple[FsText, ...]
    # `agrees`: a present folder's TOC version agrees with the recorded one;
    # `differs`: TOCs carry versions and none agrees; `unknown`: no TOC
    # carries one, or no version was recorded.
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
    # True: no record of any manager file read claims it. None: no manager
    # file was read, so wowlab cannot tell. Never True for the lab-addon.
    unclaimed: bool | None
    lab_addon: bool


class AddonSources(_Model):
    files: tuple[ManagerFile, ...]
    records: tuple[RecordMatch, ...]  # in file order
    folders: tuple[FolderSource, ...]  # in the order of the addons given


# ─── finding and reading WowUp's records ─────────────────────────────────────


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


def _unreadable(path: Path, why: str) -> ManagerFile:
    return ManagerFile(
        manager="WowUp",
        path=str(path),
        state="unreadable",
        error=why,
        written_ns=None,
        records=(),
        skipped=0,
    )


def read_wowup(folder: Path | None = None) -> ManagerFile:
    """WowUp's `addons.json` in `folder` (default: `wowup_folder()`), read
    only. A missing file is `missing`; a file that is not JSON, is past
    `MAX_RECORDS_BYTES`, or is not an object is `unreadable`, with why."""
    path = (wowup_folder() if folder is None else folder) / RECORDS_FILE
    try:
        with path.open("rb") as handle:
            facts = os.fstat(handle.fileno())
            if facts.st_size > MAX_RECORDS_BYTES:
                return _unreadable(path, f"larger than {MAX_RECORDS_BYTES} bytes")
            data = handle.read(MAX_RECORDS_BYTES + 1)
    except FileNotFoundError:
        return ManagerFile(
            manager="WowUp",
            path=str(path),
            state="missing",
            error=None,
            written_ns=None,
            records=(),
            skipped=0,
        )
    except OSError as exc:
        return _unreadable(path, exc.strerror or type(exc).__name__)
    if len(data) > MAX_RECORDS_BYTES:
        return _unreadable(path, f"larger than {MAX_RECORDS_BYTES} bytes")
    try:
        parsed = json.loads(data.decode("utf-8-sig"))
    except UnicodeDecodeError:
        return _unreadable(path, "not UTF-8 text")
    except RecursionError:
        return _unreadable(path, "nested too deeply")
    except ValueError as exc:
        return _unreadable(path, f"not JSON: {exc}")
    if not isinstance(parsed, dict):
        return _unreadable(path, "not a JSON object of addon records")
    records = []
    skipped = 0
    for key, entry in parsed.items():
        # electron-store keeps its own bookkeeping under `__internal__`
        # when an app migrates its store; that is no addon.
        if isinstance(entry, dict) and not key.startswith("__"):
            records.append(_record(key, entry))
        else:
            skipped += 1
    return ManagerFile(
        manager="WowUp",
        path=str(path),
        state="read",
        error=None,
        written_ns=facts.st_mtime_ns,
        records=tuple(records),
        skipped=skipped,
    )


# ─── joining records to the addon folders ────────────────────────────────────

_UI_ESCAPE = re.compile(r"\|c[0-9A-Fa-f]{8}|\|r")
_VERSION_PART = re.compile(r"[0-9A-Za-z.]")


def _plain(version: str) -> str:
    text = _UI_ESCAPE.sub("", version).strip().casefold()
    return text[1:] if text[:1] == "v" and text[1:2].isdigit() else text


def _found_inside(short: str, long: str) -> bool:
    start = long.find(short)
    while start != -1:
        end = start + len(short)
        before = long[start - 1] if start else ""
        after = long[end] if end < len(long) else ""
        if not _VERSION_PART.fullmatch(before or "-") and not _VERSION_PART.fullmatch(after or "-"):
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


def _toc_versions(addon: layout.Addon) -> tuple[str, ...]:
    found: list[str] = []
    for toc in addon.tocs:
        if not toc.matches_folder or toc.document is None:
            continue
        value = toc.document.get("Version")
        if value is not None and value.strip():
            found.append(value.strip())
    return tuple(dict.fromkeys(found))


def _match(manager: Manager, record: WowUpRecord, by_name: dict[str, layout.Addon]) -> RecordMatch:
    here: list[layout.Addon] = []
    missing: list[str] = []
    for name in record.folders:
        addon = by_name.get(fold_name(name))
        if addon is None:
            missing.append(name)
        elif addon not in here:
            here.append(addon)
    per_folder = [_toc_versions(a) for a in here]
    versions = tuple(dict.fromkeys(v for vs in per_folder for v in vs))
    recorded = record.installed_version
    if recorded is None or not recorded.strip() or not versions:
        check: Literal["agrees", "differs", "unknown"] = "unknown"
    elif any(versions_agree(v, recorded) for v in versions):
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
    update = (
        None
        if record.installed_version is None or record.latest_version is None
        else record.installed_version.strip() != record.latest_version.strip()
    )
    return RecordMatch(
        manager=manager,
        record_id=record.id,
        name=record.name,
        provider=record.provider,
        external_url=record.external_url,
        installed_version=record.installed_version,
        latest_version=record.latest_version,
        update_available=update,
        ignored=record.ignored,
        folders_here=tuple(a.name for a in here),
        folders_missing=tuple(missing),
        toc_versions=versions,
        version_check=check,
        stale=bool(why),
        stale_why=tuple(why),
    )


def match_sources(addons: Sequence[layout.Addon], files: Sequence[ManagerFile]) -> AddonSources:
    """Join every record of every file read to `addons` (one flavor's
    `Interface/AddOns/` folders) by folder name."""
    by_name: dict[str, layout.Addon] = {}
    for addon in addons:
        by_name.setdefault(fold_name(addon.name), addon)
    matches: list[RecordMatch] = []
    claims: dict[str, list[Claim]] = {}
    for file in files:
        for record in file.records:
            match = _match(file.manager, record, by_name)
            matches.append(match)
            for folder in match.folders_here:
                claim = Claim(manager=file.manager, record_id=record.id)
                claims.setdefault(fold_name(folder), []).append(claim)
    any_read = any(f.state == "read" for f in files)
    lab = fold_name(LAB_ADDON_NAME)
    folders = []
    for addon in addons:
        key = fold_name(addon.name)
        held = tuple(claims.get(key, ()))
        is_lab = key == lab
        unclaimed = None if not any_read else (not held and not is_lab)
        folders.append(
            FolderSource(folder=addon.name, claims=held, unclaimed=unclaimed, lab_addon=is_lab)
        )
    return AddonSources(files=tuple(files), records=tuple(matches), folders=tuple(folders))
