"""The `wowlab` CLI through Typer's runner (M10-14, docs/LAB_PLAN.md §6.11).

The install is the captured tree (`fixtures/macos/`) copied into `tmp_path`
with its flavor folder named as its provenance row says (`_classic_beta_`),
because discovery only reports `_*_` folders. Nothing here reads or writes a
real install: `WOWLAB_WOW_ROOT` points at the copy, the platform defaults
are replaced where a test needs discovery to search, the user data
directory (the snapshot store, the journal, the locks, the game-data cache)
is redirected into `tmp_path`, `psutil.process_iter` is replaced by a fake
table, and wago.tools is replayed from `fixtures/wago/` (ADR-0012).

Inputs labelled `constructed` are boundary or hostile cases (L8): a second
flavor folder, a renamed product, a damaged manifest, a file that is not
Lua data, a lowered parse budget.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import shutil
import sys
import zlib
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
import platformdirs
import psutil
import pytest
from pydantic import BaseModel
from typer.testing import CliRunner

from wowlab_core import cli, guard, install, luadata, snapshot
from wowlab_core.gamedata import GameData, WagoSource
from wowlab_core.install import Install
from wowlab_core.snapshot import GcReport, Manifest, SnapshotStore, VerifyReport

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CAPTURE = FIXTURES / "macos"
WAGO = FIXTURES / "wago"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder; tests may name it (L6)
VERSION = "1.60.1.69913"  # the captured .build.info's version, listed in builds.json
TABLE_BUILD = "1.60.1.69876"  # the recorded table's build
ACCOUNT = "90000001#6"
CHARACTER = "Labcharb-Labrealmd"
SV_DIR = f"WTF/Account/{ACCOUNT}/SavedVariables"
SYNDICATOR = f"{SV_DIR}/Syndicator.lua"

runner = CliRunner()
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _plain(text: str) -> str:
    return _ANSI.sub("", text)


# ─── fakes ───────────────────────────────────────────────────────────────────


class FakeProcess:
    """The `psutil.Process` surface `wowlab_core.process` reads."""

    def __init__(self, pid: int, name: str, exe: str) -> None:
        self._pid, self._name, self._exe = pid, name, exe

    @property
    def pid(self) -> int:
        return self._pid

    def name(self) -> str:
        return self._name

    def exe(self) -> str:
        return self._exe

    def cmdline(self) -> list[str]:
        return [self._exe]

    def status(self) -> str:
        return "running"


def _use_table(monkeypatch: pytest.MonkeyPatch, *procs: FakeProcess) -> None:
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: iter(procs))


def _recorded(request: httpx.Request) -> httpx.Response:
    """wago.tools as recorded on 2026-09-21 (fixtures/wago, README rows)."""
    if request.url.path == "/api/builds":
        body = gzip.decompress((WAGO / "builds.json.gz").read_bytes())
        return httpx.Response(200, headers={"content-type": "application/json"}, content=body)
    if request.url.path == "/db2/ChrClasses/csv" and request.url.params.get("build") == TABLE_BUILD:
        name = f"ChrClasses.{TABLE_BUILD}.csv"
        return httpx.Response(
            200,
            headers={
                "content-type": "text/csv; charset=UTF-8",
                "content-disposition": f"attachment; filename=\"{name}\"; filename*=UTF-8''{name}",
            },
            content=(WAGO / name).read_bytes(),
        )
    return httpx.Response(
        404,
        headers={"content-type": "text/html; charset=utf-8"},
        content=(WAGO / "ChrClasses.1.60.1.1.404.html").read_bytes(),
    )


# ─── fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def user_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    redirected = tmp_path / "userdata"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: redirected / "wowlab")
    return redirected / "wowlab"


@pytest.fixture(autouse=True)
def idle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A process table with no client in it."""
    _use_table(
        monkeypatch,
        FakeProcess(101, "python3", str(tmp_path / "elsewhere" / "python3")),
        FakeProcess(102, "Finder", str(tmp_path / "elsewhere" / "Finder")),
    )


@pytest.fixture(autouse=True)
def replayed(monkeypatch: pytest.MonkeyPatch, user_data: Path) -> list[httpx.Request]:
    """Every game-data call goes to the recordings; no live request (ADR-0012)."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _recorded(request)

    @contextmanager
    def fake() -> Iterator[GameData]:
        source = WagoSource(transport=httpx.MockTransport(handler), sleep=lambda _: None)
        try:
            yield GameData(source, cache_dir=user_data / "gamedata")
        finally:
            source.close()

    monkeypatch.setattr(cli, "_open_gamedata", fake)
    return requests


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The captured tree as an install, named by `WOWLAB_WOW_ROOT`."""
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


def _running(monkeypatch: pytest.MonkeyPatch, flavor: Path) -> None:
    """A client whose executable lives in the flavor folder."""
    exe = flavor / "World of Warcraft Classic.app" / "Contents" / "MacOS" / "World of Warcraft"
    _use_table(monkeypatch, FakeProcess(7300, "World of Warcraft", str(exe)))


def run(*args: str, input: str | None = None) -> Any:
    return runner.invoke(cli.app, list(args), input=input)


def ok(*args: str, input: str | None = None) -> Any:
    result = run(*args, input=input)
    assert result.exit_code == 0, (result.stdout, result.stderr, result.exception)
    return result


def _json[M: BaseModel](model: type[M], *args: str) -> M:
    """Run with --json; the output validates against `model`."""
    result = ok(*args, "--json")
    return model.model_validate_json(result.stdout)


def _state(root: Path) -> dict[str, tuple[bytes | None, int]]:
    """Every entry under `root` with its bytes and mtime: what L1 must keep."""
    out: dict[str, tuple[bytes | None, int]] = {}
    for p in sorted(root.rglob("*")):
        st = p.lstat()
        out[p.relative_to(root).as_posix()] = (
            p.read_bytes() if p.is_file() else None,
            st.st_mtime_ns,
        )
    return out


# ─── doctor ──────────────────────────────────────────────────────────────────


def test_doctor_reports_install_flavor_client_and_store(root: Path) -> None:
    result = ok("doctor")
    out = _plain(result.stdout)
    assert f"Install: {root} (found by ${install.ENV_ROOT})" in out
    assert f"Flavor {FLAVOR}: product wow_classic_beta, version {VERSION}" in out
    assert f"Game data: wago.tools lists version {VERSION}" in out
    assert "synchronizeBindings not set (client default; wowlab does not know" in out
    assert "Client: not running" in out
    assert "Snapshot store:" in out


def test_doctor_json_validates_and_prints_only_the_version_of_a_match(root: Path) -> None:
    report = _json(cli.DoctorReport, "doctor")
    assert report.install_root == str(root)
    (f,) = report.flavors
    assert (f.folder, f.version, f.game_data.status) == (FLAVOR, VERSION, "published")
    assert f.game_data.version == VERSION
    assert {s.name for s in f.sync_cvars} == set(cli._SYNC_CVARS)
    assert all(s.value is None for s in f.sync_cvars), "the capture writes none of them"
    assert report.client is not None and report.client.state == "not_running"
    assert report.problems == []


