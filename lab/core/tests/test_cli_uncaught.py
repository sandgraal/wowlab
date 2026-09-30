"""Uncaught exceptions and click usage errors print escaped (M11-33).

From the M11-30 security review (finding 3). Typer's pretty exceptions are
off, and the `wowlab` console script (`wowlab_core.cli:app`) prints an
exception nothing caught through `cli._excepthook`: Python's traceback, line
by line through `_say_err`, with the message and each note on one line
whatever they hold. A click usage error, whose message can hold the user's
own arguments, prints its usage and help hint as click does and its error on
one escaped line. A `_log.exception` record prints its message on one line
and its traceback line by line the same way.

Every input here is `constructed` (L8, hostile-input cases): an exception
message holding a line feed followed by text that would pass for a line of
the CLI's own output, U+202E, U+2028 and ESC sequences. The console-script
tests run `cli.app()` in a child Python process, as the generated `wowlab`
script does, with the function that fails replaced first. The child never
reaches an install: the replaced function raises before anything is read,
`WOWLAB_WOW_ROOT` points into `tmp_path` and the user data directory is
redirected there.

Characters outside printable ASCII are spelled with `chr` so that none of
them sits raw in this file.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import subprocess
import sys
import traceback
import unicodedata
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import typer
from typer.testing import CliRunner

from wowlab_core import cli, guard, install

LF = chr(0x0A)
ESC = chr(0x1B)
BEL = chr(0x07)
RLO = chr(0x202E)  # RIGHT-TO-LEFT OVERRIDE, Cf
LSEP = chr(0x2028)  # LINE SEPARATOR, Zl

# The hostile text: every part after the first would pass for a line of the
# CLI's own output, or reorder, retitle or clear the terminal, if it were
# printed raw.
MESSAGE = (
    f"folder Evil{LF}wowlab: restored 1 file{RLO}elif.gnp{LSEP}wowlab: done"
    f"{ESC}]0;owned{BEL}{ESC}[2J"
)
SHOWN = (
    "folder Evil\\x0awowlab: restored 1 file\\xe2\\x80\\xaeelif.gnp\\xe2\\x80\\xa8wowlab: done"
    "\\x1b]0;owned\\x07\\x1b[2J"
)

CAUSE = "The above exception was the direct cause of the following exception:"
CONTEXT = "During handling of the above exception, another exception occurred:"
TRACEBACK = "Traceback (most recent call last):"

UNSAFE_CATEGORIES = ("Cc", "Cf", "Zl", "Zp")

runner = CliRunner()


def _unsafe(text: str) -> list[str]:
    """Every character of `text` a terminal must not get raw, as hex (line
    feeds included: callers pass one line)."""
    return [
        hex(ord(c))
        for c in text
        if unicodedata.category(c) in UNSAFE_CATEGORIES or 0xD800 <= ord(c) <= 0xDFFF
    ]


def _lines_are_safe(text: str) -> bool:
    return all(_unsafe(line) == [] for line in text.split("\n"))


def _no_spoofed_line(text: str) -> bool:
    """No line of `text` starts with a part of `MESSAGE` that follows a line
    break or a separator in it."""
    return not any(
        line.startswith(("wowlab: restored", "wowlab: done")) for line in text.split("\n")
    )


def test_the_hostile_message_holds_what_the_ticket_names() -> None:
    """The positive control: `MESSAGE` holds a line feed, U+202E, U+2028 and
    an ESC sequence, so each test below has something to escape."""
    assert {LF, RLO, LSEP, ESC} <= set(MESSAGE)
    assert cli._safe(MESSAGE) == SHOWN


# ─── the console script, end to end ─────────────────────────────────────────

# Run in a child process as `python -c CHILD <where> <user data dir>`; the
# generated `wowlab` script is `sys.exit(app())` with `sys.argv` set.
CHILD = """
import contextlib
import sys
from pathlib import Path

import platformdirs

from wowlab_core import cli

where, userdata = sys.argv[1], Path(sys.argv[2])
message = MESSAGE
platformdirs.user_data_path = lambda *args, **kwargs: userdata


def boom(*args, **kwargs):
    exc = RuntimeError(message)
    exc.add_note("note: " + message)
    raise exc from ValueError(message)


def no_install(*args, **kwargs):
    raise cli.CliError("constructed: no install is looked for")


