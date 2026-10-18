"""`longpath`: the extended-length spelling at the system call (M13-15).

The spelling rules are pure text and are graded here on every platform
(constructed Windows path strings, no file system). Off Windows every
function is the call it wraps, with the path as given. The Windows-only
tests run in the ordinary `lab (windows)` passes, where long paths are on:
they check that a root or a flavor handed over in `\\\\?\\` form comes back
plain (`Install.root`, a manifest's `install_root`, the journal, `doctor`'s
output) and that the gate refuses, on enter, a flavor that `undo()` could
not reach. `test_long_paths_windows.py` grades the same code with long
paths off.

Every install here is a copy of the real capture `fixtures/macos/` (README
rows) under `tmp_path`; only the location is constructed, so the test ids
say `constructed` (L8). Nothing reads or writes a real install, the user
data directory is redirected into `tmp_path`, the process table holds no
client, and nothing asks wago.tools anything.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import platformdirs
import psutil
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, guard, install, longpath, snapshot
from wowlab_core.snapshot import SnapshotError, SnapshotStore

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CAPTURE = FIXTURES / "macos"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder; tests may name it (L6)
CONFIG = "WTF/Config.wtf"
EDIT = b"\n-- edited by the M13-15 test\n"
WINDOWS = pytest.mark.skipif(sys.platform != "win32", reason="extended-length paths are Windows'")

LONG = "C:\\" + "\\".join(["d" * 50] * 5) + "\\file.txt"  # 266 characters, past MAX_PATH


# ─── the spelling rules, on every platform ───────────────────────────────────


@pytest.mark.parametrize(
    ("given", "limit", "expected"),
    [
        pytest.param("C:\\a\\b", longpath.PLAIN_LIMIT, "C:\\a\\b", id="constructed-short-kept"),
        pytest.param(LONG, longpath.PLAIN_LIMIT, "\\\\?\\" + LONG, id="constructed-long-drive"),
        pytest.param("C:\\a\\b", 0, "\\\\?\\C:\\a\\b", id="constructed-always"),
        pytest.param(
            "C:/a/./x/../b", 0, "\\\\?\\C:\\a\\b", id="constructed-normalised-before-prefixing"
        ),
        pytest.param(
            "\\\\server\\share\\a", 0, "\\\\?\\UNC\\server\\share\\a", id="constructed-unc"
        ),
        pytest.param(
            "\\\\?\\C:\\a\\..\\b", 0, "\\\\?\\C:\\a\\..\\b", id="constructed-extended-kept"
        ),
        pytest.param("//?/C:/a", 0, "//?/C:/a", id="constructed-extended-forward-slashes-kept"),
        pytest.param("\\\\.\\pipe\\x", 0, "\\\\.\\pipe\\x", id="constructed-device-kept"),
    ],
)
def test_constructed_extended_text(given: str, limit: int, expected: str) -> None:
    assert longpath.extended_text(given, limit=limit) == expected


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        pytest.param("C:\\a\\foo.", "\\\\?\\C:\\a\\foo.", id="constructed-trailing-dot-kept"),
        pytest.param("C:\\a\\foo ", "\\\\?\\C:\\a\\foo ", id="constructed-trailing-space-kept"),
        pytest.param(
            "\\\\server\\share\\foo.", "\\\\?\\UNC\\server\\share\\foo.", id="constructed-unc"
        ),
        # Not clean: normalised as without `literal`.
        pytest.param("C:\\a\\..\\b", "\\\\?\\C:\\b", id="constructed-dot-dot-normalised"),
        pytest.param("C:/a/b", "\\\\?\\C:\\a\\b", id="constructed-forward-slash-normalised"),
    ],
)
def test_constructed_extended_text_literal(given: str, expected: str) -> None:
    """`literal` (what `extended(..., always=True)` uses, for `snapshot`'s
    confirmation that a listed file is gone) prefixes a clean absolute path as
    it is, so `foo.` is not looked up as `foo`."""
    assert longpath.extended_text(given, limit=0, literal=True) == expected


def test_constructed_utf16_units_counts_a_surrogate_pair_twice() -> None:
    assert longpath.utf16_units("a\U0001f600") == 3
    assert longpath.utf16_units("a\ud800") == 2  # a lone surrogate from a file name


def test_constructed_extended_text_at_the_limit() -> None:
    """The prefix starts at `PLAIN_LIMIT` characters, under every plain limit
    Windows has (`CreateDirectoryW`'s 248 is the tightest)."""
    assert longpath.PLAIN_LIMIT < 248
    under = "C:\\" + "x" * (longpath.PLAIN_LIMIT - 4)
    at = under + "x"
    assert len(at) == longpath.PLAIN_LIMIT
    assert longpath.extended_text(under) == under
    assert longpath.extended_text(at) == "\\\\?\\" + at


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        pytest.param("\\\\?\\C:\\a\\b", "C:\\a\\b", id="constructed-drive"),
        pytest.param("\\\\?\\c:\\a", "c:\\a", id="constructed-lower-case-drive"),
        pytest.param("\\\\?\\UNC\\server\\share\\a", "\\\\server\\share\\a", id="constructed-unc"),
        pytest.param("C:\\a\\b", "C:\\a\\b", id="constructed-already-plain"),
        # No plain spelling names the same path: each is kept as given.
        # Windows' own normalisation (GetFullPathNameW) drops a component's
        # trailing dot; `ntpath` elsewhere does not, so this one runs on Windows.
        pytest.param(
            "\\\\?\\C:\\a.\\b",
            "\\\\?\\C:\\a.\\b",
            id="constructed-trailing-dot",
            marks=WINDOWS,
        ),
        pytest.param("\\\\?\\C:\\a\\..\\b", "\\\\?\\C:\\a\\..\\b", id="constructed-dot-dot"),
        pytest.param("\\\\?\\C:\\a/b", "\\\\?\\C:\\a/b", id="constructed-forward-slash"),
        pytest.param(
            "\\\\?\\Volume{00000000-0000-0000-0000-000000000000}\\a",
            "\\\\?\\Volume{00000000-0000-0000-0000-000000000000}\\a",
            id="constructed-volume-guid",
        ),
    ],
)
def test_constructed_plain_text(given: str, expected: str) -> None:
    assert longpath.plain_text(given) == expected


