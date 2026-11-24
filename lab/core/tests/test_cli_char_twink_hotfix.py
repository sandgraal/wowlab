"""`wowlab char twink` with the game's hotfix cache (M22-23), through Typer's
runner, on the `test_cli_char_twink.py` setup: the `macos-70888` capture
copied into `tmp_path`, the recorded 70888 tables served by a stand-in
source, the user folders redirected, and the copy checked unchanged after
every test (L1).

The hotfix cache is constructed (L8, labelled): an `XFTH` file written into
the copy's `Cache/ADB/enUS/` holding the `Item` and `ItemSparse` rows of
Aboriginal Sash (14113), the character's worn belt, as the derived fixture
recorded them from the owner's real cache (`macos-71037/derived/
hotfix-items.json`; values, never the file's bytes), under the build the
test names.
"""

from __future__ import annotations

import json
import shutil
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from wowlab_core import hotfix, install, twink

sys.path.insert(0, str(Path(__file__).resolve().parent / "parser"))

from _hotfix_build import Rec, cache, encode_row
from test_cli_char_twink import (  # noqa: F401 - the fixtures are used by name
    FLAVOR,
    _state,
    report,
    root,
    run,
    served,
    slot,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FACTS: dict[str, Any] = json.loads(
    (FIXTURES / "macos-71037" / "derived" / "hotfix-items.json").read_text(encoding="utf-8")
)
SASH = 14113


def _write_cache(top: Path, build: int) -> Path:
    [entry] = [i for i in FACTS["items"] if i["id"] == SASH]
    layouts = hotfix.layouts()
    records = [
        Rec(name, SASH, encode_row(layouts[name], entry[name]["row"]), push_id=100)
        for name in twink.HOTFIX_TABLES
    ]
    folder = top / FLAVOR / "Cache" / "ADB" / "enUS"
    folder.mkdir(parents=True)
    path = folder / hotfix.CACHE_FILE
    path.write_bytes(cache(build, records))
    return path


@pytest.fixture
def cached(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    """The copy with a constructed cache of the build the test names, made
    before the copy's state is taken: the closing check holds the cache to
    what was written, as every other file (L1)."""
    top = tmp_path / "World of Warcraft"
    top.mkdir()
    shutil.copy2(FIXTURES / "macos-70888" / ".build.info", top / ".build.info")
    shutil.copytree(FIXTURES / "macos-70888" / "forever", top / FLAVOR)
    path = _write_cache(top, request.param)
    monkeypatch.setenv(install.ENV_ROOT, str(top))
    monkeypatch.delenv("WOWLAB_FLAVOR", raising=False)
    monkeypatch.chdir(tmp_path)
    before = _state(top)
    yield path
    assert _state(top) == before, "the install copy changed"


@pytest.mark.usefixtures("served", "cached")
@pytest.mark.parametrize("cached", [70888], indirect=True)
def test_a_worn_item_the_tables_lack_is_scored_from_the_cache_constructed() -> None:
    r = report("--bracket", "29")
    waist = slot(r, 6).worn
    assert waist is not None and waist.item_id == SASH
    assert waist.score is not None and waist.score > 0 and waist.unscored is None
    assert waist.stats_from == "hotfix cache"
    assert waist.required_level == 10
    cache_report = r.twink.hotfix_cache
    assert cache_report is not None and cache_report.applied
    assert cache_report.file == "Cache/ADB/enUS/DBCache.bin" and cache_report.cache_build == 70888
    counts = {t.table: t for t in cache_report.tables}
    assert counts["ItemSparse"].added == 1 and counts["Item"].replaced == 1
    notes = " ".join(r.twink.notes)
    assert "Stats for 1 weapon or armour piece were read from your game's hotfix cache" in notes
    # Every other worn item still comes from the downloaded tables.
    others = [s.worn for s in r.twink.slots if s.worn and s.worn.item_id != SASH]
    assert others and all(w.stats_from in ("game tables", None) for w in others)
    text = run("--bracket", "29").stdout
    assert "Aboriginal Sash" in text and "stats from the hotfix cache" in text


@pytest.mark.usefixtures("served", "cached")
@pytest.mark.parametrize("cached", [71037], indirect=True)
def test_a_cache_of_another_build_is_not_used_and_says_why_constructed() -> None:
    r = report("--bracket", "29", "--notes")
    waist = slot(r, 6).worn
    assert waist is not None and waist.score is None
    assert waist.unscored == "a hotfix the copy of the game tables wowlab downloads lacks"
    cache_report = r.twink.hotfix_cache
    assert cache_report is not None and not cache_report.applied
    assert cache_report.cache_build == 71037
    assert cache_report.refused == (
        "the hotfix cache is of build 71037 and the game tables of 1.60.1.70888; wowlab lays "
        "a hotfix cache only over the game tables of its own build"
    )
    notes = " ".join(" ".join(r.twink.notes).split())
    assert "wowlab did not read your game's hotfix cache" in notes
    assert "the hotfix cache is of build 71037" in notes


@pytest.mark.usefixtures("served", "root")
def test_no_cache_says_so_in_json() -> None:
    r = report("--bracket", "29")
    cache_report = r.twink.hotfix_cache
    assert cache_report is not None and not cache_report.applied
    assert cache_report.refused == "this game folder has no hotfix cache"
    assert cache_report.file is None
