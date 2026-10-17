"""Graders: long paths on Windows with `LongPathsEnabled` off (M13-15T).

Found by M13-02. With `LongPathsEnabled` = 0, the default on many player
PCs, Windows refuses a plain path of `MAX_PATH` (260 UTF-16 units, the
terminator included) or more; only an extended-length path (`\\\\?\\C:\\...`)
reaches such a file. The Lab passes plain `pathlib` paths, so `snapshot`,
`guard` and the readers cannot reach a file deep in an install. M13-15 makes
them reach it. Every grader here carries one marker line,
`@pytest.mark.xfail(strict=True, reason="M13-15 not implemented")`, which
M13-15 deletes and does not otherwise change.

What is graded, each on the same operations a player's loop runs:

- `SnapshotStore.create` captures every file of the subtrees, the deep one
  included, with its bytes;
- `guard`: a write of the deep file (with its pre-write snapshot and journal
  record), its `undo()`, and a `restore` of it from a snapshot;
- the readers: `install.discover` and `wowlab doctor` on an install whose
  root is past `MAX_PATH`, and the format parsers (`luadata`, `wtfconfig`,
  `toc`, `combatlog`) on a real file past it.

The layouts (`_build`), each a copy of the real capture `fixtures/macos/`
(README rows) under `tmp_path`; only the location is constructed, so every
test id says `constructed` (L8):

- `deep-root`: the install root itself is past `MAX_PATH`, so is every path
  in it (M13-02's case);
- `deep-dir`: a short root, and a real `.toc` copied into an addon folder
  whose directory path is past `MAX_PATH` (addon libraries nest);
- `long-name`: a short root, and a real `.toc` copied beside the original
  under a long file name, so the directory is short and the file path is past
  `MAX_PATH`;
- `at-limit`: a short root, and a real `.toc` whose path is exactly 259
  characters, the longest plain path Windows opens. The file is reachable;
  a file the gate creates beside it (a temp file, say) need not be. The
  gate must still write and restore it;
- `short`: every path well under the limit. The controls run each operation
  here, under the same conditions, unmarked: they must pass on `main`, so a
  grader that fails does so because of the path's length.

How the reason is pinned: the trees are built and checked by the test
through extended-length paths, in fixtures, so a tree that cannot be built
is an error, never an expected failure (M13-02's experiment failed there).
`test_constructed_windows_refuses_the_plain_path_of_each_long_layout` shows
the operating system refusing each plain path, and the controls show the
same operations passing where the path is short. The Lab is only ever handed
plain paths, as a caller would.

Where this runs: Windows only (`skipif` below). Every test also needs plain
long paths to be refused in this process, which a fixture probes; where they
work (`LongPathsEnabled` = 1, the runner image's default) the module skips,
since a grader would pass and a strict marker would fail. The `lab
(windows)` job turns the setting off in a step and runs this module alone
with `WOWLAB_EXPECT_LONG_PATHS_OFF=1`; with that set, a process in which
plain long paths work is an error, so the step can never pass by skipping.

The user data directory is redirected into `tmp_path`, the process table is
a fake one without a client in it, and nothing asks wago.tools anything
(`doctor --offline`; the game-data opener is replaced by one that fails).
Nothing reads or writes a real install.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import platformdirs
import psutil
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, combatlog, guard, install, luadata, toc, wtfconfig
from wowlab_core.snapshot import Manifest, SnapshotStore

pytestmark = pytest.mark.skipif(
    sys.platform != "win32", reason="MAX_PATH and LongPathsEnabled are Windows facilities"
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CAPTURE = FIXTURES / "macos"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder; tests may name it (L6)
INSTALL = "World of Warcraft"
SYNDICATOR = "WTF/Account/90000001#6/SavedVariables/Syndicator.lua"
BRAWLERS = "Interface/AddOns/DBM-Brawlers"
TOC = f"{BRAWLERS}/DBM-Brawlers.toc"
SUBTREES = (f"{FLAVOR}/WTF", f"{FLAVOR}/Interface")
EDIT = b"\n-- edited by the M13-15T grader\n"

MAX_PATH = 260  # UTF-16 units, the terminator included
LONGEST_PLAIN = MAX_PATH - 1  # the longest plain path Windows opens with the setting off
ROOM = 50  # a short layout leaves this much room for a name created beside any file
EXPECT_OFF = "WOWLAB_EXPECT_LONG_PATHS_OFF"
REFUSED = {2, 3, 206}  # ERROR_FILE_NOT_FOUND, ERROR_PATH_NOT_FOUND, ERROR_FILENAME_EXCED_RANGE

LONG = ("deep-root", "deep-dir", "long-name")

runner = CliRunner()


# ─── paths the test itself uses ──────────────────────────────────────────────


def _x(path: Path) -> Path:
    """`path` as an extended-length path, which Windows opens whatever
    `LongPathsEnabled` says. Only the test uses these, to build and check
    the trees; the Lab is handed plain paths."""
    text = str(path)
    if sys.platform != "win32" or text.startswith("\\\\?\\"):
        return path
    assert len(text) > 3 and text[1:3] == ":\\", f"a drive-letter absolute path: {text}"
    return Path("\\\\?\\" + text)


def _read(path: Path) -> bytes:
    return _x(path).read_bytes()


def _write(path: Path, data: bytes) -> None:
    _x(path).write_bytes(data)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _segments(base: Path, prefix: str, done: Callable[[Path], bool]) -> Path:
    """`base` extended by 50-character components until `done` holds."""
    path, n = base, 0
    while not done(path):
        path = path / (f"{prefix}-{n:02d}-".ljust(50, "x"))
        n += 1
    return path


# ─── the layouts ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Tree:
    """A copy of the real capture as an install, at a constructed location."""

    layout: str
    root: Path
    target: str
    """Flavor-relative, `/`-separated: the file the layout is about."""
    source: Path
    """The capture file whose bytes `target` holds."""

    @property
    def flavor(self) -> Path:
        return self.root / FLAVOR

    @property
    def target_path(self) -> Path:
        return self.flavor.joinpath(*self.target.split("/"))


def _copy_install(root: Path) -> None:
    _x(root).mkdir(parents=True)
    shutil.copy2(CAPTURE / ".build.info", _x(root / ".build.info"))
    shutil.copytree(CAPTURE / "forever", _x(root / FLAVOR))


def _files(top: Path) -> dict[str, Path]:
    """Every file under `top`, by `top`-relative `/`-separated name, walked
    through extended-length paths; the values are plain paths."""
    base = _x(top)
    found: dict[str, Path] = {}
    for dirpath, _dirs, names in base.walk():
        for name in names:
            rel = (dirpath / name).relative_to(base).as_posix()
            found[rel] = top.joinpath(*rel.split("/"))
    return found


def _build(where: Path, layout: str) -> Tree:
    """The install for `layout` under `where`, built and checked through
    extended-length paths (so a refusal here is an error, not the grader's
    expected failure)."""
    if layout == "deep-root":
        root = _segments(where, "games-library", lambda p: len(str(p / INSTALL)) > MAX_PATH)
        root = root / INSTALL
        _copy_install(root)
        tree = Tree(layout, root, SYNDICATOR, CAPTURE / "forever" / SYNDICATOR)
        assert len(str(root / ".build.info")) > MAX_PATH
    else:
        root = where / INSTALL
        _copy_install(root)
        source = CAPTURE / "forever" / TOC
        brawlers = root / FLAVOR / BRAWLERS
        if layout == "short":
            tree = Tree(layout, root, SYNDICATOR, CAPTURE / "forever" / SYNDICATOR)
        elif layout == "deep-dir":
            deep = _segments(brawlers / "Libs", "LibDeep", lambda p: len(str(p)) > MAX_PATH)
            rel = deep.relative_to(root / FLAVOR).as_posix()
            tree = Tree(layout, root, f"{rel}/DBM-Brawlers.toc", source)
            assert len(str(tree.target_path.parent)) > MAX_PATH
        elif layout == "long-name":
            length = MAX_PATH + 20 - len(str(brawlers)) - 1
            name = "DBM-Brawlers-".ljust(length - len(".toc"), "n") + ".toc"
            assert len(name) <= 255, "one component stays a legal file name"
            tree = Tree(layout, root, f"{BRAWLERS}/{name}", source)
            assert len(str(tree.target_path)) == MAX_PATH + 20
            assert len(str(tree.target_path.parent)) + 1 + ROOM < MAX_PATH
        elif layout == "at-limit":
            libs = brawlers / "Libs"
            pad = LONGEST_PLAIN - len(str(libs)) - 1 - 1 - len("DBM-Brawlers.toc")
            assert 1 <= pad <= 255, f"the runner's temp folder leaves room for a pad: {pad}"
            rel = f"{BRAWLERS}/Libs/{'p' * pad}/DBM-Brawlers.toc"
            tree = Tree(layout, root, rel, source)
            assert len(str(tree.target_path)) == LONGEST_PLAIN
        else:
            raise AssertionError(f"no layout {layout!r}")
        if layout != "short":
            _x(tree.target_path.parent).mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, _x(tree.target_path))
    assert _read(tree.target_path) == tree.source.read_bytes()
    if layout in ("short", "at-limit"):
        for path in _files(root).values():
            assert len(str(path)) <= LONGEST_PLAIN, f"{layout}: {path} is plain-openable"
            if path != tree.target_path:
                spare = LONGEST_PLAIN - len(str(path.parent)) - 1
                assert spare > ROOM, f"{layout}: room beside {path} for another name"
    return tree


@pytest.fixture
def build(tmp_path: Path) -> Iterator[Callable[[str], Tree]]:
    """Builds layouts under `tmp_path`, each in a folder of its own; removes
    them afterwards through extended-length paths (pytest's own clean-up
    passes plain ones)."""
    made: list[Path] = []

    def make(layout: str) -> Tree:
        where = tmp_path / str(len(made))
        made.append(where)
        return _build(where, layout)

    yield make
    for where in made:
        shutil.rmtree(_x(where), ignore_errors=True)


@pytest.fixture
def tree(request: pytest.FixtureRequest, build: Callable[[str], Tree]) -> Tree:
    layout = request.param
    assert isinstance(layout, str)
    return build(layout)


# ─── the conditions every test runs under ────────────────────────────────────


@pytest.fixture(scope="session")
def plain_long_paths(tmp_path_factory: pytest.TempPathFactory) -> str | None:
    """None when this process cannot open a plain path past `MAX_PATH` (the
    condition the graders need), else a sentence saying it could."""
    base = tmp_path_factory.mktemp("probe")
    deep = _segments(base, "probe", lambda p: len(str(p)) > MAX_PATH)
    _x(deep).mkdir(parents=True)
    try:
        deep.stat()
    except OSError:
        return None
    finally:
        shutil.rmtree(_x(base), ignore_errors=True)
    return f"stat of the plain {len(str(deep))}-character path {deep} succeeded"


@pytest.fixture(autouse=True)
def _long_paths_off(plain_long_paths: str | None) -> None:
    if plain_long_paths is None:
        return
    if os.environ.get(EXPECT_OFF) == "1":
        pytest.fail(
            f"{EXPECT_OFF}=1 but plain long paths work in this process "
            f"({plain_long_paths}); the step that sets LongPathsEnabled=0 did not take"
        )
    pytest.skip(
        "plain paths past MAX_PATH work in this process (LongPathsEnabled is on); "
        "these graders need it off"
    )


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


@pytest.fixture(autouse=True)
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """No client running, no network, the user data directory in `tmp_path`;
    returns the snapshot store's path there."""
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: iter([_Idle()]))

    def no_network() -> Iterator[Any]:
        raise AssertionError("no test in this module may ask wago.tools anything")

    monkeypatch.setattr(cli, "_open_gamedata", no_network)
    data = tmp_path / "data" / "wowlab"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: data)
    monkeypatch.delenv(install.ENV_ROOT, raising=False)
    return data / "store"


