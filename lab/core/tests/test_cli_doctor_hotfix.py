# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
"""`doctor`'s hotfix-cache line (M22-23): the cache's build and the rows it
adds and replaces, or why it is not used, from wowlab's cached tables only.

Every input is `constructed` (L8): the captured `.build.info` and
`.flavor.info` (`fixtures/macos/`, README row) in an install under
`tmp_path`, an `XFTH` cache written by the test into its flavor folder, and
the game tables written into the redirected user data folder. No client in
the process table, game data replayed (`test_cli`'s autouse fixtures).
Nothing reads or writes a real install.
"""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
import sys
from pathlib import Path

import pytest
from test_cli import (
    CAPTURE,
    FLAVOR,
    VERSION,
    _plain,
    idle,  # noqa: F401  (autouse: no client in the process table)
    replayed,  # noqa: F401  (autouse: recorded game data only)
    run,
    user_data,  # noqa: F401  (autouse: the store lives in tmp_path)
)

from wowlab_core import hotfix, install

sys.path.insert(0, str(Path(__file__).resolve().parent / "parser"))

from _hotfix_build import Rec, blank_row, cache, encode_row

NUMBER = int(VERSION.rsplit(".", 1)[1])
ITEM = hotfix.layouts()["Item"]
SPARSE = hotfix.layouts()["ItemSparse"]


def _install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, build: int | None) -> Path:
    root = tmp_path / "World of Warcraft"
    flavor = root / FLAVOR
    flavor.mkdir(parents=True)
    shutil.copy2(CAPTURE / ".build.info", root / ".build.info")
    shutil.copy2(CAPTURE / "forever" / ".flavor.info", flavor / ".flavor.info")
    if build is not None:
        folder = flavor / "Cache" / "ADB" / "enUS"
        folder.mkdir(parents=True)
        sparse = encode_row(SPARSE, blank_row(SPARSE, Display_lang="Constructed"))
        (folder / hotfix.CACHE_FILE).write_bytes(
            cache(
                build,
                [
                    Rec("ItemSparse", 9, sparse),
                    Rec("ItemSparse", 1, sparse),
                    Rec("Item", 2, status=3),
                ],
            )
        )
    monkeypatch.setenv(install.ENV_ROOT, str(root))
    monkeypatch.chdir(tmp_path)
    return root


def _tables(data: Path) -> None:
    folder = data / "gamedata" / "tables" / VERSION
    folder.mkdir(parents=True)
    for layout, ids in ((ITEM, (1, 2)), (SPARSE, (1,))):
        with (folder / f"{layout.table}.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(layout.columns)
            for n in ids:
                row = {"ID": str(n), **blank_row(layout)}
                writer.writerow([row[c] for c in layout.columns])


def _state(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _flavor_json(*args: str) -> dict[str, object]:
    result = run("doctor", "--offline", "--json", *args)
    assert result.exit_code in (0, 1), (result.stdout, result.exception)
    [flavor] = json.loads(result.stdout)["flavors"]
    assert isinstance(flavor, dict)
    return flavor


def _text() -> str:
    result = run("doctor", "--offline")
    assert result.exit_code in (0, 1), (result.stdout, result.exception)
    return " ".join(_plain(result.stdout).split())


def test_the_line_names_the_build_and_the_rows_added_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, user_data: Path
) -> None:
    root = _install(tmp_path, monkeypatch, NUMBER)
    _tables(user_data)
    before = _state(root)
    report = _flavor_json()["hotfix_cache"]
    assert isinstance(report, dict)
    assert report["applied"] is True and report["cache_build"] == NUMBER
    assert report["file"] == "Cache/ADB/enUS/DBCache.bin"
    counts = {t["table"]: t for t in report["tables"]}
    assert (counts["ItemSparse"]["added"], counts["ItemSparse"]["replaced"]) == (1, 1)
    assert counts["Item"]["removed"] == 1
    assert (
        f"Hotfix cache: build {NUMBER}; adds 1 row the downloaded game tables lack, replaces "
        "1 and removes 1 (Item and ItemSparse), read from Cache/ADB/enUS/DBCache.bin as your "
        "game last saved it"
    ) in _text()
    assert _state(root) == before


def test_tables_not_cached_and_another_build_say_why_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, user_data: Path
) -> None:
    _install(tmp_path, monkeypatch, NUMBER)
    assert (
        f"Hotfix cache: build {NUMBER}; not used: the game table Item of {VERSION} is not in "
        "wowlab's cache"
    ) in _text()
    _tables(user_data)
    shutil.rmtree(tmp_path / "World of Warcraft" / FLAVOR / "Cache")
    _install_cache = tmp_path / "World of Warcraft" / FLAVOR / "Cache" / "ADB" / "enUS"
    _install_cache.mkdir(parents=True)
    (_install_cache / hotfix.CACHE_FILE).write_bytes(cache(NUMBER + 1, []))
    text = _text()
    assert f"Hotfix cache: build {NUMBER + 1}; not used: the hotfix cache is of build" in text


def test_no_cache_no_line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install(tmp_path, monkeypatch, None)
    assert _flavor_json()["hotfix_cache"] is None
    assert "Hotfix cache" not in _text()
