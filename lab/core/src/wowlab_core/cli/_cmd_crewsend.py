"""`wowlab crew send` (M22-04): each player's record, written into
the game account's `WowLab.lua` as `WowLabDeskDB` for the line the
lab-addon adds to a player's tooltip in game.

The records are what wowlab's crew database adds up (`nemesis`), synced
from the crew book first, and Spy's kill-on-sight lists (`spy`, read only).
The table is built by `desktable`; this command shows the plan, asks, and
writes it through the write gate (`guard`, L2): the game must be closed, a
snapshot is taken first, and `wowlab undo` takes the table out again. It
declares `--expect`, as `crew rate` does: the pin is the file's SHA-256 as
read and the SHA-256 of the bytes it would write. The guard label names no
player. Nothing leaves the computer.
"""

import time
from typing import Annotated

import pydantic
import typer

from wowlab_core import (
    crewbook,
    crewdb,
    desktable,
    guard,
    install,
    labaddon,
    nemesis,
    snapshot,
    spy,
)
from wowlab_core.cli._cmd_crew import (
    _AS_OF,
    _SHA256_TEXT,
    ABSENT,
    _from_database,
    _open_book,
    _place_words,
    _sha,
)
from wowlab_core.cli._common import (
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
    _emit,
    _handled,
    _listed,
    _Out,
    _plural,
    _say,
    _say_err,
    _say_notes,
    crew_app,
)

# The pin `--expect` takes, in its order: the file as read, and the bytes it
# would write (`absent` when there is nothing to change).
SEND_PIN_FIELDS = ("target_sha256", "written_sha256")

# A sync that ran, or had nothing to run: the database then counts what the
# saved file holds, so the counters written beside it are the file's.
_SYNCED = ("synced", "unchanged", "not_run")

_SEND_NOTES = [
    "In game, the line adds the matches the crew book records after this write. Everything "
    "else on it (the open-world fights, the matches the game's list no longer holds, the "
    "Nemesis and Rival team marks and Spy's kill-on-sight lists) stays as of this write until "
    "you send the records again.",
    nemesis.NEMESIS_RULE,
    nemesis.RIVAL_RULE,
    "A player is marked kill on sight when the Spy addon's list of any character of this game "
    "account holds them.",
    "The line shows out of combat only, and only while the crew book is on in game.",
]
_BOOK_OFF = "The line shows only while the crew book is on in game: type /wowlab crew on."


class CrewSendReport(_Out):
    """`wowlab crew send --json`: the records written, or that
    would be written, into one game account's `WowLab.lua` for the in-game
    tooltip line.

    `day` is the day the table says it was written (this computer's date).
    `players` counts the rows it holds; of them, `nemeses`, `rivals` and
    `kos` the marked ones (Nemesis, Rival team, on a Spy kill-on-sight
    list). `left_out` counts the players past the table's limit
    (`limit`). `before` is the table the file holds now (None: none).
    `book_state` is the crew book's state in the file; the line shows only
    while it is `on`. `spy_unreadable` counts the Spy files that could not
    be read (their marks are missing). `sync` is the crew database's sync
    this run made first.

    `changed` is false when the file already holds exactly these records.
    The pin `--expect` takes is `target_sha256` (the file as read) and
    `written_sha256` (the bytes it would write; None with nothing to
    change), joined by `:`. `transaction` names the journal record once
    written."""

    flavor_folder: str
    account: str
    file: str
    day: str
    players: int
    nemeses: int
    rivals: int
    kos: int
    left_out: int
    limit: int = desktable.MAX_PLAYERS
    before: desktable.DeskTableRead | None
    book_state: crewbook.State
    spy_unreadable: int
    changed: bool
    written: bool
    transaction: str | None
    target_sha256: str
    written_sha256: str | None
    sync: crewdb.SyncOutcome | None
    notes: list[str] = pydantic.Field(default_factory=list)


