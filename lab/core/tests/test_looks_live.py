"""Manual check against the real wago.tools. Never run in CI (ADR-0012).

    uv run pytest -m live lab/core/tests/test_looks_live.py

Ten table requests through the library, serialised. wago.tools is
community-run; do not loop this. A mismatch means the export for this build
changed after it was recorded: record new files (never edit the committed
ones) and add a breakage-log line in `docs/DATA_SOURCES.md`.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from wowlab_core.gamedata import GameData, WagoSource
from wowlab_core.looks import REQUIRED_TABLES, Customizations

INDEX = Path(__file__).resolve().parent / "fixtures" / "README.md"
BUILD = "1.60.1.70009"
RECORDED = (*REQUIRED_TABLES, "ChrCustomizationElement", "ChrCustomizationConversion")


def _recorded_sha(table: str) -> str:
    for line in INDEX.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"| `wago/{table}.{BUILD}.csv"):
            (sha,) = re.findall(r"sha256 (?:of the decompressed body )?([0-9a-f]{64})", line)
            return sha
    raise AssertionError(f"no index row for {table}")


@pytest.mark.live
def test_live_tables_still_match_the_recordings(tmp_path: Path) -> None:
    with WagoSource(max_attempts=2) as source:
        data = GameData(source, cache_dir=tmp_path / "gamedata")
        for table in RECORDED:
            body = data.table(table, BUILD).read_bytes()
            assert hashlib.sha256(body).hexdigest() == _recorded_sha(table), table
        model = Customizations.from_gamedata(data, BUILD)
    assert model.playable_races()
