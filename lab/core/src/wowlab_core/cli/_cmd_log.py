"""`wowlab log tail`, `wowlab log practice` (M22-20), and the combat-log
folder helpers other commands share (M16-111)."""

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Literal

import typer
from pydantic import Field, RootModel

from wowlab_core import (
    combatlog,
    gamedata,
    layout,
    longpath,
    practice,
)
from wowlab_core.cli._common import (
    EXIT_OK,
    EXIT_USAGE,
    CliError,
    FlavorOpt,
    JsonOpt,
    NotesOpt,
    RootOpt,
    _emit,
    _handled,
    _json_text,
    _map_strings,
    _note,
    _open,
    _Out,
    _Report,
    _say,
    _say_err,
    _say_notes,
    log_app,
)

# ─── log ─────────────────────────────────────────────────────────────────────

_LOG_POLL = 0.5  # seconds between looks at the log while following
_follow_sleep: Callable[[float], None] = time.sleep  # a test replaces it
MAX_TAIL_LINES = 100_000
_NO_LOG = (
    "no combat log under Logs/ (the client creates one when combat logging is turned on, "
    "by /combatlog or by an addon)"
)
_BATCHES = (
    "the client adds to this file in batches, and only while combat logging is on: the "
    "latest events may not be on disk yet, and if logging is off, this file ends where "
    "logging was turned off or the client closed"
)
_STILL_WRITING = "the last line is still being written and is not shown"


class LogFollowing(_Out):
    """From here on, lines come from `file` (`log tail --follow`)."""

    kind: Literal["following"] = "following"
    file: str  # relative to the flavor folder
    reason: Literal["start", "rotated", "truncated", "replaced"]


type LogEntry = Annotated[
    combatlog.Record | combatlog.Unparsed | LogFollowing, Field(discriminator="kind")
]


class LogTailLine(RootModel[LogEntry]):
    """`wowlab log tail --follow --json`: one per line of output."""


class LogTailReport(_Out):
    """`wowlab log tail --json`."""

    file: str | None  # relative to the flavor folder; None when there is no combat log
    entries: list[combatlog.Entry]
    notes: list[
        str
    ]  # the caveats the text output also prints (the no-log message on stdout, the others on stderr)


def _logs_dir(lay: layout.Layout) -> Path | None:
    """The flavor's `Logs/` folder, however its case is spelled, or None."""
    for child in sorted(longpath.iterdir(lay.flavor_path)):
        if child.name.casefold() == "logs" and longpath.is_dir(child):
            return child
    return None


def _rel(lay: layout.Layout, path: Path) -> str:
    try:
        return path.relative_to(lay.flavor_path).as_posix()
    except ValueError:
        return str(path)


_FOLLOWING_WORDS = {
    "start": "",
    "rotated": " (a new log file)",
    "truncated": " (the file shrank or its earlier bytes changed; reading it from the start)",
    "replaced": " (a different file under this name; reading it from the start)",
}


def _print_log_entry(entry: combatlog.Entry | LogFollowing, json_out: bool) -> None:
    if json_out:
        line = LogTailLine(entry).model_dump(mode="json")
        _Report.printed = True
        typer.echo(json.dumps(_map_strings(line, _json_text), ensure_ascii=True))
    elif isinstance(entry, LogFollowing):
        _say(f"==> {entry.file}{_FOLLOWING_WORDS[entry.reason]} <==")
    elif isinstance(entry, combatlog.Unparsed):
        cut = ""
        if entry.truncated:
            kept = len(entry.raw.encode("utf-8", "surrogateescape"))  # bytes, not characters
            cut = f" (cut to its first {kept} of {entry.length} bytes)"
        _say(f"(not tokenized: {entry.reason}){cut} {entry.raw}")
    else:
        _say(entry.raw)


