"""`wowlab crew list` and `crew show` (docs/LAB_PLAN.md §20.2, M19-02);
`crew forget`, `crew rate` and `crew note` (§20.3, M19-03).

`list` and `show` read the crew book (`crewbook`) from a game account's
`WowLab.lua` and write nothing anywhere. `forget`, `rate` and `note` change
it only through the write gate (`guard`): the bytes they write are a
targeted edit of the parsed file (`crewedit`), shown as a plan, confirmed,
and pinned with `--expect`. Each takes `--flavor` and `--account` as `char`
does; with several books to choose from and none named, the candidates are
listed and the command exits 2. Every name, realm, guild, instance name and
note in the book is other people's text (or the player's own note): it is
printed only through `_say`, which escapes C0, C1 and bidi controls, never
through the player-copy rewriting wowlab's own sentences go through; `--json`
is ASCII-escaped as everywhere.
"""

import hashlib
import os
import re
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Annotated, Literal

import typer

from wowlab_core import (
    crewbook,
    crewdb,
    crewedit,
    guard,
    install,
    labaddon,
    layout,
    luadata,
    snapshot,
)
from wowlab_core.cli._common import (
    CREW_BOOK_SENTENCE,
    EXIT_ERROR,
    EXIT_REFUSED,
    EXIT_USAGE,
    AccountOpt,
    CliError,
    FlavorOpt,
    JsonOpt,
    NotesOpt,
    RootOpt,
    YesOpt,
    _bytes,
    _confirm,
    _discover,
    _emit,
    _given_flavor,
    _handled,
    _is_lab_file,
    _listed,
    _Out,
    _plural,
    _Report,
    _say,
    _say_err,
    _say_notes,
    _select_account,
    _select_flavor,
    crew_app,
)
from wowlab_core.errors import WowlabError

# ─── crew ────────────────────────────────────────────────────────────────────

_AS_OF = "as of your last logout or /reload"
_NOT_LISTED = "not in the game's list"
_TURN_ON = "Turn it on in game with /wowlab crew on."
_CREW_NOTES = [
    "The book knows names, not people: a player who renames or moves realm appears as a new "
    "entry, and someone else may later take a name you rated.",
    "Each game account has its own book, and so does each flavor folder.",
    "The role is the last one a group gave them; which roles this game version reports is "
    "not yet confirmed.",
    "The class is the game's internal name for it (MAGE, PRIEST, ...), as the game gave it.",
    "Dates are in this computer's time zone.",
]


class CrewPlace(_Out):
    """One game account folder a crew command looked in: its flavor folder,
    the account folder, the account `WowLab.lua` (relative to the flavor
    folder; None when there is none), and whether the file holds a crew
    book (True), holds none (False), or could not be read (None)."""

    flavor_folder: str
    account: str
    file: str | None
    holds_book: bool | None


class CrewReport(_Out):
    """`wowlab crew list --json`: the crew book of one game account, as of
    the last logout or /reload. `state` is `not_turned_on`, `off` (kept,
    not recording) or `on`. `players` are the ones shown, filtered and in
    the order the text lists them; `total` counts every player the book
    holds; `unread` lists each entry that could not be read, with its key
    and why. With no book in any account looked in, `flavor_folder`,
    `account` and `file` are None and `places` lists where wowlab looked.
    `words` is the sentence the text output opens with (the book's state,
    or that it was never turned on and how to turn it on in game); `keeps`
    is what the crew book keeps and where, as `crew --help` says it.

    The players are read from wowlab's crew database after a sync of the
    book (`sync`: what the sync did and, when it did not run, why; None when
    there was no book to sync). Each player's `in_game_list` is false for
    one the game's list no longer holds, whom only the database keeps.
    `total` counts the players the database keeps and the book's unread
    entries."""

    flavor_folder: str | None
    account: str | None
    file: str | None
    state: crewbook.State
    schema_number: int | None
    started: int | None
    probe_loads: int | None
    skipped: int | None
    current_count: int
    total: int
    sort: crewbook.SortBy
    rated: crewbook.RatedFilter | None
    noted: bool
    search: str | None
    players: list[crewdb.CrewRow]
    unread: list[crewbook.UnreadEntry]
    places: list[CrewPlace]
    words: str
    keeps: str
    notes: list[str]
    sync: crewdb.SyncOutcome | None = None


class CrewShowReport(_Out):
    """`wowlab crew show --json`: everything the book keeps about one
    player (`player`, None when the book was never turned on), and the key
    as typed (`typed`). `lines` are the lines the text output prints:
    the player and every field kept, or the one sentence of a book never
    turned on. The player is read from wowlab's crew database after a sync
    (`sync`, as in `CrewReport`)."""

    flavor_folder: str | None
    account: str | None
    file: str | None
    state: crewbook.State
    typed: str
    player: crewdb.CrewRow | None
    places: list[CrewPlace]
    lines: list[str]
    notes: list[str]
    sync: crewdb.SyncOutcome | None = None


class _Opened:
    """The book a crew command reads, and where it looked."""

    def __init__(
        self,
        place: CrewPlace | None,
        book: crewbook.CrewBook,
        places: list[CrewPlace],
        where: tuple[install.Flavor, layout.Layout] | None = None,
        data: bytes | None = None,
    ) -> None:
        self.place = place
        self.book = book
        self.places = places
        # The flavor and its layout the chosen book is in, for a write.
        self.where = where
        # The bytes of the chosen book's WowLab.lua, as read (None: no file).
        self.data = data


_READ_ERRORS = (WowlabError, luadata.LuaDataError, OSError)


def _flavors(inst: install.Install, flavor: str | None) -> list[install.Flavor]:
    """The flavor named (`--flavor`, `$WOWLAB_FLAVOR`), the only one, or
    every one when several are installed and none is named."""
    if _given_flavor(flavor) is not None or len(inst.flavors) <= 1:
        return [_select_flavor(inst, flavor)]
    return list(inst.flavors)


