"""The entry point of the frozen `wowlab` builds (ADR-0038 item 3, M17-08,
M17-09).

PyInstaller freezes this file, not `wowlab_core.cli`, so that a double-click
does something useful. It is the ordinary `wowlab` command with five
differences, and nothing else:

1. **No arguments means `ui`.** The `wowlab` command prints its help and
   exits when it gets none; a double-click gives none, so here it runs
   `wowlab ui`. Any arguments are passed through unchanged, so
   `wowlab --version` and every other command work from a terminal as they
   do from the wheel.
2. **macOS, no terminal: hand off to Terminal.** A double-click in Finder
   starts the app with no arguments and no terminal attached. The launcher
   then runs `open -a Terminal` on its own executable (a plain command, no
   AppleScript, so no Automation permission prompt) and exits. Terminal
   starts it again with a terminal attached, where rule 1 runs `wowlab ui`:
   the server is visible, Ctrl-C or closing the window stops it, and it
   never opens a second window.
3. **The UI's own runs.** `wowlab ui` runs each command as
   `[sys.executable, "-P", "-m", "wowlab_core", ...]`. In a frozen build
   `sys.executable` is this launcher, so that exact prefix is taken off and
   the rest runs as the command it names. Nothing else that looks like an
   interpreter option is interpreted.
4. **macOS `-psn_…`.** A leading process serial number argument, which
   LaunchServices can add on a Finder launch, is dropped before rules 1 to 3.
5. **`PYTHONIOENCODING`, frozen builds only.** A frozen interpreter starts
   isolated and ignores every `PYTHON*` variable. `wowlab ui` sets
   `PYTHONIOENCODING=utf-8` for each run it starts and reads the run's output
   as UTF-8; on Windows a piped stream's own encoding is the ANSI code page,
   so the launcher applies the variable to the standard streams itself, as
   the interpreter does for the wheel.

On Windows a double-click (no arguments) runs `wowlab ui` under rule 1 in the
console window Windows opens for it: the window shows the server, and
closing it or pressing Ctrl-C stops it (M17-09).

No network access, no update check, nothing that runs at login. The build
reads and writes exactly what the wheel does.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any

# The prefix `wowlab_core.ui` starts its runs with after `sys.executable`.
UI_RUN_PREFIX = ("-P", "-m", "wowlab_core")

# The process serial number argument LaunchServices may add on macOS.
PSN_PREFIX = "-psn_"

# The one program the hand-off runs, by absolute path (never a PATH lookup).
OPEN = "/usr/bin/open"


def _attached() -> bool:
    """A terminal is attached when standard input or output is one."""
    for stream in (sys.stdin, sys.stdout):
        try:
            if stream is not None and stream.isatty():
                return True
        except (OSError, ValueError):
            continue
    return False


def plan(
    argv: Sequence[str], *, platform: str, attached: bool, executable: str
) -> tuple[str, list[str]]:
    """What to do with `argv` (the arguments after the program name).

    `("terminal", command)`: run `command` and exit (the macOS hand-off).
    `("run", args)`: run the `wowlab` command with `args`."""
    args = list(argv)
    # LaunchServices can start a Finder-opened app with a process serial
    # number, `-psn_0_NNN`, as its first argument. The console bootloader
    # passes it on, so it is dropped here (macOS only) and what is left is
    # read as usual: nothing left is a double-click.
    if platform == "darwin" and args and args[0].startswith(PSN_PREFIX):
        args = args[1:]
    if tuple(args[: len(UI_RUN_PREFIX)]) == UI_RUN_PREFIX:
        return ("run", args[len(UI_RUN_PREFIX) :])
    if args:
        return ("run", args)
    if platform == "darwin" and not attached:
        return ("terminal", [OPEN, "-a", "Terminal", executable])
    return ("run", ["ui"])


def apply_io_encoding(environ: Mapping[str, str], streams: Iterable[tuple[str, Any]]) -> None:
    """Rule 5: reconfigure each `(name, stream)` as `PYTHONIOENCODING` asks,
    as the interpreter does at start (`encoding[:errors]`; standard error
    keeps its own error handler). A stream that cannot be reconfigured, or an
    encoding Python does not know, is left as it is."""
    encoding, _, errors = environ.get("PYTHONIOENCODING", "").partition(":")
    for name, stream in streams:
        options: dict[str, str] = {}
        if encoding:
            options["encoding"] = encoding
        if errors and name != "stderr":
            options["errors"] = errors
        reconfigure = getattr(stream, "reconfigure", None)
        if not options or reconfigure is None:
            continue
        try:
            reconfigure(**options)
        except (LookupError, ValueError, OSError):
            continue


def main(
    argv: Sequence[str] | None = None,
    *,
    run: Callable[[list[str]], object] | None = None,
    spawn: Callable[[list[str]], int] | None = None,
) -> int:
    """Run the launcher; `run` and `spawn` are replaced only by tests."""
    action, args = plan(
        sys.argv[1:] if argv is None else argv,
        platform=sys.platform,
        attached=_attached(),
        executable=sys.executable,
    )
    if action == "terminal":
        if spawn is None:
            return subprocess.run(args, check=False).returncode
        return spawn(args)
    if run is None:
        if getattr(sys, "frozen", False):
            apply_io_encoding(
                os.environ,
                (("stdin", sys.stdin), ("stdout", sys.stdout), ("stderr", sys.stderr)),
            )
        from wowlab_core.cli import app

        app(args=args, prog_name="wowlab")
        return 0
    run(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
