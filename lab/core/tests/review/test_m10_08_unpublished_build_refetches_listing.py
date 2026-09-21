# Probe from review of m10/08-gamedata-client; reproduces: every miss on an unpublished build re-downloads the whole builds listing, even one fetched at the same instant (LAB_PLAN §6.6 "cached with a short TTL", ADR-0022 polite client).
"""A build the source does not list yet is the expected case on a beta: the
client patches before wago.tools publishes. Each `table()` or
`resolve_build()` for such a build discards a listing that is still inside
its TTL and downloads all ~540 KB of it again. Five lookups measured in
review: ten requests, 2,698,320 bytes of `/api/builds`.

The probe holds the clock still, so the listing on disk is zero seconds old
and a refetch cannot tell the caller anything new. It does not prescribe the
fix (a minimum refresh interval, a remembered miss, or anything else that
keeps the "published five minutes ago" case working).
"""

from __future__ import annotations

import gzip
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import NamedTuple

import httpx
import pytest

from wowlab_core.gamedata import BuildNotPublished, GameData, WagoSource

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "wago"
TABLE = "ChrClasses"
UNKNOWN_BUILD = "1.60.1.1"  # the recorded 404
BUILDS_BYTES = gzip.decompress((FIXTURES / "builds.json.gz").read_bytes())
NOT_FOUND_BYTES = (FIXTURES / f"{TABLE}.{UNKNOWN_BUILD}.404.html").read_bytes()


class _Flavor(NamedTuple):
    product: str
    version: str | None


def _client(tmp_path: Path) -> tuple[GameData, list[str], list[datetime]]:
    paths: list[str] = []
    clock = [datetime(2026, 9, 21, 13, 0, tzinfo=UTC)]

    def recorded(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/api/builds":
            return httpx.Response(
                200, headers={"content-type": "application/json"}, content=BUILDS_BYTES
            )
        return httpx.Response(
            404, headers={"content-type": "text/html; charset=utf-8"}, content=NOT_FOUND_BYTES
        )

    source = WagoSource(transport=httpx.MockTransport(recorded), sleep=lambda _s: None)
    data = GameData(source, cache_dir=tmp_path / "gamedata", now=lambda: clock[0])
    return data, paths, clock


def test_control_first_miss_fetches_the_listing_once_and_ttl_expiry_refetches(
    tmp_path: Path,
) -> None:
    data, paths, clock = _client(tmp_path)
    with pytest.raises(BuildNotPublished):
        data.table(TABLE, UNKNOWN_BUILD)
    assert paths.count("/api/builds") == 1

    clock[0] += timedelta(hours=2)
    with pytest.raises(BuildNotPublished):
        data.table(TABLE, UNKNOWN_BUILD)
    assert paths.count("/api/builds") == 2, "an expired listing is refetched"


def test_repeated_table_misses_do_not_redownload_a_listing_fetched_this_instant(
    tmp_path: Path,
) -> None:
    data, paths, _clock = _client(tmp_path)
    for _ in range(5):
        with pytest.raises(BuildNotPublished):
            data.table(TABLE, UNKNOWN_BUILD)
    assert paths.count("/api/builds") == 1, (
        f"{paths.count('/api/builds')} downloads of the builds listing "
        f"({paths.count('/api/builds') * len(BUILDS_BYTES)} bytes) for one unpublished build "
        "with the clock held still"
    )


def test_repeated_resolve_build_misses_do_not_redownload_a_listing_fetched_this_instant(
    tmp_path: Path,
) -> None:
    data, paths, _clock = _client(tmp_path)
    for _ in range(5):
        with pytest.raises(BuildNotPublished):
            data.resolve_build(_Flavor("wow_classic_beta", UNKNOWN_BUILD))
    assert paths.count("/api/builds") == 1
