"""Long paths on Windows: the readers and writers M13-15 left on plain paths (M13-44).

M13-15 routed `guard`, `snapshot`, `layout` and `install` through
`wowlab_core.longpath`; the review of #247 listed the modules that still
handed the system a plain path, so on Windows, with `LongPathsEnabled` off,
each failed past the 240-character plain limit: `profiles`, `luadata`'s
sibling reads, `pathfacts`, `gaps`, `addoninstall`, `combatlog.find_logs` and
`follow`, and the `cli` sites of `tree`, `explain`, `sv dump`, `sv merge`,
`log tail` and `snap restore`. `luadata`'s graders are M13-44T's (in
`test_long_paths_windows.py`); this module is the rest.

Two parts:

- **Every platform** (`test_constructed_*_names_no_extended_length_path`):
  one test per command (`profile`, `sv merge`, `explain`, `addon`,
  `filemap gaps`, `log tail`), each run twice, once with the root (and any
  path argument) plain and once in the `\\\\?\\` spelling `longpath.plain`
  strips. Both print the same and no `\\\\?\\` reaches stdout or stderr
  (§6.9 as amended for M13-15: paths are plain everywhere they are shown).
  Off Windows the second spelling is the plain path itself, so these are
  controls there; on Windows they run in the ordinary passes.
- **Windows, long paths off** (`test_constructed_windows_*`): one test per
  call site, each on an install whose paths are past `MAX_PATH`, showing the
  call reaches the file. They skip off Windows, and skip on Windows where
  plain long paths work in this process (`LongPathsEnabled` = 1, the runner
  image's default); the `long-paths` step of `windows.yml` turns the setting
  off and runs this module with `WOWLAB_EXPECT_LONG_PATHS_OFF=1`, where a
  process in which plain long paths work is a failure, never a skip.
  `test_constructed_windows_refuses_the_plain_paths_of_the_deep_install`
  shows the operating system refusing the plain paths, which is why each
  call needs the extended spelling.

The installs are copies of the real capture `fixtures/macos/` (README rows)
under `tmp_path`; only their location is constructed, so every test id says
`constructed` (L8), as do the few files planted in them (a junction's
target, a SavedVariables pair for `sv merge`, an addon executable), each
labelled where it is made. The user data directory is redirected into
`tmp_path`, the process table holds no client, nothing asks wago.tools
anything, and nothing reads or writes a real install.
"""

from __future__ import annotations

import json
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
from typer.testing import CliRunner, Result

from wowlab_core import (
    addoninstall,
    cli,
    combatlog,
    install,
    longpath,
    luadata,
    pathfacts,
    profiles,
)
from wowlab_core.snapshot import SnapshotStore

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CAPTURE = FIXTURES / "macos"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder; tests may name it (L6)
INSTALL = "World of Warcraft"
ACCOUNT = "90000001#6"
ACCT = f"WTF/Account/{ACCOUNT}"
CONFIG = "WTF/Config.wtf"
LOGS = "Logs"
LOG = f"{LOGS}/WoWCombatLog-040126_021630.txt"
SYNDICATOR = f"{ACCT}/SavedVariables/Syndicator.lua"
CHAR_A = "Labchard-Labrealmg"  # the --into character (a real folder of the capture)
CHAR_B = "Labcharb-Labrealmf"  # the --from character
PG = "LabLongPathMerge.lua"  # constructed, planted in both characters
PG_A = f"{ACCT}/1/{CHAR_A}/SavedVariables/{PG}"
PG_B = f"{ACCT}/1/{CHAR_B}/SavedVariables/{PG}"
OURS = b'LabLongPathMergeDB = {\n["kept"] = 1,\n}\n'  # constructed: the target
THEIRS = b'LabLongPathMergeDB = {\n["kept"] = 1,\n["added"] = 2,\n}\n'  # constructed: the source
SOURCE = Path(__file__).resolve().parents[3] / "lab" / "addon" / "WowLab"

MAX_PATH = 260  # UTF-16 units, the terminator included
EXPECT_OFF = "WOWLAB_EXPECT_LONG_PATHS_OFF"
ON_WINDOWS = sys.platform == "win32"
EXTENDED = "\\\\?\\"  # the prefix `longpath.plain` strips; never in the Lab's output
REFUSED = {2, 3, 206}  # ERROR_FILE_NOT_FOUND, ERROR_PATH_NOT_FOUND, ERROR_FILENAME_EXCED_RANGE