@dataclass(frozen=True)
class _Flavor:
    """What `guard` reads from a flavor (`install.Flavor` has more). Not
    found by discovery, so a `guard` grader does not depend on `install`."""

    path: Path
    version: str | None = None


# ─── the operations, shared by graders and controls ──────────────────────────


def _snapshot(tree: Tree, store: Path) -> Manifest:
    return SnapshotStore(store).create(
        tree.root, SUBTREES, label="long paths", flavor_folder=FLAVOR, client_running=False
    )


def _check_snapshot(tree: Tree, store: Path) -> None:
    """Every file of the subtrees is captured with its bytes, the layout's
    file included; the store verifies."""
    manifest = _snapshot(tree, store)
    expected = {
        rel: path
        for rel, path in _files(tree.root).items()
        if any(rel.startswith(sub + "/") for sub in SUBTREES)
    }
    assert {e.path for e in manifest.entries} == set(expected), "nothing skipped, nothing extra"
    key = f"{FLAVOR}/{tree.target}"
    entry = manifest.entry(key)
    assert entry is not None and entry.kind == "file"
    data = tree.source.read_bytes()
    assert (entry.sha256, entry.size) == (_sha(data), len(data))
    stored = SnapshotStore(store)
    assert stored.read_file(manifest.id, key) == data
    for e in manifest.entries:
        assert e.sha256 == _sha(_read(expected[e.path])), e.path
    assert stored.verify().ok


