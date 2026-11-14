"""What every `wowlab` command shares: the app, its command groups and the root
callback, exit codes, options, output and error handling, and finding the
install, flavor, account and character. The package docstring has the
conventions (M16-111)."""

import contextlib
import errno
import functools
import json
import logging
import os
import re
import shlex
import shutil
import signal
import sys
import textwrap
import time
import traceback
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from types import TracebackType
from typing import Annotated, Any, Literal, NoReturn, override

import typer
from pydantic import BaseModel, ConfigDict, Field

# Typer vendors click as `typer._click` from 0.26 (lab/core/pyproject.toml
# bounds typer to the 0.27 minor this was tested on) and gives its exceptions
# and context no public name: the root command (`_WowlabGroup`) and its
# context catch click's errors and print them itself (`_click_error`). Prose
# wraps to the width its context holds (`_width`).
from typer._click import Context
from typer._click.exceptions import ClickException, NoArgsIsHelpError, UsageError
from typer._click.globals import get_current_context
from typer.core import TyperGroup

from wowlab_core import (
    __version__,
    guard,
    install,
    keymap,
    labaddon,
    layout,
    longpath,
    luadata,
    process,
    profiles,
    snapshot,
    svmerge,
    wtfconfig,
)
from wowlab_core.errors import WowlabError

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_REFUSED = 3


def _closed_pipe(exc: BaseException, *, windows: bool | None = None) -> bool:
    """Whether `exc`, raised by a write to stdout, means the reader went away.

    Everywhere that is `BrokenPipeError` (EPIPE). On Windows a write to a
    pipe whose reader closed can also fail with EINVAL instead: the C
    runtime maps the system's "the pipe is being closed" error to it, so
    there EINVAL from stdout counts too. `windows` is for tests; None reads
    the platform."""
    if isinstance(exc, BrokenPipeError):
        return True
    if windows is None:
        windows = sys.platform == "win32"
    return windows and isinstance(exc, OSError) and exc.errno == errno.EINVAL


class _PipeState:
    """Whether the reader of stdout has gone, shared by the text stream and
    its `buffer`."""

    def __init__(self) -> None:
        self.gone = False


class _PipeSafeStream:
    """stdout for the `wowlab` command (`_WowlabApp`): when the reader of a
    pipe goes away (`wowlab tree | head -1`), the rest of the output is
    dropped and the command goes on to the end, so a restore or any other
    write never stops half way because nobody reads its report. The command
    then exits as it would have: 0 when its work completed, with no errno
    line on stderr.

    On the first failed write the stream's file descriptor is pointed at the
    null device, so the bytes still in Python's own buffer, which it flushes
    as the process ends, go there instead of failing again. Every other
    attribute is the wrapped stream's, and `buffer` is guarded the same way
    for a write of bytes."""

    def __init__(self, stream: Any, state: _PipeState | None = None) -> None:
        self._stream = stream
        self._state = state if state is not None else _PipeState()
        self._buffer: _PipeSafeStream | None = None

    @property
    def buffer(self) -> Any:
        if self._buffer is None:
            self._buffer = _PipeSafeStream(self._stream.buffer, self._state)
        return self._buffer

    def write(self, data: Any) -> int:
        if self._state.gone:
            return len(data)
        try:
            written: int = self._stream.write(data)
        except OSError as exc:
            if not _closed_pipe(exc):
                raise
            self._reader_gone()
            return len(data)
        return written

    def writelines(self, lines: Any) -> None:
        for line in lines:
            self.write(line)

    def flush(self) -> None:
        if self._state.gone:
            return
        try:
            self._stream.flush()
        except OSError as exc:
            if not _closed_pipe(exc):
                raise
            self._reader_gone()

    def _reader_gone(self) -> None:
        self._state.gone = True
        try:
            fd = self._stream.fileno()
            devnull = os.open(os.devnull, os.O_WRONLY)
            try:
                os.dup2(devnull, fd)
            finally:
                os.close(devnull)
        except (OSError, ValueError, AttributeError):
            # Not a real file descriptor (a test's stream): nothing to point.
            pass

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)


def _ignore_sigpipe() -> None:
    """SIGPIPE ignored, so a write to a closed pipe raises an error that
    `_PipeSafeStream` handles instead of killing the process mid-write.
    Python ignores it at startup already; this keeps it so when a parent
    process or an embedding changed that. Windows has no SIGPIPE, and a
    thread other than the main one cannot set a handler: both are left
    as they are."""
    sigpipe = getattr(signal, "SIGPIPE", None)
    if sigpipe is None:
        return
    # Not `contextlib.suppress`: one call fewer where Ctrl-C can land.
    try:  # noqa: SIM105
        signal.signal(sigpipe, signal.SIG_IGN)
    except ValueError:
        pass


class _WowlabApp(typer.Typer):
    """The `wowlab` app: run as the console script (`wowlab_core.cli:app`),
    an exception nothing caught is printed by `_excepthook`, escaped.

    Typer's `__call__` puts its own hook in `sys.excepthook` on every call,
    and with pretty exceptions off that hook hands the exception to the one
    Python had when typer was imported, which prints it raw. So as an
    exception leaves the app, `_excepthook` is put there instead, just
    before Python's top level calls it. On a normal exit (`SystemExit` with
    an int or no code) the hook is put back as it was before the call. A
    `SystemExit` with any other code, which Python would print raw itself
    without calling a hook, is printed here on one escaped line and becomes
    exit 1, as Python would make it; Ctrl-C while that line is escaped is
    swallowed, and `_excepthook` is in place first in case it is not. The
    test runner calls the root command's `main`, not this, and so gets the
    exception itself.

    A closed pipe on stdout is handled here too, once for every command:
    stdout is a `_PipeSafeStream` while the app runs and SIGPIPE stays
    ignored (`_ignore_sigpipe`)."""

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        hook = sys.excepthook
        _ignore_sigpipe()
        stdout = sys.stdout
        guarded = stdout is not None and not isinstance(stdout, _PipeSafeStream)
        if guarded:
            sys.stdout = _PipeSafeStream(stdout)
        try:
            return super().__call__(*args, **kwargs)
        except SystemExit as exc:
            if exc.code is None or isinstance(exc.code, int):
                raise
            hook = _excepthook
            # Not `contextlib.suppress`: the handler body makes no call.
            # Python checks for a pending signal at a call, so a second
            # Ctrl-C while the guard itself runs would escape.
            try:  # noqa: SIM105
                _say_err(_text(exc.code, "exit code"))
            except BaseException:
                pass
            raise SystemExit(EXIT_ERROR) from None
        except BaseException:
            hook = _excepthook
            raise
        finally:
            sys.excepthook = hook
            if guarded:
                current = sys.stdout
                # Output still buffered goes out (or, with the reader gone,
                # nowhere) before stdout is put back as it was.
                try:  # noqa: SIM105
                    current.flush()
                except (OSError, ValueError):
                    pass
                if isinstance(current, _PipeSafeStream):
                    sys.stdout = stdout


class _WowlabContext(Context):
    """The root command's context: a click error raised as it closes (by a
    resource `main()` registered with `ctx.with_resource`) is printed by
    `_click_error` too. The root context closes in typer's main loop, outside
    `_WowlabGroup.invoke`; every other context closes inside it."""

    @override
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        tb: TracebackType | None,
    ) -> bool | None:
        try:
            return super().__exit__(exc_type, exc_value, tb)
        except ClickException as exc:
            _click_error(exc)


# The one footer of every command that takes --json (M16-15).
JSON_FOOTER = "With --json the output is JSON; the wiki's CLI Reference page names its format."


# Where the root context keeps the arguments it was given (`ctx.meta`).
_ARGS_KEY = "wowlab.args"


class _WowlabGroup(TyperGroup):
    """The root command: a click error (an unknown option, an extra argument,
    a bad value), whose message can hold the user's own arguments, is
    printed by `_click_error` instead of click. Subcommands are parsed and
    run inside the root's `invoke`, so this covers them too.

    Every command that takes `--json` ends its help with one shared footer,
    `JSON_FOOTER`, rather than naming its model there: the model is named
    after a form feed in the command's docstring, which `--help` does not
    print and the generated command reference reads (M16-15)."""

    context_class = _WowlabContext

    def __init__(self, **attrs: Any) -> None:
        super().__init__(**attrs)
        pending: list[Any] = list(self.commands.values())
        while pending:
            cmd = pending.pop()
            pending.extend(getattr(cmd, "commands", {}).values())
            if any("--json" in getattr(p, "opts", ()) for p in cmd.params):
                cmd.epilog = JSON_FOOTER

    def make_context(
        self,
        info_name: str | None,
        args: list[str],
        parent: Context | None = None,
        **extra: Any,
    ) -> Context:
        given = list(args)  # parsing consumes `args`
        try:
            ctx = super().make_context(info_name, args, parent, **extra)
        except ClickException as exc:
            _click_error(exc)
        # The arguments as typed, for an error that offers the same command
        # with --flavor added (M16-114); `meta` is shared with every subcommand.
        ctx.meta[_ARGS_KEY] = given
        return ctx

    def invoke(self, ctx: Context) -> Any:
        try:
            return super().invoke(ctx)
        except ClickException as exc:
            _click_error(exc)


