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

import json
import os
import re
from collections.abc import Callable
from pathlib import Path, PureWindowsPath
from typing import Any

import platformdirs
import pytest
from test_cli_player_copy import Install, hits, sandbox
from typer.testing import CliRunner

import wowlab_core
from wowlab_core import bindscoach, cli, gear, snapshot

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
        # Who else can open the store depends on the platform (modes are
        # POSIX's; Windows adds a note) and the umask: one answer, so each
        # snapshot is the same on every machine. That check's own tests are
        # in test_snapshot_store_modes.py (M16-41).
        monkeypatch.setattr(
            snapshot.SnapshotStore,
            "exposure",
            lambda self: snapshot.StoreExposure(checked=True, count=0, entries=()),
        )
        return root

    return build


def run(args: tuple[str, ...] | list[str], width: int = WIDTH) -> Any:
    result = runner.invoke(cli.app, list(args), terminal_width=width)
    assert result.exception is None or isinstance(result.exception, SystemExit), result.exception
    return result


def _known_paths(tmp_path: Path) -> list[str]:
    """Every path a run on the sandbox can print: each file and folder under
    `tmp_path` (the install copy, the store, the user folders), the two the
    output names before they exist, and the package's own files."""
    config = tmp_path / "userconfig" / "wowlab"
    found = [tmp_path, *tmp_path.rglob("*"), tmp_path / "userdata" / "wowlab" / "store"]
    found += [config / gear.SLOTS_FILE, config / bindscoach.COSTS_FILE]
    package = Path(PACKAGE)
    found += [package, *package.iterdir()]
    return [str(p) for p in found]


def _placeholders(text: str, tmp_path: Path, paths: list[str] | None = None) -> str:
    """The machine's folders as placeholders, each known path in forward
    slashes. Windows prints `C:\\...\\t0\\World of Warcraft`, and `--json`
    escapes each backslash: both spellings of each known path (`paths`, by
    default `_known_paths`, and the `~\\...` spelling of one in the home
    folder, which is `tmp_path` here) are rewritten, and nothing else is, so
    an escape after a path (`\\n`, `\\x85`) is kept. On Linux no known path
    holds a backslash: only the placeholders change."""
    tmp = str(tmp_path)
    known = set(_known_paths(tmp_path) if paths is None else paths)
    known |= {
        "~" + s[len(tmp) :] for s in known if s.startswith(tmp) and s[len(tmp) :][:1] in "\\/"
    }
    for real in sorted((s for s in known if "\\" in s), key=len, reverse=True):
        slashed = real.replace("\\", "/")
        text = text.replace(real.replace("\\", "\\\\"), slashed).replace(real, slashed)
    for real, placeholder in ((PACKAGE, "<package>"), (tmp, "<tmp>")):
        text = text.replace(real.replace("\\", "/"), placeholder)
    # A `\` the run printed after a placeholder is the run's own: kept, so a
    # difference there is not hidden.
    return _LOCAL_TIME.sub("<local time>", text)


def test_placeholders_put_windows_paths_in_forward_slashes_constructed() -> None:
    """Constructed: the spellings a Windows run prints, in text and in JSON."""
    tmp = Path(str(PureWindowsPath("C:/Users/x/AppData/Local/Temp/pytest-1/t0")))
    root = PureWindowsPath(str(tmp), "World of Warcraft")
    store = PureWindowsPath(str(tmp), "userdata", "wowlab", "store")
    config = PureWindowsPath(str(tmp), "userconfig", "wowlab", "gear_slots.toml")
    user = PureWindowsPath("~", "userconfig", "wowlab", "gear_slots.toml")
    known = [str(tmp), str(root), str(store), str(config)]
    text = f"Install: {root} (found by --root)\nSnapshot store: {store}\n  - put them in {user} (x)"
    assert _placeholders(text, tmp, known) == (
        "Install: <tmp>/World of Warcraft (found by --root)\n"
        "Snapshot store: <tmp>/userdata/wowlab/store\n"
        "  - put them in ~/userconfig/wowlab/gear_slots.toml (x)"
    )
    as_json = json.dumps({"path": str(store), "root": str(root)})
    assert json.loads(_placeholders(as_json, tmp, known)) == {
        "path": "<tmp>/userdata/wowlab/store",
        "root": "<tmp>/World of Warcraft",
    }
    # An escape after a path is kept (the review probe's cases), in JSON and
    # in text, and so is a later path that is not one of the known ones.
    after = json.dumps({"error": f"{store}\nsecond line", "other": "D:\\elsewhere\\x"})
    assert json.loads(_placeholders(after, tmp, known)) == {
        "error": "<tmp>/userdata/wowlab/store\nsecond line",
        "other": "D:\\elsewhere\\x",
    }
    escaped = f"File: {root}\\x85bar and {root}\\foo\\x85"
    assert _placeholders(escaped, tmp, known) == (
        "File: <tmp>/World of Warcraft\\x85bar and <tmp>/World of Warcraft\\foo\\x85"
    )
    # A Linux run is unchanged, slashes, "/reload" and a lone "~" included.
    plain = "Install: <tmp>/World of Warcraft (x) then /reload; ~ alone\\n"
    assert _placeholders(plain, Path("/nowhere")) == plain
    linux = json.dumps({"error": "/nowhere/x\nsecond", "file": "/nowhere/W o W/foo\\x85"})
    assert _placeholders(linux, Path("/nowhere")) == linux.replace("/nowhere", "<tmp>")


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