def _check_restore(tree: Tree, store: Path) -> None:
    """A snapshot, an edit made outside the Lab, then `tx.restore`: the file
    holds its snapshot bytes again and the journal names that one path."""
    manifest = _snapshot(tree, store)
    original = tree.source.read_bytes()
    edited = original + EDIT
    _write(tree.target_path, edited)
    before = set(_files(tree.root))

    with guard.transaction(_Flavor(tree.flavor), label="restore", store=store) as tx:
        tx.restore(manifest.id)

    assert _read(tree.target_path) == original
    assert set(_files(tree.root)) == before, "no file left behind in the install"
    (record,) = guard.history(store=store)
    assert (record.state, record.rolled_back) == ("committed", False)
    assert [(p.path, p.before, p.after) for p in record.paths] == [
        (tree.target, _sha(edited), _sha(original))
    ]


def _check_write_and_undo(tree: Tree, store: Path) -> None:
    """`tx.write` replaces the file after a pre-write snapshot that holds its
    old bytes; `undo()` puts them back."""
    original = tree.source.read_bytes()
    new = original + EDIT
    before = set(_files(tree.root))

    with guard.transaction(_Flavor(tree.flavor), label="write", store=store) as tx:
        tx.write(tree.target, new)

    assert _read(tree.target_path) == new
    assert set(_files(tree.root)) == before, "no file left behind in the install"
    (record,) = guard.history(store=store)
    assert (record.state, record.rolled_back) == ("committed", False)
    assert [(p.path, p.before, p.after) for p in record.paths] == [
        (tree.target, _sha(original), _sha(new))
    ]
    pre = SnapshotStore(store).read_file(record.snapshot_id, f"{FLAVOR}/{tree.target}")
    assert pre == original, "the pre-write snapshot holds the old bytes"

    guard.undo(store=store)
    assert _read(tree.target_path) == original
    assert set(_files(tree.root)) == before
    assert [r.state for r in guard.history(store=store)] == ["committed", "committed"]