app = _WowlabApp(
    name="wowlab",
    context_settings={"help_option_names": ["-h", "--help"]},
    help="Local-only toolchain over a World of Warcraft install.",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode=None,
    cls=_WowlabGroup,
    pretty_exceptions_enable=False,
)
install_app = typer.Typer(help="The install and its flavors.", no_args_is_help=True)
sv_app = typer.Typer(
    help="SavedVariables files: list and dump read them; merge writes one through the write gate.",
    no_args_is_help=True,
)
cvar_app = typer.Typer(help="CVars in Config.wtf and config-cache.wtf.", no_args_is_help=True)
binds_app = typer.Typer(
    help="Key bindings in bindings-cache.wtf, and a coach over the combat log and the "
    "lab-addon's record of the action bars.",
    no_args_is_help=True,
)
macros_app = typer.Typer(help="Macros in macros-cache.txt.", no_args_is_help=True)
addons_app = typer.Typer(help="Folders under Interface/AddOns/.", no_args_is_help=True)
addon_app = typer.Typer(
    help="Install or remove the lab-addon (Interface/AddOns/WowLab/) through the write gate. "
    "Its SavedVariables (WowLab.lua) are never touched.",
    no_args_is_help=True,
)
db2_app = typer.Typer(
    help="Game data tables from wago.tools, cached by build.", no_args_is_help=True
)
names_app = typer.Typer(
    help="The names of races, classes, specs and professions: one download per game version.",
    no_args_is_help=True,
)
snap_app = typer.Typer(help="The snapshot store.", no_args_is_help=True)
log_app = typer.Typer(help="The combat log (read only).", no_args_is_help=True)
looks_app = typer.Typer(
    help="Character customization looks: races, options and choices from a build's tables, "
    "and looks saved as JSON under the user data directory. Data only: nothing here writes "
    "into an install or reaches the game.",
    no_args_is_help=True,
)
profile_app = typer.Typer(
    help="Named sets of the client's local UI files, saved into the snapshot store and "
    "applied through the write gate (`wowlab undo` reverses an apply). A profile covers "
    "every account and every character folder that existed when it was saved, not only "
    "the character you play; applying it deletes files created in those places since. "
    "The lab-addon is never in a profile. " + profiles.SERVER_SIDE_NOTE,
    no_args_is_help=True,
)
filemap_app = typer.Typer(
    help="The file map: what every path in an install is.", no_args_is_help=True
)
app.add_typer(install_app, name="install", short_help="The install and its flavors.")
app.add_typer(
    filemap_app, name="filemap", short_help="The file map: what each path in an install is."
)
app.add_typer(sv_app, name="sv", short_help="SavedVariables files: list, dump and merge.")
app.add_typer(cvar_app, name="cvar", short_help="CVars in Config.wtf and config-cache.wtf.")
app.add_typer(
    binds_app, name="binds", short_help="Key bindings, and a coach that reads your combat log."
)
app.add_typer(
    macros_app,
    name="macros",
    short_help="Macros in macros-cache.txt, and a check for common mistakes.",
)
app.add_typer(addons_app, name="addons", short_help="Folders under Interface/AddOns/.")
app.add_typer(addon_app, name="addon", short_help="Install or remove the lab-addon.")
app.add_typer(db2_app, name="db2", short_help="Game data tables from wago.tools, cached by build.")
app.add_typer(
    names_app,
    name="names",
    short_help="Download the names of races, classes, specs and professions.",
)
app.add_typer(
    snap_app, name="snap", short_help="Snapshots of your settings, addons and saved data."
)
app.add_typer(log_app, name="log", short_help="The combat log (read only).")
app.add_typer(
    profile_app, name="profile", short_help="Save and apply named sets of UI settings files."
)
app.add_typer(
    looks_app, name="looks", short_help="Character customization: races, options and saved looks."
)
char_app = typer.Typer(
    help="Characters as the lab-addon recorded them in WowLab.lua (read only).",
    no_args_is_help=True,
)
app.add_typer(
    char_app, name="char", short_help="Characters as the lab-addon recorded them (read only)."
)


# ─── options ─────────────────────────────────────────────────────────────────

RootOpt = Annotated[
    Path | None,
    typer.Option(
        "--root",
        help="The install folder, the one that holds .build.info. Default: "
        "$WOWLAB_WOW_ROOT, then the usual install locations.",
    ),
]
FlavorOpt = Annotated[
    str | None,
    typer.Option(
        "--flavor",
        help="The flavor folder to use, as `wowlab install show` lists it. "
        "Default: $WOWLAB_FLAVOR; implied when the install has only one.",
    ),
]
JsonOpt = Annotated[bool, typer.Option("--json", help="Print JSON instead of text.")]
NotesOpt = Annotated[
    bool,
    typer.Option(
        "--notes",
        help="Also print the standing notes: what the figures mean and what wowlab cannot "
        "tell. Without it, one line after the answer counts them.",
    ),
]
YesOpt = Annotated[bool, typer.Option("--yes", "-y", help="Do not ask before changing files.")]
AccountOpt = Annotated[
    str | None,
    typer.Option("--account", help="Account folder under WTF/Account/. Implied when there is one."),
]
CharacterOpt = Annotated[
    str | None,
    typer.Option(
        "--character",
        help="Character folder, as <Character> or <Realm folder>/<Character> "
        "when the name alone is ambiguous.",
    ),
]


# ─── output ──────────────────────────────────────────────────────────────────


class CliError(WowlabError):
    """An error the command reports as one line, with its exit code."""

    def __init__(self, message: str, code: int = EXIT_ERROR) -> None:
        super().__init__(message)
        self.exit_code = code

    @property
    def code(self) -> int:
        """The exit code (`exit_code`), by the name this module first gave it."""
        return self.exit_code


# What `_safe` escapes: every C0 control (tab and line feed included), DEL,
# every C1 control, and the Unicode format and separator characters (Cf, Zl,
# Zp; U+202E reverses the text after it, U+2028 breaks a line) in the one
# table printed key paths use (`svmerge._FORMAT_CHARACTERS`, Unicode 15.0,
# M11-25). A newer Unicode database is caught by the exhaustive test in
# `tests/test_cli_terminal_safe.py`, not here.
_UNSAFE = re.compile(
    "[\x00-\x1f\x7f-\x9f"
    + "".join(f"{chr(lo)}-{chr(hi)}" for lo, hi in svmerge._FORMAT_CHARACTERS)
    + "]"
)


def _escape(match: re.Match[str]) -> str:
    """A C0 control or DEL as `\\xNN` of its one byte; a C1 control, format
    or separator character as `\\xNN` of each UTF-8 byte that carries it
    (U+0085 is `\\xc2\\x85`), so every `\\xNN` stands for one byte of the
    name as stored, as M11-25's printed key paths do."""
    char = match.group()
    if char < "\x80":
        return f"\\x{ord(char):02x}"
    return "".join(f"\\x{b:02x}" for b in char.encode())


def _safe(text: str) -> str:
    """Printable text for a terminal, on one line.

    Bytes that are not UTF-8 (carried as lone surrogates) are shown as `\\xNN`
    escapes instead of failing the write, and so is every C0 and C1 control
    character, tab and line feed included: a file name or value from the
    install can carry ESC or OSC sequences, which must never reach the
    terminal as control codes. Unicode format and separator characters
    (U+202E, U+2028, U+200B, ...) are shown as their UTF-8 bytes, `\\xNN`
    each, so a name cannot reorder or break the line it is printed on.
    Letters, accented or not, print as they are. A backslash is not
    escaped (it would mangle every Windows path), so a name holding the
    literal text `\\x85` prints like an invalid byte 0x85."""
    try:
        text = text.encode("utf-8", "surrogateescape").decode("utf-8", "backslashreplace")
    except UnicodeEncodeError:
        text = text.encode("utf-8", "backslashreplace").decode("utf-8")
    return _UNSAFE.sub(_escape, text)


# Player copy (M16-15, docs/LAB_PLAN.md §18.1 item 4): text output carries
# no spec vocabulary. The library's notes keep it, because they are also
# `--json` fields and `--json` does not change, so it is rendered here by
# `_player_copy`: the first `[verify]` of one run reads "(not yet
# confirmed)"; a later one is left out where it tags a phrase and reads "not
# yet confirmed" where the sentence needs it ("whether ... is [verify]");
# and a parenthesised pointer into the spec (a ticket id, `LAB_PLAN L7`, a
# doc path, `GLOSSARY`) is left out. Only wowlab's own words go through it:
# a note, a file-map row, the text `_note` is given. A value read from the
# install (a macro's name or body, a binding's command, a CVar value, a
# path) never does, so it prints as the install holds it; a line that
# mixes the two renders the note before the value is put in (`_player_copy`).
NOT_CONFIRMED = "(not yet confirmed)"
_VERIFY = re.compile(
    r"(?P<lead>[ \t]?)(?:(?P<alone>\(\*{0,2}\[verify\]\*{0,2}\))|\*{0,2}\[verify\]\*{0,2})",
    re.IGNORECASE,
)
# A later marker the sentence needs: after a verb or a joining word, or
# before a preposition ("[verify] on Forever").
_NEEDED_AFTER = re.compile(r"(?:\b(?:is|are|was|were|be|been|and|or|but)|,)\s*$", re.IGNORECASE)
_NEEDED_BEFORE = re.compile(r"\s*(?:on|for|past|against|in|at)\b", re.IGNORECASE)
_SPEC_POINTER = re.compile(
    r"[ \t]?\((?:(?:M1\d-\d+|ADR-\d+)(?:,[^()]*)?|LAB_PLAN L[1-8]|L[1-8]|GLOSSARY|docs/[^()]*"
    r"|§[^()]*)\)"
)


