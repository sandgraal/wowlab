"""`casc.open_source`, `CascSource.stat` and `CascSource.read` on the owner's
install, by hand (M15-05; ADR-0033 layer c). Never run in CI (ADR-0012):

    WOWLAB_WOW_ROOT=<the install> uv run pytest -m live lab/core/tests/test_casc_source_live.py -s

Close the client first, and let the Battle.net app finish any update or
repair (both write `Data/data`). The tests compare the install with the
derived fixture (`fixtures/macos-70450/derived/casc-files.json`) and are
skipped when the install has patched past that build: the open's report,
every recorded file's state, the MD5 check on every local one (at least
ten), each of §17.1's states, the ModelFileData sample's split (the table
from the `gamedata` cache, or one fetch from wago.tools; skipped when it
cannot be had), and §6.4's targets: the open in at most 5 s and 300 reads in
at most 10 s, with the peak resident memory printed. They also hold L1:
every open read-only and closed, no `mmap`, nothing under `Data/data`
changed.
"""

from __future__ import annotations

import hashlib
import importlib.util
import itertools
import json
import mmap
import os
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from wowlab_core import casc
from wowlab_core.gamedata import GameDataError
from wowlab_core.install import ENV_ROOT, discover

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "macos-70450" / "derived"
FACTS: dict[str, Any] = json.loads((FIXTURE / "casc-files.json").read_text(encoding="utf-8"))
SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "casc_facts.py"
_WRITING = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND


def _root() -> Path:
    value = os.environ.get(ENV_ROOT, "")
    if not value.strip():
        pytest.skip(f"set {ENV_ROOT} to the install to run the live casc tests")
    return Path(value)


def _state(folder: Path) -> dict[str, tuple[int, int]]:
    return {p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in folder.iterdir()}


