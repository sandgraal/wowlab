"""`wowlab spy list` and `spy show` (M22-01): what the Spy addon saved.

Both read every `Spy.lua` of the flavor folder (or of every flavor folder,
when the install has several and none is named) through `wowlab_core.spy`,
which parses them with `luadata` as data, and write nothing anywhere. A file
that cannot be read is named on stderr and makes the command exit 1 after
the rest is printed. Every name, guild, zone, note and tag in Spy's files is
other people's text (or the player's own note): it is printed only through
`_say`, which escapes control characters, never through the player-copy
rewriting wowlab's own sentences go through; `--json` is ASCII-escaped as
everywhere.
"""

import time
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Literal

import typer

from wowlab_core import install, layout, spy
from wowlab_core.cli._common import (
    EXIT_ERROR,
    EXIT_USAGE,
    AccountOpt,
    CliError,
    FlavorOpt,
    JsonOpt,
    NotesOpt,
    RootOpt,
    _discover,
    _emit,
    _given_flavor,
    _handled,
    _Out,
    _plural,
    _say,
    _say_err,
    _say_notes,
    _select_flavor,
    spy_app,
)

# ─── spy ─────────────────────────────────────────────────────────────────────

_SPY_AS_OF = "as of your last logout or /reload"
_SPY_NOTES = [
    "Spy saves its lists when you log out or type /reload, and the game overwrites a change "
    "made to them while it runs.",
    "Spy keeps hostile players only. It drops a player not seen for 90 days by default "
    "(Spy's purge option), unless it counted a killing blow or a death against them or they "
    "are on the kill-on-sight list (Spy's option to purge counted players drops those and "
    "kill-on-sight players alike). It also drops a player once it sees them as friendly, "
    "unless it counted a killing blow or a death against them.",
    "Spy records your own character's position, not theirs. The time and the place can be "
    "from different moments: Spy records the place when it first notices a player, and not "
    "when the game gives no position; and inside an instance it writes the instance's name "
    "but leaves the map position as it was, so the place can be older than the time.",
    "Hostile means the game called them hostile to your character when Spy last looked, not "
    "their faction: a duel, mind control or a free-for-all area can make your own faction "
    "hostile, and marking a player kill on sight, or a row another Spy user shared, sets it "
    "too.",
    "Each of your characters has its own Spy list: a player two of them saw is listed once "
    "for each.",
    "The class is the game's internal name for it (MAGE, PRIEST, ...), as Spy saved it. A "
    "level followed by ? is one Spy guessed.",
    "Spy counts your killing blows on a player, and the deaths it puts on them, some guessed "
    "from who had you targeted; neither appears until it has counted one.",
    "Dates are in this computer's time zone.",
]
# `spy list --sort`.
SpySort = Literal["last", "name", "level"]
_SPY_TAG_WORDS = {"spare": "spare (do not kill)", "rival": "rival (a fair fight)"}
# What each of Spy's tables is, for an entry that could not be read.
_SPY_TABLE_WORDS = {
    "PlayerData": "the player",
    "KOSData": "the kill-on-sight entry",
    "IgnoreData": "the ignore entry",
    "Encounters": "the fight",
    "kosData": "the shared kill-on-sight entry",
    "playerNotes": "the note on",
    "playerTags": "the tag on",
}
_SPY_RESULT_WORDS = {"win": "won", "loss": "lost", "probable_win": "probably won"}
# How Spy counted a fight (`Encounters.lua`), in words.
_SPY_HOW_WORDS = {
    "kill": "your killing blow",
    "pet": "your pet's target died",
    "group": "a group member's kill",
    "probable": "probable: they died while you fought them",
    "recap": "named by the death recap",
    "targeting": "guessed: the only enemy targeting you",
}
# The caveat when a character takes rows other Spy users share (W2).
_SPY_SHARED_CAVEAT = (
    "Spy takes the players other Spy users share for {who}, so some places and times may "
    "come from other Spy users."
)


