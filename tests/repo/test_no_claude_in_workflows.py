"""No GitHub workflow runs Claude (owner decision 2026-11-04).

The owner's instruction of 2026-11-04: "Nothing should use the Claude api,
change anything that does." `claude.yml` (@claude mentions),
`claude-review.yml` (the automated PR review) and `wiki-daily.yml` (the daily
wiki pass) were deleted for it. This keeps the rule from regressing: no file
under `.github/workflows/` or `.github/actions/` (local composite actions;
absent today) names Anthropic, Claude Code or a Claude Code provider switch.

Static: every file under those two directories is read as text, whatever
its suffix, and searched case-insensitively. The broad markers (`anthropic`,
`claude-code`, `claude_code_use_`) catch any Anthropic action, package,
secret, environment variable or host; the narrow ones are kept so a failure
names what it found. The rest of `.github/` is not scanned: the PR
template's attribution footer links `claude.com/claude-code` and runs
nothing. The constructed tests show the scan catches each form in a
workflow-shaped string; no workflow file is edited.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
ACTIONS = ROOT / ".github" / "actions"
SCANNED = (WORKFLOWS, ACTIONS)

MARKERS = (
    "anthropics/claude-code-action",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "ANTHROPIC_API_KEY",
    "api.anthropic.com",
    "anthropic",
    "claude-code",
    "claude_code_use_",
)

DELETED = ("claude.yml", "claude-review.yml", "wiki-daily.yml")


def _markers_in(text: str) -> list[str]:
    folded = text.casefold()
    return [m for m in MARKERS if m.casefold() in folded]


def _scanned_files(dirs: Iterable[Path] = SCANNED) -> list[Path]:
    # A missing directory (`.github/actions/` today) has nothing to scan.
    return sorted(p for d in dirs if d.is_dir() for p in d.rglob("*") if p.is_file())


def test_the_scan_is_not_empty() -> None:
    # A scan over nothing would pass vacuously.
    assert any(p.name == "ci.yml" for p in _scanned_files())


@pytest.mark.parametrize("path", _scanned_files(), ids=lambda p: str(p.relative_to(ROOT)))
def test_no_workflow_names_claude_or_its_credentials(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert _markers_in(text) == [], path.relative_to(ROOT)


@pytest.mark.parametrize("name", DELETED)
def test_the_deleted_claude_workflows_stay_deleted(name: str) -> None:
    assert not (WORKFLOWS / name).exists()


def test_the_wiki_daily_prompt_is_gone() -> None:
    # It existed only for wiki-daily.yml.
    assert not (ROOT / ".github" / "prompts").exists()


# Constructed: one invented workflow line per form, labelled by what it is.
@pytest.mark.parametrize(
    "line",
    [
        pytest.param(
            "      - uses: anthropics/claude-code-action@f27be6b65a4652f08a2ba3cff79114829b588967",
            id="the-claude-code-action",
        ),
        pytest.param("      - uses: anthropics/claude-code-base-action@x", id="the-base-action"),
        pytest.param(
            "          claude_code_oauth_token: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}",
            id="the-oauth-token-secret",
        ),
        pytest.param(
            "          anthropic_api_key: ${{ secrets['anthropic_api_key'] }}",
            id="the-api-key-secret-in-lower-case",
        ),
        pytest.param(
            "        run: curl -sS https://API.Anthropic.com/v1/messages", id="the-api-host"
        ),
        pytest.param("        run: npx -y @anthropic-ai/claude-code -p hi", id="the-npm-package"),
        pytest.param(
            "          ANTHROPIC_AUTH_TOKEN: ${{ secrets.T }}", id="the-auth-token-variable"
        ),
        pytest.param(
            "          ANTHROPIC_BASE_URL: https://proxy.example", id="the-base-url-variable"
        ),
        pytest.param("          CLAUDE_CODE_USE_BEDROCK: 1", id="a-provider-switch"),
        pytest.param("          claude_code_use_vertex: '1'", id="a-provider-switch-lower-case"),
    ],
)
def test_constructed_each_form_is_caught(line: str) -> None:
    text = f"name: x\non: push\njobs:\n  a:\n    steps:\n{line}\n"
    assert _markers_in(text) != [], line


def test_constructed_a_clean_workflow_line_is_not_caught() -> None:
    text = "      - uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1\n"
    assert _markers_in(text) == []


def test_constructed_a_composite_action_is_scanned_and_a_missing_directory_is_not(
    tmp_path: Path,
) -> None:
    action = tmp_path / "actions" / "setup" / "action.yml"
    action.parent.mkdir(parents=True)
    action.write_text(
        "runs:\n  using: composite\n  steps:\n    - run: npx @anthropic-ai/claude-code\n",
        encoding="utf-8",
    )
    files = _scanned_files((tmp_path / "workflows", tmp_path / "actions"))
    assert files == [action]
    assert _markers_in(action.read_text(encoding="utf-8")) != []
