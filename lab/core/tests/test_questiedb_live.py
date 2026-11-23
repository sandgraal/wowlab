"""QuestieDB read from the owner's own install (M22-08). Never run in CI;
the owner runs it by hand:

    WOWLAB_WOW_ROOT="/path/to/World of Warcraft" \\
        uv run pytest -m live lab/core/tests/test_questiedb_live.py -s

It is skipped when `$WOWLAB_WOW_ROOT` is not set or no flavor folder holds a
QuestieDB. It reads the installed copy, read-only, and checks the facts the
constructed slices of `test_questiedb.py` and `test_lookup.py` stand for
(`docs/LAB_FORMATS.md` §13): the TOC is a baked store; every kind's id
header decodes; every entity's row decodes, with a name; the drop tables
read as literals; and what is left out is what the constructed slices say.
Nothing from the install is written anywhere, and nothing is fetched.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from _live_install import live_root

from wowlab_core import lookup, questiedb
from wowlab_core.install import discover


def _flavors_with_questiedb() -> list[tuple[Path, str | None, Path]]:
    found = discover(live_root("QuestieDB"))
    out: list[tuple[Path, str | None, Path]] = []
    for flavor in found.flavors:
        _, tocs = questiedb.find_toc(flavor.path / "Interface" / "AddOns")
        out += [(flavor.path, flavor.version, toc) for toc in tocs]
    if not out:
        pytest.skip("no flavor folder of the install holds a QuestieDB")
    return out


@pytest.mark.live
def test_live_the_installed_questiedb_reads_as_data() -> None:
    for _, _, toc in _flavors_with_questiedb():
        started = time.perf_counter()
        store = questiedb.open_store(toc)
        assert store.baked, f"{toc.name} holds no baked store"
        counts = {kind: sum(1 for _ in store.names(kind)) for kind in questiedb.KINDS}
        took = time.perf_counter() - started
        assert all(counts.values()), counts
        assert store.unreadable == {}, store.unreadable
        info = store.info()
        print(f"\n{info.folder}/{info.toc} {info.version} ({info.mode}): {counts} in {took:.2f}s")
        tables, left = questiedb.read_drop_tables(store)
        assert tables, "no literal drop table was read"
        assert all(t.chances for t in tables)
        for part in [*store.left_out(), *left]:
            print(f"  left out: {part.part}: {part.why}")
        assert [p.part for p in store.left_out()] == [
            "corrections the addon applies in game for each character",
            "names in other languages",
        ]


@pytest.mark.live
def test_live_a_lookup_answers_from_the_installed_copy() -> None:
    flavor, version, _ = _flavors_with_questiedb()[0]
    started = time.perf_counter()
    index = lookup.LookupIndex.for_flavor(flavor, version)
    hits, total = index.search("claw", kinds=["item"], limit=5)
    took = time.perf_counter() - started
    print(f"\nsearch 'claw' among items: {total} matches in {took:.2f}s")
    assert total > 0 and hits[0].item is not None and hits[0].item.in_questiedb
    assert index.questie.state == "read" and index.questie.unreadable_rows == 0
