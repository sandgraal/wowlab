"""A doc that a test reads is never treated as docs-only by CI (M16-83).

`.github/workflows/ci.yml` skips the test jobs when a PR touches only
documentation (`docs/**/*.md` and three root files). Some of those documents
are graded by tests, such as `docs/INSTALL.md` by `test_doc_examples.py` and
`docs/LAB_PLAN.md` by the addon suite. A PR touching only one of them must
run the tests, so ci.yml lists them in a second filter, `doc_tests`, and
`docs_only` requires both filters to be false.

Static: the Python under the test and script folders is scanned for the
ways this repository names a document in code:
- a quoted `docs/<path>.md`;
- a `"docs" / ... / "<name>.md"` or `.joinpath("docs", ..., "<name>.md")`
  path;
- a quoted root document name.

Every named document that exists and that the docs-only filter would skip
must appear in `doc_tests`, unless `NAMED_NOT_READ` records why that file
names it without reading the repository's copy. Two forms cannot be resolved
to one document: a glob over `docs`, and `"docs/" + ...`. A file using either
must be listed in `DYNAMIC` with the reason it is safe. A docstring that
mentions a document in backticks is not a read, and is not matched.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CI = ROOT / ".github" / "workflows" / "ci.yml"
SCANNED = [ROOT / "tests", ROOT / "scripts", *sorted((ROOT / "lab").glob("*/tests"))]
ROOT_DOCS = ("CONTRIBUTING.md", "SECURITY.md", "CODE_OF_CONDUCT.md")

# (document, file that names it) -> why that file never reads the repository's
# copy. Each entry must still be found by the scan, so a stale one fails.
NAMED_NOT_READ: dict[tuple[str, str], str] = {
    ("docs/BACKLOG.md", "tests/harness/test_hooks.py"): (
        "the hook tests build a temporary repo with its own docs/BACKLOG.md and "
        "pass the path to the hooks as input; the hooks match paths only"
    ),
    ("docs/DECISIONS.md", "tests/harness/test_hooks.py"): (
        "the path is hook input: protect_paths and guard_bash match it as a "
        "string and never open the repository's copy"
    ),
}

# file -> why its glob over docs/ (or computed docs path) is safe to skip on a
# docs-only PR. Each entry must still use such a form, so a stale one fails.
DYNAMIC: dict[str, str] = {
    "lab/core/tests/test_filemap.py": (
        "_SPEC_DOCS reads only the file names of docs/*.md, for the player-text "
        "lint; editing a doc changes no name, and adding or renaming one is "
        "rare enough to be caught by the next code PR"
    ),
    "lab/core/tests/review/test_review_m16_17_player_text_lint_misses_modules_and_docs_constructed.py": (
        "the same name-only glob as test_filemap.py's _SPEC_DOCS"
    ),
    "tests/repo/test_bidi_controls.py": (
        "the f-string is a constructed file name fed to the bidi scanner, not a path it opens"
    ),
    "tests/harness/test_hooks.py": (
        "it builds docs/ folders inside temporary repos and passes docs paths "
        "to the hooks as input; see NAMED_NOT_READ"
    ),
}

_QUOTED = re.compile(r"""["']docs/([\w./-]+?\.md)\b""")
_JOINED = re.compile(r'"docs"((?:\s*/\s*"[\w.-]+")+)')
_JOINPATH = re.compile(r'joinpath\(\s*"docs"((?:\s*,\s*"[\w.-]+")+)\s*\)')
_PART = re.compile(r'"([\w.-]+)"')
_ROOT = re.compile(r"""["'](CONTRIBUTING|SECURITY|CODE_OF_CONDUCT)\.md["']""")
_DYNAMIC = re.compile(r'"docs"\s*\)\s*\.\s*r?glob\(|["\']docs/["\']\s*\+|\bf["\']docs/')
# A "docs" path part: `/ "docs"`, `"docs" /` or `joinpath("docs"`.
_DOCS_PART = re.compile(r'/\s*"docs"|"docs"\s*/|joinpath\(\s*"docs"')


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
    for chain in [*_JOINED.findall(text), *_JOINPATH.findall(text)]:
        parts = _PART.findall(chain)
        if parts and parts[-1].endswith(".md"):
            found.add("/".join(["docs", *parts]))
    found |= {f"{m}.md" for m in _ROOT.findall(text)}
    return found