def _follow_log(
    lay: layout.Layout,
    newest: Path | None,
    end: int | None,
    entries: list[combatlog.Entry],
    json_out: bool,
) -> None:
    """`log tail --follow`: print as the client appends, until Ctrl-C."""
    logs = _logs_dir(lay)
    if newest is None:
        _note("{}; waiting for one", _NO_LOG)
    while logs is None:
        _follow_sleep(_LOG_POLL)
        logs = _logs_dir(lay)
    # With no log when the command started, the first one found is read whole.
    stream = combatlog.follow(
        newest or logs,
        offset=end if newest is not None else 0,
        poll_interval=_LOG_POLL,
        sleep=_follow_sleep,
    )
    noted = False
    try:
        for item in stream:
            if isinstance(item, combatlog.Following):
                _print_log_entry(
                    LogFollowing(file=_rel(lay, item.path), reason=item.reason), json_out
                )
                if not noted:
                    _note(_BATCHES)
                    noted = True
                if item.reason == "start":
                    for entry in entries:
                        _print_log_entry(entry, json_out)
                    entries = []
            else:
                _print_log_entry(item, json_out)
    finally:
        stream.close()


@log_app.command("tail", short_help="The last lines of the newest combat log.")
@_handled
def log_tail(
    follow: Annotated[
        bool,
        typer.Option(
            "--follow",
            "-f",
            help="Keep printing lines as the client appends them, including in a "
            "new log file, until interrupted. Waits for a log if there is none yet.",
        ),
    ] = False,
    lines: Annotated[
        int,
        typer.Option(
            "--lines",
            "-n",
            min=0,
            max=MAX_TAIL_LINES,
            help=f"How many of the last lines to print (at most {MAX_TAIL_LINES:,}).",
        ),
    ] = 10,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
) -> None:
    """The last lines of the newest combat log (Logs/WoWCombatLog*.txt), tokenized.

    \f
    JSON: LogTailReport; with --follow, one LogTailLine per line of output."""
    # Line-delimited before anything can fail: an error is one line too.
    _Report.lines = follow and json_out
    _, _, lay = _open(root, flavor)
    logs = _logs_dir(lay)
    newest = combatlog.newest_log(logs) if logs is not None else None
    entries: list[combatlog.Entry] = []
    end: int | None = None
    notes = [_NO_LOG]
    if newest is not None:
        # Before the read: a line finished since is not "partial".
        size = longpath.stat(newest).st_size
        entries, end = combatlog.tail(newest, lines)
        notes = [_BATCHES] + ([_STILL_WRITING] if end < size else [])
    if follow:
        try:
            _follow_log(lay, newest, end, entries, json_out)
        except KeyboardInterrupt:
            raise typer.Exit(EXIT_OK) from None
        return
    report = LogTailReport(file=_rel(lay, newest) if newest else None, entries=entries, notes=notes)
    if json_out:
        _emit(report)
        return
    if newest is None:
        _say(_NO_LOG)
        return
    _print_log_entry(LogFollowing(file=_rel(lay, newest), reason="start"), json_out)
    for entry in entries:
        _print_log_entry(entry, json_out)
    for note in notes:
        _say_err(note)


# ─── log practice (M22-20) ───────────────────────────────────────────────────

MAX_GAP_SECONDS = 600.0
_PRACTICE_NOTES = [
    "Only what the client wrote while combat logging was on, and only what it showed you. "
    "Damage counts your own hits and your pets' and summons'; other players' damage on the "
    "same targets is left out.",
    "A segment is a run of your casts and damage with no pause longer than the gap; "
    "logging switched off and on, or another of your characters, starts a new one.",
    "Global cooldowns and cooldowns come from the game's spell table for the installed "
    "version; haste and talents that shorten them are not read, and a log recorded before "
    "a patch may not match that table.",
    "A channelled spell counts only its global cooldown: the log does not say when a channel ends.",
    "Buffs are the ones on you, from their applied and removed lines; one removed with no "
    "applied line before it was on you when logging began.",
]


