"""gamedata, replayed from the wago.tools recordings (M10-08, ADR-0012).

Nothing here touches the network: `WagoSource` gets an `httpx.MockTransport`
that serves the bytes under `fixtures/wago/`. Inputs labelled `constructed`
are hostile or boundary cases (L8); everything else is a real recording.
"""

from __future__ import annotations

import csv
import errno
import gzip
import hashlib
import json
import sys
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, NamedTuple

import httpx
import platformdirs
import pytest

from wowlab_core import gamedata
from wowlab_core.gamedata import (
    BuildNotPublished,
    CacheLocationError,
    GameData,
    MalformedTable,
    SourceUnavailable,
    TableNotPublished,
    UnexpectedResponse,
    WagoSource,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "wago"
REPO_ROOT = Path(__file__).resolve().parents[3]

TABLE = "ChrClasses"
BUILD = "1.60.1.69876"  # the recorded build; tests may name it, the library may not (L6)
OTHER_BUILD = "1.60.1.69913"  # listed in the recorded builds.json
UNKNOWN_BUILD = "1.60.1.1"  # the recorded 404

CSV_BYTES = (FIXTURES / f"{TABLE}.{BUILD}.csv").read_bytes()
BUILDS_BYTES = gzip.decompress((FIXTURES / "builds.json.gz").read_bytes())
NOT_FOUND_BYTES = (FIXTURES / f"{TABLE}.{UNKNOWN_BUILD}.404.html").read_bytes()

CSV_HEADERS = {
    "content-type": "text/csv; charset=UTF-8",
    "content-disposition": (
        f"attachment; filename=\"{TABLE}.{BUILD}.csv\"; filename*=UTF-8''{TABLE}.{BUILD}.csv"
    ),
}

Handler = Callable[[httpx.Request], httpx.Response]


def _csv_headers(table: str, build: str) -> dict[str, str]:
    """The recorded header shape, for a constructed table or build."""
    name = f"{table}.{build}.csv"
    return {
        "content-type": "text/csv; charset=UTF-8",
        "content-disposition": f"attachment; filename=\"{name}\"; filename*=UTF-8''{name}",
    }


def recorded(request: httpx.Request) -> httpx.Response:
    """The service as recorded on 2026-09-21: status, content type, body."""
    if request.url.path == "/api/builds":
        return httpx.Response(
            200, headers={"content-type": "application/json"}, content=BUILDS_BYTES
        )
    if request.url.path == f"/db2/{TABLE}/csv" and request.url.params.get("build") == BUILD:
        return httpx.Response(200, headers=CSV_HEADERS, content=CSV_BYTES)
    return httpx.Response(
        404, headers={"content-type": "text/html; charset=utf-8"}, content=NOT_FOUND_BYTES
    )


class Harness(NamedTuple):
    data: GameData
    cache: Path
    requests: list[httpx.Request]
    sleeps: list[float]
    clock: list[datetime]


@pytest.fixture
def make(tmp_path: Path) -> Iterator[Callable[..., Harness]]:
    sources: list[WagoSource] = []

    def _make(handler: Handler = recorded, *, max_attempts: int = 4, **source: Any) -> Harness:
        requests: list[httpx.Request] = []
        sleeps: list[float] = []
        clock = [datetime(2026, 9, 21, 13, 0, tzinfo=UTC)]

        def counting(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return handler(request)

        source = WagoSource(
            transport=httpx.MockTransport(counting),
            sleep=sleeps.append,
            now=lambda: clock[0],
            max_attempts=max_attempts,
            # what GameData's default source uses; the recording always has the header
            **{"require_content_disposition": True, **source},
        )
        sources.append(source)
        cache = tmp_path / "userdata" / "wowlab" / "gamedata"
        data = GameData(source, cache_dir=cache, now=lambda: clock[0])
        return Harness(data, cache, requests, sleeps, clock)

    yield _make
    for source in sources:
        source.close()


def _tree(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


# ─── cache miss, then hit ────────────────────────────────────────────────────


def test_cache_miss_downloads_then_hit_makes_no_request(make: Callable[..., Harness]) -> None:
    h = make()
    first = h.data.table(TABLE, BUILD)
    assert first.read_bytes() == CSV_BYTES
    assert len(h.requests) == 1

    second = h.data.table(TABLE, BUILD)
    assert second == first
    assert len(h.requests) == 1, "a cache hit must not touch the network"


def test_request_shape_matches_the_recording(make: Callable[..., Harness]) -> None:
    h = make()
    h.data.table(TABLE, BUILD)
    (request,) = h.requests
    assert str(request.url) == f"https://wago.tools/db2/{TABLE}/csv?build={BUILD}"
    agent = request.headers["user-agent"]
    assert agent.startswith("wowlab/")
    assert "https://github.com/sandgraal/wowlab" in agent


def test_cache_key_is_table_and_full_build_string(make: Callable[..., Harness]) -> None:
    h = make()
    path = h.data.table(TABLE, BUILD)
    assert path == h.cache / "tables" / BUILD / f"{TABLE}.csv"
    assert h.data.table_path(TABLE, OTHER_BUILD) != path


# ─── L5: never overwritten ───────────────────────────────────────────────────


def test_constructed_existing_cached_build_is_never_overwritten(
    make: Callable[..., Harness],
) -> None:
    h = make()
    final = h.data.table_path(TABLE, BUILD)
    final.parent.mkdir(parents=True)
    final.write_bytes(b"ID\n1\n")  # constructed: stands for bytes cached earlier
    before = final.stat()

    assert h.data.table(TABLE, BUILD) == final
    assert final.read_bytes() == b"ID\n1\n"
    assert final.stat().st_mtime_ns == before.st_mtime_ns
    assert h.requests == []


def test_constructed_lost_race_keeps_the_file_that_got_there_first(
    make: Callable[..., Harness],
) -> None:
    winner = b"ID\nwinner\n"  # constructed: another process published mid-download
    holder: list[Harness] = []

    def racing(request: httpx.Request) -> httpx.Response:
        final = holder[0].data.table_path(TABLE, BUILD)
        final.write_bytes(winner)
        return recorded(request)

    h = make(racing)
    holder.append(h)
    path = h.data.table(TABLE, BUILD)

    assert path.read_bytes() == winner, "the loser of a race must not replace the file"
    assert _tree(h.cache) == {f"tables/{BUILD}/{TABLE}.csv"}, (
        "no temp file left, and no sidecar describing bytes that are not on disk"
    )


def test_constructed_another_build_never_replaces_a_cached_build(
    make: Callable[..., Harness],
) -> None:
    def per_build(request: httpx.Request) -> httpx.Response:
        build = request.url.params["build"]
        if build == BUILD:
            return recorded(request)
        body = b"ID\n" + build.encode() + b"\n"  # constructed second build
        return httpx.Response(200, headers=_csv_headers(TABLE, build), content=body)

    h = make(per_build)
    a = h.data.table(TABLE, BUILD)
    b = h.data.table(TABLE, OTHER_BUILD)
    assert a != b
    assert a.read_bytes() == CSV_BYTES
    assert b.read_bytes() == b"ID\n" + OTHER_BUILD.encode() + b"\n"


def test_constructed_response_for_a_different_build_is_refused_not_cached(
    make: Callable[..., Harness],
) -> None:
    def wrong_build(request: httpx.Request) -> httpx.Response:  # constructed fallback
        return httpx.Response(200, headers=CSV_HEADERS, content=CSV_BYTES)

    h = make(wrong_build)
    with pytest.raises(UnexpectedResponse, match=OTHER_BUILD):
        h.data.table(TABLE, OTHER_BUILD)
    assert _tree(h.cache) == set()


def test_constructed_html_with_status_200_is_refused_not_cached(
    make: Callable[..., Harness],
) -> None:
    def challenge(request: httpx.Request) -> httpx.Response:  # constructed
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b"<html>")

    h = make(challenge)
    with pytest.raises(UnexpectedResponse, match="text/csv"):
        h.data.table(TABLE, BUILD)
    assert _tree(h.cache) == set()


# ─── interrupted download ────────────────────────────────────────────────────


class _DropsMidBody(httpx.SyncByteStream):
    def __init__(self, request: httpx.Request) -> None:
        self._request = request

    def __iter__(self) -> Iterator[bytes]:
        yield CSV_BYTES[:1000]
        raise httpx.ReadError("connection reset mid-body", request=self._request)


def _interrupted(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, headers=CSV_HEADERS, stream=_DropsMidBody(request))


def test_constructed_interrupted_download_leaves_nothing_under_the_final_name(
    make: Callable[..., Harness],
) -> None:
    h = make(_interrupted, max_attempts=3)
    with pytest.raises(SourceUnavailable):
        h.data.table(TABLE, BUILD)

    assert not h.data.table_path(TABLE, BUILD).exists()
    assert _tree(h.cache) == set(), "no partial file, temp file or sidecar may remain"
    assert len(h.requests) == 3


def test_constructed_interrupted_then_complete_download_caches_only_the_complete_body(
    make: Callable[..., Harness],
) -> None:
    calls = iter([_interrupted, recorded])

    h = make(lambda request: next(calls)(request))
    path = h.data.table(TABLE, BUILD)
    assert path.read_bytes() == CSV_BYTES, "the first attempt's 1000 bytes must not survive"
    assert len(h.sleeps) == 1


def test_constructed_crash_outside_the_source_still_cleans_the_temp_file(
    make: Callable[..., Harness],
) -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise RuntimeError("constructed: not a transport error, not retried")

    h = make(boom)
    with pytest.raises(RuntimeError):
        h.data.table(TABLE, BUILD)
    assert _tree(h.cache) == set()


# ─── sidecar ─────────────────────────────────────────────────────────────────


def test_sidecar_records_url_time_size_and_sha256(make: Callable[..., Harness]) -> None:
    h = make()
    path = h.data.table(TABLE, BUILD)
    raw = json.loads(path.with_name(path.name + ".json").read_text(encoding="utf-8"))

    assert raw["url"] == f"https://wago.tools/db2/{TABLE}/csv?build={BUILD}"
    assert datetime.fromisoformat(raw["fetched_at"]) == h.clock[0]
    assert raw["size"] == len(CSV_BYTES) == path.stat().st_size
    assert raw["sha256"] == hashlib.sha256(CSV_BYTES).hexdigest()
    assert (raw["table"], raw["build"], raw["source"]) == (TABLE, BUILD, "wago.tools")

    sidecar = h.data.sidecar(TABLE, BUILD)
    assert sidecar is not None
    assert sidecar.fetched_at.tzinfo is not None


def test_a_hit_does_not_rewrite_the_sidecar(make: Callable[..., Harness]) -> None:
    h = make()
    path = h.data.table(TABLE, BUILD)
    sidecar = path.with_name(path.name + ".json")
    before = sidecar.read_bytes()
    h.clock[0] += timedelta(days=30)
    h.data.table(TABLE, BUILD)
    assert sidecar.read_bytes() == before


# ─── 429 and 5xx ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("status", [429, 500, 502, 503])
def test_constructed_retryable_status_backs_off_then_succeeds(
    make: Callable[..., Harness], status: int
) -> None:
    responses = iter([status, status, 200])

    def flaky(request: httpx.Request) -> httpx.Response:
        code = next(responses)
        return recorded(request) if code == 200 else httpx.Response(code, content=b"busy")

    h = make(flaky)
    assert h.data.table(TABLE, BUILD).read_bytes() == CSV_BYTES
    assert len(h.requests) == 3
    assert len(h.sleeps) == 2
    assert h.sleeps[0] > 0
    assert h.sleeps[1] > h.sleeps[0], "backoff must grow"


def test_constructed_retry_after_seconds_is_honoured_and_capped(
    make: Callable[..., Harness],
) -> None:
    responses = iter(["45", "86400"])

    def limited(request: httpx.Request) -> httpx.Response:
        try:
            return httpx.Response(429, headers={"retry-after": next(responses)})
        except StopIteration:
            return recorded(request)

    h = make(limited)
    h.data.table(TABLE, BUILD)
    assert h.sleeps == [45.0, 120.0]


def test_constructed_retries_are_bounded_and_nothing_is_cached(
    make: Callable[..., Harness],
) -> None:
    h = make(lambda request: httpx.Response(503), max_attempts=4)
    with pytest.raises(SourceUnavailable, match="503") as caught:
        h.data.table(TABLE, BUILD)
    assert caught.value.attempts == 4
    assert len(h.requests) == 4
    assert len(h.sleeps) == 3, "no sleep after the last attempt"
    assert _tree(h.cache) == set()


def test_constructed_other_client_errors_are_not_retried(make: Callable[..., Harness]) -> None:
    h = make(lambda request: httpx.Response(403))
    with pytest.raises(UnexpectedResponse, match="403"):
        h.data.table(TABLE, BUILD)
    assert len(h.requests) == 1
    assert h.sleeps == []


# ─── builds and BuildNotPublished ────────────────────────────────────────────


class FakeFlavor(NamedTuple):
    """Stands in for `install.Flavor` (M10-05): the two fields gamedata reads."""

    product: str
    version: str | None


def test_builds_lists_every_product_in_the_recording(make: Callable[..., Harness]) -> None:
    h = make()
    listing = h.data.builds()
    expected = json.loads(BUILDS_BYTES)
    assert list(listing) == list(expected)
    assert {p: len(b) for p, b in listing.items()} == {p: len(b) for p, b in expected.items()}
    assert str(h.requests[0].url) == "https://wago.tools/api/builds"


def test_builds_are_cached_for_a_short_ttl_then_refetched(make: Callable[..., Harness]) -> None:
    h = make()
    h.data.builds()
    h.clock[0] += timedelta(minutes=59)
    h.data.builds()
    assert len(h.requests) == 1

    h.clock[0] += timedelta(minutes=2)
    h.data.builds()
    assert len(h.requests) == 2


def test_resolve_build_matches_an_installed_version(make: Callable[..., Harness]) -> None:
    h = make()
    build = h.data.resolve_build(FakeFlavor("wow_classic_beta", OTHER_BUILD))
    assert (build.product, build.version) == ("wow_classic_beta", OTHER_BUILD)
    assert build.build_config == "6c0df97e8e481a9a41600e373367c200"


def test_resolve_build_accepts_the_same_version_under_another_product(
    make: Callable[..., Harness],
) -> None:
    h = make()
    flavor = FakeFlavor("a_product_the_listing_lacks", OTHER_BUILD)
    build = h.data.resolve_build(flavor)
    assert build.version == OTHER_BUILD
    assert build.product != flavor.product, (
        "from a cross-product match only .version describes the installed flavor; "
        "product and the config hashes belong to the other product's build"
    )


def test_resolve_build_raises_build_not_published(make: Callable[..., Harness]) -> None:
    h = make()
    with pytest.raises(BuildNotPublished) as caught:
        h.data.resolve_build(FakeFlavor("wow_classic_beta", UNKNOWN_BUILD))
    assert caught.value.version == UNKNOWN_BUILD
    assert caught.value.product == "wow_classic_beta"


def test_resolve_build_refetches_once_before_giving_up(make: Callable[..., Harness]) -> None:
    h = make()
    h.data.builds()
    h.clock[0] += timedelta(minutes=6)  # past builds_min_refresh, inside the TTL
    with pytest.raises(BuildNotPublished):
        h.data.resolve_build(FakeFlavor("wow", UNKNOWN_BUILD))
    assert len(h.requests) == 2, "a build published since the cached listing must be found"


def test_misses_inside_the_minimum_refresh_interval_do_not_refetch(
    make: Callable[..., Harness],
) -> None:
    h = make()
    h.data.builds()
    h.clock[0] += timedelta(minutes=4)
    for _ in range(3):
        with pytest.raises(BuildNotPublished):
            h.data.resolve_build(FakeFlavor("wow", UNKNOWN_BUILD))
        with pytest.raises(BuildNotPublished):
            h.data.table(TABLE, UNKNOWN_BUILD)
    listing_requests = [r for r in h.requests if r.url.path == "/api/builds"]
    assert len(listing_requests) == 1, "one listing download per interval, not one per miss"


def test_resolve_build_without_a_version(make: Callable[..., Harness]) -> None:
    h = make()
    with pytest.raises(BuildNotPublished) as caught:
        h.data.resolve_build(FakeFlavor("wow", None))
    assert h.requests == []
    assert "has no version in .build.info" in str(caught.value)
    assert "not published" not in str(caught.value), "nothing was asked of the source"


def test_table_for_an_unknown_build_raises_build_not_published(
    make: Callable[..., Harness],
) -> None:
    h = make()
    with pytest.raises(BuildNotPublished) as caught:
        h.data.table(TABLE, UNKNOWN_BUILD)
    assert caught.value.version == UNKNOWN_BUILD
    assert not h.data.table_path(TABLE, UNKNOWN_BUILD).exists()
    assert not [p for p in _tree(h.cache) if p.startswith("tables/")], (
        "the recorded 404 page must never be cached as a table"
    )


def test_unknown_table_for_a_published_build(make: Callable[..., Harness]) -> None:
    h = make()
    with pytest.raises(TableNotPublished):
        h.data.table("NoSuchTable", OTHER_BUILD)


# ─── rows ────────────────────────────────────────────────────────────────────


def test_rows_are_string_dicts_with_no_coercion(make: Callable[..., Harness]) -> None:
    h = make()
    rows = list(h.data.rows(TABLE, BUILD))
    assert [(r["ID"], r["Name_lang"]) for r in rows] == [
        ("1", "Warrior"),
        ("2", "Paladin"),
        ("3", "Hunter"),
        ("4", "Rogue"),
        ("5", "Priest"),
        ("7", "Shaman"),
        ("8", "Mage"),
        ("9", "Warlock"),
        ("11", "Druid"),
    ]
    header = CSV_BYTES.split(b"\n", 1)[0].decode().split(",")
    for row in rows:
        assert list(row) == header
        assert all(isinstance(v, str) for v in row.values())
    assert rows[0]["Flags"] == "8425472"


# ─── where the cache lives ───────────────────────────────────────────────────


def test_default_cache_is_under_the_user_data_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked: list[str] = []

    def fake_user_data_path(appname: str) -> Path:
        asked.append(appname)
        return tmp_path / "AppData" / appname

    monkeypatch.setattr(platformdirs, "user_data_path", fake_user_data_path)
    data = GameData(WagoSource(transport=httpx.MockTransport(recorded)))
    assert asked == ["wowlab"]
    assert data.cache_dir == tmp_path / "AppData" / "wowlab" / "gamedata"

    path = data.table(TABLE, BUILD)
    assert path.is_relative_to(tmp_path / "AppData" / "wowlab")


def test_real_default_cache_is_not_in_the_repository() -> None:
    default = gamedata.default_cache_dir().resolve()
    assert default.is_relative_to(platformdirs.user_data_path("wowlab").resolve())
    assert not default.is_relative_to(REPO_ROOT)


def test_constructed_cache_inside_an_install_is_refused(tmp_path: Path) -> None:
    install = tmp_path / "World of Warcraft"  # synthetic tree; no real install needed
    (install / "_any_flavor_" / "Cache").mkdir(parents=True)
    (install / ".build.info").write_text("Branch!STRING:0|Active!DEC:1\n", encoding="utf-8")
    before = _tree(install)

    with pytest.raises(CacheLocationError):
        GameData(
            WagoSource(transport=httpx.MockTransport(recorded)),
            cache_dir=install / "_any_flavor_" / "Cache" / "wowlab",
        )
    assert _tree(install) == before, "refusing must not create anything in the install (L1)"


# ─── hostile keys ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("table", "build"),
    [
        pytest.param("../../escape", BUILD, id="constructed-table-traversal"),
        pytest.param("Chr/Classes", BUILD, id="constructed-table-separator"),
        pytest.param("", BUILD, id="constructed-table-empty"),
        pytest.param(TABLE, "../1.2.3.4", id="constructed-build-traversal"),
        pytest.param(TABLE, "1.60.1", id="constructed-build-not-full"),
        pytest.param(TABLE, "latest", id="constructed-build-alias"),
    ],
)
def test_keys_that_are_not_a_table_and_a_full_build_are_rejected(
    make: Callable[..., Harness], table: str, build: str
) -> None:
    h = make()
    with pytest.raises(ValueError):
        h.data.table(table, build)
    assert h.requests == []
    assert _tree(h.cache.parent) == set()


# ─── fix round 1: bounded bodies ─────────────────────────────────────────────


def _streamed(headers: dict[str, str], body: bytes) -> httpx.Response:
    """An unread response, as the network gives: the client decodes it."""
    return httpx.Response(200, headers=headers, stream=httpx.ByteStream(body))


def test_recorded_csv_served_gzipped_and_unread_is_cached_byte_for_byte(
    make: Callable[..., Harness],
) -> None:
    wire = gzip.compress(CSV_BYTES)
    h = make(lambda request: _streamed({**CSV_HEADERS, "content-encoding": "gzip"}, wire))
    assert h.data.table(TABLE, BUILD).read_bytes() == CSV_BYTES
    assert h.requests[0].headers["accept-encoding"] == "gzip"


def test_constructed_oversize_table_body_is_refused_not_cached(
    make: Callable[..., Harness],
) -> None:
    body = b"ID\n" + b"1\n" * 40_000  # constructed: 80 KB against a 64 KiB cap
    h = make(lambda request: _streamed(CSV_HEADERS, body), max_table_bytes=1 << 16)
    with pytest.raises(UnexpectedResponse, match="cap"):
        h.data.table(TABLE, BUILD)
    assert len(h.requests) == 1, "an oversize body is not retried"
    assert _tree(h.cache) == set()


def test_constructed_gzip_bomb_table_is_refused_not_cached(make: Callable[..., Harness]) -> None:
    bomb = gzip.compress(b"\0" * (32 << 20))  # constructed: about 32 KB on the wire, 32 MiB decoded
    assert len(bomb) < 1 << 16
    h = make(
        lambda request: _streamed({**CSV_HEADERS, "content-encoding": "gzip"}, bomb),
        max_table_bytes=1 << 20,
    )
    with pytest.raises(UnexpectedResponse, match="cap"):
        h.data.table(TABLE, BUILD)
    assert _tree(h.cache) == set(), "nothing of the bomb may remain on disk"


def test_constructed_gzip_bomb_builds_listing_is_refused_not_cached(
    make: Callable[..., Harness],
) -> None:
    bomb = gzip.compress(b" " * (gamedata.MAX_BUILDS_BYTES + 1))  # constructed
    h = make(
        lambda request: _streamed(
            {"content-type": "application/json", "content-encoding": "gzip"}, bomb
        )
    )
    with pytest.raises(UnexpectedResponse, match="cap"):
        h.data.builds()
    assert _tree(h.cache) == set()


def test_constructed_declared_content_length_over_the_cap_is_refused_up_front(
    make: Callable[..., Harness],
) -> None:
    class _NeverRead(httpx.SyncByteStream):
        def __iter__(self) -> Iterator[bytes]:
            raise AssertionError("the body must not be read")

    def huge(request: httpx.Request) -> httpx.Response:
        headers = {**CSV_HEADERS, "content-length": str(gamedata.MAX_TABLE_BYTES + 1)}
        return httpx.Response(200, headers=headers, stream=_NeverRead())

    h = make(huge)
    with pytest.raises(UnexpectedResponse, match="Content-Length"):
        h.data.table(TABLE, BUILD)
    assert _tree(h.cache) == set()


def test_constructed_unrequested_content_encoding_is_refused(make: Callable[..., Harness]) -> None:
    h = make(lambda request: _streamed({**CSV_HEADERS, "content-encoding": "br"}, b"xx"))
    with pytest.raises(UnexpectedResponse, match="Content-Encoding"):
        h.data.table(TABLE, BUILD)
    assert _tree(h.cache) == set()


def test_constructed_truncated_gzip_body_is_retried_never_cached(
    make: Callable[..., Harness],
) -> None:
    cut = gzip.compress(CSV_BYTES)[:-20]
    h = make(
        lambda request: _streamed({**CSV_HEADERS, "content-encoding": "gzip"}, cut),
        max_attempts=2,
    )
    with pytest.raises(SourceUnavailable, match="gzip"):
        h.data.table(TABLE, BUILD)
    assert len(h.requests) == 2
    assert _tree(h.cache) == set()


def test_constructed_download_past_its_wall_clock_deadline_is_refused(
    make: Callable[..., Harness],
) -> None:
    ticks = iter(range(0, 10_000, 100))  # constructed clock: 100 s per look

    class _Slow(httpx.SyncByteStream):
        def __iter__(self) -> Iterator[bytes]:
            for i in range(0, len(CSV_BYTES), 500):
                yield CSV_BYTES[i : i + 500]

    h = make(
        lambda request: httpx.Response(200, headers=CSV_HEADERS, stream=_Slow()),
        monotonic=lambda: float(next(ticks)),
        download_deadline=250.0,
    )
    with pytest.raises(UnexpectedResponse, match="deadline"):
        h.data.table(TABLE, BUILD)
    assert _tree(h.cache) == set()


# ─── fix round 1: no cookies, https only ─────────────────────────────────────


def test_constructed_set_cookie_is_never_sent_back(make: Callable[..., Harness]) -> None:
    def sets_cookies(request: httpx.Request) -> httpx.Response:
        response = recorded(request)
        # The recorded 404 set two cookies; their values are not committed.
        response.headers["set-cookie"] = "wagotools_session=constructed; path=/; secure"
        return response

    h = make(sets_cookies)
    with pytest.raises(BuildNotPublished):
        h.data.table(TABLE, UNKNOWN_BUILD)
    h.data.table(TABLE, BUILD)
    h.data.builds(refresh=True)
    assert len(h.requests) >= 4
    assert all("cookie" not in request.headers for request in h.requests)


@pytest.mark.parametrize(
    "base_url",
    [
        pytest.param("http://wago.tools", id="constructed-plain-http"),
        pytest.param("http://127.0.0.1.example.com", id="constructed-loopback-lookalike"),
        pytest.param("ftp://wago.tools", id="constructed-other-scheme"),
    ],
)
def test_base_url_must_be_https(base_url: str) -> None:
    with pytest.raises(ValueError, match="https"):
        WagoSource(base_url=base_url)


@pytest.mark.parametrize("base_url", ["https://wago.tools", "http://127.0.0.1:8080"])
def test_base_url_https_or_loopback_is_accepted(base_url: str) -> None:
    WagoSource(base_url=base_url, transport=httpx.MockTransport(recorded)).close()


# ─── fix round 1: Content-Disposition names the table and the build ──────────


def test_constructed_missing_content_disposition_is_refused_by_the_default_source(
    make: Callable[..., Harness],
) -> None:
    h = make(
        lambda request: httpx.Response(
            200, headers={"content-type": "text/csv; charset=UTF-8"}, content=CSV_BYTES
        )
    )
    with pytest.raises(UnexpectedResponse, match="Content-Disposition"):
        h.data.table(TABLE, BUILD)
    assert _tree(h.cache) == set()


def test_default_source_requires_content_disposition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    made: list[dict[str, Any]] = []
    real = gamedata.WagoSource

    def spy(**kwargs: Any) -> WagoSource:
        made.append(kwargs)
        return real(**kwargs)

    monkeypatch.setattr(gamedata, "WagoSource", spy)
    GameData(cache_dir=tmp_path / "gamedata")
    assert made == [{"require_content_disposition": True}]


@pytest.mark.parametrize(
    "filename",
    [
        pytest.param(f"ChrRaces.{BUILD}.csv", id="constructed-another-table"),
        pytest.param(f"{TABLE}.{OTHER_BUILD}.csv", id="constructed-another-build"),
        pytest.param(f"x{TABLE}.{BUILD}.csv", id="constructed-suffix-match-only"),
        pytest.param(f"{TABLE}.{BUILD}.csv.html", id="constructed-prefix-match-only"),
    ],
)
def test_content_disposition_must_name_exactly_the_table_and_build(
    make: Callable[..., Harness], filename: str
) -> None:
    headers = {**CSV_HEADERS, "content-disposition": f'attachment; filename="{filename}"'}
    h = make(lambda request: httpx.Response(200, headers=headers, content=CSV_BYTES))
    with pytest.raises(UnexpectedResponse, match="Content-Disposition"):
        h.data.table(TABLE, BUILD)
    assert _tree(h.cache) == set()


# ─── fix round 1: Retry-After as an HTTP-date ────────────────────────────────


def test_constructed_retry_after_http_date_is_honoured_and_capped(
    make: Callable[..., Harness],
) -> None:
    # The harness clock reads 2026-09-21 13:00:00 UTC.
    dates = iter(["Mon, 21 Sep 2026 13:00:50 GMT", "Tue, 22 Sep 2026 13:00:00 GMT", "soon"])

    def limited(request: httpx.Request) -> httpx.Response:
        try:
            return httpx.Response(429, headers={"retry-after": next(dates)})
        except StopIteration:
            return recorded(request)

    h = make(limited)
    h.data.table(TABLE, BUILD)
    assert h.sleeps == [50.0, 120.0, 8.0], "date form, capped date form, then own backoff"


# ─── fix round 1: symlinks into an install ───────────────────────────────────


def _synthetic_install(root: Path) -> Path:
    (root / "_any_flavor_" / "WTF").mkdir(parents=True)
    (root / ".build.info").write_text("Branch!STRING:0|Active!DEC:1\n", encoding="utf-8")
    return root / "_any_flavor_" / "WTF"


@pytest.mark.skipif(not hasattr(Path, "symlink_to"), reason="needs symlinks")
@pytest.mark.parametrize("link", ["tables", "tables/" + BUILD, "."])
def test_constructed_symlink_into_an_install_is_refused_before_any_write(
    make: Callable[..., Harness], tmp_path: Path, link: str
) -> None:
    wtf = _synthetic_install(tmp_path / "World of Warcraft")
    h = make()
    target = h.cache / link
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.symlink_to(wtf, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are not permitted here")
    before = _tree(tmp_path / "World of Warcraft")
    dirs_before = {p for p in (tmp_path / "World of Warcraft").rglob("*") if p.is_dir()}

    with pytest.raises(CacheLocationError):
        h.data.table(TABLE, BUILD)
    if link == ".":
        with pytest.raises(CacheLocationError):
            h.data.builds()

    assert _tree(tmp_path / "World of Warcraft") == before, "nothing written into the install"
    assert {p for p in (tmp_path / "World of Warcraft").rglob("*") if p.is_dir()} == dirs_before, (
        "no directory created in the install either"
    )
    assert h.requests == [], "refused before any request"


# ─── fix round 1: no hard links ──────────────────────────────────────────────


@pytest.mark.skipif(sys.platform == "win32", reason="Windows rename refuses an existing target")
def test_constructed_posix_filesystem_without_hard_links_is_refused_not_renamed(
    make: Callable[..., Harness], monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_hard_links(self: Path, target: Path) -> None:
        raise OSError(errno.ENOTSUP, "constructed: filesystem without hard links")

    def never(self: Path, target: Path) -> Path:
        raise AssertionError("POSIX rename replaces its target; it must not publish a table")

    monkeypatch.setattr(Path, "hardlink_to", no_hard_links)
    monkeypatch.setattr(Path, "rename", never)
    monkeypatch.setattr(Path, "replace", never)
    h = make()
    with pytest.raises(CacheLocationError, match="hard links"):
        h.data.table(TABLE, BUILD)
    assert _tree(h.cache) == set()


@pytest.mark.skipif(sys.platform == "win32", reason="Windows rename refuses an existing target")
def test_constructed_winner_survives_without_hard_links(
    make: Callable[..., Harness], monkeypatch: pytest.MonkeyPatch
) -> None:
    winner = b"ID\nwinner\n"
    h = make()
    final = h.data.table_path(TABLE, BUILD)

    def winner_lands_then_no_hard_links(self: Path, target: Path) -> None:
        if self == final:
            final.write_bytes(winner)
        raise OSError(errno.ENOTSUP, "constructed: filesystem without hard links")

    monkeypatch.setattr(Path, "hardlink_to", winner_lands_then_no_hard_links)
    assert h.data.table(TABLE, BUILD).read_bytes() == winner
    assert _tree(h.cache) == {f"tables/{BUILD}/{TABLE}.csv"}


# ─── fix round 1: rows that cannot be dict[str, str] without loss ────────────


def _cached(h: Harness, body: bytes) -> None:
    final = h.data.table_path(TABLE, BUILD)
    final.parent.mkdir(parents=True)
    final.write_bytes(body)


@pytest.mark.parametrize(
    ("body", "message"),
    [
        pytest.param(
            b"ID,Field,Field\n1,a,b\n", "column names repeat", id="constructed-dup-header"
        ),
        pytest.param(b"ID,Name\n1,ok\n2,\xff\xfe\n", "after line", id="constructed-invalid-utf8"),
        pytest.param(
            b"ID,Name\n1,ok\n2," + b"x" * (csv.field_size_limit() + 1) + b"\n",
            "line 3: field larger",
            id="constructed-field-over-the-csv-limit",
        ),
        pytest.param(b'ID,Name\n1,"a"b\n', "line 2: ", id="constructed-bad-quoting"),
        pytest.param(b"ID,Name\n1\n", "1 fields", id="constructed-short-row"),
    ],
)
def test_rows_raise_malformed_table(
    make: Callable[..., Harness], body: bytes, message: str
) -> None:
    h = make()
    _cached(h, body)
    with pytest.raises(MalformedTable, match=message):
        list(h.data.rows(TABLE, BUILD))
    assert h.data.table_path(TABLE, BUILD).read_bytes() == body, "the cached file stays as it is"
