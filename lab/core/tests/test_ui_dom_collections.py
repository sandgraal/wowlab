"""No Array method on a DOM collection in the `wowlab ui` page (the hub-filter
crash, owner report 2026-11-13).

`picker.options.some(...)` threw in the browser: `select.options` is an
HTMLOptionsCollection, `childNodes` and `querySelectorAll(...)` a NodeList,
`children`, `rows` and `cells` an HTMLCollection, and none of them is an
Array. The throw stopped the page's start-up, so the Account tab listed no
character. The Node runs of page.js in the other `test_ui_*` files did not
see it because their stand-in DOM handed back real Arrays; it now hands back
a collection that is not one (`DomList` in `test_ui_explorer.py`), and this
file holds the two checks that do not need a server:

- page.js, read as text, calls no Array method on a DOM collection;
- the stand-in's collections refuse Array methods, as a browser's do.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest
import test_ui_explorer as first_stage

_PAGE = first_stage._TEMPLATES / "page.js"

# What an Array has and none of the DOM collections has. `forEach` is
# checked apart: a NodeList has it, an HTMLCollection and an
# HTMLOptionsCollection have not.
_ARRAY_ONLY = [
    "at",
    "concat",
    "copyWithin",
    "every",
    "fill",
    "filter",
    "find",
    "findIndex",
    "findLast",
    "findLastIndex",
    "flat",
    "flatMap",
    "includes",
    "indexOf",
    "join",
    "lastIndexOf",
    "map",
    "pop",
    "push",
    "reduce",
    "reduceRight",
    "reverse",
    "shift",
    "slice",
    "some",
    "sort",
    "splice",
    "toReversed",
    "toSorted",
    "toSpliced",
    "unshift",
    "with",
]

# Always a DOM collection, whatever the receiver.
_DOM = r"\.(?:options|selectedOptions|childNodes|cells|querySelectorAll\([^()]*\)|getElementsBy(?:ClassName|TagName|Name)\([^()]*\))"
# Without `forEach`.
_NO_FOR_EACH = (
    r"\.(?:options|selectedOptions|cells|getElementsBy(?:ClassName|TagName|Name)\([^()]*\))"
)
# `children` and `rows` name data in the page too (the explorer's tree nodes,
# a report's rows), so a receiver of one of them is refused unless it is
# listed here, with what it is.
_AMBIGUOUS = r"([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*)\.(children|rows)"
_DATA = {
    "node.children": "a tree node the explorer builds from `tree --json` (an Array)",
    "report.rows": "the rows of `char list --json`",
    "hub.report.rows": "the hub's filtered copy of those rows (an Array)",
}


def _methods(names: list[str]) -> str:
    return r"\s*\.\s*(" + "|".join(names) + r")\s*\("


def _line(text: str, at: int) -> str:
    number = text.count("\n", 0, at) + 1
    return f"page.js:{number}: " + text.splitlines()[number - 1].strip()


def _found(text: str) -> list[str]:
    bad = []
    for m in re.finditer(_DOM + _methods(_ARRAY_ONLY), text):
        bad.append(_line(text, m.start()))
    for m in re.finditer(_NO_FOR_EACH + _methods(["forEach"]), text):
        bad.append(_line(text, m.start()))
    for m in re.finditer(_AMBIGUOUS + _methods([*_ARRAY_ONLY, "forEach"]), text):
        receiver = m.group(1) + "." + m.group(2)
        if receiver not in _DATA:
            bad.append(_line(text, m.start()))
    # A call's or an index's result is never on the list.
    for m in re.finditer(r"[)\]]\.(?:children|rows)" + _methods([*_ARRAY_ONLY, "forEach"]), text):
        bad.append(_line(text, m.start()))
    return bad


def test_the_page_calls_no_array_method_on_a_dom_collection() -> None:
    """`Array.from(x)` or `[...x]` first; a `for…of` loop and `.length` and
    `[i]` work on every collection as they are."""
    bad = _found(_PAGE.read_text(encoding="utf-8"))
    assert not bad, "an Array method on a DOM collection throws in the browser:\n" + "\n".join(bad)


def test_every_data_receiver_listed_is_still_in_the_page() -> None:
    """The list of `children`/`rows` receivers that are data stays exact: an
    entry the page no longer has is removed rather than left to excuse a
    later DOM use of the same name."""
    text = _PAGE.read_text(encoding="utf-8")
    seen = {
        m.group(1) + "." + m.group(2)
        for m in re.finditer(_AMBIGUOUS + _methods([*_ARRAY_ONLY, "forEach"]), text)
    }
    assert set(_DATA) <= seen, sorted(set(_DATA) - seen)


@pytest.mark.parametrize(
    "line",
    [
        "picker.value = picker.options.some((o) => o.value === value) ? value : '';",
        "const n = select.options\n      .map((o) => o.value);",
        "for (const r of table.querySelectorAll('tr').filter(Boolean)) {}",
        "list.childNodes.find((c) => c);",
        "select.options.forEach((o) => o);",
        "row.cells.map((c) => c.textContent);",
        "byId('hub-table').rows.some((r) => r);",
        "box.children.filter((c) => c.hidden);",
        "document.getElementsByTagName('li').map((li) => li);",
    ],
    ids=lambda s: s.split("(")[0][:40],
)
def test_the_check_finds_the_calls_constructed(line: str) -> None:
    """Constructed lines: each is the kind of call that crashed the page."""
    assert _found(line), line


@pytest.mark.parametrize(
    "line",
    [
        "picker.value = Array.from(picker.options).some((o) => o.value === value) ? value : '';",
        "for (const o of select.options) o.selected = false;",
        "table.querySelectorAll('button')[at].focus();",
        "list.querySelectorAll('li').forEach((li) => li.remove());",
        "const kept = node.children.filter((c) => keep.has(c));",
        "while (select.options.length > 1) select.remove(1);",
    ],
    ids=lambda s: s.split("(")[0][:40],
)
def test_the_check_lets_safe_calls_pass_constructed(line: str) -> None:
    assert not _found(line), line


def test_the_stand_in_dom_refuses_array_methods_as_a_browser_does() -> None:
    """The Node runs of page.js in the other `test_ui_*` files see an
    Array method on `options`, `childNodes` or `querySelectorAll(...)` throw,
    as the browser does. Skipped when `node` is not on PATH."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not on PATH: the stand-in DOM is graded under Node")
    harness = first_stage._HARNESS
    start = harness.index("class DomList {")
    end = harness.index("\nclass El {", start)
    script = (
        harness[start:end]
        + """
const options = new DomList([{ value: "a" }, { value: "b" }], false);
const nodes = new DomList([{ value: "a" }], true);
const out = {
  someThrows: (() => { try { options.some(() => true); return false; } catch (e) { return e instanceof TypeError; } })(),
  optionsForEach: typeof options.forEach,
  nodesForEach: typeof nodes.forEach,
  isArray: Array.isArray(options) || Array.isArray(nodes),
  fromWorks: Array.from(options).some((o) => o.value === "b"),
  spread: [...options].length,
  index: options[1].value,
  length: options.length,
  item: options.item(0).value,
};
process.stdout.write(JSON.stringify(out));
"""
    )
    done = subprocess.run(
        [node, "-e", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == {
        "someThrows": True,
        "optionsForEach": "undefined",
        "nodesForEach": "function",
        "isArray": False,
        "fromWorks": True,
        "spread": 2,
        "index": "b",
        "length": 2,
        "item": "a",
    }
