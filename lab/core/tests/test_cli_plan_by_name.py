"""Keys, macros and CVars by name in `snap diff` and in the plans of
`snap restore`, `profile apply` and `undo`; one summary line under a
SavedVariables file; the same lines in `--json` as `plan_lines` (M16-10).

The install is the captured tree as `test_cli.py` builds it (copied into
`tmp_path`, the user data directory redirected, the process table a fake).
Real captures stand for the later version of a file where one exists: the
character's `config-cache.wtf` and one-macro `macros-cache.txt` copied over
the account's, and two real sessions' `WowLab.lua` (`macos-70425` and
`macos-70450` rows). The bindings and `Config.wtf` edits, and a bindings
file of 25 keys, are byte-level edits of the capture and the test names
say `constructed` (L8). Nothing reads or writes a real install.
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

import re
import shutil
from pathlib import Path

from test_cli import (
    ACCOUNT,
    CHARACTER,
    FIXTURES,
    _json,
    _plain,
    flavor,  # noqa: F401
    idle,  # noqa: F401  (autouse: no client in the process table)
    ok,
    replayed,  # noqa: F401  (autouse: recorded game data only)
    root,  # noqa: F401
    run,
    user_data,  # noqa: F401
)

from wowlab_core import cli
from wowlab_core.snapshot import Manifest

ACC = f"WTF/Account/{ACCOUNT}"
CHAR = f"{ACC}/1/{CHARACTER}"
BINDINGS = f"{ACC}/bindings-cache.wtf"
CONFIG = f"{ACC}/config-cache.wtf"
MACROS = f"{ACC}/macros-cache.txt"
GLOBAL = "WTF/Config.wtf"
LAB_SV = f"{CHAR}/SavedVariables/WowLab.lua"
SV_EARLY = FIXTURES / "macos-70425/forever/WTF/Account/90000001#6/1/Labchard-Labrealme"
SV_LATER = FIXTURES / "macos-70450/forever/WTF/Account/90000001#6/1/Sangraal-Sett"

KEY_Q = "      key Q: MULTIACTIONBAR1BUTTON1 -> ACTIONBUTTON1"
KEY_F = "      key F: (not in the file) -> INTERACTTARGET"
KEY_E = "      key E: MULTIACTIONBAR1BUTTON3 -> (not in the file)"
MACRO_ADDED = '      macro "PolyArc": added'
CVAR_VIEW = '      CVar cameraView: "5" -> (not in the file)'
CVAR_CLUB = '      CVar lastSelectedClubId: (not in the file) -> ""'
GLOBAL_CVAR = '      CVar useTargetFPS: "0" -> "1"'


def _snapshot() -> Manifest:
    made = ok("snap", "create", "-m", "constructed", "--json")
    return Manifest.model_validate_json(made.stdout)


def _change_wtf(flavor: Path) -> None:
    """The later session: a key rebound, one added, one gone (constructed);
    the character's CVars and macros in the account's files (real); one
    CVar of Config.wtf changed (constructed)."""
    b = flavor / BINDINGS
    b.write_bytes(
        b.read_bytes()
        .replace(b"bind Q MULTIACTIONBAR1BUTTON1\r\n", b"bind Q ACTIONBUTTON1\r\n")
        .replace(b"bind E MULTIACTIONBAR1BUTTON3\r\n", b"bind F INTERACTTARGET\r\n")
    )
    shutil.copyfile(flavor / CHAR / "config-cache.wtf", flavor / CONFIG)
    shutil.copyfile(flavor / CHAR / "macros-cache.txt", flavor / MACROS)
    g = flavor / GLOBAL
    g.write_bytes(g.read_bytes().replace(b'SET useTargetFPS "0"', b'SET useTargetFPS "1"'))


def _under(lines: list[str], header_end: str) -> list[str]:
    """The indented detail lines right under the line ending with `header_end`."""
    (at,) = [i for i, ln in enumerate(lines) if ln.split("  (")[0].endswith(header_end)]
    out = []
    for ln in lines[at + 1 :]:
        if not ln.startswith("      "):
            break
        out.append(ln)
    return out


def test_snap_diff_names_keys_macros_and_cvars_constructed(root: Path, flavor: Path) -> None:
    a = _snapshot()
    _change_wtf(flavor)
    b = _snapshot()

    text = _plain(ok("snap", "diff", a.id, b.id).stdout).splitlines()
    assert _under(text, BINDINGS) == [KEY_Q, KEY_F, KEY_E]
    assert _under(text, MACROS) == [MACRO_ADDED]
    assert _under(text, GLOBAL) == [GLOBAL_CVAR]
    cvars = _under(text, CONFIG)
    assert len(cvars) == 21 + 30  # every name of each file differs
    assert CVAR_VIEW in cvars and CVAR_CLUB in cvars

    report = _json(cli.DiffReport, "snap", "diff", a.id, b.id)
    assert report.plan_lines == text


def test_snap_diff_summarises_a_saved_variables_file(root: Path, flavor: Path) -> None:
    sv = flavor / LAB_SV
    sv.parent.mkdir()
    shutil.copyfile(SV_EARLY / "SavedVariables/WowLab.lua", sv)
    a = _snapshot()
    shutil.copyfile(SV_LATER / "SavedVariables/WowLab.lua", sv)
    b = _snapshot()

    text = _plain(ok("snap", "diff", a.id, b.id).stdout).splitlines()
    detail = _under(text, LAB_SV)
    summary = detail[0]
    m = re.fullmatch(
        r"      values: (\d+) removed, (\d+) added, (\d+) changed; "
        r"size 44,200 bytes -> 44,218 bytes",
        summary,
    )
    assert m, summary
    removed, added, changed = (int(x) for x in m.groups())
    values = detail[1:]
    assert removed == sum(ln.startswith("      - ") for ln in values) > 0
    assert added == sum(ln.startswith("      + ") for ln in values)
    assert changed == sum(ln.startswith("      ~ ") for ln in values) > 0
    assert len(values) == removed + added + changed

    # The restore plan reads the same files the other way round, by another
    # path (the disk and the store, not the diff): its summary agrees.
    plan = _plain(ok("snap", "restore", a.id, "--dry-run").stdout).splitlines()
    assert _under(plan, LAB_SV) == [
        f"      values: {added} removed, {removed} added, {changed} changed; "
        "size 44,218 bytes -> 44,200 bytes"
    ]


def test_restore_plan_then_undo_plan_name_the_changes_constructed(root: Path, flavor: Path) -> None:
    a = _snapshot()
    _change_wtf(flavor)

    out = _plain(ok("snap", "restore", a.id, "--dry-run").stdout).splitlines()
    assert out[-1] == "Dry run: nothing was changed."
    # Back to the snapshot: the other way round from the diff.
    assert _under(out, BINDINGS) == [
        "      key Q: ACTIONBUTTON1 -> MULTIACTIONBAR1BUTTON1",
        "      key E: (not in the file) -> MULTIACTIONBAR1BUTTON3",
        "      key F: INTERACTTARGET -> (not in the file)",
    ]
    assert _under(out, MACROS) == ['      macro "PolyArc": removed']
    assert _under(out, GLOBAL) == ['      CVar useTargetFPS: "1" -> "0"']
    cvars = _under(out, CONFIG)
    # A plan names 20 per file, then a count.
    assert len(cvars) == 21
    assert cvars[-1] == "      ... and 31 more (snap diff lists them all)"

    plan = _json(cli.RestoreReport, "snap", "restore", a.id, "--dry-run")
    assert plan.plan_lines == out[:-1]

    done = _json(cli.RestoreReport, "snap", "restore", a.id, "--yes")
    assert done.applied and done.plan_lines == out[:-1]

    declined = run("undo", input="n\n")  # the plan, then no: exit 1, nothing undone
    assert declined.exit_code == 1, (declined.stdout, declined.stderr)
    lines = _plain(declined.stdout).splitlines()
    assert _under(lines, BINDINGS) == [KEY_Q, KEY_F, KEY_E]
    assert _under(lines, MACROS) == [MACRO_ADDED]
    assert _under(lines, GLOBAL) == [GLOBAL_CVAR]
    report = cli.UndoReport.model_validate_json(ok("undo", "--json", "--yes").stdout)
    assert report.applied
    assert report.plan_lines == lines[: len(report.plan_lines)]
    assert len(report.plan_lines) >= len(lines) - 1  # all but the answer's echo
    assert _under(report.plan_lines, BINDINGS) == [KEY_Q, KEY_F, KEY_E]


def test_profile_apply_plan_names_the_keys_constructed(root: Path, flavor: Path) -> None:
    ok("profile", "save", "keys", "--subtree", BINDINGS)
    b = flavor / BINDINGS
    b.write_bytes(b.read_bytes().replace(b"bind Q MULTIACTIONBAR1BUTTON1\r\n", b""))

    out = _plain(ok("profile", "apply", "keys", "--dry-run").stdout).splitlines()
    assert _under(out, BINDINGS) == ["      key Q: (not in the file) -> MULTIACTIONBAR1BUTTON1"]
    report = _json(cli.ProfileApplyReport, "profile", "apply", "keys", "--dry-run")
    assert report.plan_lines == out[:-1]


def test_order_only_change_says_nothing_changes_by_name_constructed(
    root: Path, flavor: Path
) -> None:
    a = _snapshot()
    b = flavor / BINDINGS
    b.write_bytes(b.read_bytes().replace(b"\r\n", b"\n"))
    out = _plain(ok("snap", "restore", a.id, "--dry-run").stdout).splitlines()
    assert _under(out, BINDINGS) == [
        "      nothing changes by name (only the order, line endings or macro ids differ)"
    ]


def test_a_file_that_is_not_a_wtf_text_file_gets_no_detail(root: Path, flavor: Path) -> None:
    a = _snapshot()
    other = flavor / CHAR / "chat-cache.txt"
    other.write_bytes(other.read_bytes() + b"constructed\n")
    out = _plain(ok("snap", "restore", a.id, "--dry-run").stdout).splitlines()
    assert _under(out, f"{CHAR}/chat-cache.txt") == []
