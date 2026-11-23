"""`wowlab live recap` (M22-05): the latest match the combat logs show, and
what changed since the character's save before the newest one.

It reads only: the install (L1), the snapshot store and wowlab's crew
database, which it never syncs or changes. The Live view of `wowlab ui` runs
it when the files it watches change (`live.Watcher`). Every name, realm,
zone and item name is other people's or the game's text: printed only
through `_say`, which escapes control characters; `--json` is ASCII-escaped
as everywhere.
"""

import os
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from wowlab_core import (
    charhistory,
    combatlog,
    crewbook,
    crewdb,
    gear,
    labaddon,
    layout,
    live,
    luadata,
    snapshot,
    worldpvp,
)
from wowlab_core.cli._cmd_char import _history_item, _history_names
from wowlab_core.cli._cmd_crew import _owner_guids
from wowlab_core.cli._cmd_log import _logs_dir
from wowlab_core.cli._common import (
    AccountOpt,
    CharacterOpt,
    CliError,
    FlavorOpt,
    JsonOpt,
    NotesOpt,
    RootOpt,
    _emit,
    _handled,
    _is_lab_file,
    _lab_char_file,
    _listed,
    _local_time,
    _open_char,
    _Out,
    _plural,
    _say,
    _say_notes,
    _select_account,
    live_app,
)
from wowlab_core.errors import WowlabError

_LIVE_READ_ERRORS = (WowlabError, luadata.LuaDataError, OSError)
_LIVE_MAX_LOGS = 1000  # the newest logs looked at, as `crew scan-log` looks

_LIVE_NOTES = [
    "The game saves addon data, the crew book included, only at /reload or logout. The "
    "match recap reads the combat log, which the game writes as you play, a little at a time.",
    "A match shows its fights only when the combat log was on during it. The lab-addon turns "
    "the log off in instances; type /combatlog in a battleground to log it yourself.",
    "Gear, currency and level changes compare the newest save with the one before it that "
    "your backups hold: take a backup after you play (wowlab snap create) to compare the next "
    "session with it.",
    "Not yet confirmed: how a battleground and its end show in the combat log, the arena's "
    "result line, and the layout of Syndicator's saved file.",
]


class LiveRecapReport(_Out):
    """`wowlab live recap --json`: the latest match the newest combat logs
    show (`match`, null when they show none; `match_words` says why) and
    what changed since the save before the newest of one character's
    lab-addon record (`session`, null when there is none; `session_words`
    says why), for one game account. `characters` counts your characters
    the combat log can name (their game id is in the lab-addon's file);
    `logs` how many combat logs were looked at. `lines` are the lines the
    text output prints."""

    flavor_folder: str
    account: str
    character: str | None  # "<realm folder>/<character folder>"
    chosen_by: Literal["--character", "latest"] | None
    characters: int
    logs: int
    match: live.MatchRecap | None
    match_words: str | None
    session: live.SessionRecap | None
    session_words: str | None
    lines: list[str]
    notes: list[str]


def _live_book(lay: layout.Layout, account: str) -> tuple[list[crewbook.CrewMatch], str | None]:
    """The crew book's matches of the account, and why there are none."""
    files = [f for f in lay.saved_variables("account") if f.account == account and _is_lab_file(f)]
    if not files:
        return [], None
    try:
        book = crewbook.parse_book(_listed(labaddon.read_file_bytes, lay, files[0].path))
    except _LIVE_READ_ERRORS as exc:
        return [], f"the crew book could not be read: {exc}"
    return list(book.matches), None


def _live_syndicator(lay: layout.Layout, account: str) -> layout.SavedVariablesFile | None:
    for f in lay.saved_variables("account"):
        if (
            f.account == account
            and not f.backup
            and f.addon is not None
            and f.addon.casefold() == "syndicator"
        ):
            return f
    return None


def _live_names(target: layout.SavedVariablesFile) -> list[str]:
    """How Syndicator may key the character: `First Second` for a
    `First-Second` folder, the folder name, and `Name-Realm`."""
    folder = target.character_folder or ""
    out = [folder]
    if "-" in folder:
        out.append(folder.replace("-", " ", 1))
    if target.realm_folder:
        out.append(f"{folder}-{target.realm_folder}")
    return out


def _live_items_before(
    store: snapshot.SnapshotStore, snapshot_id: str | None, path: str, names: Sequence[str]
) -> list[live.ItemCount] | str | None:
    if snapshot_id is None:
        return None
    try:
        manifest = store.show(snapshot_id)
    except _LIVE_READ_ERRORS as exc:
        return f"the snapshot with the earlier save could not be read: {exc}"
    entry = manifest.entry(path)
    if entry is None or entry.kind != "file" or entry.sha256 is None:
        return None
    if entry.size > luadata.MAX_FILE_BYTES:
        return "the earlier copy of Syndicator's file is too large to read"
    try:
        data = store.read_object(entry.sha256, size=entry.size)
    except _LIVE_READ_ERRORS as exc:
        return f"the earlier copy of Syndicator's file could not be read: {exc}"
    return live.syndicator_items(data, names)


