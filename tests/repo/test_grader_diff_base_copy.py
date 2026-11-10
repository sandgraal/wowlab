"""The grader-diff job runs only the base revision's copy of its script (M16-59).

From the M16-40 security review (2026-11-04): when the base revision had no
`scripts/check_grader_diff.py`, the step in `ci.yml`'s `grader-diff` job fell
back to `cp scripts/check_grader_diff.py`, the pull request's own copy, so a
pull request that deleted the script on its base side, or arrived on a base
that lacked it, was judged by a check it wrote itself. The script is on
`main` now, so a missing base copy is a hard failure with an `::error::`
message.

Behavioural: the step's `run:` text is read from the real `ci.yml` and run
under `bash -e` (GitHub's default shell for a `run:` step on Linux) in a
throwaway repository built in `tmp_path`. The scripts in that repository are
constructed stand-ins that only print which copy ran. The refusal
assertions live in one helper, `_assert_refuses`; the mutation test runs that
same helper against the old fallback, held in memory, and shows it fails
there. No workflow file is edited.

Static, from the M16-59 security review: the job has no `if:` or `needs:`
that could skip it, the workflow's triggers carry no `paths:` filter that
could keep it from running, and the base copy runs under `python3 -I`.
"""

from __future__ import annotations

import functools
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
CI = ROOT / ".github" / "workflows" / "ci.yml"
SCRIPT = "scripts/check_grader_diff.py"
STEP = "Check grader diffs"

# Constructed stand-ins for the real script: each prints which copy ran.
BASE_COPY = 'print("constructed-base-copy-ran")\n'
HEAD_COPY = 'print("constructed-head-copy-ran")\n'

# The step as it was before M16-59, for the mutation test.
OLD_FALLBACK = f"""script="$RUNNER_TEMP/check_grader_diff.py"
if git cat-file -e HEAD^1:{SCRIPT} 2>/dev/null; then
  git show HEAD^1:{SCRIPT} > "$script"
else
  cp {SCRIPT} "$script"
fi
python3 -I "$script" --repo . --base HEAD^1 --head HEAD
"""

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


@functools.cache
def _workflow() -> dict[Any, Any]:
    data = yaml.safe_load(CI.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


@functools.cache
def _step_run() -> str:
    steps = _workflow()["jobs"]["grader-diff"]["steps"]
    found = [s for s in steps if s.get("name") == STEP]
    assert len(found) == 1, STEP
    run = found[0]["run"]
    assert isinstance(run, str)
    assert "shell" not in found[0], "the step must run under the default bash -e"
    return run


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _repo(tmp_path: Path, base: str | None, head: str | None) -> Path:
    """A two-commit repository: HEAD^1 holds `base`, HEAD holds `head`."""
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "gate@example.invalid")
    _git(root, "config", "user.name", "gate")
    _git(root, "config", "commit.gpgsign", "false")
    script = root / SCRIPT
    script.parent.mkdir()
    (root / "README").write_text("base\n")
    if base is not None:
        script.write_text(base)
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "base")
    (root / "README").write_text("head\n")
    if head is None:
        script.unlink(missing_ok=True)
    else:
        script.write_text(head)
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "head")
    return root


def _run_step(run: str, repo: Path, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    runner_temp = tmp_path / "runner_temp"
    runner_temp.mkdir()
    env = {**os.environ, "RUNNER_TEMP": str(runner_temp)}
    return subprocess.run(
        ["bash", "-e", "-c", run],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def _assert_refuses(run: str, tmp_path: Path) -> None:
    """`run`, on a base without the script, refuses and never runs the head copy."""
    repo = _repo(tmp_path, base=None, head=HEAD_COPY)
    out = _run_step(run, repo, tmp_path)
    assert out.returncode != 0, out.stdout + out.stderr
    assert "constructed-head-copy-ran" not in out.stdout + out.stderr
    assert out.stdout.startswith("::error::"), out.stdout
    assert SCRIPT in out.stdout and "base revision" in out.stdout


def test_base_without_script_refuses_and_never_runs_the_head_copy(tmp_path: Path) -> None:
    _assert_refuses(_step_run(), tmp_path)


def test_base_with_script_runs_the_base_copy_not_the_head_copy(tmp_path: Path) -> None:
    repo = _repo(tmp_path, base=BASE_COPY, head=HEAD_COPY)
    out = _run_step(_step_run(), repo, tmp_path)
    assert out.returncode == 0, out.stdout + out.stderr
    assert out.stdout.strip() == "constructed-base-copy-ran"


def test_head_deleting_the_script_still_runs_the_base_copy(tmp_path: Path) -> None:
    repo = _repo(tmp_path, base=BASE_COPY, head=None)
    out = _run_step(_step_run(), repo, tmp_path)
    assert out.returncode == 0, out.stdout + out.stderr
    assert out.stdout.strip() == "constructed-base-copy-ran"


def test_base_copy_failure_fails_the_step(tmp_path: Path) -> None:
    repo = _repo(tmp_path, base="raise SystemExit(3)\n", head=HEAD_COPY)
    out = _run_step(_step_run(), repo, tmp_path)
    assert out.returncode == 3, out.stdout + out.stderr


def test_step_never_reads_the_working_tree_copy() -> None:
    run = _step_run()
    # Every command that names the script names the base revision's blob;
    # the refusal message is the one line that mentions it as text.
    for line in run.splitlines():
        if SCRIPT in line and not line.lstrip().startswith('echo "::error::'):
            assert f"HEAD^1:{SCRIPT}" in line, line


def test_mutation_old_fallback_is_caught(tmp_path: Path) -> None:
    # The pre-M16-59 step copies and runs the head's script when the base
    # lacks it; the same refusal assertions the real step passes fail here.
    with pytest.raises(AssertionError):
        _assert_refuses(OLD_FALLBACK, tmp_path)


def test_job_cannot_be_skipped() -> None:
    data = _workflow()
    job = data["jobs"]["grader-diff"]
    assert "if" not in job, job.get("if")
    assert "needs" not in job, job.get("needs")
    # PyYAML reads the bare key `on` as the boolean True.
    triggers = data.get("on", data.get(True))
    assert isinstance(triggers, dict), triggers
    for event, spec in triggers.items():
        if isinstance(spec, dict):
            assert "paths" not in spec and "paths-ignore" not in spec, event


def test_base_copy_runs_isolated() -> None:
    runs = [line.split() for line in _step_run().splitlines() if "python" in line]
    assert len(runs) == 1, runs
    assert runs[0][:2] == ["python3", "-I"], runs[0]
