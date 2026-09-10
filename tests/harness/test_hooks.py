"""Executable specification for the Claude Code hooks in `.claude/hooks/`.

Each hook is run as a subprocess with a JSON payload on stdin, exactly as
Claude Code runs it. These tests must pass under Python 3.9 as well as 3.12
(`make hooks-test`), because hooks execute on the system interpreter.

Every bypass a reviewer found is a case here, paired with the positive
control that shows the guard still allows ordinary work.
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


def run_hook(
    name: str, payload, env: dict | None = None, raw: str | None = None
) -> subprocess.CompletedProcess:
    merged_env = dict(os.environ)
    merged_env.setdefault("CLAUDE_PROJECT_DIR", str(REPO))
    merged_env.update(env or {})
    return subprocess.run(
        [sys.executable, str(HOOKS / name)],
        input=raw if raw is not None else json.dumps(payload),
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


def reason_of(result: subprocess.CompletedProcess) -> str:
    return json.loads(result.stdout.strip().splitlines()[-1])["hookSpecificOutput"][
        "permissionDecisionReason"
    ]


def assert_blocked(result: subprocess.CompletedProcess, needle: str) -> None:
    assert result.returncode == 2, f"expected block, got rc={result.returncode}: {result.stderr}"
    decision = json.loads(result.stdout.strip().splitlines()[-1])["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    assert needle in decision["permissionDecisionReason"], decision["permissionDecisionReason"]


def assert_allowed(result: subprocess.CompletedProcess) -> None:
    assert result.returncode == 0, f"expected allow, got rc={result.returncode}: {result.stderr}"


def scratch_checkout(tmp_path: Path) -> Path:
    """A checkout outside the project: .git, harness dirs, docs."""
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / ".claude" / "agents").mkdir(parents=True)
    (repo / "docs").mkdir()
    (repo / "api").mkdir()
    (repo / "AGENTS.md").write_text("x\n")
    (repo / "docs" / "BACKLOG.md").write_text("x\n")
    (repo / ".claude" / "settings.json").write_text("{}\n")
    return repo


# ─── guard_bash: git and gh ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("command", "needle"),
    [
        ("git commit -m 'x' --no-verify", "--no-verify"),
        ("git commit -n -m 'x'", "--no-verify"),
        ("git commit -nm x", "--no-verify"),
        ("git commit --no-veri -m x", "--no-verify"),
        ("git -c a=b commit --no-verify -m x", "--no-verify"),
        ("git -c core.hooksPath=/dev/null commit -m x", "hooksPath"),
        ("git push --force origin m1/02-simc-parser", "force-push"),
        ("git push -f", "force-push"),
        ("git push -fu origin x", "force-push"),
        ("git push origin +main", "force-push"),
        ("git push origin +HEAD:main", "force-push"),
        ("git push --mirror origin", "--all/--mirror"),
        ("git push --all origin", "--all/--mirror"),
        ("git push origin main", "push to main"),
        ("git push origin HEAD:main", "push to main"),
        ("git push origin main:main", "push to main"),
        ("git push origin m1/02-x:main", "push to main"),
        ("git push origin refs/heads/main:refs/heads/main", "push to main"),
        ("git -c user.name=x push origin main", "push to main"),
        ("git -C . push origin main", "push to main"),
        ("timeout 30 git push origin main", "push to main"),
        ("nohup git push origin main", "push to main"),
        ("sh -c 'git push origin main'", "push to main"),
        ("bash -lc 'git push origin main'", "push to main"),
        ("eval git push origin main", "push to main"),
        ("b=main; git push origin $b", "variables in a push refspec"),
        ("git push origin --delete main", "delete main"),
        ("git push origin :main", "delete main"),
        ("git branch -D main", "delete main"),
        ("git clean -fdx", "git clean -x"),
        ("gh pr merge 12 --squash --admin", "--admin"),
        ("gh api -X PUT repos/sandgraal/wowlab/rulesets/1 --input x.json", "bootstrap"),
        ("gh api -XPUT repos/sandgraal/wowlab/vulnerability-alerts", "bootstrap"),
        ("gh api --method=DELETE repos/sandgraal/wowlab/rulesets/1", "bootstrap"),
        ("gh api --method PATCH repos/sandgraal/wowlab -f allow_squash_merge=true", "bootstrap"),
        (
            "gh api graphql -f query='mutation{deleteRepositoryRuleset(input:{repositoryRulesetId:\"x\"}){clientMutationId}}'",
            "GraphQL mutations",
        ),
    ],
)
def test_guard_bash_blocks_git_and_gh(command: str, needle: str) -> None:
    assert_blocked(run_hook("guard_bash.py", bash(command)), needle)


@pytest.mark.parametrize(
    "command",
    [
        "git push --force-with-lease origin m1/02-simc-parser",
        "git push -u origin m1/02-simc-parser",
        "git push origin HEAD:m1/02-simc-parser",
        "git -C . push origin m1/02-simc-parser",
        "git commit -m 'feat(api): mention --no-verify in prose (M1-02)'",
        "git commit -F - <<'EOF'\nfeat: it's fine\n\nBody mentions --no-verify in prose.\nEOF",
        "git commit -am 'x'",
        "git clean -fd",
        "gh pr merge 12 --squash --delete-branch",
        "gh api graphql -f query='{viewer{login}}'",
        "gh api graphql -f query='mutation($t:ID!){resolveReviewThread(input:{threadId:$t}){thread{id}}}' -f t=x",
        "gh api graphql -f query='mutation($t:ID!,$b:String!){addPullRequestReviewThreadReply(input:{pullRequestReviewThreadId:$t,body:$b}){comment{id}}}'",
        "gh api repos/sandgraal/wowlab/pulls/1/comments",
        "make lint && make test",
        "grep -rn 'rm -rf' docs",
        "ls .claude/hooks",
    ],
)
def test_guard_bash_allows_ordinary_git_and_gh(command: str) -> None:
    assert_allowed(run_hook("guard_bash.py", bash(command)))


# ─── guard_bash: destructive and shell writes ────────────────────────────────


@pytest.mark.parametrize(
    ("command", "needle"),
    [
        ("rm -rf api/src", "recursive+force"),
        ("cd api && rm -r -f ../docs", "recursive+force"),
        ("sudo rm --recursive --force /Volumes/x", "recursive+force"),
        ("rm -rf .venv/../api", "recursive+force"),
        ("rm -rf api/src/coverage_models", "recursive+force"),
        ("find api -name '*.py' -delete", "find -delete"),
        ("echo x > .claude/settings.json", "shell write"),
        ("echo x >| AGENTS.md", "shell write"),
        ("echo x > agents.md", "shell write"),
        ("cat foo >> AGENTS.md", "shell write"),
        ("printf 'a' | tee docs/DECISIONS.md", "shell write"),
        ("timeout 5 tee .claude/settings.json", "shell write"),
        ("sed -i '' 's/a/b/' CLAUDE.md", "shell write"),
        ("sed --in-place 's/a/b/' AGENTS.md", "shell write"),
        ("cp /tmp/x .claude/agents/implementer.md", "shell write"),
        ("cp /tmp/BACKLOG.md docs/", "shell write"),
        ("cp x .claude", "shell write"),
        ("mv AGENTS.md AGENTS.old.md", "shell write"),
        ("rm AGENTS.md", "shell write"),
        ("rm -f docs/BACKLOG.md", "shell write"),
        ("git rm -r .claude", "shell write"),
        ("git checkout origin/evil -- .claude/hooks/guard_bash.py", "shell write"),
        ("git checkout --theirs docs/BACKLOG.md", "shell write"),
        ("git restore --source=HEAD~2 .claude/settings.json", "shell write"),
        ("dd if=/dev/zero of=AGENTS.md", "shell write"),
        ("ln -sf /tmp/x .claude/settings.json", "shell write"),
        ("rsync /tmp/x/ .claude/", "shell write"),
        ("patch AGENTS.md < /tmp/p", "shell write"),
        ("echo x > $PWD/AGENTS.md", "shell write"),
        ('echo x > "$CLAUDE_PROJECT_DIR"/.claude/settings.json', "shell write"),
        ("echo x > $UNKNOWN_VAR/AGENTS.md", "cannot resolve"),
        ("bash -c 'echo x > .claude/settings.json'", "shell write"),
        ("cd docs && echo x > BACKLOG.md", "shell write"),
        ("cd .claude/agents && cat > implementer.md <<'EOF'\nx\nEOF", "shell write"),
        ("cat > AGENTS.md <<'EOF'\nIt's a file\nEOF", "shell write"),
        ("echo x > LICENSE", "fixed"),
        ('echo "unbalanced > AGENTS.md', "tokenised"),
    ],
)
def test_guard_bash_blocks_destructive_and_shell_writes(command: str, needle: str) -> None:
    assert_blocked(run_hook("guard_bash.py", bash(command)), needle)


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf node_modules .venv",
        "rm -rf /tmp/scratch",
        "rm -rf api/.pytest_cache",
        "find .pytest_cache -delete",
        "echo x > /dev/null",
        "echo x > api/src/bronze_api/new.py",
        "sed -i '' 's/a/b/' api/src/bronze_api/main.py",
        "cp /tmp/x api/tests/",
        "mv api/a.py api/b.py",
        "rm api/tests/review/old_probe.py",
        "git checkout -b m1/02-x origin/main",
        "git checkout --detach origin/m1/02-x",
        "git restore api/src/bronze_api/main.py",
        "cd api && echo x > new.py",
        "cat > api/notes.md <<'EOF'\nIt's a file with an apostrophe\nEOF",
        'echo "it\'s fine" > api/notes.md',
    ],
)
def test_guard_bash_allows_ordinary_writes(command: str) -> None:
    assert_allowed(run_hook("guard_bash.py", bash(command)))


def test_guard_bash_ignores_other_tools() -> None:
    assert_allowed(
        run_hook("guard_bash.py", {"tool_name": "Read", "tool_input": {"file_path": "x"}})
    )


def test_guard_bash_fails_closed_on_bad_payload() -> None:
    assert_blocked(run_hook("guard_bash.py", {}, raw="not json"), "unparseable")


def test_guard_bash_worktree_relative_paths(tmp_path: Path) -> None:
    """A write inside a worktree is judged by its path inside that worktree."""
    main = scratch_checkout(tmp_path)
    wt = main / ".claude" / "worktrees" / "job"
    (wt / "api").mkdir(parents=True)
    (wt / ".git").write_text("gitdir: elsewhere\n")
    env = {"CLAUDE_PROJECT_DIR": str(main)}
    assert_allowed(run_hook("guard_bash.py", bash("echo x > api/new.py", cwd=str(wt)), env=env))
    assert_blocked(
        run_hook("guard_bash.py", bash("echo x > AGENTS.md", cwd=str(wt)), env=env), "shell write"
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
        "agents.md",
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


def test_protect_paths_reviewer_confined_to_new_files_in_review_dir() -> None:
    assert_allowed(
        run_hook(
            "protect_paths.py",
            edit(str(REPO / "api/tests/review/test_probe_m1_02.py"), agent="code-reviewer"),
        )
    )
    assert_blocked(
        run_hook(
            "protect_paths.py", edit(str(REPO / "api/tests/test_health.py"), agent="code-reviewer")
        ),
        "tests/review",
    )
    assert_blocked(
        run_hook(
            "protect_paths.py",
            edit(str(REPO / "api/tests/review/README.md"), agent="code-reviewer"),
        ),
        "never edits an existing",
    )
    assert_blocked(
        run_hook(
            "protect_paths.py",
            edit("/tmp/elsewhere/tests/review/x.py", agent="code-reviewer"),
            env={"CLAUDE_PROJECT_DIR": ""},
        ),
        "inside the checkout",
    )


def test_protect_paths_fails_closed() -> None:
    assert_blocked(run_hook("protect_paths.py", {}, raw="{bad"), "unparseable")
    assert_blocked(
        run_hook("protect_paths.py", {"tool_name": "Write", "tool_input": "x"}), "tool_input"
    )
    assert_blocked(
        run_hook("protect_paths.py", {"tool_name": "Write", "tool_input": {}}), "file path"
    )


def test_protect_paths_worktree_paths_resolve_inside_worktree(tmp_path: Path) -> None:
    main = scratch_checkout(tmp_path)
    wt = main / ".claude" / "worktrees" / "job"
    (wt / "api").mkdir(parents=True)
    (wt / ".git").write_text("gitdir: elsewhere\n")
    env = {"CLAUDE_PROJECT_DIR": str(main)}
    assert_allowed(
        run_hook(
            "protect_paths.py",
            edit(str(wt / "api" / "new.py"), cwd=str(wt), agent="implementer"),
            env=env,
        )
    )
    assert_blocked(
        run_hook(
            "protect_paths.py",
            edit(str(wt / "AGENTS.md"), cwd=str(wt), agent="implementer"),
            env=env,
        ),
        "conductor-only",
    )


def test_protect_paths_symlink_and_nested_git_cannot_reroot(tmp_path: Path) -> None:
    main = scratch_checkout(tmp_path)
    (main / "link").symlink_to(main / ".claude")
    (main / "docs" / ".git").mkdir()  # a stray `git init docs`
    env = {"CLAUDE_PROJECT_DIR": str(main)}
    assert_blocked(
        run_hook(
            "protect_paths.py",
            edit(str(main / "link" / "settings.json"), cwd=str(main), agent="implementer"),
            env=env,
        ),
        "conductor-only",
    )
    assert_blocked(
        run_hook(
            "protect_paths.py",
            edit(str(main / "docs" / "DECISIONS.md"), cwd=str(main), agent="implementer"),
            env=env,
        ),
        "conductor-only",
    )
    assert_blocked(
        run_hook("guard_bash.py", bash("echo x > link/settings.json", cwd=str(main)), env=env),
        "shell write",
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


@pytest.mark.parametrize(
    "command",
    [
        "git add -A && git commit -m 'x'",
        "git -c user.name=x commit -m x",
        "git commit -F - <<'EOF'\nfeat: it's fine\nEOF",
    ],
)
def test_precommit_gate_blocks_commit_when_lint_fails(tmp_path: Path, command: str) -> None:
    repo = _fake_checkout(tmp_path, "echo 'E501 too long'; exit 1")
    result = run_hook("precommit_gate.py", bash(command, cwd=str(repo)))
    assert_blocked(result, "make lint failed")
    assert "E501" in reason_of(result)


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
    assert_allowed(run_hook("format_python.py", {}, raw="garbage"))


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
