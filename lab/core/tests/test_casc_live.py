"""`casc` on the owner's install, by hand (M15-03; ADR-0033 layer c). Never
run in CI (ADR-0012):

    WOWLAB_WOW_ROOT=<the install> uv run pytest -m live lab/core/tests/test_casc_live.py

Close the client first, and let the Battle.net app finish any update or
repair (it writes Data/data too): both rewrite the index. The tests
compare the install with the derived fixture
(`fixtures/macos-70450/derived/casc-index.json`) and are skipped when the
install has patched past that build. They also hold §17.1's first target
(the index loaded in at most 2 s) and L1: nothing opened but the build
config and the taken `.idx` files, every one read-only and closed, by any
API (the "open" audit event); no file under `Data/data` changed by the
read.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from wowlab_core import casc
from wowlab_core.install import ENV_ROOT, discover

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "macos-70450" / "derived"
FACTS: dict[str, Any] = json.loads((FIXTURE / "casc-index.json").read_text(encoding="utf-8"))
TARGET_SECONDS = 2.0  # docs/LAB_PLAN.md §17.1


def _root() -> Path:
    value = os.environ.get(ENV_ROOT, "")
    if not value.strip():
        pytest.skip(f"set {ENV_ROOT} to the install to run the live casc tests")
    return Path(value)


# Every open in the process raises the "open" audit event, whatever API it
# goes through. The hook cannot be removed, so it records only while armed.
_AUDIT: list[list[tuple[str, int]]] = []


def _audit(event: str, args: tuple[Any, ...]) -> None:
    if event == "open" and _AUDIT:
        path, _mode, flags = args
        if isinstance(path, (str, bytes, os.PathLike)):
            _AUDIT[-1].append((os.fsdecode(path), flags if isinstance(flags, int) else 0))


sys.addaudithook(_audit)


def _state(folder: Path) -> dict[str, tuple[int, int]]:
    return {p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in folder.iterdir()}


@pytest.mark.live
def test_live_index_matches_the_derived_fixture(monkeypatch: pytest.MonkeyPatch) -> None:
    root = _root()
    install = discover(root)
    data = root / "Data" / "data"
    before = _state(data)
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

    monkeypatch.setattr(os, "open", spy_open)
    monkeypatch.setattr(os, "close", spy_close)
    _AUDIT.append(opened)
    try:
        storage = casc.open_storage(install, FACTS["flavor"])
    finally:
        _AUDIT.remove(opened)
        monkeypatch.undo()
    assert not still_open, f"descriptors left open: {still_open}"

    report = storage.report
    names = {Path(p).name for p, _ in opened}
    assert names == {storage.config.key, *(v.file for v in report.versions)}
    assert not names & {"shmem", "index.lock.0"}
    writing = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
    assert all(flags & writing == 0 for _, flags in opened)
    assert _state(data) == before, "Data/data changed during the read (was the client open?)"
    assert report.index_seconds <= TARGET_SECONDS, report.index_seconds

    if report.version != FACTS["version"]:
        pytest.skip(f"the install is at {report.version}; the fixture at {FACTS['version']}")
    assert (report.product, report.build_key) == (FACTS["product"], FACTS["build_key"])
    assert (report.root, report.encoding_ckey, report.encoding_ekey) == (
        FACTS["root"],
        FACTS["encoding_ckey"],
        FACTS["encoding_ekey"],
    )
    assert (report.bucket_count, report.entry_count) == (
        FACTS["bucket_count"],
        FACTS["entry_count"],
    )
    assert list(report.missing_buckets) == FACTS["missing_buckets"]
    assert list(report.skipped_files) == FACTS["skipped_files"]
    for got, want in zip(report.versions, FACTS["versions"], strict=True):
        assert got.model_dump(mode="json") == want
    for row in FACTS["key_rows"]:
        key = bytes.fromhex(row["key"])
        want_entry = casc.IndexEntry(archive=row["archive"], offset=row["offset"], size=row["size"])
        if casc.bucket_of(key) == row["file_bucket"]:
            assert storage.index.find(key) == want_entry
        else:
            i = storage.index.keys(row["file_bucket"]).index(key)
            assert storage.index.entry_at(row["file_bucket"], i) == (key, want_entry)
