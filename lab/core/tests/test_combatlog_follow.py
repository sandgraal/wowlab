"""`combatlog.follow`, `tail` and the Logs-folder helpers (M10-13, §6.8).

A log is built in `tmp_path` from the real Forever capture's lines (the
fixture itself is only read). `follow` is driven without threads or real
waits: its `sleep` is replaced by a script, and each call runs the next step
of the script (the "client" appending, truncating, rotating), so a test
controls exactly what `follow` sees between two looks. When the script runs
out, the follow ends.

Truncation, replacement, rotation and a line with no break are boundary
cases on real lines; the names of the rotated files are `constructed` (their
shape is the fixture's, `WoWCombatLog-MMDDYY_HHMMSS.txt`, and like the
fixture's, not a real date).
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from wowlab_core import combatlog
from wowlab_core.combatlog import (
    Following,
    NotARegularFileError,
    Record,
    Unparsed,
    find_logs,
    follow,
    is_log_name,
    newest_log,
    read_log,
    tail,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
REAL = FIXTURES / "macos" / "forever" / "Logs" / "WoWCombatLog-040126_021630.txt"
LINES = REAL.read_bytes().splitlines(keepends=True)  # 79 CRLF lines
NAME = REAL.name
LATER = "WoWCombatLog-040126_031500.txt"


class _ScriptOverError(Exception):
    """The script is over."""


Step = Callable[[], None]
Out = list[Record | Unparsed | Following]


def _follow(
    path: Path, script: Sequence[Step], *, offset: int | None = None
) -> tuple[Out, list[int]]:
    """Everything `follow` yields until the script runs out, and how many
    entries had been yielded at each sleep (what was seen between looks)."""
    out: Out = []
    seen_at_sleep: list[int] = []
    steps = iter(script)

    def sleep(_: float) -> None:
        seen_at_sleep.append(len(out))
        step = next(steps, None)
        if step is None:
            raise _ScriptOverError
        step()

    try:
        for entry in follow(path, offset=offset, sleep=sleep, poll_interval=0.0):
            out.append(entry)
    except _ScriptOverError:
        pass
    return out, seen_at_sleep


def _append(path: Path, data: bytes) -> Step:
    def step() -> None:
        with path.open("ab") as f:
            f.write(data)

    return step


def _records(out: Sequence[Record | Unparsed | Following]) -> list[Record | Unparsed]:
    return [e for e in out if not isinstance(e, Following)]


def _events(out: Sequence[Record | Unparsed | Following]) -> list[str]:
    return [e.event for e in out if isinstance(e, Record)]


def _event(i: int) -> str:
    entry = combatlog.tokenize_line(LINES[i].rstrip(b"\r\n").decode())
    assert isinstance(entry, Record)
    return entry.event


@pytest.fixture
def logs(tmp_path: Path) -> Path:
    d = tmp_path / "Logs"
    d.mkdir()
    return d


# ─── appending ───────────────────────────────────────────────────────────────


def test_follow_yields_what_is_appended_after_it_starts(logs: Path) -> None:
    log = logs / NAME
    log.write_bytes(b"".join(LINES[:3]))
    start = log.stat().st_size
    out, _ = _follow(log, [_append(log, b"".join(LINES[3:6]))])
    assert out[0] == Following(path=log, reason="start")
    records = _records(out)
    assert [e.offset for e in records] == [
        start,
        start + len(LINES[3]),
        start + len(LINES[3]) + len(LINES[4]),
    ]
    assert _events(out) == [_event(3), _event(4), _event(5)]


def test_follow_from_an_offset_reads_what_is_already_there(logs: Path) -> None:
    log = logs / NAME
    log.write_bytes(b"".join(LINES[:3]))
    out, _ = _follow(log, [], offset=0)
    assert _records(out) == list(read_log(log))


def test_a_partial_line_waits_for_its_break(logs: Path) -> None:
    log = logs / NAME
    log.write_bytes(LINES[0])
    line = LINES[1]
    out, seen = _follow(
        log,
        [_append(log, line[:20]), _append(log, line[20:-2]), _append(log, line[-2:])],
    )
    # One entry (Following) before each of the partial writes was seen.
    assert seen[:3] == [1, 1, 1]
    (record,) = _records(out)
    assert isinstance(record, Record) and record.ending == "\r\n"
    assert record.raw.encode() + b"\r\n" == line
    assert record.offset == len(LINES[0])


@pytest.mark.parametrize("size", [13, 257, 4096])
def test_the_real_log_streamed_in_pieces_reads_as_a_whole(logs: Path, size: int) -> None:
    data = REAL.read_bytes()
    log = logs / NAME
    log.write_bytes(b"")
    pieces = [data[i : i + size] for i in range(0, len(data), size)]
    out, _ = _follow(log, [_append(log, p) for p in pieces])
    assert _records(out) == list(read_log(REAL))
    assert len(_records(out)) == 79


# ─── truncation and replacement ──────────────────────────────────────────────


def test_truncation_starts_again_from_the_top(logs: Path) -> None:
    log = logs / NAME
    log.write_bytes(b"".join(LINES[:10]))

    def truncate() -> None:
        log.write_bytes(b"".join(LINES[20:22]))

    out, _ = _follow(log, [truncate, _append(log, LINES[22])])
    assert out[1] == Following(path=log, reason="truncated")
    assert _events(out) == [_event(20), _event(21), _event(22)]
    assert _records(out)[0].offset == 0


def test_a_rewrite_longer_than_the_old_file_is_noticed(logs: Path) -> None:
    # The file is cut and rewritten past the read position between two looks:
    # its size alone does not show it; the bytes before the position do.
    log = logs / NAME
    log.write_bytes(b"".join(LINES[:3]))

    def rewrite() -> None:
        with log.open("r+b") as f:  # same file, new content
            f.truncate(0)
            f.write(b"".join(LINES[40:50]))

    out, _ = _follow(log, [rewrite])
    assert out[1] == Following(path=log, reason="truncated")
    assert _events(out) == [_event(i) for i in range(40, 50)]


def test_a_partial_line_cut_by_truncation_is_unparsed_not_a_record(logs: Path) -> None:
    log = logs / NAME
    log.write_bytes(LINES[0])

    def truncate() -> None:
        log.write_bytes(LINES[5])

    out, _ = _follow(log, [_append(log, LINES[1][:-10]), truncate])
    cut, marker, record = out[1:4]
    assert isinstance(cut, Unparsed)
    assert cut.reason == "the file was truncated before this line's break was written"
    assert cut.raw.encode() == LINES[1][:-10] and cut.offset == len(LINES[0])
    assert marker == Following(path=log, reason="truncated")
    assert isinstance(record, Record) and record.offset == 0


def test_a_different_file_under_the_same_name_is_read_from_the_start(logs: Path) -> None:
    log = logs / NAME
    log.write_bytes(b"".join(LINES[:30]))

    def replace() -> None:
        fresh = logs / "fresh.tmp"  # not a log name
        fresh.write_bytes(b"".join(LINES[30:32]))
        fresh.replace(log)

    out, _ = _follow(log, [replace])
    assert out[1] == Following(path=log, reason="replaced")
    assert _events(out) == [_event(30), _event(31)]


# ─── rotation ────────────────────────────────────────────────────────────────


def test_a_new_log_file_is_followed_after_the_old_one_is_drained(logs: Path) -> None:
    old = logs / NAME
    old.write_bytes(b"".join(LINES[:5]))
    new = logs / LATER  # constructed name, the fixture's shape

    def rotate() -> None:
        with old.open("ab") as f:
            f.write(LINES[5] + LINES[6][:15])  # the old file ends mid-line
        new.write_bytes(b"".join(LINES[:2]))

    out, _ = _follow(old, [rotate, _append(new, LINES[2])])
    assert [type(e).__name__ for e in out] == [
        "Following",
        "Record",
        "Unparsed",
        "Following",
        "Record",
        "Record",
        "Record",
    ]
    assert _events(out) == [_event(5), _event(0), _event(1), _event(2)]
    cut = out[2]
    assert isinstance(cut, Unparsed)
    assert cut.reason == "the file was rotated before this line's break was written"
    assert out[3] == Following(path=new, reason="rotated")
    assert [e.offset for e in _records(out)[2:]] == [
        0,
        len(LINES[0]),
        len(LINES[0]) + len(LINES[1]),
    ]


def test_other_files_in_the_folder_are_not_a_rotation(logs: Path) -> None:
    log = logs / NAME
    log.write_bytes(LINES[0])

    def noise() -> None:
        for name in ("Client.log", "WoWCombatLog.txt.bak", "combat.txt", "WoWCombatLog-1.log"):
            (logs / name).write_bytes(LINES[1])
        (logs / "WoWCombatLog-dir.txt").mkdir()

    out, _ = _follow(log, [noise, _append(log, LINES[2])])
    assert out[0] == Following(path=log, reason="start")
    assert not any(isinstance(e, Following) for e in out[1:])
    assert _events(out) == [_event(2)]


def test_a_deleted_log_waits_for_the_next_one(logs: Path) -> None:
    old = logs / NAME
    old.write_bytes(LINES[0])
    new = logs / LATER

    out, seen = _follow(
        old,
        [
            old.unlink,
            lambda: None,  # nothing there: follow keeps looking
            lambda: new.write_bytes(LINES[3]),
        ],
    )
    assert seen[:3] == [1, 1, 1]
    assert out[1:] == [
        Following(path=new, reason="rotated"),
        *list(read_log(new)),
    ]


def test_following_a_folder_starts_at_the_end_of_its_newest_log(logs: Path) -> None:
    older, newer = logs / NAME, logs / LATER
    older.write_bytes(LINES[0])
    newer.write_bytes(LINES[1])
    os.utime(older, ns=(1_000_000_000, 1_000_000_000))
    os.utime(newer, ns=(2_000_000_000, 2_000_000_000))
    out, _ = _follow(logs, [_append(newer, LINES[2])])
    assert out[0] == Following(path=newer, reason="start")
    assert _events(out) == [_event(2)]


def test_following_an_empty_folder_waits_for_the_first_log(logs: Path) -> None:
    log = logs / LATER
    out, seen = _follow(logs, [lambda: None, lambda: log.write_bytes(LINES[0] + LINES[1])])
    assert seen[:2] == [0, 0]
    assert out == [Following(path=log, reason="start"), *list(read_log(log))]


def test_several_new_logs_are_read_in_turn_oldest_first(logs: Path) -> None:
    old = logs / NAME
    old.write_bytes(LINES[0])
    second, third = logs / LATER, logs / "WoWCombatLog-040126_041500.txt"  # constructed names

    def two_at_once() -> None:
        third.write_bytes(LINES[3])
        second.write_bytes(LINES[2])
        os.utime(second, ns=(1_000_000_000, 1_000_000_000))
        os.utime(third, ns=(2_000_000_000, 2_000_000_000))

    out, _ = _follow(old, [two_at_once])
    assert out == [
        Following(path=old, reason="start"),
        Following(path=second, reason="rotated"),
        *list(read_log(second)),
        Following(path=third, reason="rotated"),
        *list(read_log(third)),
    ]


def test_follow_of_a_missing_path_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        next(follow(tmp_path / "nowhere"))


posix_only = pytest.mark.skipif(sys.platform == "win32", reason="FIFOs and symlinks: POSIX")


@posix_only
def test_a_fifo_under_a_log_name_does_not_hang_constructed(logs: Path) -> None:
    fifo = logs / LATER
    os.mkfifo(fifo)
    with pytest.raises(NotARegularFileError):
        next(follow(fifo))
    with pytest.raises(NotARegularFileError):
        list(read_log(fifo))
    with pytest.raises(NotARegularFileError):
        tail(fifo, 1)
    assert find_logs(logs) == []


@posix_only
def test_a_log_replaced_by_a_fifo_is_no_longer_followed_constructed(logs: Path) -> None:
    log = logs / NAME
    log.write_bytes(LINES[0])

    def to_fifo() -> None:
        log.unlink()
        os.mkfifo(log)

    out, seen = _follow(log, [_append(log, LINES[1][:10]), to_fifo, lambda: None])
    assert len(out) == 2
    cut = out[1]
    assert isinstance(cut, Unparsed)
    assert cut.reason == "the file was replaced before this line's break was written"
    assert seen == [1, 1, 2, 2], "it waits without spinning or blocking"


@posix_only
def test_a_symlinked_log_is_not_listed_or_followed_constructed(logs: Path, tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere.txt"
    elsewhere.write_bytes(LINES[0])
    log = logs / NAME
    log.write_bytes(LINES[0])
    out, _ = _follow(log, [lambda: (logs / LATER).symlink_to(elsewhere)])
    assert find_logs(logs) == [log]
    assert out == [Following(path=log, reason="start")]


# ─── tail ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("chunk", [1, 64, 1000])
def test_tail_reads_backwards_in_blocks(monkeypatch: pytest.MonkeyPatch, chunk: int) -> None:
    monkeypatch.setattr(combatlog, "_CHUNK", chunk)
    whole = list(read_log(REAL))
    for n in (0, 1, 2, 10, 78, 79, 80):
        lines, end = tail(REAL, n)
        assert lines == (whole[-n:] if n else []), n
        assert end == REAL.stat().st_size


def test_tail_leaves_a_partial_last_line_to_follow(logs: Path) -> None:
    log = logs / NAME
    log.write_bytes(b"".join(LINES[:4]) + LINES[4][:30])
    lines, end = tail(log, 2)
    assert _events(lines) == [_event(2), _event(3)]
    assert end == len(b"".join(LINES[:4]))
    out, _ = _follow(log, [_append(log, LINES[4][30:])], offset=end)
    assert _events(out) == [_event(4)]
    assert _records(out)[0].offset == end


def test_tail_of_an_empty_file_and_of_one_line_with_no_break(logs: Path) -> None:
    log = logs / NAME
    log.write_bytes(b"")
    assert tail(log, 5) == ([], 0)
    log.write_bytes(LINES[0][:-2])
    assert tail(log, 5) == ([], 0)
    with pytest.raises(ValueError):
        tail(log, -1)


# ─── the Logs folder ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        (NAME, True),
        ("WoWCombatLog.txt", True),
        ("wowcombatlog-040126_021630.TXT", True),
        ("WoWCombatLog.txt.bak", False),
        ("Client.log", False),
        ("CombatLog.txt", False),
    ],
)
def test_is_log_name(name: str, expected: bool) -> None:
    assert is_log_name(name) is expected


def test_find_logs_orders_by_modification_time_then_name(logs: Path) -> None:
    names = ["WoWCombatLog.txt", NAME, LATER]
    for i, name in enumerate(names):
        (logs / name).write_bytes(LINES[0])
        stamp = (3 - i) * 1_000_000_000
        os.utime(logs / name, ns=(stamp, stamp))
    (logs / "Client.log").write_bytes(b"x")
    assert [p.name for p in find_logs(logs)] == [LATER, NAME, "WoWCombatLog.txt"]
    assert newest_log(logs) == logs / "WoWCombatLog.txt"
    for name in (NAME, LATER):
        os.utime(logs / name, ns=(5_000_000_000, 5_000_000_000))
    assert newest_log(logs) == logs / LATER, "a tie goes to the later name"
    assert newest_log(logs / "..") is None


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


def test_reading_and_following_change_nothing(logs: Path) -> None:
    log = logs / NAME
    log.write_bytes(REAL.read_bytes())
    before = _state(logs.parent)
    list(read_log(log))
    tail(log, 10)
    find_logs(logs)
    _follow(log, [lambda: None, lambda: None], offset=0)
    _follow(logs, [lambda: None])
    assert _state(logs.parent) == before