def _live_matches(n: int) -> str:
    return f"{n} match" if n == 1 else f"{n} matches"


def _live_when(seconds: int | None) -> str:
    return _local_time(seconds * 1_000_000_000) if seconds is not None else "an unknown time"


def _live_match_lines(m: live.MatchRecap) -> list[str]:
    where = m.zone or "a place the log did not name"
    ended = {
        "left": "over: you left it",
        "match_end": "over: the log shows its end",
        "new_log": "over: a newer combat log began after it",
        None: "still going on",
    }[m.ended_by]
    kind = "Arena" if m.arena else "Match"
    lines = [f"{kind}: {where}, from {_live_when(m.start)} to {_live_when(m.end)}, {ended}."]
    if m.result is not None:
        source = "the combat log" if m.result_from == "log" else "the crew book"
        lines.append(f"  Result: {m.result} (from {source}).")
    elif m.over:
        lines.append(
            "  Result: not in the combat log; the crew book saves it at your next /reload or "
            "logout."
        )
    lines.append(f"  Your killing blows: {m.killing_blows}; your deaths: {m.deaths}.")
    if not m.opponents:
        lines.append(
            "  The combat log shows no fight with another player there: it was probably off."
        )
    else:
        lines.append(f"  Who you fought ({len(m.opponents)}):")
        for o in m.opponents:
            how = [_plural(o.hits, "hit")]
            if o.killed_by_you:
                how.append("you killed them")
            if o.killed_you:
                how.append("they killed you")
            lines.append(f"    {o.key}: {', '.join(how)}")
    if m.book_foes:
        lines.append(f"  On the other team, by the scoreboard: {', '.join(m.book_foes)}.")
    return lines


def _live_session_lines(s: live.SessionRecap) -> list[str]:
    if s.since_ns is None:
        return [
            f"Since your last save ({_local_time(s.saved_at_ns)}): "
            f"{s.not_compared or 'nothing to compare'}."
        ]
    snap = f" (backup {s.since_snapshot})" if s.since_snapshot else ""
    lines = [
        f"Since your save of {_local_time(s.since_ns)}{snap}, to {_local_time(s.saved_at_ns)}:"
    ]
    if s.level_before is not None and s.level_after is not None:
        if s.level_after != s.level_before:
            lines.append(f"  Level {s.level_before} to {s.level_after}.")
        else:
            lines.append(f"  Level {s.level_after}, no change.")
    if s.gear_not_compared is not None:
        lines.append(f"  Gear not compared: {s.gear_not_compared}.")
    for g in s.gear_changes:
        slot = g.slot_name or f"slot {g.slot}"
        lines.append(f"  Gear, {slot}: {_history_item(g.before)} to {_history_item(g.after)}")
    if s.currencies_not_compared is not None:
        lines.append(f"  Currencies not compared: {s.currencies_not_compared}.")
    for c in s.currency_changes:
        name = c.name or f"currency {c.id}"
        lines.append(
            f"  {name}: {c.before if c.before is not None else 'none'} to "
            f"{c.after if c.after is not None else 'none'}"
        )
    if not s.gear_changes and not s.currency_changes and s.gear_not_compared is None:
        lines.append("  No gear or currency changes.")
    if s.items_note is not None:
        lines.append(f"  New items: {s.items_note}.")
    elif s.new_items:
        lines.append(f"  New items, from Syndicator ({len(s.new_items)}):")
        for n in s.new_items:
            name = n.name or f"item {n.item_id}"
            lines.append(f"    {name}: {n.before} to {n.after}")
    else:
        lines.append("  No new items in Syndicator's list.")
    if s.matches:
        won = sum(1 for m in s.matches if m.won is True)
        lost = sum(1 for m in s.matches if m.won is False)
        lines.append(f"  {_live_matches(len(s.matches))}: {won} won, {lost} lost.")
    if s.opponents:
        lines.append(f"  Players met against you ({len(s.opponents)}):")
        for o in s.opponents:
            how = []
            if o.matches:
                how.append(_live_matches(o.matches))
            if o.encounters:
                how.append(f"{_plural(o.encounters, 'fight')} in the open world")
            if o.killed_by_you:
                how.append("you killed them")
            if o.killed_you:
                how.append("they killed you")
            lines.append(f"    {o.key}: {', '.join(how)}")
    else:
        lines.append("  No players met against you.")
    if s.world_note is not None:
        lines.append(f"  Not read: {s.world_note}.")
    return lines