class _Report:
    """One run: whether "(not yet confirmed)" was printed yet, and whether
    it is a `--json` run, whose stderr is left as the library wrote it;
    and whether it has printed JSON on stdout yet (`printed`), after which
    an error prints no `ErrorReport` there (one JSON document per run);
    and whether its stdout is one JSON value per line (`lines`, set by
    `log tail --follow --json`), where an `ErrorReport` is one line too."""

    confirmed_shown = False
    json = False
    printed = False
    lines = False


def _new_report(json_out: bool = False) -> None:
    """A new run: its first `[verify]` will read "(not yet confirmed)"."""
    _Report.confirmed_shown = False
    _Report.json = json_out
    _Report.printed = False
    _Report.lines = False


def _later_marker(match: re.Match[str]) -> str:
    """A `[verify]` after the run's first: left out, unless the sentence
    needs it, where it reads "not yet confirmed" without the brackets."""
    if match.group("alone") is None and (
        _NEEDED_AFTER.search(match.string, 0, match.start())
        or _NEEDED_BEFORE.match(match.string, match.end())
    ):
        return match.group("lead") + NOT_CONFIRMED.strip("()")
    return ""


# A value a library message quotes (`{name!r}`): a macro's spell name, a
# table's option name. `_player_copy` leaves it as it is.
_QUOTED = re.compile(r"""((?<![A-Za-z])'(?:[^'\\]|\\.)*'(?![A-Za-z])|"(?:[^"\\]|\\.)*")""")


def _player_copy(text: str, keep: Sequence[str | None] = ()) -> str:
    """wowlab's own `text` with the spec's markers in player words (see the
    comment above `NOT_CONFIRMED`); as it is in a `--json` run. Never given
    a line of install data. A value a message quotes, and each value in
    `keep` (the install's words a library line carries, such as a spell's
    name in what a key does), is left as it is."""
    if _Report.json:
        return text
    values = sorted({v for v in keep if v}, key=len, reverse=True)
    if values:
        held = re.compile("(" + "|".join(re.escape(v) for v in values) + ")")
        parts = held.split(text)
        return "".join(p if i % 2 else _player_copy(p) for i, p in enumerate(parts))
    if "'" in text or '"' in text:
        parts = _QUOTED.split(text)
        return "".join(p if i % 2 else _render(p) for i, p in enumerate(parts))
    return _render(text)


def _held_words(holds: keymap.Holds | None) -> tuple[str | None, ...]:
    """The record's values in a `does`: what `_player_copy` leaves as read."""
    if holds is None:
        return ()
    return (holds.name, holds.subtext, holds.sub_type, holds.type)


def _render(text: str) -> str:
    """`_player_copy` of text that quotes nothing."""
    if "[" in text:

        def verify(match: re.Match[str]) -> str:
            if _Report.confirmed_shown:
                return _later_marker(match)
            _Report.confirmed_shown = True
            return match.group("lead") + NOT_CONFIRMED

        text = _VERIFY.sub(verify, text)
    if "(" in text:
        text = _SPEC_POINTER.sub("", text)
    return text


# The file map's "read by" names wowlab's modules; text output names what
# they are (M16-15). `--json` keeps the module names.
_READER_WORDS = {
    "casc": "the game-data archive reader",
    "combatlog": "the combat-log reader",
    "install": "install discovery",
    "layout": "the install walk",
    "luadata": "the SavedVariables reader",
    "process": "the client check",
    "snapshot": "snapshots",
    "toc": "the TOC reader",
    "wtfconfig": "the WTF settings reader",
}


def _reader_words(module: str) -> str:
    """A file-map row's "read by" in player words: each module named in
    backticks becomes what it is, and the project's wave is left out."""
    words = re.sub(r"`([a-z_]+)`", lambda m: _READER_WORDS.get(m.group(1), m.group(0)), module)
    return re.sub(r" in Wave \d+", "", words)


def _say(text: str = "") -> None:
    """One line on stdout, escaped by `_safe`. Notes in it were rendered by
    `_player_copy`; a value read from the install prints as it is."""
    typer.echo(_safe(text))


def _say_note(text: str = "") -> None:
    """One line of wowlab's own words on stdout (a note, a file-map row),
    in player words (`_player_copy`) and escaped by `_safe`."""
    typer.echo(_safe(_player_copy(text)))


def _width() -> int:
    """The width prose wraps to: the context's terminal width (a test sets
    it, so the output is the same on every machine), else the terminal's,
    as click's help formatter takes it; never under 40 columns."""
    ctx = get_current_context(silent=True)
    width = ctx.terminal_width if ctx is not None else None
    if not width:
        width = shutil.get_terminal_size().columns
    return max(width, 40)


# Stands for a space inside a value `_say_prose` keeps whole. `_safe` escapes
# every C0 control, so the text it returns never holds one of its own.
_HELD_SPACE = "\x00"


def _say_prose(
    text: str, indent: str = "", hang: str | None = None, keep: Sequence[str | None] = ()
) -> None:
    """Prose on stdout, escaped by `_safe` and wrapped to the terminal width
    (`_width`). The first line starts with `indent`, the others with `hang`
    (`indent` and two spaces when None). Each value in `keep` is one
    unbreakable token, spaces and all: every caller passes the paths and
    folder names its text holds (a `~/...` path, a record's path, a realm
    folder), and a word is never split, so no path is broken across lines;
    a line holding a long one runs past the width instead."""
    rest = indent + "  " if hang is None else hang
    safe = _safe(text)
    for value in sorted({_safe(v) for v in keep if v and " " in v}, key=len, reverse=True):
        safe = safe.replace(value, value.replace(" ", _HELD_SPACE))
    lines = textwrap.wrap(
        safe,
        width=_width(),
        initial_indent=indent,
        subsequent_indent=rest,
        break_long_words=False,
        break_on_hyphens=False,
    )
    for line in lines or [indent.rstrip()]:
        typer.echo(line.replace(_HELD_SPACE, " "))


def _say_notes(
    notes: Sequence[str],
    show: bool,
    caveat: str | None = None,
    keep: Sequence[str | None] = (),
) -> None:
    """The end of a text answer: at most one caveat that applies to this
    result, then, with --notes, the standing notes, each in player words
    (`_player_copy`), or else one footer line that counts them. A note that
    is the caveat is not said twice. `keep`: the paths and names in them,
    which `_say_prose` keeps whole."""
    if caveat is not None:
        _say_prose(_player_copy(caveat), keep=keep)
    rest = [n for n in notes if n != caveat]
    if not rest:
        return
    if show:
        _say("Notes:")
        for note in rest:
            _say_prose(_player_copy(note), indent="  - ", hang="    ", keep=keep)
        return
    them = "it" if len(rest) == 1 else "them"
    _say(f"{_plural(len(rest), 'note')}: --notes shows {them}")


def _account_words(acct: layout.Account) -> list[str]:
    """The account's folder names and character paths, as `_say_prose` keeps
    them whole: a realm folder may hold spaces."""
    words = [acct.folder]
    for realm in acct.realms:
        words.append(realm.folder)
        words += [f"{realm.folder}/{c.folder}" for c in realm.characters]
    return words


_SAVED_AT = "the file's modification time: the character as of that save"


def _home_words(path: str) -> str:
    """A path in the player's home folder as `~` and the rest, so that text
    output does not print the home folder's absolute path."""
    try:
        rest = Path(path).relative_to(Path.home())
    except ValueError:
        return path
    return str(Path("~") / rest)


def _say_err(text: str = "") -> None:
    """One line on stderr, escaped by `_safe`: `_say` for output that must
    not mix with JSON on stdout."""
    typer.echo(_safe(text), err=True)


_FIELD = "{}"


def _note(message: str, *values: object) -> None:
    """A message on stderr.

    `message` is this module's own text, in player words (`_player_copy`):
    its line breaks are kept, and every
    other control, format or separator character is escaped. Anything from
    elsewhere (a path, a folder name, an error text) is never put into
    `message` by the caller: it is passed in `values`, and each value takes
    the place of the next `{}` in `message`, escaped by `_safe` with its
    line breaks, so a value can never start a line of its own. Nothing else
    in `message` or a value is a field (no `str.format`): a value holding
    `{}` or `{:>9}` prints as it is. A `message` whose `{}` count differs
    from the number of values is a bug in this module (ValueError)."""
    parts = message.split(_FIELD)
    if len(parts) != len(values) + 1:
        raise ValueError(
            f"_note: {len(parts) - 1} {_FIELD} field(s) in the message, {len(values)} value(s)"
        )
    out = ["\n".join(_safe(line) for line in _player_copy(parts[0]).split("\n"))]
    for value, part in zip(values, parts[1:], strict=True):
        out.append(_safe(str(value)))
        out.append("\n".join(_safe(line) for line in _player_copy(part).split("\n")))
    typer.echo("".join(out), err=True)