def _send_pin(expect: str | None) -> list[str] | None:
    """`--expect`, split; a usage error when it is not the pin an earlier
    run's --json gives."""
    if expect is None:
        return None
    got = expect.split(":")
    ok = (
        len(got) == len(SEND_PIN_FIELDS)
        and _SHA256_TEXT.fullmatch(got[0]) is not None
        and (_SHA256_TEXT.fullmatch(got[1]) is not None or got[1] == ABSENT)
    )
    if not ok:
        raise CliError(
            f"--expect {expect!r} is not {':'.join(SEND_PIN_FIELDS)}, the values an earlier "
            "run's --json gives",
            EXIT_USAGE,
        )
    return got


_SEND_REFUSED = (
    "{rel} is not as the plan read it, or the records would now be other ones: it changed "
    "after the plan was made, so nothing was written; run the command again to see the new plan"
)


def _send_write(
    flavor: install.Flavor,
    rel: str,
    data: bytes,
    new: bytes,
    *,
    pin: list[str] | None,
    label: str,
    store: snapshot.SnapshotStore,
) -> str:
    """Write `new` over `rel` through the write gate and return the journal
    record. The pin, when given, is checked again under the gate's locks."""
    with guard.transaction(flavor, label=label, store=store.path) as tx:
        tx.write(rel, new)
        befores = [item.before for item in tx.plan]
        afters = [item.after for item in tx.plan]
        if pin is not None and [befores[0], afters[0]] != pin:
            raise CliError(_SEND_REFUSED.format(rel=rel), EXIT_REFUSED)
        if befores != [_sha(data)]:
            raise CliError(
                f"{rel} changed after it was read, so the change was rolled back; run the "
                "command again"
            )
    mine = [(rel, _sha(data), _sha(new))]
    for record in reversed(guard.history(store=store.path)):
        if record.label == label and [(c.path, c.before, c.after) for c in record.paths] == mine:
            return record.id
    raise CliError(  # pragma: no cover - the gate journals every committed write
        "the change was written but its journal record was not found; `wowlab undo` lists it"
    )