def _discover(tree: Tree, source: str) -> install.Install:
    if source == "argument":
        return install.discover(tree.root, environ={}, defaults=())
    return install.discover(environ={install.ENV_ROOT: str(tree.root)}, defaults=())


def _check_discovery(tree: Tree, short: install.Install, source: str) -> None:
    """The install reads as the same install read at a short root, under the
    plain root the caller named."""
    found = _discover(tree, source)
    assert found.root == tree.root
    assert [f.path for f in found.flavors] == [tree.flavor]
    assert found.raw_build_info == (CAPTURE / ".build.info").read_bytes()
    drop: Any = {"root": True, "flavors": {"__all__": {"path"}}}
    assert found.model_dump(exclude=drop) == short.model_dump(exclude=drop)


def _doctor(tree: Tree, monkeypatch: pytest.MonkeyPatch) -> cli.DoctorReport:
    monkeypatch.setenv(install.ENV_ROOT, str(tree.root))
    result = runner.invoke(cli.app, ["doctor", "--offline", "--json"])
    assert result.exit_code == 0, (result.stdout, result.stderr, result.exception)
    return cli.DoctorReport.model_validate_json(result.stdout)


def _check_doctor(tree: Tree, short: cli.DoctorReport, monkeypatch: pytest.MonkeyPatch) -> None:
    """`doctor` says of the install what it says of the same install at a
    short root: same flavors, client, first-run survey and no problems."""
    report = _doctor(tree, monkeypatch)
    assert report.install_root == str(tree.root)
    assert report.problems == []
    assert report.model_dump(exclude={"install_root"}) == short.model_dump(exclude={"install_root"})


