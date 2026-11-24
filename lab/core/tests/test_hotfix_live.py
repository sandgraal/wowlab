"""`hotfix` on the owner's real hotfix cache, by hand (M22-23; ADR-0033 note
of 2026-11-23, layer c). Never run in CI (ADR-0012):

    WOWLAB_WOW_ROOT=<the install> uv run pytest -m live lab/core/tests/test_hotfix_live.py -s

The cache is the derived fixture's (`fixtures/macos-71037/derived/
hotfix-items.json`): the flavor folder's newest `Cache/ADB/<locale>/
DBCache.bin`, read through `hotfix.read` from the install root. Skipped
when the install has no row for the fixture's product, or when the cache
is of another build than the fixture's (the game patched since). The
header must be the recorded one; every `Item` and `ItemSparse` record with
data must decode to its exact length with the shipped layouts; each of the
five items must decode to its recorded name, levels, quality and slot; and
the read must open nothing for writing and leave the file as it was (L1).
The cache grows as the server sends more, so counts are checked as at
least the recorded ones.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from wowlab_core import hotfix

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _live_install import live_install

pytestmark = pytest.mark.live

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "macos-71037" / "derived"
FACTS: dict[str, Any] = json.loads((FIXTURE / "hotfix-items.json").read_text(encoding="utf-8"))
_WRITING = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
_OPENED: list[list[tuple[str, int]]] = []
_KEY = ("Display_lang", "ItemLevel", "RequiredLevel", "OverallQualityID", "InventoryType")


def _audit(event: str, args: tuple[Any, ...]) -> None:
    if event == "open" and _OPENED:
        path, _mode, flags = args
        if isinstance(path, (str, bytes, os.PathLike)):
            _OPENED[-1].append((os.fsdecode(path), flags if isinstance(flags, int) else 0))


sys.addaudithook(_audit)


def test_the_real_cache_reads_as_recorded() -> None:
    inst = live_install(FACTS, "hotfix")
    [flavor] = [f for f in inst.flavors if f.folder == FACTS["flavor"]]
    found = hotfix.find_caches(flavor.path)
    assert found, f"{FACTS['flavor']} has no hotfix cache"
    path = found[0]
    assert path.relative_to(flavor.path).as_posix() == FACTS["file"]
    before = path.stat()
    _OPENED.append([])
    try:
        cache = hotfix.read(path, hotfix.layouts(), root=inst.root)
    finally:
        opened = _OPENED.pop()
    if cache.build != FACTS["header"]["build"]:
        pytest.skip(f"the cache is of build {cache.build}; the fixture of {FACTS['build']}")
    assert cache.version == FACTS["header"]["version"]
    assert cache.records_in_file >= FACTS["records_in_file"]
    assert not [p for p, flags in opened if flags & _WRITING], opened
    after = path.stat()
    assert (after.st_size, after.st_mtime_ns) == (before.st_size, before.st_mtime_ns)
    layouts = hotfix.layouts()
    decoded: dict[tuple[str, int], dict[str, str]] = {}
    counts = dict.fromkeys(layouts, 0)
    for record in cache.records:  # file order, as gamedata applies them
        counts[record.table] += 1
        if record.status == hotfix.STATUS_ROW and record.data:
            decoded[(record.table, record.record_id)] = hotfix.decode_row(
                layouts[record.table], record
            )
    for name, recorded in FACTS["tables"].items():
        assert counts[name] >= recorded["records"], name
    for item in FACTS["items"]:
        got = decoded[("ItemSparse", item["id"])]
        want = item["ItemSparse"]["row"]
        assert {k: got[k] for k in _KEY} == {k: want[k] for k in _KEY}, item["id"]
        assert decoded[("Item", item["id"])]["ClassID"] == item["Item"]["row"]["ClassID"]
