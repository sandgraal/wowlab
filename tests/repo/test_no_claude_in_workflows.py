"""No GitHub workflow runs Claude (owner decision 2026-11-04).

The owner's instruction of 2026-11-04: "Nothing should use the Claude api,
change anything that does." `claude.yml` (@claude mentions),
`claude-review.yml` (the automated PR review) and `wiki-daily.yml` (the daily
wiki pass) were deleted for it. This keeps the rule from regressing: no file
under `.github/workflows/` names the Claude Code action, either secret it
authenticated with, or the Anthropic API host.

Static: every file under `.github/workflows/` is read as text, whatever its
suffix, and searched case-insensitively. The constructed tests show the scan
catches each marker in a workflow-shaped string; no workflow file is edited.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"

MARKERS = (
    "anthropics/claude-code-action",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "ANTHROPIC_API_KEY",
    "api.anthropic.com",
)

DELETED = ("claude.yml", "claude-review.yml", "wiki-daily.yml")


def _markers_in(text: str) -> list[str]:
    folded = text.casefold()
    return [m for m in MARKERS if m.casefold() in folded]


def _workflow_files() -> list[Path]:
    return sorted(p for p in WORKFLOWS.rglob("*") if p.is_file())


def test_the_workflows_directory_is_not_empty() -> None:
    # A scan over nothing would pass vacuously.
    assert any(p.name == "ci.yml" for p in _workflow_files())


@pytest.mark.parametrize("path", _workflow_files(), ids=lambda p: p.name)
def test_no_workflow_names_claude_or_its_credentials(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert _markers_in(text) == [], path.relative_to(ROOT)


@pytest.mark.parametrize("name", DELETED)
def test_the_deleted_claude_workflows_stay_deleted(name: str) -> None:
    assert not (WORKFLOWS / name).exists()


def test_the_wiki_daily_prompt_is_gone() -> None:
    # It existed only for wiki-daily.yml.
    assert not (ROOT / ".github" / "prompts").exists()


@pytest.mark.parametrize(
    "line",
    [
        "      - uses: anthropics/claude-code-action@f27be6b65a4652f08a2ba3cff79114829b588967",
        "          claude_code_oauth_token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}",
        "          anthropic_api_key: ${{ secrets['anthropic_api_key'] }}",
        "        run: curl -sS https://API.Anthropic.com/v1/messages",
    ],
)
def test_constructed_each_marker_is_caught(line: str) -> None:
    text = f"name: x\non: push\njobs:\n  a:\n    steps:\n{line}\n"
    assert len(_markers_in(text)) == 1, line
