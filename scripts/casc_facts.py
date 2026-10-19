#!/usr/bin/env python3
"""Write a derived fixture of an install's CASC index (M15-03, ADR-0033).

    uv run python scripts/casc_facts.py --flavor FOLDER --out PATH [--root INSTALL]

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
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from wowlab_core import casc
from wowlab_core.install import InstallError, discover

INSTALL_MARKER = ".build.info"
SCHEMA = 1
SAMPLE_BUCKETS = (0, 7, 15)
MAX_SIZE_DIGITS = 20  # a u64 has at most 20 decimal digits


class FactsError(Exception):
    """A refusal, reported in one line."""


def refuse_install(out: Path) -> None:
    """Refuse an output path inside a game install, every link resolved."""
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--flavor", required=True, help="the flavor folder, as on disk")
    parser.add_argument("--out", required=True, type=Path, help="the JSON file to write")
    parser.add_argument("--root", default=None, help="the install (default: discovery)")
    parser.add_argument("--captured", required=True, help="the capture date, YYYY-MM-DD")
    args = parser.parse_args(argv)
    out: Path = args.out
    try:
        refuse_install(out)
        if out.exists():
            raise FactsError("--out is already there; a fixture is never replaced")
        storage = casc.open_storage(discover(args.root), args.flavor)
        record = facts(storage, args.flavor, args.captured)
        text = json.dumps(record, indent=2) + "\n"
        with out.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
    except (FactsError, casc.CascError, InstallError, OSError) as exc:
        print(f"casc_facts: {exc}", file=sys.stderr)
        return 1
    report = storage.report
    print(
        f"wrote {out}: {report.product} {report.version}, {report.bucket_count} buckets, "
        f"{report.entry_count} entries, {len(record['key_rows'])} key rows; "
        f"index read in {report.index_seconds} s"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