def _read_log(path: Path) -> list[combatlog.Entry]:
    return list(combatlog.read_log(path))


def _parse_log(data: bytes) -> list[combatlog.Entry]:
    return list(combatlog.tokenize(data))


READERS: dict[str, tuple[Callable[[Path], object], Callable[[bytes], object], str]] = {
    "luadata": (luadata.read, luadata.parse, SYNDICATOR),
    "wtfconfig-config": (wtfconfig.read_config, wtfconfig.parse_config, "WTF/Config.wtf"),
    "wtfconfig-bindings": (
        wtfconfig.read_bindings,
        wtfconfig.parse_bindings,
        "WTF/Account/90000001#6/bindings-cache.wtf",
    ),
    "wtfconfig-macros": (
        wtfconfig.read_macros,
        wtfconfig.parse_macros,
        "WTF/Account/90000001#6/1/Labcharb-Labrealmd/macros-cache.txt",
    ),
    "toc": (toc.read_toc, toc.parse_toc, TOC),
    "combatlog": (_read_log, _parse_log, "Logs/WoWCombatLog-040126_021630.txt"),
}


def _check_reader(tree: Tree, reader: str, rel: str | None) -> None:
    """The reader, handed the plain path, returns what parsing the capture's
    bytes returns."""
    read, parse, default = READERS[reader]
    rel = default if rel is None else rel
    source = CAPTURE / "forever" / default
    path = tree.flavor.joinpath(*rel.split("/"))
    assert _read(path) == source.read_bytes()
    assert read(path) == parse(source.read_bytes())


# ─── the precondition: why the graders fail ─────────────────────────────────


@pytest.mark.parametrize("tree", LONG, indirect=True)
def test_constructed_windows_refuses_the_plain_path_of_each_long_layout(tree: Tree) -> None:
    """With the setting off, the plain path of each long layout's file is
    refused by the operating system, and its extended-length path is not:
    the reason the graders fail before M13-15."""
    with pytest.raises(OSError) as refused:
        tree.target_path.stat()
    assert getattr(refused.value, "winerror", None) in REFUSED, refused.value
    assert _x(tree.target_path).stat().st_size == tree.source.stat().st_size


@pytest.mark.parametrize("tree", ["at-limit"], indirect=True)
def test_constructed_windows_opens_the_plain_path_at_the_limit(tree: Tree) -> None:
    """259 characters is the longest plain path Windows opens with the
    setting off; one more character beside it is refused. So the at-limit
    file is reachable, and a name the gate creates beside it may not be."""
    assert len(str(tree.target_path)) == LONGEST_PLAIN
    assert tree.target_path.stat().st_size == tree.source.stat().st_size
    beside = tree.target_path.with_name(tree.target_path.name + "x")
    _write(beside, b"")
    with pytest.raises(OSError) as refused:
        beside.stat()
    assert getattr(refused.value, "winerror", None) in REFUSED, refused.value


# ─── controls: the same operations at a short location pass on main ────────


@pytest.mark.parametrize("tree", ["short", "at-limit"], indirect=True)
def test_constructed_control_snapshot_where_every_path_is_plain_openable(
    tree: Tree, store: Path
) -> None:
    _check_snapshot(tree, store)


@pytest.mark.parametrize("tree", ["short"], indirect=True)
def test_constructed_control_restore_at_a_short_location(tree: Tree, store: Path) -> None:
    _check_restore(tree, store)


@pytest.mark.parametrize("tree", ["short"], indirect=True)
def test_constructed_control_guard_write_and_undo_at_a_short_location(
    tree: Tree, store: Path
) -> None:
    _check_write_and_undo(tree, store)