runner = CliRunner()


# ─── paths the test itself uses ──────────────────────────────────────────────


def _x(path: Path) -> Path:
    """`path` as an extended-length path (`\\\\?\\C:\\...`), which Windows
    opens whatever `LongPathsEnabled` says; elsewhere `path` itself. The test
    builds and checks the trees through it; the Lab is handed plain paths,
    except by the tests that hand this form over on purpose."""
    text = str(path)
    if not ON_WINDOWS or text.startswith(EXTENDED):
        return path
    assert len(text) > 3 and text[1:3] == ":\\", f"a drive-letter absolute path: {text}"
    return Path(EXTENDED + text)


def _segments(base: Path, done: Callable[[Path], bool]) -> Path:
    """`base` extended by 50-character components until `done` holds."""
    path, n = base, 0
    while not done(path):
        path = path / f"games-library-{n:02d}-".ljust(50, "x")
        n += 1
    return path


@dataclass(frozen=True)
class Tree:
    """A copy of the real capture as an install, at a constructed location."""

    root: Path  # plain, as the Lab is handed it

    @property
    def flavor(self) -> Path:
        return self.root / FLAVOR

    def at(self, rel: str) -> Path:
        """A flavor-relative, `/`-separated path, plain."""
        return self.flavor.joinpath(*rel.split("/"))


def _copy_install(root: Path) -> Tree:
    _x(root).mkdir(parents=True)
    shutil.copy2(CAPTURE / ".build.info", _x(root / ".build.info"))
    shutil.copytree(CAPTURE / "forever", _x(root / FLAVOR))
    return Tree(root)


@pytest.fixture
def build(tmp_path: Path) -> Iterator[Callable[[bool], Tree]]:
    """`build(deep)`: an install, each in a folder of its own under
    `tmp_path`; with `deep`, its root is past `MAX_PATH`, so is every path in
    it. Removed afterwards through extended-length paths."""
    made: list[Path] = []

    def make(deep: bool) -> Tree:
        where = tmp_path / str(len(made))
        made.append(where)
        if not deep:
            return _copy_install(where / INSTALL)
        root = _segments(where, lambda p: len(str(p / INSTALL)) > MAX_PATH) / INSTALL
        return _copy_install(root)

    yield make
    for where in made:
        shutil.rmtree(_x(where), ignore_errors=True)


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


def _invoke(*args: str, input: str | None = None) -> Result:
    return runner.invoke(cli.app, list(args), input=input)


def _ok(result: Result) -> Result:
    assert result.exit_code == 0, (result.stdout, result.stderr, result.exception)
    return result


def _no_extended(result: Result) -> None:
    assert EXTENDED not in result.stdout, result.stdout
    assert EXTENDED not in result.stderr, result.stderr


def _plant(tree: Tree, rel: str, data: bytes) -> Path:
    """A constructed file at `rel` (flavor-relative), written by the test."""
    path = tree.at(rel)
    _x(path.parent).mkdir(parents=True, exist_ok=True)
    _x(path).write_bytes(data)
    return path


def _same_both_ways(argv: Callable[[Path, Path], list[str]], tree: Tree) -> Result:
    """`argv(root, flavor)` run with the plain spellings and with the `\\\\?\\`
    ones: the same exit code and output, and no `\\\\?\\` in either."""
    plain = _invoke(*argv(tree.root, tree.flavor))
    spelled = _invoke(*argv(_x(tree.root), _x(tree.flavor)))
    if ON_WINDOWS:
        assert str(_x(tree.root)).startswith(EXTENDED), "the root is handed over extended"
    _no_extended(plain)
    _no_extended(spelled)
    assert (spelled.exit_code, spelled.stdout, spelled.stderr) == (
        plain.exit_code,
        plain.stdout,
        plain.stderr,
    ), (spelled.exception, plain.exception)
    return plain


# ─── every platform: no extended-length spelling in any output ───────────────