class Version:
    def __format__(self, spec):
        boom()


@contextlib.contextmanager
def fails_on_enter():
    boom()
    yield


@contextlib.contextmanager
def fails_on_exit():
    # The command's own exit (typer.Exit) is thrown in at the yield.
    try:
        yield
    finally:
        boom()


cli._discover = no_install
if where == "command-body":
    cli._discover = boom
    args = ["install", "show"]
elif where == "main-callback":
    cli._library_log_on_stderr = fails_on_enter
    args = ["install", "show"]
elif where == "main-callback-resource-exit":
    cli._library_log_on_stderr = fails_on_exit
    args = ["install", "show"]
elif where == "version-callback":
    cli.__version__ = Version()
    args = ["--version"]
else:
    raise SystemExit("unknown case " + where)
sys.argv = ["wowlab", *args]
sys.exit(cli.app())
""".replace("MESSAGE", ascii(MESSAGE))

WHERE = {
    # an exception `_handled` does not catch, raised in a command body
    "command-body": [],
    # the root callback's `ctx.with_resource`, failing as it is entered
    "main-callback": [],
    # the same resource failing as the root context closes, after the
    # command has already printed its own one-line error
    "main-callback-resource-exit": ["wowlab: constructed: no install is looked for"],
    # the eager `--version` callback
    "version-callback": [],
}


def _lines_of(output: bytes) -> str:
    """A child's output as text, strictly UTF-8, with CR LF line breaks
    (Windows) as LF."""
    return output.decode("utf-8").replace("\r\n", "\n")


@pytest.mark.parametrize("where", [pytest.param(w, id=f"constructed-{w}") for w in WHERE])
def test_the_console_script_prints_an_uncaught_exception_escaped_constructed(
    tmp_path: Path, where: str
) -> None:
    env = {
        **os.environ,
        install.ENV_ROOT: str(tmp_path / "no install"),
        "PYTHONIOENCODING": "utf-8",
    }
    child = subprocess.run(
        [sys.executable, "-c", CHILD, where, str(tmp_path / "userdata")],
        capture_output=True,
        cwd=tmp_path,
        env=env,
        timeout=120,
        check=False,
    )
    # Strict decoding: an escape that produced bytes that are not UTF-8
    # fails here. On Windows Python writes each line break as CR LF; those
    # pairs become LF, and any CR left over is a raw one (the checks below
    # count it). Not `text=True`: universal newlines would also turn a lone
    # raw CR into LF and hide it.
    out = _lines_of(child.stdout)
    err = _lines_of(child.stderr)
    assert "\r" not in out and "\r" not in err
    assert child.returncode == 1, (child.returncode, out, err)
    assert out == ""
    assert _lines_are_safe(err), _unsafe(err)
    assert _no_spoofed_line(err), err
    lines = err.split("\n")
    before = WHERE[where]
    # Anything the command printed itself, then the cause (never raised, so
    # no frames) on one line, then the link to the exception.
    assert lines[: len(before) + 5] == [*before, f"ValueError: {SHOWN}", "", CAUSE, "", TRACEBACK]
    frames = lines[len(before) + 5 : -3]
    assert frames and all(line.startswith("  ") for line in frames), err
    # The exception itself on one line, its note on one line, and the line
    # feed that ends the last line.
    assert lines[-3:] == [f"RuntimeError: {SHOWN}", f"note: {SHOWN}", ""], err


# ─── the hook, in process ────────────────────────────────────────────────────


def _raise_boom(*args: Any, **kwargs: Any) -> None:
    raise RuntimeError(MESSAGE)


def test_the_app_installs_the_escaping_hook_constructed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Typer's `__call__` puts its own hook in `sys.excepthook` on every
    call; the app puts `cli._excepthook` back before an exception leaves
    it, so Python's top level prints through it."""
    monkeypatch.setattr(sys, "excepthook", sys.__excepthook__)
    monkeypatch.setattr(cli, "_discover", _raise_boom)
    with pytest.raises(RuntimeError):
        cli.app(["install", "show"], prog_name="wowlab")
    assert sys.excepthook is cli._excepthook
    assert cli.app.pretty_exceptions_enable is False


