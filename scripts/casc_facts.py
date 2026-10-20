#!/usr/bin/env python3
"""Write a derived fixture of an install's CASC index (M15-03) or of its
archive records and BLTE (M15-04, `--blte`) (ADR-0033).

    uv run python scripts/casc_facts.py --flavor FOLDER --out PATH [--root INSTALL]
    uv run python scripts/casc_facts.py --blte --flavor FOLDER --out PATH [--root INSTALL]

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

The install is `--root`, else `WOWLAB_WOW_ROOT`, else the platform default
(`wowlab_core.install.discover`). Never print or commit its path: the JSON
holds none.

Refusals (exit 1): `--out` inside a game install (a folder with
`.build.info` in it or above it, links resolved), `--out` already there (a
committed fixture is never replaced), or any `casc` or `install` error, in
one line on stderr. The output is written only after everything is read.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from wowlab_core import casc
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--flavor", required=True, help="the flavor folder, as on disk")
    parser.add_argument("--out", required=True, type=Path, help="the JSON file to write")
    parser.add_argument("--root", default=None, help="the install (default: discovery)")
    parser.add_argument("--captured", required=True, help="the capture date, YYYY-MM-DD")
    parser.add_argument(
        "--blte", action="store_true", help="write the archive and BLTE record (M15-04)"
    )
    args = parser.parse_args(argv)
    out: Path = args.out
    try:
        refuse_install(out)
        if out.exists():
            raise FactsError("--out is already there; a fixture is never replaced")
        storage = casc.open_storage(discover(args.root), args.flavor)
        make = blte_facts if args.blte else facts
        record = make(storage, args.flavor, args.captured)
        text = json.dumps(record, indent=2) + "\n"
        with out.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    except (FactsError, casc.CascError, InstallError, OSError) as exc:
        print(f"casc_facts: {exc}", file=sys.stderr)
        return 1
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
