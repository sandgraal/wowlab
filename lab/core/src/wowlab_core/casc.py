"""casc: the build config and the local index of the install's archives
(docs/LAB_PLAN.md §17.1 steps 1 and 2, ADR-0033, M15-03).

Formats: `docs/LAB_FORMATS.md` §10. The install's `Data/` is shared by every
product in the install; only the build config is per product.

1. **The build.** The flavor's product (`.flavor.info`, read by `install`)
   picks its `Active` row of `.build.info`. Exactly one such row is used; any
   other count is refused with `BuildRowError`, never guessed. That row's
   `Build Key` names the build config `Data/config/<k[0:2]>/<k[2:4]>/<k>`,
   `key = value` text whose MD5 is its own name (checked), with `root` (a
   content key) and `encoding` (a content key and an encoding key).
2. **The local index.** Every `Data/data/<bucket:2><version:8>.idx`, the
   newest version number per bucket (the client's `shmem`, which names the
   current one, is not read; whether the newest always wins is **[verify]**,
   §17.7), so each bucket's versions not taken are reported. Each file is a
   header (its size, a Jenkins `hashlittle` of it, version 7, the bucket,
   and the field-size spec `4/5/9/30`), then, at the next 16-byte boundary,
   the entries block (its size, a hash chained entry by entry with
   `hashlittle2`) of 18-byte entries: the first nine bytes of an encoding
   key, a five-byte big-endian field (ten bits of archive number, thirty of
   offset) and a four-byte little-endian size. Both hashes are checked and
   the spec is read and held to `4/5/9/30`, so a half-written or unknown
   file is refused (`CascMalformedError`, `CascUnsupportedError`), never
   read around.

Reads only (L1, ADR-0033): every file is opened read-only without following
a final link (`snapshot.read_regular_file`), read whole into memory with a
size bound, and closed before the next is opened, so no handle or mapping
outlives the call; nothing is created, locked or written anywhere, and
`shmem` and `index.lock.0` are never opened. A folder on the way (`Data`,
`Data/data`, `Data/config` and the key's two levels) that is a link is
refused rather than followed. Whether a handle held for one read can still
block the agent or the client on Windows is a §17.7 **[verify]**.

Nothing about a flavor is known here (L6): the product, the version and the
build key come from the install's own files.

The entries hash is the one cost that grows with the index (about a million
entries for an install). It is a chain, entry after entry, within a file,
but the sixteen files are independent, so `_entries_hashes` runs the sixteen
chains side by side, one 64-bit lane each, inside Python integers: one pass
of about 66,000 steps instead of a million (§17.1's target: the index in at
most 2 s).
"""

from __future__ import annotations

import bisect
import hashlib
import operator
import re
import stat
import struct
import time
from collections.abc import Sequence
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from wowlab_core import longpath, snapshot
from wowlab_core.install import BuildInfoRow, Flavor, Install, is_link

__all__ = [
    "BUCKETS",
    "KEY_BYTES",
    "BucketVersion",
    "BuildConfig",
    "BuildRowError",
    "CascError",
    "CascIndex",
    "CascIndexReport",
    "CascMalformedError",
    "CascMissingError",
    "CascStorage",
    "CascUnreadableError",
    "CascUnsupportedError",
    "IndexEntry",
    "IndexFile",
    "bucket_of",
    "build_config_path",
    "hashlittle",
    "hashlittle2",
    "load_index",
    "open_storage",
    "parse_build_config",
    "parse_index_file",
    "read_build_config",
    "select_build_row",
]

# The install layout (docs/LAB_FILE_MAP.md); these name no flavor.
_DATA = "Data"
_CONFIG = "config"
_DATA_DATA = "data"

BUCKETS = 16
KEY_BYTES = 9  # the first nine bytes of an encoding key
_INDEX_VERSION = 7
# (size bytes, offset bytes, key bytes, offset bits) as the header states them.
_SPEC = (4, 5, KEY_BYTES, 30)
_ENTRY = _SPEC[0] + _SPEC[1] + _SPEC[2]  # 18
_OFFSET_BITS = _SPEC[3]
_OFFSET_MASK = (1 << _OFFSET_BITS) - 1
_HEADER_FIELDS = struct.Struct("<HBBBBBBQ")  # version, bucket, extra, spec x4, max archive size
_MAX_HEADER = 0x100  # a header hash size past this is refused as malformed
_INDEX_NAME = re.compile(r"([0-9a-f]{2})([0-9a-f]{8})\.idx")
_HEX_KEY = re.compile(r"[0-9a-f]{32}")

