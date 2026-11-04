#!/usr/bin/env python3
"""Write a derived fixture of an install's CASC index (M15-03), of its
archive records and BLTE (M15-04, `--blte`), or of its encoding and root
files and reads by file data id (M15-05, `--files`) (ADR-0033).

    uv run python scripts/casc_facts.py --flavor FOLDER --out PATH [--root INSTALL]
    uv run python scripts/casc_facts.py --blte --flavor FOLDER --out PATH [--root INSTALL]
    uv run python scripts/casc_facts.py --files --flavor FOLDER --out PATH [--root INSTALL]
        [--model-table CSV]

Opens one flavor's build in the install's `Data/` through `wowlab_core.casc`
(read-only: every file is read into memory and closed; `shmem` and
`index.lock.0` are never opened) and writes, as JSON, what was read: the
product, version and build, the build and config keys, the two sizes the
build config gives the encoding file (`encoding-size`: decoded, then
encoded), the bucket and entry counts, the buckets with no file and the
`.idx`-like names not taken, each bucket's taken `.idx` version with its
header facts, stored hashes, versions not taken and the counts of entries
outside their key's bucket (`entries_from_previous_bucket`,
`entries_out_of_place`, as `casc` reports them), and a handful of
key -> (archive, offset, size) rows: the encoding file's, the first and last
entry of buckets 0, 7 and 15 whose key is in that bucket, and the first entry
of bucket 0 whose key is not. Never
any bytes of a file in the install (ADR-0033: no asset byte enters the
repository), and nothing from `.build.info`'s `Tags` cell (it carries a
geo-IP country token).

With `--blte` the record is of the archives instead (`casc.read_record`,
`casc.parse_record_header`, `casc.parse_blte`, `casc.decode_blte`; each
archive opened for one ranged read and closed): for a few records, the
index entry, the 30-byte record header's fields, the BLTE chunk table
(per block: mode, stored and decoded size, MD5) and either the decoded
size and MD5 or the typed refusal (an encrypted block's key name, never a
key): the encoding file the build config names (its decode time too), the
first own-bucket record of bucket 0, in key order, whose header holds the
whole encoding key, and the first one whose BLTE holds an encrypted block;
the record headers of two archive segment headers (the shifted index rows);
and a census of every 256th own-bucket entry of every bucket (block modes,
block counts, flags, refusals, encrypted key names; for every `E` block its
key-name length, IV length and cipher byte; how many record headers' nine
index bytes are the start of the MD5 of the record's BLTE header). Never a
byte of a file: sizes, hashes, modes and names only.

With `--files` the record is of `casc.open_source` (the encoding file and
the root, filtered for the build's locale and the flavor folder's
platform): the locale and platform and where each came from, and the
speech locale the `Tags` cell names (never the rest of the cell), the
encoding header and how many content keys have one, two, … encoding keys, the root's report with the counts the filter
gives (unique ids, with and without `LowViolence`, ids in more than one
block and the flags of those blocks), a census of every root row taken
against the local index (how many ids have no local copy: §17.1's middle
state), a handful of file data ids with their state, content and encoding
hashes and, when local, their index row, sizes and whether the decoded
bytes' MD5 is the content hash; a sample of 300 `ModelFileData` ids (the
table from `--model-table`, else through `gamedata`) split into the
states; the open's timings, the time of 300 reads and of the sample's
local files, and the process's peak resident memory after the open and
after the reads. Field names say `hash`, not `key`.

The install is `--root`, else `WOWLAB_WOW_ROOT`, else the platform default
(`wowlab_core.install.discover`). Never print or commit its path: the JSON
holds none.

Refusals (exit 1): `--out` inside a game install (a folder with
`.build.info` in it or above it, links resolved), `--out` already there (a
committed fixture is never replaced), or any `casc`, `install` or `gamedata` error, in
one line on stderr. The output is written only after everything is read.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import itertools
import json
import sys
import time
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from wowlab_core import casc, gamedata
from wowlab_core.gamedata import GameDataError
from wowlab_core.install import InstallError, discover

INSTALL_MARKER = ".build.info"
SCHEMA = 1
SAMPLE_BUCKETS = (0, 7, 15)
MAX_SIZE_DIGITS = 20  # a u64 has at most 20 decimal digits
CENSUS_STEP = 256  # every 256th own-bucket entry of each bucket
SEARCH_LIMIT = 20_000  # entries tried, in key order, for a first-of-a-kind record


class FactsError(Exception):
    """A refusal, reported in one line."""


def refuse_install(out: Path) -> None:
    """Refuse an output path inside a game install, every link resolved,
    and an output path that is itself a link (its target is not looked
    at: a link is never written through)."""
    if out.is_symlink():
        raise FactsError("--out is a link; a fixture is written to a plain path")
    try:
        parent = out.parent.resolve()
    except (OSError, RuntimeError) as exc:
        raise FactsError(f"cannot resolve the folder of --out ({exc})") from None
    for candidate in (parent, *parent.parents):
        if (candidate / INSTALL_MARKER).exists():
            raise FactsError("--out is inside a game install; nothing but guard writes there")


def _row(key: bytes, file_bucket: int, entry: casc.IndexEntry, why: str) -> dict[str, Any]:
    """One key row: the key, the bucket of the file it was found in (not
    computed from the key), and where its data is."""
    return {
        "key": key.hex(),
        "file_bucket": file_bucket,
        "archive": entry.archive,
        "offset": entry.offset,
        "size": entry.size,
        "why": why,
    }


def _size(text: str) -> int | None:
    """A size cell as an integer, or None when it is not a short decimal."""
    ok = text.isascii() and text.isdecimal() and len(text) <= MAX_SIZE_DIGITS
    return int(text) if ok else None


def facts(storage: casc.CascStorage, flavor_folder: str, captured: str) -> dict[str, Any]:
    """The derived record of one opened build."""
    report = storage.report
    index = storage.index
    pairs = dict(storage.config.pairs)
    sizes = [_size(s) for s in pairs.get("encoding-size", "").split()]
    rows: list[dict[str, Any]] = []
    ekey = bytes.fromhex(report.encoding_ekey)
    try:
        found = index.find(ekey)
    except casc.BucketMissingError:
        found = None
    if found is not None:
        rows.append(
            _row(
                ekey[: casc.KEY_BYTES],
                casc.bucket_of(ekey),
                found,
                "the build config's encoding key",
            )
        )
    for bucket in SAMPLE_BUCKETS:
        own = [i for i, k in enumerate(index.keys(bucket)) if casc.bucket_of(k) == bucket]
        for i, why in ((own[0], "first"), (own[-1], "last")) if own else ():
            key, entry = index.entry_at(bucket, i)
            rows.append(
                _row(key, bucket, entry, f"{why} entry of bucket {bucket} whose key is in it")
            )
    shifted = [i for i, k in enumerate(index.keys(0)) if casc.bucket_of(k) != 0]
    if shifted:
        key, entry = index.entry_at(0, shifted[0])
        rows.append(_row(key, 0, entry, "first entry of bucket 0 whose key is not in it"))
    return {
        "schema": SCHEMA,
        "kind": "casc-index-derived",
        "made_by": "scripts/casc_facts.py",
        "captured": captured,
        "flavor": flavor_folder,
        "product": report.product,
        "version": report.version,
        "build": report.build,
        "build_key": report.build_key,
        "root": report.root,
        "encoding_ckey": report.encoding_ckey,
        "encoding_ekey": report.encoding_ekey,
        "encoding_size": {
            "decoded": sizes[0] if len(sizes) == 2 else None,
            "encoded": sizes[1] if len(sizes) == 2 else None,
        },
        "bucket_count": report.bucket_count,
        "entry_count": report.entry_count,
        "newest_taken": list(report.newest_taken),
        "missing_buckets": list(report.missing_buckets),
        "skipped_files": list(report.skipped_files),
        "versions": [v.model_dump() for v in report.versions],
        "key_rows": rows,
    }


def _layout(layout: casc.BlteLayout) -> dict[str, Any]:
    return {
        "header_size": layout.header_size,
        "encoded_size": layout.encoded_size,
        "decoded_size": layout.decoded_size,
        "blocks": [
            {
                "mode": b.mode,
                "encoded_size": b.encoded_size,
                "decoded_size": b.decoded_size,
                "md5": b.md5,
            }
            for b in layout.blocks
        ],
    }


def _outcome(blte: memoryview) -> tuple[dict[str, Any], float]:
    """The decoded size and MD5, or the typed refusal, and the decode time."""
    started = time.perf_counter()
    try:
        data = casc.decode_blte(blte)
    except casc.EncryptedContent as exc:
        refused = {"error": "EncryptedContent", "block": exc.block}
        return {**refused, "key_name": exc.key_name, "cipher": exc.cipher}, 0.0
    except casc.UnsupportedBlockMode as exc:
        return {"error": "UnsupportedBlockMode", "block": exc.block, "mode": exc.mode}, 0.0
    seconds = time.perf_counter() - started
    digest = hashlib.md5(data, usedforsecurity=False).hexdigest()
    return {"decoded_size": len(data), "decoded_md5": digest}, seconds


def _record(
    storage: casc.CascStorage, key: bytes, entry: casc.IndexEntry, why: str
) -> dict[str, Any]:
    """One record: where the index puts it, its header, its BLTE, its outcome."""
    path = casc.archive_path(storage.root, entry.archive)
    data = memoryview(casc.read_record(storage.root, entry))
    header = casc.parse_record_header(data, key, entry, path)
    blte = data[casc.RECORD_HEADER_SIZE :]
    outcome, seconds = _outcome(blte)
    row: dict[str, Any] = {
        "why": why,
        "key": key[: casc.KEY_BYTES].hex(),
        "archive": entry.archive,
        "offset": entry.offset,
        "size": entry.size,
        "record_header": header.model_dump(),
        "blte": _layout(casc.parse_blte(blte, path)),
        "outcome": outcome,
    }
    if seconds:
        row["decode_seconds"] = round(seconds, 3)
    return row


def _own(index: casc.CascIndex, bucket: int) -> list[int]:
    return [i for i, k in enumerate(index.keys(bucket)) if casc.bucket_of(k) == bucket]


def _full_hash_field(header: casc.RecordHeader) -> bool:
    """Whether the header's hash field holds more than the index's nine bytes."""
    return bool(header.hash_field[casc.KEY_BYTES * 2 :].strip("0"))


def _first(storage: casc.CascStorage, want: str) -> dict[str, Any] | None:
    """The first own-bucket record of bucket 0, in key order, whose header
    holds the whole encoding key (`want == "full"`) or whose BLTE holds an
    encrypted block (`want == "encrypted"`), among the first SEARCH_LIMIT."""
    index = storage.index
    for i in _own(index, 0)[:SEARCH_LIMIT]:
        key, entry = index.entry_at(0, i)
        if entry.size - casc.RECORD_HEADER_SIZE > casc.DECODE_LIMIT:
            continue
        data = memoryview(casc.read_record(storage.root, entry))
        header = casc.parse_record_header(data, key, entry)
        if want == "full" and _full_hash_field(header):
            why = "first own-bucket record of bucket 0 whose header holds the whole encoding key"
            return _record(storage, key, entry, why)
        if want == "encrypted":
            outcome, _ = _outcome(data[casc.RECORD_HEADER_SIZE :])
            if outcome.get("error") == "EncryptedContent":
                why = "first own-bucket record of bucket 0 with an encrypted block"
                return _record(storage, key, entry, why)
    return None


def _segment_headers(storage: casc.CascStorage) -> list[dict[str, Any]]:
    """The record headers of the shifted index rows (the archives' segment
    headers): the first such entry of buckets 0 and 15, by key order."""
    index = storage.index
    rows: list[dict[str, Any]] = []
    for bucket in (0, 15):
        shifted = [i for i, k in enumerate(index.keys(bucket)) if casc.bucket_of(k) != bucket]
        if not shifted:
            continue
        key, entry = index.entry_at(bucket, shifted[0])
        data = casc.read_record(storage.root, entry)
        header = casc.parse_record_header(data, key, entry)
        rows.append(
            {
                "why": f"first entry of bucket {bucket} whose key is not in it",
                "file_bucket": bucket,
                "key": key.hex(),
                "archive": entry.archive,
                "offset": entry.offset,
                "size": entry.size,
                "record_header": header.model_dump(),
            }
        )
    return rows


def _e_block_shapes(blte: memoryview, layout: casc.BlteLayout) -> list[tuple[int, int, str]]:
    """(key-name length, IV length, cipher byte) of every `E` block in the
    BLTE, read from each block's own prefix (the decoder stops at the
    first `E` block, so this sees them all). A block cut short gives -1."""
    shapes: list[tuple[int, int, str]] = []
    at = layout.header_size or 8
    for block in layout.blocks:
        if block.mode == "E":
            body = blte[at + 1 : at + block.encoded_size]
            try:
                key_len = body[0]
                iv_len = body[1 + key_len]
                cipher = casc.mode_text(body[2 + key_len + iv_len])
            except IndexError:
                shapes.append((-1, -1, "?"))
            else:
                shapes.append((key_len, iv_len, cipher))
        at += block.encoded_size
    return shapes


def _header_md5_starts_with_key(blte: memoryview, layout: casc.BlteLayout, key: bytes) -> bool:
    """Whether the first nine bytes of the MD5 of the BLTE header (magic
    through the last chunk row) are the record's nine index bytes."""
    if layout.header_size == 0:
        return False
    digest = hashlib.md5(blte[: layout.header_size], usedforsecurity=False).digest()
    return digest[: casc.KEY_BYTES] == key[: casc.KEY_BYTES]


