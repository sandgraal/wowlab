"""The CLI callers of `wtfconfig.read_*` refuse a FIFO or a final link with a
message, never a traceback and never a hang (M16-26).

`sv dump` is graded in `parser/test_parser_reads_regular_file_m16_26.py`;
this file covers the other commands that read a WTF file through
`wtfconfig`: `cvar list`, `cvar get`, `binds list`, `macros list`,
`char binds` and `doctor` (which reports the file it could
not read as a problem and carries on).

These commands find their file through `layout.Layout.wtf_files`, which
lists only regular files under `WTF/` (a FIFO and a link are left out, the
link not followed). So a FIFO or a link already at the path is never offered
to the reader: the command reports the file as absent (`file` / `path` null
in its `--json` report) and prints nothing read through it. The first test
pins that; it grades `layout`'s listing, not the readers, so it passes with
the old readers too. What reaches a reader is a file swapped between the
listing and the read: the second test makes that swap from inside the call,
just before the real `wtfconfig` reader runs, and fails on the old readers
(a FIFO blocks, a link is followed and its `MARKER` printed).

The install is the captured tree (`fixtures/macos/`) copied into `tmp_path`;
one of its WTF files is replaced by a FIFO or by a link to a constructed
file outside the install (both `constructed`, hostile inputs, L8). Each command
runs in a daemon thread; a FIFO a reader is still blocked on after `BOUND`
seconds is opened for writing so the reader gets end-of-file, and the test
fails. Nothing here touches a real install.
"""

from __future__ import annotations

import contextlib
import json
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

MARKER = "M1626OUTSIDEMARKER"
"""In every file a link points at outside the install, and nowhere in the
captured tree: if it reaches the output, the link was read."""
OUTSIDE = {
    "Config.wtf": f'SET gxApi "{MARKER}"\n'.encode(),
    "bindings-cache.wtf": f"BINDINGMODE 0\nbind F9 {MARKER}\n".encode(),
    "macros-cache.txt": (
        f'VER 3 0000000000000001 "{MARKER}" "INV_Misc_QuestionMark"\n/say {MARKER}\nEND\n'.encode()
    ),
}
"""What a link points at (constructed, hostile input): a valid file of the
same kind, written in `wtfconfig`'s grammar for that kind (Config.wtf `SET`
lines, bindings-cache.wtf `bind` lines, macros-cache.txt `VER` records; the
captured account macros-cache.txt is empty, so this one is not modelled on
it), and carrying `MARKER` as a value the command prints. The positive
control test shows each one does reach the output when read."""

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
        outside.write_bytes(OUTSIDE[path.name])
        path.unlink()
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

    def swapped(target: Path, **kwargs: Any) -> Any:
        if Path(target) == path and not path.is_symlink() and path.is_file():
            _make(kind, path.parent, path.name, tmp_path)
        return real(target, **kwargs)

    monkeypatch.setattr(wtfconfig, reader, swapped)


@pytest.mark.parametrize(("name", "rel", "reader", "args"), CASES, ids=IDS)
def test_constructed_outside_content_as_a_regular_file_prints_the_marker(
    name: str, rel: str, reader: str, args: list[str], flavor: Path
) -> None:
    """The positive control: each `OUTSIDE` file, put in place as a regular
    file, is read and its `MARKER` printed, so every check below that
    `MARKER` is absent can fail."""
    path = flavor / rel
    path.write_bytes(OUTSIDE[path.name])

    outcome = _invoke([*args, "--json"], path)

    assert outcome.exit_code == 0, (outcome.exit_code, outcome.stderr)
    assert MARKER in outcome.stdout, f"{name}: the marker is not printed: {outcome.stdout}"


@needs_fifo
@needs_symlink
@pytest.mark.parametrize("kind", ["constructed-fifo", "constructed-link-out"])
@pytest.mark.parametrize(("name", "rel", "reader", "args"), CASES, ids=IDS)
def test_a_fifo_or_a_link_already_there_is_not_read(
    name: str, rel: str, reader: str, args: list[str], kind: str, flavor: Path, tmp_path: Path
) -> None:
    """`layout` leaves the entry out, so the command reports no such file and
    nothing behind the FIFO or the link reaches the output."""
    path = _make(kind, flavor, rel, tmp_path)

    outcome = _invoke([*args, "--json"], path)

    assert outcome.exception is None or isinstance(outcome.exception, SystemExit), (
        f"{name}: an uncaught {outcome.exception!r}"
    )
    assert "Traceback" not in outcome.stdout + outcome.stderr
    assert MARKER not in outcome.stdout + outcome.stderr, "the link's target was read"
    assert outcome.exit_code == 0, (outcome.exit_code, outcome.stderr)
    report = json.loads(outcome.stdout)
    if name == "char-binds":
        files = {f["scope"]: f["path"] for f in report["files"]}
        assert files["account"] is None, f"the entry was listed: {files}"
    else:
        assert report["file"] is None, f"the entry was listed: {report['file']}"


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
    assert MARKER not in outcome.stderr, "the link's target was read"
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
