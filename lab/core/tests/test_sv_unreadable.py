"""`wowlab sv list --unreadable` (M16-131, docs/LAB_PLAN.md §19 CW-4
amendment): every SavedVariables file read as data by `luadata`, never run,
and the ones that do not read listed with where and why.

The install is the captured tree (`fixtures/macos/`, rows in
`fixtures/README.md`, L8) copied into `tmp_path`; every one of its
SavedVariables files reads. The broken files (cut off inside a table, a
function, a byte-order mark, past a size bound, one that cannot be opened)
are constructed, written into the copy, and their test ids say so. Nothing
reads or writes a real install, the user data directory is redirected and
the process table is a fake with no client.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import platformdirs
import psutil
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, install, luadata

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CAPTURE = FIXTURES / "macos"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder; tests may name it (L6)
ACCOUNT = "90000001#6"
SV_DIR = f"WTF/Account/{ACCOUNT}/SavedVariables"
CUT = f"{SV_DIR}/Cutoff.lua"
RUNS = f"{SV_DIR}/Runs.lua"

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    redirected = tmp_path / "userdata" / "wowlab"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: redirected)
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: iter(()))


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "World of Warcraft"
    root.mkdir()
    shutil.copy2(CAPTURE / ".build.info", root / ".build.info")
    shutil.copytree(CAPTURE / "forever", root / FLAVOR)
    monkeypatch.setenv(install.ENV_ROOT, str(root))
    monkeypatch.chdir(tmp_path)
    return root


@pytest.fixture
def flavor(root: Path) -> Path:
    return root / FLAVOR


def _broken(flavor: Path) -> None:
    """Constructed: two account files that do not read."""
    (flavor / CUT).write_bytes(b'Cutoff = {\n  ["a"] = 1,\n')
    (flavor / RUNS).write_bytes(b"Runs = function() end\n")


def ok(*args: str) -> str:
    result = runner.invoke(cli.app, list(args), terminal_width=80)
    assert result.exit_code == 0, (result.stdout, result.stderr, result.exception)
    assert result.stderr == ""
    return result.stdout


def _unreadable(*args: str) -> cli.SvListReport:
    return cli.SvListReport.model_validate_json(ok("sv", "list", "--unreadable", "--json", *args))


def _state(root: Path) -> dict[str, tuple[bytes | None, int]]:
    out: dict[str, tuple[bytes | None, int]] = {}
    for p in sorted(root.rglob("*")):
        st = p.lstat()
        out[p.relative_to(root).as_posix()] = (
            p.read_bytes() if p.is_file() else None,
            st.st_mtime_ns,
        )
    return out


def test_every_real_file_reads(root: Path) -> None:
    report = _unreadable()
    assert report.unreadable is True and report.orphans is False
    assert report.files == [] and report.read_failures == []
    every = cli.SvListReport.model_validate_json(ok("sv", "list", "--json"))
    assert len(every.files) == 10
    assert every.unreadable is False and every.read_failures == []
    out = ok("sv", "list", "--unreadable")
    assert out.splitlines() == [
        "wowlab read all 10 SavedVariables files as saved data.",
        "2 notes: --notes shows them",
    ]


def test_broken_files_are_listed_with_where_and_why_constructed(root: Path, flavor: Path) -> None:
    _broken(flavor)
    report = _unreadable()
    assert [f.path for f in report.files] == [CUT, RUNS]
    cut, runs = report.read_failures
    assert (cut.path, cut.kind, cut.line, cut.column) == (CUT, "refused", 1, 10)
    assert cut.reason == (
        "the file ends before the table that opens at line 1, column 10 is closed: it may "
        "have been cut off by a crash or a full disk during a save"
    )
    assert cut.detail.startswith("line 1, column 10: unterminated table")
    assert (runs.path, runs.kind, runs.line, runs.column) == (RUNS, "refused", 1, 8)
    assert runs.reason.startswith("it holds a function, which saved data never does")
    # The caveat for this result first; the JSON keeps the spec's marker.
    assert report.notes[0] == cli._UNREADABLE_LOSS
    assert report.notes[1:3] == [cli._UNREADABLE_WHAT, cli._SV_TIMING]


def test_text_answers_first_with_one_caveat_constructed(root: Path, flavor: Path) -> None:
    _broken(flavor)
    out = ok("sv", "list", "--unreadable")
    lines = out.splitlines()
    assert lines[0] == "2 of 12 SavedVariables files are not saved data wowlab can read:"
    assert lines[1].startswith("account ") and lines[1].endswith(CUT)
    reason = " ".join(ln.strip() for ln in lines[2:4])
    assert reason == (
        "the file ends before the table that opens at line 1, column 10 is closed: it may "
        "have been cut off by a crash or a full disk during a save."
    )
    assert lines[4].endswith(RUNS)
    text = " ".join(ln.strip() for ln in lines)
    assert "(not yet confirmed)" in text and "[verify]" not in text
    assert text.count("(not yet confirmed)") == 1
    assert lines[-1] == "2 notes: --notes shows them"
    noted = ok("sv", "list", "--unreadable", "--notes")
    assert "Notes:" in noted and "--notes shows" not in noted


def test_a_byte_order_mark_is_named_constructed(root: Path, flavor: Path) -> None:
    (flavor / SV_DIR / "Edited.lua").write_bytes(b"\xef\xbb\xbfEdited = 1\n")
    [failure] = _unreadable().read_failures
    assert failure.kind == "refused"
    assert failure.reason.startswith("this file starts with a UTF-8 byte-order mark")


def test_a_file_past_the_size_bound_is_not_checked_constructed(
    root: Path, flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The parser's own bound holds: a file over it is listed as not
    checked, not parsed."""
    (flavor / SV_DIR / "Huge.lua").write_bytes(b"Huge = " + b"1" * 64 + b"\n")
    monkeypatch.setattr(luadata, "MAX_FILE_BYTES", 32)
    report = _unreadable()
    [failure] = [f for f in report.read_failures if f.path.endswith("Huge.lua")]
    assert failure.kind == "too_large" and failure.line is None
    assert failure.reason == (
        "it is larger than wowlab reads (over 32 bytes), so wowlab did not check it"
    )
    assert failure.detail.startswith("file is 72 bytes, over the 32-byte bound")
    # Every listed file was only past a bound: that caveat, not the loss.
    assert {f.kind for f in report.read_failures} == {"too_large"}
    assert report.notes[0] == cli._UNREADABLE_TOO_LARGE
    assert cli._UNREADABLE_LOSS not in report.notes