def test_a_normal_exit_leaves_the_hook_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every run ends in `SystemExit`; one with an int code is not an
    uncaught exception, so the process's hook is left as it was."""
    monkeypatch.setattr(sys, "excepthook", sys.__excepthook__)
    for args, code in ((["--version"], 0), (["--bogus"], cli.EXIT_USAGE)):
        with pytest.raises(SystemExit) as exited:
            cli.app(args, prog_name="wowlab")
        assert exited.value.code == code
        assert sys.excepthook is sys.__excepthook__


def test_a_system_exit_with_a_text_code_is_one_escaped_line_constructed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """CPython prints a `SystemExit` code that is not an int or None itself,
    raw, and exits 1; the app prints it through `_say_err` and exits 1. The
    escaping hook is in place first, in case Ctrl-C interrupts that."""

    def text_exit(*args: Any, **kwargs: Any) -> None:
        raise SystemExit(MESSAGE)

    monkeypatch.setattr(sys, "excepthook", sys.__excepthook__)
    monkeypatch.setattr(cli, "_discover", text_exit)
    with pytest.raises(SystemExit) as exited:
        cli.app(["install", "show"], prog_name="wowlab")
    assert exited.value.code == cli.EXIT_ERROR
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"{SHOWN}\n"
    assert sys.excepthook is cli._excepthook


def test_ctrl_c_while_a_text_exit_code_prints_is_swallowed_constructed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Ctrl-C while the text is escaped: the app still exits 1 and prints
    nothing raw (the child-process version is the M11-33 review probe)."""

    def text_exit(*args: Any, **kwargs: Any) -> None:
        raise SystemExit(MESSAGE)

    def interrupted(text: str = "") -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(sys, "excepthook", sys.__excepthook__)
    monkeypatch.setattr(cli, "_discover", text_exit)
    monkeypatch.setattr(cli, "_say_err", interrupted)
    try:
        cli.app(["install", "show"], prog_name="wowlab")
    except SystemExit as exited:
        assert exited.code == cli.EXIT_ERROR
    except BaseException as escaped:
        pytest.fail(f"{escaped!r} left the app")
    assert capsys.readouterr() == ("", "")
    assert sys.excepthook is cli._excepthook


def test_ctrl_c_before_click_runs_leaves_through_the_escaping_hook_constructed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A KeyboardInterrupt can leave the app without passing click's own
    handling (typer builds the command before click's `try`); it is an
    uncaught exception like any other, so `_excepthook` is put in place."""

    def interrupted(*args: Any, **kwargs: Any) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(sys, "excepthook", sys.__excepthook__)
    monkeypatch.setattr(typer.main, "get_command", interrupted)
    with pytest.raises(KeyboardInterrupt):
        cli.app(["--version"], prog_name="wowlab")
    assert sys.excepthook is cli._excepthook


def _caught(fn: Any) -> BaseException:
    try:
        fn()
    except BaseException as exc:
        return exc
    raise AssertionError("expected an exception")


def _call_hook(exc: BaseException) -> None:
    """`cli._excepthook(exc)`; anything it lets out fails the test (a
    KeyboardInterrupt would otherwise stop the whole run)."""
    try:
        cli._excepthook(type(exc), exc, exc.__traceback__)
    except BaseException as escaped:
        pytest.fail(f"the hook let {escaped!r} out")


def _hook_output(exc: BaseException, capsys: pytest.CaptureFixture[str]) -> list[str]:
    _call_hook(exc)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert _lines_are_safe(captured.err), _unsafe(captured.err)
    assert _no_spoofed_line(captured.err), captured.err
    lines = captured.err.split("\n")
    assert lines[-1] == ""
    return lines[:-1]


def _context_chain() -> None:
    try:
        raise ValueError(MESSAGE)
    except ValueError:
        exc = RuntimeError(MESSAGE)
        exc.add_note(MESSAGE)
        exc.add_note("a second note")
        raise exc  # noqa: B904 (a context, not a cause)


def test_the_hook_prints_a_context_chain_escaped_constructed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    lines = _hook_output(_caught(_context_chain), capsys)
    assert lines[0] == TRACEBACK
    at = lines.index(f"ValueError: {SHOWN}")
    assert lines[at + 1 : at + 5] == ["", CONTEXT, "", TRACEBACK]
    assert lines[-3:] == [f"RuntimeError: {SHOWN}", SHOWN, "a second note"]


