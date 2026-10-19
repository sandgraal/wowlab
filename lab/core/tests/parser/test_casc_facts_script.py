"""`scripts/casc_facts.py` on a constructed install (M15-03, M15-04; labelled
`constructed`): it writes the derived record (the index one, and with
`--blte` the archive one), holds no file bytes and no install path, refuses
an output inside an install or one already there, and changes nothing in
the install."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import random
import sys
from pathlib import Path
from types import ModuleType

import pytest
from _casc_build import (
    FLAVOR,
    PRODUCT,
    blte,
    e_block,
    idx_bytes,
    idx_name,
    key_in,
    make_install,
    n_block,
    record,
    tree_state,
    z_block,
)

from wowlab_core import casc

pytestmark = pytest.mark.parser

SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "casc_facts.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("casc_facts", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


casc_facts = _load()


def _run(root: Path, out: Path, *extra: str) -> int:
    args = ["--root", str(root), "--flavor", FLAVOR, "--captured", "2026-10-19", "--out", str(out)]
    return int(casc_facts.main([*args, *extra]))


def test_writes_the_derived_record_constructed(tmp_path: Path) -> None:
    root, _ = make_install(tmp_path / "install")
    before = tree_state(root)
    out = tmp_path / "out" / "casc-index.json"
    out.parent.mkdir()
    assert _run(root, out) == 0
    assert tree_state(root) == before
    record = json.loads(out.read_text(encoding="utf-8"))
    assert (record["product"], record["flavor"], record["build"]) == (PRODUCT, FLAVOR, 65432)
    assert (record["bucket_count"], record["entry_count"]) == (2, 10)
    assert record["encoding_size"] == {"decoded": 1000, "encoded": 970}
    assert [v["entries_from_previous_bucket"] for v in record["versions"]] == [0, 0]
    assert record["missing_buckets"] == [b for b in range(16) if b not in (3, 9)]
    assert record["key_rows"][0]["why"] == "the build config's encoding key"
    assert record["key_rows"][0]["size"] == 1000
    text = out.read_text(encoding="utf-8")
    assert str(tmp_path) not in text and "speech" not in text


def test_refuses_an_output_inside_the_install_constructed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, _ = make_install(tmp_path)
    before = tree_state(root)
    assert _run(root, root / "Data" / "facts.json") == 1
    assert "inside a game install" in capsys.readouterr().err
    assert tree_state(root) == before


def test_refuses_an_output_already_there_constructed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, _ = make_install(tmp_path / "install")
    out = tmp_path / "facts.json"
    out.write_text("kept", encoding="utf-8")
    assert _run(root, out) == 1
    assert "already there" in capsys.readouterr().err
    assert out.read_text(encoding="utf-8") == "kept"


def test_a_casc_error_is_one_line_and_writes_nothing_constructed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, _ = make_install(tmp_path / "install", indexes={"0000000001.idx": b"\x00" * 8})
    out = tmp_path / "facts.json"
    assert _run(root, out) == 1
    err = capsys.readouterr().err
    assert err.startswith("casc_facts: ") and err.count("\n") == 1
    assert not out.exists()


def _blte_install(tmp_path: Path) -> Path:
    """A constructed install whose index lists the build config's encoding
    file (a plain-and-zlib BLTE in `data.001`), one bucket-0 record with the
    whole key in its header, one bucket-0 encrypted record, and one segment
    header filed in bucket 0 (its key's bucket is 15)."""
    root, _ = make_install(tmp_path / "install", indexes={})
    (config_path,) = (p for p in (root / "Data" / "config").rglob("*") if p.is_file())
    config = casc.read_build_config(root, config_path.name)
    ekey = bytes.fromhex(config.encoding_ekey)
    rng = random.Random(1504)
    full = key_in(0, rng) + rng.randbytes(7)
    secret = key_in(0, rng) + bytes(7)
    segment = key_in(15, rng)
    archive = bytearray(record(segment, b"", flags=b"\x01\x00"))
    rows: dict[int, list[tuple[bytes, int, int, int]]] = {0: [(segment, 1, 0, 30)], 3: []}
    for key, data, bucket, whole in (
        (ekey, blte([n_block(b"EN"), z_block(b"pages" * 100)]), 3, False),
        (full, blte([z_block(b"model" * 50)]), 0, True),
        (secret, blte([e_block()]), 0, False),
    ):
        rec = record(key, data, full_key=whole)
        rows[bucket].append((key, 1, len(archive), len(rec)))
        archive += rec
    data_dir = root / "Data" / "data"
    for bucket, entries in rows.items():
        (data_dir / idx_name(bucket, 1)).write_bytes(idx_bytes(bucket, entries))
    (data_dir / "data.001").write_bytes(bytes(archive))
    return root


def test_writes_the_blte_record_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(casc_facts, "CENSUS_STEP", 1)  # every record, not every 256th
    root = _blte_install(tmp_path)
    before = tree_state(root)
    out = tmp_path / "out" / "casc-blte.json"
    out.parent.mkdir()
    assert _run(root, out, "--blte") == 0
    assert tree_state(root) == before
    facts = json.loads(out.read_text(encoding="utf-8"))
    assert facts["kind"] == "casc-blte-derived" and facts["build"] == 65432
    enc, full, secret = facts["records"]
    assert enc["why"] == "the build config's encoding file"
    assert [b["mode"] for b in enc["blte"]["blocks"]] == ["N", "Z"]
    plain = b"EN" + b"pages" * 100
    assert enc["outcome"] == {
        "decoded_size": len(plain),
        "decoded_md5": hashlib.md5(plain).hexdigest(),
    }
    assert full["record_header"]["hash_field"][18:].strip("0")
    assert secret["outcome"]["error"] == "EncryptedContent"
    assert secret["outcome"]["key_name"] == "50505078126ACB3E"
    (segment,) = facts["segment_headers"]
    assert (segment["size"], segment["record_header"]["flags"]) == (30, "0100")
    census = facts["census"]
    assert census["blocks_by_mode"] == {"E": 1, "N": 1, "Z": 2}
    assert census["refused"] == {"EncryptedContent": 1} and census["decoded"] == 2
    assert census["encrypted_key_names"] == {"50505078126ACB3E": 1}
    assert (census["records"], census["hash_field_full"], census["step"]) == (3, 1, 1)
    text = out.read_text(encoding="utf-8")
    assert str(tmp_path) not in text and "speech" not in text