# Read bounds: the files at 70450 are 1,245,184 bytes (each index) and 59,537
# bytes (the build config); a file far past these is refused, not read.
_INDEX_LIMIT = 64 * 1024 * 1024
_CONFIG_LIMIT = 4 * 1024 * 1024


# ─── errors ──────────────────────────────────────────────────────────────────


class CascError(Exception):
    """Base class for everything this module raises on purpose. `path` is
    the file or folder concerned, when there is one."""

    def __init__(self, message: str, path: Path | None = None) -> None:
        super().__init__(message)
        self.path = path


class BuildRowError(CascError):
    """The flavor's product does not have exactly one `Active` row in
    `.build.info` (`count` says how many it has), or the flavor is not
    there, names no product, or its row has no usable build key."""

    def __init__(self, message: str, product: str, count: int, path: Path | None = None) -> None:
        super().__init__(message, path)
        self.product = product
        self.count = count


class CascMissingError(CascError):
    """A file or folder the read needs is not there."""


class CascUnreadableError(CascError):
    """A file or folder is there but cannot be read as one: the operating
    system refused, it is a link, it is not a regular file (or folder), or it
    is larger than the bound for its kind."""


class CascMalformedError(CascError):
    """A file's content is not what its format says: a hash that does not
    match, a size past the end, a key that is not hex, a missing field."""


class CascUnsupportedError(CascError):
    """A well-formed file in a version or layout this reader does not know
    (an index version other than 7, a spec other than `4/5/9/30`)."""


# ─── models ──────────────────────────────────────────────────────────────────


class BuildConfig(BaseModel):
    """The build config named by the build key. `pairs` holds every
    `key = value` line in file order, as written after trimming; `root` and
    `encoding_*` are its two keys this reader needs, lowercase hex."""

    model_config = ConfigDict(frozen=True)

    key: str
    root: str
    encoding_ckey: str
    encoding_ekey: str
    pairs: tuple[tuple[str, str], ...]


class IndexEntry(BaseModel):
    """Where an encoding key's data is: archive `data.<archive:03d>`, the
    byte `offset` in it, and `size` bytes (the record header included)."""

    model_config = ConfigDict(frozen=True)

    archive: int
    offset: int
    size: int


class BucketVersion(BaseModel):
    """The `.idx` file taken for one bucket, and the older ones not taken."""

    model_config = ConfigDict(frozen=True)

    bucket: int
    version: int
    file: str
    entries: int
    header_size: int
    header_hash: int
    extra_bytes: int  # the header's byte after the bucket; 0 at 70450
    entries_hash: int
    max_archive_size: int
    not_taken: tuple[int, ...]  # older version numbers of this bucket, newest first


class CascIndexReport(BaseModel):
    """What opening one flavor's build found."""

    model_config = ConfigDict(frozen=True)

    product: str
    version: str
    build: int | None
    build_key: str
    root: str
    encoding_ckey: str
    encoding_ekey: str
    bucket_count: int
    entry_count: int
    versions: tuple[BucketVersion, ...]  # by bucket
    # Buckets with more than one version on disk, where the newest was taken
    # (**[verify]**: `shmem` is not read).
    newest_taken: tuple[int, ...]
    index_seconds: float  # reading and checking every `.idx`
    open_seconds: float  # the whole open: the build config and the index


class IndexFile:
    """One parsed, checked `.idx` file: its facts and its entries."""

    __slots__ = (
        "bucket",
        "entries",
        "entries_hash",
        "extra",
        "header_hash",
        "header_size",
        "keys",
        "max_size",
        "version",
    )

    def __init__(
        self,
        *,
        bucket: int,
        version: int,
        header_size: int,
        header_hash: int,
        extra: int,
        entries_hash: int,
        max_size: int,
        entries: bytes,
    ) -> None:
        self.bucket = bucket
        self.version = version
        self.header_size = header_size
        self.extra = extra
        self.header_hash = header_hash
        self.entries_hash = entries_hash
        self.max_size = max_size
        self.entries = entries
        self.keys: list[bytes] = []  # filled when the file is checked