def test_the_hook_prints_a_cause_and_hides_a_suppressed_context_constructed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def chained() -> None:
        try:
            raise KeyError("hidden context")
        except KeyError:
            raise LookupError(MESSAGE) from OSError(MESSAGE)

    lines = _hook_output(_caught(chained), capsys)
    assert lines[:4] == [f"OSError: {SHOWN}", "", CAUSE, ""]
    assert lines[-1] == f"LookupError: {SHOWN}"
    assert not any("hidden context" in line for line in lines)


def test_the_hook_prints_an_exception_group_escaped_constructed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Each sub-exception is printed, indented, after the group; a
    sub-exception whose context is the group itself is not printed again."""

    def grouped() -> None:
        inner = ValueError(MESSAGE)
        group = ExceptionGroup(MESSAGE, [inner, OSError(MESSAGE)])
        inner.__context__ = group  # a cycle the renderer must not follow
        raise group

    lines = _hook_output(_caught(grouped), capsys)
    at = lines.index(f"ExceptionGroup: {SHOWN} (2 sub-exceptions)")
    assert lines[at + 1] == "sub-exception 1 of 2:"
    assert f"  ValueError: {SHOWN}" in lines
    assert f"  OSError: {SHOWN}" in lines
    assert lines.count(f"ExceptionGroup: {SHOWN} (2 sub-exceptions)") == 1


def test_a_group_member_printed_elsewhere_is_one_line_constructed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Member 2 is member 1's context, so it is printed under member 1; its
    own section is one line saying so, never empty (Python prints it twice)."""
    first, second = ValueError(MESSAGE), OSError(MESSAGE)
    first.__context__ = second
    lines = _hook_output(ExceptionGroup("g", [first, second]), capsys)
    assert lines == [
        "ExceptionGroup: g (2 sub-exceptions)",
        "sub-exception 1 of 2:",
        f"  OSError: {SHOWN}",
        "  ",
        f"  {CONTEXT}",
        "  ",
        f"  ValueError: {SHOWN}",
        "sub-exception 2 of 2:",
        "  [printed elsewhere in this traceback]",
    ]


def test_the_hook_hides_a_context_raised_from_none_constructed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`raise ... from None` hides the context, as Python does."""

    def from_none() -> None:
        try:
            raise KeyError(MESSAGE)
        except KeyError:
            raise LookupError("the error shown") from None

    lines = _hook_output(_caught(from_none), capsys)
    assert lines[0] == TRACEBACK
    assert lines[-1] == "LookupError: the error shown"
    assert CONTEXT not in lines
    assert not any("folder Evil" in line for line in lines), "the context's message"


# Three chains that loop: a context cycle of two exceptions, a cause cycle
# of two, and an exception that is its own context. Rendered in a child
# process that a watchdog ends after 3 s, so a renderer that follows the
# loop fails this test instead of hanging the run. ASCII JSON out: the lines
# `_traceback_lines` makes and the lines Python's own formatter makes.
CYCLES = """
import faulthandler
import json
import traceback

from wowlab_core import cli

