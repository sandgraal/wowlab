"""`wowlab crew list` and `crew show` (docs/LAB_PLAN.md §20.2, M19-02).

Both read the crew book (`crewbook`) from a game account's `WowLab.lua` and
write nothing anywhere. Each takes `--flavor` and `--account` as `char`
does; with several books to choose from and none named, the candidates are
listed and the command exits 2. Every name, realm, guild, instance name and
note in the book is other people's text (or the player's own note): it is
printed only through `_say`, which escapes C0, C1 and bidi controls, never
through the player-copy rewriting wowlab's own sentences go through; `--json`
is ASCII-escaped as everywhere.
"""

import time
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated

import typer

from wowlab_core import crewbook, install, layout, luadata
from wowlab_core.cli._common import (
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
    _is_lab_file,
    _listed,
    _Out,
    _plural,
    _Report,
    _say,
    _say_notes,
    _select_account,
    _select_flavor,
    crew_app,
)
from wowlab_core.errors import WowlabError

# ─── crew ────────────────────────────────────────────────────────────────────

_AS_OF = "as of your last logout or /reload"
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
    `account` and `file` are None and `places` lists where wowlab looked."""

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
    players: list[crewbook.CrewPlayer]
    unread: list[crewbook.UnreadEntry]
    places: list[CrewPlace]
    notes: list[str]


class CrewShowReport(_Out):
    """`wowlab crew show --json`: everything the book keeps about one
    player (`player`, None when the book was never turned on), and the key
    as typed (`typed`)."""

    flavor_folder: str | None
    account: str | None
    file: str | None
    state: crewbook.State
    typed: str
    player: crewbook.CrewPlayer | None
    places: list[CrewPlace]
    notes: list[str]


class _Opened:
    """The book a crew command reads, and where it looked."""

    def __init__(
        self,
        place: CrewPlace | None,
        book: crewbook.CrewBook,
        places: list[CrewPlace],
    ) -> None:
        self.place = place
        self.book = book
        self.places = places


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
            if rel is not None:
                try:
                    book = _listed(crewbook.read_book, lay, rel)
                except _READ_ERRORS as exc:
                    book, error = None, exc
            holds = None if book is None else book.state != "not_turned_on"
            place = CrewPlace(
                flavor_folder=f.folder, account=acct.folder, file=rel, holds_book=holds
            )
            found.append((place, book, error))
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
    place, book, error = chosen
    if isinstance(error, crewbook.CrewBookError):
        # The player's words; a `--json` run's message adds the field.
        words = f"{error} ({error.detail})" if _Report.json else str(error)
        raise CliError(words) from None
    if error is not None:
        raise error
    assert book is not None
    return _Opened(place, book, places)


def _place_words(p: CrewPlace) -> str:
    return f"game account {p.account} in {p.flavor_folder}"


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


def _header(opened: _Opened) -> str:
    book, place = opened.book, opened.place
    assert place is not None
    where = _place_words(place)
    n = len(book.players) + len(book.unread)
    started = f"started {_date(book.started)}"
    if book.state == "off":
        return (
            f"The crew book of {where} is turned off ({_AS_OF}): it is not recording, and it "
            f"keeps {_plural(n, 'player')} ({started})."
        )
    if n == 0:
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
    shown = crewbook.ordered(
        crewbook.select(book.players, rated=rated, noted=noted, search=search), sort
    )
    place = opened.place
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
        total=len(book.players) + len(book.unread),
        sort=sort,
        rated=rated,
        noted=noted,
        search=search,
        players=shown,
        unread=book.unread,
        places=opened.places,
        notes=_CREW_NOTES,
    )
    if json_out:
        _emit(report)
        return
    if book.state == "not_turned_on":
        _say(_never_on(opened.places if place is None else [place]))
        return
    _say(_header(opened))
    filtered = rated is not None or noted or bool(search)
    if filtered:
        _say(f"{len(shown)} of {_plural(len(book.players), 'player')} match.")
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
    where = f"{p.where} (instance id {said(p.where_id)})" if p.where else "no instance recorded"
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
    player: crewbook.CrewPlayer | None = None
    if book.state != "not_turned_on":
        assert place is not None
        found = crewbook.match(book.players, name)
        if not found:
            unread = [u.key for u in book.unread if crewbook.fold(u.key) == crewbook.fold(name)]
            why = " (its entry could not be read)" if unread else ""
            raise CliError(f"no player {name!r} in the crew book of {_place_words(place)}{why}")
        if len(found) > 1:
            keys = ", ".join(p.key for p in found)
            raise CliError(
                f"{name!r} matches {len(found)} players ({keys}); type the name as crew list "
                "shows it",
                EXIT_USAGE,
            )
        player = found[0]
    report = CrewShowReport(
        flavor_folder=place.flavor_folder if place else None,
        account=place.account if place else None,
        file=place.file if place else None,
        state=book.state,
        typed=name,
        player=player,
        places=opened.places,
        notes=_CREW_NOTES,
    )
    if json_out:
        _emit(report)
        return
    if player is None:
        _say(_never_on(opened.places if place is None else [place]))
        return
    assert place is not None
    _say(f"{player.key}, from the crew book of {_place_words(place)}, {_AS_OF}:")
    for line in _field_lines(player):
        _say(line)
    if book.state == "off":
        _say("The crew book is turned off: it is not recording.")
    _say_notes(_CREW_NOTES, notes)
