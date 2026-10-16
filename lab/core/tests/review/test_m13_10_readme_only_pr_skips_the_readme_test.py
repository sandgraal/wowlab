# Probe from review of m13/10-player-readme; reproduces a README-only PR skipping test_readme.py
"""`tests/scripts/test_readme.py` (M13-10) pins the README's first screen: the
install line, the three commands in the Click tree, the platform claims. But
`ci.yml`'s `changes` job lists `README.md` as docs-only, so a PR that touches
only `README.md` runs the trivial "doc-only PR — skipped" step in every
required job, and the test that grades the README never runs on the one kind
of PR that can break it. It fails later, on the next unrelated non-doc PR.

Positive control: the same filter does exclude `docs/**/*.md`, which proves the
parse below reads the real filter.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[4]
CI = ROOT / ".github" / "workflows" / "ci.yml"


def _docs_only_exclusions() -> set[str]:
    data = yaml.safe_load(CI.read_text(encoding="utf-8"))
    for step in data["jobs"]["changes"]["steps"]:
        if step.get("id") == "filter":
            filters = yaml.safe_load(step["with"]["filters"])
            return {p[1:] for p in filters["non_docs"] if p.startswith("!")}
    raise AssertionError("no paths filter step in ci.yml's changes job")


def test_positive_control_docs_markdown_is_docs_only() -> None:
    assert "docs/**/*.md" in _docs_only_exclusions()


def test_a_readme_only_pr_still_runs_the_readme_test() -> None:
    assert (ROOT / "tests" / "scripts" / "test_readme.py").is_file()
    assert "README.md" not in _docs_only_exclusions(), (
        "README.md is docs-only in ci.yml, so a README-only PR never runs test_readme.py"
    )