@pytest.mark.parametrize("json_out", [False, True], ids=["text", "json"])
def test_constructed_explain_output_names_no_extended_length_path(
    build: Callable[[bool], Tree], json_out: bool
) -> None:
    """`explain` of an absolute path, the root and the path both handed over
    in `\\\\?\\` form, prints what it prints for the plain ones (the query
    included), and classifies the file."""
    tree = build(False)
    extra = ["--json"] if json_out else []
    plain = _same_both_ways(
        lambda root, flavor: [
            "explain",
            "--root",
            str(root),
            *extra,
            str(flavor / "WTF/Config.wtf"),
        ],
        tree,
    )
    assert plain.exit_code == 0, (plain.stdout, plain.stderr)
    if json_out:
        report = cli.ExplainReport.model_validate_json(plain.stdout)
        assert report.query == str(tree.at(CONFIG))
        assert isinstance(report.result, cli.layout.Classified)
        assert report.result.path == CONFIG


@pytest.mark.parametrize("json_out", [False, True], ids=["text", "json"])
def test_constructed_filemap_gaps_output_names_no_extended_length_path(
    build: Callable[[bool], Tree], json_out: bool
) -> None:
    tree = build(False)
    extra = ["--json"] if json_out else []
    plain = _same_both_ways(lambda root, _f: ["filemap", "gaps", "--root", str(root), *extra], tree)
    assert plain.exit_code == 0, (plain.stdout, plain.stderr)
    if json_out:
        assert json.loads(plain.stdout)["install_root"] == str(tree.root)


@pytest.mark.parametrize("json_out", [False, True], ids=["text", "json"])
def test_constructed_log_tail_output_names_no_extended_length_path(
    build: Callable[[bool], Tree], json_out: bool
) -> None:
    tree = build(False)
    extra = ["--json"] if json_out else []
    plain = _same_both_ways(lambda root, _f: ["log", "tail", "--root", str(root), *extra], tree)
    assert plain.exit_code == 0, (plain.stdout, plain.stderr)
    if json_out:
        assert cli.LogTailReport.model_validate_json(plain.stdout).file == LOG


@pytest.mark.parametrize("json_out", [False, True], ids=["text", "json"])
def test_constructed_sv_merge_output_names_no_extended_length_path(
    build: Callable[[bool], Tree], json_out: bool
) -> None:
    """`sv merge FILE` with FILE absolute, and the root, handed over in
    `\\\\?\\` form: the same plan (nothing confirmed, so nothing written) and
    no prefix. The pair of files is constructed."""
    tree = build(False)
    _plant(tree, PG_A, OURS)
    _plant(tree, PG_B, THEIRS)
    extra = ["--json"] if json_out else []
    plain = _same_both_ways(
        lambda root, flavor: [
            "sv",
            "merge",
            str(flavor.joinpath(*PG_A.split("/"))),
            "--from",
            CHAR_B,
            "--into",
            CHAR_A,
            "--root",
            str(root),
            *extra,
        ],
        tree,
    )
    assert plain.stdout.strip(), plain.stderr
    if json_out:
        assert cli.SvMergeReport.model_validate_json(plain.stdout).file == PG_A
    assert _x(tree.at(PG_A)).read_bytes() == OURS, "nothing was confirmed, nothing written"


@pytest.mark.parametrize("json_out", [False, True], ids=["text", "json"])
def test_constructed_profile_output_names_no_extended_length_path(
    build: Callable[[bool], Tree], json_out: bool
) -> None:
    """`profile save`, `list`, `show` and `apply --dry-run` with the root
    handed over in `\\\\?\\` form: no prefix anywhere, and the profile saved
    through it holds what the one saved through the plain root holds."""
    tree = build(False)
    extra = ["--json"] if json_out else []
    saved = {}
    for name, root in (("plain", tree.root), ("spelled", _x(tree.root))):
        result = _ok(
            _invoke("profile", "save", name, "--preset", "ui", "--root", str(root), "--json")
        )
        _no_extended(result)
        saved[name] = cli.ProfileReport.model_validate_json(result.stdout)
    assert saved["spelled"].entries == saved["plain"].entries
    assert saved["spelled"].subtrees == saved["plain"].subtrees
    for argv in (["profile", "list"], ["profile", "show", "spelled"]):
        _no_extended(_ok(_invoke(*argv, *extra)))
    plain = _same_both_ways(
        lambda root, _f: ["profile", "apply", "plain", "--dry-run", "--root", str(root), *extra],
        tree,
    )
    assert plain.exit_code == 0, (plain.stdout, plain.stderr)
    if json_out:
        report = cli.ProfileApplyReport.model_validate_json(plain.stdout)
        assert (report.plan, report.flavor_path) == ([], str(tree.flavor))