def _census(storage: casc.CascStorage) -> dict[str, Any]:
    """Every CENSUS_STEP-th own-bucket entry of every bucket: read, header
    checked, BLTE laid out and decoded (or refused)."""
    index = storage.index
    modes: Counter[str] = Counter()
    counts: Counter[str] = Counter()
    flags: Counter[str] = Counter()
    refused: Counter[str] = Counter()
    key_names: Counter[str] = Counter()
    e_shapes: Counter[str] = Counter()
    records = decoded = full = chunkless = over_cap = header_md5_is_key = 0
    for bucket in index.buckets:
        for i in _own(index, bucket)[::CENSUS_STEP]:
            key, entry = index.entry_at(bucket, i)
            if entry.size - casc.RECORD_HEADER_SIZE > casc.DECODE_LIMIT:
                over_cap += 1
                continue
            records += 1
            data = memoryview(casc.read_record(storage.root, entry))
            header = casc.parse_record_header(data, key, entry)
            flags[header.flags] += 1
            full += _full_hash_field(header)
            layout = casc.parse_blte(data[casc.RECORD_HEADER_SIZE :])
            chunkless += layout.header_size == 0
            blte = data[casc.RECORD_HEADER_SIZE :]
            header_md5_is_key += _header_md5_starts_with_key(blte, layout, key)
            for key_len, iv_len, cipher in _e_block_shapes(blte, layout):
                e_shapes[f"key_name_bytes={key_len} iv_bytes={iv_len} cipher={cipher}"] += 1
            n = len(layout.blocks)
            counts["1" if n == 1 else "2-9" if n < 10 else "10-99" if n < 100 else "100+"] += 1
            modes.update(b.mode for b in layout.blocks)
            outcome, _ = _outcome(data[casc.RECORD_HEADER_SIZE :])
            if "error" in outcome:
                refused[outcome["error"]] += 1
                if "key_name" in outcome:
                    key_names[outcome["key_name"]] += 1
            else:
                decoded += 1
    return {
        "step": CENSUS_STEP,
        "records": records,
        "over_cap": over_cap,
        "decoded": decoded,
        "refused": dict(sorted(refused.items())),
        "hash_field_full": full,
        "chunkless": chunkless,
        "header_md5_starts_with_key": header_md5_is_key,
        "flags": dict(sorted(flags.items())),
        "blocks_by_mode": dict(sorted(modes.items())),
        "records_by_block_count": dict(sorted(counts.items())),
        "encrypted_key_names": dict(sorted(key_names.items())),
        "e_block_shapes": dict(sorted(e_shapes.items())),
    }


