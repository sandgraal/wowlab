"""An oversized addon TOC on the owner's own install, read by its header
(M22-21). Never run in CI (ADR-0012); the owner runs it with QuestieDB
installed (its Forever TOC was 18,902,713 bytes on 2026-11-23):

    WOWLAB_WOW_ROOT="/path/to/World of Warcraft" uv run pytest -m live lab/core/tests/test_toc_oversized_live.py -s -p no:cacheprovider

For every flavor folder it walks the addons with `layout` and checks them
with `addoncheck`, read-only: nothing is written anywhere. Every TOC over the
read limit must come back read in part (its header, within the limit,
ending at a line break, with no file line), and QuestieDB's must give its
Interface, Title and Version, and no dependency taken from an `## X-` line.
It prints the addon names, TOC sizes and the header fields; never a path,
an account folder or a flavor folder.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from wowlab_core import addoncheck, install, layout
from wowlab_core.toc import MAX_TOC_BYTES

QUESTIE = "questiedb"


@pytest.mark.live
def test_live_an_oversized_toc_gives_its_header() -> None:
    value = os.environ.get(install.ENV_ROOT, "")
    if not value.strip():
        pytest.skip(f"set {install.ENV_ROOT} to the install that holds QuestieDB")
    inst = install.discover(Path(value))
    questie_seen = 0
    for number, flavor in enumerate(inst.flavors, 1):
        lay = layout.Layout.for_flavor(flavor, inst.root)
        check = addoncheck.check_addons(lay, flavor.version)
        checked = {(a.name, t.file): t for a in check.addons for t in a.tocs}
        for addon in lay.addons():
            for toc in addon.tocs:
                if toc.size <= MAX_TOC_BYTES:
                    continue
                doc = toc.document
                assert toc.error is None, (addon.name, toc.file, toc.error)
                assert doc is not None and doc.partial is True, (addon.name, toc.file)
                kept = doc.to_bytes()
                assert len(kept) <= MAX_TOC_BYTES and kept.endswith(b"\n")
                assert doc.files == ()
                said = checked.get((addon.name, toc.file))
                print(
                    f"flavor#{number}: {addon.name}/{toc.file}: {toc.size:,} bytes, header "
                    f"{len(kept):,} bytes in {len(doc.lines)} lines; Interface "
                    f"{doc.get('Interface')!r}, Title {doc.get('Title')!r}, Version "
                    f"{doc.get('Version')!r}, Dependencies {doc.list_value('Dependencies')!r}, "
                    f"RequiredDeps {doc.list_value('RequiredDeps')!r}; check: "
                    + (
                        "not one the client may read"
                        if said is None
                        else f"interface {said.interface_status}, partial {said.partial}, "
                        f"needs {[d.name for d in said.dependencies]}"
                    )
                )
                if addon.name.casefold() != QUESTIE:
                    continue
                questie_seen += 1
                assert doc.interface is not None and doc.interface.versions
                assert doc.get("Title")
                assert doc.get("Version")
                for key in ("Dependencies", "RequiredDeps"):
                    for name in doc.list_value(key):
                        assert len(name) < 64, f"{key} looks like base64 data: {name[:40]}"
                if said is not None:
                    assert said.partial is True and said.interface_status != "unread"
    if not questie_seen:
        pytest.skip("no flavor holds a QuestieDB TOC larger than the read limit")
