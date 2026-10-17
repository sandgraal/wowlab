"""Graders: long paths on Windows with `LongPathsEnabled` off (M13-15T).

Found by M13-02. With `LongPathsEnabled` = 0, the default on many player
PCs, Windows refuses a plain path of `MAX_PATH` (260 UTF-16 units, the
terminator included) or more; only an extended-length path (`\\\\?\\C:\\...`,
or `\\\\?\\UNC\\server\\share\\...` for a network path) reaches such a file.
The Lab passes plain `pathlib` paths, so `snapshot`, `guard` and the readers
cannot reach a file deep in an install. M13-15 makes them reach it. Every
grader here carries one marker line, `@pytest.mark.xfail(strict=True, ...,
reason="M13-15 not implemented")`, which M13-15 deletes and does not
otherwise change.

What is graded, each on the same operations a player's loop runs:

- `SnapshotStore.create` captures every file of the subtrees, the deep one
  included, with its bytes. On `main` it can drop the deep file without a
  word (a partial snapshot), so the graders compare the whole entry set
  with what is on disk;
- `guard`: a write of the deep file (with its pre-write snapshot and journal
  record), its `undo()`, and a `restore` of it from a snapshot;
- the readers: `install.discover` and `wowlab doctor` on an install whose
  root is past `MAX_PATH`, and the format parsers (`luadata`, `wtfconfig`,
  `toc`, `combatlog`) on a real file past it;
- the root as callers may spell it: already extended-length (`\\\\?\\C:\\...`)
  and relative to the working folder, for `SnapshotStore.create` and
  `guard.transaction` (a prefix added to an unnormalised root opens another
  path, or none). Through an extended-length flavor the gate never writes
  what `undo()` cannot reverse (§6.10): it refuses on enter, or it journals
  the plain root so `undo()` and restore work.

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
  gate must still write and restore it (a labelled boundary case of the same
  fix; conductor ruling, M13-15T review);
- `unc-deep-dir` and `unc-short`: `deep-dir` and `short` with the root named
  by a network path, `\\\\localhost\\C$\\...` (the drive's administrative
  share, which the runner's administrator account opens). Its extended form
  is `\\\\?\\UNC\\...`, not `\\\\?\\` in front of the plain path. Only
  `snapshot` and the readers are graded there: `guard` restores and undoes
  only from local roots (`guard._local_absolute`);
- `short`: every path well under the limit.

How the reason is pinned. The trees are built and checked by the test
through extended-length paths, and the Lab is handed plain paths, as a
caller would. `test_constructed_windows_refuses_the_plain_path_of_each_long_layout`
shows the operating system refusing each plain path, and the controls run
the same operations where the path is short and must pass on `main`.

An xfail marker does not tell a failure in the test from one in its setup:
an exception while a grader's tree is built is reported as an expected
failure too. The backstop is the unmarked tests: every layout a grader uses
is also built, the same way, by an unmarked test in the same run, so a tree
that cannot be built (or a share that does not exist, or long paths that
are still on) fails the run there. `test_every_layout_a_grader_uses_is_built_by_an_unmarked_test`
holds the module to that. Where a grader fails with one exception type on
`main` the marker also says `raises=`, so any other exception fails it
outright.

Where this runs: Windows only (`_long_paths_off` skips elsewhere), except the
tests in `ANYWHERE`, which only read this module and run on every platform.
Every other test also needs plain
long paths to be refused in this process, which a fixture probes; where they
work (`LongPathsEnabled` = 1, the runner image's default) the module skips,
since a grader would pass and a strict marker would fail. The `lab
(windows)` job turns the setting off in a step and runs this module alone
with `WOWLAB_EXPECT_LONG_PATHS_OFF=1`; with that set, a process in which
plain long paths work, or a missing administrative share, is a failure,
never a skip.

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
LONG_ANYWHERE = (*LONG, "unc-deep-dir")

runner = CliRunner()


# ─── paths the test itself uses ──────────────────────────────────────────────


def _x(path: Path) -> Path:
    """`path` as an extended-length path, which Windows opens whatever
    `LongPathsEnabled` says: `\\\\?\\C:\\...` for a drive path,
    `\\\\?\\UNC\\server\\share\\...` for a network one. Only the test uses
    these, to build and check the trees; the Lab is handed plain paths
    (except by the tests that pass this form on purpose)."""
    text = str(path)
    if sys.platform != "win32" or text.startswith("\\\\?\\"):
        return path
    if text.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + text[2:])
    assert len(text) > 3 and text[1:3] == ":\\", f"a drive-letter absolute path: {text}"
    return Path("\\\\?\\" + text)


def _unc(path: Path) -> Path:
    """`C:\\a\\b` named through the drive's administrative share,
    `\\\\localhost\\C$\\a\\b`: the same folder, by a network path."""
    text = str(path)
    if sys.platform != "win32":
        return path
    assert len(text) > 3 and text[1:3] == ":\\", f"a drive-letter absolute path: {text}"
    return Path(f"\\\\localhost\\{text[0]}$\\{text[3:]}")


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
    """Plain, as the Lab is handed it (a network path for `unc-*`)."""
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


def _require_share(root: Path) -> None:
    """The administrative share must open; with `EXPECT_OFF` set its absence
    fails the test, so the network layouts can never pass by skipping."""
    try:
        _x(root).stat()
    except OSError as exc:
        why = f"the administrative share does not open: {root} ({exc})"
        if os.environ.get(EXPECT_OFF) == "1":
            pytest.fail(why)
        pytest.skip(why)


def _build(where: Path, layout: str) -> Tree:
    """The install for `layout` under `where`, built and checked through
    extended-length paths. A `unc-*` layout is built on the drive and named
    by its network path; every length is measured on the name the Lab gets."""
    unc = layout.startswith("unc-")
    shape = layout.removeprefix("unc-")
    view: Callable[[Path], Path] = _unc if unc else (lambda p: p)

    def length(path: Path) -> int:
        return len(str(view(path)))

    if shape == "deep-root":
        assert not unc
        root = _segments(where, "games-library", lambda p: length(p / INSTALL) > MAX_PATH)
        root = root / INSTALL
        _copy_install(root)
        target, source = SYNDICATOR, CAPTURE / "forever" / SYNDICATOR
        assert length(root / ".build.info") > MAX_PATH
    else:
        root = where / INSTALL
        _copy_install(root)
        source = CAPTURE / "forever" / TOC
        brawlers = root / FLAVOR / BRAWLERS
        if shape == "short":
            target, source = SYNDICATOR, CAPTURE / "forever" / SYNDICATOR
        elif shape == "deep-dir":
            deep = _segments(brawlers / "Libs", "LibDeep", lambda p: length(p) > MAX_PATH)
            target = f"{deep.relative_to(root / FLAVOR).as_posix()}/DBM-Brawlers.toc"
        elif shape == "long-name":
            size = MAX_PATH + 20 - length(brawlers) - 1
            name = "DBM-Brawlers-".ljust(size - len(".toc"), "n") + ".toc"
            assert len(name) <= 255, "one component stays a legal file name"
            target = f"{BRAWLERS}/{name}"
            assert length(brawlers / name) == MAX_PATH + 20
            assert length(brawlers) + 1 + ROOM < MAX_PATH
        elif shape == "at-limit":
            libs = brawlers / "Libs"
            pad = LONGEST_PLAIN - length(libs) - 1 - 1 - len("DBM-Brawlers.toc")
            assert 1 <= pad <= 255, f"the runner's temp folder leaves room for a pad: {pad}"
            target = f"{BRAWLERS}/Libs/{'p' * pad}/DBM-Brawlers.toc"
        else:
            raise AssertionError(f"no layout {layout!r}")
        if shape != "short":
            placed = (root / FLAVOR).joinpath(*target.split("/"))
            _x(placed.parent).mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, _x(placed))
    tree = Tree(layout, view(root), target, source)
    if unc:
        _require_share(tree.root)
    if shape == "deep-dir":
        assert len(str(tree.target_path.parent)) > MAX_PATH
    if shape == "at-limit":
        assert len(str(tree.target_path)) == LONGEST_PLAIN
    assert _read(tree.target_path) == tree.source.read_bytes()
    if shape in ("short", "at-limit"):
        for path in _files(tree.root).values():
            assert len(str(path)) <= LONGEST_PLAIN, f"{layout}: {path} is plain-openable"
            # Room for the gate's own names; the network layouts are only
            # snapshotted and read, which create nothing in the install.
            if path != tree.target_path and not unc:
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


ANYWHERE = frozenset({"test_every_layout_a_grader_uses_is_built_by_an_unmarked_test"})
"""Tests that only read this module (no file system, no Lab call): they run
on every platform, so ordinary CI catches drift."""


@pytest.fixture(autouse=True)
def _long_paths_off(request: pytest.FixtureRequest) -> None:
    """Windows only, and plain long paths refused in this process."""
    if request.node.originalname in ANYWHERE:
        return
    if sys.platform != "win32":
        pytest.skip("MAX_PATH and LongPathsEnabled are Windows facilities")
    plain_long_paths = request.getfixturevalue("plain_long_paths")
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


def _snapshot(tree: Tree, store: Path, root: Path | None = None) -> Manifest:
    return SnapshotStore(store).create(
        tree.root if root is None else root,
        SUBTREES,
        label="long paths",
        flavor_folder=FLAVOR,
        client_running=False,
    )


def _check_snapshot(tree: Tree, store: Path, root: Path | None = None) -> Manifest:
    """Every file of the subtrees is captured with its bytes, the layout's
    file included (nothing dropped, nothing extra); the store verifies.
    `root` is the spelling of the root handed to `create` (default: plain)."""
    manifest = _snapshot(tree, store, root)
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
    return manifest


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


def _write_once(tree: Tree, store: Path, flavor: Path) -> tuple[bytes, bytes, set[str]]:
    """`tx.write` of the layout's file through `flavor` (a spelling of the
    flavor folder): the bytes land, the pre-write snapshot holds the old ones,
    one committed record names the path, nothing else appears."""
    original = tree.source.read_bytes()
    new = original + EDIT
    before = set(_files(tree.root))

    with guard.transaction(_Flavor(flavor), label="write", store=store) as tx:
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
    return original, new, before


def _check_write_and_undo(tree: Tree, store: Path) -> None:
    """`tx.write` as above; `undo()` puts the old bytes back."""
    original, _new, before = _write_once(tree, store, tree.flavor)
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


# ─── the backstop: every graded layout is built by an unmarked test ──────────


def _layouts(test: object) -> set[str]:
    """The layouts a test function is parametrized over (`tree` or `layout`)."""
    found: set[str] = set()
    for mark in getattr(test, "pytestmark", []):
        if mark.name != "parametrize":
            continue
        names, values = mark.args[0], mark.args[1]
        names = [n.strip() for n in names.split(",")] if isinstance(names, str) else list(names)
        for key in ("tree", "layout"):
            if key in names:
                at = names.index(key)
                for value in values:
                    found.add(value if len(names) == 1 else value[at])
    return found


def _is_grader(test: object) -> bool:
    return any(mark.name == "xfail" for mark in getattr(test, "pytestmark", []))


def test_every_layout_a_grader_uses_is_built_by_an_unmarked_test() -> None:
    """An xfail marker also swallows a failure in setup, so a grader whose
    tree cannot be built would pass as expected-to-fail. Every layout a grader
    uses is therefore built by an unmarked test too, which fails the run."""
    tests = [f for name, f in sorted(globals().items()) if name.startswith("test_")]
    graders = [f for f in tests if _is_grader(f)]
    assert graders, "the module has graders"
    for grader in graders:
        assert _layouts(grader), f"{grader.__name__} names its layouts in a parametrize"
    graded = set().union(*(_layouts(f) for f in graders))
    built = set().union(*(_layouts(f) for f in tests if not _is_grader(f)))
    assert graded <= built, f"graded but built by no unmarked test: {sorted(graded - built)}"


# ─── the precondition: why the graders fail ─────────────────────────────────


@pytest.mark.parametrize("tree", LONG_ANYWHERE, indirect=True)
def test_constructed_windows_refuses_the_plain_path_of_each_long_layout(tree: Tree) -> None:
    """With the setting off, the plain path of each long layout's file is
    refused by the operating system, and its extended-length path is not:
    the reason the graders fail before M13-15. On `main` the refusal is a
    `FileNotFoundError` (WinError 2 or 3), which is how a file removed during
    a walk looks too."""
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


@pytest.mark.parametrize("tree", ["short", "at-limit", "unc-short"], indirect=True)
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


@pytest.mark.parametrize("tree", ["deep-dir"], indirect=True)
def test_constructed_guard_refuses_a_relative_flavor_and_writes_nothing(
    tree: Tree, store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A flavor named relative to the working folder (a short one) is refused
    on enter, as every relative flavor is: nothing journaled or written. A
    prefix must never be glued to such a path."""
    monkeypatch.chdir(tree.root.parent)
    before = {rel: _read(path) for rel, path in _files(tree.root).items()}
    with (
        pytest.raises(guard.GuardError, match="absolute"),
        guard.transaction(_Flavor(Path(INSTALL) / FLAVOR), label="relative", store=store) as tx,
    ):
        tx.write(tree.target, b"never")
    assert guard.history(store=store) == ()
    assert {rel: _read(path) for rel, path in _files(tree.root).items()} == before


