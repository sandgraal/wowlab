"""The wago.tools recordings parse, whole, with nothing dropped (L4, L8).

Two external formats enter `gamedata`: the builds listing (JSON) and a table
export (CSV). Both are graded here against the real recordings, read straight
from the fixture bytes with no HTTP layer in between.
"""

from __future__ import annotations

import gzip
import json
import shutil
from pathlib import Path
from typing import BinaryIO

import pytest

from wowlab_core.gamedata import GameData

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "wago"
TABLE = "ChrClasses"
BUILD = "1.60.1.69876"


class _FixtureSource:
    """A `Source` that hands over the recorded bytes. Also proves the protocol
    is enough for a non-HTTP source (LAB_PLAN §6.6)."""

    name = "fixtures"

    def fetch_builds(self, dest: BinaryIO) -> str:
        dest.write(gzip.decompress((FIXTURES / "builds.json.gz").read_bytes()))
        return "fixture:builds.json.gz"

    def fetch_table(self, table: str, build: str, dest: BinaryIO) -> str:
        with (FIXTURES / f"{table}.{build}.csv").open("rb") as handle:
            shutil.copyfileobj(handle, dest)
        return f"fixture:{table}.{build}.csv"


@pytest.mark.parser
def test_builds_listing_keeps_every_product_build_and_field(tmp_path: Path) -> None:
    expected = json.loads(gzip.decompress((FIXTURES / "builds.json.gz").read_bytes()))
    listing = GameData(_FixtureSource(), cache_dir=tmp_path / "cache").builds()

    assert list(listing) == list(expected), "product order is the source's"
    for product, builds in expected.items():
        got = [b.model_dump() for b in listing[product]]
        assert got == builds, f"{product}: a build or a field was dropped or reordered"


@pytest.mark.parser
def test_builds_listing_has_versions_shared_between_products(tmp_path: Path) -> None:
    listing = GameData(_FixtureSource(), cache_dir=tmp_path / "cache").builds()
    versions = [b.version for builds in listing.values() for b in builds]
    assert len(versions) > len(set(versions)), (
        "the recording shows one version under several products; "
        "resolve_build must not assume a version belongs to one product"
    )


@pytest.mark.parser
def test_table_csv_rows_match_the_recorded_bytes(tmp_path: Path) -> None:
    data = GameData(_FixtureSource(), cache_dir=tmp_path / "cache")
    raw = (FIXTURES / f"{TABLE}.{BUILD}.csv").read_bytes()
    assert data.table(TABLE, BUILD).read_bytes() == raw, "the cache holds the bytes as served"

    rows = list(data.rows(TABLE, BUILD))
    header = raw.split(b"\n", 1)[0].decode("utf-8").split(",")
    assert len(rows) == 9
    assert all(list(row) == header for row in rows)
    assert all(isinstance(value, str) for row in rows for value in row.values())

    warrior = rows[0]
    assert warrior["Name_lang"] == "Warrior"
    assert warrior["Description_lang"].startswith("Warriors train constantly")
    assert "," in warrior["Description_lang"], "a quoted field with commas stays one field"
    assert warrior["Name_female_lang"] == "", "an empty field is an empty string, not None"
