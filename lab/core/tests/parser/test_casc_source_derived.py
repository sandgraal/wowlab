"""`casc`'s encoding, root and `CascSource` against the derived fixture of
the owner's install (M15-05; ADR-0033 layer b): what `scripts/casc_facts.py
--files` read at 1.60.1.70450, never a byte of a file. Provenance:
`fixtures/README.md`, row `macos-70450/derived/casc-files.json`.

The checks tie the record to facts written by other runs or other
documents, and to the reader's stated rules: the encoding header against
the decoded size the build config gives (`casc-index.json`, M15-03); the
locale's and platform's flags against `casc.LOCALE_FLAGS` and
`casc.PLATFORM_SKIP`; the root's counts against each other and the 1,244,477
ids `docs/LAB_PLAN.md` §17.7 measured (with `LowViolence` kept); every
state of §17.1 met with its words; ten or more real files read and matched
to their content keys; the targets of §6.4 on the recorded timings; and the
evidence behind `casc.ROOT_FLAG_1`'s order.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from wowlab_core import casc

pytestmark = pytest.mark.parser

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "macos-70450" / "derived"
FACTS: dict[str, Any] = json.loads((FIXTURE / "casc-files.json").read_text(encoding="utf-8"))
INDEX: dict[str, Any] = json.loads((FIXTURE / "casc-index.json").read_text(encoding="utf-8"))
HEX32 = re.compile(r"[0-9a-f]{32}")
PAGE_ROW = 32  # a page index row: the page's first content key and its MD5


def _rows(state: str) -> list[dict[str, Any]]:
    return [r for r in FACTS["rows"] if r["state"] == state]


def test_the_record_is_the_build_the_index_record_names() -> None:
    assert FACTS["kind"] == "casc-files-derived" and FACTS["schema"] == 1
    for field in ("flavor", "product", "version", "build"):
        assert FACTS[field] == INDEX[field]
    assert FACTS["encoding"]["read_by"] == INDEX["encoding_ekey"]


def test_the_locale_and_platform_came_from_the_install_and_their_flags_are_the_readers() -> None:
    assert (FACTS["locale_from"], FACTS["platform_from"]) == ("install", "install")
    assert casc.LOCALE_FLAGS[FACTS["locale"]] == FACTS["locale_flag"]
    assert FACTS["root"]["locale_flag"] == FACTS["locale_flag"]
    assert int(FACTS["platform_mask"], 16) == casc.PLATFORM_SKIP[FACTS["platform"]]
    assert FACTS["root"]["platform"] == FACTS["platform"]


def test_nothing_of_the_tags_cell_but_the_locale_is_recorded() -> None:
    text = (FIXTURE / "casc-files.json").read_text(encoding="utf-8")
    for token in ("geoip", "acct-", "speech?", "text?", "/Users/", "\\\\"):
        assert token not in text


def test_the_encoding_header_fits_the_decoded_size_the_build_config_gives() -> None:
    """The header, the ESpec block and both page sets (index rows and
    pages) end inside the decoded file, less than one page short of it (the
    rest is the encoding file's own ESpec string)."""
    enc = FACTS["encoding"]
    assert (enc["version"], enc["ckey_size"], enc["ekey_size"]) == (1, 16, 16)
    pages = enc["ckey_page_count"] * (PAGE_ROW + enc["ckey_page_size"])
    pages += enc["espec_page_count"] * (PAGE_ROW + enc["espec_page_size"])
    end = 22 + enc["espec_size"] + pages
    decoded = INDEX["encoding_size"]["decoded"]
    assert end <= decoded < end + enc["espec_page_size"]


def test_one_content_key_may_have_several_encoding_keys() -> None:
    counts = {int(k): v for k, v in FACTS["encoding"]["encoding_hashes_per_content"].items()}
    assert counts[1] > 2_000_000 and max(counts) >= 2


def test_the_roots_counts_add_up() -> None:
    root = FACTS["root"]
    assert (root["version"], root["header_size"]) == (2, 24)
    assert root["records"] == root["total_files"]
    skipped = dict(root["skipped_blocks"])
    assert set(skipped) <= {"locale", "platform", "low_violence"}
    assert root["blocks_taken"] + sum(skipped.values()) == root["blocks"]
    assert root["encrypted_blocks_taken"] <= root["encrypted_blocks"]
    assert root["unique_ids_taken"] <= root["records_taken"] <= root["records"]
    dups = sum(root["flags_of_ids_in_more_than_one_block"].values())
    assert dups == root["ids_in_more_than_one_block"]
    assert root["ids_whose_rows_differ_in_content"] <= dups


def test_the_plans_count_is_the_one_with_low_violence_kept() -> None:
    """§17.7 measured 1,244,477 ids after filtering; the reader also skips
    `LowViolence` (wow.export does, from memory), which leaves fewer."""
    root = FACTS["root"]
    assert root["unique_ids_with_low_violence"] == 1_244_477
    assert root["unique_ids_taken"] < root["unique_ids_with_low_violence"]
    assert dict(root["skipped_blocks"])["low_violence"] > 0


def test_every_state_is_met_with_its_words() -> None:
    states = {r["state"] for r in FACTS["rows"]}
    assert states == {"local", "not_local", "not_in_build", "encrypted", "unknown_id"}
    for row in FACTS["rows"]:
        assert row["words"] == casc.STATE_WORDS[row["state"]]


def test_ten_real_files_matched_their_content_keys() -> None:
    local = _rows("local")
    assert len(local) >= 10
    for row in local:
        assert row["decoded_md5_is_content_hash"] is True
        assert row["decoded_size"] == row["size"]
        assert row["local_encoding_hash"] in row["encoding_hashes"]
        assert row["content_hash"] in row["content_hashes"]
        assert HEX32.fullmatch(row["content_hash"])
        index = row["index"]
        assert index["size"] > casc.RECORD_HEADER_SIZE and index["archive"] < 1024


def test_the_files_not_read_say_why() -> None:
    (encrypted,) = _rows("encrypted")
    assert encrypted["encryption_names"]
    assert all(re.fullmatch(r"[0-9A-F]{16}", n) for n in encrypted["encryption_names"])
    assert any(int(f, 16) & casc.ROOT_ENCRYPTED for f in encrypted["root_content_flags"])
    (not_local,) = _rows("not_local")
    assert not_local["encoding_hashes"] and "index" not in not_local
    skipped, from_models = _rows("not_in_build")
    assert skipped["note"].startswith("the root lists it only for another locale")
    assert from_models["note"] == "the root does not list it"
    (unknown,) = _rows("unknown_id")
    assert unknown["note"] == "the root does not list it" and unknown["root_content_flags"] == []


def test_the_census_settles_the_streaming_question() -> None:
    """§17.1's middle state ("not in your client's local files") is real but
    small: twenty ids of the root's 1.24 M, none of 300 ModelFileData ids."""
    census = FACTS["local_census"]
    assert census["unique_ids"] == FACTS["root"]["unique_ids_taken"]
    assert census["with_a_local_copy"] + census["with_no_local_copy"] == census["unique_ids"]
    assert census["with_no_local_copy"] == 20
    models = FACTS["model_file_data"]
    assert sum(models["states"].values()) == models["sampled"] == 300
    assert models["states"] == {"local": 157, "not_in_build": 143}


def test_rows_with_flag_1_are_the_ones_the_client_did_not_fetch() -> None:
    """The evidence for `casc.ROOT_FLAG_1`'s place in the order: of the ids
    with two rows, every one whose only local copy is one row has it in the
    row without bit 0x1."""
    patterns = FACTS["local_census"]["ids_in_more_than_one_block"]
    assert casc.ROOT_FLAG_1 == 0x1
    assert patterns["absent (0x1) + local, different content"] > 90_000
    for pattern in patterns:
        rows = pattern.split(", ")[0].split(" + ")
        local = [r for r in rows if r.startswith("local")]
        if len(local) == 1:
            assert local == ["local"], pattern


def test_the_targets_were_met_on_the_day() -> None:
    """§6.4 and §17.1: the index in at most 2 s, the open in at most 5 s,
    300 reads in at most 10 s; resident memory measured."""
    t = FACTS["timings"]
    assert t["index_seconds"] <= 2
    assert t["open_seconds"] <= 5
    assert t["encoding_seconds"] + t["root_seconds"] <= t["open_seconds"]
    assert t["reads"]["files"] == 300 and t["reads"]["seconds"] <= 10
    memory = FACTS["memory"]
    assert memory["peak_rss_mb_after_open"] > 0 and memory["reopens"] == 0
