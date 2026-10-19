"""No job in any workflow restores or saves an Actions cache (M13-42).

From the security review of #244 (M13-39): the Actions cache crosses
workflows. Any job running in main's scope can write any key, and
`claude.yml` and `wiki-daily.yml` run a model on main's ref with a runtime
token whose cache mode is write, whatever the job's own cache setting. A
model past its permissions could pre-seed the key a coming `uv.lock` change
will produce, or evict and rewrite an existing one, and every job that
restores from the cache would run what it wrote. A producer-side fix cannot
close that, so no consumer may exist: every job installs from the lockfile
or downloads its tools fresh.

Static: every workflow under `.github/workflows/` is read as data. A job
caches in one of four ways, and each is rejected:

1. an `actions/cache` step (`actions/cache@`, `actions/cache/restore@`,
   `actions/cache/save@`);
2. `astral-sh/setup-uv` without `enable-cache: false` (an omitted input
   means `auto`, which caches on GitHub-hosted runners);
3. a cache input on an action (`cache: pip` on a setup action,
   `cache-python`, `save-cache`, ...), other than the one input that turns a
   default-on cache off, set to `false`;
4. an action that caches by default with no input at all (`actions/setup-go`
   restores its module cache unless told not to) or nests one that does.
   Each workflow may use only the actions listed for it, at the commit that
   was reviewed for caching, so a new action or a bump fails here until
   someone reads its `action.yml`.

The mutation tests apply each form to the real workflows in memory and show
the check catches it; no workflow file is edited.
"""

from __future__ import annotations

import copy
import functools
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"

CHECKOUT = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
UPLOAD = "actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a"
DOWNLOAD = "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c"
PATHS_FILTER = "dorny/paths-filter@ceb8a2b8f2d89434be7ff52d3de7ec3738c5cc9d"
SETUP_UV = "astral-sh/setup-uv@1c37ad07a6a961277cf70c0d37d6f313000f5884"
ACTIONLINT = "raven-actions/actionlint@3d39aea434753780c3b3d4a1a31c854b4dbf49d7"
TRIVY = "aquasecurity/trivy-action@ed142fd0673e97e23eac54620cfb913e5ce36c25"
CLAUDE = "anthropics/claude-code-action@1d6de8cb0c237e7c15e9e1bdf973826ebae490cc"

# What each action does with the Actions cache at the pinned commit, as read
# from its action.yml on 2026-10-18 (M13-42):
# - checkout, upload-artifact, download-artifact, paths-filter: nothing.
# - setup-uv: `enable-cache` defaults to `auto` (on, on hosted runners).
# - actionlint: `cache` defaults to true and restores the actionlint binary
#   with a nested actions/cache step.
# - trivy-action: `cache` defaults to true; it restores the trivy database
#   with a nested actions/cache step and passes the input to its nested
#   aquasecurity/setup-trivy@3fb12ec, which restores and saves the trivy
#   binary. `cache: false` turns off both.
# - claude-code-action: its only nested action, oven-sh/setup-bun@0c5077e,
#   runs with `no-cache: true` (skipped when `path_to_bun_executable` is set,
#   which no workflow here does).
# A bump of any of them means re-reading its action.yml and any action it
# nests before changing the commit here.
CACHE_OFF: Mapping[str, str] = {
    SETUP_UV: "enable-cache",
    ACTIONLINT: "cache",
    TRIVY: "cache",
}

# The actions each workflow may use, exactly: a missing entry fails, and so
# does an entry the workflow no longer uses.
ALLOWED: Mapping[str, frozenset[str]] = {
    "ci.yml": frozenset({CHECKOUT, PATHS_FILTER, SETUP_UV, ACTIONLINT, TRIVY}),
    "claude-review.yml": frozenset({CHECKOUT, CLAUDE}),
    "claude.yml": frozenset({CHECKOUT, CLAUDE}),
    "release.yml": frozenset({CHECKOUT, SETUP_UV, UPLOAD, DOWNLOAD}),
    "wiki-daily.yml": frozenset({CHECKOUT, SETUP_UV, UPLOAD, DOWNLOAD, CLAUDE}),
    "windows.yml": frozenset({CHECKOUT, SETUP_UV}),
}

Finding = tuple[str, str]


def _workflow_files() -> list[Path]:
    return sorted(p for p in WORKFLOWS.iterdir() if p.suffix in {".yml", ".yaml"})


def _load(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), path
    return data


