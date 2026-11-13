"""The `wowlab` command (docs/LAB_PLAN.md §6.11, M10-14).

A thin shell over the library. Every command reads through the modules the
spec names and prints text, or JSON with `--json`; the only commands that
change an install are `snap restore`, `profile apply` (§13.3, M11-08),
`addon install|remove lab` (§13.1, M11-02), `sv merge` (§13.4, M11-09) and `undo`, and all go
through `wowlab_core.guard` (L2, ADR-0021): they print
the plan and ask before writing unless `--yes` is given. Nothing here writes a file itself; the
snapshot store, the game-data cache and saved looks are written by `snapshot`,
`gamedata` and `lookstore`, under the user data directory (L1).

`doctor` on a first run (the store holds no snapshot of the install found)
says what wowlab will and will not do, offers a first snapshot into the
store and verifies it (`firstrun`, §15.2, M13-04): it asks only when input
is a terminal, `--yes` accepts, and `--json` never asks.

Exit codes: 0 ok, 1 error, 2 usage (including "more than one flavor, pick
one with --flavor"), 3 refused by the write gate (any `guard.GuardError`:
client running or unknown, a path outside the allowlist, the store busy, a
file changed since the pre-write snapshot, a snapshot of another install).
`sv merge` also exits 1 when conflicts are left unresolved (its report is
printed, nothing is written), 2 for a character-to-character merge of an
account-wide file, and 3 when its SavedVariables loader check refuses.

Where the install is: `--root`, else `$WOWLAB_WOW_ROOT`, else the platform
defaults (`install.discover`). Which flavor: `--flavor`, else
`$WOWLAB_FLAVOR`, implied when the install has exactly one. With several and
none named, `char list` lists every flavor and the other `char` commands use
the only flavor with characters; nothing that writes picks one (M16-114).
Nothing about a flavor is known here (L6): folder names, products and
versions come from discovery.

JSON: each data command's output validates against the Pydantic model named
in its docstring after a form feed, which `--help` does not print and the
generated command reference does (the models are defined below, or are the
library's own); its help ends with one shared footer instead (M16-15). Text
that came from file names or file bytes and is not valid UTF-8 is written in
JSON with the scheme `layout` and `snapshot` use (a lone surrogate becomes
NUL followed by four hex digits); snapshot manifests are written with
`snapshot.manifest_bytes`, the one canonical encoding. `sv dump --json` is
flat (each value one row naming its parent) and written compact, row by
row, so its size grows with the number of values and never with depth.
Text output escapes every control character, tab and line feed included
(`\x1b` for ESC), and every Unicode format and separator character (Cf,
Zl, Zp, such as U+202E and U+2028, as their UTF-8 bytes), so nothing read
from an install reaches the terminal as a control sequence or reorders or
breaks a line (M11-30, §6.11 amendment). So does the traceback of an
exception nothing caught, which the console script prints line by line with
its message and each note on one line (`_excepthook`), and a click usage
error, whose `Error:` line can hold the user's own arguments (`_click_error`,
M11-33).

A whole-snapshot `snap restore` leaves alone the files the client manages
(file-map Edit `no`) unless they are named with `--paths` (owner decision
2026-09-27, §6.11 amendment).

`log tail` (§6.11, M10-13) prints the last lines of the newest combat log
under the flavor's `Logs/`, tokenized by `combatlog`, and with `--follow`
keeps printing as the client appends, across truncation and a new log file.
Its `--json` is one `LogTailReport`, or with `--follow` one `LogTailLine` per
line of output (JSON Lines), since a stream that never ends is not one
document. Timestamps are printed as the log has them; they are local times
the client wrote, and in the committed fixtures shifted, never parsed here.

`looks` (§13.2, M11-06) reads the customization tables of one build through
`gamedata` and checks looks with `wowlab_core.looks`; saved looks are JSON
files `lookstore` writes under the user data directory, never in an install.
The build is `--build` or the flavor's version; only when no install is found
does it fall back to a saved look's build or the one fully cached build.
`looks page` (M11-07, ADR-0027) writes all of that into one self-contained
HTML file (`lookspage`), under the user data directory or at `--out`, never in
an install: every verdict on the page is computed here and only shown there.

`explain` (M13-23, §15.6) also reports a path's size and modification time,
the latest snapshot holding it and whether the live file differs (only that
one file is hashed), the read commands that decode it (`views`, argv from
`_VIEWS`, keyed by file-map entry id), and copyable command text (`hints`)
that it never runs. A link, or a path inside one, gets no stat, no snapshot
facts, no views and no restore hint; a folder gets no restore hint.

`char show` (§13.1, M11-04) reads one character's `WowLab.lua`, written by
the lab-addon, through `wowlab_core.labaddon`, and the account's
`WowLab.lua` beside it; read only. Without `--character` it takes the
character whose `WowLab.lua` has the newest modification time. `char list`
(§14.4, M12-09) lists every character folder's `WowLab.lua` through
`labaddon.survey`, in every account unless `--account` names one; a file it
cannot read, and a file or folder it could not look at (a link, a FIFO, a
folder it cannot list), is listed or noted with the reason, named on stderr,
and makes the command exit 1 after the others are printed (with `--account`
too, when the account folder itself could not be looked at). Both commands
choose a character's file by `labaddon.choose_lab_file`, but `char show`
(and `looks import-char`) give it regular files only, so for a link, FIFO or
folder named like the file, or a SavedVariables/ folder that cannot be
listed, they can still answer differently: there `char list` says it could
not look, and `char show` answers as if the character had no `WowLab.lua`.

The package (M16-111): `_common` holds the app, its command groups and root
callback, options, output, error handling and the helpers more than one
command uses; each `_cmd_<group>` module holds one command group (`_cmd_restore`
holds `snap restore` and `undo`, `_changes` the plan and diff lines they and
`snap diff` print). Every name is re-exported here, so `wowlab_core.cli` stays
the one import path. A name set on this package (a test's `monkeypatch`) is set
in every module that holds the same object too, so it takes effect as it did
when all of this was one module (`_Package`).
"""

import base64 as base64
import contextlib as contextlib
import csv as csv
import datetime as datetime
import errno as errno
import functools as functools
import hashlib as hashlib
import io  # noqa: F401 - re-exported, as when cli was one module
import json as json
import logging as logging
import os  # noqa: F401 - re-exported, as when cli was one module
import re as re
import shlex as shlex
import shutil  # noqa: F401 - re-exported, as when cli was one module
import signal as signal
import stat as stat
import sys as sys
import textwrap as textwrap
import time as time
import traceback as traceback
import types
import unicodedata as unicodedata
from collections.abc import Callable as Callable
from collections.abc import Iterator as Iterator
from collections.abc import Sequence as Sequence
from contextlib import contextmanager as contextmanager
from dataclasses import dataclass as dataclass
from pathlib import Path as Path
from pathlib import PurePath as PurePath
from types import TracebackType as TracebackType
from typing import Annotated as Annotated
from typing import Any as Any
from typing import Literal as Literal
from typing import NamedTuple as NamedTuple
from typing import NoReturn as NoReturn
from typing import override as override

import typer as typer
from pydantic import BaseModel as BaseModel
from pydantic import ConfigDict as ConfigDict
from pydantic import Field as Field
from pydantic import RootModel as RootModel
from pydantic import ValidationError as ValidationError
from typer._click import Context as Context
from typer._click.exceptions import ClickException as ClickException
from typer._click.exceptions import NoArgsIsHelpError as NoArgsIsHelpError
from typer._click.exceptions import UsageError as UsageError
from typer._click.globals import get_current_context as get_current_context
from typer.core import TyperGroup as TyperGroup

from wowlab_core import __version__ as __version__
from wowlab_core import addoncheck as addoncheck
from wowlab_core import addoninstall as addoninstall
from wowlab_core import bindscoach as bindscoach
from wowlab_core import combatlog as combatlog
from wowlab_core import doctorredact as doctorredact
from wowlab_core import dressing as dressing
from wowlab_core import filemap as filemap
from wowlab_core import firstrun as firstrun
from wowlab_core import gamedata as gamedata
from wowlab_core import gaps as gaps
from wowlab_core import gear as gear
from wowlab_core import guard as guard
from wowlab_core import install as install
from wowlab_core import itemlink as itemlink
from wowlab_core import keymap as keymap
from wowlab_core import labaddon as labaddon
from wowlab_core import layout as layout
from wowlab_core import longpath as longpath
from wowlab_core import looks as looks
from wowlab_core import lookspage as lookspage
from wowlab_core import lookstore as lookstore
from wowlab_core import luadata as luadata
from wowlab_core import macrocheck as macrocheck
from wowlab_core import macroplace as macroplace
from wowlab_core import pathfacts as pathfacts
from wowlab_core import process as process
from wowlab_core import profiles as profiles
from wowlab_core import snaparchive as snaparchive
from wowlab_core import snapshot as snapshot
from wowlab_core import svmerge as svmerge
from wowlab_core import wtfconfig as wtfconfig
from wowlab_core import wtfdiff as wtfdiff
from wowlab_core.errors import WowlabError as WowlabError
from wowlab_core.filemap import FileMapEntry as FileMapEntry
from wowlab_core.verdict import WOWLAB_TEMP_ROW as WOWLAB_TEMP_ROW
from wowlab_core.verdict import Verdict as Verdict
from wowlab_core.verdict import gate_names_but_for_executable as gate_names_but_for_executable
from wowlab_core.verdict import gate_refuses_stat as gate_refuses_stat
from wowlab_core.verdict import has_executable_part as has_executable_part
from wowlab_core.verdict import tier_words as tier_words
from wowlab_core.verdict import verdict_of as verdict_of

# The modules run in this order, which is the order every command is
# registered in, so Typer lists them as before: doctor, tree and explain,
# undo, ui at the root; snap restore after the other snap commands. Each
# module imports from the modules above it only.
# isort: off
from wowlab_core.cli import _common
from wowlab_core.cli import _cmd_log
from wowlab_core.cli import _cmd_doctor
from wowlab_core.cli import _cmd_install
from wowlab_core.cli import _cmd_filemap
from wowlab_core.cli import _cmd_sv
from wowlab_core.cli import _cmd_cvar
from wowlab_core.cli import _cmd_binds
from wowlab_core.cli import _cmd_macros
from wowlab_core.cli import _cmd_addons
from wowlab_core.cli import _cmd_db2
from wowlab_core.cli import _changes
from wowlab_core.cli import _cmd_snap
from wowlab_core.cli import _cmd_restore
from wowlab_core.cli import _cmd_profile
from wowlab_core.cli import _cmd_addon
from wowlab_core.cli import _cmd_char
from wowlab_core.cli import _cmd_looks
from wowlab_core.cli import _cmd_ui
# isort: on

