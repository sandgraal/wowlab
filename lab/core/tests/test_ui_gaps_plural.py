"""M16-28: the Gaps tab's counts say "folders", not "undefined".

`page.js` declared `plural` twice in one scope: the gaps tab's
`plural(n, word)` and the character panels' `plural(n, one, many)`. A
function declaration is hoisted and the later one wins, so every count the
gaps tab drew with more than one of a thing read "2 undefined". The gaps
one is now `pluralWord`; these tests keep it that way.

- a UI test draws the gaps tab over the capture with two folders not
  entered under one row (the capture's two addon folders) and a gap folder
  holding two folders, and finds "2 folders" and no "undefined";
- a grep test fails on any name declared by two `function` statements in
  `page.js`.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
import test_ui_explorer_stage2 as stage2
from test_ui_explorer import _TEMPLATES, _drive, _scenario

from wowlab_core import ui

# The first stages' fixtures: a stand-in home, the copied capture, the real
# server over the real CLI, and the capture with gaps.
home = stage2.home
install = stage2.install
server = stage2.server
gapped = stage2.gapped

PAGE_JS = Path(ui.__file__).resolve().parent / "templates" / "page.js"
DECLARATION = re.compile(r"^\s*(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)\s*\(", re.M)


@pytest.fixture
def gapped_folders(gapped: Path) -> Path:
    """`gapped`, with two folders inside its unknown folder, so the group's
    counts name more than one folder (constructed)."""
    stuff = gapped / stage2.FLAVOR / "m13-27t-stuff"
    for name in ("m16-28-one", "m16-28-two"):
        (stuff / name).mkdir()
        (stuff / name / "c.txt").write_bytes(b"c")
    return gapped


def test_gaps_counts_name_their_word_constructed(
    server: Any, gapped_folders: Path, tmp_path: Path
) -> None:
    """Gaps drawn with more than one folder in a group, and two folders not
    entered under one row, read "2 folders" and never "undefined"."""
    scenario, _ = _scenario(server, [], [{"tab": "gaps"}])

    gaps = json.loads(scenario["gaps"])
    rows = Counter(str(item.get("entry_id") or "") for item in gaps["not_entered"])
    assert any(n > 1 for n in rows.values()), rows
    assert any(g.get("folders", 0) > 1 for g in gaps["groups"]), gaps["groups"]

    out = _drive(tmp_path / "node", scenario)
    view = out["steps"][0]
    assert "missing" not in view, view
    texts = [view["text"], *(g["text"] for g in view["groups"])]
    for text in texts:
        assert "undefined" not in text, text
    assert "2 folders" in view["text"], view["text"]
    assert any("2 folders" in g["text"] for g in view["groups"]), [
        g["text"] for g in view["groups"]
    ]


def _duplicates(script: str) -> list[str]:
    names = Counter(DECLARATION.findall(script))
    return sorted(name for name, n in names.items() if n > 1)


def test_page_js_declares_each_function_once() -> None:
    """Two `function` statements with one name in `page.js`: the later one
    silently replaces the earlier everywhere in their scope."""
    assert PAGE_JS == _TEMPLATES / "page.js"
    assert _duplicates(PAGE_JS.read_text("utf-8")) == []


def test_the_duplicate_check_finds_a_duplicate_constructed() -> None:
    """The check itself: two declarations of one name are found, one is not."""
    script = "function a(x) {}\n  function b() {}\n  async function a(y, z) {}\n"
    assert _duplicates(script) == ["a"]
    assert _duplicates("function a() {}\nfunction b() {}\n") == []
