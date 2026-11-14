"""`wowlab names fetch` (M18-02), and where `doctor` and `char list
--summary` name it.

The install is the captured tree `fixtures/macos-70058/` (version
1.60.1.70058) copied into `tmp_path` with its flavor folder named as its
provenance row says (`_classic_beta_`). wago.tools is replayed from
`fixtures/wago/` (ADR-0012): the listing of 2026-09-30, which lists that
version, and the recordings of `ChrClasses` and `ChrRaces` for it. The
other two name tables exist only as committed subsets, so a test that needs
them cached places them in the cache itself (`constructed`, as labelled).
Also constructed, as labelled: a second flavor (the launch capture
`fixtures/macos-70888/` beside it, its `.build.info` row added), a cached
table with other bytes, a 400 for a table (the recorded 400 body of
another table) and a 503. The user data directory is redirected into
`tmp_path`; nothing reads or writes a real install and no request leaves
the process.
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

import gzip
import json
import shutil
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
from test_cli import (
    idle,  # noqa: F401  (autouse: no client in the process table)
    run,
    user_data,  # noqa: F401  (autouse: the user data directory in tmp_path)
)

from wowlab_core import cli, gamedata, install
from wowlab_core.gamedata import GameData, WagoSource

FIXTURES = Path(__file__).resolve().parent / "fixtures"
EARLY = FIXTURES / "macos-70058"
LAUNCH = FIXTURES / "macos-70888"
CLASSIC = FIXTURES / "macos"
WAGO = FIXTURES / "wago"
FLAVOR = "_classic_beta_"  # the provenance rows' flavor folders; tests may name them (L6)
FOREVER = "_forever_"
VERSION = "1.60.1.70058"
LAUNCH_VERSION = "1.60.1.70888"  # not in the 2026-09-30 listing
CLASSIC_VERSION = "1.60.1.69913"
RECORDED = ("ChrClasses", "ChrRaces")  # recorded whole for VERSION
SUBSETS = ("ChrSpecialization", "SkillLine")  # committed only as subsets
ALL = ("ChrRaces", "ChrClasses", "ChrSpecialization", "SkillLine")
COMMAND = "wowlab names fetch"

Handler = Callable[[httpx.Request], httpx.Response]


def _table_response(table: str, build: str) -> httpx.Response:
    name = f"{table}.{build}.csv"
    return httpx.Response(
        200,
        headers={
            "content-type": "text/csv; charset=UTF-8",
            "content-disposition": f"attachment; filename=\"{name}\"; filename*=UTF-8''{name}",
        },
        content=(WAGO / name).read_bytes(),
    )


def _recorded(request: httpx.Request) -> httpx.Response:
    """wago.tools as the recordings show it (fixture index rows)."""
    if request.url.path == "/api/builds":
        body = gzip.decompress((WAGO / "builds.2026-09-30.json.gz").read_bytes())
        return httpx.Response(200, headers={"content-type": "application/json"}, content=body)
    build = request.url.params.get("build")
    for table in RECORDED:
        if request.url.path == f"/db2/{table}/csv" and build == VERSION:
            return _table_response(table, VERSION)
    return httpx.Response(
        404,
        headers={"content-type": "text/html; charset=utf-8"},
        content=(WAGO / "ChrClasses.1.60.1.1.404.html").read_bytes(),
    )


@pytest.fixture
def requests(monkeypatch: pytest.MonkeyPatch, user_data: Path) -> list[httpx.Request]:
    return _replay(monkeypatch, user_data, _recorded)


def _replay(
    monkeypatch: pytest.MonkeyPatch, user_data: Path, answer: Handler
) -> list[httpx.Request]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return answer(request)

    @contextmanager
    def fake() -> Iterator[GameData]:
        source = WagoSource(transport=httpx.MockTransport(handler), sleep=lambda _: None)
        try:
            yield GameData(source, cache_dir=user_data / "gamedata")
        finally:
            source.close()

    monkeypatch.setattr(cli, "_open_gamedata", fake)
    return seen


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "World of Warcraft"
    root.mkdir()
    shutil.copy2(EARLY / ".build.info", root / ".build.info")
    shutil.copytree(EARLY / "forever", root / FLAVOR)
    monkeypatch.setenv(install.ENV_ROOT, str(root))
    monkeypatch.delenv(install.ENV_FLAVOR, raising=False)
    monkeypatch.chdir(tmp_path)
    return root


@pytest.fixture
def two(root: Path) -> Path:
    """Constructed: the launch capture beside the early one."""
    head, *rows = (EARLY / ".build.info").read_bytes().splitlines(keepends=True)
    launch_head, launch_row = (LAUNCH / ".build.info").read_bytes().splitlines(keepends=True)
    assert launch_head == head
    (root / ".build.info").write_bytes(b"".join([head, *rows, launch_row]))
    shutil.copytree(LAUNCH / "forever", root / FOREVER)
    return root


def _cache(user_data: Path, build: str = VERSION) -> Path:
    folder = user_data / "gamedata" / "tables" / build
    assert gamedata.default_cache_dir() == user_data / "gamedata"
    return folder


def _place_subsets(user_data: Path) -> Path:
    """Constructed: the two subset recordings placed in the cache."""
    folder = _cache(user_data)
    folder.mkdir(parents=True, exist_ok=True)
    for table in SUBSETS:
        shutil.copy2(WAGO / f"{table}.{VERSION}.subset.csv", folder / f"{table}.csv")
    return folder


def _place_all(user_data: Path) -> None:
    """Constructed: all four name tables placed in the cache."""
    folder = _place_subsets(user_data)
    for table in RECORDED:
        shutil.copy2(WAGO / f"{table}.{VERSION}.csv", folder / f"{table}.csv")


def _fetch(*args: str) -> tuple[int, cli.NamesFetchReport]:
    result = run("names", "fetch", "--json", *args)
    assert result.exit_code in (0, 1), (result.stdout, result.stderr, result.exception)
    return result.exit_code, cli.NamesFetchReport.model_validate_json(result.stdout)


def _statuses(report: cli.NamesFetchReport, folder: str = FLAVOR) -> dict[str, str]:
    (row,) = [f for f in report.flavors if f.folder == folder]
    return {t.table: t.status for t in row.tables}


def _tables_asked(requests: list[httpx.Request]) -> list[str]:
    return [r.url.path.split("/")[2] for r in requests if r.url.path.startswith("/db2/")]


# ─── names fetch ─────────────────────────────────────────────────────────────


def test_fetches_only_the_missing_tables_one_request_each_constructed(
    root: Path, user_data: Path, requests: list[httpx.Request]
) -> None:
    folder = _place_subsets(user_data)
    before = {t: (folder / f"{t}.csv").stat().st_mtime_ns for t in SUBSETS}
    code, report = _fetch()
    assert code == 0
    assert _statuses(report) == {
        "ChrRaces": "fetched",
        "ChrClasses": "fetched",
        "ChrSpecialization": "cached",
        "SkillLine": "cached",
    }
    assert report.fetched == 2
    # One request per missing table, the full build string its only parameter.
    assert sorted(_tables_asked(requests)) == sorted(RECORDED)
    assert len(requests) == 2
    for r in requests:
        assert dict(r.url.params) == {"build": VERSION}
    for table in RECORDED:
        cached = folder / f"{table}.csv"
        assert cached.read_bytes() == (WAGO / f"{table}.{VERSION}.csv").read_bytes()
    for table in SUBSETS:
        assert (folder / f"{table}.csv").stat().st_mtime_ns == before[table]
        assert (folder / f"{table}.csv").read_bytes() == (
            WAGO / f"{table}.{VERSION}.subset.csv"
        ).read_bytes()


def test_a_second_run_makes_no_request_constructed(
    root: Path, user_data: Path, requests: list[httpx.Request]
) -> None:
    _place_subsets(user_data)
    assert _fetch()[0] == 0
    requests.clear()
    code, report = _fetch()
    assert code == 0 and report.fetched == 0
    assert set(_statuses(report).values()) == {"cached"}
    assert requests == []
    text = run("names", "fetch")
    assert text.exit_code == 0
    assert text.stdout == f"Names for {FLAVOR} ({VERSION}): already downloaded; nothing fetched.\n"
    assert requests == []


def test_a_cached_table_is_never_replaced_constructed(
    root: Path, user_data: Path, requests: list[httpx.Request]
) -> None:
    """L5: a file under the table's name is left as it is, whatever it holds."""
    folder = _place_subsets(user_data)
    other = b"ID,Name_lang\n1,constructed\n"
    (folder / "ChrClasses.csv").write_bytes(other)
    code, report = _fetch()
    assert code == 0
    assert _statuses(report)["ChrClasses"] == "cached"
    assert (folder / "ChrClasses.csv").read_bytes() == other
    assert _tables_asked(requests) == ["ChrRaces"]


