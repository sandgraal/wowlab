"""`wowlab log tail`, and the combat-log folder helpers other commands share
(M16-111)."""

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Literal

import typer
from pydantic import Field, RootModel

from wowlab_core import (
    combatlog,
    layout,
    longpath,
)
from wowlab_core.cli._common import (
    EXIT_OK,
    FlavorOpt,
    JsonOpt,
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