@live_app.command(
    "recap",
    short_help="Your latest match, and what changed since your last save.",
)
@_handled
def live_recap(
    character: CharacterOpt = None,
    account: AccountOpt = None,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
    notes_out: NotesOpt = False,
) -> None:
    """The latest battleground or arena the combat log shows: who you
    fought, your killing blows and deaths, and the result when the log or
    the crew book has it. Then what changed since the character's save
    before the newest one that wowlab has: level, gear and currencies from
    the lab-addon's record, new items from Syndicator, and the players you
    met. Without --character, the character whose lab-addon file was
    written last. Reads only; the Live view of wowlab ui shows the same and
    refreshes it by itself.

    \f
    JSON: LiveRecapReport."""
    _, chosen, lay = _open_char(root, flavor, account)
    acct = _select_account(lay, account)
    owners, _without = _owner_guids(lay, acct.folder)
    folder = _logs_dir(lay)
    logs = combatlog.find_logs(folder)[-_LIVE_MAX_LOGS:] if folder is not None else []
    matches, book_note = _live_book(lay, acct.folder)
    maps = crewdb.world_maps(chosen.folder, acct.folder)
    maps |= {m.map_id for m in matches if m.map_id is not None}

    match: live.MatchRecap | None = None
    match_words: str | None
    if not owners:
        match_words = (
            "wowlab does not know which characters are yours yet: log in once with the "
            "lab-addon on, then /reload."
        )
    elif not logs:
        match_words = "No combat log: type /combatlog in game to start one."
    else:
        match = live.match_recap(logs, owners, pvp_maps=maps, book=matches)
        match_words = None if match is not None else "The combat logs show no match."

    files = [f for f in lay.saved_variables() if f.account == acct.folder and _is_lab_file(f)]
    session: live.SessionRecap | None = None
    session_words: str | None = None
    target: layout.SavedVariablesFile | None = None
    how: Literal["--character", "latest"] | None = None
    try:
        target, how, _tie = _lab_char_file(acct, files, character)
    except CliError as exc:
        if character is not None:
            raise
        session_words = str(exc)
    if target is not None:
        session, session_words = _live_session(
            lay, chosen.folder, chosen.version, acct.folder, target, logs, owners, maps, matches
        )
        if book_note is not None and session is not None:
            session_words = book_note

    lines: list[str] = []
    lines += _live_match_lines(match) if match is not None else [match_words or ""]
    lines.append("")
    if session is not None:
        lines += _live_session_lines(session)
    if session_words is not None:
        lines.append(session_words)
    report = LiveRecapReport(
        flavor_folder=chosen.folder,
        account=acct.folder,
        character=f"{target.realm_folder}/{target.character_folder}" if target else None,
        chosen_by=how,
        characters=len(owners),
        logs=len(logs),
        match=match,
        match_words=match_words,
        session=session,
        session_words=session_words,
        lines=lines,
        notes=_LIVE_NOTES,
    )
    if json_out:
        _emit(report)
        return
    if target is not None:
        _say(f"Character: {report.character} in account {acct.folder}")
    for line in lines:
        _say(line)
    _say_notes(_LIVE_NOTES, notes_out)


def _live_session(
    lay: layout.Layout,
    flavor_folder: str,
    version: str | None,
    account: str,
    target: layout.SavedVariablesFile,
    logs: Sequence[Path],
    owners: dict[str, str | None],
    maps: set[int],
    matches: Sequence[crewbook.CrewMatch],
) -> tuple[live.SessionRecap | None, str | None]:
    try:
        table = gear.load_slot_table()
    except gear.SlotTableError as exc:
        raise CliError(f"the slot table cannot be used: {exc}") from exc
    live_path = lay.flavor_path / target.path
    try:
        live_st: os.stat_result | None = os.lstat(live_path)
    except OSError:
        live_st = None
    live_ns = live_st.st_mtime_ns if live_st is not None else target.mtime_ns

    def read_live() -> bytes:
        try:
            return snapshot.read_regular_file(
                live_path, limit=luadata.MAX_FILE_BYTES, expect=live_st, root=lay.install_root
            )
        except snapshot.LinkedPathError as exc:
            raise exc.changed_while_read(lay.flavor_path) from exc

    spec_name, currency_name = _history_names(version)
    store = snapshot.SnapshotStore()
    history = charhistory.collect(
        store,
        f"{flavor_folder}/{target.path}",
        live=(live_ns, read_live),
        table=table.table,
        spec_name=spec_name,
        currency_name=currency_name,
    )
    if not history.rows:
        return None, "No save of this character could be read."
    before = live.earlier_row(history)

    names = _live_names(target)
    syn = _live_syndicator(lay, account)
    after: list[live.ItemCount] | str | None = None
    earlier: list[live.ItemCount] | str | None = None
    if syn is not None:
        try:
            after = live.syndicator_items(
                _listed(snapshot.read_regular_file, lay, syn.path, limit=luadata.MAX_FILE_BYTES),
                names,
            )
        except _LIVE_READ_ERRORS as exc:
            after = f"Syndicator's file could not be read: {exc}"
        if before is not None and before.snapshots:
            earlier = _live_items_before(
                store, before.snapshots[-1], f"{flavor_folder}/{syn.path}", names
            )

    world: list[worldpvp.WorldEncounter] = []
    world_note: str | None = None
    if before is not None and owners:
        world, world_note = live.world_since(logs, owners, before.saved_at_ns, pvp_maps=maps)
    elif before is not None:
        world_note = "the open world: wowlab does not know which characters are yours yet"
    recap = live.session_recap(
        history,
        items_before=earlier,
        items_after=after,
        book=matches,
        world=world,
        world_note=world_note,
    )
    return recap, None
