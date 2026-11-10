"""A doc that a test reads is never treated as docs-only by CI (M16-83).

`.github/workflows/ci.yml` skips the test jobs when a PR touches only
documentation (`docs/**/*.md` and three root files). Some of those documents
are graded by tests, such as `docs/INSTALL.md` by `test_doc_examples.py` and
`docs/LAB_PLAN.md` by the addon suite. A PR touching only one of them must
run the tests, so ci.yml lists them in a second filter, `doc_tests`, and
`docs_only` requires both filters to be false.

Static: the Python under the test and script folders is scanned for the
ways this repository names a document in code: a quoted `docs/<path>.md`, a
`"docs" / ... / "<name>.md"` path, or a quoted root document name. Every
named document that exists and that the docs-only filter would skip must
appear in `doc_tests`. A docstring that mentions a document in backticks is
not a read, and is not matched.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CI = ROOT / ".github" / "workflows" / "ci.yml"
SCANNED = [ROOT / "tests", ROOT / "scripts", *sorted((ROOT / "lab").glob("*/tests"))]
ROOT_DOCS = ("CONTRIBUTING.md", "SECURITY.md", "CODE_OF_CONDUCT.md")

_QUOTED = re.compile(r"""["']docs/([\w./-]+?\.md)\b""")
_JOINED = re.compile(r'"docs"((?:\s*/\s*"[\w.-]+")+)')
_PART = re.compile(r'"([\w.-]+)"')
_ROOT = re.compile(r"""["'](CONTRIBUTING|SECURITY|CODE_OF_CONDUCT)\.md["']""")


def _filter_step() -> dict:
    workflow = yaml.safe_load(CI.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["changes"]["steps"]
    (step,) = [s for s in steps if s.get("id") == "filter"]
    return step


def _filters() -> dict[str, list[str]]:
    filters = yaml.safe_load(_filter_step()["with"]["filters"])
    assert isinstance(filters, dict)
    return filters


def _docs_only_skips(path: str) -> bool:
    """Whether the `non_docs` filter's excludes would skip this path."""
    return (path.startswith("docs/") and path.endswith(".md")) or path in ROOT_DOCS


def named_docs(text: str) -> set[str]:
    found = {"docs/" + m for m in _QUOTED.findall(text)}
    for chain in _JOINED.findall(text):
        parts = _PART.findall(chain)
        if parts and parts[-1].endswith(".md"):
            found.add("/".join(["docs", *parts]))
    found |= {f"{m}.md" for m in _ROOT.findall(text)}
    return found


def _read_by_tests() -> dict[str, list[str]]:
    readers: dict[str, list[str]] = {}
    for folder in SCANNED:
        for source in sorted(folder.rglob("*.py")):
            if source == Path(__file__).resolve():
                continue
            for doc in named_docs(source.read_text(encoding="utf-8")):
                if _docs_only_skips(doc) and (ROOT / doc).is_file():
                    readers.setdefault(doc, []).append(str(source.relative_to(ROOT)))
    return readers


def test_every_doc_a_test_reads_runs_the_tests() -> None:
    listed = set(_filters()["doc_tests"])
    missing = {doc: who for doc, who in _read_by_tests().items() if doc not in listed}
    assert not missing, (
        "add these to the doc_tests filter in .github/workflows/ci.yml, "
        f"or a PR touching only them skips the tests that read them: {missing}"
    )


def test_every_listed_doc_exists_and_would_otherwise_be_skipped() -> None:
    for doc in _filters()["doc_tests"]:
        assert (ROOT / doc).is_file(), doc
        assert _docs_only_skips(doc), f"{doc} is not docs-only, so listing it does nothing"


def test_docs_only_needs_both_filters_false() -> None:
    workflow = yaml.safe_load(CI.read_text(encoding="utf-8"))
    expression = workflow["jobs"]["changes"]["outputs"]["docs_only"]
    assert "steps.filter.outputs.non_docs == 'false'" in expression
    assert "steps.filter.outputs.doc_tests == 'false'" in expression
    assert "&&" in expression


def test_the_scanner_finds_each_form_constructed() -> None:
    text = (
        'A = "docs/INSTALL.md"\n'
        'B = ROOT / "docs" / "handoffs" / "M11-03.md"\n'
        'C = ROOT / "SECURITY.md"\n'
        "D = '`docs/LAB_PLAN.md` in a docstring'\n"
        'E = "docs/x.txt"\n'
    )
    assert named_docs(text) == {
        "docs/INSTALL.md",
        "docs/handoffs/M11-03.md",
        "SECURITY.md",
    }
