"""`snap export`, and the plans of `snap restore`, `undo` and `sv merge`,
say when they carry the crew book (M19-11; docs/LAB_PLAN.md §20.3, "Other
routes", and §20.5 term 4).

CONSTRUCTED INPUT: every crew book is built at run time by
`parser/_crew_build.py` (ADR-0036 term 3: no real crew record enters the
repository) and appended to the captured account `WowLab.lua` of the
install `test_cli.py` copies into `tmp_path`, with the user data directory
redirected and a process table holding no client. The players, guild,
instance and note are the invented ones of `_crew_build`; every output of
the four commands, text and `--json`, is searched for each of them. Nothing
reads or writes a real install or a real snapshot store.
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import zipfile
from pathlib import Path
from typing import Any

import pytest
from test_cli import (
    SV_DIR,
    _plain,
    flavor,  # noqa: F401
    idle,  # noqa: F401  (autouse: no client in the process table)
    ok,
    replayed,  # noqa: F401  (autouse: recorded game data only)
    root,  # noqa: F401
    run,
    user_data,  # noqa: F401
)

from wowlab_core import crewcount, snaparchive
from wowlab_core.snapshot import Manifest, SnapshotStore

sys.path.insert(0, str(Path(__file__).resolve().parent / "parser"))

from _crew_build import GUILD, KEY_A, KEY_B, KEY_C, KEY_D, NOTE, WHERE, book, player

LAB_FILE = f"{SV_DIR}/WowLab.lua"
OTHER_FILE = f"{SV_DIR}/Syndicator.lua"  # another addon's file, to carry a book by content
CHAR = "Labchard-Labrealmg"
# Everything a record holds that is other people's text or the player's own.
SECRETS = (
    KEY_A,
    KEY_B,
    KEY_C,
    KEY_D,
    GUILD,
    WHERE,
    NOTE,
    *(k.split("-")[0] for k in (KEY_A, KEY_B, KEY_C, KEY_D)),
)

THREE_ON = book(
    {
        KEY_A: player(rating="bad", note=NOTE),
        KEY_B: player(rating="good"),
        KEY_C: player(),
    },
    current=[KEY_A],
)
ONE_ON = book({KEY_A: player(rating="bad", note=NOTE)})
TWO_OFF = book({KEY_C: player(note=NOTE), KEY_D: player()}, on=False)
EMPTY_ON = book({})
UNKNOWN_SCHEMA = book({KEY_A: player(note=NOTE)}, schema=99)


def _out(result: Any) -> str:
    return f"{result.stdout}\n{result.stderr}\n{result.exception!r}"


def _private(*texts: str) -> None:
    """No name, realm, guild, instance or note in any output."""
    for text in texts:
        for secret in SECRETS:
            assert secret not in text, secret
            assert secret.casefold() not in text.casefold(), secret


def _with(flavor: Path, rel: str, crew: bytes | None, extra: bytes = b"") -> None:
    """The captured file at `rel` with `crew` appended (constructed)."""
    path = flavor / rel
    base = path.read_bytes().split(b"\r\n-- constructed", 1)[0]
    tail = b"" if crew is None else crew
    path.write_bytes(base + b"\r\n-- constructed\r\n" + extra + tail)


def _snapshot(label: str) -> Manifest:
    return Manifest.model_validate_json(ok("snap", "create", "-m", label, "--json").stdout)


def _sentence(lines: list[str], start: str) -> list[str]:
    return [line.strip() for line in lines if line.strip().startswith(start)]


# ─── snap export ─────────────────────────────────────────────────────────────


def _export(tmp_path: Path, name: str) -> tuple[str, dict[str, Any], Path, Path]:
    """Export the latest snapshot twice, in text and `--json`; returns both
    outputs and both archives."""
    text_file, json_file = tmp_path / f"{name}.zip", tmp_path / f"{name}-json.zip"
    text = ok("snap", "export", "latest", str(text_file))
    data = ok("snap", "export", "latest", str(json_file), "--json")
    _private(text.stdout, text.stderr, data.stdout, data.stderr)
    return _plain(text.stdout), json.loads(data.stdout), text_file, json_file


@pytest.mark.parametrize(
    ("crew", "rel", "sentence", "players", "on"),
    [
        (THREE_ON, LAB_FILE, "This archive includes the crew book: on, 3 players.", 3, True),
        (
            TWO_OFF,
            LAB_FILE,
            "This archive includes the crew book: turned off, 2 players kept.",
            2,
            False,
        ),
        # Decided from content: another addon's file holding a book counts.
        (ONE_ON, OTHER_FILE, "This archive includes the crew book: on, 1 player.", 1, True),
    ],
    ids=["on_constructed", "off_constructed", "by_content_constructed"],
)
def test_export_says_the_archive_includes_the_crew_book(
    tmp_path: Path,
    flavor: Path,
    crew: bytes,
    rel: str,
    sentence: str,
    players: int,
    on: bool,
) -> None:
    _with(flavor, rel, crew)
    _snapshot("with a book")
    text, report, _, _ = _export(tmp_path, "a")
    assert sentence in text.splitlines()[-1]
    assert text.count("crew book") == 1
    carried = report["crew_book"]
    assert carried == {
        "paths": [f"{flavor.name}/{rel}"],
        "on": on,
        "readable": 1,
        "players": players,
        "unreadable": 0,
    }


def test_export_counts_each_player_once_across_files_constructed(
    tmp_path: Path, flavor: Path
) -> None:
    _with(flavor, LAB_FILE, THREE_ON)
    _with(flavor, OTHER_FILE, TWO_OFF)  # shares no key with THREE_ON but KEY_C
    _snapshot("two books")
    text, report, _, _ = _export(tmp_path, "b")
    assert "This archive includes the crew book: on, 4 players." in text
    assert report["crew_book"]["players"] == 4 and report["crew_book"]["readable"] == 2


@pytest.mark.parametrize(
    "crew", [None, EMPTY_ON], ids=["no_book_constructed", "empty_book_constructed"]
)
def test_export_without_a_book_with_players_says_nothing(
    tmp_path: Path, flavor: Path, crew: bytes | None
) -> None:
    _with(flavor, LAB_FILE, crew)
    _snapshot("no players")
    text, report, _, _ = _export(tmp_path, "c")
    assert "crew" not in text.casefold()
    if crew is None:
        assert report["crew_book"] is None
    else:
        assert report["crew_book"]["players"] == 0  # the JSON still says there is a book


def test_export_names_a_book_it_cannot_read_without_quoting_it_constructed(
    tmp_path: Path, flavor: Path
) -> None:
    _with(flavor, LAB_FILE, UNKNOWN_SCHEMA)
    _snapshot("unknown")
    text, report, _, _ = _export(tmp_path, "d")
    assert (
        "This archive includes a crew book wowlab cannot read, which may hold the players "
        "you grouped with."
    ) in text
    assert report["crew_book"]["unreadable"] == 1 and report["crew_book"]["players"] == 0
    assert "99" not in text.splitlines()[-1]


def test_code_that_names_the_book_is_not_a_book_constructed(tmp_path: Path, flavor: Path) -> None:
    """An addon's code (not data) naming the variable carries no book."""
    addon = flavor / "Interface" / "AddOns" / "Constructed"
    addon.mkdir(parents=True)
    name = crewcount._NAME
    (addon / "Constructed.lua").write_text(
        f"local book = {name} or {{}}\nif not {name} then {name} = {{}} end\n", encoding="utf-8"
    )
    (addon / "Constructed.toc").write_text(
        f"## Interface: 1\n## SavedVariables: {name}\nConstructed.lua\n", encoding="utf-8"
    )
    _snapshot("code only")
    text, report, _, _ = _export(tmp_path, "e")
    assert "crew" not in text.casefold() and report["crew_book"] is None


