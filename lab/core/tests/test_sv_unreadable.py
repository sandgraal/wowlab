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
    [failure] = [f for f in _unreadable().read_failures if f.path.endswith("Huge.lua")]
    assert failure.kind == "too_large" and failure.line is None
    assert failure.reason.startswith("it is beyond what wowlab reads (")
    assert failure.reason.endswith("), so it was not checked")


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
