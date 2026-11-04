"""The CLI callers of `wtfconfig.read_*` refuse a FIFO or a final link with a
message, never a traceback and never a hang (M16-26).

`sv dump` is graded in `parser/test_parser_reads_regular_file_m16_26.py`;
this file covers the other commands that read a WTF file through
`wtfconfig`: `cvar list`, `cvar get`, `binds list`, `macros list`,
`char binds` and `doctor` (which reports the file it could
not read as a problem and carries on).

These commands find their file through `layout`, which lists only regular
files, so a FIFO or a link already there is not offered to the reader (the
first test pins that). What reaches the reader is a file swapped between the
listing and the read: the second test makes that swap from inside the call,
just before the real `wtfconfig` reader runs.

The install is the captured tree (`fixtures/macos/`) copied into `tmp_path`;
one of its WTF files is replaced by a FIFO or by a link to a copy of itself
outside the install (both `constructed`, hostile inputs, L8). Each command
runs in a daemon thread; a FIFO a reader is still blocked on after `BOUND`
seconds is opened for writing so the reader gets end-of-file, and the test
fails. Nothing here touches a real install.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import sys
import threading
from pathlib import Path
from typing import Any

import platformdirs
import psutil
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, install, wtfconfig

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CAPTURE = FIXTURES / "macos"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder; tests may name it (L6)
ACCOUNT = "90000001#6"
CHARACTER = "Labcharb-Labrealmd"
BOUND = 5.0

CONFIG = "WTF/Config.wtf"
BINDINGS = f"WTF/Account/{ACCOUNT}/bindings-cache.wtf"
MACROS = f"WTF/Account/{ACCOUNT}/macros-cache.txt"

CASES = [
    ("cvar-list", CONFIG, "read_config", ["cvar", "list"]),
    ("cvar-get", CONFIG, "read_config", ["cvar", "get", "gxApi"]),
    ("binds-list", BINDINGS, "read_bindings", ["binds", "list"]),
    ("macros-list", MACROS, "read_macros", ["macros", "list"]),
    ("char-binds", BINDINGS, "read_bindings", ["char", "binds", "--character", CHARACTER]),
]
IDS = [c[0] for c in CASES]

needs_fifo = pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs os.mkfifo (POSIX)")
needs_symlink = pytest.mark.skipif(
    sys.platform == "win32", reason="symlinks need a privilege on Windows"
)


class _Proc:
    def __init__(self, pid: int, name: str, exe: str) -> None:
        self.pid, self._name, self._exe = pid, name, exe

    def name(self) -> str:
        return self._name

    def exe(self) -> str:
        return self._exe

    def cmdline(self) -> list[str]:
        return [self._exe]

    def status(self) -> str:
        return "running"


@pytest.fixture
def flavor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    redirected = tmp_path / "userdata" / "wowlab"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: redirected)
    other = _Proc(101, "python3", str(tmp_path / "elsewhere" / "python3"))
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: iter([other]))
    root = tmp_path / "World of Warcraft"
    root.mkdir()
    shutil.copy2(CAPTURE / ".build.info", root / ".build.info")
    shutil.copytree(CAPTURE / "forever", root / FLAVOR)
    monkeypatch.setenv(install.ENV_ROOT, str(root))
    monkeypatch.chdir(tmp_path)
    return root / FLAVOR


def _make(kind: str, flavor: Path, rel: str, tmp_path: Path) -> Path:
    path = flavor / rel
    if kind == "constructed-fifo":
        path.unlink()
        os.mkfifo(path)
    else:
        outside = tmp_path / "Elsewhere" / path.name
        outside.parent.mkdir(exist_ok=True)
        shutil.move(path, outside)
        path.symlink_to(outside)
    return path


def _release(fifo: Path) -> None:
    with contextlib.suppress(OSError):
        os.close(os.open(fifo, os.O_WRONLY | os.O_NONBLOCK))


def _invoke(args: list[str], path: Path) -> Any:
    result: list[Any] = []

    def work() -> None:
        result.append(CliRunner().invoke(cli.app, args))

    worker = threading.Thread(target=work, name="m16-26-cli", daemon=True)
    worker.start()
    worker.join(BOUND)
    if worker.is_alive():
        for _ in range(100):
            _release(path)
            worker.join(0.05)
            if not worker.is_alive():
                break
        pytest.fail(f"{' '.join(args)} blocked on {path.name} (released by the test)")
    return result[0]


def _swap_before_read(
    monkeypatch: pytest.MonkeyPatch, reader: str, kind: str, path: Path, tmp_path: Path
) -> None:
    """Replace `path` by a FIFO or a link the moment the CLI calls `reader`
    on it: what a change between `layout`'s listing and the read looks like."""
    real = getattr(wtfconfig, reader)

    def swapped(target: Path) -> Any:
        if Path(target) == path and not path.is_symlink() and path.is_file():
            _make(kind, path.parent, path.name, tmp_path)
        return real(target)

    monkeypatch.setattr(wtfconfig, reader, swapped)


@needs_fifo
@needs_symlink
@pytest.mark.parametrize("kind", ["constructed-fifo", "constructed-link-out"])
@pytest.mark.parametrize(("name", "rel", "reader", "args"), CASES, ids=IDS)
def test_a_fifo_or_a_link_already_there_is_not_read(
    name: str, rel: str, reader: str, args: list[str], kind: str, flavor: Path, tmp_path: Path
) -> None:
    path = _make(kind, flavor, rel, tmp_path)

    outcome = _invoke(args, path)

    assert outcome.exception is None or isinstance(outcome.exception, SystemExit), (
        f"{name}: an uncaught {outcome.exception!r}"
    )
    assert "Traceback" not in outcome.stdout + outcome.stderr
    if outcome.exit_code != 0:
        assert outcome.stderr.startswith("wowlab: "), outcome.stderr


@needs_fifo
@needs_symlink
@pytest.mark.parametrize("kind", ["constructed-fifo", "constructed-link-out"])
@pytest.mark.parametrize(("name", "rel", "reader", "args"), CASES, ids=IDS)
def test_a_file_swapped_before_the_read_is_refused_with_a_message(
    name: str,
    rel: str,
    reader: str,
    args: list[str],
    kind: str,
    flavor: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = flavor / rel
    _swap_before_read(monkeypatch, reader, kind, path, tmp_path)

    outcome = _invoke(args, path)

    assert outcome.exception is None or isinstance(outcome.exception, SystemExit), (
        f"{name}: an uncaught {outcome.exception!r}, not a refusal"
    )
    assert "Traceback" not in outcome.stdout + outcome.stderr
    assert outcome.exit_code not in (0, None), (outcome.exit_code, outcome.stdout)
    assert outcome.stdout == "", "nothing was printed as if the file had been read"
    first = outcome.stderr.strip().splitlines()[0]
    assert first.startswith("wowlab: "), first
    assert path.name in first, first


@needs_fifo
@needs_symlink
@pytest.mark.parametrize("kind", ["constructed-fifo", "constructed-link-out"])
def test_doctor_reports_a_config_swapped_before_the_read_and_carries_on(
    kind: str, flavor: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = flavor / CONFIG
    _swap_before_read(monkeypatch, "read_config", kind, path, tmp_path)

    outcome = _invoke(["doctor", "--offline"], path)

    assert outcome.exception is None or isinstance(outcome.exception, SystemExit), (
        f"an uncaught {outcome.exception!r}"
    )
    assert "Traceback" not in outcome.stdout + outcome.stderr
    assert "cannot read the config files" in outcome.stdout + outcome.stderr