# A character whose realm folder holds spaces (the `macos` capture's), with
# no record: its path is in the prose of each command's Record and
# cannot-judge lines.
SPACED = "Labrealmb Partb Partc Partd/Labchard"
WRAP_CASES: list[Case] = [
    *CASES,
    ("macos", ("char", "binds", "--character", SPACED)),
    ("macos", ("char", "macros", "--character", SPACED)),
    ("macos", ("binds", "coach", "--character", SPACED)),
]


def _held_path_overflows(line: str, width: int) -> bool:
    """Whether `line` is over `width` only because it holds a path that
    cannot be broken. textwrap (words never broken) lets a line run long
    only when it is one token, alone after its indent, wider than the room:
    that token must be a held path, with a slash inside it ("/reload" or
    "/combatlog" alone is never one) and a realm folder's spaces kept."""
    body = line.lstrip(" ").replace(SPACED, SPACED.replace(" ", "\x00"))
    return len(line) > width and " " not in body and "/" in body[1:]


@pytest.mark.parametrize("case", WRAP_CASES, ids=[_case_id(c) for c in WRAP_CASES])
def test_prose_wraps_to_the_terminal_width_and_never_inside_a_path(
    case: Case, make: Callable[[str], Path]
) -> None:
    """Every line of prose fits the width given. A line longer than that is
    either a data line (a key row, a slot row, a file line), printed the same
    at any width, or one holding a path, which is never broken: a realm
    folder's spaces stay on one line."""
    overlay, args = case
    make(overlay)
    _past_first_run(args)
    narrow = run([*args, "--notes"], width=50).stdout
    wide = run([*args, "--notes"], width=200).stdout
    wide_lines = set(wide.splitlines())
    for line in narrow.splitlines():
        assert len(line) <= 50 or line in wide_lines or _held_path_overflows(line, 50), line
    assert " ".join(narrow.split()) == " ".join(wide.split())
    assert len(narrow.splitlines()) > len(wide.splitlines())
    for text in (narrow, wide):
        flat = " ".join(text.split())
        assert text.count(SPACED) == flat.count(SPACED)
    if SPACED in args:
        assert SPACED in narrow


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
    out = run(["doctor", "--offline"], width=1000).stdout
    synced = [line for line in out.splitlines() if line.startswith("  Server sync")]
    assert synced == [
        "  Server sync: the three sync settings wowlab reads are not set in WTF/Config.wtf or "
        "WTF/Account/90000001#6/config-cache.wtf (when on, the server can replace your "
        "bindings, macros or settings at login; with no line, the client may treat a setting "
        "as on or may not have it at all, and wowlab cannot tell which)"
    ]
    assert "synchronizeBindings" not in out


def test_doctor_with_problems_matches_its_snapshot_constructed(
    make: Callable[[str], Path], tmp_path: Path
) -> None:
    """Constructed (boundary): a manifest in the store that does not load.
    The footer comes after the problems (conductor's ruling, 2026-11-06),
    and after the next steps that end the report (M16-37, LAB_PLAN §18.1
    item 5); doctor exits 1, as `problems` in --json says."""
    make("macos")
    _past_first_run(("doctor",))
    manifests = tmp_path / "userdata" / "wowlab" / "store" / "manifests"
    (manifests / "constructed-bad.json").write_text("{", encoding="utf-8")
    result = run(["doctor", "--offline"])
    assert result.exit_code == 1
    lines = result.stdout.splitlines()
    assert FOOTER.fullmatch(lines[-1])
    steps = lines.index("Next steps:")
    assert lines[steps - 2 : steps] == ["Problems:", "  1 damaged snapshot manifest(s)"]
    assert all(line.startswith("  ") for line in lines[steps + 1 : -1])
    _snapshot(
        "macos-doctor-problems-constructed.txt",
        # Why the manifest does not load is the JSON library's message, which
        # names its own version: not wowlab's words to pin.
        re.sub(
            r"^(  damaged: constructed-bad\.json: ).*$",
            r"\1<why it does not load>",
            f"exit {result.exit_code}\n{_placeholders(result.stdout, tmp_path)}",
            flags=re.MULTILINE,
        ),
    )


def test_doctor_lists_sync_lines_when_one_is_set(make: Callable[[str], Path]) -> None:
    root = make("macos")
    config = root / "_classic_beta_" / "WTF" / "Config.wtf"
    config.write_bytes(config.read_bytes() + b'SET synchronizeBindings "1"\r\n')
    out = run(["doctor", "--offline"], width=1000).stdout
    assert (
        "  Server sync (when on, the server can replace your bindings, macros or settings at "
        "login):\n" in out
    )
    assert 'WTF/Config.wtf: synchronizeBindings "1"' in out
    assert "synchronizeMacros not set (the client may treat it as on or may not have it" in out


def test_coach_says_cannot_judge_where_a_count_would_be_0(make: Callable[[str], Path]) -> None:
    """A schema-1 record has no bars or bindings: each of the three reports
    says "cannot judge", not 0, and the reason is said once in full."""
    make("macos")
    out = run(["binds", "coach"], width=1000).stdout
    short = "cannot judge (no action-bar or bindings record)"
    assert f"Frequent spells on high-cost keys: {short}" in out
    assert f"Spells cast with no bind: {short}" in out
    assert f"Binds to spells not cast in this log: {short}" in out
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
