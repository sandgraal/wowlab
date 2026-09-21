# Probe from re-review of m10/08-gamedata-client (0e0400b); reproduces: a cache location that is going to be refused for lacking hard links costs the source one full table download per call before the refusal (ADR-0022 polite client).
"""The fix for the no-hard-link publish race refuses the location with
`CacheLocationError`, which holds L5. But the refusal comes from
`_publish_without_overwrite`, after the whole table has been fetched, and
nothing remembers it. Measured in review: three `table()` calls on such a
filesystem made three complete downloads and threw each one away. Tables run
to hundreds of MiB and the service is community-run.

Whether a directory supports hard links is knowable before the request (link
a throwaway file beside where the table will go). The probe does not require
a refusal: code that publishes safely some other way may make its one
request. It requires only that a call that ends in `CacheLocationError` has
not asked the source for anything.
"""

from __future__ import annotations

import errno
import os
import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

from wowlab_core.gamedata import CacheLocationError, GameData, WagoSource

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "wago"
TABLE = "ChrClasses"
BUILD = "1.60.1.69876"
CSV_BYTES = (FIXTURES / f"{TABLE}.{BUILD}.csv").read_bytes()
CSV_HEADERS = {
    "content-type": "text/csv; charset=UTF-8",
    "content-disposition": f'attachment; filename="{TABLE}.{BUILD}.csv"',
}


def _client(tmp_path: Path) -> tuple[GameData, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def recorded(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, headers=CSV_HEADERS, content=CSV_BYTES)

    source = WagoSource(transport=httpx.MockTransport(recorded), sleep=lambda _s: None)
    return GameData(source, cache_dir=tmp_path / "gamedata"), requests


def test_control_one_request_where_hard_links_work(tmp_path: Path) -> None:
    data, requests = _client(tmp_path)
    assert data.table(TABLE, BUILD).read_bytes() == CSV_BYTES
    assert len(requests) == 1


@pytest.mark.skipif(sys.platform == "win32", reason="Windows publishes by rename instead")
def test_constructed_refused_location_asks_the_source_for_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_hard_links(_src: Any, _dst: Any, **_kwargs: Any) -> None:
        raise OSError(errno.ENOTSUP, "constructed: filesystem without hard links")

    monkeypatch.setattr(os, "link", no_hard_links)
    data, requests = _client(tmp_path)

    refusals = 0
    for _ in range(3):
        try:
            data.table(TABLE, BUILD)
        except CacheLocationError:
            refusals += 1

    if refusals == 0:
        assert data.table_path(TABLE, BUILD).read_bytes() == CSV_BYTES
        return
    assert refusals == 3, "a location is either usable or it is not"
    assert requests == [], (
        f"{len(requests)} full downloads ({len(requests) * len(CSV_BYTES)} bytes) were made "
        "and discarded for a location that was always going to be refused"
    )