class SpyFileRow(_Out):
    """One `Spy.lua` read (or that could not be read): its flavor folder,
    the file (relative to the flavor folder), the game account, the
    character (`<realm or digits folder>/<character folder>`, None for the
    account file), whether it holds Spy's table (`present`), Spy's data
    version (character files), the warning about that version, the error
    when it could not be read, and how many players, kill-on-sight entries,
    ignored players and fights it holds."""

    flavor_folder: str
    file: str
    account: str
    character: str | None
    scope: Literal["account", "character"]
    present: bool | None
    version: str | None = None
    warning: str | None = None
    error: str | None = None
    players: int = 0
    kos: int = 0
    ignored: int = 0
    encounters: int = 0


class SpyListReport(_Out):
    """`wowlab spy list --json`: the players Spy saw, as of the last logout
    or /reload. `files` lists every `Spy.lua` looked at; `total` counts
    the player rows of the characters listed (one row per character that
    saw the player), `distinct` the players among them (by name, case,
    spaces and hyphens ignored);
    `players` are the rows shown, filtered and in the order the text lists
    them, each with the file and character it came from; `kos` and
    `ignored` are every kill-on-sight and ignore entry of the characters
    shown; `shared_kos`, `notes` and `tags` are the account files' shared
    kill-on-sight lists and your own notes and tags; `unread` lists each
    entry that does not fit what Spy saves. `warnings` are the version
    warnings (an unknown or missing Spy data version, read as the known one
    is). `caveat` says when a character listed takes the rows other Spy
    users share. `words` is the sentence the text output opens with."""

    flavor_folders: list[str]
    files: list[SpyFileRow]
    total: int
    distinct: int
    sort: SpySort
    search: str | None
    character: str | None
    kos_only: bool
    enemies: bool
    players: list[spy.SpyPlayer]
    kos: list[spy.SpyKos]
    ignored: list[spy.SpyIgnored]
    shared_kos: list[spy.SpySharedKos]
    player_notes: list[spy.SpyNote]
    tags: list[spy.SpyPlayerTag]
    unread: list[spy.SpyUnread]
    warnings: list[str]
    caveat: str | None = None
    words: str
    notes: list[str]


class SpyShowReport(_Out):
    """`wowlab spy show --json`: everything Spy saved about one player, the
    name as typed (`typed`): the row of each character that saw them
    (`players`), their kill-on-sight and ignore entries, the fights Spy
    counted against them (`encounters`), the shared kill-on-sight entries,
    your notes and tags. `caveat` says when a character that saw them
    takes the rows other Spy users share. `lines` are the lines the text
    output prints."""

    typed: str
    files: list[SpyFileRow]
    players: list[spy.SpyPlayer]
    kos: list[spy.SpyKos]
    ignored: list[spy.SpyIgnored]
    encounters: list[spy.SpyEncounter]
    shared_kos: list[spy.SpySharedKos]
    player_notes: list[spy.SpyNote]
    tags: list[spy.SpyPlayerTag]
    warnings: list[str]
    caveat: str | None = None
    lines: list[str]
    notes: list[str]


class _SpyRead:
    """Every Spy file in reach, read."""

    def __init__(self, folders: list[str], surveys: list[spy.SpySurvey]) -> None:
        self.folders = folders
        self.accounts = [a for s in surveys for a in s.accounts]
        self.characters = [c for s in surveys for c in s.characters]
        self.errors = [e for s in surveys for e in s.errors]

    def files(self) -> list[SpyFileRow]:
        rows: list[SpyFileRow] = []
        for a in self.accounts:
            rows.append(
                _spy_row(
                    a.source,
                    "account",
                    present=a.present,
                    kos=len(a.shared_kos),
                )
            )
        for c in self.characters:
            rows.append(
                _spy_row(
                    c.source,
                    "character",
                    present=c.present,
                    version=c.version,
                    warning=c.warning,
                    players=len(c.players),
                    kos=len(c.kos),
                    ignored=len(c.ignored),
                    encounters=len(c.encounters),
                )
            )
        for e in self.errors:
            scope: Literal["account", "character"] = (
                "account" if e.source.character is None else "character"
            )
            rows.append(_spy_row(e.source, scope, present=None, error=e.error))
        return sorted(rows, key=lambda r: (r.flavor_folder, r.scope != "account", r.file))

    def warnings(self) -> list[str]:
        """Each version warning, after the character it is about."""
        return [
            f"{_spy_label(c.source.character)}: {c.warning}"
            for c in self.characters
            if c.warning is not None
        ]

    def unread(self) -> list[spy.SpyUnread]:
        return [u for c in self.characters for u in c.unread] + [
            u for a in self.accounts for u in a.unread
        ]


