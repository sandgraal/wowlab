"""The M11-05 customization recordings: each is the body its index row
describes, and each reads whole through `gamedata` (L4, L8).

The index row's SHA-256 ties a committed file to the body wago.tools served;
for the one stored gzip-compressed it is the only such tie.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import re
import shutil
from pathlib import Path
from typing import BinaryIO

import pytest

from wowlab_core.gamedata import GameData

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "wago"
INDEX = FIXTURES.parent / "README.md"
BUILD = "1.60.1.70009"
# Every table §13.2 names, all published for the build (checked 2026-09-28).
TABLES = {
    "ChrRaces": 58,
    "ChrClasses": 9,
    "ChrModel": 127,
    "ChrRaceXChrModel": 116,
    "ChrCustomizationOption": 1173,
    "ChrCustomizationChoice": 10447,
    "ChrCustomizationReq": 492,
    "ChrCustomizationReqChoice": 446,
    "ChrCustomizationElement": 39109,
    "ChrCustomizationCategory": 62,
    "ChrCustomizationConversion": 2815,
}


def _file(table: str) -> Path:
    plain = FIXTURES / f"{table}.{BUILD}.csv"
    return plain if plain.exists() else plain.with_name(plain.name + ".gz")


def _body(table: str) -> bytes:
    path = _file(table)
    raw = path.read_bytes()
    return gzip.decompress(raw) if path.suffix == ".gz" else raw


class _FixtureSource:
    name = "fixtures"

    def fetch_builds(self, dest: BinaryIO) -> str:  # pragma: no cover - not asked for
        raise AssertionError("the builds listing is not needed to read a cached table")

    def fetch_table(self, table: str, build: str, dest: BinaryIO) -> str:
        shutil.copyfileobj(io.BytesIO(_body(table)), dest)
        return f"fixture:{table}.{build}"


@pytest.mark.parser
@pytest.mark.parametrize("table", sorted(TABLES))
def test_recording_is_the_body_its_index_row_describes(table: str) -> None:
    rel = _file(table).relative_to(FIXTURES.parent).as_posix()
    (row,) = [
        line
        for line in INDEX.read_text(encoding="utf-8").splitlines()
        if line.startswith(f"| `{rel}`")
    ]
    assert f"https://wago.tools/db2/{table}/csv?build={BUILD}" in row
    (sha,) = re.findall(r"sha256 (?:of the decompressed body )?([0-9a-f]{64})", row)
    body = _body(table)
    assert hashlib.sha256(body).hexdigest() == sha
    assert f"{TABLES[table]} rows" in row


@pytest.mark.parser
@pytest.mark.parametrize("table", sorted(TABLES))
def test_recording_reads_whole_through_gamedata(table: str, tmp_path: Path) -> None:
    data = GameData(_FixtureSource(), cache_dir=tmp_path)
    rows = list(data.rows(table, BUILD))
    body = _body(table)
    header = body.split(b"\n", 1)[0].decode("utf-8").split(",")
    assert len(rows) == TABLES[table]
    assert all(list(row) == header for row in rows), "no column dropped or reordered"
    assert data.table_path(table, BUILD).read_bytes() == body


@pytest.mark.parser
def test_new_builds_listing_is_the_body_its_index_row_describes() -> None:
    name = "builds.2026-09-28.json.gz"
    (row,) = [
        line
        for line in INDEX.read_text(encoding="utf-8").splitlines()
        if line.startswith(f"| `wago/{name}`")
    ]
    (sha,) = re.findall(r"sha256 of the decompressed body ([0-9a-f]{64})", row)
    (size,) = re.findall(r"the body is (\d+) bytes", row)
    body = gzip.decompress((FIXTURES / name).read_bytes())
    assert hashlib.sha256(body).hexdigest() == sha
    assert len(body) == int(size)
    assert f'"version":"{BUILD}"' in body.decode("utf-8").replace(" ", "")