def _accounts(lay: layout.Layout, account: str | None, alone: bool) -> list[layout.Account]:
    if account is not None:
        if alone:
            return [_select_account(lay, account)]
        try:
            return [_select_account(lay, account)]
        except CliError:
            return []
    return list(lay.wtf_walk().accounts)


def _open_book(root: Path | None, flavor: str | None, account: str | None) -> _Opened:
    """The one crew book the options name, or the only one there is.

    Every account folder of every flavor in reach is looked in: the flavor
    named (else every one) and the account named (else every one). Only one
    place: its book, whatever it holds. Several: the one whose `WowLab.lua`
    holds a book or could not be read; with more than one, the candidates
    are listed and the command exits 2; with none, the never-turned-on
    answer for every place looked in."""
    inst, _ = _discover(root)
    flavors = _flavors(inst, flavor)
    found: list[tuple[CrewPlace, crewbook.CrewBook | None, BaseException | None]] = []
    datas: list[bytes | None] = []
    where: dict[int, tuple[install.Flavor, layout.Layout]] = {}
    for f in flavors:
        lay = layout.Layout.for_flavor(f, inst.root)
        accounts = _accounts(lay, account, alone=len(flavors) == 1)
        if not accounts and account is None and len(flavors) == 1:
            raise CliError(f"no account folders under WTF/Account/ in {lay.flavor_path}")
        files = [sv for sv in lay.saved_variables("account") if _is_lab_file(sv)]
        for acct in accounts:
            mine = [sv for sv in files if sv.account == acct.folder]
            rel = mine[0].path if mine else None
            book: crewbook.CrewBook | None = crewbook.CrewBook(state="not_turned_on")
            error: BaseException | None = None
            data: bytes | None = None
            if rel is not None:
                try:
                    data = _listed(labaddon.read_file_bytes, lay, rel)
                    book = crewbook.parse_book(data)
                except _READ_ERRORS as exc:
                    book, error = None, exc
            holds = None if book is None else book.state != "not_turned_on"
            place = CrewPlace(
                flavor_folder=f.folder, account=acct.folder, file=rel, holds_book=holds
            )
            where[len(found)] = (f, lay)
            found.append((place, book, error))
            datas.append(data)
    if not found and account is None:
        raise CliError(
            "no account folders under WTF/Account/ in any flavor folder "
            f"({', '.join(f.folder for f in flavors)})"
        )
    if not found:
        raise CliError(
            f"no account folder {account!r} in any flavor folder "
            f"({', '.join(f.folder for f in flavors)})",
            EXIT_USAGE,
        )
    places = [p for p, _, _ in found]
    at = 0
    if len(found) == 1:
        chosen = found[0]
    else:
        holding = [x for x in found if x[0].holds_book is not False]
        if not holding:
            return _Opened(None, crewbook.CrewBook(state="not_turned_on"), places)
        if len(holding) > 1:
            listed = "; ".join(_place_words(p) for p, _, _ in holding)
            raise CliError(
                f"{len(holding)} crew books to choose from ({listed}); choose one with "
                "--account, and --flavor when the install has several",
                EXIT_USAGE,
            )
        chosen = holding[0]
        at = found.index(chosen)
    place, book, error = chosen
    if isinstance(error, crewbook.CrewBookError):
        # The player's words; a `--json` run's message adds the field.
        words = f"{error} ({error.detail})" if _Report.json else str(error)
        raise CliError(words) from None
    if error is not None:
        raise error
    assert book is not None
    return _Opened(place, book, places, where[at], datas[at])


def _place_words(p: CrewPlace) -> str:
    return f"game account {p.account} in {p.flavor_folder}"


# ─── wowlab's crew database (§21.3, M20-01) ──────────────────────────────────


def _target(opened: _Opened) -> crewdb.Target | None:
    """The book's place as the crew database knows it; None when no one
    book was chosen."""
    place = opened.place
    if place is None or opened.where is None:
        return None
    _, lay = opened.where
    return crewdb.Target(
        install_root=os.path.realpath(lay.install_root),
        flavor_folder=place.flavor_folder,
        flavor_path=str(lay.flavor_path),
        account=place.account,
        file=place.file or "",
    )


def _from_database(
    opened: _Opened,
) -> tuple[crewdb.SyncOutcome | None, list[crewdb.CrewRow]]:
    """Sync the chosen book into wowlab's crew database, then every player
    the database keeps (§21.3, "Reading"). A sync that did not run says why
    in its outcome; the players are then the database's as they were."""
    target = _target(opened)
    if target is None:
        return None, []
    outcome: crewdb.SyncOutcome | None = None
    if opened.book.state != "not_turned_on" and opened.data is not None:
        outcome = crewdb.sync(target, opened.data, opened.book)
    return outcome, crewdb.read(target.flavor_folder, target.account, opened.book)


def _sync_caveat(outcome: crewdb.SyncOutcome | None) -> str | None:
    """The one line a list or show adds when its sync did not run."""
    if outcome is None or outcome.state in ("synced", "unchanged", "not_run"):
        return None
    return outcome.words


def _date(seconds: int | None) -> str:
    """A time the addon stored, as a local date; "not recorded" for none."""
    if seconds is None:
        return "not recorded"
    try:
        return time.strftime("%Y-%m-%d", time.localtime(seconds))
    except (OverflowError, OSError, ValueError):
        return "a date this computer cannot show"


def _never_on(places: Sequence[CrewPlace]) -> str:
    """The one sentence for a book never turned on, naming where it looked."""
    if len(places) == 1:
        where = _place_words(places[0])
        return f"The crew book is not turned on for {where} ({_AS_OF}). {_TURN_ON}"
    where = ", ".join(_place_words(p) for p in places)
    return f"The crew book is not turned on in any game account wowlab read ({where}). {_TURN_ON}"


def _header(opened: _Opened, kept: int) -> str:
    book, place = opened.book, opened.place
    assert place is not None
    where = _place_words(place)
    n = kept + len(book.unread)
    started = f"started {_date(book.started)}"
    if book.state == "off":
        return (
            f"The crew book of {where} is turned off ({_AS_OF}): it is not recording, and it "
            f"keeps {_plural(n, 'player')} ({started})."
        )
    if n == 0 and not book.players:
        return (
            f"The crew book of {where} is on and holds no player yet ({_AS_OF}; {started}). "
            "Each game account and each flavor folder has its own book."
        )
    return f"Crew book of {where}, {_AS_OF}: {_plural(n, 'player')}, {started}."


