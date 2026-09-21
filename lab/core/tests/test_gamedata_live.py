"""Manual checks against the real wago.tools. Never run in CI (ADR-0012).

    uv run pytest -m live lab/core/tests/test_gamedata_live.py

wago.tools is community-run; do not loop this.

- `test_live_service_still_matches_the_recordings` makes two requests through
  the library: the builds listing and one small table for the recorded build.
- `test_record_fixtures` is skipped unless `WOWLAB_WAGO_RECORD_DIR` is set. It
  makes the three plain GETs the committed recordings came from (listing,
  table, the 404 for an unpublished build) and writes the bodies exactly as
  served, plus `recording.json` with the status, `Content-Type` and
  `Content-Disposition` of each: the values the index rows and the replay
  tests state. No other header is written; `Set-Cookie` never is. The
  directory must be under `lab/core/tests/fixtures/incoming/` (gitignored).
  Review the output, then add it as *new* fixture files with index rows; a
  committed fixture is never edited. The listing is over the repository's
  file size limit: commit it as `gzip -9` with mtime 0 and put the SHA-256
  of the body in its row.

  This mode has not been exercised: the committed recordings were made on
  2026-09-21 by a one-off script issuing the same three GETs.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import httpx
import pytest

from wowlab_core.gamedata import USER_AGENT, WAGO_BASE_URL, GameData, WagoSource

FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures"
FIXTURES = FIXTURE_ROOT / "wago"
INCOMING = FIXTURE_ROOT / "incoming"
TABLE = "ChrClasses"
BUILD = "1.60.1.69876"
UNKNOWN_BUILD = "1.60.1.1"
RECORD_ENV = "WOWLAB_WAGO_RECORD_DIR"
RECORDED_HEADERS = ("content-type", "content-disposition")


@pytest.mark.live
def test_live_service_still_matches_the_recordings(tmp_path: Path) -> None:
    with WagoSource(max_attempts=2, require_content_disposition=True) as source:
        data = GameData(source, cache_dir=tmp_path / "gamedata")

        listing = data.builds()
        assert any(b.version == BUILD for builds in listing.values() for b in builds), (
            f"{BUILD} is no longer listed: log it in docs/DATA_SOURCES.md"
        )

        path = data.table(TABLE, BUILD)

    recorded = (FIXTURES / f"{TABLE}.{BUILD}.csv").read_bytes()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == hashlib.sha256(recorded).hexdigest(), (
        "a table for a fixed build changed upstream (expected cause: renamed columns from "
        "updated community definitions): log it in docs/DATA_SOURCES.md"
    )
    sidecar = data.sidecar(TABLE, BUILD)
    assert sidecar is not None
    assert sidecar.url == f"{WAGO_BASE_URL}/db2/{TABLE}/csv?build={BUILD}"


def record_dir(value: str) -> Path:
    """The directory to record into: resolved, and only ever under `incoming/`."""
    out = Path(value).expanduser().resolve()
    if not out.is_relative_to(INCOMING.resolve()):
        raise ValueError(f"{RECORD_ENV} must be under {INCOMING}, got {out}")
    return out


def test_record_dir_outside_incoming_is_refused(tmp_path: Path) -> None:
    for bad in (str(tmp_path), str(FIXTURES), str(INCOMING / ".." / "wago"), "/"):
        with pytest.raises(ValueError, match="must be under"):
            record_dir(bad)
    assert record_dir(str(INCOMING / "wago")) == (INCOMING / "wago").resolve()


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get(RECORD_ENV), reason=f"{RECORD_ENV} is not set")
def test_record_fixtures() -> None:
    out = record_dir(os.environ[RECORD_ENV])
    out.mkdir(parents=True, exist_ok=True)
    wanted = {
        "builds.json": f"{WAGO_BASE_URL}/api/builds",
        f"{TABLE}.{BUILD}.csv": f"{WAGO_BASE_URL}/db2/{TABLE}/csv?build={BUILD}",
        f"{TABLE}.{UNKNOWN_BUILD}.404.html": (
            f"{WAGO_BASE_URL}/db2/{TABLE}/csv?build={UNKNOWN_BUILD}"
        ),
    }
    summary: dict[str, dict[str, object]] = {}
    with httpx.Client(headers={"User-Agent": USER_AGENT}, follow_redirects=False) as client:
        for name, url in wanted.items():
            response = client.get(url)
            client.cookies.clear()
            (out / name).write_bytes(response.content)
            summary[name] = {
                "url": url,
                "status": response.status_code,
                "sha256": hashlib.sha256(response.content).hexdigest(),
                "size": len(response.content),
                **{h: response.headers.get(h) for h in RECORDED_HEADERS},
            }
    (out / "recording.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
