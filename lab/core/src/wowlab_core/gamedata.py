"""gamedata: DB2 tables by build, from wago.tools, cached and never overwritten.

Spec: docs/LAB_PLAN.md §6.6. Decision: ADR-0022. Source notes and the URL
shapes the recordings show: docs/DATA_SOURCES.md.

The rules this module exists to hold:

- **L5.** The cache key is ``(table, full build string)``. A file that exists
  under its final name is never replaced, by another build, by a second fetch
  of the same key, or by a process that lost a race. Nothing prunes.
- **L1.** The cache lives under the user data directory. A cache directory
  inside an install is refused.
- **ADR-0012.** This is the only network client in the library and no test
  exercises it live; tests inject a transport that replays recordings.

Downloads stream into a temp name in the cache directory and are published
with a no-overwrite link, so an interrupted download never leaves a partial
file under the final name. The sidecar is written only by the process that
published the table, so it always describes the bytes on disk.
"""

from __future__ import annotations

import contextlib
import csv
import hashlib
import json
import re
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import BinaryIO, Protocol

import httpx
import platformdirs
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from wowlab_core import __version__

__all__ = [
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

_TABLE_NAME = re.compile(r"[A-Za-z0-9_]+")
_BUILD_STRING = re.compile(r"[0-9]+(\.[0-9]+){3}")
_INSTALL_MARKER = ".build.info"  # what makes a directory an install (LAB_PLAN §6.1)


# ─── errors ──────────────────────────────────────────────────────────────────


class GameDataError(Exception):
    """Base class for everything this module raises on purpose."""


class BuildNotPublished(GameDataError):  # noqa: N818 - name fixed by docs/LAB_PLAN.md §6.6
    """The source does not list this build (yet, or at all)."""

    def __init__(self, version: str | None, product: str | None = None) -> None:
        self.version = version
        self.product = product
        what = f"build {version!r}" if version else "a flavor with no version"
        where = f" (product {product!r})" if product else ""
        super().__init__(f"{what}{where} is not published by the game data source")


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
    """A response that must not be cached: wrong status, type, or build."""

    def __init__(self, url: str, detail: str) -> None:
        self.url = url
        super().__init__(f"{url}: {detail}")


class MalformedTable(GameDataError):  # noqa: N818
    """A cached CSV whose row does not match its header."""


class CacheLocationError(GameDataError):
    """The cache directory is somewhere it must never be (L1)."""


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


class WagoSource:
    """Polite HTTP client for wago.tools.

    One connection, a descriptive ``User-Agent``, no parallel fetches (a lock
    serialises requests), exponential backoff on 429, 5xx and transport
    errors, ``Retry-After`` honoured up to ``backoff_cap``.

    URL shapes come from the recordings under ``tests/fixtures/wago/``:
    ``/api/builds`` and ``/db2/<Table>/csv?build=<full build string>``.
    """

    name = "wago.tools"

    def __init__(
        self,
        *,
        base_url: str = WAGO_BASE_URL,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        max_attempts: int = 5,
        backoff_base: float = 2.0,
        backoff_cap: float = 120.0,
        timeout: float = 60.0,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        self._base_url = base_url.rstrip("/")
        self._sleep = sleep
        self._max_attempts = max_attempts
        self._backoff_base = backoff_base
        self._backoff_cap = backoff_cap
        self._lock = threading.Lock()
        self._client = httpx.Client(
            headers={"User-Agent": USER_AGENT},
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
        try:
            return self._download(url, {}, dest, content_type="application/json", build=None)
        except _NotFoundError as exc:
            raise UnexpectedResponse(url, "404 for the builds listing") from exc

    def fetch_table(self, table: str, build: str, dest: BinaryIO) -> str:
        _check_key(table, build)
        url = f"{self._base_url}/db2/{table}/csv"
        try:
            return self._download(url, {"build": build}, dest, content_type="text/csv", build=build)
        except _NotFoundError as exc:
            raise TableNotPublished(table, build) from exc

    def _download(
        self,
        url: str,
        params: dict[str, str],
        dest: BinaryIO,
        *,
        content_type: str,
        build: str | None,
    ) -> str:
        with self._lock:
            last = "no attempt made"
            full_url = str(httpx.URL(url, params=params))
            for attempt in range(self._max_attempts):
                retry_after: float | None = None
                dest.seek(0)
                dest.truncate()
                try:
                    with self._client.stream("GET", url, params=params) as response:
                        full_url = str(response.request.url)
                        status = response.status_code
                        if status == 404:
                            raise _NotFoundError(full_url)
                        if status == 429 or status >= 500:
                            last = f"HTTP {status}"
                            retry_after = _retry_after(response.headers.get("retry-after"))
                        elif status != 200:
                            raise UnexpectedResponse(full_url, f"HTTP {status}")
                        else:
                            _check_headers(full_url, response.headers, content_type, build)
                            for chunk in response.iter_bytes():
                                dest.write(chunk)
                            return full_url
                except httpx.TransportError as exc:
                    last = f"{type(exc).__name__}: {exc}"
                if attempt + 1 < self._max_attempts:
                    delay = min(self._backoff_cap, self._backoff_base * 2**attempt)
                    if retry_after is not None:
                        delay = min(self._backoff_cap, max(delay, retry_after))
                    self._sleep(delay)
            dest.seek(0)
            dest.truncate()
            raise SourceUnavailable(full_url, self._max_attempts, last)


def _retry_after(value: str | None) -> float | None:
    """Seconds form only; the HTTP-date form falls back to our own backoff."""
    if value is None:
        return None
    try:
        seconds = float(value.strip())
    except ValueError:
        return None
    return seconds if seconds >= 0 else None


def _check_headers(url: str, headers: httpx.Headers, content_type: str, build: str | None) -> None:
    """Refuse a 200 that is not what was asked for; it would be cached forever.

    The recorded CSV response names its build in ``Content-Disposition``
    (``filename="<Table>.<build>.csv"``). If the header is present and names
    another build, the server fell back to a different build: refuse (L5).
    """
    got = headers.get("content-type", "")
    if not got.lower().startswith(content_type):
        raise UnexpectedResponse(url, f"expected {content_type}, got {got!r}")
    disposition = headers.get("content-disposition")
    if build is not None and disposition and f".{build}.csv" not in disposition:
        raise UnexpectedResponse(url, f"asked for build {build}, got {disposition!r}")


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
    resolved = path.resolve()
    for candidate in (resolved, *resolved.parents):
        if (candidate / _INSTALL_MARKER).exists():
            raise CacheLocationError(
                f"{path} is inside a game install ({candidate}); "
                "the game data cache never lives in an install (L1)"
            )


def _utcnow() -> datetime:
    return datetime.now(UTC)


class GameData:
    """Tables and builds, from a ``Source``, through the never-overwrite cache."""

    def __init__(
        self,
        source: Source | None = None,
        *,
        cache_dir: Path | None = None,
        builds_ttl: timedelta = timedelta(hours=1),
        now: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._cache_dir = cache_dir if cache_dir is not None else default_cache_dir()
        _refuse_install(self._cache_dir)
        self._source: Source = source if source is not None else WagoSource()
        self._builds_ttl = builds_ttl
        self._now = now

    @property
    def cache_dir(self) -> Path:
        return self._cache_dir

    # builds ---------------------------------------------------------------

    def builds(self, *, refresh: bool = False) -> dict[str, list[Build]]:
        """Products and their builds. Cached for ``builds_ttl``.

        The listing's order is the source's; it is not "newest first" by
        date, so nothing here assumes an order.
        """
        if not refresh:
            cached = self._cached_builds()
            if cached is not None:
                return cached
        return self._fetch_builds()

    def resolve_build(self, flavor: HasVersion) -> Build:
        """The published build matching an installed flavor's version.

        Prefers the entry under the flavor's own product; the same version
        under another product is the same data and is accepted. A miss
        against a cached listing refetches once before giving up, because a
        build published five minutes ago is the common case.
        """
        version = flavor.version
        if version is None:
            raise BuildNotPublished(None, flavor.product)
        cached = self._cached_builds()
        if cached is not None:
            found = _find_build(cached, flavor.product, version)
            if found is not None:
                return found
        found = _find_build(self._fetch_builds(), flavor.product, version)
        if found is None:
            raise BuildNotPublished(version, flavor.product)
        return found

    def _builds_paths(self) -> tuple[Path, Path]:
        path = self._cache_dir / "builds.json"
        return path, path.with_name("builds.json.meta.json")

    def _cached_builds(self) -> dict[str, list[Build]] | None:
        path, meta_path = self._builds_paths()
        try:
            meta = Sidecar.model_validate_json(meta_path.read_bytes())
            raw = path.read_bytes()
        except (OSError, ValidationError):
            return None
        age = self._now() - meta.fetched_at
        if age < timedelta(0) or age >= self._builds_ttl:
            return None
        if hashlib.sha256(raw).hexdigest() != meta.sha256:
            return None
        try:
            return _BUILDS_LISTING.validate_json(raw)
        except ValidationError:
            return None

    def _fetch_builds(self) -> dict[str, list[Build]]:
        # The listing is not build-keyed data: it is replaced on refresh.
        path, meta_path = self._builds_paths()
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        tmp = _temp_beside(path)
        try:
            with tmp.open("w+b") as handle:
                url = self._source.fetch_builds(handle)
                handle.flush()
                handle.seek(0)
                raw = handle.read()
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
        final.parent.mkdir(parents=True, exist_ok=True)
        tmp = _temp_beside(final)
        try:
            digest = hashlib.sha256()
            with tmp.open("w+b") as handle:
                try:
                    url = self._source.fetch_table(name, build, handle)
                except TableNotPublished:
                    if not self._build_is_listed(build):
                        raise BuildNotPublished(build) from None
                    raise
                handle.flush()
                handle.seek(0)
                size = 0
                while chunk := handle.read(1 << 16):
                    digest.update(chunk)
                    size += len(chunk)
            if size == 0:
                raise UnexpectedResponse(url, "empty body; not caching it")
            fetched_at = self._now()
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

    def _build_is_listed(self, build: str) -> bool:
        cached = self._cached_builds()
        if cached is not None and _any_version(cached, build):
            return True
        return _any_version(self._fetch_builds(), build)


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


def _iter_rows(path: Path) -> Iterator[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle, strict=True)
        try:
            header = next(reader)
        except StopIteration:
            return
        for record in reader:
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

    A hard link fails atomically if the name exists, which ``rename`` does
    not on POSIX. Filesystems without hard links fall back to check-then-
    rename (on Windows that rename also refuses an existing target).
    Returns whether this call published the file.
    """
    try:
        final.hardlink_to(tmp)
    except FileExistsError:
        return False
    except OSError:
        if final.exists():
            return False
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