@pytest.mark.parametrize("tree", ["deep-dir"], indirect=True)
def test_constructed_snapshot_of_an_extended_length_root_captures_everything(
    tree: Tree, store: Path
) -> None:
    """A root handed over already in `\\\\?\\C:\\...` form reaches every
    file, the deep one included: a prefix is never added twice."""
    _check_snapshot(tree, store, _x(tree.root))


@pytest.mark.parametrize("tree", ["deep-dir"], indirect=True)
def test_constructed_guard_never_writes_what_it_cannot_undo_through_an_extended_length_flavor(
    tree: Tree, store: Path
) -> None:
    """A flavor handed over in `\\\\?\\C:\\...` form. §6.10: a change `undo()`
    could not reverse must not be written. Either outcome is consistent
    (conductor ruling, M13-15T review round 2):

    (a) the transaction refuses on enter, with nothing written and nothing
        journaled; or
    (b) the write lands, the journal and the pre-write manifest record the
        plain local root, `undo()` puts the old bytes back, and a restore
        from that snapshot through the plain flavor works too.

    On `main` the write lands and `undo()` refuses the `\\\\?\\` root it
    journaled."""
    original = tree.source.read_bytes()
    new = original + EDIT
    before = {rel: _read(path) for rel, path in _files(tree.root).items()}
    entered = False
    try:
        with guard.transaction(_Flavor(_x(tree.flavor)), label="extended", store=store) as tx:
            entered = True
            tx.write(tree.target, new)
    except guard.GuardError:
        assert not entered, "a refusal comes on enter, before the body runs"
        assert guard.history(store=store) == (), "(a): nothing journaled"
        assert {rel: _read(path) for rel, path in _files(tree.root).items()} == before
        return

    assert _read(tree.target_path) == new
    (record,) = guard.history(store=store)
    assert (record.state, record.rolled_back) == ("committed", False)
    assert not record.flavor_path.startswith(("\\\\", "//")), record.flavor_path
    assert Path(record.flavor_path) == tree.flavor, "(b): the journal names the plain root"
    manifest = SnapshotStore(store).show(record.snapshot_id)
    assert not manifest.install_root.startswith(("\\\\", "//")), manifest.install_root
    assert Path(manifest.install_root) == tree.root, "(b): the manifest names the plain root"

    guard.undo(store=store)
    assert {rel: _read(path) for rel, path in _files(tree.root).items()} == before

    _write(tree.target_path, new)
    with guard.transaction(_Flavor(tree.flavor), label="restore", store=store) as tx:
        tx.restore(record.snapshot_id, [tree.target])
    assert _read(tree.target_path) == original