@functools.cache
def _workflows() -> dict[str, dict[str, Any]]:
    # Parsed once; the mutation tests change deep copies only.
    return {p.name: _load(p) for p in _workflow_files()}


def _steps(workflow: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    jobs = workflow.get("jobs") or {}
    return [(name, step) for name, job in jobs.items() for step in job.get("steps") or []]


def _findings(
    name: str,
    workflow: dict[str, Any],
    allowed: Mapping[str, frozenset[str]] = ALLOWED,
) -> list[Finding]:
    """Every way `workflow` could restore or save an Actions cache."""
    found: list[Finding] = []
    if name not in allowed:
        found.append(("unknown-workflow", name))
    permitted = allowed.get(name, frozenset())
    jobs = workflow.get("jobs") or {}
    for job_name, job in jobs.items():
        # A reusable workflow's jobs and steps are in another file, so no
        # review of this one can see what they cache.
        if "uses" in job:
            found.append(("reusable-workflow", f"{name}:{job_name}"))
    for job_name, step in _steps(workflow):
        if "uses" not in step:
            continue
        uses = str(step["uses"]).strip()
        where = f"{name}:{job_name}:{step.get('name') or uses}"
        # Owner and repository names are case-insensitive to the runner.
        if uses.lower().startswith(("actions/cache@", "actions/cache/")):
            found.append(("cache-action", where))
        if uses not in permitted:
            found.append(("allowlist", where))
        inputs = step.get("with") or {}
        off = CACHE_OFF.get(uses)
        for key, value in inputs.items():
            if "cache" in str(key).lower() and not (key == off and value is False):
                found.append(("cache-input", f"{where}:{key}"))
        if off is not None and inputs.get(off) is not False:
            found.append(("cache-not-off", f"{where}:{off}"))
    return found


def _kinds(found: list[Finding]) -> set[str]:
    return {kind for kind, _ in found}


# ─── static: the real workflows ─────────────────────────────────────────────


def test_every_workflow_is_listed_and_every_listed_one_exists() -> None:
    names = {p.name for p in _workflow_files()}
    assert names == set(ALLOWED)


def test_no_job_in_any_workflow_restores_or_saves_a_cache() -> None:
    for name, workflow in _workflows().items():
        assert _findings(name, workflow) == [], name


def test_each_workflow_uses_exactly_its_reviewed_actions() -> None:
    # An entry the workflow stopped using is removed, so the list stays a
    # record of what was reviewed for each workflow and nothing more.
    for name, workflow in _workflows().items():
        used = {str(s["uses"]).strip() for _, s in _steps(workflow) if "uses" in s}
        assert used == ALLOWED[name], name


def test_the_five_consumer_jobs_install_with_the_cache_off() -> None:
    # The jobs that restored the uv cache before M13-42.
    workflows = _workflows()
    for name, job in (
        ("ci.yml", "quality"),
        ("ci.yml", "parser-suite"),
        ("ci.yml", "lab-desktop"),
        ("ci.yml", "harness"),
        ("windows.yml", "lab-windows"),
    ):
        steps = workflows[name]["jobs"][job]["steps"]
        setup = [s for s in steps if s.get("uses") == SETUP_UV]
        assert [s["with"]["enable-cache"] for s in setup] == [False], (name, job)


def test_every_allowed_action_is_pinned_to_a_full_commit() -> None:
    for entry in set().union(*ALLOWED.values()):
        repo, _, ref = entry.partition("@")
        assert repo.count("/") == 1, entry
        assert len(ref) == 40 and all(c in "0123456789abcdef" for c in ref), entry


# ─── mutations: each form, applied in memory, is caught ─────────────────────


def _mutated(name: str) -> dict[str, Any]:
    return copy.deepcopy(_workflows()[name])


def _first_step(workflow: dict[str, Any], uses: str) -> dict[str, Any]:
    for _, step in _steps(workflow):
        if step.get("uses") == uses:
            return step
    raise AssertionError(uses)


@pytest.mark.parametrize(
    "uses",
    [
        "actions/cache@0057852bfaa89a56745cba8c7296529d2fc39830",
        "actions/cache/restore@0057852bfaa89a56745cba8c7296529d2fc39830",
        "actions/cache/save@0057852bfaa89a56745cba8c7296529d2fc39830",
        "Actions/Cache@v4",
    ],
)
def test_a_cache_step_is_caught(uses: str) -> None:
    workflow = _mutated("ci.yml")
    workflow["jobs"]["quality"]["steps"].insert(
        1, {"uses": uses, "with": {"path": "~/.cache/uv", "key": "uv-${{ hashFiles('uv.lock') }}"}}
    )
    assert "cache-action" in _kinds(_findings("ci.yml", workflow))
    # Even if someone added it to the allowlist.
    allowed = {**ALLOWED, "ci.yml": ALLOWED["ci.yml"] | {uses}}
    assert "cache-action" in _kinds(_findings("ci.yml", workflow, allowed))


@pytest.mark.parametrize("value", ["<omitted>", True, "true", "auto", "${{ true }}"])
def test_setup_uv_without_enable_cache_false_is_caught(value: object) -> None:
    for name in ("windows.yml", "ci.yml", "release.yml", "wiki-daily.yml"):
        workflow = _mutated(name)
        step = _first_step(workflow, SETUP_UV)
        if value == "<omitted>":
            del step["with"]["enable-cache"]
        else:
            step["with"]["enable-cache"] = value
        assert "cache-not-off" in _kinds(_findings(name, workflow)), (name, value)


@pytest.mark.parametrize(
    ("uses", "key", "value"),
    [
        # A setup action's own cache input (allowlisted here, to show the
        # input is caught by itself and not only by the allowlist).
        ("actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065", "cache", "pip"),
        ("actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020", "cache", "npm"),
        (SETUP_UV, "cache-python", True),
        (SETUP_UV, "save-cache", True),
        (SETUP_UV, "restore-cache", True),
        (SETUP_UV, "cache-dependency-glob", "uv.lock"),
        (CHECKOUT, "cache", True),
    ],
)
def test_a_cache_input_is_caught(uses: str, key: str, value: object) -> None:
    workflow = _mutated("release.yml")
    step = {"uses": uses, "with": {key: value}}
    if uses == SETUP_UV:
        step["with"]["enable-cache"] = False
    workflow["jobs"]["build"]["steps"].insert(1, step)
    allowed = {**ALLOWED, "release.yml": ALLOWED["release.yml"] | {uses}}
    assert _kinds(_findings("release.yml", workflow, allowed)) == {"cache-input"}


@pytest.mark.parametrize("uses", [ACTIONLINT, TRIVY])
def test_an_action_whose_cache_is_on_by_default_must_turn_it_off(uses: str) -> None:
    for value in ("<omitted>", True, "true"):
        workflow = _mutated("ci.yml")
        step = _first_step(workflow, uses)
        if value == "<omitted>":
            del step["with"]["cache"]
        else:
            step["with"]["cache"] = value
        assert "cache-not-off" in _kinds(_findings("ci.yml", workflow)), (uses, value)


@pytest.mark.parametrize(
    "uses",
    [
        # Caches by default with no cache input in the step.
        "actions/setup-go@4dc6199c7b1a012772edbd06daecab0f50c9053c",
        "oven-sh/setup-bun@0c5077e51419868618aeaa5fe8019c62421857d6",
        # A reviewed action at another commit, a local composite action and
        # a docker action: none was read for what it caches.
        "actions/checkout@08eba0b27e820071cde6df949e0beb9ba4906955",
        "actions/checkout@v7",
        "./.github/actions/setup",
        "docker://alpine:3.20",
    ],
)
def test_an_action_not_reviewed_for_the_workflow_is_caught(uses: str) -> None:
    for name in ALLOWED:
        workflow = _mutated(name)
        job = next(iter(workflow["jobs"].values()))
        job.setdefault("steps", []).insert(0, {"uses": uses})
        assert _kinds(_findings(name, workflow)) == {"allowlist"}, (name, uses)


def test_an_action_reviewed_for_one_workflow_is_not_allowed_in_another() -> None:
    workflow = _mutated("windows.yml")
    workflow["jobs"]["lab-windows"]["steps"].insert(0, {"uses": TRIVY, "with": {"cache": False}})
    assert _kinds(_findings("windows.yml", workflow)) == {"allowlist"}


def test_a_reusable_workflow_job_and_an_unknown_workflow_are_caught() -> None:
    workflow = _mutated("ci.yml")
    workflow["jobs"]["elsewhere"] = {"uses": "someone/repo/.github/workflows/x.yml@main"}
    assert _kinds(_findings("ci.yml", workflow)) == {"reusable-workflow"}
    assert _kinds(_findings("new.yml", _mutated("windows.yml"))) == {
        "unknown-workflow",
        "allowlist",
    }