def test_doctor_after_a_cross_product_match_prints_the_version_only_constructed(
    root: Path,
) -> None:
    """A flavor whose product the listing does not have, at a version listed
    under another product: only `.version` is stated, never the other
    product or its config hashes (§6.6, M10-14 amendment)."""
    for path in (root / ".build.info", root / FLAVOR / ".flavor.info"):
        path.write_bytes(path.read_bytes().replace(b"wow_classic_beta", b"wow_constructed"))
    result = ok("doctor")
    out = result.stdout
    assert f"Game data: wago.tools lists version {VERSION}" in out
    assert "wow_classic_beta" not in out
    listing = json.loads(gzip.decompress((WAGO / "builds.json.gz").read_bytes()))
    (matched,) = [b for b in listing["wow_classic_beta"] if b["version"] == VERSION]
    for field in ("build_config", "product_config", "cdn_config"):
        assert matched[field] not in out
    report = _json(cli.DoctorReport, "doctor")
    assert report.flavors[0].game_data.model_dump() == {
        "status": "published",
        "version": VERSION,
        "detail": None,
    }


def test_doctor_offline_makes_no_request(root: Path, replayed: list[httpx.Request]) -> None:
    report = _json(cli.DoctorReport, "doctor", "--offline")
    assert report.flavors[0].game_data.status == "not_checked"
    assert replayed == []