a, b = ValueError("a"), OSError("b")
a.__context__, b.__context__ = b, a
c, d = ValueError("c"), OSError("d")
c.__cause__, d.__cause__ = d, c
e = ValueError("e")
e.__context__ = e
faulthandler.dump_traceback_later(3, exit=True)
out = {
    name: [cli._traceback_lines(x), "".join(traceback.format_exception(x)).split(chr(10))[:-1]]
    for name, x in (("context", a), ("cause", c), ("self", e))
}
faulthandler.cancel_dump_traceback_later()
print(json.dumps(out))
"""


def test_the_renderer_stops_where_a_chain_loops_constructed(tmp_path: Path) -> None:
    child = subprocess.run(
        [sys.executable, "-c", CYCLES],
        capture_output=True,
        cwd=tmp_path,
        timeout=120,
        check=False,
    )
    assert child.returncode == 0, _lines_of(child.stderr)
    found = json.loads(child.stdout)
    assert found["context"][0] == ["OSError: b", "", CONTEXT, "", "ValueError: a"]
    assert found["cause"][0] == ["OSError: d", "", CAUSE, "", "ValueError: c"]
    assert found["self"][0] == ["ValueError: e"]
    for name, (ours, python) in found.items():
        assert ours == python, name


def _python_lines(exc: BaseException) -> list[str]:
    return "".join(traceback.format_exception(exc)).split("\n")[:-1]


def _count(lines: list[str], text: str) -> int:
    return sum(text in line for line in lines)


def test_an_exception_group_is_bounded_as_python_bounds_it_constructed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Python's limits: 15 sub-exceptions of a group, then "and N more
    exceptions"; a group 10 deep is one line. A group 40 wide prints 15
    sub-exceptions, and one 5,000 deep prints 10 levels, as Python does,
    instead of every member (or a RecursionError)."""
    wide = ExceptionGroup(MESSAGE, [ValueError(MESSAGE) for _ in range(40)])
    lines = _hook_output(wide, capsys)
    assert _count(lines, f"ValueError: {SHOWN}") == 15 == _count(_python_lines(wide), "ValueError")
    assert lines[-1] == "and 25 more exceptions"
    assert _count(_python_lines(wide), "and 25 more exceptions") == 1

    sixteen = ExceptionGroup("g", [ValueError("v") for _ in range(16)])
    assert _hook_output(sixteen, capsys)[-1] == "and 1 more exception"

    deep: BaseException = ValueError(MESSAGE)
    for _ in range(5_000):
        deep = ExceptionGroup(MESSAGE, [deep])
    lines = _hook_output(deep, capsys)
    assert len(lines) < 100
    assert lines[-1].strip() == "... (max_group_depth is 10)"
    python = _python_lines(deep)
    assert _count(lines, "ExceptionGroup: ") == 10 == _count(python, "ExceptionGroup: ")
    assert _count(python, "... (max_group_depth is 10)") == 1


class _UnprintableError(Exception):
    def __str__(self) -> str:
        raise RuntimeError(MESSAGE)


def _raise_unprintable() -> None:
    raise _UnprintableError


def test_the_hook_survives_a_message_that_cannot_be_made_constructed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A message or note whose `str()` fails is shown as Python shows it."""
    exc = _caught(_raise_unprintable)
    exc.__notes__ = [_UnprintableError()]
    lines = _hook_output(exc, capsys)
    name = f"{__name__}._UnprintableError"
    assert lines[-2:] == [f"{name}: <exception str() failed>", "<note str() failed>"]
    expected = "".join(traceback.format_exception(exc)).split("\n")[:-1]
    assert lines == expected, "Python's own words for both"


def test_the_hook_falls_back_to_one_line_when_rendering_fails_constructed(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the traceback cannot be rendered, Python would print the original
    exception itself, raw, once the hook raised; the hook prints one line
    naming the type instead and returns."""

    def broken(exc: BaseException) -> list[str]:
        raise RuntimeError(MESSAGE)

    monkeypatch.setattr(cli, "_traceback_lines", broken)
    exc = RuntimeError(MESSAGE)
    assert _hook_output(exc, capsys) == ["wowlab: RuntimeError: its traceback could not be printed"]


class _InterruptedError(Exception):
    """Its message cannot be made: Ctrl-C arrives while it is."""

    def __str__(self) -> str:
        raise KeyboardInterrupt


def _raise_interrupted() -> None:
    exc = _InterruptedError()
    exc.add_note(MESSAGE)
    raise exc


def test_the_hook_survives_ctrl_c_while_it_renders_constructed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A KeyboardInterrupt out of the hook would make Python print the
    original exception itself, raw; the hook prints one line instead."""
    exc = _caught(_raise_interrupted)
    assert _hook_output(exc, capsys) == [
        "wowlab: _InterruptedError: its traceback could not be printed"
    ]


@pytest.mark.parametrize(
    "error",
    [pytest.param(KeyboardInterrupt, id="ctrl-c"), pytest.param(BrokenPipeError, id="broken-pipe")],
)
def test_the_hook_survives_a_write_that_fails_constructed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], error: type[BaseException]
) -> None:
    """A write to stderr that fails, on the first line or on the fallback
    line, stops the hook quietly: it returns, and nothing was written raw."""
    calls: list[str] = []

    def failing(text: str = "") -> None:
        calls.append(text)
        raise error

    monkeypatch.setattr(cli, "_say_err", failing)
    _call_hook(_caught(_context_chain))
    assert len(calls) == 2, "the first line, then the fallback line"
    assert capsys.readouterr() == ("", "")


def test_the_hook_survives_two_ctrl_c_in_a_row_constructed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Ctrl-C while the message is made, then again while the fallback line
    is written: the hook still returns, and nothing was written raw."""
    calls: list[str] = []

    def interrupted(text: str = "") -> None:
        calls.append(text)
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "_say_err", interrupted)
    _call_hook(_caught(_raise_interrupted))
    assert calls == ["wowlab: _InterruptedError: its traceback could not be printed"]
    assert capsys.readouterr() == ("", "")