def _row(p: crewbook.CrewPlayer) -> str:
    """One player's line in `crew list`."""
    who = " ".join(x for x in (p.class_token, str(p.level) if p.level is not None else None) if x)
    parts = [who or "class not recorded"]
    if p.met is not None:
        parts.append(f"grouped {p.met} {'time' if p.met == 1 else 'times'}")
    parts.append(f"last {_date(p.last)}")
    if p.where:
        parts.append(f"last instance recorded: {p.where}")
    if p.rating:
        parts.append(f"rated {p.rating}")
    if p.note:
        parts.append("has a note")
    if isinstance(p, crewdb.CrewRow) and not p.in_game_list:
        parts.append(_NOT_LISTED)
    return f"  {p.key}: " + "; ".join(parts)


def _unread_lines(book: crewbook.CrewBook) -> list[str]:
    if not book.unread:
        return []
    n = len(book.unread)
    lines = [f"{n} {'entry' if n == 1 else 'entries'} could not be read:"]
    lines += [f"  {u.key}: {u.reason}" for u in book.unread]
    return lines


RatedOpt = Annotated[
    crewbook.RatedFilter | None,
    typer.Option("--rated", help="Only players you rated good, bad, or either (any)."),
]
SortOpt = Annotated[
    crewbook.SortBy,
    typer.Option(
        "--sort",
        help="last: newest group first (the default); met: most groups first; name: by name.",
    ),
]


@crew_app.command("list", short_help="The players in the crew book, newest group first.")
@_handled
def crew_list(
    rated: RatedOpt = None,
    noted: Annotated[
        bool, typer.Option("--noted", help="Only players you wrote a note about.")
    ] = False,
    search: Annotated[
        str | None,
        typer.Option(
            "--search",
            help="Only players whose name, realm, guild or note holds this text (case, "
            "spaces and apostrophes ignored).",
        ),
    ] = None,
    sort: SortOpt = "last",
    account: AccountOpt = None,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
    notes: NotesOpt = False,
) -> None:
    """The players the crew book keeps, one line each, newest group first,
    as of your last logout or /reload. Nothing is changed.

    \f
    JSON: CrewReport."""
    if search is not None and not crewbook.fold(search):
        raise CliError(
            "--search holds only spaces and apostrophes, which a search ignores; "
            "give some letters of a name, realm, guild or note",
            EXIT_USAGE,
        )
    opened = _open_book(root, flavor, account)
    book = opened.book
    outcome, kept = _from_database(opened)
    shown = crewbook.ordered(crewbook.select(kept, rated=rated, noted=noted, search=search), sort)
    place = opened.place
    if book.state == "not_turned_on":
        words = _never_on(opened.places if place is None else [place])
    else:
        words = _header(opened, len(kept))
    report = CrewReport(
        flavor_folder=place.flavor_folder if place else None,
        account=place.account if place else None,
        file=place.file if place else None,
        state=book.state,
        schema_number=book.schema_number,
        started=book.started,
        probe_loads=book.probe_loads,
        skipped=book.skipped,
        current_count=book.current_count,
        total=len(kept) + len(book.unread),
        sort=sort,
        rated=rated,
        noted=noted,
        search=search,
        players=shown,
        unread=book.unread,
        places=opened.places,
        words=words,
        keeps=CREW_BOOK_SENTENCE,
        notes=_CREW_NOTES,
        sync=outcome,
    )
    if json_out:
        _emit(report)
        return
    _say(words)
    caveat = _sync_caveat(outcome)
    if caveat is not None:
        _say(caveat)
    if book.state == "not_turned_on" and not kept:
        return
    filtered = rated is not None or noted or bool(search)
    if filtered:
        _say(f"{len(shown)} of {_plural(len(kept), 'player')} match.")
    for p in shown:
        _say(_row(p))
    for line in _unread_lines(book):
        _say(line)
    _say_notes(_CREW_NOTES, notes)


def _field_lines(p: crewbook.CrewPlayer) -> list[str]:
    def said(value: object) -> str:
        return "not recorded" if value is None or value == "" else str(value)

    # `where` is left out in the open world and kept when a later read was
    # refused (§20.1), so it can be from an earlier group than `last`.
    where = f"{p.where} (instance id {said(p.where_id)})" if p.where else "none"
    # `current` holds every key counted since the group formed, so a player
    # who left it before the save still counts.
    counted = "yes" if p.in_current_group else "no"
    return [
        f"  Class (the game's internal name): {said(p.class_token)}",
        f"  Level when last grouped: {said(p.level)}",
        f"  Last role a group gave them: {said(p.role)}",
        f"  Guild last seen (kept if they left it): {said(p.guild)}",
        f"  Groups shared: {said(p.met)}",
        f"  First grouped: {_date(p.first)}",
        f"  Last grouped: {_date(p.last)}",
        f"  Last instance recorded: {where}",
        f"  Your rating: {p.rating or 'none'}",
        f"  Your note: {p.note or 'none'}",
        f"  Counted in the group you were in at the last save: {counted}",
    ]