def _spy_row(
    source: spy.SpySource,
    scope: Literal["account", "character"],
    **fields: object,
) -> SpyFileRow:
    return SpyFileRow.model_validate(
        {
            "flavor_folder": source.flavor_folder or "",
            "file": source.file,
            "account": source.account,
            "character": source.character,
            "scope": scope,
            **fields,
        }
    )


def _spy_flavors(inst: install.Install, flavor: str | None) -> list[install.Flavor]:
    """The flavor named (`--flavor`, `$WOWLAB_FLAVOR`), the only one, or
    every one when several are installed and none is named."""
    if _given_flavor(flavor) is not None or len(inst.flavors) <= 1:
        return [_select_flavor(inst, flavor)]
    return list(inst.flavors)


def _spy_read(root: Path | None, flavor: str | None, account: str | None) -> _SpyRead:
    inst, _ = _discover(root)
    flavors = _spy_flavors(inst, flavor)
    surveys = [spy.survey(layout.Layout.for_flavor(f, inst.root), account=account) for f in flavors]
    return _SpyRead([f.folder for f in flavors], surveys)


def _spy_label(character: str | None) -> str:
    """A character as the text names it: its folder's name."""
    return "the account" if character is None else character.rsplit("/", 1)[-1]


def _spy_date(seconds: int | None) -> str:
    if seconds is None:
        return "unknown"
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(seconds))


def _spy_character_matches(source: spy.SpySource, wanted: str) -> bool:
    if source.character is None:
        return False
    folded = spy.fold(wanted)
    return spy.fold(source.character) == folded or spy.fold(_spy_label(source.character)) == folded


def _spy_place(p: spy.SpyPlayer) -> str:
    where = p.zone or ""
    if p.sub_zone:
        where = f"{where}: {p.sub_zone}" if where else p.sub_zone
    return where or "an unknown place"


def _spy_who(p: spy.SpyPlayer) -> str:
    """Level, race, class, faction and guild, as Spy saved them."""
    level = None if p.level is None else f"level {p.level}{'?' if p.is_guess else ''}"
    parts = [level, p.race, p.class_token]
    who = " ".join(x for x in parts if x) or "nothing recorded"
    side = p.faction or "faction unknown"
    if p.is_enemy is True:
        side += ", hostile when Spy last looked"
    elif p.is_enemy is False:
        side += ", not hostile when Spy last looked"
    guild = f", guild {p.guild}" if p.guild else ""
    return f"{who}, {side}{guild}"


def _spy_line(p: spy.SpyPlayer) -> str:
    marks = "".join([" [kill on sight]" if p.kos else "", " [ignored]" if p.ignored else ""])
    return (
        f"{p.key}{marks}: {_spy_who(p)}; last seen {_spy_date(p.time)}; place Spy last "
        f"recorded: {_spy_place(p)} (by {_spy_label(p.source.character)})"
    )


def _spy_sorted(players: Sequence[spy.SpyPlayer], by: SpySort) -> list[spy.SpyPlayer]:
    items = sorted(players, key=lambda p: (spy.fold(p.key), p.source.file))
    if by == "name":
        return items
    if by == "level":
        return sorted(items, key=lambda p: -(p.level if p.level is not None else -(2**31)))
    return sorted(items, key=lambda p: -(p.time if p.time is not None else -1))


def _spy_distinct(players: Sequence[spy.SpyPlayer]) -> int:
    """How many players the rows name: one row per character that saw a
    player, so a player two characters saw is counted once."""
    return len({spy.fold(p.key) for p in players})