def _fail(message: str, code: int, exc: BaseException | None = None) -> NoReturn:
    """The error `message` as one line on stderr: it holds values (paths,
    folder names) the library or this module put in it, so every line
    break in it is escaped. The notes `exc` carries (`add_note`, such as
    the write gate's "the rollback did not finish") follow, one line each."""
    _note("wowlab: {}", message)
    for extra in getattr(exc, "__notes__", ()):
        _note("{}", extra)
    raise typer.Exit(code)


def _click_error(exc: ClickException) -> NoReturn:
    """A click error as click's `show()` prints it, line by line through
    `_say_err`, then its exit code (2 for a usage error).

    `Error: ...` can hold the user's own arguments (an unknown option, an
    extra argument), so it is one line whatever they hold. The usage and
    help-hint lines above it (`UsageError.show`) are click's text, and so
    is the help that `no_args_is_help` raises instead of an error, which
    keeps its line breaks."""
    if isinstance(exc, NoArgsIsHelpError):
        lines = exc.format_message().split("\n")
    else:
        lines = []
        ctx = exc.ctx if isinstance(exc, UsageError) else None
        if ctx is not None:
            lines.extend(ctx.get_usage().split("\n"))
            if ctx.command.get_help_option(ctx) is not None:
                names = ctx.help_option_names
                hint = "--help" if "--help" in names else names[0]
                lines.append(f"Try '{ctx.command_path} {hint}' for help.")
            lines.append("")
        lines.append(f"Error: {exc.format_message()}")
    for line in lines:
        _say_err(line)
    raise typer.Exit(exc.exit_code)


_CAUSE = "The above exception was the direct cause of the following exception:"
_CONTEXT = "During handling of the above exception, another exception occurred:"


def _text(value: object, what: str, fn: Callable[[object], str] = str) -> str:
    """`fn(value)`, or what Python's traceback prints when that fails."""
    try:
        return fn(value)
    except Exception:
        return f"<{what} {fn.__name__}() failed>"


# Python's own limits for an exception group (`traceback.TracebackException`):
# at most this many sub-exceptions of one group, then "and N more
# exceptions"; a group nested this deep is one line.
_GROUP_WIDTH = 15
_GROUP_DEPTH = 10
# In place of a group member already printed (a shared member, or one that
# is also in a chain), so its section is never empty.
_PRINTED_ELSEWHERE = "[printed elsewhere in this traceback]"


def _exception_name(kind: type[BaseException]) -> str:
    """The type as Python's traceback names it: qualified by its module,
    unless that is `builtins` or `__main__`."""
    module = kind.__module__
    if module in ("builtins", "__main__"):
        return kind.__qualname__
    return f"{module if isinstance(module, str) else '<unknown>'}.{kind.__qualname__}"


def _exception_lines(exc: BaseException, seen: set[int], level: int) -> list[str]:
    """One exception as Python prints it, without its chain: its frames, its
    type and message, its notes, and its sub-exceptions if it is a group
    (`level` is how deep in groups it sits)."""
    if isinstance(exc, BaseExceptionGroup) and level >= _GROUP_DEPTH:
        return [f"... (max_group_depth is {_GROUP_DEPTH})"]
    lines: list[str] = []
    frames = traceback.extract_tb(exc.__traceback__)
    if frames:
        lines.append("Traceback (most recent call last):")
        for frame in frames.format():
            lines.extend(frame.removesuffix("\n").split("\n"))
    name = _exception_name(type(exc))
    message = _text(exc, "exception")
    lines.append(f"{name}: {message}" if message else name)
    try:
        notes = getattr(exc, "__notes__", None)
    except Exception as error:
        notes = [f"Ignored error getting __notes__: {_text(error, '__notes__', repr)}"]
    if isinstance(notes, Sequence) and not isinstance(notes, str | bytes):
        lines.extend(_text(note, "note") for note in notes)
    elif notes is not None:
        lines.append(_text(notes, "__notes__", repr))
    if isinstance(exc, BaseExceptionGroup):
        count = len(exc.exceptions)
        for n, sub in enumerate(exc.exceptions[:_GROUP_WIDTH], 1):
            lines.append(f"sub-exception {n} of {count}:")
            if id(sub) in seen:
                lines.append(f"  {_PRINTED_ELSEWHERE}")
            else:
                lines.extend(f"  {line}" for line in _traceback_lines(sub, seen, level + 1))
        if count > _GROUP_WIDTH:
            more = count - _GROUP_WIDTH
            lines.append(f"and {more} more exception{'s' if more > 1 else ''}")
    return lines


def _traceback_lines(exc: BaseException, seen: set[int] | None = None, level: int = 0) -> list[str]:
    """`exc`'s traceback in Python's layout, one string per line: its causes
    and contexts first, oldest first, each followed by the line that links
    it to the next.

    Where it differs from Python's own: an exception's message and each of
    its notes are one line each, whatever they hold, so once `_say_err`
    escapes the line breaks in them, text from the install can never start
    a line of its own. A SyntaxError shows its `str()` (message, file,
    line) instead of the file, source and caret lines. There is no "Did you
    mean" for a NameError, AttributeError or ImportError. An exception
    group's sub-exceptions are listed as "sub-exception n of m:" with their
    lines indented, not in Python's boxes, with Python's limits (15 per
    group, then "and N more exceptions"; a group 10 deep is one line). An
    exception is printed once: a cycle through `__cause__` or `__context__`
    stops where it meets one already printed, as Python's does, and a group
    member printed elsewhere in the traceback is one line (Python prints it
    again). Frames (file, line, function, source) are code and keep
    Python's layout."""
    seen = set() if seen is None else seen
    chain: list[tuple[BaseException, str]] = []
    current: BaseException | None = exc
    link = ""
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append((current, link))
        if current.__cause__ is not None:
            current, link = current.__cause__, _CAUSE
        elif current.__context__ is not None and not current.__suppress_context__:
            current, link = current.__context__, _CONTEXT
        else:
            current = None
    lines: list[str] = []
    for current, link in reversed(chain):
        lines.extend(_exception_lines(current, seen, level))
        if link:
            lines.extend(["", link, ""])
    return lines


def _unprintable(exc: BaseException) -> str:
    return f"wowlab: {type(exc).__name__}: its traceback could not be printed"


def _traceback_text(exc: BaseException) -> list[str]:
    """`_traceback_lines(exc)`, the one rendering `_excepthook` and the log
    formatter share, or one line naming the type if an error stops it.

    It raises nothing but a BaseException that is not an Exception
    (KeyboardInterrupt): in a command that stops the command, and
    `_excepthook` catches it too. The fallback line is made before the
    `try`, so the handler body makes no call."""
    fallback = [_unprintable(exc)]
    try:
        return _traceback_lines(exc)
    except Exception:
        return fallback


def _excepthook(kind: type[BaseException], exc: BaseException, tb: TracebackType | None) -> None:
    """`sys.excepthook` for the `wowlab` command (`_WowlabApp`): an exception
    nothing caught, from a command body, the root callback or an option
    callback, is printed in Python's layout, line by line through
    `_say_err`, the message and each note on one line (`_traceback_lines`).
    Python then exits non-zero as it always does (1, or by the signal for
    KeyboardInterrupt). `tb` is `exc.__traceback__`, which the rendering
    reads.

    The hook raises nothing, not even KeyboardInterrupt (Ctrl-C while a long
    message is escaped): a hook that raised would make Python print the
    original exception itself, raw. What stops it prints one line naming
    the type instead, if that can still be printed. The outer handler's
    body makes no call: Python checks for a pending signal at a call, so a
    second Ctrl-C right after the first could otherwise escape from it."""
    try:
        try:
            for line in _traceback_text(exc):
                _say_err(line)
        except BaseException:
            _say_err(_unprintable(exc))
    except BaseException:
        pass


class _SafeLogFormatter(logging.Formatter):
    """A library log record's message as one line, escaped by `_safe`. A
    traceback it carries (`_log.exception`) or a stack (`stack_info`)
    follows, line by line, rendered as `_excepthook` renders one."""

    @override
    def format(self, record: logging.LogRecord) -> str:
        record.message = record.getMessage()
        if self.usesTime():
            record.asctime = self.formatTime(record, self.datefmt)
        lines = [self.formatMessage(record)]
        if record.exc_info is not None and record.exc_info[1] is not None:
            lines.extend(_traceback_text(record.exc_info[1]))
        if record.stack_info:
            lines.extend(record.stack_info.split("\n"))
        return "\n".join(_safe(line) for line in lines)


