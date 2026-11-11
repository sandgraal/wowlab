"""Errors in words (M16-19, docs/handoffs/outside-evaluation-2026-10.md EV-18).

- A file that is not there is said in words that name the command listing
  what is there (`sv dump` names `wowlab sv list`), never as `[Errno 2]`.
- Every "no install" error names `--root` and `WOWLAB_WOW_ROOT`.
- A full disk under the snapshot store is reported as the store being full,
  not as "cannot read" a healthy install file; a real read error still is.

The install is the captured tree (`fixtures/macos/`) copied into `tmp_path`
with its flavor folder named as its provenance row says; the user data
directory is redirected into `tmp_path` and the process probe sees no
client. The full disk, the failed read and the missing file inside a
command are constructed (an injected `OSError`), and labelled as such.
Nothing reads or writes a real install.
"""

from __future__ import annotations

import errno
import io
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, BinaryIO

import platformdirs
import psutil
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, guard, install, snapshot
from wowlab_core.gamedata import GameData

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder; tests may name it (L6)
ACCOUNT_SV = "WTF/Account/90000001#6/SavedVariables"
SYNDICATOR = f"{ACCOUNT_SV}/DBM-StatusBarTimers.lua"
NAMES_THE_INSTALL = (
    "name the install folder (the one that holds .build.info) with --root, "
    "or set WOWLAB_WOW_ROOT to it"
)

# The variable's line in the notes that end a no-install error (M16-112):
# `export` or, on Windows, `set` and `$env:`; each names the variable.
ENV_LINE = "WOWLAB_WOW_ROOT"

runner = CliRunner()


class _NoClient:
    pid = 101

    def __init__(self, exe: str) -> None:
        self._exe = exe

    def name(self) -> str:
        return "python3"

    def exe(self) -> str:
        return self._exe

    def cmdline(self) -> list[str]:
        return [self._exe]

    def status(self) -> str:
        return "running"


@pytest.fixture(autouse=True)
def user_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    user = tmp_path / "userdata" / "wowlab"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: user)
    process = _NoClient(str(tmp_path / "elsewhere" / "python3"))
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: iter([process]))
    monkeypatch.chdir(tmp_path)
    return user


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "World of Warcraft"
    root.mkdir()
    shutil.copy2(FIXTURES / "macos" / ".build.info", root / ".build.info")
    shutil.copytree(FIXTURES / "macos" / "forever", root / FLAVOR)
    monkeypatch.setenv(install.ENV_ROOT, str(root))
    return root


@pytest.fixture
def no_install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """No `WOWLAB_WOW_ROOT`, and one default that holds no install."""
    monkeypatch.delenv(install.ENV_ROOT, raising=False)
    empty = tmp_path / "defaults" / "World of Warcraft"
    empty.mkdir(parents=True)
    monkeypatch.setattr(install, "default_roots", lambda **k: (empty,))
    return empty


def run(*args: str) -> Any:
    return runner.invoke(cli.app, list(args))


def _one_line(text: str) -> str:
    lines = [line for line in text.splitlines() if line.startswith("wowlab: ")]
    assert len(lines) == 1, text
    return lines[0]


# ─── a file that is not there ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "given",
    ["nope.lua", f"{ACCOUNT_SV}/Nope.lua"],
    ids=["unknown-name", "path-in-the-flavor-folder"],
)
@pytest.mark.parametrize("install_only", [False, True], ids=["plain", "install-only"])
def test_sv_dump_of_a_missing_relative_name_names_sv_list(
    root: Path, given: str, install_only: bool
) -> None:
    # A relative FILE not on disk is looked up by name (M16-18), and its
    # not-found line is the lookup's: it names the lister too.
    args = ["sv", "dump", *(["--install-only"] if install_only else []), given]
    result = run(*args)
    assert result.exit_code == cli.EXIT_ERROR
    line = _one_line(result.stderr)
    assert "`wowlab sv list`" in line, line
    assert "Errno" not in result.stderr


def test_sv_dump_of_a_missing_absolute_path_names_sv_list(root: Path, tmp_path: Path) -> None:
    gone = tmp_path / "elsewhere" / "Nope.lua"
    result = run("sv", "dump", str(gone))
    assert result.exit_code == cli.EXIT_ERROR
    assert _one_line(result.stderr) == (
        f"wowlab: no SavedVariables file at {gone}; wowlab sv list lists them, each with "
        "the path to give sv dump (relative to the flavor folder)"
    )
    assert "Errno" not in result.stderr


