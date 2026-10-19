"""`casc` against the derived fixture of the owner's install (M15-03;
ADR-0033 layer b): what `scripts/casc_facts.py` read from the real `.idx`
files and build config at 1.60.1.70450, never their bytes. Provenance:
`fixtures/README.md`, row `macos-70450/derived/casc-index.json`.

Each check here is one the real files answer independently of the reader:
the header hash the agent stored against `hashlittle` over the header the
recorded fields make; the bucket each key was found in against
`bucket_of`; the encoding file's entry size against the build config's
`encoding-size`.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any

import pytest

from wowlab_core import casc

pytestmark = pytest.mark.parser

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "macos-70450" / "derived"
FACTS: dict[str, Any] = json.loads((FIXTURE / "casc-index.json").read_text(encoding="utf-8"))
RECORD_HEADER = 30  # the archive record header before BLTE (LAB_FORMATS §10)


def test_the_record_is_the_build_it_names() -> None:
    assert FACTS["kind"] == "casc-index-derived" and FACTS["schema"] == 1
    assert (FACTS["product"], FACTS["version"], FACTS["build"]) == (
        "wow_classic_beta",
        "1.60.1.70450",
        70450,
    )
    for key in ("build_key", "root", "encoding_ckey", "encoding_ekey"):
        assert len(FACTS[key]) == 32 and int(FACTS[key], 16) >= 0


def test_sixteen_buckets_one_version_each_and_the_counts_add_up() -> None:
    versions = FACTS["versions"]
    assert FACTS["bucket_count"] == len(versions) == casc.BUCKETS
    assert [v["bucket"] for v in versions] == list(range(casc.BUCKETS))
    assert sum(v["entries"] for v in versions) == FACTS["entry_count"] == 1_042_994
    assert FACTS["newest_taken"] == [] and all(v["not_taken"] == [] for v in versions)
    for v in versions:
        assert v["file"] == f"{v['bucket']:02x}{v['version']:08x}.idx"


@pytest.mark.parametrize("bucket", range(16))
def test_the_header_hash_the_agent_stored_is_hashlittle_of_the_header(bucket: int) -> None:
    """Version 7, the bucket, the extra byte, the spec 4/5/9/30 and the
    maximum archive size, as the reader lays them out, hash to what the file
    on disk stored."""
    v = FACTS["versions"][bucket]
    assert v["header_size"] == 16
    header = struct.pack(
        "<HBBBBBBQ", 7, bucket, v["extra_bytes"], 4, 5, 9, 30, v["max_archive_size"]
    )
    assert casc.hashlittle(header) == v["header_hash"]


def test_every_content_key_was_in_the_bucket_its_bytes_give() -> None:
    rows = [r for r in FACTS["key_rows"] if "whose key is not" not in r["why"]]
    assert len(rows) == 7
    for row in rows:
        assert casc.bucket_of(bytes.fromhex(row["key"])) == row["file_bucket"], row


def test_the_shifted_entries_sit_one_bucket_on() -> None:
    """65 entries in each file have a key whose bucket is the one before
    the file's (segment headers by their look, **[verify]**)."""
    assert all(v["entries_one_bucket_on"] == 65 for v in FACTS["versions"])
    (row,) = [r for r in FACTS["key_rows"] if "whose key is not" in r["why"]]
    key = bytes.fromhex(row["key"])
    assert (casc.bucket_of(key) + 1) % casc.BUCKETS == row["file_bucket"]
    assert (row["size"], row["offset"]) == (30, 30 * row["file_bucket"])
    assert key.hex().endswith("00552169b01989")


def test_the_encoding_files_entry_is_its_encoded_size_and_a_record_header() -> None:
    (row,) = [r for r in FACTS["key_rows"] if r["why"] == "the build config's encoding key"]
    assert row["key"] == FACTS["encoding_ekey"][: casc.KEY_BYTES * 2]
    assert row["size"] == FACTS["encoding_size"][1] + RECORD_HEADER


def test_every_key_row_fits_the_entry_fields() -> None:
    for row in FACTS["key_rows"]:
        assert len(row["key"]) == casc.KEY_BYTES * 2
        assert 0 <= row["archive"] < 1 << 10 and 0 <= row["offset"] < 1 << 30
        assert 0 < row["size"] < 1 << 32
