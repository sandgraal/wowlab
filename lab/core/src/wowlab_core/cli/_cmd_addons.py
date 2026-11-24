"""`wowlab addons list` and `addons check` (M16-111)."""

import re
import time
from typing import Literal

from wowlab_core import (
    addoncheck,
    addonsource,
    layout,
)
from wowlab_core.cli._common import (
    FlavorOpt,
    JsonOpt,
    NotesOpt,
    RootOpt,
    _emit,
    _handled,
    _home_words,
    _open,
    _Out,
    _player_copy,
    _plural,
    _say,
    _say_notes,
    addons_app,
)

# ─── addons ──────────────────────────────────────────────────────────────────


_UI_ESCAPE = re.compile(r"\|c[0-9A-Fa-f]{8}|\|r")


def _ui_text(text: str) -> str:
    """A TOC title without the client's colour escapes (`|cAARRGGBB`, `|r`)."""
    return _UI_ESCAPE.sub("", text)


class AddonsReport(_Out):
    """`wowlab addons list --json`."""

    flavor_folder: str
    addons: list[layout.Addon]
    # Where each folder came from, as the addon managers wowlab reads
    # recorded it (M22-16): WowUp's records, joined by folder name.
    sources: addonsource.AddonSources


_SOURCES_BY_NAME = (
    "WowUp keeps one list for every game folder it manages, and wowlab matches its "
    "records to this game folder's addons by folder name, so a record WowUp keeps for "
    "another game folder can match a folder of the same name here."
)
_SOURCES_STALE = (
    "A record is out of date when a folder it lists is not here, or when the addon's "
    "TOC files give a version and none of them agrees with the version WowUp installed: "
    "the addon was changed after WowUp last wrote its list, by hand or by another app."
)
_SOURCES_UPDATE = (
    "An update is the one WowUp knew of when it last checked; wowlab fetches nothing, "
    "so a newer one may exist."
)
_SOURCES_OTHERS = (
    "Only WowUp's records are read. An addon installed by hand, or by another app such "
    "as CurseForge's, shows as in no manager's list."
)
_SOURCES_NOTES = [_SOURCES_BY_NAME, _SOURCES_STALE, _SOURCES_UPDATE, _SOURCES_OTHERS]