def test_doctor_passes_install_roots_and_executable_names_to_process(
    root: Path, flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (flavor / "Wow Constructed.exe").write_bytes(b"")  # constructed executable name
    seen: dict[str, Any] = {}

    def spy(install_roots: Any = (), **kwargs: Any) -> list[Any]:
        seen["roots"] = list(install_roots)
        seen.update(kwargs)
        return []

    monkeypatch.setattr(cli.process, "running_clients", spy)
    ok("doctor", "--offline")
    assert root in seen["roots"]
    assert seen["flavor_folders"] == [FLAVOR]
    assert "Wow Constructed.exe" in seen["extra_names"]


def test_doctor_names_a_running_client(
    root: Path, flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _running(monkeypatch, flavor)
    result = ok("doctor", "--offline")
    assert "Client: running" in result.stdout
    assert "pid 7300" in result.stdout


def test_doctor_without_an_install_lists_searched_and_could_not_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(install.ENV_ROOT, raising=False)
    absent = tmp_path / "absent" / "World of Warcraft"
    empty = tmp_path / "empty" / "World of Warcraft"
    empty.mkdir(parents=True)
    a_file = tmp_path / "file" / "World of Warcraft"
    a_file.parent.mkdir()
    a_file.write_bytes(b"")  # constructed: something that is not a directory
    monkeypatch.setattr(install, "default_roots", lambda **k: (absent, empty, a_file))
    result = run("doctor", "--offline")
    assert result.exit_code == 1
    assert f"searched, no install there: {absent}" in result.stdout
    assert f"searched, no install there: {empty}" in result.stdout
    assert f"could not check: {a_file} (not a directory)" in result.stdout
    assert "no install found at the default locations that could be checked" in result.stdout
    assert f"searched, no install there: {a_file}" not in result.stdout
    report = cli.DoctorReport.model_validate_json(run("doctor", "--offline", "--json").stdout)
    assert report.locations is not None
    assert report.locations.searched == [str(absent), str(empty)]
    assert [u.location for u in report.locations.could_not_check] == [str(a_file)]


# ─── install show ────────────────────────────────────────────────────────────


def test_install_show_text(root: Path) -> None:
    out = ok("install", "show").stdout
    assert f"Install: {root}" in out
    assert f"Flavor {FLAVOR}" in out
    assert f"version: {VERSION}" in out


def test_install_show_json_is_the_install_model(root: Path) -> None:
    got = _json(Install, "install", "show")
    assert got == install.read_install(root)


def test_install_show_not_found_names_what_could_not_be_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(install.ENV_ROOT, raising=False)
    a_file = tmp_path / "World of Warcraft"
    a_file.write_bytes(b"")  # constructed
    monkeypatch.setattr(install, "default_roots", lambda **k: (a_file,))
    result = run("install", "show")
    assert result.exit_code == 1
    assert f"could not check: {a_file} (not a directory)" in result.stderr
    assert "searched: nothing; could not check" in result.stderr


def test_several_flavors_without_flavor_is_a_usage_error_constructed(
    root: Path, flavor: Path
) -> None:
    other = root / "_constructed_"
    other.mkdir()
    shutil.copy2(flavor / ".flavor.info", other / ".flavor.info")
    result = run("sv", "list")
    assert result.exit_code == 2
    assert "choose one with --flavor" in result.stderr
    ok("sv", "list", "--flavor", FLAVOR)
    assert run("sv", "list", "--flavor", "_nope_").exit_code == 2


# ─── tree and explain ────────────────────────────────────────────────────────


def test_tree_lists_the_inventory(root: Path) -> None:
    out = ok("tree").stdout
    assert f"  {SYNDICATOR}  14,484 bytes" in out
    assert "  Interface/AddOns/DBM-Brawlers/" in out
    assert "  WTF/Config.wtf" in out


def test_tree_explain_adds_the_file_map_entry(root: Path) -> None:
    out = ok("tree", "--explain", "WTF/Config.wtf").stdout
    assert "[config-wtf]" in out
    assert "with the client closed" in out
    report = _json(cli.TreeReport, "tree", "--explain")
    by_path = {i.path: i for i in report.items}
    assert by_path[SYNDICATOR].explained is not None
    assert by_path[SYNDICATOR].explained.entry_id == "account-savedvariables"
    assert by_path[f"{SV_DIR}/RareScanner.lua.bak"].explained is not None
    assert by_path[f"{SV_DIR}/RareScanner.lua.bak"].explained.entry_id == "savedvariables-backup"


def test_tree_path_limits_the_listing(root: Path) -> None:
    report = _json(cli.TreeReport, "tree", "Interface")
    assert report.items
    assert all(i.path.startswith("Interface") for i in report.items)
    assert all(i.explained is None for i in report.items)


EXPLAIN_CASES = [
    (".build.info", "build-info"),
    (f"{FLAVOR}/.flavor.info", "flavor-info"),
    ("WTF/Config.wtf", "config-wtf"),
    (SYNDICATOR, "account-savedvariables"),
    (f"{SV_DIR}/RareScanner.lua.bak", "savedvariables-backup"),
    (f"WTF/Account/{ACCOUNT}/bindings-cache.wtf", "account-bindings-cache"),
    (f"WTF/Account/{ACCOUNT}/1/{CHARACTER}/", "second-name-character-folder"),
    ("Interface/AddOns/DBM-Brawlers/DBM-Brawlers.toc", "addon"),
    ("Data/data/data.000", "casc-data"),  # not present: classified by pattern
    (f"{FLAVOR}/Logs/WoWCombatLog-092726_120000.txt", "combat-log"),
    (f"{FLAVOR}/Fonts/FRIZQT__.TTF", "fonts"),
]


@pytest.mark.parametrize(("path", "entry_id"), EXPLAIN_CASES, ids=[c[0] for c in EXPLAIN_CASES])
def test_explain_returns_the_file_map_entry(root: Path, path: str, entry_id: str) -> None:
    report = _json(cli.ExplainReport, "explain", path)
    assert report.query == path
    assert report.result.status == "classified"
    assert isinstance(report.result, cli.layout.Classified)
    assert report.result.entry.id == entry_id
    text = ok("explain", path).stdout
    assert f"file-map row: {entry_id} " in text


def test_explain_an_absolute_path_and_one_relative_to_the_current_folder(
    root: Path, flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = _json(cli.ExplainReport, "explain", str(flavor / "WTF" / "Config.wtf"))
    assert isinstance(report.result, cli.layout.Classified)
    assert report.result.entry.id == "config-wtf"
    monkeypatch.chdir(flavor / "WTF")
    report = _json(cli.ExplainReport, "explain", "Config.wtf")
    assert isinstance(report.result, cli.layout.Classified)
    assert report.result.entry.id == "config-wtf"


def test_explain_a_path_with_no_row(root: Path) -> None:
    result = ok("explain", "unknown-constructed.txt")
    assert "no file-map row" in result.stdout
    report = _json(cli.ExplainReport, "explain", "unknown-constructed.txt")
    assert report.result.status == "unclassified"


# ─── sv ──────────────────────────────────────────────────────────────────────


def test_sv_list(root: Path) -> None:
    out = ok("sv", "list").stdout
    assert SYNDICATOR in out
    report = _json(cli.SvListReport, "sv", "list", "--account", ACCOUNT)
    paths = {f.path for f in report.files}
    assert SYNDICATOR in paths
    assert f"{SV_DIR}/RareScanner.lua.bak" in paths
    only_character = _json(cli.SvListReport, "sv", "list", "--character", CHARACTER)
    assert only_character.files == []


def test_sv_dump_prints_one_line_per_value(root: Path) -> None:
    out = ok("sv", "dump", f"{SV_DIR}/RareScanner.lua").stdout
    assert out == "RareScannerDB = nil\n"
    out = ok("sv", "dump", f"{SV_DIR}/DBM-StatusBarTimers.lua").stdout
    assert 'DBT_AllPersistentOptions["Default"]["DBM"]["TimerY"] = -260\n' in out
    assert '["Texture"] = "Interface\\\\AddOns\\\\DBM-StatusBarTimers' in out


def test_sv_dump_path(root: Path, flavor: Path) -> None:
    out = ok(
        "sv", "dump", str(flavor / SYNDICATOR), "--path", "SYNDICATOR_CONFIG.show_tooltips_on_shift"
    )
    assert out.stdout == "SYNDICATOR_CONFIG.show_tooltips_on_shift = false\n"
    missing = run("sv", "dump", SYNDICATOR, "--path", "SYNDICATOR_CONFIG.nope")
    assert missing.exit_code == 1
    assert "has no key" in missing.stderr
    bad = run("sv", "dump", SYNDICATOR, "--path", "SYNDICATOR_CONFIG..x")
    assert bad.exit_code == 2


def test_sv_dump_json_validates_and_keeps_raw_numbers(root: Path, flavor: Path) -> None:
    result = ok("sv", "dump", f"{SV_DIR}/DBM-StatusBarTimers.lua", "--json")
    assert result.stdout.count("\n") == 1, "compact: one line, written row by row"
    assert "a running client overwrites them at its next save" in result.stderr
    report = cli.SvDumpReport.model_validate_json(result.stdout)
    tops = [v for v in report.values if v.parent is None]
    assert [(v.style, v.name) for v in tops] == [("variable", "DBT_AllPersistentOptions")]
    assert [v.id for v in report.values] == list(range(len(report.values)))
    by_id = {v.id: v for v in report.values}

    def path_of(v: cli.SvValue) -> list[str]:
        out: list[str] = []
        while v.parent is not None:
            out.append(v.key.text if isinstance(v.key, cli.LuaStringNode) else str(v.name))
            v = by_id[v.parent]
        return [str(v.name), *reversed(out)]

    (g,) = [
        v
        for v in report.values
        if isinstance(v.key, cli.LuaStringNode) and v.key.text == "StartColorI2G"
    ]
    assert isinstance(g.value, cli.LuaNumberNode) and g.value.raw == "0.6745098233222961"
    assert path_of(g) == ["DBT_AllPersistentOptions", "Default", "DBM", "StartColorI2G"]
    tables = [v for v in report.values if isinstance(v.value, cli.LuaTableNode)]
    for t in tables:
        assert isinstance(t.value, cli.LuaTableNode)
        assert t.value.entries == sum(1 for v in report.values if v.parent == t.id)
    one = _json(cli.SvDumpReport, "sv", "dump", SYNDICATOR, "--path", "SYNDICATOR_CONFIG")
    assert one.values[0].style == "path" and one.values[0].name == "SYNDICATOR_CONFIG"
    assert one.values[0].value.type == "table"


def test_sv_dump_json_of_a_deep_file_stays_small_and_validates_constructed(
    tmp_path: Path,
) -> None:
    """Hostile boundary: ~100 KB of tables nested to MAX_DEPTH. The flat shape
    validates at every accepted depth and the output grows with the number
    of values, not with depth times indentation."""
    depth = luadata.MAX_DEPTH - 1
    chain = b"{" * depth + b"}" * depth
    data = b"X = {" + b",".join([chain] * 250) + b"}\n"
    assert 90_000 < len(data) < 110_000
    f = tmp_path / "Deep.lua"
    f.write_bytes(data)
    result = ok("sv", "dump", str(f), "--json")
    report = cli.SvDumpReport.model_validate_json(result.stdout)
    assert len(report.values) == 1 + 250 * depth
    assert len(result.stdout) < 200 * len(report.values)


def test_sv_dump_over_the_parse_budget_is_refused_cleanly(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Boundary: the real Syndicator file with the budget lowered below it."""
    monkeypatch.setattr(luadata, "MAX_COST", 20_000)
    result = run("sv", "dump", SYNDICATOR)
    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit), "a refusal, never a traceback"
    assert "refused: this file is beyond what the SavedVariables parser will hold" in result.stderr
    assert "MAX_COST" in result.stderr
    assert result.stdout == ""


def test_sv_dump_refuses_code_constructed(tmp_path: Path, root: Path) -> None:
    hostile = tmp_path / "hostile.lua"
    hostile.write_bytes(b"X = os.execute('true')\n")  # constructed: never run (L3)
    result = run("sv", "dump", str(hostile))
    assert result.exit_code == 1
    assert "not SavedVariables data the parser accepts: line 1" in result.stderr


# ─── cvar, binds, macros, addons ─────────────────────────────────────────────


def test_cvar_list_and_get(root: Path) -> None:
    report = _json(cli.CVarListReport, "cvar", "list")
    assert report.file == "WTF/Config.wtf"
    assert report.cvars
    first = report.cvars[0]
    got = _json(cli.CVarGetReport, "cvar", "get", first.name.upper())
    assert (got.value, got.line) == (first.value, first.line)
    assert f'{first.name} = "{first.value}"' in ok("cvar", "get", first.name).stdout
    absent = _json(cli.CVarGetReport, "cvar", "get", "synchronizeBindings")
    assert absent.value is None
    assert "is not set in WTF/Config.wtf" in ok("cvar", "get", "synchronizeBindings").stdout


def test_cvar_scopes(root: Path) -> None:
    account = _json(cli.CVarListReport, "cvar", "list", "--scope", "account")
    assert account.file == f"WTF/Account/{ACCOUNT}/config-cache.wtf"
    character = _json(
        cli.CVarListReport, "cvar", "list", "--scope", "character", "--character", CHARACTER
    )
    assert character.file == f"WTF/Account/{ACCOUNT}/1/{CHARACTER}/config-cache.wtf"
    assert character.character == f"1/{CHARACTER}"
    assert run("cvar", "list", "--scope", "character").exit_code == 2


def test_binds_list(root: Path) -> None:
    report = _json(cli.BindsReport, "binds", "list")
    assert report.file == f"WTF/Account/{ACCOUNT}/bindings-cache.wtf"
    assert {b.key for b in report.bindings} >= {"Q", "E"}
    none = _json(cli.BindsReport, "binds", "list", "--character", CHARACTER)
    assert none.file is None and none.bindings == []
    assert "No bindings-cache.wtf" in ok("binds", "list", "--character", CHARACTER).stdout


def test_macros_list(root: Path) -> None:
    account = _json(cli.MacrosReport, "macros", "list")
    assert account.macros == []
    character = _json(cli.MacrosReport, "macros", "list", "--character", CHARACTER)
    assert [m.name for m in character.macros] == ["PolyArc"]
    assert "/castsequence" in ok("macros", "list", "--character", CHARACTER).stdout


def test_addons_list(root: Path) -> None:
    report = _json(cli.AddonsReport, "addons", "list")
    assert [a.name for a in report.addons] == ["DBM-Brawlers", "DBM-Challenges"]
    out = ok("addons", "list").stdout
    assert "<DBM Mod> Brawlers" in out, "colour escapes are dropped from the title"
    assert "TOC declares Interface 120100" in out


# ─── db2 ─────────────────────────────────────────────────────────────────────


def test_db2_builds(root: Path) -> None:
    report = _json(cli.BuildsReport, "db2", "builds", "--product", "wow_classic_beta")
    assert VERSION in {b.version for b in report.products["wow_classic_beta"]}
    assert "wow_classic_beta:" in ok("db2", "builds").stdout
    assert run("db2", "builds", "--product", "nope").exit_code == 1


def test_db2_fetch_and_head(root: Path, user_data: Path) -> None:
    fetched = _json(cli.FetchReport, "db2", "fetch", "ChrClasses", "--build", TABLE_BUILD)
    assert Path(fetched.path).is_relative_to(user_data)
    assert fetched.sidecar is not None and fetched.sidecar.build == TABLE_BUILD
    head = _json(cli.HeadReport, "db2", "head", "ChrClasses", "--build", TABLE_BUILD, "-n", "2")
    assert len(head.rows) == 2
    text = ok("db2", "head", "ChrClasses", "--build", TABLE_BUILD, "-n", "1").stdout
    assert len(text.splitlines()) == 2  # header and one row


def test_db2_fetch_defaults_to_the_flavor_version_and_reports_unpublished(root: Path) -> None:
    result = run("db2", "fetch", "ChrClasses")
    assert result.exit_code == 1
    assert VERSION in result.stderr


# ─── snap ────────────────────────────────────────────────────────────────────


def _create(label: str) -> Manifest:
    result = ok("snap", "create", "-m", label, "--json")
    manifest = Manifest.model_validate_json(result.stdout)
    assert result.stdout.encode("ascii") == snapshot.manifest_bytes(manifest)
    return manifest


def _edit_syndicator(flavor: Path, old: bytes, new: bytes) -> None:
    path = flavor / SYNDICATOR
    data = path.read_bytes()
    assert old in data
    path.write_bytes(data.replace(old, new))


def test_snap_create_uses_the_layout_subtrees_and_reads_only(root: Path, flavor: Path) -> None:
    before = _state(root)
    manifest = _create("baseline")
    assert _state(root) == before, "creating a snapshot writes nothing in the install (L1)"
    assert manifest.subtrees == tuple(
        sorted(f"{FLAVOR}/{s}" for s in ("WTF", "Interface", "Fonts"))
    )
    assert manifest.flavor_folder == FLAVOR
    assert manifest.flavor_version == VERSION
    assert manifest.client_running is False
    assert manifest.entry(f"{FLAVOR}/{SYNDICATOR}") is not None
    assert "Snapshot " in ok("snap", "create").stdout


def test_snap_create_records_a_running_client(
    root: Path, flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _running(monkeypatch, flavor)
    result = ok("snap", "create", "--json")
    assert Manifest.model_validate_json(result.stdout).client_running is True
    assert "could not confirm the client was closed" in result.stderr
    shown = ok("snap", "show", Manifest.model_validate_json(result.stdout).id).stdout
    assert "The client was running when this was taken" in shown


def test_snap_list_show_and_verify(root: Path) -> None:
    first = _create("one")
    listing = _json(cli.SnapListReport, "snap", "list")
    assert [s.id for s in listing.snapshots] == [first.id]
    assert listing.damaged == []
    shown = ok("snap", "show", first.id[:15], "--json")
    assert shown.stdout.encode("ascii") == snapshot.manifest_bytes(first)
    assert f"Snapshot {first.id}" in ok("snap", "show", first.id).stdout
    verified = _json(VerifyReport, "snap", "verify")
    assert verified.ok and verified.manifests_checked == 1


def test_snap_list_names_a_damaged_manifest_and_exits_nonzero_constructed(
    root: Path, user_data: Path
) -> None:
    good = _create("good")
    bad = user_data / "store" / "manifests" / "20260101T000000.000000Z-00000000.json"
    bad.write_bytes(b"{not json")  # constructed damage
    result = run("snap", "list")
    assert result.exit_code == 1
    assert good.id in result.stdout
    assert f"damaged manifest {bad.name}" in result.stderr
    as_json = run("snap", "list", "--json")
    assert as_json.exit_code == 1
    report = cli.SnapListReport.model_validate_json(as_json.stdout)
    assert [s.id for s in report.snapshots] == [good.id]
    assert [d.name for d in report.damaged] == [bad.name]


def test_snap_diff_shows_the_changed_values_of_saved_variables(root: Path, flavor: Path) -> None:
    a = _create("a")
    _edit_syndicator(
        flavor, b'"show_tooltips_on_shift"] = false', b'"show_tooltips_on_shift"] = true'
    )
    b = _create("b")
    out = ok("snap", "diff", a.id, b.id).stdout
    assert f"changed  {FLAVOR}/{SYNDICATOR}" in out
    assert '~ SYNDICATOR_CONFIG["show_tooltips_on_shift"]: false -> true' in out
    report = _json(cli.DiffReport, "snap", "diff", a.id, b.id)
    (changed,) = report.changed
    assert changed.lua == [
        cli.LuaChange(
            path='SYNDICATOR_CONFIG["show_tooltips_on_shift"]', before="false", after="true"
        )
    ]


def test_snap_diff_falls_back_to_the_path_when_a_side_does_not_parse_constructed(
    root: Path, flavor: Path
) -> None:
    a = _create("a")
    (flavor / SYNDICATOR).write_bytes(b"X = function() end\n")  # constructed
    b = _create("b")
    report = _json(cli.DiffReport, "snap", "diff", a.id, b.id)
    (changed,) = report.changed
    assert changed.path == f"{FLAVOR}/{SYNDICATOR}"
    assert changed.lua is None
    assert changed.lua_note is not None and "does not parse" in changed.lua_note
    assert f"changed  {FLAVOR}/{SYNDICATOR}" in ok("snap", "diff", a.id, b.id).stdout


def test_snap_gc_lists_then_removes_unreferenced_objects(root: Path, user_data: Path) -> None:
    _create("keep")
    store = SnapshotStore(user_data / "store")
    stray = b"constructed stray object"
    digest = hashlib.sha256(stray).hexdigest()
    path = store.object_path(digest)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(zlib.compress(stray))
    old = path.stat().st_mtime - 2 * cli.GC_GRACE_SECONDS
    os.utime(path, (old, old))  # older than the grace period
    dry = _json(GcReport, "snap", "gc", "--dry-run")
    assert dry.unreferenced == (digest,) and path.exists()
    declined = run("snap", "gc", input="n\n")
    assert declined.exit_code == 1 and path.exists()
    removed = _json(GcReport, "snap", "gc", "--yes")
    assert removed.removed == (digest,)
    assert not path.exists()


def test_snap_gc_runs_wholly_inside_the_store_lock(
    root: Path, user_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _create("keep")
    held: list[bool] = []
    calls: list[tuple[bool, bool]] = []
    real_lock = guard.store_lock
    real_gc = SnapshotStore.gc

    @contextmanager
    def spy_lock(store: Path | None = None) -> Iterator[None]:
        with real_lock(store):
            held.append(True)
            try:
                yield
            finally:
                held.pop()

    def spy_gc(self: SnapshotStore, **kwargs: Any) -> GcReport:
        calls.append((bool(held), kwargs["dry_run"]))
        return real_gc(self, **kwargs)

    monkeypatch.setattr(guard, "store_lock", spy_lock)
    monkeypatch.setattr(SnapshotStore, "gc", spy_gc)
    ok("snap", "gc", "--yes")
    assert calls and all(inside for inside, _ in calls)


def test_snap_gc_is_refused_while_the_store_is_busy(root: Path, user_data: Path) -> None:
    _create("keep")
    with guard.store_lock(user_data / "store"):
        result = run("snap", "gc", "--dry-run")
    assert result.exit_code == 3
    assert "refused by the write gate" in result.stderr


# ─── restore and undo: through the gate ──────────────────────────────────────


def test_restore_dry_run_prints_the_plan_and_changes_nothing(root: Path, flavor: Path) -> None:
    a = _create("a")
    _edit_syndicator(
        flavor, b'"show_tooltips_on_shift"] = false', b'"show_tooltips_on_shift"] = true'
    )
    before = _state(root)
    out = ok("snap", "restore", a.id, "--dry-run").stdout
    assert f"replace  {SYNDICATOR}" in out
    assert "Dry run: nothing was changed." in out
    assert _state(root) == before


def test_restore_asks_and_a_no_changes_nothing(root: Path, flavor: Path) -> None:
    a = _create("a")
    _edit_syndicator(
        flavor, b'"show_tooltips_on_shift"] = false', b'"show_tooltips_on_shift"] = true'
    )
    before = _state(root)
    result = run("snap", "restore", a.id, input="n\n")
    assert result.exit_code == 1
    assert "Apply these changes?" in result.stderr
    assert "Nothing was changed." in result.stderr
    assert _state(root) == before
    assert guard.history() == ()


def test_restore_yes_then_undo(root: Path, flavor: Path) -> None:
    a = _create("a")
    original = (flavor / SYNDICATOR).read_bytes()
    _edit_syndicator(
        flavor, b'"show_tooltips_on_shift"] = false', b'"show_tooltips_on_shift"] = true'
    )
    edited = (flavor / SYNDICATOR).read_bytes()
    out = ok("snap", "restore", a.id, "--yes").stdout
    assert "Restored 1 file(s)" in out
    assert (flavor / SYNDICATOR).read_bytes() == original
    (record,) = guard.history()
    assert record.state == "committed"
    assert [p.path for p in record.paths] == [SYNDICATOR]

    shown = run("undo", input="n\n")
    assert shown.exit_code == 1
    assert f"put back   {SYNDICATOR}" in shown.stdout
    assert (flavor / SYNDICATOR).read_bytes() == original
    ok("undo", "--yes")
    assert (flavor / SYNDICATOR).read_bytes() == edited
    assert len(guard.history()) == 2


def test_restore_rolls_back_when_the_files_change_after_the_plan_was_shown(
    root: Path, flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The plan the owner agreed to is the plan applied, or nothing is."""
    a = _create("a")
    _edit_syndicator(
        flavor, b'"show_tooltips_on_shift"] = false', b'"show_tooltips_on_shift"] = true'
    )
    config = flavor / "WTF" / "Config.wtf"

    def edit_while_asking(*args: Any, **kwargs: Any) -> bool:
        config.write_bytes(config.read_bytes() + b'SET constructedCVar "1"\n')
        return True

    monkeypatch.setattr(cli.typer, "confirm", edit_while_asking)
    result = run("snap", "restore", a.id)
    assert result.exit_code == 1
    assert "rolled back" in result.stderr
    assert b'"show_tooltips_on_shift"] = true' in (flavor / SYNDICATOR).read_bytes()
    assert b"constructedCVar" in config.read_bytes()
    (record,) = guard.history()
    assert record.state == "rolled_back"


def test_undo_refuses_when_the_journal_changed_after_the_plan_was_shown(
    root: Path, flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a = _create("a")
    _edit_syndicator(
        flavor, b'"show_tooltips_on_shift"] = false', b'"show_tooltips_on_shift"] = true'
    )
    ok("snap", "restore", a.id, "--yes")
    (discovered,) = install.read_install(root).flavors

    def transact_while_asking(*args: Any, **kwargs: Any) -> bool:
        with guard.transaction(discovered, label="constructed") as tx:
            tx.write("WTF/constructed.txt", b"x")
        return True

    monkeypatch.setattr(cli.typer, "confirm", transact_while_asking)
    result = run("undo")
    assert result.exit_code == 1
    assert "nothing was undone" in result.stderr
    assert [r.label for r in guard.history()][-1] == "constructed"
    assert (flavor / "WTF" / "constructed.txt").read_bytes() == b"x"


def test_undo_passes_the_shown_id_so_a_change_after_the_re_read_is_refused(
    root: Path, flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M10-17: `undo` hands the gate the id of the record it showed, so a
    transaction journaled after the CLI's own re-read (in the window before
    the gate holds its store lock) is refused by the gate, exit 3, with
    nothing undone."""
    a = _create("a")
    _edit_syndicator(
        flavor, b'"show_tooltips_on_shift"] = false', b'"show_tooltips_on_shift"] = true'
    )
    ok("snap", "restore", a.id, "--yes")
    (shown,) = guard.history()
    (discovered,) = install.read_install(root).flavors
    real_history = guard.history
    calls: list[int] = []

    def history_then_commit(*args: Any, **kwargs: Any) -> tuple[guard.HistoryRecord, ...]:
        records = real_history(*args, **kwargs)
        calls.append(len(records))
        if len(calls) == 2:  # the CLI's re-read after the prompt: commit behind it
            with guard.transaction(discovered, label="constructed") as tx:
                tx.write("WTF/constructed.txt", b"x")
        return records

    expected: list[str | None] = []
    real_undo = guard.undo

    def undo_spy(**kwargs: Any) -> None:
        expected.append(kwargs.get("expected_id"))
        real_undo(**kwargs)

    with pytest.MonkeyPatch.context() as patched:
        patched.setattr(guard, "history", history_then_commit)
        patched.setattr(guard, "undo", undo_spy)
        result = run("undo", "--yes")
    assert expected == [shown.id]
    assert result.exit_code == 3
    assert "refused by the write gate" in result.stderr
    assert shown.id in result.stderr
    records = guard.history()
    assert [r.label for r in records][-1] == "constructed", "no undo was journaled"
    assert (flavor / "WTF" / "constructed.txt").read_bytes() == b"x"
    assert b'"show_tooltips_on_shift"] = false' in (flavor / SYNDICATOR).read_bytes()


def test_restore_named_paths(root: Path, flavor: Path) -> None:
    a = _create("a")
    _edit_syndicator(
        flavor, b'"show_tooltips_on_shift"] = false', b'"show_tooltips_on_shift"] = true'
    )
    config = flavor / "WTF" / "Config.wtf"
    config.write_bytes(config.read_bytes() + b'SET constructedCVar "1"\n')
    ok("snap", "restore", a.id, "--paths", "WTF/Config.wtf", "--yes")
    assert b"constructedCVar" not in config.read_bytes()
    assert b'"show_tooltips_on_shift"] = true' in (flavor / SYNDICATOR).read_bytes()


def test_restore_is_refused_with_exit_3_while_the_client_runs(
    root: Path, flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a = _create("a")
    _edit_syndicator(
        flavor, b'"show_tooltips_on_shift"] = false', b'"show_tooltips_on_shift"] = true'
    )
    before = _state(root)
    _running(monkeypatch, flavor)
    result = run("snap", "restore", a.id, "--yes")
    assert result.exit_code == 3
    assert "refused by the write gate" in result.stderr
    assert "running" in result.stderr
    assert _state(root) == before


def test_undo_is_refused_with_exit_3_while_the_client_runs(
    root: Path, flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a = _create("a")
    _edit_syndicator(
        flavor, b'"show_tooltips_on_shift"] = false', b'"show_tooltips_on_shift"] = true'
    )
    ok("snap", "restore", a.id, "--yes")
    before = _state(root)
    _running(monkeypatch, flavor)
    result = run("undo", "--yes")
    assert result.exit_code == 3
    assert _state(root) == before


def test_restore_of_another_install_is_refused(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a = _create("a")
    other = tmp_path / "other" / "World of Warcraft"
    shutil.copytree(root, other)
    monkeypatch.setenv(install.ENV_ROOT, str(other))
    result = run("snap", "restore", a.id, "--yes")
    assert result.exit_code == 3
    assert "was taken of" in result.stderr


def test_restore_when_nothing_differs(root: Path) -> None:
    a = _create("a")
    assert "Nothing to restore" in ok("snap", "restore", a.id).stdout
    assert guard.history() == ()


def test_undo_with_an_empty_journal(root: Path) -> None:
    result = run("undo", "--yes")
    assert result.exit_code == 1
    assert "nothing to undo" in result.stderr


# ─── read commands never write (L1) ─────────────────────────────────────────


def test_read_commands_leave_the_install_untouched(root: Path) -> None:
    before = _state(root)
    for args in (
        ("doctor",),
        ("install", "show"),
        ("tree", "--explain"),
        ("explain", "WTF/Config.wtf"),
        ("sv", "list"),
        ("sv", "dump", SYNDICATOR),
        ("cvar", "list"),
        ("binds", "list"),
        ("macros", "list", "--character", CHARACTER),
        ("addons", "list"),
        ("snap", "create"),
        ("snap", "list"),
    ):
        ok(*args)
    assert _state(root) == before


def test_help_lists_every_command() -> None:
    out = _plain(ok("--help").stdout)
    for name in ("doctor", "install", "tree", "explain", "sv", "cvar", "binds", "macros"):
        assert name in out
    for name in ("addons", "db2", "snap", "undo"):
        assert name in out


# ─── fix round 1 (PR #68 reviews, owner decision 2026-09-27) ────────────────

BAK = f"{SV_DIR}/RareScanner.lua.bak"
RARE = f"{SV_DIR}/RareScanner.lua"
DS_STORE = "WTF/.DS_Store"


def _flip_syndicator(flavor: Path) -> None:
    _edit_syndicator(
        flavor, b'"show_tooltips_on_shift"] = false', b'"show_tooltips_on_shift"] = true'
    )


def test_whole_restore_skips_files_the_client_manages(root: Path, flavor: Path) -> None:
    """Owner decision 2026-09-27: rows with Edit `no` (`.lua.bak`, OS metadata,
    ...) are left alone by a whole-snapshot restore, and the plan says so."""
    (flavor / DS_STORE).write_bytes(b"constructed Finder metadata")
    a = _create("a")
    for rel in (BAK, RARE, DS_STORE):
        path = flavor / rel
        path.write_bytes(path.read_bytes() + b"\n-- changed\n")
    changed_bak = (flavor / BAK).read_bytes()
    changed_ds = (flavor / DS_STORE).read_bytes()

    plan = _json(cli.RestoreReport, "snap", "restore", a.id, "--dry-run")
    assert [i.path for i in plan.plan] == [RARE]
    assert {(s.path, s.entry_id) for s in plan.skipped} == {
        (BAK, "savedvariables-backup"),
        (DS_STORE, "os-metadata"),
    }
    text = ok("snap", "restore", a.id, "--dry-run").stdout
    assert (
        '  Skipped 2 file(s) wowlab leaves alone (file-map Edit "no": client-written backups, '
        "Blizzard_* folders, file-browser metadata; name one with --paths to restore it):"
    ) in text
    assert "name one with --paths to restore it" in text

    ok("snap", "restore", a.id, "--yes")
    assert (flavor / RARE).read_bytes() == (CAPTURE / "forever" / RARE).read_bytes()
    assert (flavor / BAK).read_bytes() == changed_bak
    assert (flavor / DS_STORE).read_bytes() == changed_ds
    (record,) = guard.history()
    assert [p.path for p in record.paths] == [RARE]


def test_restore_writes_a_client_managed_file_when_named(root: Path, flavor: Path) -> None:
    a = _create("a")
    (flavor / BAK).write_bytes(b"constructed\n")
    report = _json(cli.RestoreReport, "snap", "restore", a.id, "--paths", BAK, "--yes")
    assert report.applied and report.skipped == []
    assert [i.path for i in report.plan] == [BAK]
    assert (flavor / BAK).read_bytes() == (CAPTURE / "forever" / BAK).read_bytes()


def test_whole_restore_with_only_skipped_changes_writes_nothing(root: Path, flavor: Path) -> None:
    a = _create("a")
    (flavor / BAK).write_bytes(b"constructed\n")
    result = ok("snap", "restore", a.id, "--yes")
    assert "Nothing to restore but the skipped files" in result.stdout
    assert guard.history() == ()


def test_restore_json_carries_the_plan_and_the_result(root: Path, flavor: Path) -> None:
    a = _create("a")
    before = (flavor / SYNDICATOR).read_bytes()
    _flip_syndicator(flavor)
    config = flavor / "WTF" / "Config.wtf"
    config.write_bytes(config.read_bytes() + b'SET constructedCVar "1"\n')

    dry = _json(cli.RestoreReport, "snap", "restore", a.id, "--dry-run")
    assert dry.dry_run and not dry.applied and dry.transaction is None
    by_path = {i.path: i for i in dry.plan}
    assert set(by_path) == {SYNDICATOR, "WTF/Config.wtf"}
    item = by_path[SYNDICATOR]
    assert item.before == hashlib.sha256((flavor / SYNDICATOR).read_bytes()).hexdigest()
    assert item.after == hashlib.sha256(before).hexdigest()
    assert item.size == len(before)
    assert _state(root)[f"{FLAVOR}/{SYNDICATOR}"][0] != before, "a dry run changes nothing"

    done = _json(cli.RestoreReport, "snap", "restore", a.id, "--yes")
    assert done.applied and done.plan == dry.plan
    assert done.transaction == guard.history()[-1].id
    assert done.notes == [cli._RELOGIN], "Config.wtf was touched"
    text_undo = _json(cli.UndoReport, "undo", "--yes")
    assert text_undo.applied and text_undo.undone == done.transaction
    assert {p.path for p in text_undo.plan} == {SYNDICATOR, "WTF/Config.wtf"}
    assert text_undo.transaction == guard.history()[-1].id != done.transaction


def test_undo_json_declined_reports_not_applied(root: Path, flavor: Path) -> None:
    a = _create("a")
    _flip_syndicator(flavor)
    ok("snap", "restore", a.id, "--yes")
    result = run("undo", "--json", input="n\n")
    assert result.exit_code == 1
    # The test runner echoes typed input onto stdout; a terminal does not.
    report = cli.UndoReport.model_validate_json(result.stdout[result.stdout.index("{") :])
    assert not report.applied and report.transaction is None
    assert [p.path for p in report.plan] == [SYNDICATOR]


def test_restore_and_diff_name_a_version_change_constructed(root: Path, flavor: Path) -> None:
    a = _create("a")
    info = root / ".build.info"
    info.write_bytes(info.read_bytes().replace(VERSION.encode(), b"1.60.1.69977"))
    _flip_syndicator(flavor)
    b = _create("b")
    out = ok("snap", "restore", a.id, "--dry-run").stdout
    assert f"Taken on version {VERSION}; {FLAVOR} is now on 1.60.1.69977." in out
    diff = ok("snap", "diff", a.id, b.id).stdout
    assert f"({a.id} was taken on {VERSION}; {b.id} on 1.60.1.69977)" in diff
    report = _json(cli.DiffReport, "snap", "diff", a.id, b.id)
    assert (report.a_version, report.b_version) == (VERSION, "1.60.1.69977")


def test_restore_into_a_renamed_flavor_folder_says_why(
    root: Path, flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    a = _create("a")
    flavor.rename(root / "_constructed_")
    result = run("snap", "restore", a.id, "--yes")
    assert result.exit_code == 1
    assert "Flavor folders are renamed between beta and launch" in result.stderr


def test_snap_diff_marks_an_unreadable_side(root: Path, flavor: Path, user_data: Path) -> None:
    a = _create("a")
    _flip_syndicator(flavor)
    b = _create("b")
    store = SnapshotStore(user_data / "store")
    (change,) = store.diff(a.id, b.id).changed
    assert change.before.sha256 is not None
    store.object_path(change.before.sha256).unlink()  # constructed damage
    report = _json(cli.DiffReport, "snap", "diff", a.id, b.id)
    (changed,) = report.changed
    assert changed.lua is None
    assert changed.lua_note is not None and changed.lua_note.startswith("contents unreadable")


def _gc_during_create(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Run `snap gc --yes` once, while a create has stored its objects but
    not yet written its manifest."""
    real = snapshot.manifest_bytes
    during: list[Any] = []

    def gc_meanwhile(manifest: Manifest) -> bytes:
        if not during:
            during.append(run("snap", "gc", "--yes", "--json"))
        return real(manifest)

    monkeypatch.setattr(snapshot, "manifest_bytes", gc_meanwhile)
    return during


def test_the_first_snap_create_holds_the_store_lock(
    root: Path, user_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M10-17 (security review 1 of M10-14): with no store yet, `snap create`
    makes it through `guard.store_lock(create=True)` and holds its lock, so a
    gc in the middle of it is refused like one during any later create."""
    assert not (user_data / "store").exists()
    # A context, not `monkeypatch.undo()`, which would also drop the user
    # data redirection and send the checks below to the real store.
    with pytest.MonkeyPatch.context() as patched:
        during = _gc_during_create(patched)
        ok("snap", "create")
    (gc,) = during
    assert gc.exit_code == 3, "the gc was refused by the store lock"
    assert (user_data / "store" / "lock").is_file()
    assert _json(VerifyReport, "snap", "verify").ok


def test_gc_grace_keeps_the_young_objects_of_an_unlocked_library_create(
    root: Path, user_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Defence in depth kept by M10-17: a library caller of
    `SnapshotStore.create` holds no store lock, so a gc can run in the middle
    of it; `GC_GRACE_SECONDS` keeps the objects it has stored so far."""
    (discovered,) = install.read_install(root).flavors
    with pytest.MonkeyPatch.context() as patched:
        during = _gc_during_create(patched)
        made = SnapshotStore(user_data / "store").create(
            root, [f"{discovered.folder}/WTF"], label="library, unlocked"
        )
    (gc,) = during
    assert gc.exit_code == 0, "unlocked: the gc ran"
    assert GcReport.model_validate_json(gc.stdout).removed == ()
    assert [m.id for m in SnapshotStore(user_data / "store").list()] == [made.id]
    assert _json(VerifyReport, "snap", "verify").ok


def test_a_later_snap_create_holds_the_store_lock(
    root: Path, user_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _create("first")
    with pytest.MonkeyPatch.context() as patched:
        during = _gc_during_create(patched)
        ok("snap", "create")
    (gc,) = during
    assert gc.exit_code == 3, "the gc was refused by the store lock"
    assert _json(VerifyReport, "snap", "verify").ok


_REAL_USER_DATA_PATH = platformdirs.user_data_path


def _tree_names(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*")}


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_a_user_data_dir_linked_into_an_install_creates_nothing_there_constructed(
    root: Path, flavor: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Security repro A: the user data directory is a symlink into the install."""
    link = tmp_path / "linked-user-data"
    link.symlink_to(flavor / "WTF", target_is_directory=True)
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: link / "wowlab")
    before = _tree_names(root)
    result = run("snap", "create")
    # M10-17: the first create goes through `guard.store_lock(create=True)`,
    # so the refusal is the gate's (exit 3), before anything is created.
    assert result.exit_code == 3
    assert "is inside an install" in result.stderr
    assert run("snap", "gc", "--yes").exit_code == 0  # no store: nothing to collect
    assert _tree_names(root) == before


@pytest.mark.skipif(sys.platform == "win32", reason="the Windows data folder does not follow HOME")
def test_home_inside_an_install_creates_nothing_there_constructed(
    root: Path, flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Security repro B: HOME (so the platform's user data directory) is
    inside the install."""
    monkeypatch.setattr(platformdirs, "user_data_path", _REAL_USER_DATA_PATH)
    home = flavor / "WTF" / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    assert root in platformdirs.user_data_path("wowlab").parents
    before = _tree_names(root)
    result = run("snap", "create")
    assert result.exit_code == 3  # refused by the gate's store_lock(create=True) (M10-17)
    assert _tree_names(root) == before


def test_a_user_data_dir_inside_another_install_creates_nothing_there_constructed(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The store is refused inside any install, not only the one captured."""
    other = tmp_path / "other" / "World of Warcraft"
    shutil.copytree(root, other)
    inside = other / FLAVOR / "WTF" / "ud" / "wowlab"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: inside)
    before = _tree_names(other)
    result = run("snap", "create")
    assert result.exit_code == 3  # refused by the gate's store_lock(create=True) (M10-17)
    assert "is inside an install" in result.stderr
    assert _tree_names(other) == before


def test_snap_gc_keeps_young_unreferenced_objects(root: Path, user_data: Path) -> None:
    _create("keep")
    store = SnapshotStore(user_data / "store")
    stray = b"constructed young stray object"
    path = store.object_path(hashlib.sha256(stray).hexdigest())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(zlib.compress(stray))
    assert _json(GcReport, "snap", "gc", "--yes").removed == ()
    assert path.exists()


def test_client_names_follow_the_gates_rule_constructed(tmp_path: Path, root: Path) -> None:
    folder = tmp_path / "names"
    (folder / "World of Warcraft.app" / "Contents" / "MacOS").mkdir(parents=True)
    (folder / "World of Warcraft.app" / "Contents" / "MacOS" / "World of Warcraft").write_bytes(b"")
    (folder / "Other.APP").mkdir()
    for name in ("Wow.exe", "WowT.EXE", "readme.txt", "Utils.dll"):
        (folder / name).write_bytes(b"")
    assert cli._client_names(folder) == guard._client_names(folder)
    assert "World of Warcraft" in cli._client_names(folder)
    missing = tmp_path / "missing"
    with pytest.raises(OSError):
        guard._client_names(missing)
    with pytest.raises(OSError):
        cli._client_names(missing)


def test_doctor_cannot_check_the_client_when_a_flavor_folder_cannot_be_listed(
    root: Path, flavor: Path
) -> None:
    inst = install.read_install(root)
    shutil.rmtree(flavor)  # gone between discovery and the check
    report = cli._client_report(inst)
    assert report.state == "could_not_check"
    assert report.error is not None


@pytest.mark.skipif(
    sys.platform == "win32", reason="Windows forbids control characters 0-31 in file names"
)
def test_control_characters_in_a_file_name_never_reach_the_terminal_constructed(
    root: Path, flavor: Path
) -> None:
    """Security review 4: ESC and OSC in a file name are shown escaped."""
    esc_name = "evil\x1b]0;pwned\x07\x1b[31m.txt"
    (flavor / "WTF" / esc_name).write_bytes(b"")
    tree = ok("tree")
    assert "\x1b" not in tree.stdout and "\x07" not in tree.stdout
    assert "evil\\x1b]0;pwned\\x07\\x1b[31m.txt" in tree.stdout


def test_control_characters_never_reach_the_terminal_constructed(
    root: Path, flavor: Path, tmp_path: Path
) -> None:
    """Security review 4: ESC and OSC in a value are shown escaped; C1 CSI in
    a path the gate accepts is escaped in the plans."""
    lua = tmp_path / "Esc.lua"
    lua.write_bytes(b'X = "a\x1b]0;t\x07b"\n')
    dumped = ok("sv", "dump", str(lua))
    assert "\x1b" not in dumped.stdout and '"a\\x1b]0;t\\x07b"' in dumped.stdout

    csi = "WTF/csi\x9b31m.txt"
    (flavor / csi).write_bytes(b"one")
    a = _create("a")
    (flavor / csi).write_bytes(b"two")
    plan = ok("snap", "restore", a.id, "--yes").stdout
    assert "\x9b" not in plan and "csi\\x9b31m.txt" in plan
    undo = ok("undo", "--yes").stdout
    assert "\x9b" not in undo and "csi\\x9b31m.txt" in undo


def test_explain_words_for_edit_and_tier(root: Path) -> None:
    bak = ok("explain", BAK).stdout
    assert (
        "edit:         no; wowlab leaves it alone (a restore writes it only if you name it "
        "with --paths)"
    ) in bak
    assert "tier:         A (read): reading it is ordinary addon-user behaviour" in bak
    lines = bak.splitlines()
    assert lines[-1].startswith("  file-map row: savedvariables-backup"), "the row id comes last"
    build = ok("explain", ".build.info").stdout
    assert "edit:         no; wowlab never writes it" in build
    cache = ok("explain", f"WTF/Account/{ACCOUNT}/config-cache.wtf").stdout
    assert (
        "edit:         yes, through wowlab's write gate only, with the client closed; the server "
        "may replace it at login (sync settings: wowlab doctor)"
    ) in cache
    fonts = ok("explain", f"{FLAVOR}/Fonts/FRIZQT__.TTF").stdout
    assert "; unsupported by Blizzard, a patch can reset it" in fonts
    assert "tier:         B: works, but Blizzard does not support it" in fonts
    folder = ok("explain", "WTF/").stdout
    assert "edit:         —; a folder, see the files in it" in folder


def test_notes_carry_the_caveats_in_json(root: Path) -> None:
    assert _json(cli.SvListReport, "sv", "list").notes == [cli._SV_TIMING]
    assert _json(cli.CVarListReport, "cvar", "list").notes == [cli._WHAT_THE_FILE_SAYS]
    assert _json(cli.CVarGetReport, "cvar", "get", "x").notes == [cli._WHAT_THE_FILE_SAYS]
    assert _json(cli.BindsReport, "binds", "list").notes == [cli._ACCOUNT_BINDINGS]
    none = _json(cli.BindsReport, "binds", "list", "--character", CHARACTER)
    assert none.notes == [cli._NO_CHARACTER_BINDINGS]
    assert _json(cli.MacrosReport, "macros", "list").notes == [cli._MACRO_SYNC]


def test_json_prompts_show_the_text_plan_on_stderr(root: Path, flavor: Path) -> None:
    """Security review (round 2): with --json and a prompt, the plan is on
    stderr before the question; stdout stays JSON."""
    a = _create("a")
    _flip_syndicator(flavor)
    result = run("snap", "restore", a.id, "--json", input="n\n")
    assert result.exit_code == 1
    plan_at = result.stderr.index(f"replace  {SYNDICATOR}")
    assert plan_at < result.stderr.index("Apply these changes?")
    assert not cli.RestoreReport.model_validate_json(
        result.stdout[result.stdout.index("{") :]
    ).applied
    quiet = run("snap", "restore", a.id, "--json", "--dry-run")
    assert "replace" not in quiet.stderr, "no prompt, no text plan"

    ok("snap", "restore", a.id, "--yes")
    undo = run("undo", "--json", input="n\n")
    assert undo.exit_code == 1
    assert undo.stderr.index(f"put back   {SYNDICATOR}") < undo.stderr.index("Undo it?")
    assert "put back" not in ok("undo", "--json", "--yes").stderr


def test_a_missing_version_reads_no_recorded_version_constructed(root: Path, flavor: Path) -> None:
    info = root / ".build.info"
    original = info.read_bytes()
    info.write_bytes(original.replace(b"wow_classic_beta", b"wow_constructed"))
    a = _create("a")
    assert a.flavor_version is None
    info.write_bytes(original)
    _flip_syndicator(flavor)
    b = _create("b")
    out = ok("snap", "restore", a.id, "--dry-run").stdout
    assert f"Taken on version no recorded version; {FLAVOR} is now on {VERSION}." in out
    diff = ok("snap", "diff", a.id, b.id).stdout
    assert f"({a.id} was taken on no recorded version; {b.id} on {VERSION})" in diff
    assert "None" not in out + diff