class _StderrLogHandler(logging.Handler):
    """Library log records (the write gate's warnings) on stderr, looked up
    at each record so it follows the stream the command is writing to."""

    @override
    def emit(self, record: logging.LogRecord) -> None:
        try:
            typer.echo(self.format(record), err=True)
        except Exception:
            self.handleError(record)

    @override
    def handleError(self, record: logging.LogRecord) -> None:
        """One escaped line naming the record, instead of logging's report,
        which prints the record's message, arguments and exception raw. A
        KeyboardInterrupt goes on and stops the command, which prints
        nothing of the record; the handler body makes no call."""
        line = _safe(
            f"wowlab: a log record could not be printed ({record.levelname}, {record.name})"
        )
        try:  # noqa: SIM105 (not contextlib.suppress: no call in the handler)
            typer.echo(line, err=True)
        except Exception:
            pass


_LIBRARY_LOGGER = "wowlab_core"
_LOG_HANDLER = _StderrLogHandler(logging.WARNING)
_LOG_HANDLER.setFormatter(_SafeLogFormatter("wowlab: %(message)s"))


@contextmanager
def _library_log_on_stderr() -> Iterator[None]:
    """While a command runs, `wowlab_core` log records go to stderr through
    `_LOG_HANDLER` and nowhere else (Python's fallback handler would print
    them raw); the logger is put back as it was afterwards."""
    logger = logging.getLogger(_LIBRARY_LOGGER)
    if _LOG_HANDLER in logger.handlers:  # nested: already installed
        yield
        return
    propagate = logger.propagate
    logger.addHandler(_LOG_HANDLER)
    logger.propagate = False
    try:
        yield
    finally:
        logger.removeHandler(_LOG_HANDLER)
        logger.propagate = propagate


_SURROGATE = re.compile("[\ud800-\udfff]")


def _json_text(value: str) -> str:
    """A lone surrogate as NUL and four hex digits, as `layout` and `snapshot` do."""
    return _SURROGATE.sub(lambda m: f"\x00{ord(m.group()):04X}", value)


def _map_strings(value: Any, fn: Callable[[str], str]) -> Any:
    if isinstance(value, str):
        return fn(value)
    if isinstance(value, list | tuple):
        return [_map_strings(v, fn) for v in value]
    if isinstance(value, dict):
        return {fn(k) if isinstance(k, str) else k: _map_strings(v, fn) for k, v in value.items()}
    return value


def _dumps(payload: Any) -> str:
    return json.dumps(_map_strings(payload, _json_text), indent=2, ensure_ascii=True)


def _emit(model: BaseModel) -> None:
    """The model as JSON on stdout; `type(model).model_validate_json` reads it back."""
    _Report.printed = True
    typer.echo(_dumps(model.model_dump(mode="json")))


def _emit_line(model: BaseModel) -> None:
    """The model as one line of JSON on stdout, for a line-delimited run."""
    _Report.printed = True
    typer.echo(
        json.dumps(_map_strings(model.model_dump(mode="json"), _json_text), ensure_ascii=True)
    )


def _emit_manifest(manifest: snapshot.Manifest) -> None:
    """A manifest in its one canonical encoding (`snapshot.manifest_bytes`)."""
    _Report.printed = True
    typer.echo(snapshot.manifest_bytes(manifest).decode("ascii"), nl=False)


def _bytes(n: int) -> str:
    return f"{n:,} byte" + ("" if n == 1 else "s")


CONFIRM_DECLINED = "Nothing was changed."
"""The last line `_confirm` prints on stderr when it gets no yes, before the
command exits 1. `wowlab ui` reads a plan run of a command with no
`--dry-run` (`undo`, `sv merge`) by it: that run is the leaf with `--json`,
no `--yes` and stdin closed, so `_confirm` gets no answer, and the text plan
is what stderr holds before this line (docs/LAB_PLAN.md §16.1, M14-03)."""

EXPECT_FIELDS: dict[tuple[str, ...], tuple[str, ...]] = {
    ("undo",): ("undone",),
    ("sv", "merge"): ("target_sha256", "merged_sha256"),
}
"""For each command that confirms through `_confirm` and declares `--expect`:
the fields of its `--json` report whose values, joined by `:` and passed
back as `--expect`, make a later run refuse (exit 3) unless it would apply
the same plan. The journal record `undo` would undo; for `sv merge`, the
SHA-256 it read its target with and the SHA-256 of the bytes it would write
(§16.1, "applies the plan that was shown"; M13-74)."""


def _confirm(question: str, yes: bool) -> None:
    if yes:
        return
    try:
        agreed = typer.confirm(question, default=False, err=True)
    except typer.Abort:  # end of input: no answer is not a yes
        agreed = False
        _note("")
    if not agreed:
        _note(CONFIRM_DECLINED)
        raise typer.Exit(EXIT_ERROR)


# The parser's refusals in player words (M16-15). The parser's message names
# the rule it applied (L3, a spec section); `--json` runs keep that message
# on stderr, and `luadata` itself is unchanged.
_LUA_REFUSAL_WORDS = {
    "function definitions are rejected (L3)": "it holds a function, which saved data never "
    "does (wowlab reads the file as data and never runs it)",
    "calls are rejected (L3)": "it holds a function call, which saved data never does "
    "(wowlab reads the file as data and never runs it)",
    "bare identifiers are not values (L3)": "it holds a name where a value should be, which "
    "the game is not known to write",
    "long comments are rejected (§6.4)": "it holds a long comment (--[[ ]]), which the "
    "client does not write",
    "long-bracket strings are rejected (§6.4)": "it holds a long-bracket string ([[ ]]), "
    "which the client does not write",
    "a NUL byte is rejected (§4.3)": "it holds a NUL byte, which the client does not write",
    "nil is legal only as a top-level value or a positional entry (§4.1)": "it holds nil "
    "under a key inside a table, which the client does not write",
}
_UTF8_BOM = b"\xef\xbb\xbf"
# The parser's refusal of a file that ends inside a table (M16-131).
_LUA_UNCLOSED_TABLE = "unterminated table (end of input before its `}`)"
# The parser's refusal of a string that reaches the end of the file.
_LUA_UNCLOSED_STRING = "unterminated string"
# What the parser adds to a message whose token it keeps only the start of.
_LUA_TOKEN_KEPT = re.compile(
    r"\A(?P<message>.*) \(a (?P<length>[0-9]+)-byte token; its first [0-9]+ bytes are kept\)\Z",
    re.DOTALL,
)
_LUA_CUT_OFF = "it may have been cut off by a crash or a full disk during a save"


def _lua_refusal_words(exc: luadata.LuaDataError) -> str:
    """Why a SavedVariables file was refused, for a player: the two common
    real causes (a file cut off during a save, and a byte-order mark a text
    editor added) by name, and the parser's own rules in plain words."""
    return f"not SavedVariables data wowlab can read: {_lua_refusal_why(exc)}"


def _lua_refusal_why(exc: luadata.LuaDataError, size: int | None = None) -> str:
    """The reason `_lua_refusal_words` gives, on its own (`sv list
    --unreadable` prints it under a file's row, M16-131). `size`, the
    file's length when the caller knows it: a refused token that runs to
    the end of the file (a name, a number or an operator cut short) is then
    named as cut off too. A string that reaches the end is, without it."""
    where = f"line {exc.line}, column {exc.column}"
    kept = _LUA_TOKEN_KEPT.match(exc.message)
    message = kept.group("message") if kept else exc.message
    length = int(kept.group("length")) if kept else len(exc.token)
    at_end = size is not None and exc.offset + length >= size
    if exc.offset == 0 and exc.token and _UTF8_BOM.startswith(exc.token[:3]):
        why = (
            "this file starts with a UTF-8 byte-order mark, which the client does not "
            "write; a text editor may have added it"
        )
    elif exc.token == b"":
        why = f"the file ends early, at {where}: {_LUA_CUT_OFF}"
    elif exc.message == _LUA_UNCLOSED_TABLE:
        # The parser names the table left open, not the end of the file.
        why = f"the file ends before the table that opens at {where} is closed: {_LUA_CUT_OFF}"
    elif message == _LUA_UNCLOSED_STRING:
        # The parser names where the string opens; it runs to the end.
        why = f"the file ends inside a text value that opens at {where}: {_LUA_CUT_OFF}"
    elif at_end:
        why = f"the file ends in the middle of a value, at {where}: {_LUA_CUT_OFF}"
    else:
        # The parser's own words, with no install value in them.
        said = _LUA_REFUSAL_WORDS.get(message) or _render(message)
        why = f"{said}, at {where}"
    return why


# What every "no install" error ends with (M16-19): how to name the install.
_NAME_THE_INSTALL = (
    f"name the install folder (the one that holds {install.BUILD_INFO}) with --root, "
    f"or set {install.ENV_ROOT} to it; wowlab doctor says how"
)


# The same, where the install must be the one wowlab finds by itself and
# `--root` is only checked against it (`sv dump --install-only`).
_NAME_THE_INSTALL_BY_ITSELF = (
    f"set {install.ENV_ROOT} to the install folder (the one that holds {install.BUILD_INFO}); "
    "with --install-only, --root does not choose the install, it is only checked against "
    "the one wowlab finds by itself"
)