class CascIndex:
    """The lookup from the first nine bytes of an encoding key to where its
    data is, over every bucket taken. Holds bytes only; no file is open."""

    def __init__(self, files: Sequence[IndexFile]) -> None:
        self._files: dict[int, IndexFile] = {f.bucket: f for f in files}

    def __len__(self) -> int:
        return sum(len(f.keys) for f in self._files.values())

    @property
    def buckets(self) -> tuple[int, ...]:
        return tuple(sorted(self._files))

    def find(self, ekey: bytes) -> IndexEntry | None:
        """Where `ekey` (an encoding key, or at least its first nine bytes)
        is, or `None` when the local index does not list it. The file
        searched is the one of `bucket_of(ekey)`. (Each file at 70450 also
        holds 65 entries whose key's bucket is the one before it, each of
        size 30 at offset 30 times the file's bucket in one archive: the
        archives' segment headers by their look, **[verify]**; no content
        key is among them, and this lookup does not find them.)"""
        if len(ekey) < KEY_BYTES:
            raise ValueError(f"an index key is {KEY_BYTES} bytes; got {len(ekey)}")
        key = bytes(ekey[:KEY_BYTES])
        file = self._files.get(bucket_of(key))
        if file is None:
            return None
        i = bisect.bisect_left(file.keys, key)
        if i == len(file.keys) or file.keys[i] != key:
            return None
        return _entry_at(file.entries, i)

    def entry_at(self, bucket: int, i: int) -> tuple[bytes, IndexEntry]:
        """The `i`-th entry of `bucket` in key order (its key and where it
        is); for derived fixtures and tests."""
        file = self._files[bucket]
        return file.keys[i], _entry_at(file.entries, i)

    def count(self, bucket: int) -> int:
        """How many entries the file taken for `bucket` holds."""
        file = self._files.get(bucket)
        return 0 if file is None else len(file.keys)

    def keys(self, bucket: int) -> tuple[bytes, ...]:
        """The nine-byte keys of `bucket`'s file, in key order."""
        file = self._files.get(bucket)
        return () if file is None else tuple(file.keys)


class CascStorage:
    """One flavor's build opened: its `.build.info` row, its build config,
    the local index and the report. Nothing in it holds a file open."""

    def __init__(
        self,
        root: Path,
        row: BuildInfoRow,
        config: BuildConfig,
        index: CascIndex,
        report: CascIndexReport,
    ) -> None:
        self.root = root
        self.row = row
        self.config = config
        self.index = index
        self.report = report


# ─── hashing ─────────────────────────────────────────────────────────────────

_M32 = 0xFFFFFFFF


def _rot(x: int, k: int) -> int:
    return ((x << k) | (x >> (32 - k))) & _M32


def hashlittle2(data: bytes, pc: int = 0, pb: int = 0) -> tuple[int, int]:
    """Bob Jenkins' lookup3 `hashlittle2` (public domain): `(pc, pb)` after
    hashing `data` from the given seeds. Written from the published
    algorithm; `hashlittle(data, seed)` is its first value with `pb = 0`."""
    n = len(data)
    a = b = c = (0xDEADBEEF + n + pc) & _M32
    c = (c + pb) & _M32
    off = 0
    while n > 12:
        x, y, z = struct.unpack_from("<3I", data, off)
        a = (a + x) & _M32
        b = (b + y) & _M32
        c = (c + z) & _M32
        a = (a - c) & _M32
        a ^= _rot(c, 4)
        c = (c + b) & _M32
        b = (b - a) & _M32
        b ^= _rot(a, 6)
        a = (a + c) & _M32
        c = (c - b) & _M32
        c ^= _rot(b, 8)
        b = (b + a) & _M32
        a = (a - c) & _M32
        a ^= _rot(c, 16)
        c = (c + b) & _M32
        b = (b - a) & _M32
        b ^= _rot(a, 19)
        a = (a + c) & _M32
        c = (c - b) & _M32
        c ^= _rot(b, 4)
        b = (b + a) & _M32
        n -= 12
        off += 12
    if n == 0:
        return c, b
    x, y, z = struct.unpack("<3I", bytes(data[off : off + n]) + bytes(12 - n))
    a = (a + x) & _M32
    b = (b + y) & _M32
    c = (c + z) & _M32
    c ^= b
    c = (c - _rot(b, 14)) & _M32
    a ^= c
    a = (a - _rot(c, 11)) & _M32
    b ^= a
    b = (b - _rot(a, 25)) & _M32
    c ^= b
    c = (c - _rot(b, 16)) & _M32
    a ^= c
    a = (a - _rot(c, 4)) & _M32
    b ^= a
    b = (b - _rot(a, 14)) & _M32
    c ^= b
    c = (c - _rot(b, 24)) & _M32
    return c, b