@crew_app.command("show", short_help="Everything the crew book keeps about one player.")
@_handled
def crew_show(
    name: Annotated[
        str,
        typer.Argument(
            metavar="NAME-REALM",
            help="The player as crew list shows them. Case, spaces and apostrophes are "
            "ignored when no stored name matches exactly.",
        ),
    ],
    account: AccountOpt = None,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
    notes: NotesOpt = False,
) -> None:
    """Everything the crew book keeps about one player, as of your last
    logout or /reload. Nothing is changed.

    \f
    JSON: CrewShowReport."""
    opened = _open_book(root, flavor, account)
    book, place = opened.book, opened.place
    outcome, kept = _from_database(opened)
    caveat = _sync_caveat(outcome)
    player: crewdb.CrewRow | None = None
    if book.state != "not_turned_on" or kept:
        assert place is not None
        found = crewbook.match(kept, name)
        if not found:
            unread = [u.key for u in book.unread if crewbook.fold(u.key) == crewbook.fold(name)]
            why = " (its entry could not be read)" if unread else ""
            also = f" {caveat}" if caveat is not None else ""
            raise CliError(
                f"no player {name!r} in the crew book of {_place_words(place)}{why}{also}"
            )
        if len(found) > 1:
            keys = ", ".join(p.key for p in found)
            raise CliError(
                f"{name!r} matches {len(found)} players ({keys}); type the name as crew list "
                "shows it",
                EXIT_USAGE,
            )
        player = found[0]
    if player is None:
        lines = [_never_on(opened.places if place is None else [place])]
    else:
        assert place is not None
        lines = [f"{player.key}, from the crew book of {_place_words(place)}, {_AS_OF}:"]
        lines += _field_lines(player)
        if not player.in_game_list:
            lines.append(
                "  In the game's list: no; only wowlab's crew database keeps them now "
                "(the game dropped or forgot them)"
            )
        if book.state == "off":
            lines.append("The crew book is turned off: it is not recording.")
        elif book.state == "not_turned_on":
            lines.append("The crew book is not turned on in game: this is what wowlab kept.")
        if caveat is not None:
            lines.append(caveat)
    report = CrewShowReport(
        flavor_folder=place.flavor_folder if place else None,
        account=place.account if place else None,
        file=place.file if place else None,
        state=book.state,
        typed=name,
        player=player,
        places=opened.places,
        lines=lines,
        notes=_CREW_NOTES,
        sync=outcome,
    )
    if json_out:
        _emit(report)
        return
    for line in lines:
        _say(line)
    if player is not None:
        _say_notes(_CREW_NOTES, notes)


# ─── sync (§21.3, M20-01) ────────────────────────────────────────────────────


class CrewSyncReport(_Out):
    """`wowlab crew sync --json`: what a sync of one game account's crew book
    into wowlab's crew database did (`sync`; None when the book was never
    turned on), what `--adopt` did first (`adopted`; None without it), and
    how many players the database keeps afterwards (`total`). `words` is
    the sentence the text output opens with."""

    flavor_folder: str | None
    account: str | None
    file: str | None
    state: crewbook.State
    sync: crewdb.SyncOutcome | None
    adopted: crewdb.AdoptOutcome | None
    total: int
    words: str


def _sync_plan_line(plan: crewdb.SyncPlan) -> str:
    return (
        f"A sync would add {_plural(plan.players_added, 'player')} and "
        f"{plan.matches_added} {'match' if plan.matches_added == 1 else 'matches'}, update {_plural(plan.players_updated, 'player')} "
        f"(changing {_plural(plan.ratings_changed, 'rating')} and "
        f"{_plural(plan.notes_changed, 'note')}), and mark "
        f"{_plural(plan.left_game_list, 'player')} as no longer in the game's list. It never "
        "lowers a count the database keeps."
    )


@crew_app.command(
    "sync", short_help="Copy what is new in the crew book into wowlab's crew database."
)
@_handled
def crew_sync(
    adopt: Annotated[
        bool,
        typer.Option(
            "--adopt",
            help="First make this game account's crew database, kept for a flavor folder "
            "since renamed or for another install folder, this install's. It is moved, never "
            "merged with another.",
        ),
    ] = False,
    account: AccountOpt = None,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
) -> None:
    """Copy what is new in the crew book (WowLab.lua, as of your last logout
    or /reload) into wowlab's own crew database, which keeps every player
    for good, even once the game's list has dropped them. Nothing in the
    game folder is changed. crew list and crew show do this first.

    When the book is not the one the database was filled from, or wowlab put
    WowLab.lua back since the last sync (a snapshot restore, an undo or a
    merge), the sync shows what it would change and asks first (type y to
    sync), and it never lowers a count.

    \f
    JSON: CrewSyncReport."""
    opened = _open_book(root, flavor, account)
    book, place = opened.book, opened.place
    target = _target(opened)
    if target is None or book.state == "not_turned_on" or opened.data is None:
        words = _never_on(opened.places if place is None else [place])
        if json_out:
            _emit(
                CrewSyncReport(
                    flavor_folder=place.flavor_folder if place else None,
                    account=place.account if place else None,
                    file=place.file if place else None,
                    state=book.state,
                    sync=None,
                    adopted=None,
                    total=0,
                    words=words,
                )
            )
            return
        _say(words)
        return
    adopted = crewdb.adopt(target) if adopt else None
    show = _say_err if json_out else _say
    if adopted is not None:
        show(adopted.words)

    def ask(plan: crewdb.SyncPlan, why: str) -> bool:
        show(why)
        show(_sync_plan_line(plan))
        try:
            # No --yes: a held sync is confirmed only by a typed answer.
            _confirm("Sync this crew book into wowlab's crew database?", False)
        except typer.Exit:
            return False
        return True

    data = opened.data
    outcome = crewdb.sync(target, data, book, by_hand=True, confirm=ask)
    total = len(crewdb.read(target.flavor_folder, target.account))
    words = f"{outcome.words} ({_place_words(place)}, {_AS_OF})" if place else outcome.words
    report = CrewSyncReport(
        flavor_folder=target.flavor_folder,
        account=target.account,
        file=target.file,
        state=book.state,
        sync=outcome,
        adopted=adopted,
        total=total,
        words=words,
    )
    failed = outcome.state in ("declined", "skipped", "refused", "elsewhere")
    if json_out:
        _emit(report)
    elif outcome.state == "declined":
        pass  # the confirm step said so
    elif failed:
        _say_err(words)
    else:
        _say(words)
    if failed:
        raise typer.Exit(EXIT_ERROR)


# ─── forget, rate, note (§20.3, M19-03) ──────────────────────────────────────

