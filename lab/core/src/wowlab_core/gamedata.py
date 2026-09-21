"""gamedata: DB2 tables by build, from wago.tools, cached and never overwritten.

Spec: docs/LAB_PLAN.md §6.6. Decision: ADR-0022. Source notes and the URL
shapes the recordings show: docs/DATA_SOURCES.md.

The rules this module exists to hold:

- **L5.** The cache key is ``(table, full build string)``. A file that exists
  under its final name is never replaced, by another build, by a second fetch
  of the same key, or by a process that lost a race. Nothing prunes.
- **L1.** The cache lives under the user data directory. A cache directory
  that is, or resolves through a symlink to, somewhere inside an install is
  refused, at construction and again before every directory creation and
  write.
- **ADR-0012.** This is the only network client in the library and no test
  exercises it live; tests inject a transport that replays recordings.

Downloads stream into a temp name in the cache directory and are published
under the final name only when complete, so an interrupted download never
leaves a partial file there. Publishing is a hard link, which fails
atomically when the name is taken. There is deliberately no POSIX fallback:
without hard links the only portable primitive left is ``rename``, which
replaces its target, and no lock file protects against a writer that does
not take the lock. A cache on such a filesystem is refused with
``CacheLocationError`` rather than risk L5, and it is refused before any
request: hard-link support is probed with a throwaway file when the table's
directory is prepared. On Windows ``rename`` refuses an existing target, so
it is used when hard links are unavailable.

Bodies are bounded: decoded bytes are counted while streaming (gzip is
inflated here, in bounded steps, not by the HTTP library), a declared
``Content-Length`` over the cap is refused up front, a gzip body with anything
after its first member is refused (the recording shows single-member bodies;
caching the first member alone would be a truncated table for good), and
each download has one wall-clock deadline across all its attempts. A
response that fails any check is never cached.

Known limits, accepted:

- The sidecar is written only by the process that published the table, right
  after publishing, so it always describes the bytes on disk. A crash between
  the two leaves a valid table with no sidecar, permanently: a later hit does
  not invent a fetch record it does not have. ``sidecar()`` returns ``None``.
- Temp files (``.<name>.<hex>.part``) left by a killed process are never
  swept; nothing in this cache is pruned implicitly (ADR-0022). They never
  carry a final name and are never read.
"""

from __future__ import annotations

import contextlib
import csv
import errno
import hashlib
import http.cookiejar
import json
import os
import re
import threading
import time
import uuid
import zlib
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import BinaryIO, NamedTuple, Protocol

import httpx
import platformdirs
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from wowlab_core import __version__

__all__ = [
    "DOWNLOAD_DEADLINE_SECONDS",
    "MAX_BUILDS_BYTES",
    "MAX_TABLE_BYTES",
    "Build",
    "BuildNotPublished",
    "CacheLocationError",
    "GameData",
    "GameDataError",
    "HasVersion",
    "MalformedTable",
    "Sidecar",
    "Source",
    "SourceUnavailable",
    "TableNotPublished",
    "UnexpectedResponse",
    "WagoSource",
    "builds",
    "default_cache_dir",
    "resolve_build",
    "rows",
    "table",
]

REPO_URL = "https://github.com/sandgraal/wowlab"
USER_AGENT = f"wowlab/{__version__} (+{REPO_URL}; local personal tool; cached by build)"
WAGO_BASE_URL = "https://wago.tools"

# Decoded-body caps. The recorded listing is about 0.5 MiB; the largest tables
# the community exports are in the low hundreds of MiB.
MAX_BUILDS_BYTES = 16 * 1024 * 1024
MAX_TABLE_BYTES = 1024 * 1024 * 1024
# One download, all attempts and backoff included. Requests are serialised, so
# this is also the longest one slow peer can hold the others up.
DOWNLOAD_DEADLINE_SECONDS = 1800.0