def _spy_words(read: _SpyRead, characters: Sequence[spy.SpyCharacterData]) -> str:
    """The opening sentence, about the characters listed (`--character`)."""
    if not read.accounts and not read.characters and not read.errors:
        return (
            f"No Spy saved data in {', '.join(read.folders)}: Spy is not installed, or has not "
            "saved yet (it saves when you log out or type /reload)."
        )
    rows = [p for c in characters for p in c.players]
    distinct = _spy_distinct(rows)
    counts = ", ".join(
        f"{_spy_label(c.source.character)} {len(c.players)}" for c in characters if c.present
    )
    by = f" ({counts})" if counts else ""
    held = f"{_plural(distinct, 'enemy player')} Spy still keeps"
    if len(rows) != distinct:
        held = (
            f"{_plural(len(rows), 'row')} for {_plural(distinct, 'enemy player')} Spy still "
            "keeps (one per character that saw them)"
        )
    return f"{held[0].upper()}{held[1:]}, {_SPY_AS_OF}{by}."


def _spy_purge_words(found: spy.SpySettings) -> str:
    """What a character's Spy profile drops, from its purge options."""
    if found.purge_days is None:
        when = (
            f"by a purge setting wowlab does not know ({found.purge_setting}; Spy treats it "
            "as 90 days)"
        )
    else:
        when = f"when not seen for {_plural(found.purge_days, 'day')}"
    # `PurgeWinLossData` deletes every row past the timeout, kill on sight
    # or not, whatever `PurgeKoS` says (List.lua, around line 798).
    if found.purge_counted:
        extra = (
            ", those it counted a killing blow or death against too, and kill-on-sight players too"
        )
    elif found.purge_kos:
        extra = ", kill-on-sight players too"
    else:
        extra = ""
    return f"drops players {when}{extra}"


def _spy_notes(read: _SpyRead, sources: Sequence[spy.SpySource]) -> list[str]:
    """The standing notes, then one naming the purge setting of each
    character listed whose profile the account file holds."""
    said: list[str] = []
    seen: set[spy.SpySource] = set()
    for source in sources:
        if source in seen:
            continue
        seen.add(source)
        found = spy.settings_for(read.accounts, source)
        if found is not None:
            said.append(f"{_spy_label(source.character)} {_spy_purge_words(found)}")
    if not said:
        return list(_SPY_NOTES)
    return [*_SPY_NOTES, "Spy's purge setting for each character: " + "; ".join(said) + "."]


def _spy_caveat(read: _SpyRead, players: Sequence[spy.SpyPlayer]) -> str | None:
    """The caveat when a character whose rows are shown takes the rows
    other Spy users share."""
    sources = {p.source for p in players}
    who = sorted(
        {_spy_label(s.character) for s in sources if spy.uses_shared_data(read.accounts, s)}
    )
    return _SPY_SHARED_CAVEAT.format(who=", ".join(who)) if who else None


def _spy_errors(read: _SpyRead) -> None:
    for e in read.errors:
        _say_err(f"could not read {e.source.file}: {e.error}")