_SHA256_TEXT = re.compile(r"[0-9a-f]{64}")
# The pin part that stands for a backup file there is none of.
ABSENT = "absent"
_BACKUP_NAME = labaddon.LAB_FILE_NAME + ".bak"

CrewCommand = Literal["forget", "rate", "note"]
BackupOutcome = Literal["absent", "left_alone", "changed"]


class CrewEditReport(_Out):
    """`wowlab crew forget|rate|note --json`: the change to one game
    account's crew book, and whether it was written.

    `players` are the stored keys the change acts on. For `rate` and `note`,
    `field` is `rating` or `note`, with its value as stored (`before`) and as
    it would be (`after`; None: taken out). For `forget`, `backup` says what
    happens to the game's backup `WowLab.lua.bak` (`absent`; `left_alone`:
    it does not hold them; `changed`), and `not_reached` says what a forget
    does not reach. `changed` is false when there is nothing to change.

    The pin `--expect` takes: `target_sha256` (WowLab.lua as read) and
    `written_sha256` (the bytes it would write; None with nothing to
    change), and for `forget` also `backup_sha256` and
    `backup_written_sha256` (the backup as read and as it would be: the same
    when it is left alone, `absent` when there is none)."""

    command: CrewCommand
    flavor_folder: str
    account: str
    file: str
    backup_file: str | None
    players: list[str]
    field: crewedit.Field | None
    before: str | None
    after: str | None
    backup: BackupOutcome | None
    changed: bool
    written: bool
    transaction: str | None
    target_sha256: str
    written_sha256: str | None
    backup_sha256: str | None
    backup_written_sha256: str | None
    not_reached: str | None
    notes: list[str]


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


_EXPECT_FIELDS_OF: dict[str, tuple[str, ...]] = {
    "forget": ("target_sha256", "written_sha256", "backup_sha256", "backup_written_sha256"),
    "rate": ("target_sha256", "written_sha256"),
    "note": ("target_sha256", "written_sha256"),
}


def _pin(expect: str | None, command: str) -> list[str] | None:
    """`--expect`, split into its parts; a usage error when it is not the
    pin an earlier run's --json gives."""
    if expect is None:
        return None
    fields = _EXPECT_FIELDS_OF[command]
    got = expect.split(":")
    ok = len(got) == len(fields) and all(
        _SHA256_TEXT.fullmatch(p) or (i >= 2 and p == ABSENT) for i, p in enumerate(got)
    )
    if not ok:
        raise CliError(
            f"--expect {expect!r} is not {':'.join(fields)}, the values an earlier run's "
            "--json gives",
            EXIT_USAGE,
        )
    return got


_EXPECT_HELP = (
    "Write only if the files are still as an earlier run's --json read them and the change "
    "would write the same bytes (its {fields}, joined by ':'): else refused, exit 3, nothing "
    "written. For a plan shown by one run and confirmed by another."
)


class _Edit:
    """A crew book a write command changes: where it is, its bytes as read,
    and the game's backup beside it."""

    def __init__(self, opened: _Opened, command: str) -> None:
        place = opened.place
        if opened.book.state == "not_turned_on" or place is None or place.file is None:
            where = _place_words(place) if place is not None else "any game account wowlab read"
            raise CliError(
                f"The crew book is not turned on for {where} ({_AS_OF}), so there is nothing "
                f"to {command}; wowlab never starts a crew book. Nothing was changed."
            )
        assert opened.where is not None
        self.flavor, self.lay = opened.where
        self.place = place
        self.rel: str = place.file
        # Read again: the bytes this run plans from are the bytes it writes from.
        self.data = _listed(labaddon.read_file_bytes, self.lay, self.rel)
        self.book = _parse_book(self.data)
        if self.book.state == "not_turned_on":
            raise CliError(
                f"The crew book of {self.words()} is gone since wowlab looked; nothing was changed."
            )
        self.backup_error: str | None = None
        self.backup_rel = self._backup()
        self.backup_data: bytes | None = None
        if self.backup_rel is not None and self.backup_error is None:
            try:
                self.backup_data = _listed(labaddon.read_file_bytes, self.lay, self.backup_rel)
                crewbook.parse_book(self.backup_data)
            except crewbook.CrewBookError as exc:
                self.backup_error = str(exc)
            except luadata.LuaDataError as exc:
                self.backup_error = (
                    f"it is not data wowlab can read (line {exc.line}, column {exc.column})"
                )
            except (WowlabError, OSError) as exc:
                self.backup_error = f"it cannot be read ({exc})"

    def _backup(self) -> str | None:
        """The game's `WowLab.lua.bak` beside the file, as the listing found
        it; None when there is none."""
        mine = [
            sv
            for sv in self.lay.saved_variables("account")
            if sv.backup
            and sv.account == self.place.account
            and sv.path.rsplit("/", 1)[-1].casefold() == _BACKUP_NAME.casefold()
        ]
        # The listing takes regular files only: anything else there under the
        # backup's name (a link, a folder) could still lead to an old book,
        # so it is refused rather than taken for no backup (looked at, never
        # followed or opened).
        folder = self.lay.flavor_path / self.rel.rsplit("/", 1)[0]
        try:
            with os.scandir(folder) as entries:
                odd = [
                    e.name
                    for e in entries
                    if e.name.casefold() == _BACKUP_NAME.casefold()
                    and not e.is_file(follow_symlinks=False)
                ]
        except OSError:
            odd = []
        if odd:
            # Only a forget needs the backup; it refuses on this.
            self.backup_error = "it is a link or a folder, not a file"
            return self.rel.rsplit("/", 1)[0] + "/" + odd[0]
        exact = [sv for sv in mine if sv.path == self.rel + ".bak"]
        if exact:
            return exact[0].path
        if len(mine) > 1:
            raise CliError(
                f"{len(mine)} files named like {_BACKUP_NAME} sit beside {self.rel}, so wowlab "
                "cannot tell which one is the game's backup. Nothing was changed."
            )
        return mine[0].path if mine else None

    def words(self) -> str:
        return _place_words(self.place)


