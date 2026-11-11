"""A closed pipe on stdout (M16-14, docs/LAB_PLAN.md §18.1 item 4).

`wowlab tree | head -1` used to end with "wowlab: [Errno 32] Broken pipe"
and exit 1, and a `snap restore --yes` whose reader went away stopped after
its plan, before the write. The console script (`cli.app`) now handles it
once, at the entry point: stdout is a `cli._PipeSafeStream` while the app
runs, SIGPIPE stays ignored where there is one (Windows has none), and the
command goes on to the end and exits as it would have.

The end-to-end tests run `cli.app()` in a child Python process, as the
generated `wowlab` script does, with its stdout a real pipe. The reader
reads one line and closes its end. So that the close lands after that first
line and before the rest, whatever the scheduler does, the child's stdout is
wrapped first in a shim that waits after the first line until the test says
the reader is gone, and that records the failed write it then sees. The
install is the fixture sandbox (`fixtures/macos/`, copied into `tmp_path`
with its flavor folder named as its provenance row says); the user data
directory is redirected into `tmp_path` and the process probe sees no
client. Nothing reads or writes a real install.
"""

from __future__ import annotations

import errno
import io
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from wowlab_core import cli, install

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder; tests may name it (L6)
ACCOUNT = "90000001#6"

# Run as `python -c CHILD <user data dir> <go file> <broke file> <args...>`.
# With `-` for the go file, stdout is left as it is.
CHILD = """
import sys
import time
from pathlib import Path

import platformdirs
import psutil

from wowlab_core import cli

userdata, go, broke = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
platformdirs.user_data_path = lambda *args, **kwargs: userdata
psutil.process_iter = lambda *args, **kwargs: iter([])


class FirstLineThenWait:
    # The real stdout, flushed after every write; after the first line it
    # waits until the reader has closed its end of the pipe.

    def __init__(self, real, go):
        self._real = real
        self._go = Path(go)
        self._lines = False
        self._waited = False

    def write(self, data):
        if self._lines and not self._waited:
            self._waited = True
            deadline = time.monotonic() + 60
            while not self._go.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
        try:
            n = self._real.write(data)
            self._real.flush()
        except OSError as exc:
            broke.write_text(type(exc).__name__ + " " + str(exc.errno))
            raise
        if "\\n" in data:
            self._lines = True
        return n

    def flush(self):
        try:
            self._real.flush()
        except OSError as exc:
            broke.write_text(type(exc).__name__ + " " + str(exc.errno))
            raise

    def __getattr__(self, name):
        return getattr(self._real, name)


if go != "-":
    sys.stdout = FirstLineThenWait(sys.stdout, go)
sys.argv = ["wowlab", *sys.argv[4:]]
sys.exit(cli.app())
"""


def _sandbox(tmp_path: Path) -> Path:
    root = tmp_path / "World of Warcraft"
    root.mkdir()
    shutil.copy2(FIXTURES / "macos" / ".build.info", root / ".build.info")
    shutil.copytree(FIXTURES / "macos" / "forever", root / FLAVOR)
    return root


def _env(root: Path) -> dict[str, str]:
    return {**os.environ, install.ENV_ROOT: str(root), "PYTHONIOENCODING": "utf-8"}


