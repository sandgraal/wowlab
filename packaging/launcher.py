"""The entry point of the frozen `wowlab` builds (ADR-0038 item 3, M17-08).

PyInstaller freezes this file, not `wowlab_core.cli`, so that a double-click
does something useful. It is the ordinary `wowlab` command with three
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

No network access, no update check, nothing that runs at login. The build
reads and writes exactly what the wheel does.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable, Sequence

# The prefix `wowlab_core.ui` starts its runs with after `sys.executable`.
UI_RUN_PREFIX = ("-P", "-m", "wowlab_core")

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
    if tuple(args[: len(UI_RUN_PREFIX)]) == UI_RUN_PREFIX:
        return ("run", args[len(UI_RUN_PREFIX) :])
    if args:
        return ("run", args)
    if platform == "darwin" and not attached:
        return ("terminal", [OPEN, "-a", "Terminal", executable])
    return ("run", ["ui"])


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
        from wowlab_core.cli import app

        app(args=args, prog_name="wowlab")
        return 0
    run(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
