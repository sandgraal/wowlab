"""The one base of wowlab's own errors (M16-22).

Every error a module raises for the CLI to report subclasses `WowlabError`,
which carries the exit code the command ends with. The CLI's `_handled`
catches the base, so a new module error is reported as one line, not a
traceback, without a new entry in the CLI. The write gate's and the
SavedVariables parser's errors (`guard.GuardError`, `luadata.LuaDataError`)
keep their own bases, and the CLI names them beside this one.

This module imports nothing from wowlab, so every module can import it.
"""

from __future__ import annotations

# The exit code of a command that failed (the CLI's `EXIT_ERROR`).
EXIT_ERROR = 1


class WowlabError(Exception):
    """An error wowlab reports as one line, and the exit code it ends with."""

    exit_code: int = EXIT_ERROR