def _install_words(exc: install.InstallError, how: str = _NAME_THE_INSTALL) -> str:
    """Why there is no install to read, then how to name one (`how`): every
    such error names `--root` and `WOWLAB_WOW_ROOT` (M16-19)."""
    if isinstance(exc, install.InstallNotFoundError):
        # The locations are the error's notes, one line each after this one
        # (`_fail`): a dozen paths on one line could not be read (M16-112).
        # The lines that name the install end the notes: the default advice
        # points there rather than saying it twice.
        if how == _NAME_THE_INSTALL:
            how = "name the install as shown at the end (wowlab doctor says more)"
        n = len(exc.checked)
        if n:
            # Listed in the notes: on stderr after this line, in an
            # `ErrorReport`'s `notes`.
            found = (
                f"no WoW install found in the folders wowlab searches ({n} checked, listed below)"
            )
        else:
            found = "no WoW install found: wowlab knows no default install location on this system"
        return f"{found}; {how}"
    return f"{exc}; {how}"


def _keep_notes(err: CliError, exc: BaseException, *, root: bool = True) -> CliError:
    """`err` with the notes of `exc`, the error it words: an install not
    found carries the locations checked and the lines that name the install
    as notes, and `_fail` prints a `CliError`'s notes, not its cause's.
    Without `root` (`--root` does not choose the install there), the
    naming lines offer only the variable."""
    if not root and isinstance(exc, install.InstallNotFoundError):
        lines = [
            *exc.location_lines(),
            *install.name_the_install_lines(exc.platform, root=False),
        ]
    else:
        lines = list(getattr(exc, "__notes__", ()))
    for line in lines:
        err.add_note(line)
    return err


# The command that lists what a command of each group names (M16-19): a
# file, snapshot or profile it could not find is answered with it.
_LISTERS = {
    "sv": "wowlab sv list",
    "snap": "wowlab snap list",
    "profile": "wowlab profile list",
}


def _lister() -> str | None:
    """The lister of the command running now (`_LISTERS`), if it has one."""
    ctx = get_current_context(silent=True)
    if ctx is None:
        return None
    words = ctx.command_path.split()
    return _LISTERS.get(words[1]) if len(words) > 1 else None


def _os_error_words(exc: OSError) -> str:
    """An operating-system error in words, with no `[Errno N]` in them: a
    missing file names the lister of the command (`_lister`), and any other
    error says what the system said about which path (M16-19)."""
    path = exc.filename
    if isinstance(exc, FileNotFoundError):
        where = f"no file or folder at {os.fsdecode(path)}" if path is not None else str(exc)
        lister = _lister()
        return f"{where}; {lister} lists what there is" if lister else where
    if path is None or not exc.strerror:
        return str(exc)
    why = exc.strerror[:1].lower() + exc.strerror[1:]
    if exc.filename2 is not None:
        return f"{why}: {os.fsdecode(path)} -> {os.fsdecode(exc.filename2)}"
    return f"{why}: {os.fsdecode(path)}"


# Every error `_handled` turns into words (`_error_words`), and nothing else:
# wowlab's own errors by their one base (M16-22: `CliError` and the errors of
# install, snapshot, profiles, addoninstall, labaddon, gamedata, lookstore and
# wtfconfig), the write gate's and the SavedVariables parser's errors, which
# keep their own bases, and the operating system's.
_HANDLED_ERRORS: tuple[type[Exception], ...] = (
    WowlabError,
    guard.GuardError,
    luadata.LuaDataError,
    OSError,
)


def _error_words(exc: Exception, json_out: bool) -> tuple[str, int]:
    """The one line a handled error is reported as, and its exit code: the
    single place an error becomes words (M16-19), which `--json` error
    output builds on."""
    if isinstance(exc, guard.GuardError):
        return f"refused by the write gate: {exc}", EXIT_REFUSED
    if isinstance(exc, CliError):
        return str(exc), exc.exit_code
    if isinstance(exc, luadata.LuaLimitError):
        limit = exc.message if json_out else exc.message.replace(" (MAX_COST)", "")
        return (
            f"refused: this file is beyond what the SavedVariables parser will hold "
            f"({limit}); nothing was printed"
        ), EXIT_ERROR
    if isinstance(exc, wtfconfig.WtfLimitError):
        return (
            f"refused: this file is beyond what wowlab will read of it ({exc}); nothing was printed"
        ), EXIT_ERROR
    if isinstance(exc, luadata.LuaDataError):
        if json_out:
            return f"not SavedVariables data the parser accepts: {exc}", EXIT_ERROR
        return _lua_refusal_words(exc), EXIT_ERROR
    if isinstance(exc, install.InstallError):
        return _install_words(exc), EXIT_ERROR
    if isinstance(exc, OSError):
        return _os_error_words(exc), EXIT_ERROR
    if isinstance(exc, WowlabError):
        return str(exc), exc.exit_code
    return str(exc), EXIT_ERROR


def _error_kind(exc: Exception) -> str:
    """`ErrorReport.kind`: the name of the error's class, or of the first
    public class it derives from (`FileNotFoundError`, `ClientRunningError`,
    `InstallNotFoundError`, `CliError`)."""
    for cls in type(exc).__mro__:
        if not cls.__name__.startswith("_"):
            return cls.__name__
    return "Exception"  # pragma: no cover - `Exception` is public


def _handled[**P, R](fn: Callable[P, R]) -> Callable[P, R]:
    """Turn the library's typed errors into one line on stderr and an exit
    code; in a `--json` run that has printed no JSON yet, an `ErrorReport`
    on stdout too (M16-22)."""

    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        json_out = bool(kwargs.get("json_out"))
        _new_report(json_out)
        try:
            return fn(*args, **kwargs)
        except _HANDLED_ERRORS as exc:
            message, code = _error_words(exc, json_out)
            if json_out and not _Report.printed:
                report = ErrorReport(
                    kind=_error_kind(exc),
                    message=message,
                    exit_code=code,
                    notes=[str(n) for n in getattr(exc, "__notes__", ())],
                )
                if _Report.lines:
                    _emit_line(report)
                else:
                    _emit(report)
            _fail(message, code, exc)

    return wrapper