def test_the_hooks_last_guard_makes_no_call_constructed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Python checks for a pending signal at a call, so a guard that makes
    one after catching a first Ctrl-C (building `contextlib.suppress(...)`
    does) lets a second one out. Here any use of `contextlib` in `cli`
    raises KeyboardInterrupt, standing for that second signal, while the
    rendering and the fallback line both fail: the hook still returns."""

    class _Signalled:
        def __getattr__(self, name: str) -> Any:
            raise KeyboardInterrupt

    def interrupted(text: str = "") -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "contextlib", _Signalled())
    monkeypatch.setattr(cli, "_say_err", interrupted)
    _call_hook(_caught(_raise_interrupted))
    assert capsys.readouterr() == ("", "")


def _plain_chain() -> None:
    try:
        raise ValueError("first")
    except ValueError as first:
        exc = KeyError("second")
        exc.add_note("a note")
        raise exc from first


def _plain_context() -> None:
    try:
        raise KeyError("missing")
    except KeyError:
        raise OSError(2, "no such file")  # noqa: B904 (a context, not a cause)


def _notes_text() -> None:
    exc = ValueError("notes that are one string")
    exc.__notes__ = "a note"  # type: ignore[assignment]
    raise exc


class _NowhereError(Exception):
    pass


_NowhereError.__module__ = None  # type: ignore[assignment]


def _nowhere() -> None:
    raise _NowhereError("a type whose module is None")


class _NotesFailError(Exception):
    @property
    def __notes__(self) -> list[str]:  # type: ignore[override]
        raise RuntimeError("no notes")


def _notes_fail() -> None:
    raise _NotesFailError("its notes cannot be read")


def _from_none() -> None:
    try:
        raise KeyError("a hidden context")
    except KeyError:
        raise LookupError("the error shown") from None


@pytest.mark.parametrize(
    "fn",
    [
        pytest.param(_plain_chain, id="cause-and-note"),
        pytest.param(_plain_context, id="context"),
        pytest.param(lambda: int("x"), id="builtin"),
        pytest.param(lambda: cli._safe(None), id="library-frame"),
        pytest.param(_notes_text, id="notes-a-string"),
        pytest.param(_nowhere, id="module-none"),
        pytest.param(_notes_fail, id="notes-raise"),
        pytest.param(_from_none, id="from-none"),
    ],
)
def test_the_hook_prints_what_python_prints_when_nothing_needs_escaping(
    capsys: pytest.CaptureFixture[str], fn: Any
) -> None:
    """For text with nothing to escape, the lines are Python's own."""
    exc = _caught(fn)
    assert _hook_output(exc, capsys) == _python_lines(exc)