@crew_app.command(
    "send",
    short_help="Write your record against each player into the game (asks first).",
)
@_handled
def crew_send(
    expect: Annotated[
        str | None,
        typer.Option(
            "--expect",
            metavar="PIN",
            help="Write only if WowLab.lua is still as an earlier run's --json read it and the "
            "records would be the same bytes (its target_sha256 and written_sha256, joined by "
            "':'): else refused, exit 3, nothing written. For a plan shown by one run and "
            "confirmed by another.",
        ),
    ] = None,
    account: AccountOpt = None,
    yes: YesOpt = False,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
    notes: NotesOpt = False,
) -> None:
    """Write each player's record into this game account's WowLab.lua, for
    the line the WowLab addon adds when you hover a player in game: how
    often you fought them, the killing blows each way, the matches your side
    won, and the Nemesis, Rival team and kill-on-sight marks. The game reads
    them at your next login or /reload; until you send them again, the line
    adds only the matches the crew book records in game.

    Syncs the crew book into wowlab's crew database first, and reads the
    Spy addon's kill-on-sight lists. Shows the plan and asks first, then
    writes through the write gate: the game must be closed, a snapshot is
    taken first, and `wowlab undo` takes the records out again. Nothing
    leaves this computer.

    \f
    JSON: CrewSendReport."""
    pin = _send_pin(expect)
    store = snapshot.SnapshotStore()
    opened = _open_book(root, flavor, account)
    place = opened.place
    if place is None or place.file is None or opened.where is None:
        where = _place_words(place) if place is not None else "any game account wowlab read"
        raise CliError(
            f"There is no WowLab.lua for {where} yet, so there is nowhere to write the records: "
            "log in once with the WowLab addon on, then log out. Nothing was changed."
        )
    flavor_found, lay = opened.where
    rel = place.file
    outcome, rows = _from_database(opened)
    if outcome is not None and outcome.state not in _SYNCED:
        raise CliError(
            f"{outcome.words} The records were not written, since they would not match what "
            "the crew book holds. Nothing was changed."
        )
    data = _listed(labaddon.read_file_bytes, lay, rel)
    if opened.data is not None and data != opened.data:
        raise CliError(f"{rel} changed while wowlab read it; run the command again.")
    survey = spy.survey(lay, account=place.account)
    kos = {k.key for c in survey.characters for k in c.kos}
    records = [(r.key, r.record, r.record.last if r.record is not None else None) for r in rows]
    desk_rows, left_out = desktable.rows_from(records, opened.book.pvp, kos)
    day = time.strftime("%Y-%m-%d", time.localtime())
    before = desktable.read_table(data)
    try:
        new = desktable.with_table(data, desk_rows, day)
    except desktable.DeskTableError as exc:
        raise CliError(str(exc)) from None
    changed = new != data
    target_sha = _sha(data)
    written_sha = _sha(new) if changed else None
    if pin is not None and pin != [target_sha, written_sha or ABSENT]:
        raise CliError(_SEND_REFUSED.format(rel=rel), EXIT_REFUSED)
    count = len(desk_rows)
    nemeses = sum(r.nemesis for r in desk_rows)
    rivals = sum(r.rival for r in desk_rows)
    marked = sum(r.kos for r in desk_rows)
    words = _place_words(place)
    caveat = _BOOK_OFF if opened.book.state != "on" else None

    def report(*, written: bool = False, transaction: str | None = None) -> CrewSendReport:
        return CrewSendReport(
            flavor_folder=place.flavor_folder,
            account=place.account,
            file=rel,
            day=day,
            players=count,
            nemeses=nemeses,
            rivals=rivals,
            kos=marked,
            left_out=left_out,
            before=before,
            book_state=opened.book.state,
            spy_unreadable=len(survey.errors),
            changed=changed,
            written=written,
            transaction=transaction,
            target_sha256=target_sha,
            written_sha256=written_sha,
            sync=outcome,
            notes=_SEND_NOTES,
        )

    if not changed:
        if json_out:
            _emit(report())
            return
        _say(
            f"The game already holds these records ({_plural(count, 'player')}, written "
            f"{day}): nothing was changed."
        )
        _say_notes(_SEND_NOTES, notes, caveat)
        return

    def lines() -> list[str]:
        out = [
            f"Write the records of {_plural(count, 'player')} into WowLab.lua of {words} "
            f"({_AS_OF}), for the line WowLab adds to a player's tooltip in game:",
            f"  {nemeses} Nemesis, {rivals} Rival team, {marked} on a Spy kill-on-sight list",
        ]
        if before is not None:
            was = f"written {before.day}" if before.day is not None else "of an unknown day"
            out.append(f"  replaces the records {was} ({_plural(before.players, 'player')})")
        if left_out:
            out.append(
                f"  {_plural(left_out, 'player')} left out: the game is given at most "
                f"{desktable.MAX_PLAYERS:,}, the marked players and the latest fights first"
            )
        if survey.errors:
            out.append(
                f"  {_plural(len(survey.errors), 'Spy file')} could not be read; the marks "
                "from them are missing"
            )
        out.append(f"  replace  {rel}  ({_bytes(len(new))})")
        return out

    show = _say if not json_out else _say_err
    if not yes:
        for line in lines():
            show(line)
    try:
        _confirm("Write the records?", yes)
    except typer.Exit:
        if json_out:
            _emit(report())
        raise
    record = _send_write(
        flavor_found,
        rel,
        data,
        new,
        pin=pin,
        label=f"crew send ({_plural(count, 'player')})",
        store=store,
    )
    finished = report(written=True, transaction=record)
    if json_out:
        _emit(finished)
        return
    if yes:
        for line in lines():
            _say(line)
    _say(
        f"Wrote the records of {_plural(count, 'player')}. The game reads them at your next "
        "login or /reload; `wowlab undo` takes them out again."
    )
    _say_notes(_SEND_NOTES, notes, caveat)