def test_text_says_what_was_downloaded_in_the_players_words_constructed(
    root: Path, user_data: Path, requests: list[httpx.Request]
) -> None:
    _place_subsets(user_data)
    result = run("names", "fetch")
    assert result.exit_code == 0, result.stderr
    assert " ".join(result.stdout.split()) == (
        f"Names for {FLAVOR} ({VERSION}): downloaded the names of races and classes; "
        "the names of specs and professions were already here."
    )


def test_a_version_the_source_does_not_list_is_asked_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, requests: list[httpx.Request]
) -> None:
    """The launch capture `fixtures/macos-70888/` (1.60.1.70888, which the
    2026-09-30 listing does not hold): one table request and the listing,
    and no request for the other three tables."""
    root = tmp_path / "launch"
    root.mkdir()
    shutil.copy2(LAUNCH / ".build.info", root / ".build.info")
    shutil.copytree(LAUNCH / "forever", root / FOREVER)
    monkeypatch.setenv(install.ENV_ROOT, str(root))
    monkeypatch.delenv(install.ENV_FLAVOR, raising=False)
    code, report = _fetch()
    assert code == 1
    assert set(_statuses(report, FOREVER).values()) == {"not_published"}
    assert len(_tables_asked(requests)) == 1
    assert [r.url.path for r in requests].count("/api/builds") == 1
    assert report.fetched == 0
    requests.clear()
    result = run("names", "fetch")
    assert result.exit_code == 1
    flat = " ".join(result.stdout.split())
    assert flat.startswith(
        f"Names for {FOREVER} ({LAUNCH_VERSION}): the names of races, classes, specs and "
        "professions could not be downloaded."
    )
    assert "is not published by the game data source" in flat
    assert flat.count("not published") == 1  # one reason, said once