def test_the_size_bound_in_player_words() -> None:
    mib = 1024 * 1024
    assert cli._limit_words(f"file is {300 * mib} bytes, over the {256 * mib}-byte bound") == (
        "it is larger than wowlab reads (over 256 MiB)"
    )
    assert cli._limit_words(f"tables nested deeper than {luadata.MAX_DEPTH}") == (
        "it holds tables nested more than 200 deep"
    )
    assert cli._limit_words("document over the parse budget of 1150000000 (MAX_COST)") == (
        "it holds too much data for wowlab to hold in memory"
    )


def test_tables_nested_too_deep_are_not_checked_constructed(root: Path, flavor: Path) -> None:
    depth = luadata.MAX_DEPTH + 1
    (flavor / SV_DIR / "Deep.lua").write_bytes(b"Deep = " + b"{" * depth + b"}" * depth + b"\n")
    report = _unreadable()
    [failure] = report.read_failures
    assert failure.kind == "too_large"
    assert failure.reason == "it holds tables nested more than 200 deep, so wowlab did not check it"
    assert "MAX_" not in failure.reason
    assert report.notes[0] == cli._UNREADABLE_TOO_LARGE


# ─── cut off inside a value (code review of M16-131) ────────────────────────
# Constructed from a real capture (L8): the captured DBM-StatusBarTimers.lua
# cut short at a point inside one value, written into the copy.

TIMERS = f"{SV_DIR}/DBM-StatusBarTimers.lua"


@pytest.mark.parametrize(
    ("marker", "into", "words"),
    [
        (b'["TimerPoint"] = "TOPRIGHT"', 21, "the file ends inside a text value that opens at"),
        (b'["HugeBarsEnabled"] = true', 24, "the file ends in the middle of a value, at"),
        (b'["EndColorPR"] = 0.5', 19, "the file ends in the middle of a value, at"),
        (b'["TimerY"] = -260', 14, "the file ends in the middle of a value, at"),
        (b'["TimerY"] = -260', 15, "the file ends before the table that opens at"),
    ],
    ids=[
        "inside-a-string-constructed",
        "inside-true-constructed",
        "inside-a-number-constructed",
        "after-a-minus-constructed",
        "inside-a-negative-number-constructed",
    ],
)
def test_a_cut_inside_a_value_is_named_cut_off(
    root: Path, flavor: Path, marker: bytes, into: int, words: str
) -> None:
    data = (CAPTURE / "forever" / TIMERS).read_bytes()
    (flavor / TIMERS).write_bytes(data[: data.index(marker) + into])
    [failure] = _unreadable().read_failures
    assert failure.kind == "refused"
    assert failure.reason.startswith(words)
    assert failure.reason.endswith(
        ": it may have been cut off by a crash or a full disk during a save"
    )


