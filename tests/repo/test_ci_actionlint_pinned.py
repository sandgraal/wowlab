"""actionlint is pinned and checksummed in CI; Windows lists its skips (M13-65).

From the security and code reviews of #278 (M13-42) and #276 (M13-51):

1. `ci.yml` ran `raven-actions/actionlint` with its default `version:
   latest`, so the actionlint binary was downloaded unpinned and unchecked
   on every run. The harness job now downloads it the way the gitleaks job
   does: an exact release, checked with `sha256sum -c -` against the digest
   the release's checksums file gives, before the tarball is unpacked. The
   version is the pre-commit hook's, so a local commit and CI lint with the
   same binary.
2. `windows.yml`'s parallel and serial pytest steps ran `-q` without `-rs`,
   so a Windows-only test that skipped was invisible in the log. Every
   pytest step there now reports its skips.

Static: the workflows are read as data. The mutation tests apply each
regression to the real `ci.yml` in memory and show it is caught; no
workflow file is edited. Constructed inputs, labelled `constructed-`.
"""

from __future__ import annotations

import copy
import functools
import re
import shlex
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
PRE_COMMIT = ROOT / ".pre-commit-config.yaml"

TARBALL = "/tmp/actionlint.tar.gz"
# The check, as a bare command on its own line: under GitHub's default
# `bash -e {0}` a mismatch stops the step before anything is unpacked.
CHECK = f'echo "${{ACTIONLINT_SHA256}}  {TARBALL}" | sha256sum -c -'
# Anything after the fetch that reads or runs what was downloaded.
USE = re.compile(r"\b(tar|unzip|install|chmod)\b|/actionlint\b|^actionlint\b")


@functools.cache
def _load(name: str) -> dict[str, Any]:
    data = yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
    assert isinstance(data, dict), name
    return data


def _harness(workflow: dict[str, Any]) -> dict[str, Any]:
    job = workflow["jobs"]["harness"]
    assert isinstance(job, dict)
    return job


def _step(job: dict[str, Any], name: str) -> dict[str, Any]:
    found = [s for s in job["steps"] if s.get("name") == name]
    assert len(found) == 1, name
    return found[0]


def _precommit_rev() -> str:
    config = yaml.safe_load(PRE_COMMIT.read_text(encoding="utf-8"))
    revs = [r["rev"] for r in config["repos"] if r["repo"].endswith("/rhysd/actionlint")]
    assert len(revs) == 1, revs
    rev = str(revs[0])
    assert rev.startswith("v"), rev
    return rev[1:]


def _problems(workflow: dict[str, Any]) -> list[str]:
    """Every way the harness job's actionlint is not pinned and checked."""
    out: list[str] = []
    for job_name, job in (workflow.get("jobs") or {}).items():
        for step in job.get("steps") or []:
            if "actionlint" in str(step.get("uses", "")).lower():
                out.append(f"action:{job_name}")
    job = _harness(workflow)
    if "windows" in str(job.get("runs-on", "")).lower() or "shell" in str(job.get("defaults")):
        out.append("shell")
    install = _step(job, "Install actionlint")
    lint = _step(job, "actionlint")
    steps = job["steps"]
    if steps.index(install) + 1 != steps.index(lint):
        out.append("order")
    for step in (install, lint):
        if "shell" in step or step.get("continue-on-error"):
            out.append(f"shell:{step['name']}")
        if step.get("if") != install.get("if"):
            out.append(f"if:{step['name']}")
    env = install.get("env") or {}
    version = str(env.get("ACTIONLINT_VERSION", ""))
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        out.append("version-not-exact")
    elif version != _precommit_rev():
        out.append("version-not-pre-commit")
    if not re.fullmatch(r"[0-9a-f]{64}", str(env.get("ACTIONLINT_SHA256", ""))):
        out.append("digest")
    lines = [ln.strip() for ln in str(install.get("run", "")).replace("\\\n", " ").splitlines()]
    lines = [ln for ln in lines if ln]
    fetch = [i for i, ln in enumerate(lines) if ln.startswith("curl ")]
    if len(fetch) != 1:
        out.append("fetch")
        return out
    url = lines[fetch[0]]
    if "latest" in url or "${ACTIONLINT_VERSION}" not in url or f"-o {TARBALL}" not in url:
        out.append("url")
    check = [i for i, ln in enumerate(lines) if "sha256sum" in ln]
    if [lines[i] for i in check] != [CHECK]:
        out.append("check-not-bare")
    elif check[0] != fetch[0] + 1:
        out.append("check-not-next")
    before = lines[: fetch[0]] + lines[fetch[0] + 1 : (check or [len(lines)])[0]]
    if any(USE.search(ln) for ln in before):
        out.append("used-before-check")
    if any(re.search(r"\|\s*(ba|z|da)?sh\b", ln) for ln in lines):
        out.append("piped-to-shell")
    if "set +e" in "\n".join(lines):
        out.append("set+e")
    argv = shlex.split(str(lint.get("run", "")))
    if not argv or argv[0] != "actionlint" or "-pyflakes=" not in argv:
        out.append("lint-command")
    return out


# ─── static: the real workflows ─────────────────────────────────────────────


def test_ci_installs_actionlint_pinned_and_checked() -> None:
    assert _problems(_load("ci.yml")) == []


def test_the_pin_is_the_pre_commit_hooks_version() -> None:
    install = _step(_harness(_load("ci.yml")), "Install actionlint")
    assert install["env"]["ACTIONLINT_VERSION"] == _precommit_rev()


