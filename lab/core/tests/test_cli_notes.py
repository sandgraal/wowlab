"""Answers first, notes behind `--notes` (M16-16, docs/LAB_PLAN.md §6.11).

`char gear`, `char binds`, `char macros`, `macros check`, `binds coach` and
`doctor` print the data, then at most one caveat that applies to this
result, then one footer line counting the standing notes ("N notes: --notes
shows them"). With `--notes` the notes are printed instead of the footer.
Prose wraps to the terminal width; no absolute path to wowlab's own files
is printed; `--json` is what it was before this change.

The text of each command on the fixture sandbox is compared with a snapshot
file under `tests/snapshots/notes/`. The `--json` snapshots there were
written by `main`'s code before this change (4f1bb29), so they prove that
`--json` is byte for byte as it was. Before a comparison the sandbox's
folder is put back as `<tmp>`, the package folder as `<package>` and a local
time as `<local time>`; every file time in the sandbox is one fixed
instant, so the character taken without --character is the same on every
machine. `WOWLAB_UPDATE_SNAPSHOTS=1` rewrites the snapshots instead.

The sandbox is `test_cli_player_copy.sandbox`: the captured trees copied
into `tmp_path`, the user data directory redirected, no client running and
wago.tools replayed. The user config directory is redirected too, and the
home folder is `tmp_path`, so a path under it prints the same everywhere.
Nothing here reads or writes a real install.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import platformdirs
import pytest
from test_cli_player_copy import Install, hits, sandbox
from typer.testing import CliRunner

import wowlab_core
from wowlab_core import cli

SNAPSHOTS = Path(__file__).resolve().parent / "snapshots" / "notes"
UPDATE = os.environ.get("WOWLAB_UPDATE_SNAPSHOTS") == "1"
MTIME_NS = 1_700_000_000_000_000_000
WIDTH = 80
PACKAGE = str(Path(wowlab_core.__file__).resolve().parent)
_LOCAL_TIME = re.compile(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d [+-]\d{4}")
FOOTER = re.compile(r"(\d+) notes?: --notes shows (?:them|it)")

Case = tuple[str, tuple[str, ...]]

# (overlay, command): each command on the sandbox, with a character of each
# kind the overlays hold (schema 1, schema 4, no record).
CASES: list[Case] = [
    ("macos", ("doctor", "--offline")),
    ("macos-70450", ("doctor", "--offline")),
    ("macos", ("char", "gear")),
    ("macos-70450", ("char", "gear", "--character", "1/Dangling-Participle")),
    ("macos", ("char", "binds")),
    ("macos", ("char", "binds", "--character", "1/Labcharb-Labrealmd")),
    ("macos-70450", ("char", "binds", "--character", "1/Dangling-Participle")),
    ("macos", ("char", "macros")),
    ("macos", ("char", "macros", "--character", "1/Labcharb-Labrealmd")),
    ("macos-70450", ("char", "macros", "--character", "1/Sangraal-Sett")),
    ("macos", ("macros", "check")),
    ("macos", ("macros", "check", "--character", "1/Labcharb-Labrealmd")),
    ("macos-70450", ("macros", "check", "--character", "1/Dangling-Participle")),
    ("macos", ("binds", "coach")),
    ("macos", ("binds", "coach", "--character", "1/Labcharb-Labrealmd")),
    ("macos-70450", ("binds", "coach", "--character", "1/Dangling-Participle")),
]
COMMANDS = [("doctor",), ("char", "gear"), ("char", "binds"), ("char", "macros")]
COMMANDS += [("macros", "check"), ("binds", "coach")]

runner = CliRunner()


def _case_id(case: Case) -> str:
    overlay, args = case
    words = [a.split("/")[-1] for a in args if a not in ("--character", "--offline")]
    return "-".join([overlay, *words])


CASE_IDS = [_case_id(c) for c in CASES]


@pytest.fixture
def make(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Callable[[str], Path]:
    """The sandbox with `overlay`, every file time one instant."""

    def build(overlay: str) -> Path:
        root = sandbox(tmp_path, monkeypatch, overlay)
        for p in [root, *root.rglob("*")]:
            if not p.is_symlink():
                os.utime(p, ns=(MTIME_NS, MTIME_NS))
        config = tmp_path / "userconfig" / "wowlab"
        monkeypatch.setattr(platformdirs, "user_config_path", lambda *a, **k: config)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
        return root

    return build


def run(args: tuple[str, ...] | list[str], width: int = WIDTH) -> Any:
    result = runner.invoke(cli.app, list(args), terminal_width=width)
    assert result.exception is None or isinstance(result.exception, SystemExit), result.exception
    return result


def _placeholders(text: str, tmp_path: Path) -> str:
    text = text.replace(PACKAGE, "<package>").replace(str(tmp_path), "<tmp>")
    return _LOCAL_TIME.sub("<local time>", text)


def _snapshot(name: str, actual: str) -> None:
    path = SNAPSHOTS / name
    if UPDATE:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(actual, encoding="utf-8", newline="\n")
    assert path.is_file(), f"no snapshot {path.name}; run with WOWLAB_UPDATE_SNAPSHOTS=1"
    assert actual == path.read_text(encoding="utf-8")


def _past_first_run(args: tuple[str, ...]) -> None:
    """`doctor` is shown past its first run: the first run's intro is its
    own text (and the first-run tests'), and it names the store's folder,
    whose length differs from machine to machine, in its prose."""
    if args[0] == "doctor":
        assert run(["snap", "create", "-m", "first"]).exit_code == 0


# ─── the text ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_text_matches_its_snapshot(case: Case, make: Callable[[str], Path], tmp_path: Path) -> None:
    overlay, args = case
    make(overlay)
    _past_first_run(args)
    result = run(args)
    text = f"exit {result.exit_code}\n{_placeholders(result.stdout, tmp_path)}"
    _snapshot(f"{_case_id(case)}.txt", text)


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_notes_text_matches_its_snapshot_and_the_footer_counts_them(
    case: Case, make: Callable[[str], Path], tmp_path: Path
) -> None:
    """With --notes the output is the plain output with its footer replaced
    by the notes, as many as the footer counted, under one heading."""
    overlay, args = case
    root = make(overlay)
    _past_first_run(args)
    plain = run(args).stdout
    with_notes = run([*args, "--notes"])
    _snapshot(
        f"{_case_id(case)}.notes.txt",
        f"exit {with_notes.exit_code}\n{_placeholders(with_notes.stdout, tmp_path)}",
    )
    lines = plain.splitlines()
    footer = FOOTER.fullmatch(lines[-1])
    assert footer is not None, lines[-1]
    head = plain[: plain.rindex(lines[-1])]
    assert with_notes.stdout.startswith(head + "Notes:\n")
    items = with_notes.stdout[len(head) :].splitlines()[1:]
    assert sum(1 for line in items if line.startswith("  - ")) == int(footer.group(1))
    assert all(line.startswith(("  - ", "    ")) for line in items), items
    assert hits(with_notes.stdout, Install.of(root)) == []


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_at_most_one_caveat_comes_between_the_data_and_the_footer(
    case: Case, make: Callable[[str], Path]
) -> None:
    """The footer is the last line, and every standing note is behind it:
    no note of the command's own list is printed by default except the one
    caveat."""
    overlay, args = case
    make(overlay)
    _past_first_run(args)
    plain = run(args).stdout
    assert FOOTER.fullmatch(plain.splitlines()[-1])
    noted = run([*args, "--notes"]).stdout
    notes = noted[noted.rindex("\nNotes:\n") + len("\nNotes:\n") :]
    flat = " ".join(plain.split())
    for item in notes.split("\n  - "):
        words = " ".join(item.removeprefix("  - ").split())
        assert words not in flat, words


# ─── --json is unchanged ─────────────────────────────────────────────────────


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_json_is_unchanged(case: Case, make: Callable[[str], Path], tmp_path: Path) -> None:
    """`--json` matches the snapshot `main` wrote before this change, and
    --notes does not change it."""
    overlay, args = case
    make(overlay)
    _past_first_run(args)
    as_json = run([*args, "--json"])
    assert as_json.exit_code == 0, as_json.stderr  # as on `main`, for every case here
    _snapshot(f"{_case_id(case)}.json", _placeholders(as_json.stdout, tmp_path))
    if not UPDATE:
        assert run([*args, "--json", "--notes"]).stdout == as_json.stdout


# ─── the rules ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("path", COMMANDS, ids=" ".join)
def test_notes_option_is_in_help(path: tuple[str, ...]) -> None:
    out = runner.invoke(cli.app, [*path, "--help"], terminal_width=200).stdout
    assert "--notes" in out
    assert "Also print the standing notes" in out


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_prose_wraps_to_the_terminal_width(case: Case, make: Callable[[str], Path]) -> None:
    """The notes, caveat and footer fit the width given; data lines (a key
    row, a slot row, a path) are left whole, and are the same at any width."""
    overlay, args = case
    make(overlay)
    _past_first_run(args)
    narrow = run([*args, "--notes"], width=50).stdout
    wide = run([*args, "--notes"], width=200).stdout
    notes = narrow[narrow.rindex("\nNotes:\n") :].splitlines()[2:]
    assert notes
    for line in notes:
        assert len(line) <= 50 or " " not in line.strip(), line
    assert " ".join(narrow.split()) == " ".join(wide.split())
    assert len(narrow.splitlines()) > len(wide.splitlines())


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_no_absolute_path_of_wowlabs_own_files(
    case: Case, make: Callable[[str], Path], tmp_path: Path
) -> None:
    """No shipped table's path (the package folder) and no path under the
    home folder: the user config file is named as `~/...`, with --notes."""
    overlay, args = case
    make(overlay)
    _past_first_run(args)
    for out in (run(args).stdout, run([*args, "--notes"]).stdout):
        assert PACKAGE not in out
        assert str(tmp_path / "userconfig") not in out


def test_doctor_folds_six_unset_sync_lines_into_one(make: Callable[[str], Path]) -> None:
    make("macos")
    out = run(["doctor", "--offline"], width=200).stdout
    synced = [line for line in out.splitlines() if line.startswith("  Server sync")]
    assert synced == [
        "  Server sync: not set in WTF/Config.wtf or WTF/Account/90000001#6/config-cache.wtf "
        "(the client's default, and wowlab cannot tell whether that default is on)"
    ]
    assert "synchronizeBindings" not in out


def test_doctor_lists_sync_lines_when_one_is_set(make: Callable[[str], Path]) -> None:
    root = make("macos")
    config = root / "_classic_beta_" / "WTF" / "Config.wtf"
    config.write_bytes(config.read_bytes() + b'SET synchronizeBindings "1"\r\n')
    out = run(["doctor", "--offline"]).stdout
    assert "  Server sync:\n" in out
    assert 'WTF/Config.wtf: synchronizeBindings "1"' in out
    assert "synchronizeMacros not set (the client's default" in out


def test_coach_says_cannot_judge_where_a_count_would_be_0(make: Callable[[str], Path]) -> None:
    """A schema-1 record has no bars or bindings: each of the three reports
    says "cannot judge", not 0, and the reason is said once in full."""
    make("macos")
    out = run(["binds", "coach"], width=200).stdout
    short = "cannot judge (no action-bar or bindings record)"
    assert f"Frequent spells on high-cost keys: {short}" in out
    assert f"Spells cast with no bind: {short}" in out
    assert re.search(rf"Binds to spells not cast in this log \(.*\): {re.escape(short)}", out)
    assert out.count("Cannot judge because: no action-bar or bindings record (schema 1") == 1
    assert not re.search(r"^(Frequent spells|Spells cast|Binds to spells).*: 0$", out, re.M)


def test_coach_keeps_its_counts_when_it_can_judge(make: Callable[[str], Path]) -> None:
    make("macos-70450")
    out = run(["binds", "coach", "--character", "1/Dangling-Participle"], width=200).stdout
    assert "Frequent spells on high-cost keys: 0\n" in out
    assert "Spells cast with no bind: 4\n" in out
    assert "Cannot judge" not in out


def test_short_reason_constructed() -> None:
    """Constructed reasons: cut at the first explanation."""
    assert cli._short_reason("no lab-addon record: no WowLab.lua in 1/X") == "no lab-addon record"
    assert cli._short_reason("no combat log under Logs/ (the client ...)") == (
        "no combat log under Logs/"
    )
    assert cli._short_reason("the log has none of your casts, so every bound spell") == (
        "the log has none of your casts"
    )
    assert cli._short_reason("plain") == "plain"