@spy_app.command("list", short_help="The players Spy saw, newest first.")
@_handled
def spy_list(
    search: Annotated[
        str | None,
        typer.Option(
            "--search",
            help="Only players whose name or guild holds this text (case, spaces, hyphens "
            "and apostrophes ignored).",
        ),
    ] = None,
    kos_only: Annotated[
        bool, typer.Option("--kos", help="Only players on a kill-on-sight list.")
    ] = False,
    enemies: Annotated[
        bool, typer.Option("--enemies", help="Only players Spy marked as enemies.")
    ] = False,
    character: Annotated[
        str | None,
        typer.Option(
            "--character",
            help="Only what this character's Spy list holds: its folder name "
            "(First-Second), or <realm or digits folder>/<folder>.",
        ),
    ] = None,
    sort: Annotated[
        SpySort,
        typer.Option("--sort", help="last: newest first; name: by name; level: highest first."),
    ] = "last",
    account: AccountOpt = None,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
    notes: NotesOpt = False,
) -> None:
    """The players the Spy addon saw, one line each, newest first, with
    the character that saw them, as of your last logout or /reload. Spy's
    files are only read; nothing is changed.

    \f
    JSON: SpyListReport."""
    if search is not None and not spy.fold(search):
        raise CliError(
            "--search holds only spaces, hyphens and apostrophes, which a search ignores; "
            "give some letters of a name or guild",
            EXIT_USAGE,
        )
    read = _spy_read(root, flavor, account)
    characters = [
        c
        for c in read.characters
        if character is None or _spy_character_matches(c.source, character)
    ]
    if character is not None and not characters:
        listed = ", ".join(_spy_label(c.source.character) for c in read.characters) or "none"
        raise CliError(
            f"no Spy list of a character {character!r} (characters with one: {listed})",
            EXIT_USAGE,
        )
    everyone = [p for c in characters for p in c.players]
    want = spy.fold(search) if search else ""
    picked = [
        p
        for p in everyone
        if (not want or any(want in spy.fold(t) for t in (p.key, p.guild or "")))
        and (not kos_only or p.kos)
        and (not enemies or p.is_enemy is True)
    ]
    shown = _spy_sorted(picked, sort)
    words = _spy_words(read, characters)
    caveat = _spy_caveat(read, everyone)
    report = SpyListReport(
        flavor_folders=read.folders,
        files=read.files(),
        total=len(everyone),
        distinct=_spy_distinct(everyone),
        sort=sort,
        search=search,
        character=character,
        kos_only=kos_only,
        enemies=enemies,
        players=shown,
        kos=[k for c in characters for k in c.kos],
        ignored=[i for c in characters for i in c.ignored],
        shared_kos=[k for a in read.accounts for k in a.shared_kos],
        player_notes=[n for a in read.accounts for n in a.notes],
        tags=[t for a in read.accounts for t in a.tags],
        unread=read.unread(),
        warnings=read.warnings(),
        caveat=caveat,
        words=words,
        notes=_spy_notes(read, [c.source for c in characters]),
    )
    if json_out:
        _spy_errors(read)
        _emit(report)
    else:
        _say(words)
        for warning in report.warnings:
            _say(warning)
        if search is not None or kos_only or enemies:
            _say(f"{len(shown)} of {_plural(len(everyone), 'row')} match.")
        for p in shown:
            _say(_spy_line(p))
        for u in report.unread:
            what = _SPY_TABLE_WORDS.get(u.table, "an entry")
            whose = _spy_label(u.source.character)
            _say(f"Could not be read: {what} {u.key} ({whose}): {u.reason}")
        _spy_errors(read)
        if read.accounts or read.characters:
            _say_notes(report.notes, notes, caveat)
    if read.errors:
        raise typer.Exit(EXIT_ERROR)


def _spy_player_lines(p: spy.SpyPlayer) -> list[str]:
    who = _spy_who(p).replace(
        "hostile when Spy last looked", "hostile to your character when Spy last looked"
    )
    lines = [
        f"  Seen by {_spy_label(p.source.character)}: {who}",
        f"    Last seen: {_spy_date(p.time)}",
        f"    Place Spy last recorded: {_spy_place(p)}",
    ]
    if p.map_id is not None:
        at = f" at {p.map_x:g}, {p.map_y:g}" if p.map_x is not None and p.map_y is not None else ""
        lines.append(
            f"    The last map position Spy recorded: map {p.map_id}{at} (where your character "
            "stood; Spy does not update it inside an instance, so it can be older than the "
            "date above)"
        )
    if p.wins is not None:
        lines.append(f"    Your killing blows on them (Spy's count): {p.wins}")
    if p.loses is not None:
        lines.append(f"    Your deaths Spy put on them: {p.loses} (some from who had you targeted)")
    if p.kos:
        added = f", added {_spy_date(p.kos_added)}" if p.kos_added is not None else ""
        lines.append(f"    Kill on sight: yes{added}")
    if p.reasons:
        said = ", ".join(v if isinstance(v, str) else k for k, v in p.reasons.items())
        lines.append(f"    Kill-on-sight reasons: {said}")
    if p.ignored:
        lines.append("    Ignored by Spy: yes")
    if p.other:
        lines.append(f"    Other fields Spy saved: {', '.join(p.other)}")
    return lines


