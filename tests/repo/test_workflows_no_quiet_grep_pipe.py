"""No workflow step pipes into a quiet `grep` (M18-12).

A step runs under `bash -eo pipefail`. In `printf '%s\\n' "$assets" | grep -q
NAME`, grep exits at its first match. Bash line-buffers its stdout, so the
builtin `printf` writes a multi-line value one line per write(); a match on
an early line can close the pipe before the later writes, `printf` dies of
SIGPIPE (141), and pipefail turns grep's match into a failed test. Under
load it happened about 4 times in 1000 (24 processes on 8 cores, bash 3.2),
and once failed `test_constructed_a_rerun_after_both_uploads_nothing`:
release.yml's attach job read "SHA256SUMS is not on the release" for a
release that had one. `grep … >/dev/null` reads all of its input, so the
writer never sees a closed pipe.

Static: every `run:` block of every workflow is read as data. The mutation
case puts the old form into a constructed block and shows the check finds it.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"

# A pipe into grep with -q (alone or among short options), --quiet or --silent,
# before any `--` that ends the options.
QUIET_GREP = re.compile(
    r"\|\s*grep\b(?:(?!\s--(?:\s|$))[^|;&\n])*?\s(?:-[A-Za-z]*q[A-Za-z]*|--quiet|--silent)(?=\s|$)",
    re.MULTILINE,
)


def _runs(data: Any) -> Iterator[tuple[str, str]]:
    for job_id, job in (data.get("jobs") or {}).items():
        for i, step in enumerate(job.get("steps") or []):
            if "run" in step:
                yield f"{job_id}[{i}] {step.get('name', '')}".rstrip(), step["run"]


def quiet_grep_pipes(data: Any) -> list[str]:
    """Every step whose `run` pipes into a quiet grep, with the line."""
    found = []
    for where, run in _runs(data):
        for match in QUIET_GREP.finditer(run):
            line = run[: match.end()].rsplit("\n", 1)[-1].strip()
            found.append(f"{where}: {line}")
    return found


def _workflows() -> list[Path]:
    return sorted(WORKFLOWS.glob("*.yml")) + sorted(WORKFLOWS.glob("*.yaml"))


def test_there_are_workflows_with_run_blocks_to_check() -> None:
    assert any(list(_runs(yaml.safe_load(p.read_text(encoding="utf-8")))) for p in _workflows())


@pytest.mark.parametrize("path", _workflows(), ids=lambda p: p.name)
def test_no_run_block_pipes_into_a_quiet_grep(path: Path) -> None:
    found = quiet_grep_pipes(yaml.safe_load(path.read_text(encoding="utf-8")))
    assert found == [], (
        "under pipefail the writer can die of SIGPIPE when grep -q exits early; "
        "use `grep … >/dev/null`, which reads all its input: " + "; ".join(found)
    )


def _constructed(run: str) -> dict[str, Any]:
    return {"jobs": {"j": {"steps": [{"name": "s", "run": run}]}}}


@pytest.mark.parametrize(
    "run",
    [
        "if ! printf '%s\\n' \"$assets\" | grep -Fqx SHA256SUMS; then exit 1; fi\n",
        'printf \'%s\\n\' "$a" | grep -q -- "$B" && echo yes\n',
        "echo x |grep --quiet x\n",
        "echo x | grep --silent x\n",
        "cat f | grep -Eqx '[0-9a-f]{64}'\n",
        "a\nb | grep -sq x\n",
    ],
)
def test_constructed_a_quiet_grep_pipe_is_found(run: str) -> None:
    assert len(quiet_grep_pipes(_constructed(run))) == 1


@pytest.mark.parametrize(
    "run",
    [
        "if ! printf '%s\\n' \"$assets\" | grep -Fx SHA256SUMS >/dev/null; then exit 1; fi\n",
        "grep -q x file\n",  # no pipe: grep reads a file
        "grep -Ecx '[0-9a-f]{64}  [^ ]+' \"$RUNNER_TEMP/expected\"\n",
        "printf x | grep -x -- -q >/dev/null\n",  # -q as the pattern, after --
    ],
)
def test_constructed_a_full_read_grep_is_not_flagged(run: str) -> None:
    assert quiet_grep_pipes(_constructed(run)) == []