@pytest.mark.parametrize(
    "spelled",
    [
        pytest.param(LONG, id="constructed-drive"),
        pytest.param("\\\\server\\share\\" + "s" * 250, id="constructed-unc"),
    ],
)
def test_constructed_plain_text_undoes_extended_text(spelled: str) -> None:
    assert longpath.plain_text(longpath.extended_text(spelled, limit=0)) == spelled


@pytest.mark.skipif(sys.platform == "win32", reason="the identity off Windows")
def test_constructed_off_windows_every_spelling_is_the_path_as_given(tmp_path: Path) -> None:
    deep = tmp_path / ("y" * 200) / ("z" * 200)
    assert longpath.extended(deep) is deep
    assert longpath.extended(deep, always=True) is deep
    assert longpath.xpath(deep) is deep
    odd = "\\\\?\\C:\\not-a-windows-path"  # a legal POSIX file name
    assert longpath.plain(odd) == Path(odd)


def test_constructed_realpath_and_lexists_are_the_os_calls_on_a_short_path(tmp_path: Path) -> None:
    """M13-44: `realpath` and `lexists` answer as `os.path` does for a path
    Windows opens plainly (every path off Windows), a dangling link and a
    link's target included; `realpath` never names a `\\\\?\\` path."""
    real = tmp_path / "real"
    real.mkdir()
    (real / "file.txt").write_bytes(b"")
    for path in (real / "file.txt", real / "missing.txt", tmp_path / "real" / ".." / "real"):
        assert longpath.realpath(path) == os.path.realpath(path)
        assert longpath.lexists(path) == os.path.lexists(path)
        assert not longpath.realpath(path).startswith("\\\\?\\")
    if sys.platform != "win32":
        (tmp_path / "dangling").symlink_to(tmp_path / "nowhere")
        (tmp_path / "linked").symlink_to(real, target_is_directory=True)
        assert longpath.lexists(tmp_path / "dangling") is True
        assert longpath.realpath(tmp_path / "linked" / "file.txt") == os.path.realpath(
            real / "file.txt"
        )