def blte_facts(storage: casc.CascStorage, flavor_folder: str, captured: str) -> dict[str, Any]:
    """The derived record of the archives of one opened build."""
    report = storage.report
    records: list[dict[str, Any]] = []
    ekey = bytes.fromhex(report.encoding_ekey)
    try:
        found = storage.index.find(ekey)
    except casc.BucketMissingError:
        found = None
    if found is not None:
        records.append(_record(storage, ekey, found, "the build config's encoding file"))
    for want in ("full", "encrypted"):
        row = _first(storage, want)
        if row is not None:
            records.append(row)
    return {
        "schema": SCHEMA,
        "kind": "casc-blte-derived",
        "made_by": "scripts/casc_facts.py --blte",
        "captured": captured,
        "flavor": flavor_folder,
        "product": report.product,
        "version": report.version,
        "build": report.build,
        "decode_limit": casc.DECODE_LIMIT,
        "records": records,
        "segment_headers": _segment_headers(storage),
        "census": _census(storage),
    }


# ─── --files: encoding, root and reads by file data id (M15-05) ─────────────


def _peak_rss_mb() -> float | None:
    """The process's peak resident memory so far, in MB (`ru_maxrss` is in
    bytes on macOS, in KiB elsewhere; not on Windows, where it is None)."""
    try:
        import resource  # POSIX only
    except ImportError:
        return None
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(peak / 1e6 if sys.platform == "darwin" else peak * 1024 / 1e6, 1)


