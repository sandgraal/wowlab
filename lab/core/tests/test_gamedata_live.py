"""Manual check against the real wago.tools. Never runs in CI (ADR-0012).

    uv run pytest -m live lab/core/tests/test_gamedata_live.py

It makes two requests, one after the other: the builds listing and one small
table for the recorded build. wago.tools is community-run; do not loop this.

To refresh the recordings, set `WOWLAB_WAGO_RECORD_DIR` to a directory under
`lab/core/tests/fixtures/incoming/` (gitignored). The cache stores response
bodies exactly as served, so the files copied out are verbatim recordings,
not something the parser produced. Review them, then add them as *new*
fixture files with index rows; a committed fixture is never edited.
"""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path

import pytest

from wowlab_core.gamedata import GameData, WagoSource

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "wago"
TABLE = "ChrClasses"
BUILD = "1.60.1.69876"


@pytest.mark.live
def test_live_service_still_matches_the_recordings(tmp_path: Path) -> None:
    with WagoSource(max_attempts=2) as source:
        data = GameData(source, cache_dir=tmp_path / "gamedata")

        listing = data.builds()
        assert any(b.version == BUILD for builds in listing.values() for b in builds), (
            f"{BUILD} is no longer listed: log it in docs/DATA_SOURCES.md"
        )

        path = data.table(TABLE, BUILD)

    recorded = (FIXTURES / f"{TABLE}.{BUILD}.csv").read_bytes()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == hashlib.sha256(recorded).hexdigest(), (
        "a table for a fixed build changed upstream: log it in docs/DATA_SOURCES.md"
    )
    sidecar = data.sidecar(TABLE, BUILD)
    assert sidecar is not None
    assert sidecar.url == f"https://wago.tools/db2/{TABLE}/csv?build={BUILD}"

    record_dir = os.environ.get("WOWLAB_WAGO_RECORD_DIR")
    if record_dir:
        out = Path(record_dir)
        out.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(tmp_path / "gamedata" / "builds.json", out / "builds.json")
        shutil.copyfile(path, out / f"{TABLE}.{BUILD}.csv")
