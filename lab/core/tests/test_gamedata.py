"""gamedata, replayed from the wago.tools recordings (M10-08, ADR-0012).

Nothing here touches the network: `WagoSource` gets an `httpx.MockTransport`
that serves the bytes under `fixtures/wago/`. Inputs labelled `constructed`
are hostile or boundary cases (L8); everything else is a real recording.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import NamedTuple

import httpx
import platformdirs
import pytest

from wowlab_core import gamedata
from wowlab_core.gamedata import (
    BuildNotPublished,
    CacheLocationError,
    GameData,
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

    def _make(handler: Handler = recorded, *, max_attempts: int = 4) -> Harness:
        requests: list[httpx.Request] = []
        sleeps: list[float] = []
        clock = [datetime(2026, 9, 21, 13, 0, tzinfo=UTC)]

        def counting(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return handler(request)

        source = WagoSource(
            transport=httpx.MockTransport(counting),
            sleep=sleeps.append,
            max_attempts=max_attempts,
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


def test_existing_cached_build_is_never_overwritten(make: Callable[..., Harness]) -> None:
    h = make()
    final = h.data.table_path(TABLE, BUILD)
    final.parent.mkdir(parents=True)
    final.write_bytes(b"ID\n1\n")  # constructed: stands for bytes cached earlier
    before = final.stat()

    assert h.data.table(TABLE, BUILD) == final
    assert final.read_bytes() == b"ID\n1\n"
    assert final.stat().st_mtime_ns == before.st_mtime_ns
    assert h.requests == []


def test_lost_race_keeps_the_file_that_got_there_first(make: Callable[..., Harness]) -> None:
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


def test_another_build_never_replaces_a_cached_build(make: Callable[..., Harness]) -> None:
    def per_build(request: httpx.Request) -> httpx.Response:
        build = request.url.params["build"]
        if build == BUILD:
            return recorded(request)
        body = b"ID\n" + build.encode() + b"\n"  # constructed second build
        return httpx.Response(200, headers={"content-type": "text/csv"}, content=body)

    h = make(per_build)
    a = h.data.table(TABLE, BUILD)
    b = h.data.table(TABLE, OTHER_BUILD)
    assert a != b
    assert a.read_bytes() == CSV_BYTES
    assert b.read_bytes() == b"ID\n" + OTHER_BUILD.encode() + b"\n"


def test_response_for_a_different_build_is_refused_not_cached(
    make: Callable[..., Harness],
) -> None:
    def wrong_build(request: httpx.Request) -> httpx.Response:  # constructed fallback
        return httpx.Response(200, headers=CSV_HEADERS, content=CSV_BYTES)

    h = make(wrong_build)
    with pytest.raises(UnexpectedResponse, match=OTHER_BUILD):
        h.data.table(TABLE, OTHER_BUILD)
    assert _tree(h.cache) == set()


def test_html_with_status_200_is_refused_not_cached(make: Callable[..., Harness]) -> None:
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


def test_interrupted_download_leaves_nothing_under_the_final_name(
    make: Callable[..., Harness],
) -> None:
    h = make(_interrupted, max_attempts=3)
    with pytest.raises(SourceUnavailable):
        h.data.table(TABLE, BUILD)

    assert not h.data.table_path(TABLE, BUILD).exists()
    assert _tree(h.cache) == set(), "no partial file, temp file or sidecar may remain"
    assert len(h.requests) == 3


def test_interrupted_then_complete_download_caches_only_the_complete_body(
    make: Callable[..., Harness],
) -> None:
    calls = iter([_interrupted, recorded])

    h = make(lambda request: next(calls)(request))
    path = h.data.table(TABLE, BUILD)
    assert path.read_bytes() == CSV_BYTES, "the first attempt's 1000 bytes must not survive"
    assert len(h.sleeps) == 1


def test_a_crash_outside_the_source_still_cleans_the_temp_file(
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
def test_retryable_status_backs_off_then_succeeds(
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


def test_retry_after_is_honoured_and_capped(make: Callable[..., Harness]) -> None:
    responses = iter(["45", "86400"])

    def limited(request: httpx.Request) -> httpx.Response:
        try:
            return httpx.Response(429, headers={"retry-after": next(responses)})
        except StopIteration:
            return recorded(request)

    h = make(limited)
    h.data.table(TABLE, BUILD)
    assert h.sleeps == [45.0, 120.0]


def test_retries_are_bounded_and_nothing_is_cached(make: Callable[..., Harness]) -> None:
    h = make(lambda request: httpx.Response(503), max_attempts=4)
    with pytest.raises(SourceUnavailable, match="503") as caught:
        h.data.table(TABLE, BUILD)
    assert caught.value.attempts == 4
    assert len(h.requests) == 4
    assert len(h.sleeps) == 3, "no sleep after the last attempt"
    assert _tree(h.cache) == set()


def test_other_client_errors_are_not_retried(make: Callable[..., Harness]) -> None:
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
    build = h.data.resolve_build(FakeFlavor("a_product_the_listing_lacks", OTHER_BUILD))
    assert build.version == OTHER_BUILD


def test_resolve_build_raises_build_not_published(make: Callable[..., Harness]) -> None:
    h = make()
    with pytest.raises(BuildNotPublished) as caught:
        h.data.resolve_build(FakeFlavor("wow_classic_beta", UNKNOWN_BUILD))
    assert caught.value.version == UNKNOWN_BUILD
    assert caught.value.product == "wow_classic_beta"


def test_resolve_build_refetches_once_before_giving_up(make: Callable[..., Harness]) -> None:
    h = make()
    h.data.builds()
    with pytest.raises(BuildNotPublished):
        h.data.resolve_build(FakeFlavor("wow", UNKNOWN_BUILD))
    assert len(h.requests) == 2, "a build published since the cached listing must be found"


def test_resolve_build_without_a_version(make: Callable[..., Harness]) -> None:
    h = make()
    with pytest.raises(BuildNotPublished):
        h.data.resolve_build(FakeFlavor("wow", None))
    assert h.requests == []


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


def test_cache_inside_an_install_is_refused(tmp_path: Path) -> None:
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