def _file_row(
    source: casc.CascSource, fdid: int, why: str, *, in_table: bool = True
) -> dict[str, Any]:
    """One file: its stat and, when local, its read checked by its content
    key. The field names say `hash`, not `key`: they are MD5s of public game
    files, and gitleaks reads `"…key": "<hex>"` as a credential."""
    stat = source.stat(fdid, in_table=in_table)
    row: dict[str, Any] = {
        "why": why,
        "fdid": fdid,
        "state": stat.state,
        "words": stat.words,
        "content_hash": stat.content_key,
        "content_hashes": list(stat.content_keys),
        "encoding_hashes": list(stat.encoding_keys),
        "encryption_names": list(stat.key_names),
        "root_content_flags": [f"{h.content_flags:#x}" for h in source.root.find(fdid)],
        "note": stat.note,
    }
    if stat.state == "local" and stat.encoding_key is not None:
        entry = source.storage.index.find(bytes.fromhex(stat.encoding_key))
        data = source.read(fdid)
        row.update(
            {
                "local_encoding_hash": stat.encoding_key,
                "index": None if entry is None else entry.model_dump(),
                "size": stat.size,
                "decoded_size": len(data),
                "decoded_md5_is_content_hash": hashlib.md5(data, usedforsecurity=False).hexdigest()
                == stat.content_key,
            }
        )
    return row


