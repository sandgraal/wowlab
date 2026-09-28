"""`wowlab log tail [--follow]` through Typer's runner (M10-13, §6.11).

The install is the captured tree (`fixtures/macos/`) copied into `tmp_path`,
as in `test_cli.py`, so its `Logs/` holds the real Forever combat log. The
follow loop never waits for real: `cli._follow_sleep` is replaced by a script
whose steps play the client (append, rotate) and whose last step is the
owner pressing Ctrl-C. Inputs labelled `constructed` are boundary cases: a
missing log, a line that is not UTF-8 or carries a control character.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import platformdirs
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, combatlog, install
from wowlab_core.cli import LogFollowing, LogTailLine, LogTailReport

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CAPTURE = FIXTURES / "macos"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder; tests may name it (L6)
LOG = "Logs/WoWCombatLog-040126_021630.txt"
LATER = "Logs/WoWCombatLog-040126_031500.txt"  # constructed name, the fixture's shape
REAL = CAPTURE / "forever" / LOG
LINES = REAL.read_bytes().splitlines(keepends=True)
TEXT = [line.rstrip(b"\r\n").decode() for line in LINES]

runner = CliRunner()


@pytest.fixture(autouse=True)
def user_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    redirected = tmp_path / "userdata"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: redirected / "wowlab")
    return redirected / "wowlab"


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


def run(*args: str) -> Any:
    return runner.invoke(cli.app, list(args))


def ok(*args: str) -> Any:
    result = run(*args)
    assert result.exit_code == 0, (result.stdout, result.stderr, result.exception)
    return result


def _script(monkeypatch: pytest.MonkeyPatch, steps: Sequence[Callable[[], None]]) -> None:
    """Each look that finds nothing runs the next step; then Ctrl-C."""
    remaining = iter(steps)

    def sleep(_: float) -> None:
        step = next(remaining, None)
        if step is None:
            raise KeyboardInterrupt
        step()

    monkeypatch.setattr(cli, "_follow_sleep", sleep)


def _append(path: Path, data: bytes) -> Callable[[], None]:
    def step() -> None:
        with path.open("ab") as f:
            f.write(data)

    return step


# ─── tail ────────────────────────────────────────────────────────────────────


def test_log_tail_prints_the_last_ten_lines_of_the_newest_log(root: Path) -> None:
    out = ok("log", "tail").stdout.splitlines()
    assert out == [f"==> {LOG} <==", *TEXT[-10:]]


@pytest.mark.parametrize("n", ["0", "3", "79", "500"])
def test_log_tail_lines(root: Path, n: str) -> None:
    out = ok("log", "tail", "-n", n).stdout.splitlines()
    assert out[1:] == (TEXT[-int(n) :] if int(n) else [])


def test_log_tail_rejects_a_negative_count(root: Path) -> None:
    assert run("log", "tail", "-n", "-1").exit_code == 2


def test_log_tail_json_validates_and_carries_the_tokens(root: Path) -> None:
    result = ok("log", "tail", "--json", "-n", "79")
    report = LogTailReport.model_validate_json(result.stdout)
    assert report.file == LOG
    assert report.notes == [cli._BATCHES]
    assert report.entries == combatlog.tail(REAL, 79)[0]
    first = report.entries[0]
    assert isinstance(first, combatlog.Record)
    assert first.event == "COMBAT_LOG_VERSION"
    assert first.timestamp == "4/1/2026 02:16:30.666-4"  # shifted, printed as written
    assert json.loads(result.stdout)["entries"][5]["fields"][1] == {
        "text": "Labchard-LabrealmbPartbPartcPartd-"
    }


def test_log_tail_picks_the_newest_log(flavor: Path) -> None:
    newer = flavor / LATER
    newer.write_bytes(LINES[0] + LINES[1])
    out = ok("log", "tail").stdout.splitlines()
    assert out == [f"==> {LATER} <==", TEXT[0], TEXT[1]]


def test_log_tail_without_a_log_says_so_constructed(flavor: Path) -> None:
    (flavor / LOG).unlink()
    assert "no combat log under Logs/" in ok("log", "tail").stdout
    report = LogTailReport.model_validate_json(ok("log", "tail", "--json").stdout)
    assert report.file is None and report.entries == [] and report.notes


def test_log_tail_prints_unparsed_lines_and_escapes_control_characters_constructed(
    flavor: Path,
) -> None:
    (flavor / LATER).write_bytes(
        LINES[0]
        + b'4/1/2026 02:16:31.000-4  ZONE_CHANGE,0,"Caf\xe9 \x1b[31m",0\r\n'
        + b"not a record\r\n"
    )
    out = ok("log", "tail").stdout.splitlines()
    assert out[-2] == '4/1/2026 02:16:31.000-4  ZONE_CHANGE,0,"Caf\\xe9 \\x1b[31m",0'
    assert out[-1] == "(not tokenized: no two spaces after a timestamp) not a record"
    report = LogTailReport.model_validate_json(ok("log", "tail", "--json").stdout)
    zone = report.entries[1]
    assert isinstance(zone, combatlog.Record)
    # A byte that is not UTF-8 is written as NUL and four hex digits (as `layout` does).
    assert zone.fields[1] == combatlog.Quoted(text="Caf\x00DCE9 \x1b[31m")


# ─── follow ──────────────────────────────────────────────────────────────────


def test_log_tail_follow_prints_appended_lines_and_a_new_log(
    flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = flavor / LOG
    log.write_bytes(b"".join(LINES[:5]))
    newer = flavor / LATER
    _script(
        monkeypatch,
        [
            _append(log, LINES[5] + LINES[6][:12]),  # a partial line is not printed yet
            _append(log, LINES[6][12:]),
            lambda: newer.write_bytes(LINES[0]),
        ],
    )
    out = ok("log", "tail", "--follow", "-n", "2").stdout.splitlines()
    assert out == [
        f"==> {LOG} <==",
        TEXT[3],
        TEXT[4],
        TEXT[5],
        TEXT[6],
        f"==> {LATER} (a new log file) <==",
        TEXT[0],
    ]


def test_log_tail_follow_survives_truncation(flavor: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    log = flavor / LOG
    _script(monkeypatch, [lambda: log.write_bytes(LINES[10])])
    out = ok("log", "tail", "-f", "-n", "1").stdout.splitlines()
    assert out == [
        f"==> {LOG} <==",
        TEXT[-1],
        f"==> {LOG} (the file shrank or its earlier bytes changed; reading it from the start) <==",
        TEXT[10],
    ]


def test_log_tail_follow_json_is_one_valid_line_each(
    flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = flavor / LOG
    _script(monkeypatch, [_append(log, LINES[1]), lambda: (flavor / LATER).write_bytes(LINES[2])])
    out = ok("log", "tail", "--follow", "--json", "-n", "1").stdout.splitlines()
    parsed = [LogTailLine.model_validate_json(line).root for line in out]
    assert parsed[0] == LogFollowing(file=LOG, reason="start")
    assert [type(p).__name__ for p in parsed] == [
        "LogFollowing",
        "Record",
        "Record",
        "LogFollowing",
        "Record",
    ]
    assert parsed[3] == LogFollowing(file=LATER, reason="rotated")
    assert [p.raw for p in parsed if isinstance(p, combatlog.Record)] == [
        TEXT[-1],
        TEXT[1],
        TEXT[2],
    ]


def test_log_tail_follow_waits_for_a_first_log_constructed(
    flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (flavor / LOG).unlink()
    _script(monkeypatch, [lambda: None, lambda: (flavor / LATER).write_bytes(LINES[0])])
    result = ok("log", "tail", "--follow")
    assert "waiting for one" in result.stderr
    assert result.stdout.splitlines() == [f"==> {LATER} <==", TEXT[0]]


def test_log_tail_follow_waits_for_a_missing_logs_folder_constructed(
    flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shutil.rmtree(flavor / "Logs")
    assert ok("log", "tail").stdout.startswith("no combat log under Logs/")

    def logging_turned_on() -> None:
        (flavor / "Logs").mkdir()
        (flavor / LATER).write_bytes(LINES[0])

    _script(monkeypatch, [lambda: None, logging_turned_on])
    result = ok("log", "tail", "--follow")
    assert "waiting for one" in result.stderr
    assert result.stdout.splitlines() == [f"==> {LATER} <==", TEXT[0]]


# ─── notes and limits ────────────────────────────────────────────────────────


def test_log_tail_notes_go_to_stderr_and_into_the_json(flavor: Path) -> None:
    result = ok("log", "tail", "-n", "1")
    assert result.stdout.splitlines() == [f"==> {LOG} <==", TEXT[-1]]
    assert cli._BATCHES in result.stderr
    assert cli._STILL_WRITING not in result.stderr
    with (flavor / LOG).open("ab") as f:
        f.write(LINES[3][:20])  # the client mid-flush
    result = ok("log", "tail", "-n", "1")
    assert result.stdout.splitlines() == [f"==> {LOG} <==", TEXT[-1]]
    assert cli._STILL_WRITING in result.stderr
    report = LogTailReport.model_validate_json(ok("log", "tail", "--json").stdout)
    assert report.notes == [cli._BATCHES, cli._STILL_WRITING]


def test_log_tail_follow_prints_the_batches_note_once(
    flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _script(monkeypatch, [lambda: (flavor / LATER).write_bytes(LINES[0])])
    result = ok("log", "tail", "--follow", "-n", "0")
    assert result.stderr.count(cli._BATCHES) == 1


@pytest.mark.parametrize(("n", "code"), [("100000", 0), ("100001", 2)])
def test_log_tail_lines_are_capped(root: Path, n: str, code: int) -> None:
    assert run("log", "tail", "-n", n).exit_code == code


def test_log_tail_marks_a_cut_line_constructed(flavor: Path) -> None:
    long = b"x" * (combatlog.MAX_LINE_BYTES + 5)
    (flavor / LATER).write_bytes(long + b"\r\n")
    out = ok("log", "tail").stdout.splitlines()
    assert out[1].startswith(
        f"(not tokenized: a line longer than {combatlog.MAX_LINE_BYTES} bytes; only its start "
        f"is kept) (cut to its first {combatlog.MAX_LINE_BYTES} of {len(long)} bytes) xxx"
    )
    report = LogTailReport.model_validate_json(ok("log", "tail", "--json").stdout)
    (cut,) = report.entries
    assert isinstance(cut, combatlog.Unparsed)
    assert (cut.truncated, cut.length, cut.ending) == (True, len(long), "\r\n")


# ─── L1 ──────────────────────────────────────────────────────────────────────


def _state(root: Path) -> dict[str, tuple[bytes | None, int]]:
    out: dict[str, tuple[bytes | None, int]] = {}
    for p in sorted(root.rglob("*")):
        st = p.lstat()
        out[p.relative_to(root).as_posix()] = (
            p.read_bytes() if p.is_file() else None,
            st.st_mtime_ns,
        )
    return out


def test_log_tail_writes_nothing(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    before = _state(root)
    ok("log", "tail")
    ok("log", "tail", "--json")
    _script(monkeypatch, [lambda: None])
    ok("log", "tail", "--follow")
    assert _state(root) == before