@WINDOWS
def test_constructed_realpath_of_an_extended_spelling_is_plain(tmp_path: Path) -> None:
    """M13-44: a `\\\\?\\` path with a plain spelling resolves to that plain
    spelling, so two results compare whatever spelling each was given in."""
    (tmp_path / "file.txt").write_bytes(b"")
    spelled = longpath.extended_text(str(tmp_path / "file.txt"), limit=0)
    assert longpath.realpath(spelled) == longpath.realpath(tmp_path / "file.txt")
    assert not longpath.realpath(spelled).startswith("\\\\?\\")
    assert longpath.lexists(spelled) is True


def test_constructed_plain_errors_names_the_plain_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(longpath, "ON_WINDOWS", True)
    with pytest.raises(FileNotFoundError) as raised, longpath.plain_errors():
        raise FileNotFoundError(2, "not found", "\\\\?\\" + LONG, None, "\\\\?\\C:\\b")
    assert raised.value.filename == LONG
    assert raised.value.filename2 == "C:\\b"
    assert "\\\\?\\" not in str(raised.value)


def test_constructed_plain_errors_leaves_names_alone_off_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(longpath, "ON_WINDOWS", False)
    name = "\\\\?\\C:\\a"
    with pytest.raises(FileNotFoundError) as raised, longpath.plain_errors():
        raise FileNotFoundError(2, "not found", name)
    assert raised.value.filename == name


# ─── a snapshot never skips a file the system only says is missing ──────────