_TABLE_NAME = re.compile(r"[A-Za-z0-9_]+")
_BUILD_STRING = re.compile(r"[0-9]+(\.[0-9]+){3}")
_DISPOSITION_FILENAME = re.compile(r'(?:^|;)\s*filename="([^"]*)"', re.IGNORECASE)
_INSTALL_MARKER = ".build.info"  # what makes a directory an install (LAB_PLAN §6.1)
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_ASCII_DIGITS = re.compile(r"[0-9]+")
# What a filesystem says when it has no hard links. Anything else (EACCES,
# ENOSPC, EMLINK, ...) is a different problem and is raised as itself.
_NO_HARD_LINKS = frozenset({errno.ENOTSUP, errno.EOPNOTSUPP, errno.EPERM, errno.ENOSYS})
_CHUNK = 1 << 16


# ─── errors ──────────────────────────────────────────────────────────────────


class GameDataError(Exception):
    """Base class for everything this module raises on purpose."""


class BuildNotPublished(GameDataError):  # noqa: N818 - name fixed by docs/LAB_PLAN.md §6.6
    """There is no published build to use: the source does not list the
    version, or the flavor has no version to look up."""

    def __init__(self, version: str | None, product: str | None = None) -> None:
        self.version = version
        self.product = product
        where = f" (product {product!r})" if product else ""
        if version is None:
            message = (
                f"flavor{where} has no version in .build.info, so there is no build to look up"
            )
        else:
            message = f"build {version!r}{where} is not published by the game data source"
        super().__init__(message)


class TableNotPublished(GameDataError):  # noqa: N818
    """The source has the build but not this table for it."""

    def __init__(self, table: str, build: str) -> None:
        self.table = table
        self.build = build
        super().__init__(f"table {table!r} is not published for build {build!r}")


class SourceUnavailable(GameDataError):  # noqa: N818
    """Retries were exhausted on 429, 5xx or transport failures."""

    def __init__(self, url: str, attempts: int, last: str) -> None:
        self.url = url
        self.attempts = attempts
        super().__init__(f"{url}: gave up after {attempts} attempts (last: {last})")


class UnexpectedResponse(GameDataError):  # noqa: N818
    """A response that must not be cached: wrong status, type, name, build,
    size, or one that took too long."""

    def __init__(self, url: str, detail: str) -> None:
        self.url = url
        super().__init__(f"{url}: {detail}")


class MalformedTable(GameDataError):  # noqa: N818
    """A cached CSV that cannot be read as ``dict[str, str]`` rows without
    losing something (L4)."""


class CacheLocationError(GameDataError):
    """The cache directory is somewhere it must never be (L1), or on a
    filesystem where a table cannot be published without risking L5."""


# ─── models ──────────────────────────────────────────────────────────────────


class Build(BaseModel):
    """One entry of the builds listing. Unknown fields are kept (L4)."""

    model_config = ConfigDict(frozen=True, extra="allow")

    product: str
    version: str  # full build string, e.g. "12.1.5.65432"
    # Kept as text: the listing gives "YYYY-MM-DD HH:MM:SS" with no zone.
    created_at: str | None = None
    build_config: str | None = None
    product_config: str | None = None
    cdn_config: str | None = None
    is_bgdl: bool = False


class Sidecar(BaseModel):
    """What was fetched, from where, when. Written beside each cached file."""

    model_config = ConfigDict(frozen=True)

    url: str
    fetched_at: datetime  # UTC
    size: int  # bytes on disk
    sha256: str
    table: str | None = None
    build: str | None = None
    source: str | None = None


_BUILDS_LISTING = TypeAdapter(dict[str, list[Build]])


class HasVersion(Protocol):
    """The part of ``install.Flavor`` that ``resolve_build`` reads."""

    @property
    def product(self) -> str: ...
    @property
    def version(self) -> str | None: ...