def _parse_book(data: bytes) -> crewbook.CrewBook:
    try:
        return crewbook.parse_book(data)
    except crewbook.CrewBookError as exc:
        words = f"{exc} ({exc.detail})" if _Report.json else str(exc)
        raise CliError(words) from None


def _one_key(keys: Sequence[str], typed: str, edit: _Edit) -> str:
    """The stored key `typed` names: exact, else the only folded match
    (§20.2's rule); more than one lists the stored keys, exit 2."""
    if typed in keys:
        return typed
    want = crewbook.fold(typed)
    found = [k for k in keys if crewbook.fold(k) == want]
    if not found:
        raise CliError(
            f"no player {typed!r} in the crew book of {edit.words()}. Nothing was changed."
        )
    if len(found) > 1:
        raise CliError(
            f"{typed!r} matches {len(found)} players ({', '.join(found)}); type the name as "
            "crew list shows it. Nothing was changed.",
            EXIT_USAGE,
        )
    return found[0]


def _not_reached(store: snapshot.SnapshotStore, n: int, *, done: bool, one: bool) -> str:
    """What a forget does not reach (§20.3), in its plan and its result.
    wowlab has no command that removes a snapshot yet, and the sentence says
    so."""
    them = "this player" if n == 1 else "these players"
    saves = "saved" if done else "saves"
    return (
        f"This command first {saves} a copy that still holds {them}, so `wowlab undo` can "
        f"bring them back. That copy and every earlier snapshot stay in wowlab's snapshot "
        f"folder ({store.path}): wowlab cannot remove a snapshot yet. Archives you made "
        "with `snap export` keep theirs."
    ) + (
        " The character's name alone, without the realm, may still be in your notes about "
        "other players: forget looks for the whole name."
        if one
        else ""
    )


_FIELD_WORDS = {"note": "note", "guild": "guild name", "where": "instance name"}


def _still_there(spot: crewedit.NameSpot, rel: str) -> str:
    """Why a forget refuses when the name would still be in a file: where,
    in the player's words (the text itself is never quoted), and what to do."""
    then = "then run the forget again. Nothing was changed."
    if spot.player is not None and spot.field == "note":
        return (
            f"The name is also in your note about {spot.player!r} in {rel}, so the forget "
            "would not remove every copy of it. Change or clear that note with `wowlab crew "
            f"note`, {then}"
        )
    if spot.player is not None and spot.field == "key":
        return (
            f"{spot.player!r} in {rel} is the same name in another spelling, so the forget "
            f"would not remove every copy of it. Forget that entry too, {then}"
        )
    if spot.player is not None:
        what = _FIELD_WORDS.get(spot.field or "", "record")
        return (
            f"The name is also in the {what} kept for {spot.player!r} in {rel}, which wowlab "
            "does not change, so the forget would not remove every copy of it. Forget that "
            f"player too, {then}"
        )
    return (
        f"The name is also elsewhere in {rel}, outside the crew book's players, so the forget "
        "would not remove every copy of it. Nothing was changed."
    )


def _check_pin(pin: list[str] | None, got: Sequence[str | None], rel: str) -> None:
    """Refuse (exit 3) when this run's pin is not the plan's."""
    if pin is None or list(pin) == list(got):
        return
    raise CliError(
        f"{rel} or its backup is not as the plan read it, or the change would now write other "
        "bytes: it changed after the plan was made, so nothing was written; run the command "
        "again to see the new plan",
        EXIT_REFUSED,
    )


def _write(
    edit: _Edit,
    *,
    new: bytes,
    new_backup: bytes | None,
    pin: list[str] | None,
    label: str,
    store: snapshot.SnapshotStore,
) -> str:
    """Write the change through the write gate and return its journal
    record. The pin, when given, is checked again under the gate's locks: a
    file that changed since the plan rolls the whole change back."""
    # (path, bytes as read, bytes to write, the pin's two parts for it):
    # only a file whose bytes change is written.
    writes: list[tuple[str, bytes, bytes, list[str]]] = []
    if new != edit.data:
        writes.append((edit.rel, edit.data, new, pin[0:2] if pin else []))
    if new_backup is not None:
        assert edit.backup_rel is not None and edit.backup_data is not None
        writes.append((edit.backup_rel, edit.backup_data, new_backup, pin[2:4] if pin else []))
    with guard.transaction(edit.flavor, label=label, store=store.path) as tx:
        for rel, _, data, _ in writes:
            tx.write(rel, data)
        befores = [item.before for item in tx.plan]
        afters = [item.after for item in tx.plan]
        if pin is not None and [[b, a] for b, a in zip(befores, afters, strict=True)] != [
            w[3] for w in writes
        ]:
            raise CliError(
                f"{edit.rel} or its backup changed after the plan was made, so the change was "
                "rolled back; run the command again to see the new plan",
                EXIT_REFUSED,
            )
        if befores != [_sha(before) for _, before, _, _ in writes]:
            raise CliError(
                f"{edit.rel} or its backup changed after it was read, so the change was rolled "
                "back; run the command again"
            )
    # The record this transaction journaled: the newest one with this label
    # and exactly these paths and hashes (another process's write, journaled
    # after it, is never taken for it).
    mine = [(rel, _sha(before), _sha(data)) for rel, before, data, _ in writes]
    for record in reversed(guard.history(store=store.path)):
        if record.label == label and [(c.path, c.before, c.after) for c in record.paths] == mine:
            return record.id
    raise CliError(  # pragma: no cover - the gate journals every committed write
        "the change was written but its journal record was not found; `wowlab undo` lists it"
    )


def _apply(
    edit: _Edit,
    report: Callable[..., CrewEditReport],
    *,
    new: bytes,
    new_backup: bytes | None,
    pin: list[str] | None,
    got: Sequence[str | None],
    label: str,
    question: str,
    plan: Callable[[Callable[[str], None]], None],
    done: Callable[[CrewEditReport], None],
    store: snapshot.SnapshotStore,
    yes: bool,
    json_out: bool,
) -> None:
    """Show the plan, ask, write through the gate, say what was done."""
    _check_pin(pin, got, edit.rel)
    show = _say if not json_out else _say_err
    if not yes:
        plan(show)
    try:
        _confirm(question, yes)
    except typer.Exit:
        if json_out:
            _emit(report(written=False))
        raise
    record = _write(edit, new=new, new_backup=new_backup, pin=pin, label=label, store=store)
    finished = report(written=True, transaction=record)
    if json_out:
        _emit(finished)
        return
    if yes:
        plan(_say)
    done(finished)