def test_no_workflow_uses_an_actionlint_action() -> None:
    for path in sorted(WORKFLOWS.glob("*.y*ml")):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"uses:\s*\S*actionlint", text, re.IGNORECASE), path.name


def _pytest_runs(name: str) -> list[tuple[str, str]]:
    runs: list[tuple[str, str]] = []
    for job in _load(name)["jobs"].values():
        for step in job.get("steps") or []:
            run = str(step.get("run", ""))
            if re.search(r"\bpytest\b", run):
                runs.append((str(step.get("name")), run))
    return runs


def _reports_skips(run: str) -> bool:
    # pytest's -r chars: `s` is skipped; `a` and `A` include it.
    return any(tok.startswith("-r") and bool(set(tok[2:]) & set("saA")) for tok in shlex.split(run))


def test_every_windows_pytest_step_lists_its_skips() -> None:
    runs = _pytest_runs("windows.yml")
    names = {name for name, _ in runs}
    assert {"pytest lab/core/tests (parallel)", "pytest lab/core/tests (serial)"} <= names
    for name, run in runs:
        assert _reports_skips(run), name


# ─── mutations: each regression, applied in memory, is caught ───────────────


def _mutated() -> dict[str, Any]:
    return copy.deepcopy(_load("ci.yml"))


def _install(workflow: dict[str, Any]) -> dict[str, Any]:
    return _step(_harness(workflow), "Install actionlint")


def _edit_run(workflow: dict[str, Any], old: str, new: str) -> None:
    step = _install(workflow)
    assert old in step["run"], old
    step["run"] = step["run"].replace(old, new)


def _cases() -> list[Any]:
    def env(key: str, value: object) -> Any:
        return lambda w: _install(w)["env"].__setitem__(key, value)

    def run(old: str, new: str) -> Any:
        return lambda w: _edit_run(w, old, new)

    def drop_env(key: str) -> Any:
        return lambda w: _install(w)["env"].pop(key)

    def readd_action(w: dict[str, Any]) -> None:
        _step(_harness(w), "actionlint")["uses"] = "raven-actions/actionlint@v2"

    def lint_with_pyflakes(w: dict[str, Any]) -> None:
        _step(_harness(w), "actionlint")["run"] = "actionlint -color"

    def continue_on_error(w: dict[str, Any]) -> None:
        _install(w)["continue-on-error"] = True

    def pwsh(w: dict[str, Any]) -> None:
        _install(w)["shell"] = "pwsh"

    tarball_line = "tar -xzf /tmp/actionlint.tar.gz -C /tmp actionlint"
    return [
        pytest.param(
            env("ACTIONLINT_VERSION", "latest"), "version-not-exact", id="constructed-latest"
        ),
        pytest.param(env("ACTIONLINT_VERSION", "1.7"), "version-not-exact", id="constructed-short"),
        pytest.param(
            env("ACTIONLINT_VERSION", "1.7.0"), "version-not-pre-commit", id="constructed-drift"
        ),
        pytest.param(drop_env("ACTIONLINT_SHA256"), "digest", id="constructed-no-digest"),
        pytest.param(env("ACTIONLINT_SHA256", "abc123"), "digest", id="constructed-short-digest"),
        pytest.param(
            run("download/v${ACTIONLINT_VERSION}/", "latest/download/"),
            "url",
            id="constructed-latest-url",
        ),
        pytest.param(run(CHECK, CHECK + " || true"), "check-not-bare", id="constructed-or-true"),
        pytest.param(run(CHECK, "! " + CHECK), "check-not-bare", id="constructed-negated"),
        pytest.param(run(CHECK, "set +e\n" + CHECK), "set+e", id="constructed-set-plus-e"),
        pytest.param(run(CHECK + "\n", ""), "check-not-bare", id="constructed-no-check"),
        pytest.param(
            run(CHECK + "\n" + tarball_line, tarball_line + "\n" + CHECK),
            "check-not-next",
            id="constructed-unpacked-first",
        ),
        pytest.param(
            run(TARBALL + " \\\n", TARBALL + " \\\n  | sh \\\n"),
            "piped-to-shell",
            id="constructed-pipe-sh",
        ),
        pytest.param(readd_action, "action:harness", id="constructed-action-back"),
        pytest.param(lint_with_pyflakes, "lint-command", id="constructed-pyflakes-on"),
        pytest.param(continue_on_error, "shell:Install actionlint", id="constructed-continue"),
        pytest.param(pwsh, "shell:Install actionlint", id="constructed-pwsh"),
    ]


@pytest.mark.parametrize(("mutate", "kind"), _cases())
def test_a_regression_is_caught(mutate: Any, kind: str) -> None:
    workflow = _mutated()
    mutate(workflow)
    assert kind in _problems(workflow)


@pytest.mark.parametrize(
    ("run", "expected"),
    [
        ("uv run pytest -q -rs lab", True),
        ("uv run pytest -q -rxXs lab", True),
        ("uv run pytest -q -ra lab", True),
        ("uv run pytest -q -rA lab", True),
        ("uv run pytest -q lab", False),
        ("uv run pytest -q -rxX lab", False),
    ],
    ids=lambda v: f"constructed-{v}" if isinstance(v, str) else str(v),
)
def test_the_skip_report_flag_is_recognised(run: str, expected: bool) -> None:
    assert _reports_skips(run) is expected
