"""Executable specification for the Claude Code hooks in `.claude/hooks/`.

Each hook is run as a subprocess with a JSON payload on stdin, exactly as
Claude Code runs it. These tests must pass under Python 3.9 as well as 3.12
(`make hooks-test`), because hooks execute on the system interpreter.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
HOOKS = REPO / ".claude" / "hooks"


def run_hook(name: str, payload: dict, env: dict | None = None) -> subprocess.CompletedProcess:
    merged_env = dict(os.environ)
    merged_env.update(env or {})
    return subprocess.run(
        [sys.executable, str(HOOKS / name)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=120,
        env=merged_env,
        check=False,
    )


def bash(command: str, cwd: str | None = None, agent: str | None = None) -> dict:
    payload = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": cwd or str(REPO)}
    if agent:
        payload["agent_type"] = agent
    return payload


def edit(path: str, cwd: str | None = None, agent: str | None = None) -> dict:
    payload = {"tool_name": "Edit", "tool_input": {"file_path": path}, "cwd": cwd or str(REPO)}
    if agent:
        payload["agent_type"] = agent
    return payload


def assert_blocked(result: subprocess.CompletedProcess, needle: str) -> None:
    assert result.returncode == 2, f"expected block, got rc={result.returncode}: {result.stderr}"
    decision = json.loads(result.stdout.strip().splitlines()[-1])["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    assert needle in decision["permissionDecisionReason"], decision["permissionDecisionReason"]


def assert_allowed(result: subprocess.CompletedProcess) -> None:
    assert result.returncode == 0, f"expected allow, got rc={result.returncode}: {result.stderr}"


# ─── guard_bash ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("command", "needle"),
    [
        ("git commit -m 'x' --no-verify", "--no-verify"),
        ("git commit -n -m 'x'", "--no-verify"),
        ("git push --force origin m1/02-simc-parser", "force-push"),
        ("git push -f", "force-push"),
        ("git push origin main", "push to main"),
        ("git push origin HEAD:main", "push to main"),
        ("git push origin --delete main", "delete main"),
        ("git branch -D main", "delete main"),
        ("gh pr merge 12 --squash --admin", "--admin"),
        ("gh api -X PUT repos/sandgraal/wowlab/rulesets/1 --input x.json", "bootstrap"),
        ("gh api --method PATCH repos/sandgraal/wowlab -f allow_squash_merge=true", "bootstrap"),
        ("rm -rf api/src", "recursive+force"),
        ("cd api && rm -r -f ../docs", "recursive+force"),
        ("sudo rm --recursive --force /Volumes/x", "recursive+force"),
        ("echo x > .claude/settings.json", "shell write"),
        ("cat foo >> AGENTS.md", "shell write"),
        ("printf 'a' | tee docs/DECISIONS.md", "shell write"),
        ("sed -i '' 's/a/b/' CLAUDE.md", "shell write"),
        ("cp /tmp/x .claude/agents/implementer.md", "shell write"),
        ("echo x > LICENSE", "fixed"),
    ],
)
def test_guard_bash_blocks(command: str, needle: str) -> None:
    assert_blocked(run_hook("guard_bash.py", bash(command)), needle)


@pytest.mark.parametrize(
    "command",
    [
        "git push --force-with-lease origin m1/02-simc-parser",
        "git push -u origin m1/02-simc-parser",
        "git commit -m 'feat(api): mention --no-verify in prose (M1-02)'",
        "gh pr merge 12 --squash --delete-branch",
        "gh api graphql -f query='{viewer{login}}'",
        "gh api repos/sandgraal/wowlab/pulls/1/comments",
        "rm -rf node_modules .venv",
        "rm -rf /tmp/scratch",
        "echo x > /dev/null",
        "echo x > api/src/bronze_api/new.py",
        "sed -i '' 's/a/b/' api/src/bronze_api/main.py",
        "grep -rn 'rm -rf' docs",
        "make lint && make test",
        "ls .claude/hooks",
    ],
)
def test_guard_bash_allows(command: str) -> None:
    assert_allowed(run_hook("guard_bash.py", bash(command)))


def test_guard_bash_ignores_other_tools() -> None:
    assert_allowed(
        run_hook("guard_bash.py", {"tool_name": "Read", "tool_input": {"file_path": "x"}})
    )


def test_guard_bash_worktree_relative_paths(tmp_path: Path) -> None:
    """A write inside a worktree is judged by its path inside that worktree."""
    main = tmp_path / "repo"
    (main / ".git").mkdir(parents=True)
    wt = main / ".claude" / "worktrees" / "job"
    wt.mkdir(parents=True)
    (wt / ".git").write_text("gitdir: elsewhere\n")
    (wt / "api").mkdir()
    assert_allowed(run_hook("guard_bash.py", bash("echo x > api/new.py", cwd=str(wt))))
    assert_blocked(
        run_hook("guard_bash.py", bash("echo x > AGENTS.md", cwd=str(wt))), "shell write"
    )


# ─── protect_paths ────────────────────────────────────────────────────────────


def test_protect_paths_license_is_read_only_for_everyone() -> None:
    assert_blocked(run_hook("protect_paths.py", edit(str(REPO / "LICENSE"))), "fixed")


def test_protect_paths_conductor_may_edit_harness() -> None:
    assert_allowed(run_hook("protect_paths.py", edit(str(REPO / ".claude" / "settings.json"))))
    assert_allowed(run_hook("protect_paths.py", edit(str(REPO / "docs" / "BACKLOG.md"))))


@pytest.mark.parametrize(
    "rel",
    [
        ".claude/settings.json",
        ".claude/agents/implementer.md",
        "AGENTS.md",
        "CLAUDE.md",
        "docs/DECISIONS.md",
        "docs/BACKLOG.md",
    ],
)
def test_protect_paths_subagents_cannot_edit_harness(rel: str) -> None:
    assert_blocked(
        run_hook("protect_paths.py", edit(str(REPO / rel), agent="implementer")), "conductor-only"
    )


def test_protect_paths_subagent_may_edit_source() -> None:
    assert_allowed(
        run_hook(
            "protect_paths.py", edit(str(REPO / "api/src/bronze_api/main.py"), agent="implementer")
        )
    )


def test_protect_paths_reviewer_confined_to_review_dir() -> None:
    ok = edit(str(REPO / "api/tests/review/test_probe_m1_02.py"), agent="code-reviewer")
    assert_allowed(run_hook("protect_paths.py", ok))
    bad = edit(str(REPO / "api/tests/test_health.py"), agent="code-reviewer")
    assert_blocked(run_hook("protect_paths.py", bad), "tests/review")


def test_protect_paths_worktree_paths_resolve_inside_worktree(tmp_path: Path) -> None:
    main = tmp_path / "repo"
    (main / ".git").mkdir(parents=True)
    wt = main / ".claude" / "worktrees" / "job"
    (wt / "api").mkdir(parents=True)
    (wt / ".git").write_text("gitdir: elsewhere\n")
    target = wt / "api" / "new.py"
    assert_allowed(
        run_hook("protect_paths.py", edit(str(target), cwd=str(wt), agent="implementer"))
    )
    assert_blocked(
        run_hook("protect_paths.py", edit(str(wt / "AGENTS.md"), cwd=str(wt), agent="implementer")),
        "conductor-only",
    )


# ─── precommit_gate ───────────────────────────────────────────────────────────


def _fake_checkout(tmp_path: Path, lint_recipe: str) -> Path:
    """A checkout with .git, .venv and a Makefile whose `lint` target runs `lint_recipe`."""
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".venv").mkdir()
    (repo / "Makefile").write_text(f"lint:\n\t@{lint_recipe}\n")
    return repo


def test_precommit_gate_ignores_non_commits(tmp_path: Path) -> None:
    repo = _fake_checkout(tmp_path, "exit 1")
    assert_allowed(run_hook("precommit_gate.py", bash("git status", cwd=str(repo))))


def test_precommit_gate_skips_when_environment_is_missing(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)  # no .venv, no Makefile: setup gap, not a defect
    assert_allowed(run_hook("precommit_gate.py", bash("git commit -m x", cwd=str(repo))))


def test_precommit_gate_allows_commit_when_lint_passes(tmp_path: Path) -> None:
    repo = _fake_checkout(tmp_path, "echo ok")
    assert_allowed(run_hook("precommit_gate.py", bash("git commit -m x", cwd=str(repo))))


def test_precommit_gate_blocks_commit_when_lint_fails(tmp_path: Path) -> None:
    repo = _fake_checkout(tmp_path, "echo 'E501 too long'; exit 1")
    result = run_hook("precommit_gate.py", bash("git add -A && git commit -m 'x'", cwd=str(repo)))
    assert_blocked(result, "make lint failed")
    reason = json.loads(result.stdout.strip().splitlines()[-1])["hookSpecificOutput"][
        "permissionDecisionReason"
    ]
    assert "E501" in reason


# ─── format_python / session_start ────────────────────────────────────────────


def test_format_python_never_blocks(tmp_path: Path) -> None:
    f = tmp_path / "x.py"
    f.write_text("x=1\n")
    assert_allowed(
        run_hook(
            "format_python.py",
            {"tool_name": "Write", "tool_input": {"file_path": str(f)}, "cwd": str(tmp_path)},
        )
    )


def test_session_start_reports_frontier(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "docs").mkdir()
    (repo / "docs" / "BACKLOG.md").write_text(
        "## [x] M0-01 — Done thing\n**Size:** S · **Depends on:** nothing\n\n"
        "## [ ] M0-02 — Ready thing\n**Size:** M · **Depends on:** M0-01 · **Blocks:** M0-03\n\n"
        "## [ ] M0-03 — Blocked thing\n**Size:** M · **Depends on:** M0-02\n\n"
        "## [ ] M0-04T — Graders [TEST]\n**Size:** S · **Depends on:** M0-01 · **Blocks:** M0-04\n\n"
        "## [ ] M0-04 — Impl [IMPL]\n**Size:** M · **Depends on:** M0-04T (graders merged)\n"
    )
    result = run_hook(
        "session_start.py",
        {"hook_event_name": "SessionStart", "cwd": str(repo)},
        env={"PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 0, result.stderr
    assert "ready   M0-02 Ready thing" in result.stdout
    assert "blocked M0-03 Blocked thing (needs M0-02)" in result.stdout
    assert "ready   M0-04T Graders [TEST]" in result.stdout
    assert "blocked M0-04 Impl [IMPL] (needs M0-04T)" in result.stdout
    assert "/conduct" in result.stdout