CrewExpectForgetOpt = Annotated[
    str | None,
    typer.Option(
        "--expect",
        metavar="PIN",
        help=_EXPECT_HELP.format(fields=", ".join(_EXPECT_FIELDS_OF["forget"])),
    ),
]
CrewExpectOneOpt = Annotated[
    str | None,
    typer.Option(
        "--expect",
        metavar="PIN",
        help=_EXPECT_HELP.format(fields=", ".join(_EXPECT_FIELDS_OF["rate"])),
    ),
]
_NAME_HELP = (
    "The player as crew list shows them. Case, spaces and apostrophes are ignored when no "
    "stored name matches exactly; with several matches nothing is changed."
)
CrewNameArg = Annotated[str, typer.Argument(metavar="NAME-REALM", help=_NAME_HELP)]


@crew_app.command(
    "forget", short_help="Remove one player, or every player, from the crew book (asks first)."
)
@_handled
def crew_forget(
    name: Annotated[
        str | None,
        typer.Argument(metavar="NAME-REALM", help=_NAME_HELP, show_default=False),
    ] = None,
    all_players: Annotated[
        bool,
        typer.Option("--all", help="Forget every player; the book stays on or off as it is."),
    ] = False,
    expect: CrewExpectForgetOpt = None,
    account: AccountOpt = None,
    yes: YesOpt = False,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
) -> None:
    """Remove a player (or every player, with --all) from the crew book: from
    WowLab.lua and from the game's backup of it, WowLab.lua.bak, so the name
    is in neither file afterwards. Whether the book is on, and when it was
    started, stay as they are.

    Shows the plan and asks first, then writes through the write gate: the
    game must be closed, a snapshot is taken first, and `wowlab undo` brings
    the player back. That snapshot, the earlier ones, and archives made with
    snap export still hold the player.

    \f
    JSON: CrewEditReport."""
    if (name is None) != all_players:
        raise CliError("name one player, or give --all (not both)", EXIT_USAGE)
    pin = _pin(expect, "forget")
    store = snapshot.SnapshotStore()
    edit = _Edit(_open_book(root, flavor, account), "forget")
    if edit.backup_error is not None:
        raise CliError(
            f"The game's backup of the crew book, {edit.backup_rel}, cannot be used: "
            f"{edit.backup_error}. A forget must clear it too, so nothing was changed."
        )
    live_keys = crewedit.keys_held(edit.data)
    backup_keys = crewedit.keys_held(edit.backup_data) if edit.backup_data is not None else []
    known = list(dict.fromkeys([*live_keys, *backup_keys]))
    try:
        if name is None:
            keys = known
            new = crewedit.forget_all(edit.data)
            new_backup = (
                crewedit.forget_all(edit.backup_data) if edit.backup_data is not None else None
            )
        else:
            keys = [_one_key(known, name, edit)]
            new = crewedit.forget(edit.data, keys)
            new_backup = (
                crewedit.forget(edit.backup_data, keys) if edit.backup_data is not None else None
            )
    except crewedit.CrewEditError as exc:
        raise CliError(str(exc)) from None
    for rel, after in ((edit.rel, new), (edit.backup_rel, new_backup)):
        left = crewedit.names_left(after, keys) if after is not None and rel else []
        if left:
            raise CliError(_still_there(left[0], rel or edit.rel))
    backup_changed = new_backup is not None and new_backup != edit.backup_data
    outcome: BackupOutcome = (
        "absent" if edit.backup_data is None else "changed" if backup_changed else "left_alone"
    )
    changed = new != edit.data or backup_changed
    target_sha = _sha(edit.data)
    backup_sha = _sha(edit.backup_data) if edit.backup_data is not None else ABSENT
    backup_after = _sha(new_backup) if new_backup is not None and backup_changed else backup_sha
    written_sha = _sha(new) if changed else None
    count = max(len(keys), 1)

    def report(*, written: bool = False, transaction: str | None = None) -> CrewEditReport:
        return CrewEditReport(
            command="forget",
            flavor_folder=edit.flavor.folder,
            account=edit.place.account,
            file=edit.rel,
            backup_file=edit.backup_rel,
            players=keys,
            field=None,
            before=None,
            after=None,
            backup=outcome,
            changed=changed,
            written=written,
            transaction=transaction,
            target_sha256=target_sha,
            written_sha256=written_sha,
            backup_sha256=backup_sha,
            backup_written_sha256=backup_after if changed else None,
            not_reached=_not_reached(store, count, done=written, one=name is not None)
            if changed
            else None,
            notes=[],
        )

    if not changed:
        _check_pin(pin, [target_sha, None, backup_sha, None], edit.rel)
        if json_out:
            _emit(report())
            return
        _say(f"The crew book of {edit.words()} holds no player ({_AS_OF}): nothing to forget.")
        return

    def plan(show: Callable[[str], None]) -> None:
        who = keys[0] if name is not None else f"every player ({len(keys)})"
        them = "this player" if name is not None else "any player"
        show(f"Forget {who} from the crew book of {edit.words()} ({_AS_OF}):")
        if new != edit.data:
            show(f"  replace  {edit.rel}  ({_bytes(len(new))})")
        else:
            show(f"  {edit.rel} does not hold {them}: left alone")
        if outcome == "changed":
            assert new_backup is not None
            show(f"  replace  {edit.backup_rel}  ({_bytes(len(new_backup))}), the game's backup")
        elif outcome == "left_alone":
            show(f"  {edit.backup_rel}, the game's backup, does not hold {them}: left alone")
        else:
            show(f"  There is no {_BACKUP_NAME} beside it.")
        show(_not_reached(store, count, done=False, one=name is not None))

    def done(finished: CrewEditReport) -> None:
        who = keys[0] if name is not None else _plural(len(keys), "player")
        names = "the name" if name is not None else "their names"
        _say(
            f"Forgot {who}: neither {labaddon.LAB_FILE_NAME} nor {_BACKUP_NAME} holds {names} now."
        )
        assert finished.not_reached is not None
        _say(finished.not_reached)

    _apply(
        edit,
        report,
        new=new,
        new_backup=new_backup if backup_changed else None,
        pin=pin,
        got=[target_sha, written_sha, backup_sha, backup_after],
        label=f"crew forget ({_plural(len(keys), 'player')})",
        question="Forget this player?" if name is not None else "Forget every player?",
        plan=plan,
        done=done,
        store=store,
        yes=yes,
        json_out=json_out,
    )


