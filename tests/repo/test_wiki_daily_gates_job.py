"""The wiki-daily gates run in a job of their own, on a fresh runner (M13-19).

From the M13-16 reviews: within one job, the steps after the model cannot
protect themselves from a model that gets past its permissions. A line it
appends to its step's `$GITHUB_ENV` file sets `BASH_ENV` (which bash sources
even with `--noprofile --norc`), `LD_PRELOAD`, `GIT_*`, `UV_*` or
`PYTHONPATH` for every later step of that job. Files outside `repo/` (uv's
cache, the actions' code, the venv's base interpreter) are not compared, a
process left running defeats a point-in-time snapshot, and passwordless sudo
makes the rest of the runner writable. So check_wiki, gitleaks and the gate
result run in `gates`, which takes from `improve` only the pages (as an
artifact, as data), the outputs of steps that ran before the model, and step
outcomes; and `publish` and `report` take what `gates` uploaded by artifact id.

Static tests read `.github/workflows/wiki-daily.yml` as data. The
`constructed` test runs the gates' own step scripts with bash, in the
environment the runner would give each step after the model's step wrote
`BASH_ENV=<a planted script that writes a marker>` to its `$GITHUB_ENV`.
GitHub applies a `$GITHUB_ENV` write to the remaining steps of the same job
only (workflow commands, "Setting an environment variable"); the test derives
from the workflow which steps those are, so a gate moved back after the model
in its job fails it.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "wiki-daily.yml"
ACTION = "anthropics/claude-code-action@"
# GitHub's command line for `shell: bash` (the workflow's default).
DEFAULT_BASH = "bash --noprofile --norc -eo pipefail {0}"
GATE_IDS = ("apply", "check", "gitleaks", "gate")
# What judges the model's work: the checker, gitleaks, the state update the
# gate checks, and the gate report.
RUNS_A_GATE = re.compile(
    r"scripts/check_wiki\.py|\bgitleaks\b|wiki_state\.py update|report/check\.txt"
)
GATED_ARTIFACTS = ("wiki-daily-report", "wiki-daily-pages")
HANDOFF = "wiki-daily-unchecked"
# The improve steps after the model: a tripwire, a redacted diagnosis, the
# staging and hand-off of the pages, and the cleanup for its post steps. None
# of them a gate that could pass the pages; `withhold` (round 1, security)
# runs check_wiki but can only keep them back.
AFTER_THE_MODEL = [
    "Nothing outside the wiki changed",
    "Diagnose the Claude step (redacted)",
    "Stage the pages for the gates",
    "Withhold pages with personal data",
    "Hand the pages to the gates",
    "Leave nothing in repo/ for the post steps",
]
BLOCK_ONLY = "withhold"
TREE_CHECK = "odd=$(find wiki -path wiki/.git -prune -o ! -type f ! -type d -print -quit)"
PINNED = re.compile(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}")
# Variables a model past its permissions would set through $GITHUB_ENV.
MODEL_WRITES = (
    "BASH_ENV",
    "ENV",
    "LD_PRELOAD",
    "LD_LIBRARY_PATH",
    "PYTHONPATH",
    "GIT_CONFIG_GLOBAL",
    "UV_PROJECT",
    "PATH",
)


def _workflow() -> dict[str, Any]:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _jobs() -> dict[str, Any]:
    jobs = _workflow()["jobs"]
    assert isinstance(jobs, dict)
    return jobs


def _model_job(jobs: dict[str, Any]) -> tuple[str, int]:
    """The one job that runs the model, and the index of its Claude step."""
    found = [
        (name, i)
        for name, job in jobs.items()
        for i, step in enumerate(job.get("steps", []))
        if str(step.get("uses", "")).startswith(ACTION)
    ]
    assert len(found) == 1, found
    return found[0]


def _code(step: dict[str, Any]) -> str:
    lines = step.get("run", "").splitlines()
    return "\n".join(ln for ln in lines if not ln.lstrip().startswith("#"))


def _uses(step: dict[str, Any], prefix: str) -> bool:
    return str(step.get("uses", "")).startswith(prefix)


def _by_id(steps: list[dict[str, Any]], step_id: str) -> dict[str, Any]:
    found = [s for s in steps if s.get("id") == step_id]
    assert len(found) == 1, step_id
    return found[0]


# ─── static: nothing judges the model on its own runner ──────────────────────


def test_no_gate_runs_after_the_model_in_its_job() -> None:
    jobs = _jobs()
    job, claude = _model_job(jobs)
    assert job == "improve"
    after = jobs[job]["steps"][claude + 1 :]
    assert [s["name"] for s in after] == AFTER_THE_MODEL
    for step in after:
        assert step.get("id") not in GATE_IDS, step["name"]
        if step.get("id") != BLOCK_ONLY:
            assert not RUNS_A_GATE.search(_code(step)), step["name"]
        # No gated artifact leaves the model's job.
        if _uses(step, "actions/upload-artifact@"):
            assert step["with"]["name"] == HANDOFF
    # The one exception can only withhold the pages: it writes no output, it
    # fails (and only fails) on check_wiki's personal-data or error exit,
    # and the hand-off needs its success.
    block = _by_id(jobs[job]["steps"], BLOCK_ONLY)
    assert block["continue-on-error"] is True
    run = block["run"]
    assert "GITHUB_OUTPUT" not in run and "GITHUB_ENV" not in run
    assert "uv run --frozen python scripts/check_wiki.py ../handoff > /dev/null 2>&1" in run
    assert re.findall(r"\bexit\b.*", run) == ["exit 1"]
    handoff = _by_id(jobs[job]["steps"], "handoff")
    assert "steps.withhold.outcome == 'success'" in handoff["if"]
    assert handoff["with"]["path"] == "handoff/"
    # The gate steps are all in gates, and nowhere else.
    for name, body in jobs.items():
        ids = {s.get("id") for s in body.get("steps", [])}
        if name == "gates":
            assert set(GATE_IDS) <= ids
        else:
            assert not set(GATE_IDS) & ids, name


def test_the_gates_job_runs_on_a_fresh_runner_with_nothing_of_the_models() -> None:
    jobs = _jobs()
    gates = jobs["gates"]
    # A GitHub-hosted label: a fresh virtual machine for every job. A
    # self-hosted runner could be the one improve ran on.
    assert gates["runs-on"] == jobs["improve"]["runs-on"] == "ubuntu-latest"
    assert gates["needs"] == "improve"
    assert gates["permissions"] == {"contents": "read"}
    assert "env" not in gates
    assert "container" not in gates and "services" not in gates
    steps = gates["steps"]
    assert not any(_uses(s, ACTION) for s in steps)
    # Only these actions, pinned; none of them caches anything.
    allowed = (
        "actions/checkout@",
        "astral-sh/setup-uv@",
        "actions/download-artifact@",
        "actions/upload-artifact@",
    )
    for step in steps:
        if "uses" in step:
            assert step["uses"].startswith(allowed), step["uses"]
            assert PINNED.fullmatch(step["uses"].split(" ")[0]), step["uses"]
    assert "actions/cache" not in yaml.safe_dump(gates)
    setup = [s for s in steps if _uses(s, "astral-sh/setup-uv@")]
    assert len(setup) == 1
    assert setup[0]["with"]["enable-cache"] is False
    # Its own checkout of main and its own clone of the wiki.
    checkout = [s for s in steps if _uses(s, "actions/checkout@")]
    assert len(checkout) == 1
    assert checkout[0]["with"] == {
        "ref": "${{ github.sha }}",
        "path": "repo",
        "persist-credentials": False,
    }
    clone = next(s for s in steps if s.get("name", "").startswith("Clone the wiki"))["run"]
    assert 'git clone --quiet "${GITHUB_SERVER_URL}/${GITHUB_REPOSITORY}.wiki.git" wiki' in clone
    assert 'checkout --quiet --detach "$BASE"' in clone
    # From improve's runner, the pages only: one download, of the hand-off.
    downloads = [s for s in steps if _uses(s, "actions/download-artifact@")]
    assert len(downloads) == 1
    assert downloads[0]["with"] == {"name": HANDOFF, "path": "incoming"}
    handoff = _by_id(jobs["improve"]["steps"], "handoff")
    assert handoff["with"]["name"] == HANDOFF
    assert handoff["with"]["retention-days"] == 1
    # Without overwrite, an artifact of that name already in the run fails it.
    assert "overwrite" not in handoff["with"]
    # And only regular top-level .md files of it, as publish takes them,
    # each replacing what it lands on (round 1, security).
    apply = _by_id(steps, "apply")["run"]
    assert (
        "find ../incoming -maxdepth 1 -type f -name '*.md'"
        " -exec cp --remove-destination -- {} ../wiki/ \\;"
    ) in apply
    assert apply.count("../incoming") == 1


def test_no_symlink_in_the_wiki_or_an_upload_can_carry_page_text_elsewhere() -> None:
    # Round 1 (security): a symlinked page in the base clone let `cp` write
    # page text through it into repo/, and upload-artifact follows symlinks.
    jobs = _jobs()
    trees = []
    for name in ("gates", "publish"):
        steps = jobs[name]["steps"]
        tree = _by_id(steps, "tree")
        trees.append(tree["run"])
        assert "if" not in tree and tree.get("continue-on-error") is not True
        assert TREE_CHECK in tree["run"].splitlines()[1]
        # Straight after the clone, before anything is taken or applied.
        at = steps.index(tree)
        assert steps[at - 1]["name"].startswith("Clone the wiki")
        apply = next(s for s in steps if s.get("name") == "Apply the pages")
        assert steps.index(apply) > at
        assert "cp --remove-destination --" in apply["run"]
        assert re.search(r"\bcp -- ", apply["run"]) is None
    assert trees[0] == trees[1]
    # Every upload of pages is a folder of copied regular files.
    stage = _by_id(jobs["improve"]["steps"], "stage")["run"]
    assert "find wiki -maxdepth 1 -type f -name '*.md' -exec cp -- {} handoff/ \\;" in stage
    stage = _by_id(jobs["gates"]["steps"], "stage")["run"]
    assert "find wiki -maxdepth 1 -type f -name '*.md' -exec cp -- {} out/wiki/ \\;" in stage
    for body in jobs.values():
        for step in body.get("steps", []):
            if _uses(step, "actions/upload-artifact@"):
                assert "wiki/" not in str(step["with"]["path"]), step["name"]


def test_the_gated_uploads_leave_only_on_the_gate_results_word() -> None:
    # Round 1 (code review): the only thing keeping the report and the pages
    # in on a personal-data hit or a gitleaks finding.
    steps = _jobs()["gates"]["steps"]
    assert _by_id(steps, "report")["if"] == "always() && steps.gate.outputs.report == 'true'"
    assert _by_id(steps, "stage")["if"] == "always() && steps.gate.outputs.pages == 'true'"
    assert _by_id(steps, "pages")["if"] == (
        "always() && steps.gate.outputs.pages == 'true' && steps.stage.outcome == 'success'"
    )
    assert _by_id(steps, "pages")["with"]["path"] == "out/"


def test_the_gate_result_reads_each_verdict_from_its_own_step() -> None:
    # Round 2 (code review): `LEAKS: success` would ignore gitleaks' verdict
    # and still pass every constructed run. Every input is pinned to its source.
    env = _by_id(_jobs()["gates"]["steps"], "gate")["env"]
    assert env == {
        "OUTSIDE": "${{ needs.improve.outputs.outside }}",
        "WITHHOLD": "${{ needs.improve.outputs.withhold }}",
        "HANDOFF": "${{ needs.improve.outputs.handoff }}",
        "SETUP": "${{ steps.tree.outcome }}",
        "DOWNLOAD": "${{ steps.download.outcome }}",
        "APPLY": "${{ steps.apply.outcome }}",
        "CODE": "${{ steps.check.outputs.code }}",
        "LEAKS": "${{ steps.gitleaks.outcome }}",
        "CLAUDE": "${{ needs.improve.outputs.claude }}",
        "BASE": "${{ needs.improve.outputs.wiki_base }}",
    }
    # gitleaks' outcome is its verdict: it fails on a finding, and only its
    # continue-on-error keeps the job going to the gate result.
    gitleaks = _by_id(_jobs()["gates"]["steps"], "gitleaks")
    assert gitleaks["continue-on-error"] is True
    assert "--exit-code 1" in gitleaks["run"]


def test_the_gates_job_runs_only_on_a_planned_run_that_was_not_cancelled() -> None:
    # Round 2 (code review): `always()` here would run gates on a cancelled
    # run or one whose plan failed.
    assert _jobs()["gates"]["if"] == (
        "${{ !cancelled() && needs.improve.outputs.plan == 'success' }}"
    )
    assert _jobs()["improve"]["outputs"]["plan"] == "${{ steps.plan.outcome }}"


EXPR = re.compile(r"\$\{\{\s*(.*?)\s*\}\}")
NEEDS_IMPROVE = re.compile(r"needs\.improve\.([\w-]+)(?:\.([\w-]+))?")


def test_every_output_of_the_model_job_is_fixed_before_the_model_or_an_outcome() -> None:
    # A step's outputs are fixed when it ends, and an outcome is the runner's
    # record; outputs of a step after the model may be the model's.
    jobs = _jobs()
    improve = jobs["improve"]
    _, claude = _model_job(jobs)
    order = {s.get("id"): i for i, s in enumerate(improve["steps"]) if s.get("id")}
    outputs = improve["outputs"]
    for name, value in outputs.items():
        m = re.fullmatch(
            r"\$\{\{ steps\.([\w-]+)\.(outputs\.[\w-]+|outcome)(?: == 'success')? \}\}", value
        )
        assert m, (name, value)
        if m.group(2) != "outcome":
            assert order[m.group(1)] < claude, name
    # Every use of improve anywhere is one of those outputs, or its result.
    text = WORKFLOW.read_text(encoding="utf-8")
    uses = NEEDS_IMPROVE.findall(text)
    assert uses
    for kind, field in uses:
        assert (kind, field) == ("result", "") or (kind == "outputs" and field in outputs), (
            kind,
            field,
        )
    # gates needs no other job, and nothing else of improve's.
    gates_text = yaml.safe_dump(jobs["gates"])
    assert "needs.previous-report" not in gates_text


def test_publish_and_report_take_the_gated_artifacts_by_id() -> None:
    jobs = _jobs()
    gates, publish, report = jobs["gates"], jobs["publish"], jobs["report"]
    assert gates["outputs"]["report_id"] == "${{ steps.report.outputs.artifact-id }}"
    assert gates["outputs"]["pages_id"] == "${{ steps.pages.outputs.artifact-id }}"
    assert _by_id(gates["steps"], "report")["with"]["name"] == "wiki-daily-report"
    assert _by_id(gates["steps"], "pages")["with"]["name"] == "wiki-daily-pages"
    for step_id in ("report", "pages"):
        assert "overwrite" not in _by_id(gates["steps"], step_id)["with"]
    # publish: only after both jobs succeeded, on what gates found changed.
    assert set(publish["needs"]) == {"improve", "gates"}
    cond = " ".join(publish["if"].split())
    assert "needs.improve.result == 'success'" in cond
    assert "needs.gates.result == 'success'" in cond
    assert "needs.gates.outputs.changed == 'true'" in cond
    assert "needs.improve.outputs.changed" not in cond
    downloads = [s for s in publish["steps"] if _uses(s, "actions/download-artifact@")]
    assert [d["with"] for d in downloads] == [
        {"artifact-ids": "${{ needs.gates.outputs.pages_id }}", "path": "incoming"}
    ]
    # No cache in publish either: it can push.
    setup = [s for s in publish["steps"] if _uses(s, "astral-sh/setup-uv@")]
    assert [s["with"]["enable-cache"] for s in setup] == [False]
    downloads = [s for s in report["steps"] if _uses(s, "actions/download-artifact@")]
    assert [d["with"] for d in downloads] == [
        {"artifact-ids": "${{ needs.gates.outputs.report_id }}", "path": "report"}
    ]
    # Nothing downloads a gated artifact by name.
    for name, body in jobs.items():
        for step in body.get("steps", []):
            if _uses(step, "actions/download-artifact@"):
                assert step["with"].get("name") not in GATED_ARTIFACTS, name


def test_no_job_that_runs_or_follows_the_model_uses_a_cache() -> None:
    # M13-39 (security review of #236): improve restored the uv cache. A
    # model past its permissions there could write cache entries for main,
    # which the cached jobs in ci.yml restore. The model's job, the gates
    # and publish each install with setup-uv's cache off, restored or saved.
    jobs = _jobs()
    model_job, _ = _model_job(jobs)
    assert model_job == "improve"
    for name in (model_job, "gates", "publish"):
        setup = [s for s in jobs[name]["steps"] if _uses(s, "astral-sh/setup-uv@")]
        assert [s["with"]["enable-cache"] for s in setup] == [False], name
        assert "save-cache" not in setup[0]["with"], name
        assert "restore-cache" not in setup[0]["with"], name
    # No job in the workflow caches anything, by any action.
    for name, body in jobs.items():
        for step in body.get("steps", []):
            uses = str(step.get("uses", ""))
            assert not uses.startswith(("actions/cache@", "actions/cache/")), name
            if uses.startswith("astral-sh/setup-uv@"):
                assert step["with"]["enable-cache"] is False, name
            assert not any("cache" in k for k in step.get("with", {}) if k != "enable-cache"), (
                name,
                step.get("name"),
            )


def test_the_model_may_not_edit_or_write_the_runners_temp_directory() -> None:
    # Defence in depth (M13-19): the settings deny Edit and Write under
    # $RUNNER_TEMP, where $GITHUB_ENV, uv's cache and the snapshot live. The
    # step's own jq program is run to build them.
    jq = shutil.which("jq")
    if jq is None:
        pytest.skip("jq is not on PATH")
    run = _by_id(_jobs()["improve"]["steps"], "prompt")["run"]
    m = re.search(r"jq -n --arg ws \"\$ws\" --arg tmp \"/\$\{RUNNER_TEMP\}\" '(.*?)' >", run, re.S)
    assert m is not None
    done = subprocess.run(
        [jq, "-n", "--arg", "ws", "//ws", "--arg", "tmp", "//tmp/rt", m.group(1)],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    deny = json.loads(done.stdout)["permissions"]["deny"]
    for rule in ("Read(//tmp/rt/**)", "Edit(//tmp/rt/**)", "Write(//tmp/rt/**)"):
        assert rule in deny, rule


# ─── constructed: the model's BASH_ENV never reaches a gate ──────────────────


def _runner_env(
    data: dict[str, Any], job: str, index: int, writes: dict[tuple[str, int], dict[str, str]]
) -> dict[str, str]:
    """The variables the runner sets for step `index` of `job`.

    The workflow's, the job's and the step's `env`, with expressions as empty
    strings, then every `$GITHUB_ENV` write made by an earlier step of the
    same job, last, so that it wins (pessimistically).
    """
    body = data["jobs"][job]
    step = body["steps"][index]
    env: dict[str, str] = {}
    for layer in (data.get("env", {}), body.get("env", {}), step.get("env", {})):
        for key, value in layer.items():
            env[key] = EXPR.sub("", str(value))
    for (wjob, windex), lines in writes.items():
        if wjob == job and windex < index:
            env.update(lines)
    return env


def _bash() -> str:
    bash = shutil.which("bash", path="/usr/bin:/bin")
    if bash is None:
        pytest.skip("bash is not in /usr/bin or /bin")
    return bash


def _argv(data: dict[str, Any], step: dict[str, Any], script: Path) -> list[str]:
    shell = step.get("shell") or data["defaults"]["run"]["shell"]
    if shell == "bash":
        shell = DEFAULT_BASH
    tokens = shell.split()
    # Every run step here is bash; the test would need another shell's
    # start-up files otherwise.
    assert Path(tokens[0]).name == "bash", shell
    assert tokens.count("{0}") == 1
    return [_bash(), *[str(script) if t == "{0}" else t for t in tokens[1:]]]


def _start(
    data: dict[str, Any], step: dict[str, Any], env: dict[str, str], ws: Path
) -> subprocess.CompletedProcess[str]:
    """Run a step's own script as the runner would, in an invented workspace.

    PATH is an empty directory, so no tool runs: every script stops at its
    first external command, except the ones that reset PATH to the system's,
    which then run only inside `ws`. BASH_ENV is read when bash starts,
    before the first line, so how far a script gets does not matter.
    """
    script = ws / "runner" / "step.sh"
    script.write_text(EXPR.sub("", step["run"]), encoding="utf-8")
    cwd = ws / step.get("working-directory", ".")
    cwd.mkdir(parents=True, exist_ok=True)
    full = {
        "PATH": str(ws / "empty"),
        "HOME": str(ws / "runner"),
        "RUNNER_TEMP": str(ws / "runner"),
        "GITHUB_WORKSPACE": str(ws),
        "GITHUB_OUTPUT": str(ws / "runner" / "output"),
        "GITHUB_ENV": str(ws / "runner" / "env"),
        "GITHUB_STEP_SUMMARY": str(ws / "runner" / "summary"),
        **env,
    }
    return subprocess.run(
        _argv(data, step, script),
        cwd=cwd,
        env=full,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def _ws(tmp_path: Path) -> tuple[Path, Path, Path]:
    ws = tmp_path / "ws"
    for d in ("runner", "empty", "repo", "report"):
        (ws / d).mkdir(parents=True)
    marker = ws / "runner" / "bash-env-ran"
    planted = ws / "runner" / "planted.sh"
    # Builtins only: PATH holds nothing.
    planted.write_text(f"echo ran >> '{marker}'\n", encoding="utf-8")
    return ws, planted, marker


def _is_gate(step: dict[str, Any]) -> bool:
    return step.get("id") in GATE_IDS or bool(RUNS_A_GATE.search(_code(step)))


def _gate_steps(data: dict[str, Any], model_job: str) -> list[tuple[str, int]]:
    """The run steps the gates are made of, wherever they are.

    Every run step of a job other than the model's that holds a gate, and
    every gate step of the model's job (its plan step runs check_wiki before
    the model, for the model's list of gaps). Not the block-only check after
    the model: a model past its permissions can make it pass, which lets
    its pages reach the gates, as they would anyway.
    """
    found = []
    for job, body in data["jobs"].items():
        steps = body.get("steps", [])
        if job == model_job:
            found += [
                (job, i)
                for i, s in enumerate(steps)
                if "run" in s and _is_gate(s) and s.get("id") != BLOCK_ONLY
            ]
        elif any(_is_gate(s) for s in steps):
            found += [(job, i) for i, s in enumerate(steps) if "run" in s]
    return found


def test_constructed_bash_env_from_the_model_never_runs_in_the_gates(tmp_path: Path) -> None:
    data = _workflow()
    model_job, claude = _model_job(data["jobs"])
    ws, planted, marker = _ws(tmp_path)
    # The model, past its permissions, appends to its step's $GITHUB_ENV.
    lines = dict.fromkeys(MODEL_WRITES, "/nonexistent/planted")
    lines["BASH_ENV"] = lines["ENV"] = str(planted)
    writes = {(model_job, claude): lines}

    gates = _gate_steps(data, model_job)
    # The gates job's own gate steps, publish's second gates, and report,
    # which quotes the gate report, are here.
    ids = {data["jobs"][j]["steps"][i].get("id") for j, i in gates}
    assert {"apply", "check", "gitleaks", "gate"} <= ids
    assert {j for j, _ in gates} == {"improve", "gates", "publish", "report"}
    # A gate after the model in its own job would get the planted BASH_ENV
    # below and fail this test.
    for job, index in gates:
        step = data["jobs"][job]["steps"][index]
        assert "/usr/bin/sudo" not in step["run"], step["name"]  # tests never sudo
        env = _runner_env(data, job, index, writes)
        _start(data, step, env, ws)
        assert not marker.exists(), (job, step["name"])
        # Nor does any other variable the model set.
        assert not set(MODEL_WRITES) & set(env), (job, step["name"])

    # Control: the same gate step, in the model's job after the model, would
    # run the planted script. --noprofile --norc do not stop BASH_ENV; only
    # the job boundary does.
    gate = _by_id(data["jobs"]["gates"]["steps"], "gate")
    assert "--noprofile --norc" in gate["shell"]
    env = _runner_env(data, model_job, claude + 1, writes)
    assert env["BASH_ENV"] == str(planted)
    _start(data, gate, {"BASH_ENV": env["BASH_ENV"]}, ws)
    assert marker.read_text(encoding="utf-8") == "ran\n"


# ─── constructed: the gate result ────────────────────────────────────────────

ALL_PASSED = {
    "OUTSIDE": "success",
    "WITHHOLD": "success",
    "HANDOFF": "success",
    "SETUP": "success",
    "DOWNLOAD": "success",
    "APPLY": "success",
    "CODE": "0",
    "LEAKS": "success",
    "CLAUDE": "success",
}


def _gate_result(
    tmp_path: Path, env: dict[str, str]
) -> tuple[subprocess.CompletedProcess[str], Path, list[str]]:
    data = _workflow()
    ws, _, marker = _ws(tmp_path)
    (ws / "report" / "check.txt").write_text("planted report\n", encoding="utf-8")
    for name in ("output", "summary"):
        (ws / "runner" / name).touch()
    done = _start(data, _by_id(data["jobs"]["gates"]["steps"], "gate"), env, ws)
    assert not marker.exists()
    out = (ws / "runner" / "output").read_text(encoding="utf-8").split()
    return done, ws, out


# Round 1 (code review): each cause named as itself, first one first.
CAUSES = [
    ({"OUTSIDE": "failure"}, "the checkout of main changed outside the wiki while the model ran"),
    ({"OUTSIDE": "skipped"}, "the check that nothing outside the wiki changed did not finish"),
    (
        {"WITHHOLD": "failure", "HANDOFF": "skipped"},
        "improve withheld the pages: check_wiki found personal data in them",
    ),
    ({"WITHHOLD": "skipped"}, "improve could not stage the pages"),
    ({"HANDOFF": "failure"}, "improve did not hand the pages on"),
    ({"SETUP": "failure", "DOWNLOAD": "skipped"}, "this job's setup failed"),
    ({"SETUP": "skipped", "APPLY": "skipped"}, "this job's setup failed"),
    ({"DOWNLOAD": "failure", "APPLY": "skipped"}, "the pages did not reach the gate"),
    ({"APPLY": "failure"}, "the pages could not be applied"),
]


@pytest.mark.parametrize(("changes", "cause"), CAUSES, ids=[c for _, c in CAUSES])
def test_constructed_the_gate_result_names_why_the_gate_did_not_run(
    tmp_path: Path, changes: dict[str, str], cause: str
) -> None:
    # The gate's own steps were skipped: no code, no gitleaks verdict.
    env = {**ALL_PASSED, "CODE": "", "LEAKS": "skipped", **changes}
    done, ws, out = _gate_result(tmp_path, env)
    assert done.returncode == 1, done.stdout + done.stderr
    assert "::error::the gate did not run; nothing is pushed" in done.stdout
    report = (ws / "report" / "check.txt").read_text(encoding="utf-8")
    assert report.startswith(f"check_wiki did not run: {cause}")
    assert "planted" not in report
    # The fixed reason leaves as the report; never the pages.
    assert out == ["report=true"]


def _wiki_repo(ws: Path) -> str:
    wiki = ws / "wiki"
    wiki.mkdir()
    (wiki / "Home.md").write_text("# Home\n", encoding="utf-8")
    env = {"PATH": "/usr/bin:/bin", "HOME": str(ws / "runner"), "GIT_CONFIG_NOSYSTEM": "1"}
    git = shutil.which("git", path="/usr/bin:/bin")
    if git is None:
        pytest.skip("git is not in /usr/bin or /bin")
    for args in (
        ["init", "-q"],
        ["add", "-A"],
        ["-c", "user.name=t", "-c", "user.email=t@invalid", "commit", "-qm", "base"],
    ):
        subprocess.run([git, *args], cwd=wiki, env=env, check=True, capture_output=True)
    (wiki / "Home.md").write_text("# Home\n\nEdited.\n", encoding="utf-8")
    subprocess.run([git, "add", "-A"], cwd=wiki, env=env, check=True, capture_output=True)
    base = subprocess.run(
        [git, "rev-parse", "HEAD"], cwd=wiki, env=env, check=True, capture_output=True, text=True
    )
    return base.stdout.strip()


@pytest.mark.parametrize(
    ("code", "leaks", "expected", "fails"),
    [
        pytest.param("0", "success", ["report=true", "pages=true"], False, id="clean"),
        pytest.param("1", "success", ["report=true", "pages=true"], True, id="failures"),
        pytest.param("3", "success", [], True, id="personal-data"),
        pytest.param("2", "success", [], True, id="error"),
        pytest.param("0", "failure", ["report=true"], True, id="gitleaks-found-something"),
        pytest.param("3", "failure", [], True, id="both"),
    ],
)
def test_constructed_the_report_and_pages_leave_only_when_clean(
    tmp_path: Path, code: str, leaks: str, expected: list[str], fails: bool
) -> None:
    # Round 1 (code review): personal data keeps the report in; a gitleaks
    # finding keeps the pages in; and gitleaks' outcome reaches the gate.
    ws = tmp_path / "ws"
    ws.mkdir()
    base = _wiki_repo(ws)
    env = {**ALL_PASSED, "CODE": code, "LEAKS": leaks, "BASE": base}
    done, _, out = _gate_result(tmp_path, env)
    assert (done.returncode != 0) is fails, done.stdout + done.stderr
    assert [o for o in out if not o.startswith("changed=")] == expected
    assert "changed=true" in out
    if fails:
        assert "::error::the gate rejected the wiki; nothing is pushed" in done.stdout


# ─── constructed: symlinks and the staged uploads ─────────────────────────────


def _step_script(job: str, step_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    data = _workflow()
    return data, _by_id(data["jobs"][job]["steps"], step_id)


def _plain_wiki(ws: Path) -> Path:
    wiki = ws / "wiki"
    (wiki / ".git" / "hooks").mkdir(parents=True)
    (wiki / ".lab").mkdir()
    (wiki / "Home.md").write_text("# Home\n", encoding="utf-8")
    (wiki / ".lab" / "state.json").write_text("{}\n", encoding="utf-8")
    # A link inside .git is git's business, not a page.
    (wiki / ".git" / "hooks" / "link").symlink_to("/nonexistent")
    return wiki


def _link_to_repo_code(ws: Path) -> None:
    (ws / "repo" / "scripts").mkdir(parents=True, exist_ok=True)
    (ws / "repo" / "scripts" / "check_wiki.py").write_text("print('repo code')\n")
    (ws / "wiki" / "A.md").symlink_to("../repo/scripts/check_wiki.py")


TREES = {
    "plain": (lambda ws: None, 0),
    "a page linked to repository code": (_link_to_repo_code, 1),
    "a linked directory": (lambda ws: (ws / "wiki" / "sub").symlink_to(ws / "repo"), 1),
    "a dangling link in .lab": (lambda ws: (ws / "wiki" / ".lab" / "x").symlink_to("/x"), 1),
    "a fifo": (lambda ws: os.mkfifo(ws / "wiki" / "pipe.md"), 1),
}


@pytest.mark.parametrize("tree", list(TREES), ids=list(TREES))
@pytest.mark.parametrize("job", ["gates", "publish"])
def test_constructed_a_symlink_in_the_wiki_stops_the_job(
    tmp_path: Path, tree: str, job: str
) -> None:
    ws, _, marker = _ws(tmp_path)
    _plain_wiki(ws)
    plant, expected = TREES[tree]
    plant(ws)
    data, step = _step_script(job, "tree")
    done = _start(data, step, {}, ws)
    assert done.returncode == expected, done.stdout + done.stderr
    if expected:
        assert "::error::the wiki holds a symlink or another special file" in done.stdout
        # The path itself is not printed.
        assert "A.md" not in done.stdout and "check_wiki.py" not in done.stdout
    assert not marker.exists()


@pytest.mark.parametrize(("job", "dest"), [("improve", "handoff"), ("gates", "out/wiki")])
def test_constructed_the_staged_upload_holds_only_regular_top_level_pages(
    tmp_path: Path, job: str, dest: str
) -> None:
    # upload-artifact follows symlinks: a page linked to a secret would have
    # put the secret in a public artifact. Only regular files are copied.
    ws, _, _ = _ws(tmp_path)
    wiki = _plain_wiki(ws)
    secret = ws / "runner" / "environ"
    secret.write_text("ACTIONS_RUNTIME_TOKEN=planted-secret\n", encoding="utf-8")
    (wiki / "Leak.md").symlink_to(secret)
    (wiki / "sub").mkdir()
    (wiki / "sub" / "Deep.md").write_text("deep\n", encoding="utf-8")
    (wiki / "notes.txt").write_text("not a page\n", encoding="utf-8")
    if job == "gates":
        (ws / "report" / "wiki.diff").write_text("diff\n", encoding="utf-8")
    data, step = _step_script(job, "stage")
    done = _start(data, step, {}, ws)
    assert done.returncode == 0, done.stdout + done.stderr
    staged = sorted(p.relative_to(ws).as_posix() for p in (ws / dest.split("/")[0]).rglob("*"))
    pages = [p for p in staged if p.endswith(".md")]
    assert pages == [f"{dest}/Home.md"]
    for path in (ws / dest.split("/")[0]).rglob("*"):
        assert not path.is_symlink()
        if path.is_file():
            assert "planted-secret" not in path.read_text(encoding="utf-8")


@pytest.mark.parametrize(("code", "withheld"), [(0, False), (1, False), (2, True), (3, True)])
def test_constructed_the_block_only_check_withholds_on_personal_data(
    tmp_path: Path, code: int, withheld: bool
) -> None:
    # Round 1 (security): check_wiki's exit 3 (personal data) or 2 (error)
    # keeps the pages from the public hand-off artifact; 0 and 1 let them go
    # on to the gates. A stand-in `uv` exits with the code check_wiki would.
    ws, _, marker = _ws(tmp_path)
    uv = ws / "empty" / "uv"
    uv.write_text(f"#!/bin/sh\nexit {code}\n", encoding="utf-8")
    uv.chmod(0o755)
    data, step = _step_script("improve", BLOCK_ONLY)
    done = _start(data, step, {}, ws)
    assert (done.returncode == 1) is withheld, done.stdout + done.stderr
    assert done.returncode in (0, 1)
    if withheld:
        assert f"::error::check_wiki exited {code} on the edited pages" in done.stdout
    assert (ws / "runner" / "output").exists() is False
    assert not marker.exists()