# ─── the graders ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("tree", LONG_ANYWHERE, indirect=True)
def test_constructed_snapshot_captures_a_file_past_max_path(tree: Tree, store: Path) -> None:
    """`snap create` (`SnapshotStore.create`) captures the file past
    `MAX_PATH` and every other file of the subtrees, byte for byte, on a
    drive path and on a network path. A file it cannot reach is never left
    out without an error."""
    _check_snapshot(tree, store)


@pytest.mark.parametrize("tree", ["deep-dir"], indirect=True)
def test_constructed_snapshot_of_a_relative_root_captures_the_file_past_max_path(
    tree: Tree, store: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A root named relative to a short working folder: made absolute first,
    then every file captured, the deep one included; the manifest names the
    plain absolute root (a manifest with a `\\\\?\\` root is not restorable)."""
    monkeypatch.chdir(tree.root.parent)
    manifest = _check_snapshot(tree, store, Path(INSTALL))
    assert manifest.install_root == str(tree.root)


@pytest.mark.parametrize("tree", [*LONG, "at-limit"], indirect=True)
def test_constructed_guard_restores_the_deep_file_from_a_snapshot(tree: Tree, store: Path) -> None:
    """`snap restore` (`tx.restore`) puts the deep file's snapshot bytes back,
    journaled; at the limit too, where the file is reachable but a name
    beside it is not."""
    _check_restore(tree, store)


@pytest.mark.parametrize("tree", [*LONG, "at-limit"], indirect=True)
def test_constructed_guard_writes_and_undoes_the_deep_file(tree: Tree, store: Path) -> None:
    """A gate write of the deep file: snapshot first, journaled, atomically
    replaced, no temp file left; `undo()` puts the old bytes back."""
    _check_write_and_undo(tree, store)


@pytest.mark.parametrize(
    ("layout", "source"), [("deep-root", "argument"), ("deep-root", "environment")]
)
def test_constructed_discovery_reads_an_install_rooted_past_max_path(
    build: Callable[[str], Tree], layout: str, source: str
) -> None:
    """`install.discover` finds and reads the install whose `.build.info` is
    past `MAX_PATH`, as it reads the same install at a short root, and hands
    back the caller's plain root."""
    short = install.read_install(build("short").root)
    _check_discovery(build(layout), short, source)


@pytest.mark.parametrize("layout", ["deep-root"])
def test_constructed_doctor_reports_an_install_rooted_past_max_path(
    build: Callable[[str], Tree], monkeypatch: pytest.MonkeyPatch, layout: str
) -> None:
    """`wowlab doctor --offline` reports the deep-rooted install exactly as
    it reports the same install at a short root, with no problems."""
    short = _doctor(build("short"), monkeypatch)
    _check_doctor(build(layout), short, monkeypatch)


@pytest.mark.parametrize(
    ("layout", "reader"),
    [
        *[("deep-root", reader) for reader in sorted(READERS)],
        ("deep-dir", "toc"),
        ("long-name", "toc"),
        ("unc-deep-dir", "toc"),
    ],
)
def test_constructed_reader_parses_a_real_file_past_max_path(
    build: Callable[[str], Tree], layout: str, reader: str
) -> None:
    """Each format reader, handed the plain path of a real file past
    `MAX_PATH`, returns what parsing the file's bytes returns."""
    tree = build(layout)
    _check_reader(tree, reader, tree.target if layout != "deep-root" else None)