def _spy_encounter_line(e: spy.SpyEncounter) -> str:
    result = _SPY_RESULT_WORDS.get(e.result or "", f"result {e.result_code or 'unknown'}")
    how = f" ({_SPY_HOW_WORDS.get(e.how, e.how)})" if e.how else ""
    at = f", map {e.map_id}" if e.map_id is not None else ""
    return f"    {_spy_date(e.time)}: {result}{how}{at} (by {_spy_label(e.source.character)})"


@spy_app.command("show", short_help="Everything Spy saved about one player.")
@_handled
def spy_show(
    name: Annotated[
        str,
        typer.Argument(
            metavar="NAME",
            help="The player as spy list shows them (First-Second). Case, spaces, hyphens "
            "and apostrophes are ignored when no saved name matches exactly.",
        ),
    ],
    account: AccountOpt = None,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
    notes: NotesOpt = False,
) -> None:
    """Everything the Spy addon saved about one player: what each of your
    characters saw, kill on sight, ignored, the fights Spy counted, and your
    notes and tags, as of your last logout or /reload. Nothing is changed.

    \f
    JSON: SpyShowReport."""
    read = _spy_read(root, flavor, account)
    players = spy.named([p for c in read.characters for p in c.players], name)
    kos = spy.named([k for c in read.characters for k in c.kos], name)
    ignored = spy.named([i for c in read.characters for i in c.ignored], name)
    encounters = spy.encounters_with([e for c in read.characters for e in c.encounters], name)
    shared = spy.named([k for a in read.accounts for k in a.shared_kos], name)
    player_notes = spy.named([n for a in read.accounts for n in a.notes], name)
    tags = spy.named([t for a in read.accounts for t in a.tags], name)
    found = players or kos or ignored or encounters or shared or player_notes or tags
    if not found:
        _spy_errors(read)
        where = ", ".join(read.folders)
        raise CliError(f"no player {name!r} in Spy's saved data in {where}")
    keys = [
        *(r.key for r in players),
        *(r.key for r in kos),
        *(r.key for r in ignored),
        *(r.key for r in shared),
        *(r.key for r in player_notes),
        *(r.key for r in tags),
    ]
    title = keys[0] if keys else (encounters[0].name or name)
    lines = [f"{title}, from Spy's saved data, {_SPY_AS_OF}:"]
    for p in sorted(players, key=lambda p: p.source.file):
        lines += _spy_player_lines(p)
    seen = {(p.source.file, p.key) for p in players}
    for k in kos:
        if (k.source.file, k.key) not in seen:
            added = f", added {_spy_date(k.added)}" if k.added is not None else ""
            lines.append(f"  On {_spy_label(k.source.character)}'s kill-on-sight list{added}")
    for i in ignored:
        if (i.source.file, i.key) not in seen:
            lines.append(f"  Ignored by Spy for {_spy_label(i.source.character)}")
    if encounters:
        lines.append(f"  Fights Spy counted against them: {len(encounters)}")
        lines += [_spy_encounter_line(e) for e in sorted(encounters, key=lambda e: -(e.time or 0))]
    for s in shared:
        added = f", added {_spy_date(s.added)}" if s.added is not None else ""
        lines.append(f"  On the kill-on-sight list {s.character} shares ({s.faction}){added}")
    for n in player_notes:
        lines.append(f"  Your note in Spy: {n.text}")
    for t in tags:
        tag = _SPY_TAG_WORDS[t.tag] if t.tag is not None else (t.other_tag or "")
        lines.append(f"  Your tag in Spy: {tag}")
    warnings = read.warnings()
    caveat = _spy_caveat(read, players)
    report = SpyShowReport(
        typed=name,
        files=read.files(),
        players=players,
        kos=kos,
        ignored=ignored,
        encounters=encounters,
        shared_kos=shared,
        player_notes=player_notes,
        tags=tags,
        warnings=warnings,
        caveat=caveat,
        lines=lines,
        notes=_spy_notes(read, sorted({p.source for p in players}, key=lambda x: x.file)),
    )
    if json_out:
        _spy_errors(read)
        _emit(report)
    else:
        for warning in warnings:
            _say(warning)
        for line in lines:
            _say(line)
        _spy_errors(read)
        _say_notes(report.notes, notes, caveat)
    if read.errors:
        raise typer.Exit(EXIT_ERROR)
