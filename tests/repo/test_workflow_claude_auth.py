"""Every workflow that runs Claude authenticates with the owner's subscription token.

Owner decision, 2026-10-14: the Claude workflows run on the owner's Claude
subscription through the `CLAUDE_CODE_OAUTH_TOKEN` repository secret, not on
a pay-as-you-go API key (wiki-daily run 38659417983 skipped on "credit
balance too low"). These tests read `.github/workflows/*.yml` as data: no
workflow names the API-key secret, the Console workspace header, or the old
API preflight; each Claude step takes the token; each "Check secret" step
checks the token and skips cleanly without it (its script is run with bash,
with and without a token); the token is referenced only by that check and by
the Claude step, nowhere else in any workflow; and the Claude steps run on
that check alone, with no `continue-on-error`, so a usage-limit failure fails
the step rather than being skipped as a preflight result.

A usage-limit failure in the Claude step is reported by the action as
is_error:true; `test_constructed_a_usage_limit_result_is_diagnosed` in
`tests/scripts/test_wiki_diagnose.py` covers what wiki-daily prints then.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
ACTION = "anthropics/claude-code-action@"
CLAUDE_WORKFLOWS = ("claude.yml", "claude-review.yml", "wiki-daily.yml")
TOKEN_INPUT = "${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}"
PRESENT = "steps.secret.outputs.present == 'true'"


def _files() -> list[Path]:
    return sorted([*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")])


def _steps(path: Path) -> list[tuple[str, list[dict[str, Any]]]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    jobs = data["jobs"]
    assert isinstance(jobs, dict)
    return [(name, job.get("steps", [])) for name, job in jobs.items()]


def _claude_jobs(path: Path) -> list[tuple[str, list[dict[str, Any]]]]:
    return [
        (name, steps)
        for name, steps in _steps(path)
        if any(str(s.get("uses", "")).startswith(ACTION) for s in steps)
    ]


def test_the_claude_workflows_are_the_ones_that_use_the_action() -> None:
    using = {p.name for p in _files() if ACTION in p.read_text(encoding="utf-8")}
    assert using == set(CLAUDE_WORKFLOWS)


@pytest.mark.parametrize("path", _files(), ids=lambda p: p.name)
def test_no_workflow_uses_the_api_key_or_the_workspace_header(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert not re.search(r"secrets\s*\.\s*ANTHROPIC_API_KEY\b", text)
    assert "secrets['ANTHROPIC_API_KEY']" not in text
    assert 'secrets["ANTHROPIC_API_KEY"]' not in text
    assert "anthropic_api_key" not in text
    assert "ANTHROPIC_CUSTOM_HEADERS" not in text
    assert "ANTHROPIC_WORKSPACE_ID" not in text
    assert "anthropic-workspace-id" not in text
    # The old API preflight: a direct Messages API call with the key.
    assert "x-api-key" not in text
    assert "api.anthropic.com" not in text
    assert "steps.preflight" not in text


@pytest.mark.parametrize("name", CLAUDE_WORKFLOWS)
def test_every_claude_step_takes_the_subscription_token(name: str) -> None:
    jobs = _claude_jobs(WORKFLOWS / name)
    assert jobs
    for _, steps in jobs:
        for step in steps:
            if not str(step.get("uses", "")).startswith(ACTION):
                continue
            with_ = step["with"]
            assert with_["claude_code_oauth_token"] == TOKEN_INPUT
            assert "anthropic_api_key" not in with_
    # The model stays pinned by full id.
    assert "claude-opus-5-5" in (WORKFLOWS / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("name", CLAUDE_WORKFLOWS)
def test_the_secret_check_checks_the_token_and_gates_the_claude_step(name: str) -> None:
    for job, steps in _claude_jobs(WORKFLOWS / name):
        checks = [s for s in steps if s.get("id") == "secret"]
        assert len(checks) == 1, job
        check = checks[0]
        assert check["env"] == {"TOKEN": TOKEN_INPUT}
        run = check["run"]
        # Skips cleanly: the step itself never fails on a missing token.
        assert 'if [ -n "$TOKEN" ]' in run
        assert 'echo "present=false" >> "$GITHUB_OUTPUT"' in run
        assert "CLAUDE_CODE_OAUTH_TOKEN is not set; skipping" in run
        assert "exit 1" not in run
        index = steps.index(check)
        for i, step in enumerate(steps):
            if str(step.get("uses", "")).startswith(ACTION):
                assert i > index
                # The Claude step runs on the presence check alone (no
                # preflight), and is never allowed to fail quietly.
                assert step["if"] == PRESENT
                # Absent entirely: `continue-on-error: ${{ true }}` or any
                # other expression would also let it fail quietly.
                assert "continue-on-error" not in step


# Any spelling of the secret reference: `secrets.X`, `secrets . X`,
# `secrets['X']`, `secrets[ "X" ]`. GitHub secret names are case-insensitive.
SECRET_REF = re.compile(
    r"secrets\s*(?:\.\s*CLAUDE_CODE_OAUTH_TOKEN\b"
    r"|\[\s*(['\"])\s*CLAUDE_CODE_OAUTH_TOKEN\s*\1\s*\])",
    re.IGNORECASE,
)

Where = tuple[Any, ...]


def _secret_uses(node: Any, where: Where = ()) -> list[Where]:
    """Every path in a parsed workflow whose key or string value names the secret."""
    found: list[Where] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str) and SECRET_REF.search(key):
                found.append((*where, key))
            found += _secret_uses(value, (*where, key))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            found += _secret_uses(value, (*where, i))
    elif isinstance(node, str):
        found += [where] * len(SECRET_REF.findall(node))
    return found


def _allowed_secret_uses(data: dict[str, Any]) -> list[Where]:
    allowed: list[Where] = []
    for job, body in data["jobs"].items():
        for i, step in enumerate(body.get("steps", [])):
            if step.get("id") == "secret":
                allowed.append(("jobs", job, "steps", i, "env", "TOKEN"))
            if str(step.get("uses", "")).startswith(ACTION):
                allowed.append(("jobs", job, "steps", i, "with", "claude_code_oauth_token"))
    return allowed


def test_the_secret_pattern_matches_every_spelling() -> None:
    for spelling in (
        "${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}",
        "${{secrets . CLAUDE_CODE_OAUTH_TOKEN}}",
        "${{ secrets['CLAUDE_CODE_OAUTH_TOKEN'] }}",
        '${{ secrets[ "CLAUDE_CODE_OAUTH_TOKEN" ] }}',
        "${{ SECRETS.claude_code_oauth_token }}",
    ):
        assert SECRET_REF.search(spelling), spelling
    assert not SECRET_REF.search("CLAUDE_CODE_OAUTH_TOKEN is not set; skipping")
    assert not SECRET_REF.search("${{ secrets.CLAUDE_CODE_OAUTH_TOKEN_OLD }}")


@pytest.mark.parametrize("path", _files(), ids=lambda p: p.name)
def test_the_secret_appears_only_where_the_claude_steps_need_it(path: Path) -> None:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    uses = _secret_uses(data)
    if path.name not in CLAUDE_WORKFLOWS:
        assert uses == []
        return
    # Exactly two: the presence check's env.TOKEN and the action's input.
    # Not in workflow-level or job-level env, and not in any other step.
    allowed = _allowed_secret_uses(data)
    assert len(allowed) == 2
    assert sorted(uses, key=repr) == sorted(allowed, key=repr)


def _secret_check_scripts() -> list[tuple[str, str]]:
    scripts = []
    for name in CLAUDE_WORKFLOWS:
        for _, steps in _claude_jobs(WORKFLOWS / name):
            for step in steps:
                if step.get("id") == "secret":
                    scripts.append((name, step["run"]))
    return scripts


def _run_check(script: str, token: str, tmp_path: Path) -> tuple[int, str]:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is not on PATH")
    out = tmp_path / f"github_output_{bool(token)}"
    out.write_text("", encoding="utf-8")
    env = {"PATH": os.environ.get("PATH", ""), "TOKEN": token, "GITHUB_OUTPUT": str(out)}
    # The same shell GitHub uses for `shell: bash`.
    done = subprocess.run(
        [bash, "--noprofile", "--norc", "-eo", "pipefail", "-c", script],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    return done.returncode, out.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("name", "script"), [pytest.param(n, s, id=n) for n, s in _secret_check_scripts()]
)
def test_the_secret_check_really_skips_without_the_token(
    name: str, script: str, tmp_path: Path
) -> None:
    code, output = _run_check(script, "", tmp_path)
    assert code == 0, name
    assert output.split() == ["present=false"], name
    code, output = _run_check(script, "dummy-not-a-token", tmp_path)
    assert code == 0, name
    assert output.split() == ["present=true"], name


def test_every_claude_workflow_has_a_secret_check_script() -> None:
    assert sorted(name for name, _ in _secret_check_scripts()) == sorted(CLAUDE_WORKFLOWS)