@pytest.mark.parametrize("json_out", [False, True], ids=["text", "json"])
def test_constructed_addon_output_names_no_extended_length_path(
    build: Callable[[bool], Tree], json_out: bool
) -> None:
    """`addon install lab` and `addon remove lab` (dry runs, then real ones)
    with the root handed over in `\\\\?\\` form print what they print with the
    plain root, and name no prefix (the journal record id aside, which
    differs per write)."""
    tree = build(False)
    extra = ["--json"] if json_out else []
    for verb in ("install", "remove"):
        _same_both_ways(
            lambda root, _f, verb=verb: [
                "addon",
                verb,
                "lab",
                "--dry-run",
                "--root",
                str(root),
                *extra,
            ],
            tree,
        )
        done = _ok(_invoke("addon", verb, "lab", "--yes", "--root", str(_x(tree.root)), *extra))
        _no_extended(done)
        if json_out:
            report = json.loads(done.stdout)
            assert report["flavor_path"] == str(tree.flavor)
            assert report["applied"] is True
    assert addoninstall._installed(tree.flavor)[0] == [], "installed, then removed"


# ─── Windows, long paths off ─────────────────────────────────────────────────


@pytest.fixture(scope="session")
def plain_long_paths(tmp_path_factory: pytest.TempPathFactory) -> str | None:
    """None when this process cannot open a plain path past `MAX_PATH` (the
    condition the Windows tests need), else a sentence saying it could."""
    base = tmp_path_factory.mktemp("probe-m13-44")
    deep = _segments(base, lambda p: len(str(p)) > MAX_PATH)
    _x(deep).mkdir(parents=True)
    try:
        deep.stat()
    except OSError:
        return None
    finally:
        shutil.rmtree(_x(base), ignore_errors=True)
    return f"stat of the plain {len(str(deep))}-character path {deep} succeeded"


@pytest.fixture
def long_paths_off(request: pytest.FixtureRequest) -> None:
    """Windows only, and plain long paths refused in this process."""
    if not ON_WINDOWS:
        pytest.skip("MAX_PATH and LongPathsEnabled are Windows facilities")
    found = request.getfixturevalue("plain_long_paths")
    if found is None:
        return
    if os.environ.get(EXPECT_OFF) == "1":
        pytest.fail(
            f"{EXPECT_OFF}=1 but plain long paths work in this process "
            f"({found}); the step that sets LongPathsEnabled=0 did not take"
        )
    pytest.skip("plain paths past MAX_PATH work in this process (LongPathsEnabled is on)")


WINDOWS_LONG = pytest.mark.usefixtures("long_paths_off")


@pytest.fixture
def deep(build: Callable[[bool], Tree], long_paths_off: None) -> Tree:
    return build(True)


@pytest.fixture
def short(build: Callable[[bool], Tree]) -> Tree:
    return build(False)


def _junction(link: Path, target: Path) -> None:
    """A junction at `link` (created through its extended spelling) to the
    short folder `target`."""
    import _winapi  # Windows only

    _winapi.CreateJunction(str(target), str(_x(link)))  # type: ignore[attr-defined]


def _json(argv: list[str]) -> dict[str, Any]:
    result = _ok(_invoke(*argv, "--json"))
    _no_extended(result)
    value = json.loads(result.stdout)
    assert isinstance(value, dict)
    return value


