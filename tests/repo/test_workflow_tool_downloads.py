"""Every tool a workflow downloads is pinned and checksummed (M13-65).

From the reviews of #278 (M13-42): `raven-actions/actionlint` ran with its
default `version: latest`, so the actionlint binary was fetched unpinned and
unchecked on every run. The CI job now downloads it the way the gitleaks
jobs do: an exact release, checked against the sha256 in the release's
checksums file before it is unpacked.

Static: every `run:` step under `.github/workflows/` that fetches with `curl`
or `wget` must
- fetch no `latest` URL;
- name its version through `*_VERSION` variables set (step, job or workflow
  `env`) to an exact `x.y.z`;
- check what it fetched with `sha256sum -c` against a 64-hex digest, written
  in the script or taken from an `env` variable.

The mutation tests apply each failure to the real workflows in memory; no
workflow file is edited.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"

FETCH = re.compile(r"\b(?:curl|wget)\b")
VERSION_REF = re.compile(r"\$\{?([A-Z][A-Z0-9_]*_VERSION)\}?")
EXACT_VERSION = re.compile(r"\d+\.\d+\.\d+")
CHECK = re.compile(r"""echo\s+["']?(\S+)\s+\S+["']?\s*\|\s*sha256sum\s+-c\s+-""")
SHA256 = re.compile(r"[0-9a-f]{64}")
VAR = re.compile(r"\$\{?([A-Za-z_][A-Za-z0-9_]*)\}?")


def _load(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), path
    return data


def _workflows() -> dict[str, dict[str, Any]]:
    return {p.name: _load(p) for p in sorted(WORKFLOWS.glob("*.y*ml"))}


def _fetching_steps(
    workflow: dict[str, Any],
) -> Iterator[tuple[str, str, Mapping[str, Any]]]:
    """(where, script, env in scope) for each run step that downloads."""
    top = workflow.get("env") or {}
    for job_id, job in (workflow.get("jobs") or {}).items():
        job_env = {**top, **(job.get("env") or {})}
        for i, step in enumerate(job.get("steps") or []):
            script = step.get("run")
            if not isinstance(script, str) or not FETCH.search(script):
                continue
            env = {**job_env, **(step.get("env") or {})}
            yield f"{job_id}[{i}] {step.get('name', '')}", script, env


def _findings(workflow: dict[str, Any]) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for where, script, env in _fetching_steps(workflow):
        if "latest" in script:
            found.append((where, "latest"))
        for name in VERSION_REF.findall(script):
            if not EXACT_VERSION.fullmatch(str(env.get(name, ""))):
                found.append((where, "version"))
        digests = CHECK.findall(script)
        if not digests:
            found.append((where, "unchecked"))
        for digest in digests:
            ref = VAR.fullmatch(digest)
            value = str(env.get(ref.group(1), "")) if ref else digest
            if not SHA256.fullmatch(value):
                found.append((where, "digest"))
    return found


def _ci() -> dict[str, Any]:
    return copy.deepcopy(_workflows()["ci.yml"])


def _actionlint_step(workflow: dict[str, Any]) -> dict[str, Any]:
    for job in workflow["jobs"].values():
        for step in job.get("steps") or []:
            if step.get("name") == "Install actionlint":
                return step  # type: ignore[no-any-return]
    raise AssertionError("ci.yml has no 'Install actionlint' step")


@pytest.mark.parametrize("name", sorted(_workflows()))
def test_every_download_is_pinned_and_checksummed(name: str) -> None:
    assert _findings(_workflows()[name]) == []


def test_ci_downloads_actionlint_and_gitleaks() -> None:
    wheres = [where for where, _, _ in _fetching_steps(_workflows()["ci.yml"])]
    assert any("Install actionlint" in w for w in wheres), wheres
    assert any("Install gitleaks" in w for w in wheres), wheres


def test_ci_does_not_use_an_actionlint_action() -> None:
    text = (WORKFLOWS / "ci.yml").read_text(encoding="utf-8")
    assert "raven-actions/actionlint" not in text


def test_a_latest_url_is_caught() -> None:
    workflow = _ci()
    step = _actionlint_step(workflow)
    step["run"] = step["run"].replace("download/v${ACTIONLINT_VERSION}", "latest/download")
    assert "latest" in {kind for _, kind in _findings(workflow)}


@pytest.mark.parametrize("version", ["latest", "1.7", "v1.7.12", "", None])
def test_an_inexact_version_is_caught(version: str | None) -> None:
    workflow = _ci()
    step = _actionlint_step(workflow)
    if version is None:
        del step["env"]["ACTIONLINT_VERSION"]
    else:
        step["env"]["ACTIONLINT_VERSION"] = version
    assert "version" in {kind for _, kind in _findings(workflow)}


def test_a_missing_check_is_caught() -> None:
    workflow = _ci()
    step = _actionlint_step(workflow)
    step["run"] = "\n".join(line for line in step["run"].splitlines() if "sha256sum" not in line)
    assert "unchecked" in {kind for _, kind in _findings(workflow)}


@pytest.mark.parametrize("digest", ["deadbeef", "${UNSET_SHA}", "X" * 64])
def test_a_bad_digest_is_caught(digest: str) -> None:
    workflow = _ci()
    step = _actionlint_step(workflow)
    step["run"] = SHA256.sub(digest, step["run"], count=1)
    assert "digest" in {kind for _, kind in _findings(workflow)}


def test_a_digest_from_env_is_resolved() -> None:
    # wiki-daily.yml takes its gitleaks digest from GITLEAKS_SHA256.
    workflow = copy.deepcopy(_workflows()["wiki-daily.yml"])
    assert _findings(workflow) == []
    workflow["env"]["GITLEAKS_SHA256"] = "not-a-digest"
    assert "digest" in {kind for _, kind in _findings(workflow)}