# ─── click usage errors ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("args", "usage", "error"),
    [
        pytest.param(
            ["--" + MESSAGE],
            "Usage: wowlab [OPTIONS] COMMAND [ARGS]...",
            "Error: No such option: --" + SHOWN,
            id="constructed-root-option",
        ),
        pytest.param(
            ["sv", "list", "--" + MESSAGE],
            "Usage: wowlab sv list [OPTIONS]",
            "Error: No such option: --" + SHOWN,
            id="constructed-subcommand-option",
        ),
        pytest.param(
            ["explain", "a", MESSAGE],
            "Usage: wowlab explain [OPTIONS] {path}",
            f"Error: Got unexpected extra argument(s) ({SHOWN})",
            id="constructed-extra-argument",
        ),
    ],
)
def test_a_usage_error_prints_the_users_argument_escaped_constructed(
    args: list[str], usage: str, error: str
) -> None:
    result = runner.invoke(cli.app, args)
    assert result.exit_code == cli.EXIT_USAGE, (result.stdout, result.stderr, result.exception)
    assert result.stdout == ""
    assert _lines_are_safe(result.stderr), _unsafe(result.stderr)
    assert _no_spoofed_line(result.stderr), result.stderr
    command = usage.removeprefix("Usage: ").split(" [OPTIONS]")[0]
    assert result.stderr.split("\n") == [
        usage,
        f"Try '{command} --help' for help.",
        "",
        error,
        "",
    ]


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        pytest.param(
            ["--bogus"],
            "Usage: wowlab [OPTIONS] COMMAND [ARGS]...\nTry 'wowlab --help' for help.\n\n"
            "Error: No such option: --bogus\n",
            id="unknown-option",
        ),
        pytest.param(
            ["tre"],
            "Usage: wowlab [OPTIONS] COMMAND [ARGS]...\nTry 'wowlab --help' for help.\n\n"
            "Error: No such command 'tre'. Did you mean 'tree'?\n",
            id="unknown-command",
        ),
        pytest.param(
            ["explain"],
            "Usage: wowlab explain [OPTIONS] {path}\nTry 'wowlab explain --help' for help.\n\n"
            "Error: Missing argument 'path'.\n",
            id="missing-argument",
        ),
        pytest.param(
            ["log", "tail", "-n", "x"],
            "Usage: wowlab log tail [OPTIONS]\nTry 'wowlab log tail --help' for help.\n\n"
            "Error: Invalid value for '--lines' / '-n': 'x' is not a valid int range.\n",
            id="bad-value",
        ),
    ],
)
def test_a_usage_error_with_nothing_to_escape_prints_as_click_does(
    args: list[str], expected: str
) -> None:
    """The text is the one click printed before M11-33, byte for byte."""
    result = runner.invoke(cli.app, args)
    assert result.exit_code == cli.EXIT_USAGE
    assert result.stdout == ""
    assert result.stderr == expected


@pytest.mark.parametrize("args", [pytest.param([], id="root"), pytest.param(["sv"], id="group")])
def test_no_arguments_prints_the_help_on_stderr_as_before(args: list[str]) -> None:
    """`no_args_is_help`: click shows the help instead of an error, on
    stderr with exit 2; its line breaks are kept."""
    result = runner.invoke(cli.app, args)
    helped = runner.invoke(cli.app, [*args, "--help"])
    assert result.exit_code == cli.EXIT_USAGE
    assert helped.exit_code == 0
    assert result.stdout == ""
    assert result.stderr == helped.stdout
    assert result.stderr.count("\n") > 5


def _no_install(*args: Any, **kwargs: Any) -> None:
    raise cli.CliError("constructed: no install is looked for")


def test_a_click_error_as_the_root_context_closes_is_escaped_constructed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The root context closes in typer's main loop, outside the root
    command's `invoke`: a click error raised then (by a resource `main()`
    registered with `ctx.with_resource`) is printed by `_click_error` too."""

    @contextlib.contextmanager
    def fails_on_exit() -> Iterator[None]:
        try:
            yield
        finally:
            raise typer.BadParameter(MESSAGE)

    monkeypatch.setattr(cli, "_library_log_on_stderr", fails_on_exit)
    monkeypatch.setattr(cli, "_discover", _no_install)
    result = runner.invoke(cli.app, ["install", "show"])
    assert result.exit_code == cli.EXIT_USAGE, (result.stdout, result.stderr, result.exception)
    assert result.stdout == ""
    assert _lines_are_safe(result.stderr), _unsafe(result.stderr)
    assert result.stderr.split("\n") == [
        "wowlab: constructed: no install is looked for",
        f"Error: Invalid value: {SHOWN}",
        "",
    ]


# ─── _log.exception ──────────────────────────────────────────────────────────


@contextlib.contextmanager
def _library_log(capsys: pytest.CaptureFixture[str]) -> Iterator[None]:
    capsys.readouterr()
    library = logging.getLogger("wowlab_core")
    before = (list(library.handlers), library.propagate)
    with cli._library_log_on_stderr():
        yield
    assert (list(library.handlers), library.propagate) == before


def test_a_logged_traceback_prints_line_by_line_escaped_constructed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`guard` logs with `_log.exception` when it cannot record a rollback;
    the record's message is one line, and the traceback it carries follows
    line by line, as an uncaught exception's is printed."""
    with _library_log(capsys):
        try:
            raise OSError(MESSAGE)
        except OSError:
            guard._log.exception("could not record the rollback of %s", MESSAGE)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert _lines_are_safe(captured.err), _unsafe(captured.err)
    assert _no_spoofed_line(captured.err), captured.err
    lines = captured.err.split("\n")
    assert lines[:2] == [f"wowlab: could not record the rollback of {SHOWN}", TRACEBACK]
    assert lines[-2:] == [f"OSError: {SHOWN}", ""]