from wowlab_core.cli._changes import (
    _CHANGED_AFTER_ROLLBACK as _CHANGED_AFTER_ROLLBACK,
)
from wowlab_core.cli._changes import (
    _CHANGED_DURING as _CHANGED_DURING,
)
from wowlab_core.cli._changes import (
    _CHANGED_LINE as _CHANGED_LINE,
)
from wowlab_core.cli._changes import (
    _CHANGED_SINCE as _CHANGED_SINCE,
)
from wowlab_core.cli._changes import (
    _CHANGED_UNFINISHED as _CHANGED_UNFINISHED,
)
from wowlab_core.cli._changes import (
    _CHANGED_UNKNOWN_STATE as _CHANGED_UNKNOWN_STATE,
)
from wowlab_core.cli._changes import (
    _COMPARE as _COMPARE,
)
from wowlab_core.cli._changes import (
    _DETAIL_INDENT as _DETAIL_INDENT,
)
from wowlab_core.cli._changes import (
    _DETAIL_PLAN_LIMIT as _DETAIL_PLAN_LIMIT,
)
from wowlab_core.cli._changes import (
    _DETAIL_READ_LIMIT as _DETAIL_READ_LIMIT,
)
from wowlab_core.cli._changes import (
    _EXECUTABLE_ROW as _EXECUTABLE_ROW,
)
from wowlab_core.cli._changes import (
    _KEEPS_A_COPY as _KEEPS_A_COPY,
)
from wowlab_core.cli._changes import (
    _NO_CVAR_LINE as _NO_CVAR_LINE,
)
from wowlab_core.cli._changes import (
    _NO_KEY_LINE as _NO_KEY_LINE,
)
from wowlab_core.cli._changes import (
    _NOT_RESTORED_SHOWN as _NOT_RESTORED_SHOWN,
)
from wowlab_core.cli._changes import (
    _NOTHING_TO_PUT_BACK as _NOTHING_TO_PUT_BACK,
)
from wowlab_core.cli._changes import (
    _NOTHING_TO_PUT_BACK_UNFINISHED as _NOTHING_TO_PUT_BACK_UNFINISHED,
)
from wowlab_core.cli._changes import (
    _OTHER_LINES as _OTHER_LINES,
)
from wowlab_core.cli._changes import (
    _RELOGIN as _RELOGIN,
)
from wowlab_core.cli._changes import (
    _RESTART as _RESTART,
)
from wowlab_core.cli._changes import (
    _RESTART_RELOGIN as _RESTART_RELOGIN,
)
from wowlab_core.cli._changes import (
    _SCOPE_LINE as _SCOPE_LINE,
)
from wowlab_core.cli._changes import (
    _SCREENSHOTS_ROW as _SCREENSHOTS_ROW,
)
from wowlab_core.cli._changes import (
    _STOPPED_BEFORE_ANY_FILE as _STOPPED_BEFORE_ANY_FILE,
)
from wowlab_core.cli._changes import (
    _UNREAD as _UNREAD,
)
from wowlab_core.cli._changes import (
    _VALUE_WIDTH as _VALUE_WIDTH,
)
from wowlab_core.cli._changes import (
    AccountMapped as AccountMapped,
)
from wowlab_core.cli._changes import (
    IntoReport as IntoReport,
)
from wowlab_core.cli._changes import (
    NotRestoredPath as NotRestoredPath,
)
from wowlab_core.cli._changes import (
    RestoreReport as RestoreReport,
)
from wowlab_core.cli._changes import (
    SkippedPath as SkippedPath,
)
from wowlab_core.cli._changes import (
    UndoReport as UndoReport,
)
from wowlab_core.cli._changes import (
    _added_since as _added_since,
)
from wowlab_core.cli._changes import (
    _changed_executables as _changed_executables,
)
from wowlab_core.cli._changes import (
    _client_managed as _client_managed,
)
from wowlab_core.cli._changes import (
    _detail_lines as _detail_lines,
)
from wowlab_core.cli._changes import (
    _disk_now as _disk_now,
)
from wowlab_core.cli._changes import (
    _executable_note as _executable_note,
)
from wowlab_core.cli._changes import (
    _is_reparse as _is_reparse,
)
from wowlab_core.cli._changes import (
    _is_saved_variables as _is_saved_variables,
)
from wowlab_core.cli._changes import (
    _leaves as _leaves,
)
from wowlab_core.cli._changes import (
    _left_alone_lines as _left_alone_lines,
)
from wowlab_core.cli._changes import (
    _linked_side as _linked_side,
)
from wowlab_core.cli._changes import (
    _macro_label as _macro_label,
)
from wowlab_core.cli._changes import (
    _named_line as _named_line,
)
from wowlab_core.cli._changes import (
    _names_for_whole as _names_for_whole,
)
from wowlab_core.cli._changes import (
    _not_restored_lines as _not_restored_lines,
)
from wowlab_core.cli._changes import (
    _on_disk as _on_disk,
)
from wowlab_core.cli._changes import (
    _ordinal as _ordinal,
)
from wowlab_core.cli._changes import (
    _plainly_changed as _plainly_changed,
)
from wowlab_core.cli._changes import (
    _plainly_on_disk as _plainly_on_disk,
)
from wowlab_core.cli._changes import (
    _plan_footer as _plan_footer,
)
from wowlab_core.cli._changes import (
    _plan_item_lines as _plan_item_lines,
)
from wowlab_core.cli._changes import (
    _read_detail as _read_detail,
)
from wowlab_core.cli._changes import (
    _relogin_notes as _relogin_notes,
)
from wowlab_core.cli._changes import (
    _screenshot_note as _screenshot_note,
)
from wowlab_core.cli._changes import (
    _screenshot_row as _screenshot_row,
)
from wowlab_core.cli._changes import (
    _set_aside as _set_aside,
)
from wowlab_core.cli._changes import (
    _Side as _Side,
)
from wowlab_core.cli._changes import (
    _size_text as _size_text,
)
from wowlab_core.cli._changes import (
    _skipped_lines as _skipped_lines,
)
from wowlab_core.cli._changes import (
    _stored as _stored,
)
from wowlab_core.cli._changes import (
    _sv_lines as _sv_lines,
)
from wowlab_core.cli._changes import (
    _sv_summary as _sv_summary,
)
from wowlab_core.cli._changes import (
    _unchanged as _unchanged,
)
from wowlab_core.cli._changes import (
    _Unread as _Unread,
)
from wowlab_core.cli._changes import (
    _value_text as _value_text,
)
from wowlab_core.cli._changes import (
    _wants_detail as _wants_detail,
)
from wowlab_core.cli._changes import (
    _wtf_lines as _wtf_lines,
)
from wowlab_core.cli._cmd_addon import (
    AddonArg as AddonArg,
)
from wowlab_core.cli._cmd_addon import (
    AddonInstallReport as AddonInstallReport,
)
from wowlab_core.cli._cmd_addon import (
    AddonRemoveReport as AddonRemoveReport,
)
from wowlab_core.cli._cmd_addon import (
    DryRunOpt as DryRunOpt,
)
from wowlab_core.cli._cmd_addon import (
    _lab_addon as _lab_addon,
)
from wowlab_core.cli._cmd_addon import (
    _print_left as _print_left,
)
from wowlab_core.cli._cmd_addon import (
    addon_install as addon_install,
)
from wowlab_core.cli._cmd_addon import (
    addon_remove as addon_remove,
)
from wowlab_core.cli._cmd_addons import (
    _CHECK_BLIZZARD as _CHECK_BLIZZARD,
)
from wowlab_core.cli._cmd_addons import (
    _CHECK_DEFAULT as _CHECK_DEFAULT,
)
from wowlab_core.cli._cmd_addons import (
    _CHECK_DEPS as _CHECK_DEPS,
)
from wowlab_core.cli._cmd_addons import (
    _CHECK_OUT_OF_DATE as _CHECK_OUT_OF_DATE,
)
from wowlab_core.cli._cmd_addons import (
    _UI_ESCAPE as _UI_ESCAPE,
)
from wowlab_core.cli._cmd_addons import (
    AddonsCheckReport as AddonsCheckReport,
)
from wowlab_core.cli._cmd_addons import (
    AddonsReport as AddonsReport,
)
from wowlab_core.cli._cmd_addons import (
    _check_caveat as _check_caveat,
)
from wowlab_core.cli._cmd_addons import (
    _check_characters as _check_characters,
)
from wowlab_core.cli._cmd_addons import (
    _check_interface as _check_interface,
)
from wowlab_core.cli._cmd_addons import (
    _check_toc_lines as _check_toc_lines,
)
from wowlab_core.cli._cmd_addons import (
    _TocRole as _TocRole,
)
from wowlab_core.cli._cmd_addons import (
    _ui_text as _ui_text,
)
from wowlab_core.cli._cmd_addons import (
    addons_check as addons_check,
)
from wowlab_core.cli._cmd_addons import (
    addons_list as addons_list,
)
from wowlab_core.cli._cmd_binds import (
    _COACH_LOG_BUDGET as _COACH_LOG_BUDGET,
)
from wowlab_core.cli._cmd_binds import (
    _COACH_LOG_SCAN as _COACH_LOG_SCAN,
)
from wowlab_core.cli._cmd_binds import (
    _COACH_NOTES as _COACH_NOTES,
)
from wowlab_core.cli._cmd_binds import (
    _COMBATLOG_ON as _COMBATLOG_ON,
)
from wowlab_core.cli._cmd_binds import (
    _SHORT_REASON as _SHORT_REASON,
)
from wowlab_core.cli._cmd_binds import (
    _SPAN_TIME as _SPAN_TIME,
)
from wowlab_core.cli._cmd_binds import (
    BindItem as BindItem,
)
from wowlab_core.cli._cmd_binds import (
    BindsCoachReport as BindsCoachReport,
)
from wowlab_core.cli._cmd_binds import (
    BindsReport as BindsReport,
)
from wowlab_core.cli._cmd_binds import (
    CasterMatch as CasterMatch,
)
from wowlab_core.cli._cmd_binds import (
    CoachLog as CoachLog,
)
from wowlab_core.cli._cmd_binds import (
    Folder as Folder,
)
from wowlab_core.cli._cmd_binds import (
    LogNameOpt as LogNameOpt,
)
from wowlab_core.cli._cmd_binds import (
    _bindings_note as _bindings_note,
)
from wowlab_core.cli._cmd_binds import (
    _caster_match as _caster_match,
)
from wowlab_core.cli._cmd_binds import (
    _clock as _clock,
)
from wowlab_core.cli._cmd_binds import (
    _coach_because as _coach_because,
)
from wowlab_core.cli._cmd_binds import (
    _coach_default_record as _coach_default_record,
)
from wowlab_core.cli._cmd_binds import (
    _coach_log as _coach_log,
)
from wowlab_core.cli._cmd_binds import (
    _coach_pick_log as _coach_pick_log,
)
from wowlab_core.cli._cmd_binds import (
    _coach_record as _coach_record,
)
from wowlab_core.cli._cmd_binds import (
    _CoachRanked as _CoachRanked,
)
from wowlab_core.cli._cmd_binds import (
    _first_name as _first_name,
)
from wowlab_core.cli._cmd_binds import (
    _key_words as _key_words,
)
from wowlab_core.cli._cmd_binds import (
    _namesakes as _namesakes,
)
from wowlab_core.cli._cmd_binds import (
    _read_counts as _read_counts,
)
from wowlab_core.cli._cmd_binds import (
    _say_coach as _say_coach,
)
from wowlab_core.cli._cmd_binds import (
    _scan_note as _scan_note,
)
from wowlab_core.cli._cmd_binds import (
    _shared_warning as _shared_warning,
)
from wowlab_core.cli._cmd_binds import (
    _short_reason as _short_reason,
)
from wowlab_core.cli._cmd_binds import (
    binds_coach as binds_coach,
)
from wowlab_core.cli._cmd_binds import (
    binds_list as binds_list,
)
from wowlab_core.cli._cmd_char import (
    _AGREEMENTS as _AGREEMENTS,
)
from wowlab_core.cli._cmd_char import (
    _BARE_NOTE as _BARE_NOTE,
)
from wowlab_core.cli._cmd_char import (
    _CHAR_LIST_NOTES as _CHAR_LIST_NOTES,
)
from wowlab_core.cli._cmd_char import (
    _CHAR_NOTES as _CHAR_NOTES,
)
from wowlab_core.cli._cmd_char import (
    _FIRST_NAME_FOLDER as _FIRST_NAME_FOLDER,
)
from wowlab_core.cli._cmd_char import (
    _FIX_COMMAND as _FIX_COMMAND,
)
from wowlab_core.cli._cmd_char import (
    _GEAR_LEVEL_NOTE as _GEAR_LEVEL_NOTE,
)
from wowlab_core.cli._cmd_char import (
    _GEAR_NAMES_NOTE as _GEAR_NAMES_NOTE,
)
from wowlab_core.cli._cmd_char import (
    _GEAR_OVERALL_NOTE as _GEAR_OVERALL_NOTE,
)
from wowlab_core.cli._cmd_char import (
    _GEAR_SPEC_NOTE as _GEAR_SPEC_NOTE,
)
from wowlab_core.cli._cmd_char import (
    _HISTORY_FEW as _HISTORY_FEW,
)
from wowlab_core.cli._cmd_char import (
    _HISTORY_NOTES as _HISTORY_NOTES,
)
from wowlab_core.cli._cmd_char import (
    _INSTALL_ADVICE as _INSTALL_ADVICE,
)
from wowlab_core.cli._cmd_char import (
    _NAME_TABLES as _NAME_TABLES,
)
from wowlab_core.cli._cmd_char import (
    _SUMMARY_ADVICE as _SUMMARY_ADVICE,
)
from wowlab_core.cli._cmd_char import (
    _SUMMARY_NOTES as _SUMMARY_NOTES,
)
from wowlab_core.cli._cmd_char import (
    CharBindsFile as CharBindsFile,
)
from wowlab_core.cli._cmd_char import (
    CharBindsReport as CharBindsReport,
)
from wowlab_core.cli._cmd_char import (
    CharGearReport as CharGearReport,
)
from wowlab_core.cli._cmd_char import (
    CharHistoryReport as CharHistoryReport,
)
from wowlab_core.cli._cmd_char import (
    CharListFlavor as CharListFlavor,
)
from wowlab_core.cli._cmd_char import (
    CharListReport as CharListReport,
)
from wowlab_core.cli._cmd_char import (
    CharMacro as CharMacro,
)
from wowlab_core.cli._cmd_char import (
    CharMacrosFile as CharMacrosFile,
)
from wowlab_core.cli._cmd_char import (
    CharMacrosReport as CharMacrosReport,
)
from wowlab_core.cli._cmd_char import (
    CharNames as CharNames,
)
from wowlab_core.cli._cmd_char import (
    CharShowReport as CharShowReport,
)
from wowlab_core.cli._cmd_char import (
    CharSummaryProfession as CharSummaryProfession,
)
from wowlab_core.cli._cmd_char import (
    CharSummaryReport as CharSummaryReport,
)
from wowlab_core.cli._cmd_char import (
    CharSummaryRow as CharSummaryRow,
)
from wowlab_core.cli._cmd_char import (
    _bare_words as _bare_words,
)
from wowlab_core.cli._cmd_char import (
    _binds_left_out_note as _binds_left_out_note,
)
from wowlab_core.cli._cmd_char import (
    _cached_name as _cached_name,
)
from wowlab_core.cli._cmd_char import (
    _char_binds_file as _char_binds_file,
)
from wowlab_core.cli._cmd_char import (
    _char_list_flavor as _char_list_flavor,
)
from wowlab_core.cli._cmd_char import (
    _char_list_rows as _char_list_rows,
)
from wowlab_core.cli._cmd_char import (
    _char_macros_file as _char_macros_file,
)
from wowlab_core.cli._cmd_char import (
    _char_names as _char_names,
)
from wowlab_core.cli._cmd_char import (
    _currency_value as _currency_value,
)
from wowlab_core.cli._cmd_char import (
    _file_note as _file_note,
)
from wowlab_core.cli._cmd_char import (
    _gear_change_words as _gear_change_words,
)
from wowlab_core.cli._cmd_char import (
    _gear_line as _gear_line,
)
from wowlab_core.cli._cmd_char import (
    _gear_table_note as _gear_table_note,
)
from wowlab_core.cli._cmd_char import (
    _header_lines as _header_lines,
)
from wowlab_core.cli._cmd_char import (
    _history_head as _history_head,
)
from wowlab_core.cli._cmd_char import (
    _history_item as _history_item,
)
from wowlab_core.cli._cmd_char import (
    _history_lines as _history_lines,
)
from wowlab_core.cli._cmd_char import (
    _history_names as _history_names,
)
from wowlab_core.cli._cmd_char import (
    _history_summary as _history_summary,
)
from wowlab_core.cli._cmd_char import (
    _named as _named,
)
from wowlab_core.cli._cmd_char import (
    _no_gear as _no_gear,
)
from wowlab_core.cli._cmd_char import (
    _num_words as _num_words,
)
from wowlab_core.cli._cmd_char import (
    _older_record_line as _older_record_line,
)
from wowlab_core.cli._cmd_char import (
    _picked_words as _picked_words,
)
from wowlab_core.cli._cmd_char import (
    _placement_lines as _placement_lines,
)
from wowlab_core.cli._cmd_char import (
    _profession_words as _profession_words,
)
from wowlab_core.cli._cmd_char import (
    _row_words as _row_words,
)
from wowlab_core.cli._cmd_char import (
    _say_char_binds as _say_char_binds,
)
from wowlab_core.cli._cmd_char import (
    _say_char_history as _say_char_history,
)
from wowlab_core.cli._cmd_char import (
    _say_char_macros as _say_char_macros,
)
from wowlab_core.cli._cmd_char import (
    _say_record as _say_record,
)
from wowlab_core.cli._cmd_char import (
    _say_summary as _say_summary,
)
from wowlab_core.cli._cmd_char import (
    _section_absent as _section_absent,
)
from wowlab_core.cli._cmd_char import (
    _shows_counts as _shows_counts,
)
from wowlab_core.cli._cmd_char import (
    _skill_names as _skill_names,
)
from wowlab_core.cli._cmd_char import (
    _slot_label as _slot_label,
)
from wowlab_core.cli._cmd_char import (
    _source_words as _source_words,
)
from wowlab_core.cli._cmd_char import (
    _summary_lines as _summary_lines,
)
from wowlab_core.cli._cmd_char import (
    _summary_row as _summary_row,
)
from wowlab_core.cli._cmd_char import (
    _summary_value as _summary_value,
)
from wowlab_core.cli._cmd_char import (
    _unseen_words as _unseen_words,
)
from wowlab_core.cli._cmd_char import (
    char_binds as char_binds,
)
from wowlab_core.cli._cmd_char import (
    char_gear as char_gear,
)
from wowlab_core.cli._cmd_char import (
    char_history as char_history,
)
from wowlab_core.cli._cmd_char import (
    char_list as char_list,
)
from wowlab_core.cli._cmd_char import (
    char_macros as char_macros,
)
from wowlab_core.cli._cmd_char import (
    char_show as char_show,
)
from wowlab_core.cli._cmd_cvar import (
    _ACCOUNT_BINDINGS as _ACCOUNT_BINDINGS,
)
from wowlab_core.cli._cmd_cvar import (
    _MACRO_SYNC as _MACRO_SYNC,
)
from wowlab_core.cli._cmd_cvar import (
    _NO_CHARACTER_BINDINGS as _NO_CHARACTER_BINDINGS,
)
from wowlab_core.cli._cmd_cvar import (
    _WHAT_THE_FILE_SAYS as _WHAT_THE_FILE_SAYS,
)
from wowlab_core.cli._cmd_cvar import (
    CVarGetReport as CVarGetReport,
)
from wowlab_core.cli._cmd_cvar import (
    CVarItem as CVarItem,
)
from wowlab_core.cli._cmd_cvar import (
    CVarListReport as CVarListReport,
)
from wowlab_core.cli._cmd_cvar import (
    ScopeOpt as ScopeOpt,
)
from wowlab_core.cli._cmd_cvar import (
    _no_file as _no_file,
)
from wowlab_core.cli._cmd_cvar import (
    _read_config as _read_config,
)
from wowlab_core.cli._cmd_cvar import (
    cvar_get as cvar_get,
)
from wowlab_core.cli._cmd_cvar import (
    cvar_list as cvar_list,
)
from wowlab_core.cli._cmd_db2 import (
    BuildOpt as BuildOpt,
)
from wowlab_core.cli._cmd_db2 import (
    BuildsReport as BuildsReport,
)
from wowlab_core.cli._cmd_db2 import (
    FetchReport as FetchReport,
)
from wowlab_core.cli._cmd_db2 import (
    HeadReport as HeadReport,
)
from wowlab_core.cli._cmd_db2 import (
    _build_for as _build_for,
)
from wowlab_core.cli._cmd_db2 import (
    _check_table_key as _check_table_key,
)
from wowlab_core.cli._cmd_db2 import (
    _unknown_build as _unknown_build,
)
from wowlab_core.cli._cmd_db2 import (
    db2_builds as db2_builds,
)
from wowlab_core.cli._cmd_db2 import (
    db2_fetch as db2_fetch,
)
from wowlab_core.cli._cmd_db2 import (
    db2_head as db2_head,
)
from wowlab_core.cli._cmd_doctor import (
    _CHECKOUT_PROJECT as _CHECKOUT_PROJECT,
)
from wowlab_core.cli._cmd_doctor import (
    _COMBATLOG_HOW as _COMBATLOG_HOW,
)
from wowlab_core.cli._cmd_doctor import (
    _DIST as _DIST,
)
from wowlab_core.cli._cmd_doctor import (
    _JOURNAL_STATE_WORDS as _JOURNAL_STATE_WORDS,
)
from wowlab_core.cli._cmd_doctor import (
    _LEFT_ORDER as _LEFT_ORDER,
)
from wowlab_core.cli._cmd_doctor import (
    _LOAD_OUT_OF_DATE as _LOAD_OUT_OF_DATE,
)
from wowlab_core.cli._cmd_doctor import (
    _NUMBER_WORDS as _NUMBER_WORDS,
)
from wowlab_core.cli._cmd_doctor import (
    _OFFLINE_DEPRECATED as _OFFLINE_DEPRECATED,
)
from wowlab_core.cli._cmd_doctor import (
    _POST_RE as _POST_RE,
)
from wowlab_core.cli._cmd_doctor import (
    _PRE as _PRE,
)
from wowlab_core.cli._cmd_doctor import (
    _PRE_RE as _PRE_RE,
)
from wowlab_core.cli._cmd_doctor import (
    _RECORD_HOW as _RECORD_HOW,
)
from wowlab_core.cli._cmd_doctor import (
    _RECORDS_WAIT as _RECORDS_WAIT,
)
from wowlab_core.cli._cmd_doctor import (
    _REINSTALL as _REINSTALL,
)
from wowlab_core.cli._cmd_doctor import (
    _SCAN_AND_REPAIR as _SCAN_AND_REPAIR,
)
from wowlab_core.cli._cmd_doctor import (
    _STALE_EFFECT as _STALE_EFFECT,
)
from wowlab_core.cli._cmd_doctor import (
    _STORE_MODES_NOTE as _STORE_MODES_NOTE,
)
from wowlab_core.cli._cmd_doctor import (
    _STORE_VERIFY_NOTE as _STORE_VERIFY_NOTE,
)
from wowlab_core.cli._cmd_doctor import (
    _SYNC_CVARS as _SYNC_CVARS,
)
from wowlab_core.cli._cmd_doctor import (
    _SYNC_NOTE as _SYNC_NOTE,
)
from wowlab_core.cli._cmd_doctor import (
    _SYNC_UNSET as _SYNC_UNSET,
)
from wowlab_core.cli._cmd_doctor import (
    _SYNC_UNSET_ONE as _SYNC_UNSET_ONE,
)
from wowlab_core.cli._cmd_doctor import (
    _THE_GAME as _THE_GAME,
)
from wowlab_core.cli._cmd_doctor import (
    CannotWorkOut as CannotWorkOut,
)
from wowlab_core.cli._cmd_doctor import (
    CannotWorkOutCause as CannotWorkOutCause,
)
from wowlab_core.cli._cmd_doctor import (
    ClientReport as ClientReport,
)
from wowlab_core.cli._cmd_doctor import (
    DoctorFlavor as DoctorFlavor,
)
from wowlab_core.cli._cmd_doctor import (
    DoctorReport as DoctorReport,
)
from wowlab_core.cli._cmd_doctor import (
    GameDataReport as GameDataReport,
)
from wowlab_core.cli._cmd_doctor import (
    LastTransaction as LastTransaction,
)
from wowlab_core.cli._cmd_doctor import (
    LeftNow as LeftNow,
)
from wowlab_core.cli._cmd_doctor import (
    LeftPath as LeftPath,
)
from wowlab_core.cli._cmd_doctor import (
    Locations as Locations,
)
from wowlab_core.cli._cmd_doctor import (
    NextStep as NextStep,
)
from wowlab_core.cli._cmd_doctor import (
    NextStepItem as NextStepItem,
)
from wowlab_core.cli._cmd_doctor import (
    NextStepState as NextStepState,
)
from wowlab_core.cli._cmd_doctor import (
    RunningWowlab as RunningWowlab,
)
from wowlab_core.cli._cmd_doctor import (
    StoreReport as StoreReport,
)
from wowlab_core.cli._cmd_doctor import (
    SyncCVar as SyncCVar,
)
from wowlab_core.cli._cmd_doctor import (
    UncheckedLocation as UncheckedLocation,
)
from wowlab_core.cli._cmd_doctor import (
    WowlabInstall as WowlabInstall,
)
from wowlab_core.cli._cmd_doctor import (
    _ask as _ask,
)
from wowlab_core.cli._cmd_doctor import (
    _cannot_work_out as _cannot_work_out,
)
from wowlab_core.cli._cmd_doctor import (
    _cannot_work_out_problem as _cannot_work_out_problem,
)
from wowlab_core.cli._cmd_doctor import (
    _characters_step as _characters_step,
)
from wowlab_core.cli._cmd_doctor import (
    _checkout_holding as _checkout_holding,
)
from wowlab_core.cli._cmd_doctor import (
    _checkout_of as _checkout_of,
)
from wowlab_core.cli._cmd_doctor import (
    _checkout_version as _checkout_version,
)
from wowlab_core.cli._cmd_doctor import (
    _choose_flavor as _choose_flavor,
)
from wowlab_core.cli._cmd_doctor import (
    _client_descriptions as _client_descriptions,
)
from wowlab_core.cli._cmd_doctor import (
    _client_report as _client_report,
)
from wowlab_core.cli._cmd_doctor import (
    _combat_log_step as _combat_log_step,
)
from wowlab_core.cli._cmd_doctor import (
    _commands_in as _commands_in,
)
from wowlab_core.cli._cmd_doctor import (
    _could_be_an_install as _could_be_an_install,
)
from wowlab_core.cli._cmd_doctor import (
    _doctor as _doctor,
)
from wowlab_core.cli._cmd_doctor import (
    _doctor_redacted as _doctor_redacted,
)
from wowlab_core.cli._cmd_doctor import (
    _editable as _editable,
)
from wowlab_core.cli._cmd_doctor import (
    _exposure_line as _exposure_line,
)
from wowlab_core.cli._cmd_doctor import (
    _first_run as _first_run,
)
from wowlab_core.cli._cmd_doctor import (
    _flavor_steps as _flavor_steps,
)
from wowlab_core.cli._cmd_doctor import (
    _game_data as _game_data,
)
from wowlab_core.cli._cmd_doctor import (
    _hash_now as _hash_now,
)
from wowlab_core.cli._cmd_doctor import (
    _install_step as _install_step,
)
from wowlab_core.cli._cmd_doctor import (
    _interactive as _interactive,
)
from wowlab_core.cli._cmd_doctor import (
    _interrupted as _interrupted,
)
from wowlab_core.cli._cmd_doctor import (
    _journal_state_words as _journal_state_words,
)
from wowlab_core.cli._cmd_doctor import (
    _journaled_flavor as _journaled_flavor,
)
from wowlab_core.cli._cmd_doctor import (
    _lab_addon_line as _lab_addon_line,
)
from wowlab_core.cli._cmd_doctor import (
    _lab_addon_step as _lab_addon_step,
)
from wowlab_core.cli._cmd_doctor import (
    _left_clause as _left_clause,
)
from wowlab_core.cli._cmd_doctor import (
    _left_paths as _left_paths,
)
from wowlab_core.cli._cmd_doctor import (
    _newest_whole_snapshot as _newest_whole_snapshot,
)
from wowlab_core.cli._cmd_doctor import (
    _next_step_answer as _next_step_answer,
)
from wowlab_core.cli._cmd_doctor import (
    _no_text as _no_text,
)
from wowlab_core.cli._cmd_doctor import (
    _no_version_cause as _no_version_cause,
)
from wowlab_core.cli._cmd_doctor import (
    _normal_version as _normal_version,
)
from wowlab_core.cli._cmd_doctor import (
    _offers_undo as _offers_undo,
)
from wowlab_core.cli._cmd_doctor import (
    _open_gamedata as _open_gamedata,
)
from wowlab_core.cli._cmd_doctor import (
    _print_doctor as _print_doctor,
)
from wowlab_core.cli._cmd_doctor import (
    _print_game_data as _print_game_data,
)
from wowlab_core.cli._cmd_doctor import (
    _print_next_steps as _print_next_steps,
)
from wowlab_core.cli._cmd_doctor import (
    _print_wowlab as _print_wowlab,
)
from wowlab_core.cli._cmd_doctor import (
    _read_left as _read_left,
)
from wowlab_core.cli._cmd_doctor import (
    _rollback_unfinished as _rollback_unfinished,
)
from wowlab_core.cli._cmd_doctor import (
    _run_checkout as _run_checkout,
)
from wowlab_core.cli._cmd_doctor import (
    _running_wowlab as _running_wowlab,
)
from wowlab_core.cli._cmd_doctor import (
    _snapshot_unreadable as _snapshot_unreadable,
)
from wowlab_core.cli._cmd_doctor import (
    _stopped_before_any_file as _stopped_before_any_file,
)
from wowlab_core.cli._cmd_doctor import (
    _store_report as _store_report,
)
from wowlab_core.cli._cmd_doctor import (
    _subject as _subject,
)
from wowlab_core.cli._cmd_doctor import (
    _sync_cvars as _sync_cvars,
)
from wowlab_core.cli._cmd_doctor import (
    _unknown_version as _unknown_version,
)
from wowlab_core.cli._cmd_doctor import (
    _wowlab_command as _wowlab_command,
)
from wowlab_core.cli._cmd_doctor import (
    _wowlab_probe as _wowlab_probe,
)
from wowlab_core.cli._cmd_doctor import (
    doctor as doctor,
)
from wowlab_core.cli._cmd_filemap import (
    _ABSENT_VIEWS as _ABSENT_VIEWS,
)
from wowlab_core.cli._cmd_filemap import (
    _CANDIDATES_EXPLAINED as _CANDIDATES_EXPLAINED,
)
from wowlab_core.cli._cmd_filemap import (
    _CANDIDATES_SHOWN as _CANDIDATES_SHOWN,
)
from wowlab_core.cli._cmd_filemap import (
    _FIRST_SNAPSHOT as _FIRST_SNAPSHOT,
)
from wowlab_core.cli._cmd_filemap import (
    _FIRST_SNAPSHOT_ABSENT as _FIRST_SNAPSHOT_ABSENT,
)
from wowlab_core.cli._cmd_filemap import (
    _FLAVOR_LINK_NOTE as _FLAVOR_LINK_NOTE,
)
from wowlab_core.cli._cmd_filemap import (
    _NO_READER as _NO_READER,
)
from wowlab_core.cli._cmd_filemap import (
    _NOT_FOUND_AT_ROOT as _NOT_FOUND_AT_ROOT,
)
from wowlab_core.cli._cmd_filemap import (
    _SELECTION_WORDS as _SELECTION_WORDS,
)
from wowlab_core.cli._cmd_filemap import (
    _SHELL_LABELS as _SHELL_LABELS,
)
from wowlab_core.cli._cmd_filemap import (
    _UNCONFIRMED as _UNCONFIRMED,
)
from wowlab_core.cli._cmd_filemap import (
    _VERIFY_MARK as _VERIFY_MARK,
)
from wowlab_core.cli._cmd_filemap import (
    _VIEW_OPTIONS as _VIEW_OPTIONS,
)
from wowlab_core.cli._cmd_filemap import (
    _VIEWS as _VIEWS,
)
from wowlab_core.cli._cmd_filemap import (
    WRITTEN_AT_LOGOUT as WRITTEN_AT_LOGOUT,
)
from wowlab_core.cli._cmd_filemap import (
    ExplainCandidate as ExplainCandidate,
)
from wowlab_core.cli._cmd_filemap import (
    Explained as Explained,
)
from wowlab_core.cli._cmd_filemap import (
    ExplainHint as ExplainHint,
)
from wowlab_core.cli._cmd_filemap import (
    ExplainReport as ExplainReport,
)
from wowlab_core.cli._cmd_filemap import (
    ExplainView as ExplainView,
)
from wowlab_core.cli._cmd_filemap import (
    InstallTreeReport as InstallTreeReport,
)
from wowlab_core.cli._cmd_filemap import (
    PlainText as PlainText,
)
from wowlab_core.cli._cmd_filemap import (
    TreeItem as TreeItem,
)
from wowlab_core.cli._cmd_filemap import (
    TreeReport as TreeReport,
)
from wowlab_core.cli._cmd_filemap import (
    _absent_line as _absent_line,
)
from wowlab_core.cli._cmd_filemap import (
    _account as _account,
)
from wowlab_core.cli._cmd_filemap import (
    _account_binds as _account_binds,
)
from wowlab_core.cli._cmd_filemap import (
    _account_config as _account_config,
)
from wowlab_core.cli._cmd_filemap import (
    _account_macros as _account_macros,
)
from wowlab_core.cli._cmd_filemap import (
    _addon_executable as _addon_executable,
)
from wowlab_core.cli._cmd_filemap import (
    _addons_check_view as _addons_check_view,
)
from wowlab_core.cli._cmd_filemap import (
    _addons_list as _addons_list,
)
from wowlab_core.cli._cmd_filemap import (
    _ancestors as _ancestors,
)
from wowlab_core.cli._cmd_filemap import (
    _At as _At,
)
from wowlab_core.cli._cmd_filemap import (
    _captured_words as _captured_words,
)
from wowlab_core.cli._cmd_filemap import (
    _character as _character,
)
from wowlab_core.cli._cmd_filemap import (
    _character_wtf as _character_wtf,
)
from wowlab_core.cli._cmd_filemap import (
    _combat_log as _combat_log,
)
from wowlab_core.cli._cmd_filemap import (
    _config_wtf as _config_wtf,
)
from wowlab_core.cli._cmd_filemap import (
    _count as _count,
)
from wowlab_core.cli._cmd_filemap import (
    _detail as _detail,
)
from wowlab_core.cli._cmd_filemap import (
    _disk_parts as _disk_parts,
)
from wowlab_core.cli._cmd_filemap import (
    _explain_candidates as _explain_candidates,
)
from wowlab_core.cli._cmd_filemap import (
    _explained as _explained,
)
from wowlab_core.cli._cmd_filemap import (
    _flavor as _flavor,
)
from wowlab_core.cli._cmd_filemap import (
    _flavor_layout as _flavor_layout,
)
from wowlab_core.cli._cmd_filemap import (
    _flavor_reparse as _flavor_reparse,
)
from wowlab_core.cli._cmd_filemap import (
    _folder_verdict as _folder_verdict,
)
from wowlab_core.cli._cmd_filemap import (
    _found_at_root as _found_at_root,
)
from wowlab_core.cli._cmd_filemap import (
    _gap_examples as _gap_examples,
)
from wowlab_core.cli._cmd_filemap import (
    _group_counts as _group_counts,
)
from wowlab_core.cli._cmd_filemap import (
    _hint as _hint,
)
from wowlab_core.cli._cmd_filemap import (
    _hints as _hints,
)
from wowlab_core.cli._cmd_filemap import (
    _in_scope as _in_scope,
)
from wowlab_core.cli._cmd_filemap import (
    _install_path as _install_path,
)
from wowlab_core.cli._cmd_filemap import (
    _install_show as _install_show,
)
from wowlab_core.cli._cmd_filemap import (
    _is_inside as _is_inside,
)
from wowlab_core.cli._cmd_filemap import (
    _link_note as _link_note,
)
from wowlab_core.cli._cmd_filemap import (
    _link_stands_for as _link_stands_for,
)
from wowlab_core.cli._cmd_filemap import (
    _link_verdict as _link_verdict,
)
from wowlab_core.cli._cmd_filemap import (
    _not_taken_at as _not_taken_at,
)
from wowlab_core.cli._cmd_filemap import (
    _nowhere as _nowhere,
)
from wowlab_core.cli._cmd_filemap import (
    _path_kind as _path_kind,
)
from wowlab_core.cli._cmd_filemap import (
    _plain as _plain,
)
from wowlab_core.cli._cmd_filemap import (
    _plain_arg as _plain_arg,
)
from wowlab_core.cli._cmd_filemap import (
    _plain_text as _plain_text,
)
from wowlab_core.cli._cmd_filemap import (
    _player_text as _player_text,
)
from wowlab_core.cli._cmd_filemap import (
    _pre_write_words as _pre_write_words,
)
from wowlab_core.cli._cmd_filemap import (
    _print_gaps as _print_gaps,
)
from wowlab_core.cli._cmd_filemap import (
    _Reparse as _Reparse,
)
from wowlab_core.cli._cmd_filemap import (
    _root_items as _root_items,
)
from wowlab_core.cli._cmd_filemap import (
    _say_candidates as _say_candidates,
)
from wowlab_core.cli._cmd_filemap import (
    _say_detail as _say_detail,
)
from wowlab_core.cli._cmd_filemap import (
    _say_gap_groups as _say_gap_groups,
)
from wowlab_core.cli._cmd_filemap import (
    _say_tree_item as _say_tree_item,
)
from wowlab_core.cli._cmd_filemap import (
    _snap_name as _snap_name,
)
from wowlab_core.cli._cmd_filemap import (
    _snapshot_line as _snapshot_line,
)
from wowlab_core.cli._cmd_filemap import (
    _stored_target as _stored_target,
)
from wowlab_core.cli._cmd_filemap import (
    _sv_dump as _sv_dump,
)
from wowlab_core.cli._cmd_filemap import (
    _tree_install as _tree_install,
)
from wowlab_core.cli._cmd_filemap import (
    _tree_items as _tree_items,
)
from wowlab_core.cli._cmd_filemap import (
    _tree_prefix as _tree_prefix,
)
from wowlab_core.cli._cmd_filemap import (
    _view as _view,
)
from wowlab_core.cli._cmd_filemap import (
    _views as _views,
)
from wowlab_core.cli._cmd_filemap import (
    _ViewSpec as _ViewSpec,
)
from wowlab_core.cli._cmd_filemap import (
    _with_explained as _with_explained,
)
from wowlab_core.cli._cmd_filemap import (
    explain as explain,
)
from wowlab_core.cli._cmd_filemap import (
    filemap_gaps as filemap_gaps,
)
from wowlab_core.cli._cmd_filemap import (
    tree as tree,
)
from wowlab_core.cli._cmd_install import (
    install_show as install_show,
)
from wowlab_core.cli._cmd_log import (
    _BATCHES as _BATCHES,
)
from wowlab_core.cli._cmd_log import (
    _FOLLOWING_WORDS as _FOLLOWING_WORDS,
)
from wowlab_core.cli._cmd_log import (
    _LOG_POLL as _LOG_POLL,
)
from wowlab_core.cli._cmd_log import (
    _NO_LOG as _NO_LOG,
)
from wowlab_core.cli._cmd_log import (
    _STILL_WRITING as _STILL_WRITING,
)
from wowlab_core.cli._cmd_log import (
    MAX_TAIL_LINES as MAX_TAIL_LINES,
)
from wowlab_core.cli._cmd_log import (
    LogEntry as LogEntry,
)
from wowlab_core.cli._cmd_log import (
    LogFollowing as LogFollowing,
)
from wowlab_core.cli._cmd_log import (
    LogTailLine as LogTailLine,
)
from wowlab_core.cli._cmd_log import (
    LogTailReport as LogTailReport,
)
from wowlab_core.cli._cmd_log import (
    _follow_log as _follow_log,
)
from wowlab_core.cli._cmd_log import (
    _follow_sleep as _follow_sleep,
)
from wowlab_core.cli._cmd_log import (
    _logs_dir as _logs_dir,
)
from wowlab_core.cli._cmd_log import (
    _print_log_entry as _print_log_entry,
)
from wowlab_core.cli._cmd_log import (
    _rel as _rel,
)
from wowlab_core.cli._cmd_log import (
    log_tail as log_tail,
)
from wowlab_core.cli._cmd_looks import (
    _ALLIANCE_WORDS as _ALLIANCE_WORDS,
)
from wowlab_core.cli._cmd_looks import (
    _AS_OF_WORDS as _AS_OF_WORDS,
)
from wowlab_core.cli._cmd_looks import (
    _CLASS_DISAGREES_REMARK as _CLASS_DISAGREES_REMARK,
)
from wowlab_core.cli._cmd_looks import (
    _EXPORTED_ONLY as _EXPORTED_ONLY,
)
from wowlab_core.cli._cmd_looks import (
    _HOTFIX_HINT as _HOTFIX_HINT,
)
from wowlab_core.cli._cmd_looks import (
    _ID as _ID,
)
from wowlab_core.cli._cmd_looks import (
    _IMPORTED_BUILD_REMARK as _IMPORTED_BUILD_REMARK,
)
from wowlab_core.cli._cmd_looks import (
    _IMPORTED_REMARK as _IMPORTED_REMARK,
)
from wowlab_core.cli._cmd_looks import (
    _IMPORTED_UNKNOWN as _IMPORTED_UNKNOWN,
)
from wowlab_core.cli._cmd_looks import (
    _MODEL_HOTFIX as _MODEL_HOTFIX,
)
from wowlab_core.cli._cmd_looks import (
    _MODEL_ONLY_REMARK as _MODEL_ONLY_REMARK,
)
from wowlab_core.cli._cmd_looks import (
    _NO_CLASS_REMARK as _NO_CLASS_REMARK,
)
from wowlab_core.cli._cmd_looks import (
    _NOT_LISTED_REMARK as _NOT_LISTED_REMARK,
)
from wowlab_core.cli._cmd_looks import (
    _OPEN_REMARK as _OPEN_REMARK,
)
from wowlab_core.cli._cmd_looks import (
    _PAGE_CLASSES_NOTE as _PAGE_CLASSES_NOTE,
)
from wowlab_core.cli._cmd_looks import (
    _PAGE_LEGEND as _PAGE_LEGEND,
)
from wowlab_core.cli._cmd_looks import (
    _PAGE_NO_CLASS_LABEL as _PAGE_NO_CLASS_LABEL,
)
from wowlab_core.cli._cmd_looks import (
    _PAGE_NO_CLASS_NOTE as _PAGE_NO_CLASS_NOTE,
)
from wowlab_core.cli._cmd_looks import (
    _PAGE_PAIR_NOTE as _PAGE_PAIR_NOTE,
)
from wowlab_core.cli._cmd_looks import (
    _PLAYABLE_NOTE as _PLAYABLE_NOTE,
)
from wowlab_core.cli._cmd_looks import (
    _RACES_DIFFER_REMARK as _RACES_DIFFER_REMARK,
)
from wowlab_core.cli._cmd_looks import (
    _RECORDED_CLASS_REMARK as _RECORDED_CLASS_REMARK,
)
from wowlab_core.cli._cmd_looks import (
    _RECORDED_NO_CLASS_REMARK as _RECORDED_NO_CLASS_REMARK,
)
from wowlab_core.cli._cmd_looks import (
    _SEX_WORDS as _SEX_WORDS,
)
from wowlab_core.cli._cmd_looks import (
    LOOKS_PAGE_FORMAT as LOOKS_PAGE_FORMAT,
)
from wowlab_core.cli._cmd_looks import (
    ClassOpt as ClassOpt,
)
from wowlab_core.cli._cmd_looks import (
    LookNameArg as LookNameArg,
)
from wowlab_core.cli._cmd_looks import (
    LookReport as LookReport,
)
from wowlab_core.cli._cmd_looks import (
    LooksBodyType as LooksBodyType,
)
from wowlab_core.cli._cmd_looks import (
    LooksBuildOpt as LooksBuildOpt,
)
from wowlab_core.cli._cmd_looks import (
    LooksChoice as LooksChoice,
)
from wowlab_core.cli._cmd_looks import (
    LooksChoiceRef as LooksChoiceRef,
)
from wowlab_core.cli._cmd_looks import (
    LooksCompareReport as LooksCompareReport,
)
from wowlab_core.cli._cmd_looks import (
    LooksDifference as LooksDifference,
)
from wowlab_core.cli._cmd_looks import (
    LooksImportReport as LooksImportReport,
)
from wowlab_core.cli._cmd_looks import (
    LooksListReport as LooksListReport,
)
from wowlab_core.cli._cmd_looks import (
    LooksOption as LooksOption,
)
from wowlab_core.cli._cmd_looks import (
    LooksOptionsReport as LooksOptionsReport,
)
from wowlab_core.cli._cmd_looks import (
    LooksPageClass as LooksPageClass,
)
from wowlab_core.cli._cmd_looks import (
    LooksPageData as LooksPageData,
)
from wowlab_core.cli._cmd_looks import (
    LooksPageLook as LooksPageLook,
)
from wowlab_core.cli._cmd_looks import (
    LooksPageOption as LooksPageOption,
)
from wowlab_core.cli._cmd_looks import (
    LooksPageRace as LooksPageRace,
)
from wowlab_core.cli._cmd_looks import (
    LooksPageReport as LooksPageReport,
)
from wowlab_core.cli._cmd_looks import (
    LooksPageView as LooksPageView,
)
from wowlab_core.cli._cmd_looks import (
    LooksRace as LooksRace,
)
from wowlab_core.cli._cmd_looks import (
    LooksRacesReport as LooksRacesReport,
)
from wowlab_core.cli._cmd_looks import (
    LookSummary as LookSummary,
)
from wowlab_core.cli._cmd_looks import (
    SexOpt as SexOpt,
)
from wowlab_core.cli._cmd_looks import (
    _as_id as _as_id,
)
from wowlab_core.cli._cmd_looks import (
    _cached_look_builds as _cached_look_builds,
)
from wowlab_core.cli._cmd_looks import (
    _choice_ref as _choice_ref,
)
from wowlab_core.cli._cmd_looks import (
    _choice_text as _choice_text,
)
from wowlab_core.cli._cmd_looks import (
    _class_name as _class_name,
)
from wowlab_core.cli._cmd_looks import (
    _class_words as _class_words,
)
from wowlab_core.cli._cmd_looks import (
    _faction as _faction,
)
from wowlab_core.cli._cmd_looks import (
    _home_short as _home_short,
)
from wowlab_core.cli._cmd_looks import (
    _import_class as _import_class,
)
from wowlab_core.cli._cmd_looks import (
    _import_remarks as _import_remarks,
)
from wowlab_core.cli._cmd_looks import (
    _imported_wording as _imported_wording,
)
from wowlab_core.cli._cmd_looks import (
    _load_model as _load_model,
)
from wowlab_core.cli._cmd_looks import (
    _loads_ago_text as _loads_ago_text,
)
from wowlab_core.cli._cmd_looks import (
    _look_report as _look_report,
)
from wowlab_core.cli._cmd_looks import (
    _looks_build as _looks_build,
)
from wowlab_core.cli._cmd_looks import (
    _looks_page_data as _looks_page_data,
)
from wowlab_core.cli._cmd_looks import (
    _option_out as _option_out,
)
from wowlab_core.cli._cmd_looks import (
    _options_for_body as _options_for_body,
)
from wowlab_core.cli._cmd_looks import (
    _page_look as _page_look,
)
from wowlab_core.cli._cmd_looks import (
    _page_option as _page_option,
)
from wowlab_core.cli._cmd_looks import (
    _page_report as _page_report,
)
from wowlab_core.cli._cmd_looks import (
    _parse_choices as _parse_choices,
)
from wowlab_core.cli._cmd_looks import (
    _parse_sex as _parse_sex,
)
from wowlab_core.cli._cmd_looks import (
    _print_findings as _print_findings,
)
from wowlab_core.cli._cmd_looks import (
    _print_look as _print_look,
)
from wowlab_core.cli._cmd_looks import (
    _race_label as _race_label,
)
from wowlab_core.cli._cmd_looks import (
    _race_out as _race_out,
)
from wowlab_core.cli._cmd_looks import (
    _ref_choice_text as _ref_choice_text,
)
from wowlab_core.cli._cmd_looks import (
    _ref_text as _ref_text,
)
from wowlab_core.cli._cmd_looks import (
    _resolve_class as _resolve_class,
)
from wowlab_core.cli._cmd_looks import (
    _resolve_race as _resolve_race,
)
from wowlab_core.cli._cmd_looks import (
    _save_wording as _save_wording,
)
from wowlab_core.cli._cmd_looks import (
    looks_compare as looks_compare,
)
from wowlab_core.cli._cmd_looks import (
    looks_import_char as looks_import_char,
)
from wowlab_core.cli._cmd_looks import (
    looks_options as looks_options,
)
from wowlab_core.cli._cmd_looks import (
    looks_page as looks_page,
)
from wowlab_core.cli._cmd_looks import (
    looks_races as looks_races,
)
from wowlab_core.cli._cmd_looks import (
    looks_save as looks_save,
)
from wowlab_core.cli._cmd_looks import (
    looks_show as looks_show,
)
from wowlab_core.cli._cmd_macros import (
    _MACRO_FILE_NOTE as _MACRO_FILE_NOTE,
)
from wowlab_core.cli._cmd_macros import (
    _MACRO_FILE_TEXT as _MACRO_FILE_TEXT,
)
from wowlab_core.cli._cmd_macros import (
    _MACROS_CHECK_NOTES as _MACROS_CHECK_NOTES,
)
from wowlab_core.cli._cmd_macros import (
    _MACROS_LIMIT as _MACROS_LIMIT,
)
from wowlab_core.cli._cmd_macros import (
    MacroItem as MacroItem,
)
from wowlab_core.cli._cmd_macros import (
    MacrosCheckReport as MacrosCheckReport,
)
from wowlab_core.cli._cmd_macros import (
    MacrosFile as MacrosFile,
)
from wowlab_core.cli._cmd_macros import (
    MacrosReport as MacrosReport,
)
from wowlab_core.cli._cmd_macros import (
    _check_record as _check_record,
)
from wowlab_core.cli._cmd_macros import (
    _macro_notes as _macro_notes,
)
from wowlab_core.cli._cmd_macros import (
    macros_check as macros_check,
)
from wowlab_core.cli._cmd_macros import (
    macros_list as macros_list,
)
from wowlab_core.cli._cmd_profile import (
    NameArg as NameArg,
)
from wowlab_core.cli._cmd_profile import (
    PresetOpt as PresetOpt,
)
from wowlab_core.cli._cmd_profile import (
    ProfileApplyReport as ProfileApplyReport,
)
from wowlab_core.cli._cmd_profile import (
    ProfileDeleteReport as ProfileDeleteReport,
)
from wowlab_core.cli._cmd_profile import (
    ProfileListReport as ProfileListReport,
)
from wowlab_core.cli._cmd_profile import (
    ProfileReport as ProfileReport,
)
from wowlab_core.cli._cmd_profile import (
    ProfileSummary as ProfileSummary,
)
from wowlab_core.cli._cmd_profile import (
    SubtreeOpt as SubtreeOpt,
)
from wowlab_core.cli._cmd_profile import (
    _apply_plan_lines as _apply_plan_lines,
)
from wowlab_core.cli._cmd_profile import (
    _print_profile as _print_profile,
)
from wowlab_core.cli._cmd_profile import (
    _profile_report as _profile_report,
)
from wowlab_core.cli._cmd_profile import (
    _profile_summary as _profile_summary,
)
from wowlab_core.cli._cmd_profile import (
    profile_apply as profile_apply,
)
from wowlab_core.cli._cmd_profile import (
    profile_delete as profile_delete,
)
from wowlab_core.cli._cmd_profile import (
    profile_list as profile_list,
)
from wowlab_core.cli._cmd_profile import (
    profile_save as profile_save,
)
from wowlab_core.cli._cmd_profile import (
    profile_show as profile_show,
)
from wowlab_core.cli._cmd_restore import (
    IntoFlavorOpt as IntoFlavorOpt,
)
from wowlab_core.cli._cmd_restore import (
    MapAccountOpt as MapAccountOpt,
)
from wowlab_core.cli._cmd_restore import (
    _account_pairs as _account_pairs,
)
from wowlab_core.cli._cmd_restore import (
    _closed_unsnapshotted as _closed_unsnapshotted,
)
from wowlab_core.cli._cmd_restore import (
    _into_lines as _into_lines,
)
from wowlab_core.cli._cmd_restore import (
    _into_report as _into_report,
)
from wowlab_core.cli._cmd_restore import (
    _into_target as _into_target,
)
from wowlab_core.cli._cmd_restore import (
    _one_folder_name as _one_folder_name,
)
from wowlab_core.cli._cmd_restore import (
    _undo_lines as _undo_lines,
)
from wowlab_core.cli._cmd_restore import (
    _undo_plan as _undo_plan,
)
from wowlab_core.cli._cmd_restore import (
    _undone_unlisted as _undone_unlisted,
)
from wowlab_core.cli._cmd_restore import (
    snap_restore as snap_restore,
)
from wowlab_core.cli._cmd_restore import (
    undo as undo,
)
from wowlab_core.cli._cmd_snap import (
    _REF_HELP as _REF_HELP,
)
from wowlab_core.cli._cmd_snap import (
    GC_GRACE_SECONDS as GC_GRACE_SECONDS,
)
from wowlab_core.cli._cmd_snap import (
    ChangedFile as ChangedFile,
)
from wowlab_core.cli._cmd_snap import (
    DiffReport as DiffReport,
)
from wowlab_core.cli._cmd_snap import (
    LuaChange as LuaChange,
)
from wowlab_core.cli._cmd_snap import (
    SnapListReport as SnapListReport,
)
from wowlab_core.cli._cmd_snap import (
    SnapshotSummary as SnapshotSummary,
)
from wowlab_core.cli._cmd_snap import (
    _diff_detail_lines as _diff_detail_lines,
)
from wowlab_core.cli._cmd_snap import (
    _lua_diff as _lua_diff,
)
from wowlab_core.cli._cmd_snap import (
    _plan_line as _plan_line,
)
from wowlab_core.cli._cmd_snap import (
    _resolve_ref as _resolve_ref,
)
from wowlab_core.cli._cmd_snap import (
    _summary as _summary,
)
from wowlab_core.cli._cmd_snap import (
    _version as _version,
)
from wowlab_core.cli._cmd_snap import (
    snap_create as snap_create,
)
from wowlab_core.cli._cmd_snap import (
    snap_diff as snap_diff,
)
from wowlab_core.cli._cmd_snap import (
    snap_export as snap_export,
)
from wowlab_core.cli._cmd_snap import (
    snap_gc as snap_gc,
)
from wowlab_core.cli._cmd_snap import (
    snap_import as snap_import,
)
from wowlab_core.cli._cmd_snap import (
    snap_list as snap_list,
)
from wowlab_core.cli._cmd_snap import (
    snap_show as snap_show,
)
from wowlab_core.cli._cmd_snap import (
    snap_verify as snap_verify,
)
from wowlab_core.cli._cmd_sv import (
    _ABSENT_NOTE as _ABSENT_NOTE,
)
from wowlab_core.cli._cmd_sv import (
    _CHARACTER_SV_PARTS as _CHARACTER_SV_PARTS,
)
from wowlab_core.cli._cmd_sv import (
    _CLOCK_AHEAD as _CLOCK_AHEAD,
)
from wowlab_core.cli._cmd_sv import (
    _FOLDED_ACCOUNT as _FOLDED_ACCOUNT,
)
from wowlab_core.cli._cmd_sv import (
    _FOLDED_SAVED_VARIABLES as _FOLDED_SAVED_VARIABLES,
)
from wowlab_core.cli._cmd_sv import (
    _FOLDED_WTF as _FOLDED_WTF,
)
from wowlab_core.cli._cmd_sv import (
    _LAB_WROTE as _LAB_WROTE,
)
from wowlab_core.cli._cmd_sv import (
    _LOADER_BUG as _LOADER_BUG,
)
from wowlab_core.cli._cmd_sv import (
    _MAX_LINK_HOPS as _MAX_LINK_HOPS,
)
from wowlab_core.cli._cmd_sv import (
    _MERGE_OWN_NOTES as _MERGE_OWN_NOTES,
)
from wowlab_core.cli._cmd_sv import (
    _MERGE_PROVEN as _MERGE_PROVEN,
)
from wowlab_core.cli._cmd_sv import (
    _NO_LOGIN as _NO_LOGIN,
)
from wowlab_core.cli._cmd_sv import (
    _ORPHAN_BLIZZARD as _ORPHAN_BLIZZARD,
)
from wowlab_core.cli._cmd_sv import (
    _ORPHAN_REINSTALL as _ORPHAN_REINSTALL,
)
from wowlab_core.cli._cmd_sv import (
    _ORPHAN_WHAT as _ORPHAN_WHAT,
)
from wowlab_core.cli._cmd_sv import (
    _PROBE_KEPT as _PROBE_KEPT,
)
from wowlab_core.cli._cmd_sv import (
    _SHA256 as _SHA256,
)
from wowlab_core.cli._cmd_sv import (
    _SV_LAST_PART_ONLY as _SV_LAST_PART_ONLY,
)
from wowlab_core.cli._cmd_sv import (
    _SV_TIMING as _SV_TIMING,
)
from wowlab_core.cli._cmd_sv import (
    LuaBoolNode as LuaBoolNode,
)
from wowlab_core.cli._cmd_sv import (
    LuaNilNode as LuaNilNode,
)
from wowlab_core.cli._cmd_sv import (
    LuaNode as LuaNode,
)
from wowlab_core.cli._cmd_sv import (
    LuaNumberNode as LuaNumberNode,
)
from wowlab_core.cli._cmd_sv import (
    LuaStringNode as LuaStringNode,
)
from wowlab_core.cli._cmd_sv import (
    LuaTableNode as LuaTableNode,
)
from wowlab_core.cli._cmd_sv import (
    OrphansOpt as OrphansOpt,
)
from wowlab_core.cli._cmd_sv import (
    SvDumpReport as SvDumpReport,
)
from wowlab_core.cli._cmd_sv import (
    SvListReport as SvListReport,
)
from wowlab_core.cli._cmd_sv import (
    SvMergeReport as SvMergeReport,
)
from wowlab_core.cli._cmd_sv import (
    SvValue as SvValue,
)
from wowlab_core.cli._cmd_sv import (
    _character_sv as _character_sv,
)
from wowlab_core.cli._cmd_sv import (
    _flatten as _flatten,
)
from wowlab_core.cli._cmd_sv import (
    _install_holding as _install_holding,
)
from wowlab_core.cli._cmd_sv import (
    _into_character as _into_character,
)
from wowlab_core.cli._cmd_sv import (
    _is_character as _is_character,
)
from wowlab_core.cli._cmd_sv import (
    _is_character_lab_file as _is_character_lab_file,
)
from wowlab_core.cli._cmd_sv import (
    _key_node as _key_node,
)
from wowlab_core.cli._cmd_sv import (
    _lab_records_caveat as _lab_records_caveat,
)
from wowlab_core.cli._cmd_sv import (
    _lab_writes as _lab_writes,
)
from wowlab_core.cli._cmd_sv import (
    _lab_written as _lab_written,
)
from wowlab_core.cli._cmd_sv import (
    _LabWrite as _LabWrite,
)
from wowlab_core.cli._cmd_sv import (
    _latest_in as _latest_in,
)
from wowlab_core.cli._cmd_sv import (
    _link_out_of_install as _link_out_of_install,
)
from wowlab_core.cli._cmd_sv import (
    _links_met as _links_met,
)
from wowlab_core.cli._cmd_sv import (
    _loader_check as _loader_check,
)
from wowlab_core.cli._cmd_sv import (
    _lua_text as _lua_text,
)
from wowlab_core.cli._cmd_sv import (
    _merge_expect as _merge_expect,
)
from wowlab_core.cli._cmd_sv import (
    _merge_target as _merge_target,
)
from wowlab_core.cli._cmd_sv import (
    _no_account_notes as _no_account_notes,
)
from wowlab_core.cli._cmd_sv import (
    _no_toc_caveat as _no_toc_caveat,
)
from wowlab_core.cli._cmd_sv import (
    _not_judged_caveat as _not_judged_caveat,
)
from wowlab_core.cli._cmd_sv import (
    _parse_path as _parse_path,
)
from wowlab_core.cli._cmd_sv import (
    _print_merge as _print_merge,
)
from wowlab_core.cli._cmd_sv import (
    _read_sv as _read_sv,
)
from wowlab_core.cli._cmd_sv import (
    _really_inside as _really_inside,
)
from wowlab_core.cli._cmd_sv import (
    _resolve_path as _resolve_path,
)
from wowlab_core.cli._cmd_sv import (
    _rows as _rows,
)
from wowlab_core.cli._cmd_sv import (
    _same_root as _same_root,
)
from wowlab_core.cli._cmd_sv import (
    _scalar as _scalar,
)
from wowlab_core.cli._cmd_sv import (
    _snapshot_sv as _snapshot_sv,
)
from wowlab_core.cli._cmd_sv import (
    _stamp_now as _stamp_now,
)
from wowlab_core.cli._cmd_sv import (
    _stream_dump as _stream_dump,
)
from wowlab_core.cli._cmd_sv import (
    _string_node as _string_node,
)
from wowlab_core.cli._cmd_sv import (
    _sv_file as _sv_file,
)
from wowlab_core.cli._cmd_sv import (
    _sv_file_and_root as _sv_file_and_root,
)
from wowlab_core.cli._cmd_sv import (
    _sv_inside_install as _sv_inside_install,
)
from wowlab_core.cli._cmd_sv import (
    _sv_named as _sv_named,
)
from wowlab_core.cli._cmd_sv import (
    _sv_not_found as _sv_not_found,
)
from wowlab_core.cli._cmd_sv import (
    _sv_orphans as _sv_orphans,
)
from wowlab_core.cli._cmd_sv import (
    _sv_row as _sv_row,
)
from wowlab_core.cli._cmd_sv import (
    _value_node as _value_node,
)
from wowlab_core.cli._cmd_sv import (
    _When as _When,
)
from wowlab_core.cli._cmd_sv import (
    sv_dump as sv_dump,
)
from wowlab_core.cli._cmd_sv import (
    sv_list as sv_list,
)
from wowlab_core.cli._cmd_sv import (
    sv_merge as sv_merge,
)
from wowlab_core.cli._cmd_ui import (
    _BROWSER_WAIT_S as _BROWSER_WAIT_S,
)
from wowlab_core.cli._cmd_ui import (
    ui_command as ui_command,
)
from wowlab_core.cli._common import (
    _ARGS_KEY as _ARGS_KEY,
)
from wowlab_core.cli._common import (
    _BARE as _BARE,
)
from wowlab_core.cli._common import (
    _BLOCKED_PLACE as _BLOCKED_PLACE,
)
from wowlab_core.cli._common import (
    _CAUSE as _CAUSE,
)
from wowlab_core.cli._common import (
    _CONTEXT as _CONTEXT,
)
from wowlab_core.cli._common import (
    _FIELD as _FIELD,
)
from wowlab_core.cli._common import (
    _GROUP_DEPTH as _GROUP_DEPTH,
)
from wowlab_core.cli._common import (
    _GROUP_WIDTH as _GROUP_WIDTH,
)
from wowlab_core.cli._common import (
    _HANDLED_ERRORS as _HANDLED_ERRORS,
)
from wowlab_core.cli._common import (
    _HELD_SPACE as _HELD_SPACE,
)
from wowlab_core.cli._common import (
    _KIND_WORDS as _KIND_WORDS,
)
from wowlab_core.cli._common import (
    _LIBRARY_LOGGER as _LIBRARY_LOGGER,
)
from wowlab_core.cli._common import (
    _LISTERS as _LISTERS,
)
from wowlab_core.cli._common import (
    _LOG_HANDLER as _LOG_HANDLER,
)
from wowlab_core.cli._common import (
    _LUA_REFUSAL_WORDS as _LUA_REFUSAL_WORDS,
)
from wowlab_core.cli._common import (
    _NAME_THE_INSTALL as _NAME_THE_INSTALL,
)
from wowlab_core.cli._common import (
    _NAME_THE_INSTALL_BY_ITSELF as _NAME_THE_INSTALL_BY_ITSELF,
)
from wowlab_core.cli._common import (
    _NEEDED_AFTER as _NEEDED_AFTER,
)
from wowlab_core.cli._common import (
    _NEEDED_BEFORE as _NEEDED_BEFORE,
)
from wowlab_core.cli._common import (
    _PRINTED_ELSEWHERE as _PRINTED_ELSEWHERE,
)
from wowlab_core.cli._common import (
    _PS_QUOTES as _PS_QUOTES,
)
from wowlab_core.cli._common import (
    _QUOTED as _QUOTED,
)
from wowlab_core.cli._common import (
    _READER_WORDS as _READER_WORDS,
)
from wowlab_core.cli._common import (
    _SAVED_AT as _SAVED_AT,
)
from wowlab_core.cli._common import (
    _SPEC_POINTER as _SPEC_POINTER,
)
from wowlab_core.cli._common import (
    _SURROGATE as _SURROGATE,
)
from wowlab_core.cli._common import (
    _UNSAFE as _UNSAFE,
)
from wowlab_core.cli._common import (
    _UTF8_BOM as _UTF8_BOM,
)
from wowlab_core.cli._common import (
    _VERIFY as _VERIFY,
)
from wowlab_core.cli._common import (
    CONFIRM_DECLINED as CONFIRM_DECLINED,
)
from wowlab_core.cli._common import (
    EXIT_ERROR as EXIT_ERROR,
)
from wowlab_core.cli._common import (
    EXIT_OK as EXIT_OK,
)
from wowlab_core.cli._common import (
    EXIT_REFUSED as EXIT_REFUSED,
)
from wowlab_core.cli._common import (
    EXIT_USAGE as EXIT_USAGE,
)
from wowlab_core.cli._common import (
    EXPECT_FIELDS as EXPECT_FIELDS,
)
from wowlab_core.cli._common import (
    JSON_FOOTER as JSON_FOOTER,
)
from wowlab_core.cli._common import (
    NOT_CONFIRMED as NOT_CONFIRMED,
)
from wowlab_core.cli._common import (
    AccountOpt as AccountOpt,
)
from wowlab_core.cli._common import (
    CharacterOpt as CharacterOpt,
)
from wowlab_core.cli._common import (
    CliError as CliError,
)
from wowlab_core.cli._common import (
    ErrorReport as ErrorReport,
)
from wowlab_core.cli._common import (
    FlavorOpt as FlavorOpt,
)
from wowlab_core.cli._common import (
    JsonOpt as JsonOpt,
)
from wowlab_core.cli._common import (
    NotesOpt as NotesOpt,
)
from wowlab_core.cli._common import (
    RootOpt as RootOpt,
)
from wowlab_core.cli._common import (
    Scope as Scope,
)
from wowlab_core.cli._common import (
    YesOpt as YesOpt,
)
from wowlab_core.cli._common import (
    _account_words as _account_words,
)
from wowlab_core.cli._common import (
    _bytes as _bytes,
)
from wowlab_core.cli._common import (
    _characters_in as _characters_in,
)
from wowlab_core.cli._common import (
    _chosen_lab_file as _chosen_lab_file,
)
from wowlab_core.cli._common import (
    _click_error as _click_error,
)
from wowlab_core.cli._common import (
    _client_names as _client_names,
)
from wowlab_core.cli._common import (
    _client_running as _client_running,
)
from wowlab_core.cli._common import (
    _closed_pipe as _closed_pipe,
)
from wowlab_core.cli._common import (
    _confirm as _confirm,
)
from wowlab_core.cli._common import (
    _discover as _discover,
)
from wowlab_core.cli._common import (
    _dumps as _dumps,
)
from wowlab_core.cli._common import (
    _emit as _emit,
)
from wowlab_core.cli._common import (
    _emit_line as _emit_line,
)
from wowlab_core.cli._common import (
    _emit_manifest as _emit_manifest,
)
from wowlab_core.cli._common import (
    _env_flavor as _env_flavor,
)
from wowlab_core.cli._common import (
    _error_kind as _error_kind,
)
from wowlab_core.cli._common import (
    _error_words as _error_words,
)
from wowlab_core.cli._common import (
    _escape as _escape,
)
from wowlab_core.cli._common import (
    _excepthook as _excepthook,
)
from wowlab_core.cli._common import (
    _exception_lines as _exception_lines,
)
from wowlab_core.cli._common import (
    _exception_name as _exception_name,
)
from wowlab_core.cli._common import (
    _fail as _fail,
)
from wowlab_core.cli._common import (
    _first_name_folder as _first_name_folder,
)
from wowlab_core.cli._common import (
    _flavor_list as _flavor_list,
)
from wowlab_core.cli._common import (
    _found_by as _found_by,
)
from wowlab_core.cli._common import (
    _given_flavor as _given_flavor,
)
from wowlab_core.cli._common import (
    _handled as _handled,
)
from wowlab_core.cli._common import (
    _held_words as _held_words,
)
from wowlab_core.cli._common import (
    _hidden_account as _hidden_account,
)
from wowlab_core.cli._common import (
    _home_words as _home_words,
)
from wowlab_core.cli._common import (
    _ignore_sigpipe as _ignore_sigpipe,
)
from wowlab_core.cli._common import (
    _install_words as _install_words,
)
from wowlab_core.cli._common import (
    _is_lab_file as _is_lab_file,
)
from wowlab_core.cli._common import (
    _json_text as _json_text,
)
from wowlab_core.cli._common import (
    _keep_notes as _keep_notes,
)
from wowlab_core.cli._common import (
    _lab_char_file as _lab_char_file,
)
from wowlab_core.cli._common import (
    _later_marker as _later_marker,
)
from wowlab_core.cli._common import (
    _library_log_on_stderr as _library_log_on_stderr,
)
from wowlab_core.cli._common import (
    _listed as _listed,
)
from wowlab_core.cli._common import (
    _lister as _lister,
)
from wowlab_core.cli._common import (
    _local_time as _local_time,
)
from wowlab_core.cli._common import (
    _lua_refusal_words as _lua_refusal_words,
)
from wowlab_core.cli._common import (
    _map_strings as _map_strings,
)
from wowlab_core.cli._common import (
    _new_report as _new_report,
)
from wowlab_core.cli._common import (
    _no_flavors as _no_flavors,
)
from wowlab_core.cli._common import (
    _not_taken_lines as _not_taken_lines,
)
from wowlab_core.cli._common import (
    _not_taken_named as _not_taken_named,
)
from wowlab_core.cli._common import (
    _note as _note,
)
from wowlab_core.cli._common import (
    _open as _open,
)
from wowlab_core.cli._common import (
    _open_char as _open_char,
)
from wowlab_core.cli._common import (
    _os_error_words as _os_error_words,
)
from wowlab_core.cli._common import (
    _Out as _Out,
)
from wowlab_core.cli._common import (
    _PipeSafeStream as _PipeSafeStream,
)
from wowlab_core.cli._common import (
    _PipeState as _PipeState,
)
from wowlab_core.cli._common import (
    _player_copy as _player_copy,
)
from wowlab_core.cli._common import (
    _plural as _plural,
)
from wowlab_core.cli._common import (
    _powershell_join as _powershell_join,
)
from wowlab_core.cli._common import (
    _pre_write_ids as _pre_write_ids,
)
from wowlab_core.cli._common import (
    _print_version as _print_version,
)
from wowlab_core.cli._common import (
    _reader_words as _reader_words,
)
from wowlab_core.cli._common import (
    _render as _render,
)
from wowlab_core.cli._common import (
    _Report as _Report,
)
from wowlab_core.cli._common import (
    _roots as _roots,
)
from wowlab_core.cli._common import (
    _safe as _safe,
)
from wowlab_core.cli._common import (
    _SafeLogFormatter as _SafeLogFormatter,
)
from wowlab_core.cli._common import (
    _say as _say,
)
from wowlab_core.cli._common import (
    _say_err as _say_err,
)
from wowlab_core.cli._common import (
    _say_note as _say_note,
)
from wowlab_core.cli._common import (
    _say_notes as _say_notes,
)
from wowlab_core.cli._common import (
    _say_prose as _say_prose,
)
from wowlab_core.cli._common import (
    _select_account as _select_account,
)
from wowlab_core.cli._common import (
    _select_character as _select_character,
)
from wowlab_core.cli._common import (
    _select_flavor as _select_flavor,
)
from wowlab_core.cli._common import (
    _several_flavors as _several_flavors,
)
from wowlab_core.cli._common import (
    _shell as _shell,
)
from wowlab_core.cli._common import (
    _StderrLogHandler as _StderrLogHandler,
)
from wowlab_core.cli._common import (
    _sv_name as _sv_name,
)
from wowlab_core.cli._common import (
    _Target as _Target,
)
from wowlab_core.cli._common import (
    _text as _text,
)
from wowlab_core.cli._common import (
    _traceback_lines as _traceback_lines,
)
from wowlab_core.cli._common import (
    _traceback_text as _traceback_text,
)
from wowlab_core.cli._common import (
    _typed_args as _typed_args,
)
from wowlab_core.cli._common import (
    _unprintable as _unprintable,
)
from wowlab_core.cli._common import (
    _width as _width,
)
from wowlab_core.cli._common import (
    _with_flavor_lines as _with_flavor_lines,
)
from wowlab_core.cli._common import (
    _WowlabApp as _WowlabApp,
)
from wowlab_core.cli._common import (
    _WowlabContext as _WowlabContext,
)
from wowlab_core.cli._common import (
    _WowlabGroup as _WowlabGroup,
)
from wowlab_core.cli._common import (
    _wtf_file as _wtf_file,
)
from wowlab_core.cli._common import (
    addon_app as addon_app,
)
from wowlab_core.cli._common import (
    addons_app as addons_app,
)
from wowlab_core.cli._common import (
    app as app,
)
from wowlab_core.cli._common import (
    binds_app as binds_app,
)
from wowlab_core.cli._common import (
    char_app as char_app,
)
from wowlab_core.cli._common import (
    cvar_app as cvar_app,
)
from wowlab_core.cli._common import (
    db2_app as db2_app,
)
from wowlab_core.cli._common import (
    filemap_app as filemap_app,
)
from wowlab_core.cli._common import (
    install_app as install_app,
)
from wowlab_core.cli._common import (
    log_app as log_app,
)
from wowlab_core.cli._common import (
    looks_app as looks_app,
)
from wowlab_core.cli._common import (
    macros_app as macros_app,
)
from wowlab_core.cli._common import (
    main as main,
)
from wowlab_core.cli._common import (
    profile_app as profile_app,
)
from wowlab_core.cli._common import (
    snap_app as snap_app,
)
from wowlab_core.cli._common import (
    sv_app as sv_app,
)