@pytest.mark.parametrize("source", ["argument", "environment"])
def test_constructed_control_discovery_at_a_short_root(
    build: Callable[[str], Tree], source: str
) -> None:
    short = build("short")
    reference = install.read_install(build("short").root)
    _check_discovery(short, reference, source)
    assert [(f.folder, f.matching_rows) for f in reference.flavors] == [(FLAVOR, 1)]


def test_constructed_control_doctor_at_a_short_root(
    build: Callable[[str], Tree], monkeypatch: pytest.MonkeyPatch
) -> None:
    short = build("short")
    report = _doctor(short, monkeypatch)
    assert report.install_root == str(short.root)
    assert report.problems == []
    assert [f.folder for f in report.flavors] == [FLAVOR]
    assert report.client is not None and report.client.state == "not_running"
    assert report.first_run is not None
    assert [f.unreadable for f in report.first_run.flavors] == [[]]


@pytest.mark.parametrize("reader", sorted(READERS))
def test_constructed_control_reader_at_a_short_root(
    build: Callable[[str], Tree], reader: str
) -> None:
    _check_reader(build("short"), reader, None)


# ─── the graders ─────────────────────────────────────────────────────────────


@pytest.mark.xfail(strict=True, reason="M13-15 not implemented")
@pytest.mark.parametrize("tree", LONG, indirect=True)
def test_constructed_snapshot_captures_a_file_past_max_path(tree: Tree, store: Path) -> None:
    """`snap create` (`SnapshotStore.create`) captures the file past
    `MAX_PATH` and every other file of the subtrees, byte for byte."""
    _check_snapshot(tree, store)


@pytest.mark.xfail(strict=True, reason="M13-15 not implemented")
@pytest.mark.parametrize("tree", [*LONG, "at-limit"], indirect=True)
def test_constructed_guard_restores_the_deep_file_from_a_snapshot(tree: Tree, store: Path) -> None:
    """`snap restore` (`tx.restore`) puts the deep file's snapshot bytes back,
    journaled; at the limit too, where the file is reachable but a name
    beside it is not."""
    _check_restore(tree, store)


@pytest.mark.xfail(strict=True, reason="M13-15 not implemented")
@pytest.mark.parametrize("tree", [*LONG, "at-limit"], indirect=True)
def test_constructed_guard_writes_and_undoes_the_deep_file(tree: Tree, store: Path) -> None:
    """A gate write of the deep file: snapshot first, journaled, atomically
    replaced, no temp file left; `undo()` puts the old bytes back."""
    _check_write_and_undo(tree, store)


@pytest.mark.xfail(strict=True, reason="M13-15 not implemented")
@pytest.mark.parametrize("source", ["argument", "environment"])
def test_constructed_discovery_reads_an_install_rooted_past_max_path(
    build: Callable[[str], Tree], source: str
) -> None:
    """`install.discover` finds and reads the install whose `.build.info` is
    past `MAX_PATH`, as it reads the same install at a short root."""
    short = install.read_install(build("short").root)
    _check_discovery(build("deep-root"), short, source)


@pytest.mark.xfail(strict=True, reason="M13-15 not implemented")
def test_constructed_doctor_reports_an_install_rooted_past_max_path(
    build: Callable[[str], Tree], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`wowlab doctor --offline` reports the deep-rooted install exactly as
    it reports the same install at a short root, with no problems."""
    short = _doctor(build("short"), monkeypatch)
    _check_doctor(build("deep-root"), short, monkeypatch)


@pytest.mark.xfail(strict=True, reason="M13-15 not implemented")
@pytest.mark.parametrize(
    ("layout", "reader"),
    [
        *[("deep-root", reader) for reader in sorted(READERS)],
        ("deep-dir", "toc"),
        ("long-name", "toc"),
    ],
)
def test_constructed_reader_parses_a_real_file_past_max_path(
    build: Callable[[str], Tree], layout: str, reader: str
) -> None:
    """Each format reader, handed the plain path of a real file past
    `MAX_PATH`, returns what parsing the file's bytes returns."""
    tree = build(layout)
    _check_reader(tree, reader, tree.target if layout != "deep-root" else None)
