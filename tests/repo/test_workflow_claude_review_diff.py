"""The PR review reads a diff the workflow wrote, not one it cannot fetch (M13-59).

Found on #265 and #266: the review hit its 30-turn cap before a verdict. Its
sandbox has no network, so `gh pr diff` could not reach api.github.com; `gh`
and `git` trip over the denied `.git/config`; and the action puts the base
branch's `.claude/`, `CLAUDE.md`, `.mcp.json` and its other config paths back
before the model starts, so for those paths the files on disk are not the
PR's. The model rebuilt the diff from whole files and ran out of turns.

The fix lives in `.github/workflows/claude-review.yml`: a step before the
action, with no token and `persist-credentials: false`, diffs the merge ref
against its first parent (the checkout fetches depth 2) and writes the result
to `.pr-review/pr.diff`, capped at 400 KiB. These tests read the workflow as
data, and run the step's script with bash on throwaway git repositories built
in `tmp_path` (constructed: no network, no real PR).
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
REVIEW = ROOT / ".github" / "workflows" / "claude-review.yml"
ACTION = "anthropics/claude-code-action@"
CHECKOUT = "actions/checkout@"
PRESENT = "steps.secret.outputs.present == 'true'"
STEP_NAME = "Write the pull request diff for the review"
DIFF_FILE = ".pr-review/pr.diff"
CAP = 400 * 1024
# What the pinned action restores from the base branch before the model
# starts (SENSITIVE_PATHS in src/github/operations/restore-config.ts at
# 1d6de8cb0c237e7c15e9e1bdf973826ebae490cc), and where it saves the PR's copy.
RESTORED = (
    ".claude",
    ".mcp.json",
    ".claude.json",
    ".gitmodules",
    ".ripgreprc",
    "CLAUDE.md",
    "CLAUDE.local.md",
    ".husky",
)
PR_COPY = ".claude-pr/"


def _steps() -> list[dict[str, Any]]:
    data = yaml.safe_load(REVIEW.read_text(encoding="utf-8"))
    jobs = data["jobs"]
    assert list(jobs) == ["review"]
    steps = jobs["review"]["steps"]
    assert isinstance(steps, list)
    return steps


def _index(predicate: Any) -> int:
    found = [i for i, step in enumerate(_steps()) if predicate(step)]
    assert len(found) == 1
    return found[0]


def _diff_step() -> dict[str, Any]:
    return _steps()[_index(lambda s: s.get("name") == STEP_NAME)]


def _prompt() -> str:
    step = _steps()[_index(lambda s: str(s.get("uses", "")).startswith(ACTION))]
    return " ".join(str(step["with"]["prompt"]).split())


def test_the_checkout_fetches_both_parents_and_writes_no_credentials() -> None:
    checkout = _steps()[_index(lambda s: str(s.get("uses", "")).startswith(CHECKOUT))]
    assert checkout["with"]["persist-credentials"] is False
    assert checkout["with"]["fetch-depth"] == 2
    assert checkout["if"] == PRESENT


def test_the_diff_step_runs_after_the_checkout_and_before_the_action() -> None:
    steps = _steps()
    checkout = _index(lambda s: str(s.get("uses", "")).startswith(CHECKOUT))
    diff = _index(lambda s: s.get("name") == STEP_NAME)
    action = _index(lambda s: str(s.get("uses", "")).startswith(ACTION))
    assert checkout < diff < action
    # Runs whenever the review does, and is never allowed to fail quietly.
    assert steps[diff]["if"] == steps[action]["if"] == PRESENT
    assert "continue-on-error" not in steps[diff]


def test_the_diff_step_has_no_token_and_no_network() -> None:
    step = _diff_step()
    # A shell step only: no action, no environment, no secret, no token.
    assert set(step) == {"name", "if", "run"}
    run = step["run"]
    assert "secrets" not in run
    assert "github.token" not in run
    assert "${{" not in run
    # Nothing that reaches the network: no gh, curl or wget, and no git
    # command that talks to a remote.
    assert not re.search(r"(^|[\s;|&(])(gh|curl|wget)\s", run, re.MULTILINE)
    assert not re.search(r"\bgit\b[^\n]*\b(fetch|pull|clone|remote|ls-remote|push)\b", run)


def test_every_git_diff_runs_without_external_programs() -> None:
    # Every git invocation: at the start of a line, after `!`, or in `$(...)`.
    run = _diff_step()["run"]
    lines = [line for line in run.splitlines() if re.search(r"(^\s*|!\s+|\$\()git\s", line)]
    assert len(lines) == 5
    diffs = [line for line in lines if re.search(r"\bdiff\b", line)]
    assert len(diffs) == 2
    for line in diffs:
        assert "--no-ext-diff" in line
        assert "--no-textconv" in line
        assert "'HEAD^1' HEAD" in line
    # The only other git commands read the merge commit's parents.
    assert all("git rev-parse" in line for line in lines if line not in diffs)


def test_the_diff_file_is_outside_git_and_every_restored_path() -> None:
    top = DIFF_FILE.split("/", 1)[0]
    assert top != ".git"
    assert top not in RESTORED
    assert not top.startswith(PR_COPY.rstrip("/"))
    run = _diff_step()["run"]
    assert f"out={top}\n" in run
    assert f"cap={CAP}\n" in run


def test_the_prompt_names_the_diff_file_and_the_restored_paths() -> None:
    prompt = _prompt()
    assert DIFF_FILE in prompt
    assert "[truncated" in prompt
    # It no longer sends the model to a command that cannot run, nor claims
    # every file on disk is the PR's.
    assert "gh pr diff" not in prompt
    assert "the PR branch is checked out" not in prompt
    for path in RESTORED:
        assert path in prompt, path
    assert PR_COPY in prompt


# --- the script itself, on constructed repositories -------------------------


def _git_env(tmp_path: Path) -> dict[str, str]:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(home),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.invalid",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.invalid",
    }


def _git(repo: Path, env: dict[str, str], *args: str) -> str:
    done = subprocess.run(
        ["git", *args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return done.stdout.strip()


def _repo(
    tmp_path: Path, files: dict[str, bytes], *, merge: bool = True
) -> tuple[Path, dict[str, str]]:
    """A base commit, a PR commit that writes `files`, and GitHub's merge ref.

    The merge ref is a merge commit whose first parent is the base and whose
    second is the PR head, as `refs/pull/<n>/merge` is; it is checked out
    detached, as actions/checkout leaves it.
    """
    if shutil.which("git") is None:
        pytest.skip("git is not on PATH")
    env = _git_env(tmp_path)
    repo = tmp_path / "work"
    repo.mkdir()
    _git(repo, env, "init", "-q", "-b", "main")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, env, "add", "-A")
    _git(repo, env, "commit", "-q", "-m", "base")
    _git(repo, env, "checkout", "-q", "-b", "pr")
    for name, data in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    _git(repo, env, "add", "-A")
    _git(repo, env, "commit", "-q", "-m", "pr")
    if merge:
        tree = _git(repo, env, "rev-parse", "pr^{tree}")
        commit = _git(repo, env, "commit-tree", tree, "-p", "main", "-p", "pr", "-m", "merge")
        _git(repo, env, "checkout", "-q", "--detach", commit)
    return repo, env


def _run_step(repo: Path, env: dict[str, str], tmp_path: Path) -> subprocess.CompletedProcess[str]:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is not on PATH")
    runner_temp = tmp_path / "runner_temp"
    runner_temp.mkdir(exist_ok=True)
    # The same shell GitHub uses for a `run:` step without `shell:` is
    # `bash -e`; the script turns on -u and pipefail itself.
    return subprocess.run(
        [bash, "--noprofile", "--norc", "-e", "-c", _diff_step()["run"]],
        cwd=repo,
        env={**env, "RUNNER_TEMP": str(runner_temp)},
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


def test_constructed_the_step_writes_the_stat_and_the_diff(tmp_path: Path) -> None:
    repo, env = _repo(
        tmp_path,
        {
            "lab/core/src/x.py": b"print('pr line')\n",
            ".claude/agents/pr-shepherd.md": b"the PR's line\n",
        },
    )
    done = _run_step(repo, env, tmp_path)
    assert done.returncode == 0, done.stderr
    text = (repo / DIFF_FILE).read_text(encoding="utf-8")
    head = _git(repo, env, "rev-parse", "HEAD^2")
    base = _git(repo, env, "rev-parse", "HEAD^1")
    assert text.startswith("# The pull request's diff:")
    assert head in text.splitlines()[0]
    assert base in text.splitlines()[0]
    # The stat, then the unified diff, of the PR's changes and nothing else.
    assert re.search(r"lab/core/src/x\.py\s+\| 1 \+", text)
    assert "2 files changed, 2 insertions(+)" in text
    assert "+print('pr line')" in text
    assert "+the PR's line" in text
    assert "README.md" not in text
    assert "[truncated" not in text
    # Nothing left behind in the runner's temp directory.
    assert list((tmp_path / "runner_temp").iterdir()) == []


def test_constructed_a_large_diff_is_capped_with_a_truncated_line(tmp_path: Path) -> None:
    big = b"".join(b"line %06d of a large generated file\n" % i for i in range(20000))
    assert len(big) > CAP
    repo, env = _repo(tmp_path, {"big.txt": big})
    done = _run_step(repo, env, tmp_path)
    assert done.returncode == 0, done.stderr
    data = (repo / DIFF_FILE).read_bytes()
    lines = data.decode("utf-8").splitlines()
    assert lines[-1].startswith("[truncated: the diff is ")
    assert f"only the first {CAP} are above" in lines[-1]
    body = data[: -len(lines[-1]) - 2]
    assert len(body) == CAP
    # The stat came first, so it survives the cut.
    assert re.search(r"big\.txt\s+\| 20000 \+", data.decode("utf-8"))


def test_constructed_a_link_at_the_output_path_is_not_written_through(tmp_path: Path) -> None:
    repo, env = _repo(tmp_path, {"a.txt": b"a\n"})
    outside = tmp_path / "outside"
    outside.mkdir()
    (repo / ".pr-review").symlink_to(outside, target_is_directory=True)
    done = _run_step(repo, env, tmp_path)
    assert done.returncode == 0, done.stderr
    assert list(outside.iterdir()) == []
    assert not (repo / ".pr-review").is_symlink()
    assert (repo / DIFF_FILE).is_file()


def test_constructed_no_diff_driver_from_the_repository_runs(tmp_path: Path) -> None:
    # The PR's tree can name a diff driver in .gitattributes; only git config
    # can define one, and the PR supplies none. Even when one is defined (as
    # here, in the throwaway repository's own config), the step runs neither
    # an external diff nor a textconv program.
    repo, env = _repo(
        tmp_path,
        {".gitattributes": b"*.txt diff=probe\n", "a.txt": b"a\n"},
    )
    marker = tmp_path / "ran"
    _git(repo, env, "config", "diff.probe.textconv", f"touch {marker} #")
    _git(repo, env, "config", "diff.external", f"sh -c 'touch {marker}'")
    done = _run_step(repo, env, tmp_path)
    assert done.returncode == 0, done.stderr
    assert not marker.exists()
    assert "+a" in (repo / DIFF_FILE).read_text(encoding="utf-8")


def test_constructed_a_head_that_is_not_a_merge_fails_the_step(tmp_path: Path) -> None:
    repo, env = _repo(tmp_path, {"a.txt": b"a\n"}, merge=False)
    done = _run_step(repo, env, tmp_path)
    assert done.returncode == 1
    assert "::error::HEAD is not the pull request's merge commit" in done.stdout
    assert not (repo / ".pr-review").exists()