def _source_date(mtime_ns: int) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(mtime_ns // 1_000_000_000))


def _record_words(match: addonsource.RecordMatch) -> str:
    """What one record says about its addon, for the line of each folder it
    claims."""
    source = f"from {match.provider} through WowUp" if match.provider else "through WowUp"
    recorded = match.installed_version
    if not match.stale:
        words = f"{source}, {recorded}" if recorded else source
        if match.update_available and match.latest_version:
            words += f", update {match.latest_version} available"
        return words
    words = f"{source}, WowUp's record is out of date"
    if recorded:
        words += f": it installed {recorded}"
        if match.version_check == "differs":
            words += f", the TOC says {', '.join(match.toc_versions)}"
    if match.folders_missing:
        words += f"; not here: {', '.join(match.folders_missing)}"
    return words


@addons_app.command("list", short_help="Every folder under Interface/AddOns/ with its TOC files.")
@_handled
def addons_list(
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
    notes_out: NotesOpt = False,
) -> None:
    """Every folder under Interface/AddOns/ with its TOC files, and where it
    came from when the WowUp app installed it: the site, the version, an
    update WowUp knew of, and whether WowUp's record is out of date. A folder
    in no addon manager's list is marked. Reads only; WowUp's own files are
    read, never changed, and nothing is fetched.

    \f
    JSON: AddonsReport."""
    _, chosen, lay = _open(root, flavor)
    addons = list(lay.addons())
    files = (addonsource.read_wowup(),)
    sources = addonsource.match_sources(addons, files)
    report = AddonsReport(flavor_folder=chosen.folder, addons=addons, sources=sources)
    if json_out:
        _emit(report)
        return
    if not addons:
        _say("No addon folders.")
    matches = {(m.manager, m.record_id): m for m in sources.records}
    for a, where in zip(addons, sources.folders, strict=True):
        toc = next((t for t in a.tocs if t.file == a.selected_toc), None)
        doc = toc.document if toc else None
        title = _ui_text(doc.get("Title") or "") if doc else None
        declared = doc.interface if doc else None
        bits = [a.name]
        if title:
            bits.append(f'"{title}"')
        if a.selection == "depends_on_game_type":
            bits.append(f"{len(a.tocs)} TOC files; which one loads depends on the game type")
        elif a.selection == "no_toc":
            bits.append("no TOC file named after the folder; the client does not load it")
        if declared is not None:
            bits.append(f"TOC declares Interface {declared.text}")
        if a.blizzard:
            bits.append("named like a Blizzard addon")
        for claim in where.claims:
            bits.append(_record_words(matches[(claim.manager, claim.record_id)]))
        if where.unclaimed:
            bits.append("in no addon manager's list")
        _say("  ".join(bits))
    elsewhere = [m for m in sources.records if "no_folder_here" in m.stale_why]
    if elsewhere:
        names = ", ".join(m.name or m.record_id for m in elsewhere)
        _say(
            f"WowUp also lists {names}, with none of {'its' if len(elsewhere) == 1 else 'their'} folders here."
        )
    caveat = None
    read = False
    keep: list[str] = []
    for file in sources.files:
        where_file = _home_words(file.path)
        if file.state == "read" and file.written_ns is not None:
            read = True
            _say(
                f"WowUp's list: {where_file}, last written {_source_date(file.written_ns)}, "
                f"{_plural(len(file.records), 'addon')}."
            )
        elif file.state == "unreadable":
            caveat = f"wowlab could not read WowUp's list at {where_file}: {file.error}."
            keep += [where_file, file.error or ""]
    if read or caveat is not None:
        _say_notes(_SOURCES_NOTES, notes_out, caveat, keep=keep)


class AddonsCheckReport(_Out):
    """`wowlab addons check --json`."""

    flavor_folder: str
    check: addoncheck.AddonsCheck
    notes: list[str]  # the caveat, if any, then the standing notes


_CHECK_OUT_OF_DATE = (
    "An interface that differs from the client's makes the client list the addon as out of "
    "date; it does not change what the addon can call. This client's interface is worked out "
    "from its version: major x 10000 + minor x 100 + patch."
)
_CHECK_DEFAULT = (
    "An addon that a character's AddOns.txt does not list is at the client's default for that "
    "character, so a missing name does not mean disabled. When a character has two folders "
    "that each hold an AddOns.txt, which one the client reads is not known [verify]."
)
_CHECK_BLIZZARD = (
    "A Blizzard_ dependency is taken to be one of the client's own addons, which load from "
    "game data, not from Interface/AddOns/ [verify]. wowlab cannot see which ones this client "
    "ships, so it never counts one as missing."
)
_CHECK_DEPS = (
    "Only RequiredDeps and Dependencies are checked; OptionalDeps and other Dep-prefixed "
    "directives (which the client may also treat as required [verify]) are not."
)


def _check_caveat(check: addoncheck.AddonsCheck) -> str | None:
    """The one caveat that applies to this result, if any."""
    if check.client_interface is None:
        return (
            "wowlab cannot tell this client's interface: the flavor's version is not known, "
            "so no addon's interface is judged."
        )
    unread = [t for t in check.addons_txt if t.error is not None]
    if unread:
        return (
            f"{_plural(len(unread), 'AddOns.txt file')} could not be read, so the characters "
            "shown may be incomplete: " + "; ".join(f"{t.path}: {t.error}" for t in unread)
        )
    return None


_TocRole = Literal["only", "several", "another game type"]


def _check_interface(toc: addoncheck.TocCheck, interface: int | None, role: _TocRole) -> str:
    """`role`: the addon's one TOC; one of several; or one of several when
    another one matches this client, so this one is for another game type."""
    if toc.interface_status == "unread":
        return f"not read: {toc.error}"
    if toc.interface_status == "none":
        return "no ## Interface: line"
    if toc.interface_status == "matches":
        return f"{toc.interface}: matches this client"
    if toc.interface_status == "differs":
        if role == "another game type":
            # wowlab's own words, rendered for a player; the interface is the TOC's.
            return f"{toc.interface}: " + _player_copy("taken to be for another game type [verify]")
        if role == "several":
            return f"{toc.interface}: not this client's {interface}"
        return f"{toc.interface}: this client is {interface}, so it is out of date"
    return f"{toc.interface}: this client's is not known"


def _check_toc_lines(
    toc: addoncheck.TocCheck, interface: int | None, indent: str, role: _TocRole
) -> list[str]:
    lines = [f"{indent}interface   {_check_interface(toc, interface, role)}"]
    if toc.interface_status == "unread":
        return lines
    if toc.dependencies:
        words = {
            "installed": "installed",
            "installed_no_toc": "installed, but has no TOC the client loads",
            "missing": "not installed",
            "blizzard": "the client's own",
        }
        deps = ", ".join(f"{d.name} ({words[d.status]})" for d in toc.dependencies)
        lines.append(f"{indent}needs       {deps}")
    else:
        lines.append(f"{indent}needs       nothing")
    lines.append(
        f"{indent}loads       "
        + (
            "on demand: only when another addon or the client asks for it"
            if toc.load_on_demand
            else "at login, when enabled"
        )
    )
    lines.append(f"{indent}version     {_ui_text(toc.version) if toc.version else 'not given'}")
    return lines


def _check_characters(addon: addoncheck.AddonCheck, files: int) -> str:
    if files == 0:
        return "no character folder holds an AddOns.txt"
    if not addon.characters:
        if files == 1:
            return "not listed in the one AddOns.txt found"
        return f"not listed in any of the {files} AddOns.txt files found"
    groups: dict[str, list[str]] = {}
    for c in addon.characters:
        groups.setdefault(c.state, []).append(f"{c.realm_folder}/{c.character_folder}")
    return "; ".join(f"{state} for {', '.join(names)}" for state, names in groups.items())


@addons_app.command(
    "check", short_help="Each addon against this client: interface, dependencies, characters."
)
@_handled
def addons_check(
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
    notes_out: NotesOpt = False,
) -> None:
    """Check each folder under Interface/AddOns/: its TOC's interface against
    this client's, a required dependency that is not installed, whether it
    loads on demand, its version, and which characters' AddOns.txt list it.
    Reads only; nothing is fetched.

    \f
    JSON: AddonsCheckReport."""
    _, chosen, lay = _open(root, flavor)
    check = addoncheck.check_addons(lay, chosen.version)
    caveat = _check_caveat(check)
    standing = [_CHECK_OUT_OF_DATE, _CHECK_DEFAULT, _CHECK_BLIZZARD, _CHECK_DEPS]
    report = AddonsCheckReport(
        flavor_folder=chosen.folder,
        check=check,
        notes=[caveat, *standing] if caveat is not None else standing,
    )
    if json_out:
        _emit(report)
        return
    addons = check.addons
    if not addons:
        _say("No addon folders.")
    else:
        missing = sum(1 for a in addons if a.missing_dependencies)
        differs = sum(1 for a in addons if a.out_of_date)
        none_for_client = sum(1 for a in addons if a.no_toc_for_client)
        unloaded = sum(1 for a in addons if a.selection == "no_toc")
        unloadable = sum(1 for a in addons if a.unloadable_dependencies)
        bits = [f"{missing} missing a required addon", f"{differs} out of date"]
        if unloadable:
            bits.append(f"{unloadable} needing an addon that has no TOC the client loads")
        if none_for_client:
            bits.append(f"{none_for_client} with several TOCs, none for this client's interface")
        if unloaded:
            bits.append(f"{unloaded} with no TOC the client loads")
        interface = (
            f"this client's interface is {check.client_interface}"
            if check.client_interface is not None
            else "this client's interface is not known"
        )
        _say(f"{_plural(len(addons), 'addon')}: {', '.join(bits)}; {interface}.")
    files = len(check.addons_txt)
    for a in addons:
        _say()
        head = [a.name]
        titled = next((t.title for t in a.tocs if t.title), None)
        if titled:
            head.append(f'"{_ui_text(titled)}"')
        if a.blizzard:
            head.append("named like a Blizzard addon")
        _say("  ".join(head))
        if a.selection == "no_toc":
            _say("  no TOC file named after the folder; the client does not load it")
        elif len(a.tocs) == 1:
            for line in _check_toc_lines(a.tocs[0], check.client_interface, "  ", "only"):
                _say(line)
        else:
            _say(f"  {len(a.tocs)} TOC files; which one loads depends on the game type")
            one_match = sum(1 for t in a.tocs if t.interface_status == "matches") == 1
            for toc in a.tocs:
                role: _TocRole = "another game type" if one_match else "several"
                _say(f"  {toc.file}")
                for line in _check_toc_lines(toc, check.client_interface, "    ", role):
                    _say(line)
        _say(f"  characters  {_check_characters(a, files)}")
    if addons:
        _say()
    _say_notes(standing, notes_out, caveat, keep=[t.path for t in check.addons_txt])