def hashlittle(data: bytes, seed: int = 0) -> int:
    """lookup3 `hashlittle`: the first value of `hashlittle2(data, seed, 0)`."""
    return hashlittle2(data, seed, 0)[0]


_LANE_BYTES = 8  # each chain runs in a 64-bit lane; its values stay below 2**57


def _entries_hashes(blocks: Sequence[bytes]) -> list[int]:
    """For each block of 18-byte entries, the `pc` of `hashlittle2` chained
    over its entries in order (each entry hashed with the last one's
    `(pc, pb)` as seeds, from `(0, 0)`): the index's entries hash.

    The blocks are independent, so they run side by side: lane `i` of every
    integer below is the 32-bit state of block `i` (bits `64i` to `64i+31`),
    and each step hashes the next entry of every block at once. An
    addition is masked back to 32 bits per lane; a subtraction first sets
    bit 32 of each lane, so no borrow crosses a lane; a rotation's two
    halves stay inside the lane (shifts of at most 25 bits) and are masked.
    Lanes whose block has ended keep running on zero bytes; their value was
    taken when their last entry was hashed. This equals `hashlittle2`
    applied entry by entry (tested against it)."""
    lanes = len(blocks)
    counts = [len(b) // _ENTRY for b in blocks]
    steps = max(counts, default=0)
    results = [0] * lanes
    if steps == 0:
        return results
    stride = lanes * _LANE_BYTES
    # words[j]: step k, lane i at byte (k * lanes + i) * 8, the j-th
    # little-endian word of that lane's k-th entry (the fifth is the last two
    # bytes, zero-padded, as lookup3 reads an 18-byte key's tail).
    words = [bytearray(steps * stride) for _ in range(5)]
    for lane, block in enumerate(blocks):
        n = counts[lane]
        if n == 0:
            continue
        for j in range(5):
            for i in range(4 if j < 4 else 2):
                start = lane * _LANE_BYTES + i
                words[j][start : start + n * stride : stride] = block[j * 4 + i :: _ENTRY]
    m = sum(_M32 << (64 * i) for i in range(lanes))
    h = sum(1 << (32 + 64 * i) for i in range(lanes))
    init = sum((0xDEADBEEF + _ENTRY) << (64 * i) for i in range(lanes))
    ends: dict[int, list[int]] = {}
    for lane, n in enumerate(counts):
        ends.setdefault(n, []).append(lane)
    w0, w1, w2, w3, w4 = (memoryview(w) for w in words)
    fb = int.from_bytes
    pc = pb = 0
    for k in range(steps):
        s = k * stride
        e = s + stride
        a = (init + pc) & m
        c = (a + pb) & m
        b = (a + fb(w1[s:e], "little")) & m
        a = (a + fb(w0[s:e], "little")) & m
        c = (c + fb(w2[s:e], "little")) & m
        # mix
        a = ((a | h) - c) & m
        a ^= ((c << 4) | (c >> 28)) & m
        c = (c + b) & m
        b = ((b | h) - a) & m
        b ^= ((a << 6) | (a >> 26)) & m
        a = (a + c) & m
        c = ((c | h) - b) & m
        c ^= ((b << 8) | (b >> 24)) & m
        b = (b + a) & m
        a = ((a | h) - c) & m
        a ^= ((c << 16) | (c >> 16)) & m
        c = (c + b) & m
        b = ((b | h) - a) & m
        b ^= ((a << 19) | (a >> 13)) & m
        a = (a + c) & m
        c = ((c | h) - b) & m
        c ^= ((b << 4) | (b >> 28)) & m
        b = (b + a) & m
        # the six-byte tail, then final
        a = (a + fb(w3[s:e], "little")) & m
        b = (b + fb(w4[s:e], "little")) & m
        c ^= b
        c = ((c | h) - (((b << 14) | (b >> 18)) & m)) & m
        a ^= c
        a = ((a | h) - (((c << 11) | (c >> 21)) & m)) & m
        b ^= a
        b = ((b | h) - (((a << 25) | (a >> 7)) & m)) & m
        c ^= b
        c = ((c | h) - (((b << 16) | (b >> 16)) & m)) & m
        a ^= c
        a = ((a | h) - (((c << 4) | (c >> 28)) & m)) & m
        b ^= a
        b = ((b | h) - (((a << 14) | (a >> 18)) & m)) & m
        c ^= b
        c = ((c | h) - (((b << 24) | (b >> 8)) & m)) & m
        pc, pb = c, b
        done = ends.get(k + 1)
        if done is not None:
            for lane in done:
                results[lane] = (pc >> (64 * lane)) & _M32
    return results


# ─── reading ─────────────────────────────────────────────────────────────────


def _check_dir(path: Path) -> None:
    """`path` is a folder and not a link (a link is never followed)."""
    try:
        st = longpath.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        raise CascMissingError(f"{path} is not there", path) from None
    except OSError as exc:
        raise CascUnreadableError(f"cannot read {path} ({exc.strerror or exc})", path) from exc
    if is_link(path, st):
        raise CascUnreadableError(f"{path} is a link; links are not followed", path)
    if not stat.S_ISDIR(st.st_mode):
        raise CascUnreadableError(f"{path} is not a folder", path)


def _read(path: Path, limit: int) -> bytes:
    """The bytes of `path`, read-only, whole, bounded, the handle closed."""
    try:
        return snapshot.read_regular_file(path, limit=limit)
    except FileNotFoundError:
        raise CascMissingError(f"{path} is not there", path) from None
    except snapshot.UnsafeReadError as exc:
        raise CascUnreadableError(f"cannot read {path} ({exc})", path) from exc
    except OSError as exc:  # refused, or a link (ELOOP)
        raise CascUnreadableError(f"cannot read {path} ({exc.strerror or exc})", path) from exc


def _build_number(version: str) -> int | None:
    last = version.rsplit(".", 1)[-1].strip()
    return int(last) if last.isascii() and last.isdecimal() else None


def select_build_row(install: Install, flavor: Flavor | str) -> tuple[Flavor, BuildInfoRow]:
    """The flavor (a `Flavor` of `install`, or its folder name) and its
    product's one `Active` row of `.build.info`. Refused with
    `BuildRowError` when the flavor is not in the install, names no
    product, or its product has no `Active` row or more than one."""
    if isinstance(flavor, str):
        found = next((f for f in install.flavors if f.folder == flavor), None)
        if found is None:
            raise BuildRowError(
                f"no flavor folder {flavor!r} in {install.root}", "", 0, install.root
            )
        flavor = found
    product = flavor.product
    if not product:
        raise BuildRowError(
            f"the flavor folder {flavor.folder!r} names no product in its .flavor.info",
            "",
            0,
            flavor.path,
        )
    rows = [r for r in install.products if (r.product or "").strip() == product and r.is_active]
    if len(rows) != 1:
        raise BuildRowError(
            f".build.info has {len(rows)} Active rows for the product {product!r}; "
            "exactly one is needed, and none is guessed",
            product,
            len(rows),
            install.root / ".build.info",
        )
    return flavor, rows[0]


def _hex_key(text: str | None, what: str, path: Path | None) -> str:
    key = (text or "").strip().lower()
    if not _HEX_KEY.fullmatch(key):
        raise CascMalformedError(f"{what} is not a 32-digit hex key: {text!r}", path)
    return key


def build_config_path(root: Path, build_key: str) -> Path:
    """`Data/config/<k[0:2]>/<k[2:4]>/<k>` under the install `root`."""
    key = _hex_key(build_key, "the build key", None)
    return root / _DATA / _CONFIG / key[0:2] / key[2:4] / key


def parse_build_config(data: bytes, key: str, path: Path | None = None) -> BuildConfig:
    """A build config's text (LAB_FORMATS §10): its MD5 must be `key`, its
    name; `#` lines and blank lines are skipped, every other line is kept as
    a `key = value` pair (split at the first `=`; a line with none is kept
    with an empty value). `root` must be one hex key and `encoding` two,
    each given once."""
    key = _hex_key(key, "the build key", path)
    digest = hashlib.md5(data, usedforsecurity=False).hexdigest()
    if digest != key:
        raise CascMalformedError(
            f"the build config's MD5 is {digest}, not its name {key}: "
            "a partial or changed file is not read",
            path,
        )
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CascMalformedError(f"the build config is not UTF-8 ({exc})", path) from exc
    pairs: list[tuple[str, str]] = []
    for line in text.split("\n"):
        line = line.removesuffix("\r").strip()
        if not line or line.startswith("#"):
            continue
        name, _, value = line.partition("=")
        pairs.append((name.strip(), value.strip()))

    def one(name: str, parts: int) -> list[str]:
        values = [v for n, v in pairs if n == name]
        if len(values) != 1:
            raise CascMalformedError(
                f"the build config has {len(values)} {name!r} lines; exactly one is needed", path
            )
        cells = values[0].split()
        if len(cells) != parts:
            raise CascMalformedError(
                f"the build config's {name!r} has {len(cells)} values; {parts} expected", path
            )
        return [_hex_key(c, f"the build config's {name!r}", path) for c in cells]

    (root,) = one("root", 1)
    encoding_ckey, encoding_ekey = one("encoding", 2)
    return BuildConfig(
        key=key,
        root=root,
        encoding_ckey=encoding_ckey,
        encoding_ekey=encoding_ekey,
        pairs=tuple(pairs),
    )


def read_build_config(root: Path, build_key: str) -> BuildConfig:
    """Read and check the build config `build_key` names under `root`."""
    key = _hex_key(build_key, "the build key", root / ".build.info")
    path = build_config_path(root, key)
    for folder in (root / _DATA, root / _DATA / _CONFIG, path.parent.parent, path.parent):
        _check_dir(folder)
    return parse_build_config(_read(path, _CONFIG_LIMIT), key, path)


def bucket_of(key: bytes) -> int:
    """The bucket of an encoding key: its first nine bytes XORed together,
    the two nibbles of that XORed again (LAB_FORMATS §10)."""
    x = 0
    for byte in key[:KEY_BYTES]:
        x ^= byte
    return (x & 0xF) ^ (x >> 4)


def _entry_at(entries: bytes, i: int) -> IndexEntry:
    base = i * _ENTRY + KEY_BYTES
    field = int.from_bytes(entries[base : base + 5], "big")
    size = int.from_bytes(entries[base + 5 : base + 9], "little")
    return IndexEntry(archive=field >> _OFFSET_BITS, offset=field & _OFFSET_MASK, size=size)


def parse_index_file(data: bytes, bucket: int, version: int, name: str = "") -> IndexFile:
    """Parse one `.idx` file and check its header: the header hash, version
    7, the bucket its name gives, the spec `4/5/9/30`, the entries block
    inside the file and a whole number of entries. The entries hash is
    checked by `_check_entries` (all files at once); the key order there too."""
    path = Path(name) if name else None
    if len(data) < 8:
        raise CascMalformedError(f"{name}: {len(data)} bytes, too short for a header", path)
    header_size, header_hash = struct.unpack_from("<II", data, 0)
    if header_size < _HEADER_FIELDS.size or header_size > _MAX_HEADER:
        raise CascMalformedError(f"{name}: a header of {header_size} bytes is not read", path)
    if 8 + header_size > len(data):
        raise CascMalformedError(f"{name}: the header runs past the end of the file", path)
    header = data[8 : 8 + header_size]
    got = hashlittle(header)
    if got != header_hash:
        raise CascMalformedError(
            f"{name}: header hash {got:#010x}, the file says {header_hash:#010x}", path
        )
    idx_version, idx_bucket, extra, *spec, max_size = _HEADER_FIELDS.unpack_from(header, 0)
    if idx_version != _INDEX_VERSION:
        raise CascUnsupportedError(
            f"{name}: index version {idx_version}; this reader knows {_INDEX_VERSION}", path
        )
    if tuple(spec) != _SPEC:
        raise CascUnsupportedError(
            f"{name}: entry spec {'/'.join(map(str, spec))}; this reader knows "
            f"{'/'.join(map(str, _SPEC))}",
            path,
        )
    if idx_bucket != bucket:
        raise CascMalformedError(
            f"{name}: the header says bucket {idx_bucket}, the name {bucket}", path
        )
    block = (8 + header_size + 15) & ~15
    if block + 8 > len(data):
        raise CascMalformedError(f"{name}: no entries block after the header", path)
    entries_size, entries_hash = struct.unpack_from("<II", data, block)
    start = block + 8
    if start + entries_size > len(data):
        raise CascMalformedError(
            f"{name}: the entries block ({entries_size} bytes) runs past the end of the file",
            path,
        )
    if entries_size % _ENTRY:
        raise CascMalformedError(
            f"{name}: the entries block ({entries_size} bytes) is not whole {_ENTRY}-byte entries",
            path,
        )
    return IndexFile(
        bucket=bucket,
        version=version,
        header_size=header_size,
        header_hash=header_hash,
        extra=extra,
        entries_hash=entries_hash,
        max_size=max_size,
        entries=data[start : start + entries_size],
    )


_KEYS = struct.Struct(f"{KEY_BYTES}s{_ENTRY - KEY_BYTES}x")


def _check_entries(files: Sequence[IndexFile], names: Sequence[str], data_dir: Path) -> None:
    """Every file's entries hash, and its keys in strictly rising order (the
    lookup's binary search relies on it; every file at 70450 is so)."""
    for file, got, name in zip(
        files, _entries_hashes([f.entries for f in files]), names, strict=True
    ):
        if got != file.entries_hash:
            raise CascMalformedError(
                f"{name}: entries hash {got:#010x}, the file says {file.entries_hash:#010x}",
                data_dir / name,
            )
        keys = list(map(operator.itemgetter(0), _KEYS.iter_unpack(file.entries)))
        if not all(map(operator.lt, keys, keys[1:])):
            raise CascMalformedError(
                f"{name}: the entries are not in strictly rising key order", data_dir / name
            )
        file.keys = keys


def load_index(root: Path) -> tuple[CascIndex, tuple[BucketVersion, ...]]:
    """Read every bucket's newest `.idx` under `root/Data/data` into one
    lookup, and say which version each bucket took and which it did not."""
    data_dir = root / _DATA / _DATA_DATA
    for folder in (root / _DATA, data_dir):
        _check_dir(folder)
    try:
        names = [e.name for e in longpath.entries(data_dir)]
    except OSError as exc:
        raise CascUnreadableError(
            f"cannot list {data_dir} ({exc.strerror or exc})", data_dir
        ) from exc
    found: dict[int, list[int]] = {}
    for name in names:
        match = _INDEX_NAME.fullmatch(name)
        if match is None:
            continue  # data.NNN, shmem, index.lock.0, the .lru file: not opened
        bucket, version = int(match[1], 16), int(match[2], 16)
        if bucket >= BUCKETS:
            raise CascUnsupportedError(
                f"{name}: bucket {bucket}; this reader knows {BUCKETS}", data_dir / name
            )
        found.setdefault(bucket, []).append(version)
    if not found:
        raise CascMissingError(f"no .idx file in {data_dir}", data_dir)
    files: list[IndexFile] = []
    taken_names: list[str] = []
    for bucket in sorted(found):
        version = max(found[bucket])
        name = f"{bucket:02x}{version:08x}.idx"
        path = data_dir / name
        files.append(parse_index_file(_read(path, _INDEX_LIMIT), bucket, version, name))
        taken_names.append(name)
    _check_entries(files, taken_names, data_dir)
    versions = tuple(
        BucketVersion(
            bucket=f.bucket,
            version=f.version,
            file=name,
            entries=len(f.keys),
            header_size=f.header_size,
            header_hash=f.header_hash,
            extra_bytes=f.extra,
            entries_hash=f.entries_hash,
            max_archive_size=f.max_size,
            not_taken=tuple(sorted((v for v in found[f.bucket] if v != f.version), reverse=True)),
        )
        for f, name in zip(files, taken_names, strict=True)
    )
    return CascIndex(files), versions


def open_storage(install: Install, flavor: Flavor | str) -> CascStorage:
    """Open one flavor's build in the install's shared `Data/`: its
    `.build.info` row, its build config and the local index, with the
    report. Every file is read and closed before this returns."""
    started = time.perf_counter()
    flavor, row = select_build_row(install, flavor)
    root = install.root
    build_key = _hex_key(row.build_key, "the Active row's Build Key", root / ".build.info")
    config = read_build_config(root, build_key)
    index_started = time.perf_counter()
    index, versions = load_index(root)
    finished = time.perf_counter()
    version = (row.version or "").strip()
    report = CascIndexReport(
        product=flavor.product,
        version=version,
        build=_build_number(version) if version else None,
        build_key=build_key,
        root=config.root,
        encoding_ckey=config.encoding_ckey,
        encoding_ekey=config.encoding_ekey,
        bucket_count=len(versions),
        entry_count=sum(v.entries for v in versions),
        versions=versions,
        newest_taken=tuple(v.bucket for v in versions if v.not_taken),
        index_seconds=round(finished - index_started, 3),
        open_seconds=round(finished - started, 3),
    )
    return CascStorage(Path(root), row, config, index, report)
