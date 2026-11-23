"""`wowlab lookup` (M22-08): the command over `lookup`.

The install is the captured tree `fixtures/macos-70888/` copied into
`tmp_path` with two CONSTRUCTED changes, as labelled: its `.build.info`
version is moved to 1.60.1.71037, the build of the recorded game-table
subsets, and a constructed QuestieDB slice (`_questie_build.py`; no byte of
the third-party data is committed) is placed under its `Interface/AddOns/`.
wago.tools is replayed (ADR-0012): the recorded `ItemSparse` and
`AreaTable` subsets are served as if they were the downloads
(`constructed`). The user data directory is redirected into `tmp_path`;
nothing reads or writes a real install and no request leaves the process.
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
from _questie_build import FLAVOR, VERSION, place, slice_toc
from test_cli import (
    idle,  # noqa: F401  (autouse: no client in the process table)
    run,
    user_data,  # noqa: F401  (autouse: the user data directory in tmp_path)
)
from test_cli_player_copy import hits

from wowlab_core import cli, install
from wowlab_core.gamedata import GameData, WagoSource

FIXTURES = Path(__file__).resolve().parent / "fixtures"
LAUNCH = FIXTURES / "macos-70888"
WAGO = FIXTURES / "wago"


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "World of Warcraft"
    root.mkdir()
    info = (LAUNCH / ".build.info").read_bytes().replace(b"1.60.1.70888", VERSION.encode())
    (root / ".build.info").write_bytes(info)
    shutil.copytree(LAUNCH / "forever", root / FLAVOR)
    place(root / FLAVOR, slice_toc())
    monkeypatch.setenv(install.ENV_ROOT, str(root))
    monkeypatch.delenv(install.ENV_FLAVOR, raising=False)
    monkeypatch.chdir(tmp_path)
    return root


def _cached(user_data: Path, *tables: str) -> Path:
    folder = user_data / "gamedata" / "tables" / VERSION
    folder.mkdir(parents=True, exist_ok=True)
    for table in tables:
        shutil.copy2(WAGO / f"{table}.{VERSION}.subset.csv", folder / f"{table}.csv")
    return folder


@pytest.fixture
def requests(monkeypatch: pytest.MonkeyPatch, user_data: Path) -> list[httpx.Request]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        table = request.url.path.split("/")[2]
        name = f"{table}.{VERSION}.csv"
        return httpx.Response(
            200,
            headers={
                "content-type": "text/csv; charset=UTF-8",
                "content-disposition": f"attachment; filename=\"{name}\"; filename*=UTF-8''{name}",
            },
            content=(WAGO / f"{table}.{VERSION}.subset.csv").read_bytes(),
        )

    @contextmanager
    def fake() -> Iterator[GameData]:
        source = WagoSource(transport=httpx.MockTransport(handler), sleep=lambda _: None)
        try:
            yield GameData(source, cache_dir=user_data / "gamedata")
        finally:
            source.close()

    monkeypatch.setattr(cli, "_open_gamedata", fake)
    return seen


def _report(*args: str, code: int = 0) -> cli.LookupReport:
    result = run("lookup", *args, "--json")
    assert result.exit_code == code, (result.stdout, result.stderr, result.exception)
    return cli.LookupReport.model_validate_json(result.stdout)


def test_finds_with_both_sources_and_says_it_in_text_constructed(
    root: Path, user_data: Path, requests: list[httpx.Request]
) -> None:
    _cached(user_data, "ItemSparse", "AreaTable")
    result = run("lookup", "sharptalon")
    assert result.exit_code == 0, (result.stdout, result.stderr)
    assert result.stdout.splitlines()[:6] == [
        '3 matches for "sharptalon".',
        "",
        "NPC: Sharptalon (12676)",
        "  Level 31",
        "  Found in Ashenvale at 75.0, 70.1",
        "",
    ]
    assert "Item: Sharptalon's Claw (16305), item level 20, needs level 20" in result.stdout
    assert "  Drops from Sharptalon (level 31) in Ashenvale at 75.0, 70.1" in result.stdout
    assert "  Starts the quest Sharptalon's Claw (level 30, Ashenvale)" in result.stdout
    assert (
        "  Turned in to Senani Thunderheart (level 30, Horde) in Ashenvale at 73.8, 61.5"
        in result.stdout
    )
    assert result.stdout.rstrip().endswith("notes: --notes shows them")
    assert requests == []


def test_json_carries_the_detail_lines_and_states_constructed(
    root: Path, user_data: Path, requests: list[httpx.Request]
) -> None:
    _cached(user_data, "ItemSparse", "AreaTable")
    report = _report("water", "--kind", "item")
    assert (report.query, report.kinds, report.flavor, report.version) == (
        "water",
        ["item"],
        FLAVOR,
        VERSION,
    )
    (hit,) = report.hits
    assert hit.item is not None and [v.name for v in hit.item.vendors] == [
        "Innkeeper Farley",
        "Innkeeper Grosk",
    ]
    assert hit.lines == [
        "Reward of the quest A Rogue's Deal (level 5, Elwynn Forest)",
        "Sold by Innkeeper Grosk (Innkeeper, level 30, Horde) in Orgrimmar at 54.1, 68.4 "
        "(and 1 more spot); Durotar at 51.5, 41.6",
        "Sold by Innkeeper Farley (Innkeeper, level 30, Alliance) in Elwynn Forest at 43.8, 65.8",
    ]
    assert report.questiedb.state == "read"
    assert [(t.table, t.state) for t in report.game_tables] == [
        ("ItemSparse", "cached"),
        ("AreaTable", "cached"),
    ]
    assert report.words == '1 match for "water".'
    assert requests == []


def test_drop_chances_and_the_limit_constructed(root: Path, user_data: Path) -> None:
    _cached(user_data, "ItemSparse", "AreaTable")
    report = _report("linen")
    (hit,) = report.hits
    assert hit.lines[0] == (
        "Drops from Flesh Eater (level 24-25) in Duskwood at 25.1, 38.2 (and 1 more spot), "
        "12.5%, 10% chance"
    )
    report = _report("innkeeper", "--limit", "1")
    assert report.total == 2 and len(report.hits) == 1
    assert report.words == '2 matches for "innkeeper"; the best one is shown.'


def test_without_the_tables_it_says_how_to_get_them_constructed(
    root: Path, requests: list[httpx.Request]
) -> None:
    result = run("lookup", "sharptalon")
    assert result.exit_code == 0
    assert "Found in zone 331 at 75.0, 70.1" in result.stdout
    assert (
        "The item and zone tables are not downloaded for this game version, so zones show as "
        "numbers: --fetch downloads them from wago.tools."
    ) in " ".join(result.stdout.split())
    assert requests == []


def test_fetch_downloads_only_the_missing_tables_once_constructed(
    root: Path, user_data: Path, requests: list[httpx.Request]
) -> None:
    folder = _cached(user_data, "AreaTable")
    before = (folder / "AreaTable.csv").stat().st_mtime_ns
    report = _report("guild tabard", "--fetch")
    assert [r.url.path for r in requests] == ["/db2/ItemSparse/csv"]
    assert dict(requests[0].url.params) == {"build": VERSION}
    assert [(t.table, t.state) for t in report.game_tables] == [
        ("ItemSparse", "fetched"),
        ("AreaTable", "cached"),
    ]
    assert (folder / "AreaTable.csv").stat().st_mtime_ns == before
    (hit,) = report.hits
    assert (hit.id, hit.sources) == (5976, ["game_table"])
    assert hit.lines == ["Only the game's item table knows it; QuestieDB has no entry for it."]
    report = _report("guild tabard", "--fetch")
    assert len(requests) == 1  # nothing fetched the second time
    assert {t.state for t in report.game_tables} == {"cached"}


def test_no_match_and_nothing_to_search_constructed(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = _report("zzz-no-such-thing")
    assert (report.total, report.hits, report.words) == (0, [], 'No match for "zzz-no-such-thing".')
    shutil.rmtree(root / FLAVOR / "Interface" / "AddOns" / "QuestieDB")
    report = _report("sharptalon", code=1)
    assert report.questiedb.state == "not_installed"
    assert report.words.startswith('Nothing to search for "sharptalon"')
    report = _report("   ", code=1)
    assert report.words == "Nothing to look up: give a name, part of one, or an id."


def test_a_query_that_looks_like_an_option_is_text_constructed(root: Path, user_data: Path) -> None:
    _cached(user_data, "ItemSparse", "AreaTable")
    result = run("lookup", "--json", "--", "-claw")
    assert result.exit_code == 0, (result.stdout, result.stderr)
    report = cli.LookupReport.model_validate_json(result.stdout)
    assert report.query == "-claw" and report.total == 0


def test_notes_list_what_was_left_out_constructed(root: Path, user_data: Path) -> None:
    _cached(user_data, "ItemSparse", "AreaTable")
    result = run("lookup", "claw", "--notes")
    text = " ".join(result.stdout.split())
    assert "Not read: corrections the addon applies in game for each character" in text
    assert "Not read: names in other languages" in text
    assert "itemDropCorrections.lua, because it is Lua code" in text
    assert "Drop chances are shown only where QuestieDB's drop tables have one" in text


def test_help_and_text_are_player_copy(root: Path, user_data: Path) -> None:
    _cached(user_data, "ItemSparse", "AreaTable")
    help_text = run("lookup", "--help").stdout
    assert "Where an item drops or which quest gives it" in " ".join(help_text.split())
    assert hits(help_text) == []
    assert hits(run("lookup", "claw", "--notes").stdout) == []