class PracticeReport(_Out):
    """`wowlab log practice --json`: the segments of one combat log, and
    with `--segment` the summary of one of them."""

    file: str | None  # relative to the flavor folder; None when there is no combat log
    logs: list[str]  # every combat log's file name under Logs/, oldest first
    gap: float  # seconds: a longer pause starts a new segment
    segments: list[practice.PracticeSegment]
    segments_left_out: int = 0
    summary: practice.PracticeSummary | None = None
    # Whether the installed version's spell timing table is cached (`table`),
    # or not (`missing`: every global cooldown is assumed 1.5 s and no
    # cooldown use is given), the version looked for, and the wowlab
    # arguments that download the table when it is missing.
    timings: Literal["table", "missing"]
    timing_build: str | None
    fetch: list[str] | None = None
    notes: list[str]


def _practice_timings(version: str | None) -> dict[int, practice.SpellTiming] | None:
    """The spell timings cached for `version`, or None. Never fetches."""
    if version is None:
        return None
    try:
        rows = gamedata.cached_rows(practice.TIMING_TABLE, version)
        return practice.timings_from_rows(rows) if rows is not None else None
    except (ValueError, OSError, gamedata.GameDataError):
        return None


def _practice_clock(timestamp: str) -> str:
    """A log timestamp's time of day to the second, as written."""
    return timestamp.rpartition(" ")[2].split(".", 1)[0]


def _practice_counted(n: int, word: str) -> str:
    return f"{n:,} {word}" if n == 1 else f"{n:,} {word}s"


def _practice_pct(share: float) -> str:
    return f"{share * 100:.0f}%"


def _say_segments(report: PracticeReport) -> None:
    _say(f"Segments in {report.file}:")
    for seg in report.segments:
        hit = f", {', '.join(seg.targets)}" if seg.targets else ""
        _say(
            f"  {seg.index}. {_practice_clock(seg.start)} to {_practice_clock(seg.end)}, {seg.seconds:.0f} s, "
            f"{_practice_counted(seg.casts, 'cast')}, {seg.damage:,} damage{hit}"
        )
    if report.segments_left_out:
        _say(f"  and {_practice_counted(report.segments_left_out, 'more segment')} not listed")
    _say("Pick one with --segment N.")


def _say_practice(report: PracticeReport, s: practice.PracticeSummary) -> None:
    seg = s.segment
    who = f", {seg.name}" if seg.name else ""
    _say(f"Segment {seg.index} of {report.file}: {s.seconds:.1f} s{who}")
    d = s.damage
    _say(f"Damage: {d.total:,} ({s.dps:,.1f} per second)")
    for src in d.by_source:
        label = "you" if src.you else (src.name or src.guid)
        _say(f"  {label}: {src.amount:,} ({_practice_pct(src.share)})")
    if d.others_on_targets:
        _say(f"  left out: {d.others_on_targets:,} by others on the same targets")
    _say(f"Casts: {s.casts:,}")
    for sp in s.spells:
        every = f", every {sp.median_gap:.1f} s" if sp.median_gap is not None else ""
        _say(f"  {sp.spell_name}: {sp.casts:,} ({sp.per_minute:.1f} a minute{every})")
    g = s.gcd
    _say(
        f"Global cooldown: busy {g.busy_seconds:.1f} s of {s.seconds:.1f} s ({_practice_pct(g.share)}), "
        f"{_practice_counted(g.pauses, 'pause')} over {practice.PAUSE_SECONDS:.0f} s, "
        f"longest idle {g.longest_pause:.1f} s"
    )
    if s.cooldowns:
        _say("Cooldowns:")
        for cd in s.cooldowns:
            _say(
                f"  {cd.spell_name} ({cd.cooldown_ms / 1000:g} s): {cd.casts:,} of "
                f"{cd.possible:,} ({_practice_pct(cd.share)})"
            )
    if s.buffs:
        _say("Buffs on you:")
        for b in s.buffs:
            _say(f"  {b.spell_name}: {_practice_pct(b.share)} ({b.seconds:.1f} s)")