def _resolved_spans(text: str) -> list[tuple[int, int]]:
    spans = []
    for pattern in (_JOINED, _JOINPATH):
        for m in pattern.finditer(text):
            parts = _PART.findall(m.group(1))
            if parts and parts[-1].endswith(".md"):
                spans.append((m.start(), m.end()))
    return spans


def is_dynamic(text: str) -> bool:
    """A docs path this scan cannot resolve to one `.md` file."""
    if _DYNAMIC.search(text):
        return True
    spans = _resolved_spans(text)
    for m in _DOCS_PART.finditer(text):
        docs_at = text.index('"docs"', m.start())
        if not any(start <= docs_at < end for start, end in spans):
            return True
    return False


def _sources() -> list[tuple[str, str]]:
    out = []
    for folder in SCANNED:
        for source in sorted(folder.rglob("*.py")):
            if source == Path(__file__).resolve():
                continue
            out.append((str(source.relative_to(ROOT)), source.read_text(encoding="utf-8")))
    return out


def _read_by_tests() -> dict[str, list[str]]:
    readers: dict[str, list[str]] = {}
    for rel, text in _sources():
        for doc in named_docs(text):
            if not (_docs_only_skips(doc) and (ROOT / doc).is_file()):
                continue
            if (doc, rel) in NAMED_NOT_READ:
                continue
            readers.setdefault(doc, []).append(rel)
    return readers


def test_every_doc_a_test_reads_runs_the_tests() -> None:
    listed = set(_filters()["doc_tests"])
    missing = {doc: who for doc, who in _read_by_tests().items() if doc not in listed}
    assert not missing, (
        "add these to the doc_tests filter in .github/workflows/ci.yml, "
        f"or a PR touching only them skips the tests that read them: {missing}"
    )


def test_every_listed_doc_is_read_exists_and_would_otherwise_be_skipped() -> None:
    read = _read_by_tests()
    for doc in _filters()["doc_tests"]:
        assert (ROOT / doc).is_file(), doc
        assert _docs_only_skips(doc), f"{doc} is not docs-only, so listing it does nothing"
        assert doc in read, f"no test reads {doc}; listing it only costs CI time"


def test_every_computed_docs_path_is_accounted_for() -> None:
    dynamic = {rel for rel, text in _sources() if is_dynamic(text)}
    unexplained = dynamic - set(DYNAMIC)
    assert not unexplained, (
        "these files glob docs/ or build a docs path at run time; list the "
        f"documents they read in doc_tests, or add them to DYNAMIC with why: {unexplained}"
    )
    stale = set(DYNAMIC) - dynamic
    assert not stale, f"DYNAMIC entries no longer computing a docs path: {stale}"


def test_every_named_not_read_entry_is_still_found() -> None:
    texts = dict(_sources())
    for doc, rel in NAMED_NOT_READ:
        assert rel in texts and doc in named_docs(texts[rel]), (doc, rel)


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
        'F = ROOT.joinpath("docs", "GLOSSARY.md")\n'
    )
    assert named_docs(text) == {
        "docs/INSTALL.md",
        "docs/handoffs/M11-03.md",
        "SECURITY.md",
        "docs/GLOSSARY.md",
    }
    assert is_dynamic('x = (ROOT / "docs").glob("*.md")')
    assert is_dynamic('x = (ROOT / "docs").rglob("*.md")')
    assert is_dynamic('x = ROOT / ("docs/" + name)')
    assert not is_dynamic('x = ROOT / "docs" / "INSTALL.md"')
    assert not is_dynamic('x = ROOT.joinpath("docs", "SETUP.md")')
    assert is_dynamic('DOCS = ROOT / "docs"\nx = (DOCS / "GLOSSARY.md").read_text()')
    assert is_dynamic('DOCS = ROOT / "docs"\nfor p in DOCS.glob("*.md"): pass')
    assert is_dynamic('p = ROOT / f"docs/{name}.md"')
    assert is_dynamic("p = ROOT / f'docs/{name}.md'")