def _without(report: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {k: v for k, v in report.items() if k not in keys}


@WINDOWS_LONG
def test_constructed_windows_refuses_the_plain_paths_of_the_deep_install(deep: Tree) -> None:
    """The precondition: with the setting off, Windows refuses the plain
    path of every file the tests below read, and opens its extended one."""
    for rel in (CONFIG, LOG, SYNDICATOR):
        with pytest.raises(OSError) as refused:
            deep.at(rel).stat()
        assert getattr(refused.value, "winerror", None) in REFUSED, refused.value
        assert _x(deep.at(rel)).stat().st_size == (CAPTURE / "forever" / rel).stat().st_size


# luadata: the containment check past MAX_PATH (the security point of M13-44)


@WINDOWS_LONG
def test_constructed_windows_read_sibling_refuses_a_file_through_a_junction_past_max_path(
    deep: Tree, tmp_path: Path
) -> None:
    """`_read_sibling`'s real-path check holds past `MAX_PATH`: a file reached
    through a junction below `WTF/Account` that leads out of it is not read,
    though the open alone would reach it; a real file beside it is read. Had
    only the open been extended, the check would resolve the plain path only
    as far up as Windows opens it, miss the junction, and read the file."""
    account = deep.at("WTF/Account")
    root = luadata._sibling_paths(deep.at(f"{ACCT}/SavedVariables/LabNew.lua"), ())[0][1]
    assert root == longpath.realpath(account)
    assert EXTENDED not in root
    assert os.path.normcase(root) == os.path.normcase(str(account))
    assert luadata._read_sibling(deep.at(SYNDICATOR), root) is not None

    outside = tmp_path / "outside-account"
    outside.mkdir()
    (outside / "Lab.lua").write_bytes(b"LabOutsideDB = {\n}\n")  # constructed
    link = deep.at(f"{ACCT}/LabLinked")
    _junction(link, outside)
    through = link / "Lab.lua"
    assert _x(through).read_bytes() == b"LabOutsideDB = {\n}\n", "the open alone reaches it"
    assert luadata._read_sibling(through, root) is None


@WINDOWS_LONG
def test_constructed_windows_list_siblings_names_each_file_plainly_past_max_path(
    deep: Tree,
) -> None:
    """The folder listing reaches `WTF/Account` past `MAX_PATH`, and every
    path it returns is spelled from the plain `account`, never `\\\\?\\`."""
    found = luadata._list_siblings(deep.at("WTF/Account"))
    assert deep.at(SYNDICATOR) in found
    assert all(EXTENDED not in str(path) for path in found)


# profiles


@WINDOWS_LONG
def test_constructed_windows_profile_speller_lists_a_folder_past_max_path(deep: Tree) -> None:
    """`_Speller` lists each folder past `MAX_PATH`, so a part typed in
    another case is spelled as the disk spells it."""
    assert profiles._Speller(deep.flavor)("wtf/config.wtf") == CONFIG


@WINDOWS_LONG
def test_constructed_windows_profile_disk_state_past_max_path(deep: Tree, store: Path) -> None:
    """`disk_state` (the `lstat` walk, the open and the `missing` walk)
    reaches a file past `MAX_PATH`: `same`, then `differs`, then `missing`."""
    manifest = SnapshotStore(store).create(
        deep.root, [f"{FLAVOR}/WTF"], flavor_folder=FLAVOR, client_running=False
    )
    entry = manifest.entry(f"{FLAVOR}/{CONFIG}")
    assert entry is not None
    assert profiles.disk_state(deep.root, FLAVOR, entry) == "same"
    _x(deep.at(CONFIG)).write_bytes(b'SET labEdited "1"\n')  # constructed edit
    assert profiles.disk_state(deep.root, FLAVOR, entry) == "differs"
    _x(deep.at(CONFIG)).unlink()
    assert profiles.disk_state(deep.root, FLAVOR, entry) == "missing"


@WINDOWS_LONG
def test_constructed_windows_profile_apply_past_max_path(deep: Tree) -> None:
    """`profile save` then `profile apply --dry-run` on an install past
    `MAX_PATH`: nothing to do while the files are as saved; an edit is planned."""
    _ok(_invoke("profile", "save", "deep", "--preset", "ui", "--root", str(deep.root)))
    report = _json(["profile", "apply", "deep", "--dry-run", "--root", str(deep.root)])
    assert report["plan"] == [] and report["flavor_path"] == str(deep.flavor)
    _x(deep.at(CONFIG)).write_bytes(b'SET labEdited "1"\n')  # constructed edit
    report = _json(["profile", "apply", "deep", "--dry-run", "--root", str(deep.root)])
    assert [item["path"] for item in report["plan"]] == [CONFIG]


# pathfacts and explain


@WINDOWS_LONG
def test_constructed_windows_pathfacts_past_max_path(deep: Tree) -> None:
    """`disk_spelling`, `path_stat`, `_Volume.same` and `_same_root` reach
    paths past `MAX_PATH`."""
    typed = (FLAVOR, "wtf", "config.wtf")
    assert pathfacts.disk_spelling(deep.root, typed) == (FLAVOR, "WTF", "Config.wtf")
    facts = pathfacts.path_stat(deep.at(CONFIG))
    assert facts is not None and facts.kind == "file"
    assert facts.size == (CAPTURE / "forever" / CONFIG).stat().st_size
    volume = pathfacts._Volume(deep.root)
    assert volume.same(f"{FLAVOR}/{CONFIG}", f"{FLAVOR}/wtf/config.wtf") is True
    assert pathfacts._same_root(str(deep.root / "x" / ".."), deep.root)


@WINDOWS_LONG
def test_constructed_windows_explain_past_max_path(
    deep: Tree, short: Tree, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`explain` on an install past `MAX_PATH` says of a file what it says at
    a short root (its component walk, `stat` and spelling reach it), with
    the path typed relative to the flavor folder and relative to the current
    folder (looked up there by its extended spelling)."""

    def report(tree: Tree, path: str) -> dict[str, Any]:
        found = _json(["explain", "--root", str(tree.root), path])
        assert found["stat"] is not None, found
        return _without(found, "query", "snapshot", "hints", "views")

    expected = report(short, "wtf/config.wtf")
    assert report(deep, "wtf/config.wtf") == expected
    monkeypatch.chdir(tmp_path)
    relative = str(deep.at(CONFIG).relative_to(tmp_path))
    assert len(str(tmp_path)) + 50 < MAX_PATH, "the current folder is short"
    found = report(deep, relative)
    assert found["result"]["path"] == CONFIG and found["result"]["status"] == "classified"


@WINDOWS_LONG
def test_constructed_windows_tree_past_max_path(deep: Tree, short: Tree) -> None:
    """`tree` and `tree --install` (the root's listing, the flavor folder's
    `lstat` and listing) on an install past `MAX_PATH` list what they list
    at a short root, with no error."""
    for extra in ([], ["--install"]):
        found = _json(["tree", "--root", str(deep.root), *extra])
        assert found["errors"] == []
        assert found["install_root"] == str(deep.root)
        expected = _json(["tree", "--root", str(short.root), *extra])
        assert _without(found, "install_root") == _without(expected, "install_root")


# gaps


@WINDOWS_LONG
def test_constructed_windows_filemap_gaps_past_max_path(deep: Tree, short: Tree) -> None:
    """`filemap gaps` walks an install past `MAX_PATH` as it walks one at a
    short root: no error, the same groups."""
    found = _json(["filemap", "gaps", "--root", str(deep.root)])
    assert found["errors"] == []
    expected = _json(["filemap", "gaps", "--root", str(short.root)])
    assert _without(found, "install_root") == _without(expected, "install_root")


# combatlog and log tail


@WINDOWS_LONG
def test_constructed_windows_find_logs_and_follow_past_max_path(deep: Tree) -> None:
    """`find_logs` lists a `Logs` folder past `MAX_PATH` and names the log
    plainly; `follow` takes the folder (its type checks reach it) and starts
    on that log."""
    logs = deep.at(LOGS)
    assert combatlog.find_logs(logs) == [deep.at(LOG)]
    assert combatlog.newest_log(logs) == deep.at(LOG)
    following = combatlog.follow(logs, sleep=lambda _s: None)
    try:
        first = next(following)
    finally:
        following.close()
    assert isinstance(first, combatlog.Following)
    assert (first.path, first.reason) == (deep.at(LOG), "start")


@WINDOWS_LONG
def test_constructed_windows_log_tail_past_max_path(deep: Tree, short: Tree) -> None:
    """`log tail` (the `Logs` lookup, the size `stat`, the read) on an install
    past `MAX_PATH` prints what it prints at a short root."""
    found = _json(["log", "tail", "--root", str(deep.root)])
    assert found["file"] == LOG and found["entries"]
    assert found == _json(["log", "tail", "--root", str(short.root)])


# sv merge


@WINDOWS_LONG
def test_constructed_windows_sv_merge_file_named_from_the_current_folder_past_max_path(
    deep: Tree, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`sv merge FILE` takes FILE relative to the current folder when it is
    there, looked up by its extended spelling past `MAX_PATH` (the pair is
    constructed); nothing is confirmed, so nothing is written."""
    _plant(deep, PG_A, OURS)
    _plant(deep, PG_B, THEIRS)
    monkeypatch.chdir(tmp_path)
    relative = str(deep.at(PG_A).relative_to(tmp_path))
    result = _invoke(
        "sv",
        "merge",
        relative,
        "--from",
        CHAR_B,
        "--into",
        CHAR_A,
        "--root",
        str(deep.root),
        "--json",
    )
    _no_extended(result)
    assert result.stdout.strip(), (result.stderr, result.exception)
    report = cli.SvMergeReport.model_validate_json(result.stdout)
    assert report.file == PG_A
    assert [a.path for a in report.absent] == ['LabLongPathMergeDB["added"]']
    assert _x(deep.at(PG_A)).read_bytes() == OURS


@WINDOWS_LONG
def test_constructed_windows_cli_same_root_past_max_path(deep: Tree) -> None:
    """`sv merge`'s snapshot-root comparison resolves a root past `MAX_PATH`."""
    assert cli._same_root(str(deep.root / "x" / ".."), deep.root)


# snap restore's checks


@WINDOWS_LONG
def test_constructed_windows_restore_checks_past_max_path(deep: Tree, store: Path) -> None:
    """`_plainly_on_disk`, `_plainly_changed` and `_unchanged` (what a whole
    `snap restore` skips or names) reach files past `MAX_PATH`. The
    executable is constructed: a `.dll` in a real addon folder."""
    dll = "Interface/AddOns/DBM-Brawlers/LabLong.dll"
    _plant(deep, dll, b"MZ constructed\n")
    manifest = SnapshotStore(store).create(
        deep.root,
        [f"{FLAVOR}/WTF", f"{FLAVOR}/Interface"],
        flavor_folder=FLAVOR,
        client_running=False,
    )
    config = manifest.entry(f"{FLAVOR}/{CONFIG}")
    executable = manifest.entry(f"{FLAVOR}/{dll}")
    assert config is not None and executable is not None
    assert cli._plainly_on_disk(deep.flavor, CONFIG) == "file"
    assert cli._plainly_on_disk(deep.flavor, "WTF/LabNone.wtf") == "missing"
    assert cli._unchanged(deep.root, FLAVOR, executable) is True
    _x(deep.at(CONFIG)).write_bytes(b'SET labEdited "1"\n')  # constructed edit
    assert cli._plainly_changed(deep.root, FLAVOR, config, CONFIG) is True


# addoninstall


@WINDOWS_LONG
def test_constructed_windows_addon_sources_past_max_path(deep: Tree) -> None:
    """`addon_files` lists and reads the lab-addon's sources from a folder
    past `MAX_PATH` (a checkout deep in a user's folders) as from the
    repository's own."""
    copy = deep.root.parent / "WowLab"
    shutil.copytree(SOURCE, _x(copy))
    expected = addoninstall.addon_files(16001, source=SOURCE)
    assert addoninstall.addon_files(16001, source=copy) == expected


@WINDOWS_LONG
def test_constructed_windows_addon_install_and_remove_past_max_path(deep: Tree) -> None:
    """`addon install lab` and `addon remove lab` on an install past
    `MAX_PATH`: the folder walk and the reads of what is installed reach it,
    so a second install finds every file unchanged, and remove empties it."""
    first = _json(["addon", "install", "lab", "--yes", "--root", str(deep.root)])
    assert first["applied"] is True and first["plan"]
    again = _json(["addon", "install", "lab", "--dry-run", "--root", str(deep.root)])
    assert again["plan"] == [] and sorted(again["unchanged"]) == sorted(first["files"])
    files, left, there = addoninstall._installed(deep.flavor)
    assert there and left == [] and files == sorted(first["files"])
    removed = _json(["addon", "remove", "lab", "--yes", "--root", str(deep.root)])
    assert removed["applied"] is True
    assert sorted(item["path"] for item in removed["plan"]) == sorted(first["files"])