def test_a_missing_file_elsewhere_names_the_lister_of_its_group_constructed(
    root: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    gone = tmp_path / "gone" / "manifest.json"

    def missing(*args: object, **kwargs: object) -> str:
        raise FileNotFoundError(errno.ENOENT, "No such file or directory", str(gone))

    monkeypatch.setattr(cli, "_resolve_ref", missing)
    result = run("snap", "show", "20260101")
    assert result.exit_code == cli.EXIT_ERROR
    assert _one_line(result.stderr) == (
        f"wowlab: no file or folder at {gone}; wowlab snap list lists what there is"
    )


def test_other_system_errors_lose_their_errno_constructed() -> None:
    denied = PermissionError(errno.EACCES, "Permission denied", "/x/y.lua")
    assert cli._os_error_words(denied) == "permission denied: /x/y.lua"
    moved = OSError(errno.EXDEV, "Invalid cross-device link", "/a", None, "/b")
    assert cli._os_error_words(moved) == "invalid cross-device link: /a -> /b"
    # Outside a command there is no lister to name.
    missing = FileNotFoundError(errno.ENOENT, "No such file or directory", "/x")
    assert cli._os_error_words(missing) == "no file or folder at /x"
    assert cli._os_error_words(OSError("no errno")) == "no errno"


# ─── no install: --root and WOWLAB_WOW_ROOT ──────────────────────────────────


def test_no_install_found_names_root_and_the_variable(no_install: Path) -> None:
    result = run("cvar", "list")
    assert result.exit_code == cli.EXIT_ERROR
    assert _one_line(result.stderr) == (
        "wowlab: no WoW install found in the folders wowlab searches "
        "(1 checked, listed below); name the install as shown at the end"
    )
    assert '  --root "<path>"' in result.stderr.splitlines()
    assert any(ENV_LINE in line for line in result.stderr.splitlines())
    # M16-112: the location, then the lines that name the install, one each.
    assert f"searched, no install there: {no_install}" in result.stderr.splitlines()


def test_no_default_location_names_root_and_the_variable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(install.ENV_ROOT, raising=False)
    monkeypatch.setattr(install, "default_roots", lambda **k: ())
    stderr = run("sv", "list").stderr
    line = _one_line(stderr)
    assert "wowlab knows no default install location on this system" in line
    assert line.endswith("; name the install as shown at the end")
    assert '  --root "<path>"' in stderr.splitlines()
    assert any(ENV_LINE in out for out in stderr.splitlines())


def test_could_not_check_names_root_and_the_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(install.ENV_ROOT, raising=False)
    a_file = tmp_path / "World of Warcraft"
    a_file.write_bytes(b"")  # constructed: not a directory
    monkeypatch.setattr(install, "default_roots", lambda **k: (a_file,))
    stderr = run("install", "show").stderr
    line = _one_line(stderr)
    assert "(1 checked, listed below)" in line
    assert f"could not check: {a_file} (not a directory)" in stderr.splitlines()
    assert '  --root "<path>"' in stderr.splitlines()
    assert any(ENV_LINE in out for out in stderr.splitlines())


@pytest.mark.parametrize("how", ["--root", "env"])
@pytest.mark.parametrize("what", ["not-an-install", "absent", "flavor-folder"])
def test_a_named_root_that_is_no_install_names_root_and_the_variable(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, how: str, what: str
) -> None:
    named = {
        "not-an-install": tmp_path,
        "absent": tmp_path / "nope",
        "flavor-folder": root / FLAVOR,
    }[what]
    if how == "--root":
        result = run("cvar", "list", "--root", str(named))
    else:
        monkeypatch.setenv(install.ENV_ROOT, str(named))
        result = run("cvar", "list")
    assert result.exit_code == cli.EXIT_ERROR
    line = _one_line(result.stderr)
    assert line.startswith(f"wowlab: {named} ")
    assert line.endswith(f"; {NAMES_THE_INSTALL}; wowlab doctor says how")


def test_sv_dump_install_only_with_no_install_names_root_and_the_variable(
    no_install: Path,
) -> None:
    result = run("sv", "dump", "--install-only", "x.lua")
    assert result.exit_code == cli.EXIT_REFUSED
    line = _one_line(result.stderr)
    assert line.startswith("wowlab: refused: no WoW install found")
    # --root cannot choose the install here, so it is named but not offered.
    assert NAMES_THE_INSTALL not in line
    assert line.endswith(
        "; set WOWLAB_WOW_ROOT to the install folder (the one that holds .build.info); "
        "with --install-only, --root does not choose the install, it is only checked "
        "against the one wowlab finds by itself"
    )


def test_looks_with_no_install_names_root_and_the_variable(
    no_install: Path, user_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _Offline:
        """Constructed: a source that is never asked; the build is not known."""

        name = "offline"

        def fetch_builds(self, dest: BinaryIO) -> str:
            raise AssertionError("no request is made before the build is known")

        def fetch_table(self, table: str, build: str, dest: BinaryIO) -> str:
            raise AssertionError("no request is made before the build is known")

    @contextmanager
    def offline() -> Iterator[GameData]:
        yield GameData(_Offline(), cache_dir=user_data / "gamedata")

    monkeypatch.setattr(cli, "_open_gamedata", offline)
    result = run("looks", "options", "Human")
    assert result.exit_code == cli.EXIT_ERROR
    line = _one_line(result.stderr)
    assert NAMES_THE_INSTALL in line
    assert "Without an install, pass --build with a full version string" in line


# ─── a full store ────────────────────────────────────────────────────────────


def _full_disk(monkeypatch: pytest.MonkeyPatch) -> None:
    """Constructed: every move into the store fails as a full disk does, and
    the disk under the store has 12,345 bytes left."""

    def full(*args: object, **kwargs: object) -> None:
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(snapshot.SnapshotStore, "_publish", staticmethod(full))
    usage = shutil.disk_usage(Path.cwd())
    monkeypatch.setattr(snapshot.shutil, "disk_usage", lambda p: usage._replace(free=12345))


def test_a_full_store_is_reported_as_a_full_store_constructed(
    root: Path, user_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _full_disk(monkeypatch)
    result = run("snap", "create")
    assert result.exit_code == cli.EXIT_ERROR
    line = _one_line(result.stderr)
    assert line == (
        f"wowlab: the snapshot store at {user_data / 'store'} is full (12,345 bytes free): "
        "No space left on device; free some space on that disk and try again"
    )
    assert "cannot read" not in line


def test_a_full_store_while_writing_the_manifest_is_a_full_store_constructed(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = snapshot.SnapshotStore(tmp_path / "store")
    real = snapshot.SnapshotStore._publish

    def objects_only(staged: Any, dest: Any, name: str) -> None:
        if name.endswith(".json"):
            raise OSError(errno.EDQUOT if hasattr(errno, "EDQUOT") else errno.ENOSPC, "full")
        real(staged, dest, name)

    monkeypatch.setattr(snapshot.SnapshotStore, "_publish", staticmethod(objects_only))
    with pytest.raises(snapshot.StoreFullError, match="is full"):
        store.create(root / FLAVOR, subtrees=("WTF",))


def test_the_pre_write_snapshot_on_a_full_store_says_so_constructed(
    root: Path, user_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    created = run("snap", "create")
    assert created.exit_code == 0, created.stderr
    (manifest,) = snapshot.SnapshotStore(user_data / "store").list()
    target = root / FLAVOR / SYNDICATOR
    target.write_bytes(target.read_bytes() + b"\n-- changed\n")
    _full_disk(monkeypatch)
    result = run("snap", "restore", manifest.id, "--yes")
    assert result.exit_code == cli.EXIT_REFUSED
    line = _one_line(result.stderr)
    assert line.startswith("wowlab: refused by the write gate: ")
    assert f"the snapshot store at {user_data / 'store'} is full (12,345 bytes free)" in line
    assert "cannot read" not in line
    assert guard.history() == ()


class _FailsOnSecondPass(io.RawIOBase):
    """Constructed: reads as the file did on the hashing pass, then fails as
    a disk error does when the store reads it again to compress it."""

    def __init__(self, real: BinaryIO) -> None:
        self._real = real
        self._passes = 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def seek(self, offset: int, whence: int = 0) -> int:
        self._passes += 1
        return self._real.seek(offset, whence)

    def read(self, size: int = -1) -> bytes:
        if self._passes:
            raise OSError(errno.EIO, "Input/output error")
        return self._real.read(size)

    def close(self) -> None:
        self._real.close()
        super().close()


def test_a_read_error_is_still_cannot_read_the_install_file_constructed(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = snapshot._checked_stream
    monkeypatch.setattr(snapshot, "_checked_stream", lambda fd: _FailsOnSecondPass(real(fd)))
    store = snapshot.SnapshotStore(tmp_path / "store")
    with pytest.raises(snapshot.SnapshotError) as caught:
        store.create(root / FLAVOR, subtrees=("WTF",))
    assert not isinstance(caught.value, snapshot.StoreFullError)
    assert str(caught.value).startswith(f"cannot read {root / FLAVOR}")
    assert "Input/output error" in str(caught.value)