def _model_ids(path: Path | None, version: str) -> tuple[list[int], str, str | None]:
    """The `FileDataID` column of `ModelFileData` at `version`: from `path`
    (a CSV), else through `gamedata` (its cache under the data directory,
    or one fetch from wago.tools, as any `wowlab` table read does); where
    it came from; and the build the table is of (gamedata's own record of
    the fetch, else the build asked for; None for a CSV)."""
    table_build: str | None = None
    if path is not None:
        with path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        where = "a CSV given with --model-table"
    else:
        data = gamedata.GameData()
        rows = list(data.rows("ModelFileData", version))
        sidecar = data.sidecar("ModelFileData", version)
        table_build = sidecar.build if sidecar is not None else version
        where = "gamedata (wago.tools, cached under the data directory)"
    ids = sorted({int(r["FileDataID"]) for r in rows if r.get("FileDataID", "").isdecimal()})
    return ids, where, table_build


def _sample(values: Sequence[int], n: int) -> list[int]:
    """`n` values evenly spaced over `values` (all of them when fewer)."""
    if len(values) <= n:
        return list(values)
    step = len(values) / n
    return [values[int(i * step)] for i in range(n)]


def _timed_reads(source: casc.CascSource, ids: Sequence[int]) -> tuple[int, dict[str, int], float]:
    """Read every id in turn: the bytes decoded, the refusals by kind (an
    encrypted or not local file is refused, not read), and the seconds."""
    total = 0
    refused: Counter[str] = Counter()
    started = time.perf_counter()
    for fdid in ids:
        try:
            total += len(source.read(fdid))
        except casc.EncryptedContent:
            refused["encrypted"] += 1
        except casc.FileUnavailable as exc:
            refused[exc.state] += 1
        except casc.CascError as exc:
            refused[type(exc).__name__] += 1
    return total, dict(sorted(refused.items())), time.perf_counter() - started


