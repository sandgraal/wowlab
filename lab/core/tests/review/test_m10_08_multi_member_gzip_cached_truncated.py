# Probe from re-review of m10/08-gamedata-client (0e0400b); reproduces: a gzip body with more than one member is cached truncated to its first member, permanently, with a sidecar vouching for it (L5, ADR-0022).
"""`WagoSource._copy_body` inflates with one `zlib.decompressobj`. When the
first gzip member ends, `eof` is true, everything after it goes to
`unused_data`, and the loop carries on as if the body were complete. RFC 1952
§2.2 allows a gzip stream of several members, and its content is their
concatenation. Measured in review over a loopback server: a 5163-byte table
sent as two members was cached as 2000 bytes, with a sidecar recording
size 2000 and the hash of the fragment. Because a cached table is never
replaced, that fragment is the table for that build for good.

Passing code either inflates every member or refuses the response. The probe
accepts both and only rejects a cached file that is not the whole body. The
body is handed over as an unread stream, so the library takes the same path
as it does for a network response.
"""

from __future__ import annotations

import gzip
from collections.abc import Iterator
from pathlib import Path

import httpx

from wowlab_core.gamedata import GameData, GameDataError, WagoSource

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "wago"
TABLE = "ChrClasses"
BUILD = "1.60.1.69876"
CSV_BYTES = (FIXTURES / f"{TABLE}.{BUILD}.csv").read_bytes()
SPLIT = 2000


class _Unread(httpx.SyncByteStream):
    def __init__(self, wire: bytes) -> None:
        self._wire = wire

    def __iter__(self) -> Iterator[bytes]:
        for start in range(0, len(self._wire), 512):
            yield self._wire[start : start + 512]


def _client(tmp_path: Path, wire: bytes) -> GameData:
    def constructed(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "content-type": "text/csv; charset=UTF-8",
                "content-encoding": "gzip",
                "content-disposition": f'attachment; filename="{TABLE}.{BUILD}.csv"',
            },
            stream=_Unread(wire),
        )

    source = WagoSource(
        transport=httpx.MockTransport(constructed), sleep=lambda _s: None, max_attempts=2
    )
    return GameData(source, cache_dir=tmp_path / "gamedata")


def test_control_constructed_single_member_gzip_is_cached_whole(tmp_path: Path) -> None:
    data = _client(tmp_path, gzip.compress(CSV_BYTES))
    assert data.table(TABLE, BUILD).read_bytes() == CSV_BYTES


def test_constructed_multi_member_gzip_is_never_cached_as_its_first_member(
    tmp_path: Path,
) -> None:
    wire = gzip.compress(CSV_BYTES[:SPLIT]) + gzip.compress(CSV_BYTES[SPLIT:])
    assert gzip.decompress(wire) == CSV_BYTES, "the wire body is a valid gzip stream of the table"

    data = _client(tmp_path, wire)
    final = data.table_path(TABLE, BUILD)
    try:
        data.table(TABLE, BUILD)
    except GameDataError:
        assert not final.exists(), "refused, so nothing may sit under the final name"
        return
    cached = final.read_bytes()
    assert cached == CSV_BYTES, (
        f"cached {len(cached)} of {len(CSV_BYTES)} bytes: the first gzip member only, "
        "and L5 means it is never corrected"
    )
