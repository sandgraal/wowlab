# Probe from review of m13/59-review-diff-file; reproduces the PR's own .gitattributes turning every hunk of .pr-review/pr.diff into "Binary files ... differ"
"""Constructed (not a real capture): a throwaway repository in `tmp_path` with
a base commit, a PR commit and a merge commit shaped like
`refs/pull/<n>/merge`, checked out detached as actions/checkout leaves it.

The review step in `.github/workflows/claude-review.yml` diffs the merge ref
with `--no-ext-diff --no-textconv`, so no program named by `.gitattributes`
runs. But `git diff` still reads the `diff` attribute from the working tree,
which on the merge ref is the PR's tree. A PR that adds `* -diff` (or marks
one path `-diff`/`binary`) makes git print "Binary files a/x and b/x differ"
for that path: the file the model is told is "the diff" then carries no
changed line for it. The step's script is read from the workflow and run
with bash, as the M13-59 tests do.

Positive control: the same PR without the attribute line puts the changed
line in the file.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[4]
REVIEW = ROOT / ".github" / "workflows" / "claude-review.yml"
STEP_NAME = "Write the pull request diff for the review"
SECRET_LINE = "skip_snapshot = True"


def _script() -> str:
    steps = yaml.safe_load(REVIEW.read_text(encoding="utf-8"))["jobs"]["review"]["steps"]
    found = [s for s in steps if s.get("name") == STEP_NAME]
    assert len(found) == 1
    return str(found[0]["run"])


def _git(repo: Path, env: dict[str, str], *args: str) -> str:
    done = subprocess.run(
        ["git", *args], cwd=repo, env=env, capture_output=True, text=True, check=True, timeout=60
    )
    return done.stdout.strip()


def _diff_file(tmp_path: Path, files: dict[str, bytes]) -> str:
    bash = shutil.which("bash")
    if shutil.which("git") is None or bash is None:
        pytest.skip("git or bash is not on PATH")
    home = tmp_path / "home"
    home.mkdir()
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(home),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.invalid",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.invalid",
    }
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
    tree = _git(repo, env, "rev-parse", "pr^{tree}")
    merge = _git(repo, env, "commit-tree", tree, "-p", "main", "-p", "pr", "-m", "merge")
    _git(repo, env, "checkout", "-q", "--detach", merge)
    runner_temp = tmp_path / "runner_temp"
    runner_temp.mkdir()
    done = subprocess.run(
        [bash, "--noprofile", "--norc", "-e", "-c", _script()],
        cwd=repo,
        env={**env, "RUNNER_TEMP": str(runner_temp)},
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert done.returncode == 0, done.stderr
    return (repo / ".pr-review" / "pr.diff").read_text(encoding="utf-8")


CHANGE = {"lab/core/src/wowlab_core/guard.py": f"def write():\n    {SECRET_LINE}\n".encode()}


def test_constructed_positive_control_the_changed_line_is_in_the_diff(tmp_path: Path) -> None:
    assert f"+    {SECRET_LINE}" in _diff_file(tmp_path, CHANGE)


def test_constructed_the_prs_gitattributes_cannot_hide_a_change(tmp_path: Path) -> None:
    text = _diff_file(tmp_path, {".gitattributes": b"* -diff\n", **CHANGE})
    assert "Binary files" not in text
    assert f"+    {SECRET_LINE}" in text