def _source(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    (root / "WTF" / "Account").mkdir(parents=True)
    (root / "WTF" / "Config.wtf").write_bytes(b'SET constructed "1"\n')
    (root / "WTF" / "Account" / "a.txt").write_bytes(b"constructed a\n")
    (root / "WTF" / "Account" / "b.txt").write_bytes(b"constructed b\n")
    return root


def test_constructed_snapshot_raises_when_a_file_said_missing_is_there(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Before skipping a file for `FileNotFoundError`, `create` confirms it is
    gone with an extended-length `lstat`; a file still there raises, and no
    manifest is written (a partial snapshot never is)."""
    root = _source(tmp_path)
    store = SnapshotStore(tmp_path / "store")
    real = snapshot.open_regular_file

    def refuse_b(path: Path, **kwargs: Any) -> Any:
        if Path(path).name == "b.txt":
            raise FileNotFoundError(2, "constructed: reported missing", os.fspath(path))
        return real(path, **kwargs)

    monkeypatch.setattr(snapshot, "open_regular_file", refuse_b)
    with pytest.raises(SnapshotError, match="but it is there"):
        store.create(root, ["WTF"])
    assert store.list() == ()


def test_constructed_snapshot_skips_a_file_removed_while_it_walked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _source(tmp_path)
    store = SnapshotStore(tmp_path / "store")
    real = snapshot.open_regular_file

    def remove_b(path: Path, **kwargs: Any) -> Any:
        if Path(path).name == "b.txt":
            Path(path).unlink()
        return real(path, **kwargs)

    monkeypatch.setattr(snapshot, "open_regular_file", remove_b)
    manifest = store.create(root, ["WTF"])
    assert [e.path for e in manifest.entries] == ["WTF/Account/a.txt", "WTF/Config.wtf"]


def test_constructed_snapshot_raises_when_a_folder_said_missing_is_there(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _source(tmp_path)
    store = SnapshotStore(tmp_path / "store")
    real = longpath.entries

    def refuse_account(path: Any) -> Any:
        if Path(path).name == "Account":
            raise FileNotFoundError(3, "constructed: reported missing", os.fspath(path))
        return real(path)

    monkeypatch.setattr(longpath, "entries", refuse_account)
    with pytest.raises(SnapshotError, match="but it is there"):
        store.create(root, ["WTF"])
    assert store.list() == ()


# ─── Windows: a root or flavor handed over in `\\?\` form ────────────────────


def _x(path: Path) -> str:
    """`path` in the `\\\\?\\` form a caller might hand over."""
    return longpath.extended_text(str(path), limit=0)


@dataclass(frozen=True)
class _Flavor:
    path: Path
    version: str | None = None


class _Idle:
    """A process with no client in it (the `psutil.Process` surface read)."""

    pid = 4242

    def name(self) -> str:
        return "python3"

    def exe(self) -> str:
        return "/elsewhere/python3"

    def cmdline(self) -> list[str]:
        return [self.exe()]

    def status(self) -> str:
        return "running"


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """No client running, no network, the user data directory in `tmp_path`."""
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: iter([_Idle()]))

    def no_network() -> Iterator[Any]:
        raise AssertionError("no test in this module may ask wago.tools anything")

    monkeypatch.setattr(cli, "_open_gamedata", no_network)
    data = tmp_path / "data" / "wowlab"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: data)
    monkeypatch.delenv(install.ENV_ROOT, raising=False)
    return data / "store"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """A copy of the real capture as an install, at a short location."""
    where = tmp_path / "World of Warcraft"
    where.mkdir()
    shutil.copy2(CAPTURE / ".build.info", where / ".build.info")
    shutil.copytree(CAPTURE / "forever", where / FLAVOR)
    return where


@WINDOWS
def test_constructed_discovery_of_an_extended_root_reports_it_plain(root: Path) -> None:
    found = install.discover(_x(root), environ={}, defaults=())
    assert found.root == root
    assert [f.path for f in found.flavors] == [root / FLAVOR]
    from_env = install.discover(environ={install.ENV_ROOT: _x(root)}, defaults=())
    assert from_env.root == root


def _strings(value: Any) -> Iterator[str]:
    """Every string in a JSON value, keys included."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


@WINDOWS
def test_constructed_doctor_of_an_extended_root_says_nothing_extended(
    root: Path, store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(install.ENV_ROOT, _x(root))
    result = CliRunner().invoke(cli.app, ["doctor", "--offline", "--json"])
    assert result.exit_code == 0, (result.stdout, result.stderr, result.exception)
    report = json.loads(result.stdout)
    assert report["install_root"] == str(root)
    extended = [s for s in _strings(report) if "\\\\?\\" in s]
    assert extended == [], "no extended-length prefix in doctor's output"


@WINDOWS
def test_constructed_snapshot_of_an_extended_root_names_it_plain(root: Path, store: Path) -> None:
    manifest = SnapshotStore(store).create(_x(root), [f"{FLAVOR}/WTF"], flavor_folder=FLAVOR)
    assert manifest.install_root == str(root)
    assert manifest.entry(f"{FLAVOR}/{CONFIG}") is not None


@WINDOWS
def test_constructed_guard_through_an_extended_flavor_journals_it_plain_and_undoes(
    root: Path, store: Path
) -> None:
    """The choice M13-15 made for a flavor in `\\\\?\\C:\\...` form: it is taken
    by its plain spelling, so the journal and the pre-write manifest name a
    local root and `undo()` reverses the write."""
    config = root / FLAVOR / "WTF" / "Config.wtf"
    original = config.read_bytes()
    with guard.transaction(_Flavor(Path(_x(root / FLAVOR))), label="x", store=store) as tx:
        tx.write(CONFIG, original + EDIT)
    (record,) = guard.history(store=store)
    assert record.flavor_path == str(root / FLAVOR)
    assert SnapshotStore(store).show(record.snapshot_id).install_root == str(root)
    journal = b"".join(p.read_bytes() for p in sorted((store / "journal").glob("*.json")))
    assert b"\\\\\\\\?\\\\" not in journal and b"?\\\\UNC" not in journal
    guard.undo(store=store)
    assert config.read_bytes() == original


def _share(path: Path) -> str:
    """`C:\\a` named through the drive's administrative share."""
    text = str(path)
    return f"\\\\localhost\\{text[0]}$\\{text[3:]}"


@WINDOWS
@pytest.mark.parametrize("spelling", ["unc", "extended-unc"])
def test_constructed_guard_refuses_a_network_flavor_on_enter(
    root: Path, store: Path, spelling: str
) -> None:
    """`undo()` and restore accept only a local absolute root (§6.10, item 1),
    so a write through a network path could not be reversed: the gate
    refuses such a flavor on enter, with nothing written or journaled, in
    either spelling (`\\\\server\\share\\...` or `\\\\?\\UNC\\...`)."""
    unc = _share(root / FLAVOR)
    try:
        Path(unc).stat()
    except OSError as exc:
        pytest.skip(f"the administrative share does not open: {unc} ({exc})")
    flavor = Path(unc if spelling == "unc" else longpath.extended_text(unc, limit=0))
    config = root / FLAVOR / "WTF" / "Config.wtf"
    before = config.read_bytes()
    with (
        pytest.raises(guard.GuardError, match="not a local absolute path"),
        guard.transaction(_Flavor(flavor), label="network", store=store) as tx,
    ):
        tx.write(CONFIG, before + EDIT)
    assert config.read_bytes() == before
    assert guard.history(store=store) == ()


@WINDOWS
def test_constructed_snapshot_raises_for_a_listed_name_with_a_trailing_dot(
    tmp_path: Path, store: Path
) -> None:
    """A file named `foo.` (made through `\\\\?\\`) is listed, but its plain
    path opens `foo`, which is not there. The confirmation looks at `foo.`
    itself, finds it, and the `create` raises: never a silent skip."""
    root = _source(tmp_path)
    odd = longpath.extended_text(str(root / "WTF" / "foo."), limit=0, literal=True)
    Path(odd).write_bytes(b"constructed\n")
    try:
        with pytest.raises(SnapshotError, match="but it is there"):
            SnapshotStore(store).create(root, ["WTF"])
        assert SnapshotStore(store).list() == ()
    finally:
        Path(odd).unlink()


@WINDOWS
def test_constructed_discovery_refuses_an_extended_root_with_no_plain_spelling(
    root: Path,
) -> None:
    """`Install.root` is always plain: a `\\\\?\\` root with a `..` component
    has no plain spelling, and is reported as not an install, before any
    lookup."""
    spelled = _x(root) + "\\..\\" + root.name
    with pytest.raises(install.NotAnInstallError, match="no plain spelling"):
        install.discover(spelled, environ={}, defaults=())


@pytest.mark.skipif(sys.platform == "win32", reason="`//` begins a network path on Windows")
def test_constructed_guard_takes_a_posix_flavor_spelled_with_a_double_slash(
    root: Path, store: Path
) -> None:
    """Off Windows, `//x` is the local `/x`: the gate takes such a flavor as
    before M13-15, journals the resolved path, and `undo()` reverses it."""
    flavor = Path("/" + str(root / FLAVOR))
    assert str(flavor).startswith("//")
    config = root / FLAVOR / "WTF" / "Config.wtf"
    original = config.read_bytes()
    with guard.transaction(_Flavor(flavor), label="double slash", store=store) as tx:
        tx.write(CONFIG, original + EDIT)
    assert config.read_bytes() == original + EDIT
    (record,) = guard.history(store=store)
    assert not record.flavor_path.startswith("//")
    guard.undo(store=store)
    assert config.read_bytes() == original


UNREACHABLE = "\\\\wowlab-nonexistent.invalid\\share\\World of Warcraft\\" + FLAVOR


@WINDOWS
@pytest.mark.parametrize(
    "flavor",
    [
        pytest.param(UNREACHABLE, id="constructed-unc"),
        pytest.param(longpath.extended_text(UNREACHABLE, limit=0), id="constructed-extended-unc"),
    ],
)
def test_constructed_guard_refuses_an_unreachable_network_flavor_before_any_lookup(
    store: Path, flavor: str
) -> None:
    """Never skips: a network flavor on a server that does not exist is
    refused on enter as not a local absolute path, with nothing journaled.
    The refusal comes before any lookup: one made after an `lstat` or a
    `resolve` would be a different error, or a wait on the network."""
    with (
        pytest.raises(guard.GuardError, match="not a local absolute path"),
        guard.transaction(_Flavor(Path(flavor)), label="unreachable", store=store) as tx,
    ):
        tx.write(CONFIG, b"never")
    assert guard.history(store=store) == ()