@log_app.command(
    "practice",
    short_help="Casts, cooldowns and buffs in one stretch of a combat log.",
)
@_handled
def log_practice(
    file: Annotated[
        str | None,
        typer.Option("--file", help="The combat log's file name under Logs/. Default: the newest."),
    ] = None,
    segment: Annotated[
        int | None,
        typer.Option(
            "--segment",
            min=1,
            help="The segment to summarize, by its number in the list. Without it, the "
            "segments are listed.",
        ),
    ] = None,
    gap: Annotated[
        float,
        typer.Option(
            "--gap",
            min=1.0,
            max=MAX_GAP_SECONDS,
            help="Seconds without a cast or a hit of yours that end a segment.",
        ),
    ] = practice.DEFAULT_GAP_SECONDS,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
    notes_out: NotesOpt = False,
) -> None:
    """Your casts per spell, how busy your global cooldown kept you, how often
    you used each cooldown and how long each buff was on you, for one
    stretch of play in a combat log, such as a session at a training dummy.

    Without --segment, lists the log's segments: runs of your casts and of
    your and your pets' damage. Reads the log only; nothing is written.

    \f
    JSON: PracticeReport."""
    _, chosen, lay = _open(root, flavor)
    logs_dir = _logs_dir(lay)
    logs = combatlog.find_logs(logs_dir) if logs_dir is not None else []
    names = [p.name for p in logs]
    if file is not None and file not in names:
        listed = ", ".join(names) or "none"
        raise CliError(f"no combat log {file!r} under Logs/ (logs: {listed})", EXIT_USAGE)
    version = chosen.version
    timings = _practice_timings(version)
    fetch = (
        None
        if timings is not None or version is None
        else ["db2", "fetch", practice.TIMING_TABLE, "--build", version]
    )
    path = next(p for p in logs if p.name == file) if file else (logs[-1] if logs else None)
    have: Literal["table", "missing"] = "table" if timings is not None else "missing"
    if path is None:
        empty = PracticeReport(
            file=None,
            logs=names,
            gap=gap,
            segments=[],
            timings=have,
            timing_build=version,
            fetch=fetch,
            notes=[_NO_LOG],
        )
        if json_out:
            _emit(empty)
            return
        _say(_NO_LOG)
        return
    scan = practice.find_segments(
        combatlog.read_log(path, events=practice.PRACTICE_EVENTS), gap=gap
    )
    summary: practice.PracticeSummary | None = None
    if segment is not None:
        if segment > len(scan.segments):
            raise CliError(
                f"no segment {segment} in {path.name} ({_practice_counted(len(scan.segments), 'segment')})",
                EXIT_USAGE,
            )
        summary = practice.summarize(
            combatlog.read_log(path, events=practice.PRACTICE_EVENTS),
            scan.segments[segment - 1],
            timings,
        )
    report = PracticeReport(
        file=_rel(lay, path),
        logs=names,
        gap=gap,
        segments=list(scan.segments),
        segments_left_out=scan.segments_left_out,
        summary=summary,
        timings=have,
        timing_build=version,
        fetch=fetch,
        notes=[_BATCHES, *_PRACTICE_NOTES],
    )
    if json_out:
        _emit(report)
        return
    caveat: str | None = None
    if not scan.segments:
        _say(f"No casts or damage of yours in {report.file}.")
    elif summary is None:
        _say_segments(report)
    else:
        _say_practice(report, summary)
        if fetch is not None:
            caveat = (
                "Every global cooldown was taken as 1.5 s and cooldown use is not shown: "
                f"wowlab {' '.join(fetch)} downloads the game's spell timings once."
            )
    _say_notes(report.notes, notes_out, caveat=caveat)