def test_the_archive_bytes_are_not_changed_by_the_sentence_constructed(
    tmp_path: Path, flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _with(flavor, LAB_FILE, THREE_ON)
    manifest = _snapshot("bytes")
    _, report, text_zip, json_zip = _export(tmp_path, "f")
    assert report["crew_book"] is not None
    # The same export with the crew book never looked at: the same bytes.
    monkeypatch.setattr(crewcount, "names_the_book", lambda data: False)
    blind = tmp_path / "blind.zip"
    blind_report = snaparchive.export_snapshot(SnapshotStore(), manifest.id, blind)
    assert blind_report.crew_book is None
    assert blind.read_bytes() == text_zip.read_bytes() == json_zip.read_bytes()
    # And the archive holds the manifest and the store's objects, nothing more.
    store = SnapshotStore()
    with zipfile.ZipFile(text_zip) as zf:
        names = zf.namelist()
        assert names[0] == snaparchive.MANIFEST_NAME
        sizes = {e.sha256: e.size for e in manifest.entries if e.sha256 is not None}
        assert sorted(names[1:]) == sorted(f"objects/{d}" for d in sizes)
        for digest, size in sizes.items():
            assert zf.read(f"objects/{digest}") == store.read_object(digest, size=size)


def test_one_content_under_several_paths_is_parsed_once_constructed(
    tmp_path: Path, flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same book under five paths: one parse in the export, one per
    distinct content in a plan (counted calls, no time budget)."""
    _with(flavor, LAB_FILE, THREE_ON)
    same = (flavor / LAB_FILE).read_bytes()
    for n in range(4):
        (flavor / SV_DIR / f"Copy{n}.lua").write_bytes(same)
    calls: list[int] = []
    real = crewcount._read_found

    def counted(data: bytes) -> Any:
        calls.append(len(data))
        return real(data)

    monkeypatch.setattr(crewcount, "_read_found", counted)
    manifest = _snapshot("five paths")
    text, report, _, _ = _export(tmp_path, "g")  # two exports: text and --json
    assert "This archive includes the crew book: on, 3 players." in text
    assert len(report["crew_book"]["paths"]) == 5 and report["crew_book"]["readable"] == 5
    assert calls == [len(same)] * 2
    calls.clear()
    files = [(f"p{n}", same) for n in range(5)]
    assert crewcount.carried(files) is not None and calls == [len(same)]
    calls.clear()
    # A plan: five paths written with the same book, none on disk now.
    for n in range(4):
        (flavor / SV_DIR / f"Copy{n}.lua").unlink()
    _with(flavor, LAB_FILE, None)
    lines, _ = _restore_plan(manifest)  # text and --json
    assert _sentence(lines, "This brings back") == [
        "This brings back the crew book: on, 3 players."
    ]
    assert calls == [len(same)] * 2


@pytest.mark.parametrize("where", ["_read_found", "names_the_book", "carried"])
def test_an_error_in_the_crew_check_is_said_as_unreadable_constructed(
    tmp_path: Path, flavor: Path, monkeypatch: pytest.MonkeyPatch, where: str
) -> None:
    """The export still succeeds, with the archive it would have written,
    and says it holds a book it cannot read: no traceback, no silence."""
    _with(flavor, LAB_FILE, THREE_ON)
    manifest = _snapshot("raises")
    good = tmp_path / "good.zip"
    snaparchive.export_snapshot(SnapshotStore(), manifest.id, good)

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("constructed failure in the crew check")

    monkeypatch.setattr(crewcount, where, boom)
    text, report, text_zip, json_zip = _export(tmp_path, "h")
    assert (
        "This archive includes a crew book wowlab cannot read, which may hold the players "
        "you grouped with."
    ) in text
    assert "constructed failure" not in text
    assert report["crew_book"]["unreadable"] >= 1 and report["crew_book"]["players"] == 0
    assert text_zip.read_bytes() == json_zip.read_bytes() == good.read_bytes()


# ─── snap restore ────────────────────────────────────────────────────────────


def _restore_plan(manifest: Manifest) -> tuple[list[str], dict[str, Any]]:
    text = ok("snap", "restore", manifest.id, "--dry-run")
    data = ok("snap", "restore", manifest.id, "--dry-run", "--json")
    _private(text.stdout, text.stderr, data.stdout, data.stderr)
    report = json.loads(data.stdout)
    assert report["plan_lines"] == _plain(text.stdout).splitlines()[:-1]
    return report["plan_lines"], report


@pytest.mark.parametrize(
    ("then", "now", "sentence", "on", "players"),
    [
        (THREE_ON, ONE_ON, "This brings back the crew book: on, 3 players.", True, 3),
        (TWO_OFF, ONE_ON, "This brings back the crew book: turned off, 2 players kept.", False, 2),
        # On disk no book at all: the restore brings one back.
        (THREE_ON, None, "This brings back the crew book: on, 3 players.", True, 3),
    ],
    ids=["on_constructed", "off_constructed", "none_on_disk_constructed"],
)
def test_restore_plan_says_it_brings_back_the_crew_book(
    flavor: Path, then: bytes, now: bytes | None, sentence: str, on: bool, players: int
) -> None:
    _with(flavor, LAB_FILE, then)
    manifest = _snapshot("then")
    _with(flavor, LAB_FILE, now)
    lines, report = _restore_plan(manifest)
    assert _sentence(lines, "This brings back") == [sentence]
    assert report["crew_book"]["on"] is on and report["crew_book"]["players"] == players
    assert report["crew_book"]["paths"] == [LAB_FILE]


@pytest.mark.parametrize(
    ("then", "now"),
    [(None, None), (THREE_ON, THREE_ON)],
    ids=["no_book_constructed", "same_book_constructed"],
)
def test_restore_plan_without_a_book_brought_back_says_nothing(
    flavor: Path, then: bytes | None, now: bytes | None
) -> None:
    """No book, or the same book as on disk with another value changed."""
    _with(flavor, LAB_FILE, then, b"ConstructedValue = 1\r\n")
    manifest = _snapshot("then")
    _with(flavor, LAB_FILE, now, b"ConstructedValue = 2\r\n")
    lines, report = _restore_plan(manifest)
    assert any(LAB_FILE in line for line in lines)  # the file is in the plan
    assert not [line for line in lines if "crew" in line.casefold()]
    assert report["crew_book"] is None


def test_restore_plan_names_a_book_it_cannot_read_constructed(flavor: Path) -> None:
    _with(flavor, LAB_FILE, UNKNOWN_SCHEMA)
    manifest = _snapshot("then")
    _with(flavor, LAB_FILE, ONE_ON)
    lines, report = _restore_plan(manifest)
    assert _sentence(lines, "This brings back") == [
        "This brings back a crew book wowlab cannot read, so it cannot say whether it is on "
        "or how many players it keeps."
    ]
    assert report["crew_book"]["unreadable"] == 1


# ─── undo ────────────────────────────────────────────────────────────────────


def _undo_plan(flavor: Path, then: bytes | None, after: bytes | None, extra: bytes = b"") -> Any:
    """A guarded write that changes the account WowLab.lua from `then` to
    `after` (a restore of a snapshot holding `after`), then undo's plan,
    declined, in text and `--json`."""
    _with(flavor, LAB_FILE, after, extra)
    later = _snapshot("after")
    _with(flavor, LAB_FILE, then)
    ok("snap", "restore", later.id, "--yes", "--paths", LAB_FILE)
    text = run("undo", input="n\n")
    data = run("undo", "--json", input="")
    assert text.exit_code == 1 and data.exit_code == 1, _out(text) + _out(data)
    _private(text.stdout, text.stderr, data.stdout, data.stderr)
    report = json.loads(data.stdout.lstrip())
    assert report["applied"] is False
    return report


@pytest.mark.parametrize(
    ("then", "sentence", "on", "players"),
    [
        (THREE_ON, "This brings back the crew book: on, 3 players.", True, 3),
        (TWO_OFF, "This brings back the crew book: turned off, 2 players kept.", False, 2),
    ],
    ids=["on_constructed", "off_constructed"],
)
def test_undo_plan_says_it_brings_back_the_crew_book(
    flavor: Path, then: bytes, sentence: str, on: bool, players: int
) -> None:
    """An undo of a write that dropped players (as a forget would)."""
    report = _undo_plan(flavor, then, ONE_ON)
    assert _sentence(report["plan_lines"], "This brings back") == [sentence]
    assert report["crew_book"]["on"] is on and report["crew_book"]["players"] == players


def test_undo_plan_without_a_book_says_nothing_constructed(flavor: Path) -> None:
    report = _undo_plan(flavor, None, None, b"ConstructedValue = 2\r\n")
    assert any(LAB_FILE in line for line in report["plan_lines"])
    assert not [line for line in report["plan_lines"] if "crew" in line.casefold()]
    assert report["crew_book"] is None


# ─── sv merge ────────────────────────────────────────────────────────────────


def _merge_plan(
    flavor: Path, then: bytes | None, now: bytes | None, extra: bytes, *more: str
) -> Any:
    """`sv merge` of the account WowLab.lua from a snapshot holding `then`
    into the file now holding `now`, every conflict taken from the
    snapshot (and `more` arguments); the plan declined, in text and
    `--json`."""
    _with(flavor, LAB_FILE, then, extra)
    manifest = _snapshot("then")
    _with(flavor, LAB_FILE, now, b"ConstructedValue = 2\r\n")
    args = (
        "sv",
        "merge",
        LAB_FILE,
        "--into",
        CHAR,
        "--from",
        manifest.id,
        "--take",
        "theirs",
        *more,
    )
    text = run(*args, input="n\n")
    data = run(*args, "--json", input="")
    assert text.exit_code == 1 and data.exit_code == 1, _out(text) + _out(data)
    report = json.loads(data.stdout.lstrip())
    assert report["written"] is False
    # `sv merge` lists the keys it takes and leaves by path, as it always
    # has: showing the records locally is display (§20.5, after the
    # table). What this ticket adds names no one: the sentence and the
    # field are searched, and every line that is not a key path.
    out = _plain(text.stdout + text.stderr)
    _private(
        *_sentence(out.splitlines(), "This brings back"),
        json.dumps(report["crew_book"]),
        *(line for line in out.splitlines() if crewcount._NAME not in line),
    )
    return out, report


@pytest.mark.parametrize(
    ("then", "sentence", "on", "players"),
    [
        (THREE_ON, "This brings back the crew book: on, 3 players.", True, 3),
        (TWO_OFF, "This brings back the crew book: turned off, 2 players kept.", False, 2),
    ],
    ids=["on_constructed", "off_constructed"],
)
def test_merge_plan_says_it_brings_back_the_crew_book(
    flavor: Path, then: bytes, sentence: str, on: bool, players: int
) -> None:
    """The book copied whole from the snapshot, as a player bringing back
    forgotten players would (a two-way merge without it adds no player:
    a key on one side only is never taken)."""
    text, report = _merge_plan(flavor, then, ONE_ON, b"", "--key", crewcount._NAME, "--replace")
    assert _sentence(text.splitlines(), "This brings back") == [sentence]
    assert report["crew_book"]["on"] is on and report["crew_book"]["players"] == players


@pytest.mark.parametrize(
    "book_both", [None, THREE_ON], ids=["no_book_constructed", "same_book_constructed"]
)
def test_merge_plan_that_brings_no_book_back_says_nothing(
    flavor: Path, book_both: bytes | None
) -> None:
    text, report = _merge_plan(flavor, book_both, book_both, b"ConstructedValue = 1\r\n")
    assert "replace" in text  # a plan was shown
    assert "crew" not in text.casefold()
    assert report["crew_book"] is None


# ─── the reader stays unloaded ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "argv",
    [
        ("snap", "export", "--help"),
        ("snap", "restore", "--help"),
        ("undo", "--help"),
        ("sv", "merge", "--help"),
    ],
)
def test_the_four_commands_do_not_load_the_reader_at_startup(argv: tuple[str, ...]) -> None:
    """The crew book's reader is imported only when a file names the book."""
    code = textwrap.dedent(
        f"""
        import sys
        import typer
        from wowlab_core import cli
        root = typer.main.get_command(cli.app)
        try:
            root.main({list(argv)!r}, prog_name="wowlab", standalone_mode=False)
        except SystemExit:
            pass
        print("wowlab_core.crewbook" in sys.modules, "wowlab_core.crewcount" in sys.modules)
        """
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("WOWLAB_")}
    done = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.splitlines()[-1] == "False True"