def test_a_logged_stack_prints_line_by_line_escaped_constructed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with _library_log(capsys):
        guard._log.warning("leftover temp file left in place: %s", MESSAGE, stack_info=True)
    captured = capsys.readouterr()
    assert _lines_are_safe(captured.err), _unsafe(captured.err)
    lines = captured.err.split("\n")
    assert lines[:2] == [
        f"wowlab: leftover temp file left in place: {SHOWN}",
        "Stack (most recent call last):",
    ]


def test_a_log_record_without_a_traceback_is_one_line_constructed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with _library_log(capsys):
        guard._log.error("rollback of %r did not finish: %s", "x", MESSAGE)
    assert capsys.readouterr().err == f"wowlab: rollback of 'x' did not finish: {SHOWN}\n"


def _logged(capsys: pytest.CaptureFixture[str], exc: BaseException) -> list[str]:
    """What `guard._log.exception` prints for `exc` while a command runs:
    every line escaped, and never logging's own error report, which would
    print the record raw."""
    with _library_log(capsys):
        try:
            raise exc
        except BaseException:
            guard._log.exception("could not record the rollback of %s", MESSAGE)
    captured = capsys.readouterr()
    assert captured.out == ""
    assert _lines_are_safe(captured.err), _unsafe(captured.err)
    assert _no_spoofed_line(captured.err), captured.err
    assert "Logging error" not in captured.err
    lines = captured.err.split("\n")
    assert lines[0] == f"wowlab: could not record the rollback of {SHOWN}"
    assert lines[-1] == ""
    return lines[:-1]


class _HostileNotesError(Exception):
    """Reading its notes raises, with the hostile text."""

    @property
    def __notes__(self) -> list[str]:  # type: ignore[override]
        raise RuntimeError(MESSAGE)


def test_a_logged_exception_whose_notes_raise_is_escaped_constructed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Python's words for notes that cannot be read, on one escaped line."""
    lines = _logged(capsys, _HostileNotesError(MESSAGE))
    assert lines[-2:] == [
        f"{__name__}._HostileNotesError: {SHOWN}",
        f"Ignored error getting __notes__: {RuntimeError(MESSAGE)!r}",
    ]


def test_a_logged_deep_exception_group_is_bounded_and_escaped_constructed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A group 5,000 deep prints 10 levels, not a RecursionError that
    logging would report with the record raw."""
    deep: BaseException = ValueError(MESSAGE)
    for _ in range(5_000):
        deep = ExceptionGroup(MESSAGE, [deep])
    lines = _logged(capsys, deep)
    assert len(lines) < 100
    assert lines[-1].strip() == "... (max_group_depth is 10)"


def test_a_logged_traceback_that_cannot_be_rendered_is_one_line_constructed(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The formatter's own fallback: the record's message, then one line
    naming the type in place of the traceback (not the handler's
    "could not be printed" line for the whole record)."""

    def broken(exc: BaseException, *args: Any) -> list[str]:
        raise RuntimeError(MESSAGE)

    monkeypatch.setattr(cli, "_traceback_lines", broken)
    assert _logged(capsys, OSError(MESSAGE)) == [
        f"wowlab: could not record the rollback of {SHOWN}",
        "wowlab: OSError: its traceback could not be printed",
    ]


def test_a_record_that_cannot_be_formatted_is_one_escaped_line_constructed(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A record whose formatting fails reaches the handler's `handleError`,
    which prints one line naming it instead of logging's report (which
    prints the record's message, arguments and exception raw)."""
    with _library_log(capsys):
        guard._log.error("%s and %s", MESSAGE)  # one argument for two fields
    assert capsys.readouterr().err == (
        "wowlab: a log record could not be printed (ERROR, wowlab_core.guard)\n"
    )