class _Out(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ErrorReport(_Out):
    """A `--json` run that ended in an error (M16-22): the error's class
    (`kind`), the line printed on stderr after `wowlab: ` (`message`), and
    the exit code. Printed on stdout only when the run printed no other JSON
    there (as one line in a line-delimited run, `log tail --follow`); the
    stderr line and the exit code are as without `--json`. `kind` is the
    bare class name (`_error_kind`), not a stable enum: it changes if a class
    is renamed, and a new error class adds a value. `notes` are the lines
    printed on stderr after that line, in order (M16-112: an install not
    found lists there every location checked and how to name the install),
    empty for most errors."""

    kind: str
    message: str
    exit_code: int
    notes: list[str] = Field(default_factory=list)


# ─── install, flavor, account, character ────────────────────────────────────


def _found_by(root: Path | None) -> str:
    if root is not None and str(root) != "":
        return "--root"
    if os.environ.get(install.ENV_ROOT, "").strip():
        return f"${install.ENV_ROOT}"
    return "a default location"


def _discover(root: Path | None) -> tuple[install.Install, str]:
    return install.discover(root), _found_by(root)


def _flavor_list(inst: install.Install) -> str:
    return ", ".join(f.folder for f in inst.flavors) or "none"


def _not_taken_named(inst: install.Install, name: str) -> install.NotTaken | None:
    """The `_*_` folder discovery did not take that `name` names, exactly
    or, failing that, as the only one of that name in any case."""
    exact = [n for n in inst.not_taken if n.name == name]
    folded = [n for n in inst.not_taken if n.name.casefold() == name.casefold()]
    match = exact or folded
    return match[0] if len(match) == 1 else None


def _no_flavors(inst: install.Install) -> str:
    """Why there is no flavor to read (M13-45): the folders named `_..._`
    that were not taken, each with its reason, when there are any."""
    if not inst.not_taken:
        return f"{inst.root} has no flavor folders (a folder named _..._ holding {install.FLAVOR_INFO})"
    words = f"{inst.root} {install.NO_READABLE_FLAVOR}; not taken: " + ", ".join(
        f"{n.name} ({install.not_taken_why(n)})" for n in inst.not_taken
    )
    if any(n.reason == "link" for n in inst.not_taken):
        words += f"; {install.NOT_TAKEN_LINK}"
    return words


def _not_taken_lines(not_taken: Sequence[install.NotTaken], other_dirs: Sequence[str]) -> list[str]:
    """`doctor` and `install show`: each `_..._` folder not taken, with the
    reason `--json` carries in `not_taken[].note` (M13-45). A model built
    with `other_dirs` alone (no reasons) gets the names only."""
    if not_taken:
        return ["Folders named _..._ not taken as flavors:"] + [
            f"  {n.name}: {n.note}" for n in not_taken
        ]
    if other_dirs:
        return ["Folders named _..._ not taken as flavors: " + ", ".join(other_dirs)]
    return []


def _env_flavor() -> str | None:
    """`$WOWLAB_FLAVOR`, stripped; None when it is unset or blank (M16-114)."""
    return os.environ.get(install.ENV_FLAVOR, "").strip() or None


def _given_flavor(flavor: str | None) -> str | None:
    """The flavor the player named: `--flavor`, else `$WOWLAB_FLAVOR`
    (M16-114). `--flavor` wins."""
    return flavor if flavor is not None else _env_flavor()


def _typed_args() -> list[str] | None:
    """The arguments this run was given after `wowlab`, as typed; None
    outside a command."""
    ctx = get_current_context(silent=True)
    if ctx is None:
        return None
    given = ctx.meta.get(_ARGS_KEY)
    return [str(a) for a in given] if isinstance(given, list) else None


def _with_flavor_lines(inst: install.Install) -> str:
    """This command once per flavor, with `--flavor` added, each quoted for
    this machine's shell, for a player to copy (M16-114)."""
    args = _typed_args()
    if args is None:
        ctx = get_current_context(silent=True)
        args = ctx.command_path.split()[1:] if ctx is not None else []
    lines = []
    for f in inst.flavors:
        # Before a `--`: after it, --flavor would be read as an argument.
        cut = args.index("--") if "--" in args else len(args)
        argv = ["wowlab", *args[:cut], "--flavor", f.folder, *args[cut:]]
        lines.append(_powershell_join(argv) if _shell() == "powershell" else shlex.join(argv))
    return ", ".join(f"`{line}`" for line in lines)


def _several_flavors(inst: install.Install) -> CliError:
    """The refusal when several flavors are installed and none is named."""
    return CliError(
        f"{inst.root} has {len(inst.flavors)} flavors ({_flavor_list(inst)}); choose one with "
        f"--flavor, or set {install.ENV_FLAVOR}. Run one of: {_with_flavor_lines(inst)}",
        EXIT_USAGE,
    )


def _select_flavor(inst: install.Install, name: str | None) -> install.Flavor:
    """The flavor `name` names (`--flavor`), else the one
    `$WOWLAB_FLAVOR` names, else the only one. Nothing here picks one of
    several by itself: a write never runs on a flavor the player did not
    name (M16-114)."""
    flavors = inst.flavors
    from_env = name is None and _env_flavor() is not None
    name = _given_flavor(name)
    if name is not None:
        exact = [f for f in flavors if f.folder == name]
        folded = [f for f in flavors if f.folder.casefold() == name.casefold()]
        match = exact or folded
        if len(match) == 1:
            return match[0]
        source = f" (from {install.ENV_FLAVOR})" if from_env else ""
        missed = _not_taken_named(inst, name) if not match else None
        if missed is not None:
            # M13-45: the folder is there; say why it was not taken.
            tail = f"; {install.NOT_TAKEN_LINK}" if missed.reason == "link" else ""
            raise CliError(
                f"{missed.name!r}{source} in {inst.root}: {missed.note}{tail}; "
                f"flavors found: {_flavor_list(inst)}",
                EXIT_USAGE,
            )
        raise CliError(
            f"no flavor folder {name!r}{source} in {inst.root} "
            f"(flavors found: {_flavor_list(inst)})",
            EXIT_USAGE,
        )
    if len(flavors) == 1:
        return flavors[0]
    if not flavors:
        raise CliError(_no_flavors(inst))
    raise _several_flavors(inst)


def _characters_in(inst: install.Install, f: install.Flavor, account: str | None) -> bool | None:
    """Whether the flavor holds any character folder (in the account
    `account` names, when it names one), as `char list` finds them: True
    when one was found; False when none was and every place that could
    hold one was looked inside; None (unknown) when one could not be: a
    folder that could not be listed, a link not followed, the walk's bound,
    or an error reading the flavor (M16-114)."""
    try:
        lay = layout.Layout.for_flavor(f, inst.root)
        acct: str | None = None
        if account is not None:
            try:
                acct = _select_account(lay, account).folder
            except CliError:
                return None if _hidden_account(lay, account) else False
        found = labaddon.survey(lay, account=acct, keep_records=False, every_folder=True)
    except (OSError, WowlabError):
        return None
    if any(not _first_name_folder(e) for e in found.characters):
        return True
    return None if found.not_looked_at else False


def _open_char(
    root: Path | None, flavor: str | None, account: str | None = None
) -> tuple[install.Install, install.Flavor, layout.Layout]:
    """`_open` for the `char` commands that read one character (M16-114):
    with several flavors and none named, the one flavor with character
    folders (in `account`, when given) is used when every other flavor
    could be read and has none, and one line on stderr says so; otherwise
    the several-flavors refusal stands. These commands only read; a write
    never picks a flavor."""
    inst, _ = _discover(root)
    if _given_flavor(flavor) is None and len(inst.flavors) > 1:
        seen = [(f, _characters_in(inst, f, account)) for f in inst.flavors]
        having = [f for f, has in seen if has is True]
        if len(having) == 1 and all(has is False for f, has in seen if f is not having[0]):
            chosen = having[0]
            if chosen.version:
                _note(
                    "wowlab: using {} ({}), the only flavor with character folders "
                    "(--flavor picks another)",
                    chosen.folder,
                    chosen.version,
                )
            else:
                _note(
                    "wowlab: using {}, the only flavor with character folders "
                    "(--flavor picks another)",
                    chosen.folder,
                )
            return inst, chosen, layout.Layout.for_flavor(chosen, inst.root)
    chosen = _select_flavor(inst, flavor)
    return inst, chosen, layout.Layout.for_flavor(chosen, inst.root)


def _open(
    root: Path | None, flavor: str | None
) -> tuple[install.Install, install.Flavor, layout.Layout]:
    inst, _ = _discover(root)
    chosen = _select_flavor(inst, flavor)
    return inst, chosen, layout.Layout.for_flavor(chosen, inst.root)


def _select_account(lay: layout.Layout, name: str | None) -> layout.Account:
    """The account folder `name`, or the only one. Only account folders
    count (M13-50): a stray folder under WTF/Account/ is neither chosen nor
    counted, and naming it is refused with the accounts listed."""
    walk = lay.wtf_walk()
    accounts = walk.accounts
    names = ", ".join(a.folder for a in accounts) or "none"
    if name is not None:
        match = [a for a in accounts if a.folder == name] or [
            a for a in accounts if a.folder.casefold() == name.casefold()
        ]
        if len(match) == 1:
            return match[0]
        stray = [p for p in walk.not_accounts if p.rsplit("/", 1)[-1].casefold() == name.casefold()]
        why = (
            f"; {stray[0]} is {layout.not_account_note(stray[0].rsplit('/', 1)[-1])}"
            if stray
            else ""
        )
        raise CliError(
            f"no account folder {name!r} (accounts wowlab could look inside: {names}){why}",
            EXIT_USAGE,
        )
    if len(accounts) == 1:
        return accounts[0]
    if not accounts:
        raise CliError(f"no account folders under WTF/Account/ in {lay.flavor_path}")
    raise CliError(f"{len(accounts)} accounts ({names}); choose one with --account", EXIT_USAGE)


def _select_character(account: layout.Account, spec: str) -> layout.Character:
    characters = [c for r in account.realms for c in r.characters]
    if "/" in spec:
        realm, _, folder = spec.rpartition("/")
        match = [c for c in characters if c.realm_folder == realm and c.folder == folder]
    else:
        match = [c for c in characters if c.folder == spec] or [
            c for c in characters if c.folder.casefold() == spec.casefold()
        ]
    if len(match) == 1:
        return match[0]
    listing = ", ".join(f"{c.realm_folder}/{c.folder}" for c in characters) or "none"
    if not match:
        raise CliError(
            f"no character folder {spec!r} in account {account.folder} (characters: {listing})",
            EXIT_USAGE,
        )
    raise CliError(
        f"{spec!r} names {len(match)} character folders; use <Realm folder>/<Character> "
        f"(characters: {listing})",
        EXIT_USAGE,
    )


Scope = Literal["global", "account", "character"]


class _Target(BaseModel):
    """Which WTF file a cvar/binds/macros command reads."""

    scope: Scope
    account: str | None
    character: str | None  # "<realm folder>/<character folder>"


def _wtf_file(
    lay: layout.Layout,
    file_name: str,
    scope: Scope,
    account: str | None,
    character: str | None,
) -> tuple[_Target, layout.WtfFile | None]:
    """The WTF file of `file_name` for the scope, or None when it is absent."""
    files = lay.wtf_files()
    folded = file_name.casefold()
    if scope == "global":
        target = _Target(scope=scope, account=None, character=None)
        found = [f for f in files if f.scope == "machine" and f.name.casefold() == folded]
        return target, (found[0] if found else None)
    acct = _select_account(lay, account)
    if scope == "account":
        target = _Target(scope=scope, account=acct.folder, character=None)
        found = [
            f
            for f in files
            if f.scope == "account" and f.account == acct.folder and f.name.casefold() == folded
        ]
        return target, (found[0] if found else None)
    if character is None:
        raise CliError("--scope character needs --character", EXIT_USAGE)
    char = _select_character(acct, character)
    target = _Target(
        scope=scope, account=acct.folder, character=f"{char.realm_folder}/{char.folder}"
    )
    found = [
        f
        for f in files
        if f.scope == "character"
        and f.account == acct.folder
        and f.realm_folder == char.realm_folder
        and f.character_folder == char.folder
        and f.name.casefold() == folded
    ]
    return target, (found[0] if found else None)


def _client_names(flavor_dir: Path) -> list[str]:
    """Executable names in a flavor folder, for `process` (`extra_names`).

    Lists the folder (and a macOS bundle's `Contents/MacOS/`); opens nothing.
    The same rule as `guard`'s own (private) helper, which it applies before
    a write; a test pins the two together. A flavor folder that cannot be
    listed raises `OSError`, so a caller reports "could not check" rather
    than checking with no names.
    """
    names: list[str] = []
    for child in sorted(longpath.iterdir(flavor_dir)):
        folded = child.name.casefold()
        if folded.endswith(".exe"):
            names.append(child.name)
        elif folded.endswith(".app"):
            names.append(child.name[: -len(".app")])
            with contextlib.suppress(OSError):
                names.extend(p.name for p in longpath.iterdir(child / "Contents" / "MacOS"))
    return names


def _roots(inst: install.Install) -> list[Path]:
    roots = [Path(inst.root)]
    try:
        resolved = longpath.resolve(inst.root)
    except (OSError, RuntimeError):
        return roots
    if resolved != roots[0]:
        roots.append(resolved)
    return roots


def _shell() -> Literal["posix", "powershell"]:
    return "powershell" if os.name == "nt" else "posix"


# Characters an argument may hold and still be left bare in either shell.
_BARE = re.compile(r"[A-Za-z0-9_./:=+@%\\-]+")


# PowerShell takes each of these as a single quote, inside a quoted string too.
_PS_QUOTES = "'\u2018\u2019\u201a\u201b"


def _powershell_join(argv: Sequence[str]) -> str:
    """`argv` as one PowerShell command line: an argument of plain characters
    bare, any other in single quotes, where nothing is special but a quote
    character, which is doubled. Windows Terminal opens PowerShell by default."""
    out: list[str] = []
    for arg in argv:
        if _BARE.fullmatch(arg) and not arg.startswith("@"):
            out.append(arg)
            continue
        doubled = "".join(c * 2 if c in _PS_QUOTES else c for c in arg)
        out.append(f"'{doubled}'")
    return " ".join(out)


def _sv_name(f: layout.SavedVariablesFile) -> str:
    return f.path.rsplit("/", 1)[-1]


def _listed[T](read: Callable[..., T], lay: layout.Layout, rel: str, **kwargs: Any) -> T:
    """`read(<flavor folder>/rel, root=<install root>, **kwargs)`: a file a
    listing of the install just found, read from the install root one folder
    at a time (M16-49, M16-65). The listing follows no link, so a link met
    now changed while wowlab read it, and the refusal says so."""
    try:
        return read(lay.flavor_path / rel, root=lay.install_root, **kwargs)
    except snapshot.LinkedPathError as exc:
        raise exc.changed_while_read(lay.flavor_path) from exc


# What each kind of snapshot is, in words (M16-09): `snap show` prints it, and
# `explain` uses the pre-write one when it points at such a copy.
_KIND_WORDS: dict[snapshot.SnapshotKind, str] = {
    "user": "one you took",
    "first-run": "taken by doctor on its first run",
    "profile": "a saved profile",
    "pre-write": (
        "taken automatically just before a change; restoring it puts the files back "
        "as they were just before that change"
    ),
    "imported": (
        "added from a file by snap import, so not one you took on this computer; "
        "latest-user never names it"
    ),
}


def _pre_write_ids(store: snapshot.SnapshotStore) -> frozenset[str]:
    """The snapshots the store's journal names: the pre-write copies, for
    manifests written before kinds existed (`snapshot.kind_of`). A journal
    that cannot be read names none, so such a manifest reads as `user`."""
    try:
        return frozenset(r.snapshot_id for r in guard.history(store=store.path))
    except guard.GuardError:
        return frozenset()


def _client_running(inst: install.Install, chosen: install.Flavor) -> bool | None:
    """True running, False not, None when it cannot be told."""
    try:
        state = process.client_state(
            _roots(inst), flavor_folders=[chosen.folder], extra_names=_client_names(chosen.path)
        )
    except Exception:
        return None
    if state is process.ClientState.RUNNING:
        return True
    if state is process.ClientState.NOT_RUNNING:
        return False
    return None


def _is_lab_file(f: layout.SavedVariablesFile) -> bool:
    return not f.backup and labaddon.is_lab_file_name(_sv_name(f))


def _local_time(mtime_ns: int) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S %z", time.localtime(mtime_ns // 1_000_000_000))


def _lab_char_file(
    acct: layout.Account,
    files: Sequence[layout.SavedVariablesFile],
    character: str | None,
) -> tuple[layout.SavedVariablesFile, Literal["--character", "latest"], str]:
    """The character `WowLab.lua` to read: in the folder `--character` names,
    else in the folder holding the newest file by modification time (ties by
    path, said in the third value). Within the folder the file is the one
    `labaddon.choose_lab_file` chooses (M12-09), the rule `char list` uses;
    `files` are regular files only, so for a link, FIFO or folder of that
    name, or a SavedVariables/ folder that cannot be listed, this can still
    answer differently from `char list`: it finds no file there and says so
    (with install advice), where `char list` says it could not look."""
    char_files = [f for f in files if f.scope == "character"]
    how: Literal["--character", "latest"]
    tie = ""
    if character is not None:
        char = _select_character(acct, character)
        mine = [
            f
            for f in char_files
            if f.realm_folder == char.realm_folder and f.character_folder == char.folder
        ]
        if not mine:
            having = (
                ", ".join(f"{f.realm_folder}/{f.character_folder}" for f in char_files) or "none"
            )
            raise CliError(
                f"no {labaddon.ADDON_NAME}.lua in {char.realm_folder}/{char.folder}. Character "
                f"folders in account {acct.folder} that have one: {having}. If none is listed: "
                "install the lab-addon (wowlab addon install lab), log in on the character, "
                "then log out or /reload."
            )
        target, how = _chosen_lab_file(mine), "--character"
    else:
        if not char_files:
            raise CliError(
                f"no character in account {acct.folder} has a {labaddon.ADDON_NAME}.lua "
                "(install the lab-addon with `wowlab addon install lab`, log in, then log out "
                "or /reload)"
            )
        ranked = sorted(char_files, key=lambda f: (f.mtime_ns, f.path), reverse=True)
        top = ranked[0]
        folder = (top.realm_folder, top.character_folder)
        target = _chosen_lab_file(
            [f for f in char_files if (f.realm_folder, f.character_folder) == folder]
        )
        others = [f for f in ranked if (f.realm_folder, f.character_folder) != folder]
        if others and others[0].mtime_ns == top.mtime_ns:
            tie = (
                f"; tied with {others[0].realm_folder}/{others[0].character_folder} on that "
                "time, taken by path order"
            )
        how = "latest"
    return target, how, tie


def _chosen_lab_file(
    mine: Sequence[layout.SavedVariablesFile],
) -> layout.SavedVariablesFile:
    """The file `labaddon.choose_lab_file` chooses among one character
    folder's `WowLab.lua` files (at least one); several with no single
    choice is an error naming the folder."""
    by_path = {f.path: f for f in mine}
    who = f"{mine[0].realm_folder}/{mine[0].character_folder}"
    try:
        path = labaddon.choose_lab_file(list(by_path))
    except labaddon.LabAddonError as exc:
        raise CliError(f"{who}: {exc}") from exc
    if path is None:  # every file given is named like WowLab.lua (`_is_lab_file`)
        raise CliError(f"{who}: no {labaddon.LAB_FILE_NAME}")
    return by_path[path]


# How a row and stderr name the folder `CharacterFile.file` is, when it is one.
_BLOCKED_PLACE = {
    "character_folder": "the character folder",
    "saved_variables_folder": "the character's SavedVariables folder",
}


def _plural(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _first_name_folder(e: labaddon.CharacterFile) -> bool:
    return e.no_lab_record is not None and e.no_lab_record.first_name_folder


def _hidden_account(lay: layout.Layout, name: str) -> list[labaddon.NotLookedAt]:
    """What wowlab could not look inside that could be, or hold, the account
    folder `name` (compared with case folded, as `_select_account` does):
    a place above every account, or one in an account of that name. The
    listing alone answers this: `budget=0` opens no file."""
    found = labaddon.survey(lay, keep_records=False, budget=0).not_looked_at
    return [n for n in found if n.account is None or n.account.casefold() == name.casefold()]


# ─── version ─────────────────────────────────────────────────────────────────


def _print_version(value: bool) -> None:
    if value:
        typer.echo(f"wowlab {__version__}")
        raise typer.Exit


@app.callback()
def main(
    ctx: typer.Context,
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_print_version,
            is_eager=True,
            help="Print the version and exit.",
        ),
    ] = False,
) -> None:
    """Local-only toolchain over a World of Warcraft install."""
    _new_report()
    ctx.with_resource(_library_log_on_stderr())
