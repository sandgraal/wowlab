"""The Practice tab (M22-20): the page's view over `wowlab log practice`.

Read from the shipped templates: the view's shell, and its own section of
`page.js` (from its heading to the page's start call), which is the only
code that names its elements. What the command reports is graded in
`test_practice.py`; the server's run rules in the other `test_ui_*` files.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_TEMPLATES = Path(__file__).resolve().parents[1] / "src" / "wowlab_core" / "ui" / "templates"
_HTML = (_TEMPLATES / "page.html").read_text(encoding="utf-8")
_JS = (_TEMPLATES / "page.js").read_text(encoding="utf-8")


def _section() -> str:
    start = _JS.index("// ─── practice (M22-20) ───")
    return _JS[start : _JS.index("\n  load();\n})();", start)]


def _view() -> str:
    start = _HTML.index('<section id="practice-view"')
    return _HTML[start : _HTML.index('<section id="backups-view"', start)]


def test_the_tab_sits_under_characters_and_starts_hidden() -> None:
    assert re.search(
        r'<button id="tab-practice" class="tab" type="button" role="tab" '
        r'aria-selected="false" aria-controls="practice-view">Practice</button>',
        _HTML,
    )
    assert re.search(r'<section id="practice-view" role="tabpanel"[^>]*\bhidden\b', _HTML)
    sec = re.search(r'<button id="sec-characters"[^>]*aria-controls="([^"]+)"', _HTML)
    assert sec is not None and "practice-view" in sec.group(1).split()
    view = _view()
    assert '<h2 class="page-title">Practice</h2>' in view
    assert '<p id="practice-status" role="status"></p>' in view
    assert re.search(r'<div id="practice-offer" class="names-offer" hidden>', view)


def test_the_view_registers_itself_and_draws_only_its_own_elements() -> None:
    section = _section()
    for line in (
        'SECTIONS.characters.push("practice");',
        'TABS.push("practice");',
        'VIEWS.push("practice");',
    ):
        assert line in section
    ids = set(re.findall(r'byId\("(practice-[a-z-]+|tab-practice)"\)', section))
    assert ids and all(f'id="{i}"' in _HTML for i in ids)
    outside = _JS.replace(section, "")
    assert not re.search(r'byId\("practice-', outside)


def test_it_runs_only_log_practice_and_the_timing_download() -> None:
    section = _section()
    used = {
        tuple(json.loads("[" + inside + "]"))
        for inside in re.findall(r"runJson\(\[([^\]]*)\]", section)
    }
    assert used == {("log", "practice"), ("db2", "fetch")}
    # The download's table and version are the command's own `fetch`.
    assert (
        'runJson(["db2", "fetch"], { table: report.fetch[2], build: report.fetch[4] })' in section
    )


def test_a_new_flavor_resets_the_view() -> None:
    start = _JS.index("  function switchRunFlavor(folder) {")
    body = _JS[start : _JS.index("\n  }\n", start)]
    assert "resetPractice();" in body
    reset = _section()[_section().index("function resetPractice()") :]
    reset = reset[: reset.index("\n  }\n")]
    for line in (
        "practiceView.report = null;",
        "forgetPracticeLog();",
        'byId("practice-segments").replaceChildren();',
        'byId("practice-summary").hidden = true;',
        'if (tabShown === "practice") loadPractice(null);',
    ):
        assert line in reset, line


def test_log_text_reaches_the_page_only_as_text() -> None:
    section = _section()
    for unsafe in (
        "innerHTML",
        "outerHTML",
        "insertAdjacentHTML",
        "document.write",
        ".href",
        "setAttribute",
    ):
        assert unsafe not in section, unsafe
    assert "visible(" in section