def _local_census(source: casc.CascSource) -> tuple[dict[str, Any], int | None]:
    """Every root row taken, checked against the local index by its
    content key's encoding keys (the index only; no record is read, so an
    encrypted copy counts as local here): how many unique ids have a local
    copy and how many none (§17.1's middle state), with the root flags of
    the rows of those with none; for ids with more than one row, which rows
    are local (in file order, each with its block's `0x1` bit) and whether
    the rows' content differs; for ids with two or more local rows of
    different content, none with bit 0x1, the locale flags of those rows
    (in file order); and the first id with no local copy."""
    index = source.storage.index
    lookup = source.encoding.lookup
    local: dict[int, list[tuple[bool, bytes, int, int]]] = {}
    for fdid, ckey, flags, locales in source.root.rows():
        row = lookup(ckey)
        here = row is not None and any(index.find(e) is not None for e in row.ekeys)
        local.setdefault(fdid, []).append((here, ckey, flags, locales))
    some = sum(1 for v in local.values() if any(x[0] for x in v))
    none = sorted(f for f, v in local.items() if not any(x[0] for x in v))
    none_flags: Counter[str] = Counter(" + ".join(f"{x[2]:#x}" for x in local[f]) for f in none)
    patterns: Counter[str] = Counter()
    locale_pairs: Counter[str] = Counter()
    for v in local.values():
        present = [x for x in v if x[0]]
        if (
            len(present) > 1
            and len({x[1] for x in present}) > 1
            and not any(x[2] & casc.ROOT_FLAG_1 for x in present)
        ):
            locale_pairs[" + ".join(f"{x[3]:#x}" for x in present)] += 1
        if len(v) > 1:
            rows = " + ".join(
                f"{'local' if x[0] else 'absent'}{' (0x1)' if x[2] & 0x1 else ''}" for x in v
            )
            same = "same content" if len({x[1] for x in v}) == 1 else "different content"
            patterns[f"{rows}, {same}"] += 1
    return (
        {
            "what": "every root row taken; local means an encoding key of its content is in "
            "the local index (no record read)",
            "unique_ids": len(local),
            "with_a_local_copy": some,
            "with_no_local_copy": len(none),
            "flags_of_ids_with_no_local_copy": dict(none_flags.most_common()),
            "ids_in_more_than_one_block": dict(patterns.most_common()),
            "locale_flags_of_two_local_rows_without_0x1": dict(locale_pairs.most_common()),
        },
        none[0] if none else None,
    )