_PARTS = (
    _common,
    _cmd_log,
    _cmd_doctor,
    _cmd_install,
    _cmd_filemap,
    _cmd_sv,
    _cmd_cvar,
    _cmd_binds,
    _cmd_macros,
    _cmd_addons,
    _cmd_db2,
    _changes,
    _cmd_snap,
    _cmd_restore,
    _cmd_profile,
    _cmd_addon,
    _cmd_char,
    _cmd_looks,
    _cmd_ui,
)
_UNSET = object()
# The modules a name was deleted from along with the package's, so that the
# next set of that name (a `monkeypatch.delattr` undone) gives it back to them.
_DELETED_FROM: dict[str, list[types.ModuleType]] = {}


class _Package(types.ModuleType):
    """`wowlab_core.cli`: setting or deleting a name here (a test's
    `monkeypatch.setattr(cli, "_confirm", ...)`) does the same in every
    module of the package that holds the same object under that name, so code
    that looks the name up in its own module's globals sees the change, as
    it did when the package was one module (M16-111). A name deleted here and
    set again is set again in the modules it was deleted from."""

    @override
    def __setattr__(self, name: str, value: object) -> None:
        old = self.__dict__.get(name, _UNSET)
        super().__setattr__(name, value)
        if old is _UNSET:
            for part in _DELETED_FROM.pop(name, []):
                setattr(part, name, value)
            return
        for part in _PARTS:
            if part.__dict__.get(name, _UNSET) is old:
                setattr(part, name, value)

    @override
    def __delattr__(self, name: str) -> None:
        old = self.__dict__.get(name, _UNSET)
        super().__delattr__(name)
        if old is _UNSET:
            return
        held = [part for part in _PARTS if part.__dict__.get(name, _UNSET) is old]
        for part in held:
            delattr(part, name)
        if held:
            _DELETED_FROM[name] = held


sys.modules[__name__].__class__ = _Package