def _run(tmp_path: Path, root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    """The command with stdout read to the end, as a plain run."""
    return subprocess.run(
        [sys.executable, "-c", CHILD, str(tmp_path / "userdata"), "-", "-", *args],
        capture_output=True,
        cwd=tmp_path,
        env=_env(root),
        timeout=120,
        check=False,
    )


def _piped(tmp_path: Path, root: Path, *args: str) -> tuple[int, str, str, str]:
    """The command with a reader that closes after one line: its exit status,
    the line read, stderr, and the failed write the child saw."""
    go = tmp_path / "reader-closed"
    broke = tmp_path / "write-failed"
    with (tmp_path / "stderr.txt").open("wb") as err:
        child = subprocess.Popen(
            [sys.executable, "-c", CHILD, str(tmp_path / "userdata"), str(go), str(broke), *args],
            stdout=subprocess.PIPE,
            stderr=err,
            cwd=tmp_path,
            env=_env(root),
        )
        try:
            assert child.stdout is not None
            first = child.stdout.readline().decode("utf-8")
            child.stdout.close()
            go.write_text("closed")
            code = child.wait(timeout=120)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()
    stderr = (tmp_path / "stderr.txt").read_bytes().decode("utf-8")
    seen = broke.read_text() if broke.exists() else ""
    return code, first, stderr, seen


def _no_pipe_noise(stderr: str) -> None:
    for word in ("Errno", "Broken pipe", "BrokenPipeError", "Exception ignored", "Traceback"):
        assert word not in stderr, stderr


def test_tree_into_a_reader_that_closes_after_one_line_exits_0(tmp_path: Path) -> None:
    root = _sandbox(tmp_path)
    whole = _run(tmp_path, root, "tree")
    assert whole.returncode == 0, whole.stderr
    assert len(whole.stdout.splitlines()) > 1, "tree prints more than the one line read"

    code, first, stderr, seen = _piped(tmp_path, root, "tree")
    assert first.startswith(FLAVOR), first
    assert seen, "the reader closed before the second line was written"
    assert code == 0, stderr
    _no_pipe_noise(stderr)


def test_snap_restore_yes_into_a_reader_that_closes_after_one_line_writes(
    tmp_path: Path,
) -> None:
    """The plan's first line is read, the pipe closes before its second, and
    the restore still writes every file and exits 0."""
    root = _sandbox(tmp_path)
    config = root / FLAVOR / "WTF" / "Config.wtf"
    account = root / FLAVOR / "WTF" / "Account" / ACCOUNT
    other = next(p for p in sorted(account.rglob("*")) if p.is_file() and p.suffix == ".lua")
    before = {config: config.read_bytes(), other: other.read_bytes()}
    taken = _run(tmp_path, root, "snap", "create")
    assert taken.returncode == 0, taken.stderr
    config.write_bytes(before[config] + b'SET pipeTest "1"\n')
    other.write_bytes(before[other] + b"\n-- changed\n")

    code, first, stderr, seen = _piped(tmp_path, root, "snap", "restore", "latest", "--yes")
    assert first.startswith("Restore from snapshot"), first
    assert seen, "the reader closed before the plan's second line was written"
    assert code == 0, stderr
    _no_pipe_noise(stderr)
    assert config.read_bytes() == before[config]
    assert other.read_bytes() == before[other]


# ─── the stream, in process ──────────────────────────────────────────────────


class _Failing(io.StringIO):
    """A stream whose writes fail with `error` once `failing` is set."""

    def __init__(self, error: OSError) -> None:
        super().__init__()
        self.error = error
        self.failing = False
        self.writes = 0

    def write(self, s: str) -> int:
        self.writes += 1
        if self.failing:
            raise self.error
        return super().write(s)


def test_closed_pipe_is_epipe_and_on_windows_also_einval() -> None:
    assert cli._closed_pipe(BrokenPipeError(errno.EPIPE, "Broken pipe"), windows=False)
    assert cli._closed_pipe(BrokenPipeError(errno.EPIPE, "Broken pipe"), windows=True)
    einval = OSError(errno.EINVAL, "Invalid argument")
    assert cli._closed_pipe(einval, windows=True)
    assert not cli._closed_pipe(einval, windows=False)
    assert not cli._closed_pipe(OSError(errno.ENOSPC, "No space left"), windows=True)


def test_the_stream_drops_output_after_the_reader_goes() -> None:
    inner = _Failing(BrokenPipeError(errno.EPIPE, "Broken pipe"))
    stream = cli._PipeSafeStream(inner)
    assert stream.write("one\n") == 4
    inner.failing = True
    assert stream.write("two\n") == 4
    assert stream.write("three\n") == 6
    stream.flush()
    assert inner.writes == 2, "nothing is written after the reader went"
    assert inner.getvalue() == "one\n"


def test_the_stream_raises_every_other_error() -> None:
    inner = _Failing(OSError(errno.ENOSPC, "No space left"))
    inner.failing = True
    with pytest.raises(OSError, match="No space left"):
        cli._PipeSafeStream(inner).write("x\n")


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_the_stream_takes_einval_as_a_closed_pipe_only_on_windows(
    monkeypatch: pytest.MonkeyPatch, platform: str
) -> None:
    inner = _Failing(OSError(errno.EINVAL, "Invalid argument"))
    inner.failing = True
    stream = cli._PipeSafeStream(inner)
    monkeypatch.setattr(sys, "platform", platform)
    if platform == "win32":
        assert stream.write("x\n") == 2
    else:
        with pytest.raises(OSError, match="Invalid argument"):
            stream.write("x\n")


def test_the_stream_and_its_buffer_share_one_state() -> None:
    raw = io.BytesIO()
    text = io.TextIOWrapper(raw, encoding="utf-8")
    stream = cli._PipeSafeStream(text)
    buffer: Any = stream.buffer
    assert buffer is stream.buffer
    stream._state.gone = True
    assert buffer.write(b"dropped") == len(b"dropped")
    assert raw.getvalue() == b""


def test_a_real_closed_pipe_is_pointed_at_the_null_device() -> None:
    """The bytes still buffered when the reader went, which Python flushes as
    the process ends, then go to the null device instead of failing again."""
    read, write = os.pipe()
    os.close(read)
    with os.fdopen(write, "w", encoding="utf-8") as raw:
        stream = cli._PipeSafeStream(raw)
        stream.write("x" * 10 + "\n")
        stream.flush()
        assert stream._state.gone
        raw.write("still buffered\n")
        raw.flush()  # would raise without the null device


def test_sigpipe_is_left_alone_where_there_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows: the signal module has no SIGPIPE, and nothing is set."""
    calls: list[Any] = []
    windows_signal = SimpleNamespace(SIG_IGN=signal.SIG_IGN, signal=lambda *a: calls.append(a))
    monkeypatch.setattr(cli, "signal", windows_signal)
    cli._ignore_sigpipe()
    assert calls == []


@pytest.mark.skipif(not hasattr(signal, "SIGPIPE"), reason="no SIGPIPE on this platform")
def test_sigpipe_is_ignored_where_there_is_one(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Any] = []
    posix_signal = SimpleNamespace(
        SIGPIPE=signal.SIGPIPE, SIG_IGN=signal.SIG_IGN, signal=lambda *a: calls.append(a)
    )
    monkeypatch.setattr(cli, "signal", posix_signal)
    cli._ignore_sigpipe()
    assert calls == [(signal.SIGPIPE, signal.SIG_IGN)]