def files_facts(
    install_root: str | None,
    flavor_folder: str,
    captured: str,
    model_table: Path | None,
) -> dict[str, Any]:
    """The derived record of one build's encoding and root and of reads by
    file data id."""
    inst = discover(install_root)
    source = casc.open_source(inst, flavor_folder)
    peak_open = _peak_rss_mb()
    report = source.report
    root = source.root
    # The timed reads come first, so the peak after them is the reader's,
    # not this script's analysis below.
    step = max(1, len(root) // 300)
    reads = list(dict.fromkeys(itertools.islice(root.ids(), 0, None, step)))[:300]
    read_bytes, read_refused, reads_seconds = _timed_reads(source, reads)
    peak_reads = _peak_rss_mb()
    taken = list(root.ids())
    unique = sorted(set(taken))
    counted = Counter(taken)
    dups = [f for f, n in counted.items() if n > 1]
    pairs: Counter[str] = Counter()
    differing = 0
    first_differing: int | None = None
    for fdid in dups:
        hits = root.find(fdid)
        pairs[" + ".join(f"{h.content_flags:#x}" for h in hits)] += 1
        if len({h.ckey for h in hits}) > 1:
            differing += 1
            first_differing = fdid if first_differing is None else first_differing
    with_low_violence = set(unique)
    skipped_any = set()
    flag = report.root.locale_flag
    mask = casc.PLATFORM_SKIP[report.platform]
    for fdid, content, locales in root.skipped_rows():
        skipped_any.add(fdid)
        if locales & flag and not content & mask:
            with_low_violence.add(fdid)
    only_skipped = sorted(skipped_any - set(unique))
    absent = next(i for i in range(1, unique[-1]) if i not in counted and i not in skipped_any)
    encrypted_ids = [
        f
        for f in unique[:: max(1, len(unique) // 20000)]
        if any(h.content_flags & casc.ROOT_ENCRYPTED for h in root.find(f))
    ]
    census, first_not_local = _local_census(source)
    rows: list[dict[str, Any]] = [
        _file_row(source, unique[0], "the lowest file data id taken"),
        _file_row(source, unique[-1], "the highest file data id taken"),
    ]
    for fdid in _sample(unique, 8)[1:]:
        rows.append(_file_row(source, fdid, "evenly spaced over the ids taken"))
    if first_differing is not None:
        rows.append(
            _file_row(
                source, first_differing, "the first id with two root rows of different content"
            )
        )
    if encrypted_ids:
        rows.append(
            _file_row(source, encrypted_ids[0], "the first sampled id in an Encrypted block")
        )
    if only_skipped:
        rows.append(
            _file_row(source, only_skipped[0], "the first id the root lists only in skipped blocks")
        )
    if first_not_local is not None:
        rows.append(
            _file_row(source, first_not_local, "the first id taken with no copy in the local index")
        )
    rows.append(
        _file_row(
            source,
            absent,
            "the first id the root does not list, asked with in_table=False (the script's "
            "choice: no game table was looked up)",
            in_table=False,
        )
    )

    model_ids, model_source, model_build = _model_ids(model_table, report.index.version)
    sampled = _sample(model_ids, 300)
    states: Counter[str] = Counter()
    examples: dict[str, int] = {}
    stats = []
    for fdid in sampled:
        stat = source.stat(fdid)
        states[stat.state] += 1
        examples.setdefault(stat.state, fdid)
        stats.append(stat)
    for state, fdid in sorted(examples.items()):
        rows.append(_file_row(source, fdid, f"the first sampled ModelFileData id in state {state}"))

    local_models = [s.fdid for s in stats if s.state == "local"]
    model_bytes, model_refused, model_seconds = _timed_reads(source, local_models)
    return {
        "schema": SCHEMA,
        "kind": "casc-files-derived",
        "made_by": "scripts/casc_facts.py --files",
        "captured": captured,
        "flavor": flavor_folder,
        "product": report.index.product,
        "version": report.index.version,
        "build": report.index.build,
        "locale": report.locale,
        "locale_from": report.locale_from,
        "speech_locale": report.speech_locale,
        "locale_flag": report.root.locale_flag,
        "platform": report.platform,
        "platform_from": report.platform_from,
        "platform_mask": f"{mask:#x}",
        "encoding": {
            **report.encoding.model_dump(),
            "read_by": report.encoding_ekey,
            "encoding_hashes_per_content": {
                str(k): v for k, v in source.encoding.key_counts().items()
            },
        },
        "root": {
            **report.root.model_dump(),
            "read_by_hash": report.root_ekey,
            "unique_ids_taken": len(unique),
            "unique_ids_with_low_violence": len(with_low_violence),
            "ids_in_more_than_one_block": len(dups),
            "ids_whose_rows_differ_in_content": differing,
            "flags_of_ids_in_more_than_one_block": dict(pairs.most_common()),
            "ids_only_in_skipped_blocks": len(only_skipped),
        },
        "local_census": census,
        "rows": rows,
        "model_file_data": {
            "source": model_source,
            "version": model_build,
            "ids_in_table": len(model_ids),
            "sampled": len(sampled),
            "states": dict(sorted(states.items())),
        },
        "timings": {
            "index_seconds": report.index.index_seconds,
            "encoding_seconds": report.encoding_seconds,
            "root_seconds": report.root_seconds,
            "open_seconds": report.open_seconds,
            "reads": {
                "files": len(reads),
                "what": "every (rows taken // 300)-th row taken, in file order, each id once",
                "bytes": read_bytes,
                "refused": read_refused,
                "seconds": round(reads_seconds, 3),
            },
            "model_reads": {
                "files": len(local_models),
                "what": "every local file of the ModelFileData sample",
                "bytes": model_bytes,
                "refused": model_refused,
                "seconds": round(model_seconds, 3),
            },
        },
        "memory": {
            "peak_rss_mb_after_open": peak_open,
            "peak_rss_mb_after_reads": peak_reads,
            "reopens": source.reopens,
        },
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--flavor", required=True, help="the flavor folder, as on disk")
    parser.add_argument("--out", required=True, type=Path, help="the JSON file to write")
    parser.add_argument("--root", default=None, help="the install (default: discovery)")
    parser.add_argument("--captured", required=True, help="the capture date, YYYY-MM-DD")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--blte", action="store_true", help="write the archive and BLTE record (M15-04)"
    )
    mode.add_argument(
        "--files",
        action="store_true",
        help="write the encoding, root and file-read record (M15-05)",
    )
    parser.add_argument(
        "--model-table",
        type=Path,
        default=None,
        help="with --files: a ModelFileData CSV for the build (default: through gamedata)",
    )
    args = parser.parse_args(argv)
    out: Path = args.out
    try:
        refuse_install(out)
        if out.exists():
            raise FactsError("--out is already there; a fixture is never replaced")
        if args.files:
            record = files_facts(args.root, args.flavor, args.captured, args.model_table)
        else:
            storage = casc.open_storage(discover(args.root), args.flavor)
            make = blte_facts if args.blte else facts
            record = make(storage, args.flavor, args.captured)
        text = json.dumps(record, indent=2) + "\n"
        with out.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    except (FactsError, casc.CascError, InstallError, GameDataError, OSError) as exc:
        print(f"casc_facts: {exc}", file=sys.stderr)
        return 1
    if args.files:
        timings = record["timings"]
        print(
            f"wrote {out}: {record['product']} {record['version']}, {len(record['rows'])} rows; "
            f"open {timings['open_seconds']} s, {timings['reads']['files']} reads in "
            f"{timings['reads']['seconds']} s, peak RSS {record['memory']['peak_rss_mb_after_reads']} MB; "
            f"ModelFileData sample {record['model_file_data']['states']}"
        )
        return 0
    report = storage.report
    if args.blte:
        census = record["census"]
        print(
            f"wrote {out}: {report.product} {report.version}, {len(record['records'])} records, "
            f"census of {census['records']} ({census['blocks_by_mode']})"
        )
        return 0
    print(
        f"wrote {out}: {report.product} {report.version}, {report.bucket_count} buckets, "
        f"{report.entry_count} entries, {len(record['key_rows'])} key rows; "
        f"index read in {report.index_seconds} s"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