def _set(
    command: Literal["rate", "note"],
    field: crewedit.Field,
    name: str,
    value: str | None,
    *,
    expect: str | None,
    account: str | None,
    yes: bool,
    root: Path | None,
    flavor: str | None,
    json_out: bool,
) -> None:
    """`crew rate` and `crew note`: one field of one player's entry."""
    pin = _pin(expect, command)
    store = snapshot.SnapshotStore()
    edit = _Edit(_open_book(root, flavor, account), command)
    readable = [p.key for p in edit.book.players]
    unread = [u.key for u in edit.book.unread]
    key = _one_key(list(dict.fromkeys([*readable, *unread])), name, edit)
    if key not in readable:
        raise CliError(
            f"the entry of {key!r} in the crew book of {edit.words()} could not be read, so "
            "wowlab does not change it. Nothing was changed."
        )
    player = next(p for p in edit.book.players if p.key == key)
    before = player.rating if field == "rating" else player.note
    try:
        new = crewedit.set_field(edit.data, key, field, value)
    except crewedit.CrewEditError as exc:
        raise CliError(str(exc)) from None
    changed = new != edit.data
    target_sha = _sha(edit.data)
    written_sha = _sha(new) if changed else None

    def report(*, written: bool = False, transaction: str | None = None) -> CrewEditReport:
        return CrewEditReport(
            command=command,
            flavor_folder=edit.flavor.folder,
            account=edit.place.account,
            file=edit.rel,
            backup_file=None,
            players=[key],
            field=field,
            before=before,
            after=value,
            backup=None,
            changed=changed,
            written=written,
            transaction=transaction,
            target_sha256=target_sha,
            written_sha256=written_sha,
            backup_sha256=None,
            backup_written_sha256=None,
            not_reached=None,
            notes=[],
        )

    def said(text: str | None) -> str:
        if text is None:
            return "none"
        return f'"{text}"' if field == "note" else text

    if not changed:
        _check_pin(pin, [target_sha, None], edit.rel)
        if json_out:
            _emit(report())
            return
        _say(f"{key}'s {field} is already {said(value)}: nothing was changed.")
        return

    def plan(show: Callable[[str], None]) -> None:
        show(
            f"Change {key}'s {field} in the crew book of {edit.words()} ({_AS_OF}): "
            f"{said(before)} -> {said(value)}"
        )
        show(f"  replace  {edit.rel}  ({_bytes(len(new))})")

    def done(finished: CrewEditReport) -> None:
        _say(f"Changed {key}'s {field} to {said(value)}. `wowlab undo` puts back what was there.")

    _apply(
        edit,
        report,
        new=new,
        new_backup=None,
        pin=pin,
        got=[target_sha, written_sha],
        label=f"crew {command} (1 player)",
        question=f"Write the {field}?",
        plan=plan,
        done=done,
        store=store,
        yes=yes,
        json_out=json_out,
    )


@crew_app.command("rate", short_help="Rate one player good or bad, or clear it (asks first).")
@_handled
def crew_rate(
    name: CrewNameArg,
    rating: Annotated[
        str,
        typer.Argument(metavar="RATING", help="good, bad, or none to take the rating away."),
    ],
    expect: CrewExpectOneOpt = None,
    account: AccountOpt = None,
    yes: YesOpt = False,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
) -> None:
    """Rate a player in the crew book good or bad, or take the rating away
    with none: the same change /wowlab crew good, bad and clear make in
    game. Nothing else in the file changes.

    Shows the plan and asks first, then writes through the write gate: the
    game must be closed, a snapshot is taken first, and `wowlab undo` puts
    the old rating back.

    \f
    JSON: CrewEditReport."""
    if rating not in ("good", "bad", "none"):
        raise CliError(f"the rating is good, bad or none, not {rating!r}", EXIT_USAGE)
    _set(
        "rate",
        "rating",
        name,
        None if rating == "none" else rating,
        expect=expect,
        account=account,
        yes=yes,
        root=root,
        flavor=flavor,
        json_out=json_out,
    )


@crew_app.command("note", short_help="Write your note about one player (asks first).")
@_handled
def crew_note(
    name: CrewNameArg,
    text: Annotated[
        str,
        typer.Argument(
            metavar="TEXT",
            help=f"At most {crewedit.NOTE_MAX_BYTES} bytes after trimming spaces, with no "
            "control character and no |. Empty text takes the note away.",
        ),
    ],
    expect: CrewExpectOneOpt = None,
    account: AccountOpt = None,
    yes: YesOpt = False,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
) -> None:
    """Write your note about a player in the crew book, or take it away with
    empty text: the same change /wowlab crew note makes in game. A note over
    200 bytes, or holding a control character or |, is refused rather than
    cut. Nothing else in the file changes.

    Shows the plan and asks first, then writes through the write gate: the
    game must be closed, a snapshot is taken first, and `wowlab undo` puts
    the old note back.

    \f
    JSON: CrewEditReport."""
    try:
        note = crewedit.check_note(text)
    except crewedit.CrewEditError as exc:
        raise CliError(str(exc), EXIT_USAGE) from None
    _set(
        "note",
        "note",
        name,
        note,
        expect=expect,
        account=account,
        yes=yes,
        root=root,
        flavor=flavor,
        json_out=json_out,
    )