def test_a_table_the_source_lacks_for_a_listed_version_is_not_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, requests: list[httpx.Request]
) -> None:
    """The captured tree of `fixtures/macos/` (1.60.1.69913, listed, with no
    table recorded for it: each answers the recorded 404)."""
    root = tmp_path / "classic"
    root.mkdir()
    shutil.copy2(CLASSIC / ".build.info", root / ".build.info")
    shutil.copytree(CLASSIC / "forever", root / FLAVOR)
    monkeypatch.setenv(install.ENV_ROOT, str(root))
    monkeypatch.delenv(install.ENV_FLAVOR, raising=False)
    code, report = _fetch()
    assert code == 1
    assert set(_statuses(report).values()) == {"not_published"}
    assert _tables_asked(requests) == list(ALL)
    cached = gamedata.default_cache_dir() / "tables" / CLASSIC_VERSION
    assert list(cached.glob("*.csv")) == []  # nothing cached


def test_a_table_the_build_lacks_does_not_stop_the_others_constructed(
    root: Path, user_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/db2/ChrRaces/csv":
            return httpx.Response(
                400,
                headers={"content-type": "application/json"},
                content=(WAGO / f"TraitSubTree.{VERSION}.400.json").read_bytes(),
            )
        return _recorded(request)

    seen = _replay(monkeypatch, user_data, answer)
    _place_subsets(user_data)
    code, report = _fetch()
    assert code == 1
    assert _statuses(report)["ChrRaces"] == "not_published"
    assert _statuses(report)["ChrClasses"] == "fetched"
    assert not (_cache(user_data) / "ChrRaces.csv").exists()
    assert sorted(_tables_asked(seen)) == sorted(RECORDED)


def test_an_unreachable_source_stops_every_later_download_constructed(
    two: Path, user_data: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = _replay(
        monkeypatch,
        user_data,
        lambda _r: httpx.Response(503, headers={"content-type": "text/plain"}, content=b""),
    )
    code, report = _fetch()
    assert code == 1
    statuses = [t.status for f in report.flavors for t in f.tables]
    assert statuses[0] == "error"
    assert set(statuses[1:]) == {"not_tried"}
    # One table, retried by the client's own policy; nothing after it.
    assert set(_tables_asked(seen)) == {ALL[0]}
    assert not (user_data / "gamedata" / "tables" / VERSION / f"{ALL[0]}.csv").exists()


def test_each_flavor_is_fetched_for_its_own_version_constructed(
    two: Path, user_data: Path, requests: list[httpx.Request]
) -> None:
    _place_subsets(user_data)
    code, report = _fetch()
    assert code == 1  # the launch version is not in the 2026-09-30 listing
    assert [(f.folder, f.version) for f in report.flavors] == [
        (FLAVOR, VERSION),
        (FOREVER, LAUNCH_VERSION),
    ]
    assert set(_statuses(report, FLAVOR).values()) <= {"fetched", "cached"}
    assert set(_statuses(report, FOREVER).values()) == {"not_published"}
    assert {r.url.params.get("build") for r in requests if r.url.path.startswith("/db2/")} == {
        VERSION,
        LAUNCH_VERSION,
    }


def test_flavor_option_limits_it_to_one_constructed(
    two: Path, user_data: Path, requests: list[httpx.Request]
) -> None:
    _place_subsets(user_data)
    code, report = _fetch("--flavor", FLAVOR)
    assert code == 0
    assert [f.folder for f in report.flavors] == [FLAVOR]
    assert {r.url.params.get("build") for r in requests if r.url.path.startswith("/db2/")} == {
        VERSION
    }


def test_help_names_the_json_model() -> None:
    assert "NamesFetchReport" in (cli.names_fetch.__doc__ or "")


# ─── doctor and char list --summary name it ──────────────────────────────────


def _doctor() -> cli.DoctorReport:
    result = run("doctor", "--json")
    assert result.exit_code in (0, 1), (result.stdout, result.stderr, result.exception)
    return cli.DoctorReport.model_validate_json(result.stdout)


def test_doctor_names_the_command_when_a_table_is_missing(
    root: Path, user_data: Path, requests: list[httpx.Request]
) -> None:
    (f,) = _doctor().flavors
    assert f.names_missing == list(ALL)
    assert f.names_command == COMMAND
    text = " ".join(run("doctor").stdout.split())
    assert (
        "Names: wowlab shows ids for races, classes, specs and professions until you "
        f"download their names once: run `{COMMAND}`."
    ) in text
    assert requests == []  # doctor reads the cache only


def test_doctor_says_nothing_about_names_once_they_are_here_constructed(
    root: Path, user_data: Path, requests: list[httpx.Request]
) -> None:
    _place_all(user_data)
    (f,) = _doctor().flavors
    assert f.names_missing == [] and f.names_command is None
    assert "Names:" not in run("doctor").stdout
    assert requests == []


def test_doctor_carries_the_callers_root(
    root: Path, user_data: Path, requests: list[httpx.Request]
) -> None:
    result = run("doctor", "--json", "--root", str(root))
    report = cli.DoctorReport.model_validate_json(result.stdout)
    (f,) = report.flavors
    assert f.names_command is not None and str(root) in f.names_command
    assert f.names_command.startswith(COMMAND + " --root ")


def _summary(*args: str) -> tuple[cli.CharSummaryReport, str]:
    done = run("char", "list", "--summary", "--json", *args)
    text = run("char", "list", "--summary", *args)
    return cli.CharSummaryReport.model_validate_json(done.stdout), text.stdout


def test_summary_names_the_command_when_names_are_missing(
    root: Path, user_data: Path, requests: list[httpx.Request]
) -> None:
    report, text = _summary()
    assert [(n.flavor, n.version, n.tables, n.command) for n in report.names_missing] == [
        (FLAVOR, VERSION, list(ALL), COMMAND)
    ]
    assert (
        "Names: wowlab shows ids for races, classes, specs and professions until you "
        f"download their names once: run `{COMMAND}`."
    ) in " ".join(text.split())
    assert requests == []


def test_summary_names_only_what_is_missing_constructed(
    root: Path, user_data: Path, requests: list[httpx.Request]
) -> None:
    _place_subsets(user_data)
    report, text = _summary()
    assert [n.tables for n in report.names_missing] == [["ChrRaces", "ChrClasses"]]
    assert "shows ids for races and classes until" in " ".join(text.split())
    _place_all(user_data)
    report, text = _summary()
    assert report.names_missing == []
    assert "Names:" not in text
    assert json.loads(run("char", "list", "--summary", "--json").stdout)["names_missing"] == []
    assert requests == []


@pytest.mark.parametrize(
    ("cell", "words"),
    [(b"", "has no version in it"), (b"1.60.1", "not a full build string")],
    ids=["no_version_constructed", "short_version_constructed"],
)
def test_a_version_it_cannot_use_is_said_and_nothing_is_asked(
    root: Path, requests: list[httpx.Request], cell: bytes, words: str
) -> None:
    """Constructed: the version cell of `.build.info` emptied or cut short."""
    info = root / ".build.info"
    data = info.read_bytes()
    assert data.count(VERSION.encode()) == 1
    info.write_bytes(data.replace(VERSION.encode(), cell))
    code, report = _fetch()
    assert code == 1
    (row,) = report.flavors
    assert row.tables == [] and row.detail is not None and words in row.detail
    assert requests == []
    result = run("names", "fetch")
    assert result.exit_code == 1
    flat = " ".join(result.stdout.split())
    assert flat.startswith(f"Names for {FLAVOR}") and "not downloaded, because" in flat
    assert words in flat