def _script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("casc_facts", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


FACTS_SCRIPT = _script()
_AUDIT: list[list[tuple[str, int]]] = []


def _audit(event: str, args: tuple[Any, ...]) -> None:
    if event == "open" and _AUDIT:
        path, _mode, flags = args
        if isinstance(path, (str, bytes, os.PathLike)):
            _AUDIT[-1].append((os.fsdecode(path), flags if isinstance(flags, int) else 0))


sys.addaudithook(_audit)


@pytest.fixture(scope="module")
def source() -> casc.CascSource:
    opened = casc.open_source(discover(_root()), FACTS["flavor"])
    if opened.report.index.version != FACTS["version"]:
        pytest.skip(
            f"the install is at {opened.report.index.version}; the fixture at {FACTS['version']}"
        )
    return opened


@pytest.fixture
def held(source: casc.CascSource, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """L1 while the test runs: every open read-only, every descriptor
    closed, no `mmap`, `Data/data` unchanged."""
    data_dir = source.storage.root / "Data" / "data"
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
        yield
    finally:
        _AUDIT.remove(opened)
        monkeypatch.undo()
    assert not still_open, f"descriptors left open: {still_open}"
    assert all(flags & _WRITING == 0 for _, flags in opened)
    assert all(Path(p).name.startswith("data.") for p, _ in opened)
    assert _state(data_dir) == before, "Data/data changed during the read (was the client open?)"


@pytest.mark.live
def test_live_open_matches_the_derived_fixture_and_its_target() -> None:
    data_dir = _root() / "Data" / "data"
    before = _state(data_dir)
    started = time.perf_counter()
    opened = casc.open_source(discover(_root()), FACTS["flavor"])
    seconds = time.perf_counter() - started
    r = opened.report
    if r.index.version != FACTS["version"]:
        pytest.skip(f"the install is at {r.index.version}; the fixture at {FACTS['version']}")
    assert (r.locale, r.locale_from, r.platform, r.platform_from) == (
        FACTS["locale"],
        FACTS["locale_from"],
        FACTS["platform"],
        FACTS["platform_from"],
    )
    enc = {k: v for k, v in FACTS["encoding"].items() if k in casc.EncodingHeader.model_fields}
    assert r.encoding.model_dump() == enc
    assert r.encoding_ekey == FACTS["encoding"]["read_by"]
    root = {k: v for k, v in FACTS["root"].items() if k in casc.RootReport.model_fields}
    root["skipped_blocks"] = tuple(tuple(p) for p in root["skipped_blocks"])
    assert r.root.model_dump() == root
    assert r.root_ekey == FACTS["root"]["read_by_hash"]
    assert seconds <= 5, f"the open took {seconds:.2f} s; §6.4's target is 5 s (stop and ask)"
    assert _state(data_dir) == before
    peak = FACTS_SCRIPT._peak_rss_mb()
    print(
        f"\nopen {seconds:.3f} s (index {r.index.index_seconds} s, encoding "
        f"{r.encoding_seconds} s, root {r.root_seconds} s); peak RSS {peak} MB"
    )


@pytest.mark.live
def test_live_every_recorded_file_has_its_state_and_its_bytes(
    source: casc.CascSource, held: None
) -> None:
    checked = 0
    for row in FACTS["rows"]:
        in_table = False if row["state"] == "unknown_id" else None
        stat = source.stat(row["fdid"], in_table=in_table)
        assert (stat.state, stat.words) == (row["state"], row["words"]), row["why"]
        assert stat.content_key == row["content_hash"]
        assert list(stat.encoding_keys) == row["encoding_hashes"]
        assert list(stat.key_names) == row["encryption_names"]
        if row["state"] == "local":
            data = source.read(row["fdid"])
            assert len(data) == row["decoded_size"]
            assert hashlib.md5(data, usedforsecurity=False).hexdigest() == row["content_hash"]
            checked += 1
        elif row["state"] == "encrypted":
            with pytest.raises(casc.EncryptedContent) as caught:
                source.read(row["fdid"])
            assert caught.value.key_name in row["encryption_names"]
        else:
            with pytest.raises(casc.FileUnavailable) as unavailable:
                source.read(row["fdid"])
            want = "not_in_build" if row["state"] == "unknown_id" else row["state"]
            assert unavailable.value.state == want
    assert checked >= 10
    assert {r["state"] for r in FACTS["rows"]} == set(casc.STATE_WORDS)
    print(f"\n{checked} real files read and matched to their content keys")


@pytest.mark.live
def test_live_three_hundred_reads_meet_the_target(source: casc.CascSource, held: None) -> None:
    step = max(1, len(source.root) // 300)
    ids = list(dict.fromkeys(itertools.islice(source.root.ids(), 0, None, step)))[:300]
    assert len(ids) == 300
    started = time.perf_counter()
    total = 0
    refused = 0
    for fdid in ids:
        try:
            total += len(source.read(fdid))
        except (casc.EncryptedContent, casc.FileUnavailable):
            refused += 1
    seconds = time.perf_counter() - started
    assert seconds <= 10, f"300 reads took {seconds:.2f} s; §6.4's target is 10 s (stop and ask)"
    assert source.reopens == 0
    peak = FACTS_SCRIPT._peak_rss_mb()
    print(f"\n300 reads: {total} bytes, {refused} refused, {seconds:.3f} s; peak RSS {peak} MB")


@pytest.mark.live
def test_live_the_model_file_data_sample_splits_as_recorded(
    source: casc.CascSource,
) -> None:
    script = FACTS_SCRIPT
    try:
        ids, _where, build = script._model_ids(None, FACTS["version"])
    except GameDataError as exc:
        pytest.skip(f"ModelFileData could not be had: {exc}")
    sample = script._sample(ids, 300)
    assert len(ids) == FACTS["model_file_data"]["ids_in_table"]
    assert build == FACTS["model_file_data"]["version"]
    states: dict[str, int] = {}
    for fdid in sample:
        state = source.stat(fdid).state
        states[state] = states.get(state, 0) + 1
    assert states == FACTS["model_file_data"]["states"]
    print(f"\nModelFileData sample of {len(sample)}: {states}")