def test_a_long_string_cut_off_says_nothing_of_the_parser_constructed(
    root: Path, flavor: Path
) -> None:
    (flavor / SV_DIR / "Long.lua").write_bytes(b'Long = {\n  ["a"] = "' + b"x" * 80)
    [failure] = _unreadable().read_failures
    assert failure.reason == (
        "the file ends inside a text value that opens at line 2, column 11: it may have been "
        "cut off by a crash or a full disk during a save"
    )
    assert "token" in failure.detail and "token" not in failure.reason


def test_a_value_cut_mid_file_is_not_called_cut_off_constructed(root: Path, flavor: Path) -> None:
    """A name where a value should be, with more of the file after it."""
    (flavor / SV_DIR / "Named.lua").write_bytes(b'Named = {\n  ["a"] = yes,\n}\n')
    [failure] = _unreadable().read_failures
    assert failure.reason == (
        "it holds a name where a value should be, which the game is not known to write, at "
        "line 2, column 11"
    )


# ─── a broken .lua.bak (domain review of M16-131) ───────────────────────────


def test_a_broken_backup_alone_gives_no_loss_caveat_constructed(root: Path, flavor: Path) -> None:
    (flavor / SV_DIR / "Cutoff.lua.bak").write_bytes(b'Cutoff = {\n  ["a"] = 1,\n')
    report = _unreadable()
    [failure] = report.read_failures
    assert failure.backup is True and failure.kind == "refused"
    assert cli._UNREADABLE_LOSS not in report.notes
    assert cli._UNREADABLE_TOO_LARGE not in report.notes
    assert report.notes[0] == cli._UNREADABLE_WHAT
    out = ok("sv", "list", "--unreadable")
    text = " ".join(ln.strip() for ln in out.splitlines())
    assert (
        "This is the game's backup copy, which it does not load; it is replaced at a later "
        "save (not yet confirmed)." in text
    )
    assert "If the game cannot load" not in text
    assert text.count("(not yet confirmed)") == 1


def test_a_broken_backup_beside_a_broken_file_says_not_yet_confirmed_once_constructed(
    root: Path, flavor: Path
) -> None:
    _broken(flavor)
    (flavor / SV_DIR / "Cutoff.lua.bak").write_bytes(b'Cutoff = {\n  ["a"] = 1,\n')
    report = _unreadable()
    assert report.notes[0] == cli._UNREADABLE_LOSS
    for args in (["--unreadable"], ["--unreadable", "--notes"]):
        text = " ".join(ln.strip() for ln in ok("sv", "list", *args).splitlines())
        assert text.count("(not yet confirmed)") == 1
        assert "[verify]" not in text


def test_a_file_that_cannot_be_opened_is_listed_constructed(
    root: Path, flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = luadata.read

    def refuse(path: str | Path, **kwargs: object) -> luadata.LuaDocument:
        if str(path).endswith("Syndicator.lua"):
            raise PermissionError(13, "Permission denied")
        return real(path, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(luadata, "read", refuse)
    [failure] = _unreadable().read_failures
    assert failure.path == f"{SV_DIR}/Syndicator.lua"
    assert failure.kind == "not_opened"
    assert failure.reason == "wowlab could not open it: Permission denied"


def test_a_file_gone_since_the_listing_is_not_a_failure_constructed(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def gone(path: str | Path, **kwargs: object) -> luadata.LuaDocument:
        raise FileNotFoundError(2, "No such file or directory")

    monkeypatch.setattr(luadata, "read", gone)
    assert _unreadable().read_failures == []


def test_one_character_reads_only_its_files_constructed(root: Path, flavor: Path) -> None:
    _broken(flavor)
    report = _unreadable("--character", "Labchard-Labrealmg")
    assert report.read_failures == []
    assert all(f.scope == "character" for f in report.files)


def test_reads_never_write_constructed(root: Path, flavor: Path) -> None:
    _broken(flavor)
    before = _state(root)
    ok("sv", "list", "--unreadable")
    ok("sv", "list", "--unreadable", "--json")
    assert _state(root) == before


def test_with_orphans_is_a_usage_error(root: Path) -> None:
    result = runner.invoke(cli.app, ["sv", "list", "--unreadable", "--orphans"])
    assert result.exit_code == cli.EXIT_USAGE
    assert cli._UNREADABLE_WITH_ORPHANS in result.stderr