class Source(Protocol):
    """Where tables come from. wago.tools today; a local CASC reader later."""

    @property
    def name(self) -> str: ...

    def fetch_builds(self, dest: BinaryIO) -> str:
        """Write the builds listing (JSON: product -> list of builds) to
        ``dest`` and return where it came from (a URL)."""
        ...

    def fetch_table(self, table: str, build: str, dest: BinaryIO) -> str:
        """Write the table's CSV for exactly ``build`` to ``dest`` and return
        where it came from. Raise ``TableNotPublished`` if there is none."""
        ...


# ─── wago.tools ──────────────────────────────────────────────────────────────


class _NotFoundError(Exception):
    pass


class _RetryableError(Exception):
    pass


class _Expected(NamedTuple):
    content_type: str
    filename: str | None  # exact Content-Disposition filename, for tables
    max_bytes: int


def _no_cookies() -> http.cookiejar.CookieJar:
    """A jar that accepts nothing: the client is identified by its
    ``User-Agent`` and by nothing else (ADR-0022)."""
    return http.cookiejar.CookieJar(http.cookiejar.DefaultCookiePolicy(allowed_domains=[]))


class WagoSource:
    """Polite HTTP client for wago.tools.

    One connection, a descriptive ``User-Agent``, no cookies, no redirects,
    no parallel fetches (a lock serialises requests), exponential backoff on
    429, 5xx and transport errors, ``Retry-After`` (seconds or HTTP-date)
    honoured up to ``backoff_cap``.

    URL shapes come from the recordings under ``tests/fixtures/wago/``:
    ``/api/builds`` and ``/db2/<Table>/csv?build=<full build string>``.

    A table response is checked against the recording's
    ``Content-Disposition: attachment; filename="<Table>.<build>.csv"``: a
    filename naming another table or build is always refused, and so is a
    response without the header, because the recording shows it on every 200.
    ``require_content_disposition=False`` accepts a missing header (never a
    wrong one); nothing in the library uses it.
    """

    name = "wago.tools"

    def __init__(
        self,
        *,
        base_url: str = WAGO_BASE_URL,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic: Callable[[], float] = time.monotonic,
        max_attempts: int = 5,
        backoff_base: float = 2.0,
        backoff_cap: float = 120.0,
        timeout: float = 60.0,
        download_deadline: float = DOWNLOAD_DEADLINE_SECONDS,
        max_builds_bytes: int = MAX_BUILDS_BYTES,
        max_table_bytes: int = MAX_TABLE_BYTES,
        require_content_disposition: bool = True,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        parsed = httpx.URL(base_url)
        if parsed.scheme != "https" and not (
            parsed.scheme == "http" and parsed.host in _LOOPBACK_HOSTS
        ):
            raise ValueError(f"base_url must be https (http only for loopback): {base_url!r}")
        self._base_url = base_url.rstrip("/")
        self._sleep = sleep
        self._now = now
        self._monotonic = monotonic
        self._max_attempts = max_attempts
        self._backoff_base = backoff_base
        self._backoff_cap = backoff_cap
        self._download_deadline = download_deadline
        self._max_builds_bytes = max_builds_bytes
        self._max_table_bytes = max_table_bytes
        self._require_disposition = require_content_disposition
        self._lock = threading.Lock()
        self._client = httpx.Client(
            # gzip only: the body is inflated here, in bounded steps.
            headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"},
            cookies=_no_cookies(),
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
            timeout=timeout,
            follow_redirects=False,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> WagoSource:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def fetch_builds(self, dest: BinaryIO) -> str:
        url = f"{self._base_url}/api/builds"
        expected = _Expected("application/json", None, self._max_builds_bytes)
        try:
            return self._download(url, {}, dest, expected)
        except _NotFoundError as exc:
            raise UnexpectedResponse(url, "404 for the builds listing") from exc

    def fetch_table(self, table: str, build: str, dest: BinaryIO) -> str:
        _check_key(table, build)
        url = f"{self._base_url}/db2/{table}/csv"
        expected = _Expected("text/csv", f"{table}.{build}.csv", self._max_table_bytes)
        try:
            return self._download(url, {"build": build}, dest, expected)
        except _NotFoundError as exc:
            raise TableNotPublished(table, build) from exc

    def _download(
        self, url: str, params: dict[str, str], dest: BinaryIO, expected: _Expected
    ) -> str:
        with self._lock:
            last = "no attempt made"
            full_url = str(httpx.URL(url, params=params))
            # One deadline for the whole download, attempts and backoff included.
            deadline = self._monotonic() + self._download_deadline
            attempts = 0
            for attempt in range(self._max_attempts):
                retry_after: float | None = None
                dest.seek(0)
                dest.truncate()
                self._client.cookies.clear()
                if self._monotonic() > deadline:
                    last = f"{last}; passed the {self._download_deadline:.0f} s deadline"
                    break
                attempts += 1
                try:
                    with self._client.stream("GET", url, params=params) as response:
                        full_url = str(response.request.url)
                        status = response.status_code
                        if status == 404:
                            raise _NotFoundError(full_url)
                        if status == 429 or status >= 500:
                            last = f"HTTP {status}"
                            retry_after = _retry_after(
                                response.headers.get("retry-after"), self._now()
                            )
                        elif status != 200:
                            raise UnexpectedResponse(full_url, f"HTTP {status}")
                        else:
                            self._check_headers(full_url, response.headers, expected)
                            self._copy_body(full_url, response, dest, expected, deadline)
                            return full_url
                except (httpx.TransportError, httpx.DecodingError, _RetryableError) as exc:
                    last = f"{type(exc).__name__}: {exc}"
                except BaseException:
                    dest.seek(0)
                    dest.truncate()
                    raise
                if attempt + 1 < self._max_attempts:
                    delay = min(self._backoff_cap, self._backoff_base * 2**attempt)
                    if retry_after is not None:
                        delay = min(self._backoff_cap, max(delay, retry_after))
                    if self._monotonic() + delay > deadline:
                        last = (
                            f"{last}; no time left in the {self._download_deadline:.0f} s deadline"
                        )
                        break
                    self._sleep(delay)
            dest.seek(0)
            dest.truncate()
            raise SourceUnavailable(full_url, attempts, last)

    def _check_headers(self, url: str, headers: httpx.Headers, expected: _Expected) -> None:
        """Refuse a 200 that is not what was asked for; it would be cached
        forever (L5)."""
        got = headers.get("content-type", "")
        if not got.lower().startswith(expected.content_type):
            raise UnexpectedResponse(url, f"expected {expected.content_type}, got {got!r}")
        declared = headers.get("content-length")
        if (
            declared is not None
            and _ASCII_DIGITS.fullmatch(declared.strip())  # str.isdigit() accepts "²"
            and int(declared) > expected.max_bytes
        ):
            raise UnexpectedResponse(
                url, f"Content-Length {declared} is over the {expected.max_bytes}-byte cap"
            )
        if expected.filename is None:
            return
        disposition = headers.get("content-disposition")
        if disposition is None:
            if self._require_disposition:
                raise UnexpectedResponse(
                    url, f"no Content-Disposition to confirm {expected.filename!r}"
                )
            return
        match = _DISPOSITION_FILENAME.search(disposition)
        if match is None or match.group(1) != expected.filename:
            raise UnexpectedResponse(
                url, f"asked for {expected.filename!r}, got Content-Disposition {disposition!r}"
            )

    def _copy_body(
        self,
        url: str,
        response: httpx.Response,
        dest: BinaryIO,
        expected: _Expected,
        deadline: float,
    ) -> None:
        encoding = response.headers.get("content-encoding", "identity").strip().lower()
        chunks: Iterator[bytes]
        if response.is_stream_consumed:
            # Only a transport that hands over a pre-read response (a mock) gets
            # here; httpx has already decoded that body. A network response is
            # always an unread stream and takes the raw, bounded path below.
            chunks, encoding = response.iter_bytes(_CHUNK), "identity"
        else:
            # As the network delivers, so the deadline is looked at on every read.
            chunks = response.iter_raw()
        if encoding in ("", "identity"):
            inflater = None
        elif encoding == "gzip":
            inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
        else:
            raise UnexpectedResponse(url, f"unrequested Content-Encoding {encoding!r}")

        written = 0
        ended = False  # the gzip member is complete

        def emit(piece: bytes) -> None:
            nonlocal written
            written += len(piece)
            if written > expected.max_bytes:
                raise UnexpectedResponse(
                    url, f"body is over the {expected.max_bytes}-byte cap; not caching it"
                )
            dest.write(piece)

        for raw in chunks:
            if self._monotonic() > deadline:
                raise UnexpectedResponse(
                    url, f"download passed its {self._download_deadline:.0f} s deadline"
                )
            if inflater is None:
                emit(raw)
                continue
            if ended:
                raise UnexpectedResponse(url, "data after the end of the gzip body")
            data = raw
            while True:
                try:
                    piece = inflater.decompress(data, _CHUNK)
                except zlib.error as exc:
                    raise _RetryableError(f"corrupt gzip body: {exc}") from exc
                emit(piece)
                if inflater.eof:
                    ended = True
                    # A second member or junk. Inflating only the first member
                    # would cache a truncated table for good (L5); refuse.
                    if inflater.unused_data:
                        raise UnexpectedResponse(url, "data after the end of the gzip body")
                    break
                data = inflater.unconsumed_tail
                if not data and len(piece) < _CHUNK:
                    break
        if inflater is not None and not ended:
            raise _RetryableError("gzip body ended early")


def _retry_after(value: str | None, now: datetime) -> float | None:
    """``Retry-After`` as seconds from now: the delta form or the HTTP-date form."""
    if value is None:
        return None
    text = value.strip()
    try:
        seconds = float(text)
    except ValueError:
        try:
            when = parsedate_to_datetime(text)
        except (TypeError, ValueError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        seconds = (when - now).total_seconds()
        return max(seconds, 0.0)
    return seconds if seconds >= 0 else None


def _check_key(table: str, build: str) -> None:
    """Both parts become path segments and URL parts; keep them boring."""
    if not _TABLE_NAME.fullmatch(table):
        raise ValueError(f"not a table name: {table!r}")
    if not _BUILD_STRING.fullmatch(build):
        raise ValueError(f"not a full build string (a.b.c.d): {build!r}")


# ─── cache ───────────────────────────────────────────────────────────────────


def default_cache_dir() -> Path:
    """``<user data dir>/wowlab/gamedata`` (docs/SETUP.md)."""
    return platformdirs.user_data_path("wowlab") / "gamedata"


def _refuse_install(path: Path) -> None:
    """Raise if ``path``, with every symlink resolved, is inside an install."""
    resolved = path.resolve()
    for candidate in (resolved, *resolved.parents):
        if (candidate / _INSTALL_MARKER).exists():
            raise CacheLocationError(
                f"{path} is inside a game install ({candidate}); "
                "the game data cache never lives in an install (L1)"
            )


def _writable_dir(path: Path, *, publishes_tables: bool = False) -> None:
    """Create ``path`` for writing, refusing an install before the mkdir and
    again after it (a symlink anywhere along the way resolves differently
    once the directory exists). A directory tables are published into must
    also be able to publish one without replacing (``_refuse_no_hard_links``);
    finding that out here means a refused location costs the source nothing.
    """
    _refuse_install(path)
    path.mkdir(parents=True, exist_ok=True)
    _refuse_install(path)
    if publishes_tables:
        _refuse_no_hard_links(path)


def _no_hard_links_error(directory: Path, cause: OSError) -> CacheLocationError:
    return CacheLocationError(
        f"{directory}: this filesystem does not support hard links ({cause}), so a table "
        "cannot be published there without risking the replacement of a cached one (L5). "
        "Choose a cache directory on a filesystem with hard links: pass "
        "GameData(cache_dir=...), or move the user data directory off this volume."
    )


def _refuse_no_hard_links(directory: Path) -> None:
    """Link a throwaway file inside ``directory``; refuse the location if the
    filesystem cannot. Windows is exempt: its ``rename`` refuses an existing
    target, which is all publishing needs."""
    if os.name == "nt":
        return
    probe = directory / f".hardlink-probe.{uuid.uuid4().hex}.part"
    linked = probe.with_name(probe.name + ".link")
    try:
        probe.touch()
        try:
            linked.hardlink_to(probe)
        except OSError as exc:
            if exc.errno in _NO_HARD_LINKS:
                raise _no_hard_links_error(directory, exc) from exc
            raise
    finally:
        linked.unlink(missing_ok=True)
        probe.unlink(missing_ok=True)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class GameData:
    """Tables and builds, from a ``Source``, through the never-overwrite cache.

    The builds listing is cached for ``builds_ttl``. A lookup that misses
    against a cached listing refetches it only if the listing is older than
    ``builds_min_refresh``: a build published a few minutes ago is found, and
    repeated lookups of a build that is not published yet (the normal state
    on a beta, where the client patches before the source publishes) cost one
    listing download per interval, not one per call.
    """

    def __init__(
        self,
        source: Source | None = None,
        *,
        cache_dir: Path | None = None,
        builds_ttl: timedelta = timedelta(hours=1),
        builds_min_refresh: timedelta = timedelta(minutes=5),
        now: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._cache_dir = cache_dir if cache_dir is not None else default_cache_dir()
        _refuse_install(self._cache_dir)
        self._source: Source = source if source is not None else WagoSource()
        self._builds_ttl = builds_ttl
        self._builds_min_refresh = builds_min_refresh
        self._now = now

    @property
    def cache_dir(self) -> Path:
        return self._cache_dir

    # builds ---------------------------------------------------------------

    def builds(self, *, refresh: bool = False) -> dict[str, list[Build]]:
        """Products and their builds. Cached for ``builds_ttl``.

        Each product's list arrives sorted by version, descending (compared
        numerically per component, not as text), and a product code is reused
        across game versions, so neither position
        nor the highest version means "newest". Nothing here reads "latest"
        from the listing.
        """
        if not refresh:
            cached = self._cached_builds()
            if cached is not None:
                return cached[0]
        return self._fetch_builds()

    def resolve_build(self, flavor: HasVersion) -> Build:
        """The published build matching an installed flavor's version.

        Prefers the entry under the flavor's own product. The source's table
        endpoint is keyed by version string alone, so a version listed only
        under another product still selects the same export, and such an
        entry is accepted. From a cross-product match only ``.version``
        describes the installed flavor; ``product`` and the config hashes
        describe the other product's build and differ.
        """
        version = flavor.version
        if version is None:
            raise BuildNotPublished(None, flavor.product)
        product = flavor.product
        found = self._lookup(lambda listing: _find_build(listing, product, version))
        if found is None:
            raise BuildNotPublished(version, product)
        return found

    def _lookup[T](self, find: Callable[[dict[str, list[Build]]], T | None]) -> T | None:
        cached = self._cached_builds()
        if cached is not None:
            listing, fetched_at = cached
            found = find(listing)
            if found is not None or self._now() - fetched_at < self._builds_min_refresh:
                return found
        return find(self._fetch_builds())

    def _builds_paths(self) -> tuple[Path, Path]:
        path = self._cache_dir / "builds.json"
        return path, path.with_name("builds.json.meta.json")

    def _cached_builds(self) -> tuple[dict[str, list[Build]], datetime] | None:
        path, meta_path = self._builds_paths()
        try:
            meta = Sidecar.model_validate_json(meta_path.read_bytes())
            if path.stat().st_size > MAX_BUILDS_BYTES:
                return None
            raw = path.read_bytes()
        except (OSError, ValidationError):
            return None
        age = self._now() - meta.fetched_at
        if age < timedelta(0) or age >= self._builds_ttl:
            return None
        if hashlib.sha256(raw).hexdigest() != meta.sha256:
            return None
        try:
            return _BUILDS_LISTING.validate_json(raw), meta.fetched_at
        except ValidationError:
            return None

    def _fetch_builds(self) -> dict[str, list[Build]]:
        # The listing is not build-keyed data: it is replaced on refresh.
        path, meta_path = self._builds_paths()
        _writable_dir(path.parent)
        tmp = _temp_beside(path)
        try:
            with tmp.open("w+b") as handle:
                url = self._source.fetch_builds(handle)
                handle.flush()
                handle.seek(0)
                raw = handle.read(MAX_BUILDS_BYTES + 1)
            if len(raw) > MAX_BUILDS_BYTES:
                raise UnexpectedResponse(url, f"listing is over the {MAX_BUILDS_BYTES}-byte cap")
            try:
                listing = _BUILDS_LISTING.validate_json(raw)
            except ValidationError as exc:
                raise UnexpectedResponse(url, f"builds listing did not validate: {exc}") from exc
            meta = Sidecar(
                url=url,
                fetched_at=self._now(),
                size=len(raw),
                sha256=hashlib.sha256(raw).hexdigest(),
                source=self._source.name,
            )
            _refuse_install(path.parent)
            tmp.replace(path)
            _write_json_replacing(meta_path, meta)
            return listing
        finally:
            tmp.unlink(missing_ok=True)

    # tables ---------------------------------------------------------------

    def table_path(self, name: str, build: str) -> Path:
        """Where ``(name, build)`` is or would be cached. No I/O."""
        _check_key(name, build)
        return self._cache_dir / "tables" / build / f"{name}.csv"

    def sidecar(self, name: str, build: str) -> Sidecar | None:
        """The fetch record beside a cached table, if there is one."""
        path = _sidecar_path(self.table_path(name, build))
        try:
            return Sidecar.model_validate_json(path.read_bytes())
        except (OSError, ValidationError):
            return None

    def table(self, name: str, build: str) -> Path:
        """Path to the cached CSV for ``name`` at exactly ``build``.

        A file already under that name is returned untouched, whatever it
        holds (L5). Otherwise the table is downloaded to a temp name beside
        it and published without overwriting.
        """
        final = self.table_path(name, build)
        if final.exists():
            return final
        _writable_dir(final.parent, publishes_tables=True)
        tmp = _temp_beside(final)
        try:
            digest = hashlib.sha256()
            with tmp.open("w+b") as handle:
                try:
                    url = self._source.fetch_table(name, build, handle)
                except TableNotPublished:
                    if not self._lookup(lambda listing: _any_version(listing, build) or None):
                        raise BuildNotPublished(build) from None
                    raise
                handle.flush()
                handle.seek(0)
                size = 0
                while chunk := handle.read(_CHUNK):
                    digest.update(chunk)
                    size += len(chunk)
            if size == 0:
                raise UnexpectedResponse(url, "empty body; not caching it")
            if size > MAX_TABLE_BYTES:
                raise UnexpectedResponse(url, f"table is over the {MAX_TABLE_BYTES}-byte cap")
            fetched_at = self._now()
            _refuse_install(final.parent)
            if _publish_without_overwrite(tmp, final):
                meta = Sidecar(
                    url=url,
                    fetched_at=fetched_at,
                    size=size,
                    sha256=digest.hexdigest(),
                    table=name,
                    build=build,
                    source=self._source.name,
                )
                _write_json_once(_sidecar_path(final), meta)
            return final
        finally:
            tmp.unlink(missing_ok=True)

    def rows(self, name: str, build: str) -> Iterator[dict[str, str]]:
        """Rows of the table as ``dict[str, str]``. No type coercion."""
        path = self.table(name, build)
        return _iter_rows(path)


def _any_version(listing: dict[str, list[Build]], version: str) -> bool:
    return any(b.version == version for entries in listing.values() for b in entries)


def _find_build(listing: dict[str, list[Build]], product: str, version: str) -> Build | None:
    for entry in listing.get(product, []):
        if entry.version == version:
            return entry
    for entries in listing.values():
        for entry in entries:
            if entry.version == version:
                return entry
    return None


def _decoded_lines(path: Path, handle: BinaryIO) -> Iterator[str]:
    """Physical lines as text, line endings kept. Decoding here, not in the
    file object, is what gives bad UTF-8 a line and a byte offset."""
    offset = 0
    for number, line in enumerate(handle, start=1):
        try:
            yield line.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise MalformedTable(
                f"{path}: line {number}, byte offset {offset + exc.start}: not UTF-8 ({exc.reason})"
            ) from exc
        offset += len(line)


def _iter_rows(path: Path) -> Iterator[dict[str, str]]:
    with path.open("rb") as handle:
        reader = csv.reader(_decoded_lines(path, handle), strict=True)
        header: list[str] | None = None
        while True:
            try:
                record = next(reader)
            except StopIteration:
                return
            except csv.Error as exc:
                raise MalformedTable(f"{path}: line {reader.line_num}: {exc}") from exc
            if header is None:
                header = record
                repeated = sorted({name for name in header if header.count(name) > 1})
                if repeated:
                    raise MalformedTable(
                        f"{path}: line {reader.line_num}: column names repeat: {repeated}; "
                        "a dict row would drop a column"
                    )
                continue
            if len(record) != len(header):
                raise MalformedTable(
                    f"{path}: line {reader.line_num}: {len(record)} fields, "
                    f"header has {len(header)}"
                )
            yield dict(zip(header, record, strict=True))


def _sidecar_path(table_path: Path) -> Path:
    return table_path.with_name(table_path.name + ".json")


def _temp_beside(final: Path) -> Path:
    return final.with_name(f".{final.name}.{uuid.uuid4().hex}.part")


def _publish_without_overwrite(tmp: Path, final: Path) -> bool:
    """Give ``tmp``'s complete content the name ``final`` unless it is taken.

    A hard link fails atomically if the name exists (``EEXIST``: the race
    was lost). If the filesystem has no hard links: Windows ``rename`` also
    refuses an existing target, so it is used there; POSIX ``rename``
    replaces its target, so there is nothing safe to fall back to and the
    location is refused (normally already done by ``_refuse_no_hard_links``
    before the download). Any other error is raised as what it is.
    Returns whether this call published the file.
    """
    try:
        final.hardlink_to(tmp)
    except FileExistsError:
        return False
    except OSError as exc:
        if exc.errno not in _NO_HARD_LINKS:
            raise
        if final.exists():
            return False
        if os.name != "nt":
            raise _no_hard_links_error(final.parent, exc) from exc
        try:
            tmp.rename(final)
        except FileExistsError:
            return False
    return True


def _write_json_once(path: Path, model: BaseModel) -> None:
    tmp = _temp_beside(path)
    try:
        tmp.write_text(_dump(model), encoding="utf-8")
        _publish_without_overwrite(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _write_json_replacing(path: Path, model: BaseModel) -> None:
    tmp = _temp_beside(path)
    try:
        tmp.write_text(_dump(model), encoding="utf-8")
        tmp.replace(path)
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)


def _dump(model: BaseModel) -> str:
    return json.dumps(model.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"


# ─── module-level convenience over the default source and cache ─────────────

_default: GameData | None = None
_default_lock = threading.Lock()


def _default_gamedata() -> GameData:
    global _default
    with _default_lock:
        if _default is None:
            _default = GameData()
        return _default


def builds() -> dict[str, list[Build]]:
    return _default_gamedata().builds()


def table(name: str, build: str) -> Path:
    return _default_gamedata().table(name, build)


def rows(name: str, build: str) -> Iterator[dict[str, str]]:
    return _default_gamedata().rows(name, build)


def resolve_build(flavor: HasVersion) -> Build:
    return _default_gamedata().resolve_build(flavor)
