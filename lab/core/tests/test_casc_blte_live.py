"""`casc`'s archive records and BLTE on the owner's install, by hand (M15-04;
ADR-0033 layer c). Never run in CI (ADR-0012):

    WOWLAB_WOW_ROOT=<the install> uv run pytest -m live lab/core/tests/test_casc_blte_live.py -s

Close the client first, and let the Battle.net app finish any update or
repair (both write `Data/data`). The tests read the records the derived
fixture (`fixtures/macos-70450/derived/casc-blte.json`) names and compare
what they find; they are skipped when the install has patched past that
build. They also hold L1 for the archive read: only `data.NNN` files are
opened, each read-only and closed, no `mmap`, nothing under `Data/data`
changed; and they print the encoding file's decode time (§17.7 wants the
number; the target is M15-05's).
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import mmap
import os
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from wowlab_core import casc
from wowlab_core.install import ENV_ROOT, discover

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "macos-70450" / "derived"
FACTS: dict[str, Any] = json.loads((FIXTURE / "casc-blte.json").read_text(encoding="utf-8"))
SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "casc_facts.py"


def _root() -> Path:
    value = os.environ.get(ENV_ROOT, "")
    if not value.strip():
        pytest.skip(f"set {ENV_ROOT} to the install to run the live casc tests")
    return Path(value)


def _state(folder: Path) -> dict[str, tuple[int, int]]:
    return {p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in folder.iterdir()}


def _storage() -> casc.CascStorage:
    storage = casc.open_storage(discover(_root()), FACTS["flavor"])
    if storage.report.version != FACTS["version"]:
        pytest.skip(
            f"the install is at {storage.report.version}; the fixture at {FACTS['version']}"
        )
    return storage


_AUDIT: list[list[tuple[str, int]]] = []


def _audit(event: str, args: tuple[Any, ...]) -> None:
    if event == "open" and _AUDIT:
        path, _mode, flags = args
        if isinstance(path, (str, bytes, os.PathLike)):
            _AUDIT[-1].append((os.fsdecode(path), flags if isinstance(flags, int) else 0))


sys.addaudithook(_audit)


@pytest.mark.live
def test_live_records_match_the_derived_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    storage = _storage()
    data_dir = storage.root / "Data" / "data"
    before = _state(data_dir)
    opened: list[tuple[str, int]] = []
    still_open: set[int] = set()
    real_open, real_close = os.open, os.close

    def spy_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        fd = real_open(path, flags, *args, **kwargs)
        still_open.add(fd)
        return fd

    def spy_close(fd: int) -> None:
        still_open.discard(fd)
        real_close(fd)

    def no_mmap(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("mmap is never used")

    monkeypatch.setattr(os, "open", spy_open)
    monkeypatch.setattr(os, "close", spy_close)
    monkeypatch.setattr(mmap, "mmap", no_mmap)
    _AUDIT.append(opened)
    try:
        for row in FACTS["records"]:
            key = bytes.fromhex(row["key"])
            entry = storage.index.find(key)
            assert entry == casc.IndexEntry(
                archive=row["archive"], offset=row["offset"], size=row["size"]
            )
            record = memoryview(casc.read_record(storage.root, entry))
            header = casc.parse_record_header(record, key, entry)
            assert header.model_dump() == row["record_header"]
            layout = casc.parse_blte(record[casc.RECORD_HEADER_SIZE :])
            assert [b.model_dump() for b in layout.blocks] == row["blte"]["blocks"]
            want = row["outcome"]
            if "error" in want:
                with pytest.raises(casc.EncryptedContent) as caught:
                    casc.read_encoded(storage, key)
                assert (caught.value.key_name, caught.value.block, caught.value.cipher) == (
                    want["key_name"],
                    want["block"],
                    want["cipher"],
                )
                continue
            started = time.perf_counter()
            decoded = casc.read_encoded(storage, key)
            seconds = time.perf_counter() - started
            assert decoded is not None
            assert len(decoded) == want["decoded_size"]
            assert hashlib.md5(decoded).hexdigest() == want["decoded_md5"]
            print(f"\n{row['why']}: {len(decoded)} bytes read and decoded in {seconds:.3f} s")
    finally:
        _AUDIT.remove(opened)
        monkeypatch.undo()
    assert not still_open, f"descriptors left open: {still_open}"
    assert opened, "the audit hook saw no open"
    assert all(Path(p).name.startswith("data.") for p, _ in opened)
    writing = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
    assert all(flags & writing == 0 for _, flags in opened)
    assert _state(data_dir) == before, "Data/data changed during the read (was the client open?)"


@pytest.mark.live
def test_live_encoding_file_is_its_content_key() -> None:
    storage = _storage()
    started = time.perf_counter()
    decoded = casc.read_encoded(storage, bytes.fromhex(storage.config.encoding_ekey))
    seconds = time.perf_counter() - started
    assert decoded is not None and decoded[:2] == b"EN"
    assert hashlib.md5(decoded).hexdigest() == storage.config.encoding_ckey
    print(f"\nencoding file: {len(decoded)} bytes read and decoded in {seconds:.3f} s")


@pytest.mark.live
def test_live_segment_headers_and_census_match() -> None:
    storage = _storage()
    spec = importlib.util.spec_from_file_location("casc_facts", SCRIPT)
    assert spec is not None and spec.loader is not None
    module: ModuleType = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    assert module._segment_headers(storage) == FACTS["segment_headers"]
    assert module._census(storage) == FACTS["census"]
