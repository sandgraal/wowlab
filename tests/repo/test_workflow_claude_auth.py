"""Every workflow that runs Claude authenticates with the owner's subscription token.

Owner decision, 2026-10-14: the Claude workflows run on the owner's Claude
subscription through the `CLAUDE_CODE_OAUTH_TOKEN` repository secret, not on
a pay-as-you-go API key (wiki-daily run 38659417983 skipped on "credit
balance too low"). These tests read `.github/workflows/*.yml` as data: no
workflow names the API-key secret, the Console workspace header, or the old
API preflight; each Claude step takes the token; each "Check secret" step
checks the token and skips cleanly without it (its script is run with bash,
with and without a token); the token is referenced only by that check and by
the Claude step, nowhere else in any workflow; and the Claude steps run on
that check alone, with no `continue-on-error`, so a usage-limit failure fails
the step rather than being skipped as a preflight result.

Round 2 (security review, 2026-10-14): each Claude job runs with
CLAUDE_CODE_SUBPROCESS_ENV_SCRUB=1 after an identical bubblewrap install and
probe, passes a settings deny list, and the two comment-driven workflows
admit only the owner's (and the action's own) comments to the prompt.

Follow-ups to #180 (security review): the PR review is read-only, the
responder skips fork PRs, every Claude workflow denies `./.git/config`, and
the only gh command any of them allows is `gh pr diff`, which has no
--jq/--template/--json to read the App token from the environment. Since
M13-59 the PR review allows no shell command at all: `gh pr diff` never ran
in its sandbox, and the workflow writes the diff to a file instead
(`tests/repo/test_workflow_claude_review_diff.py`).

A usage-limit failure in the Claude step is reported by the action as
is_error:true; `test_constructed_a_usage_limit_result_is_diagnosed` in
`tests/scripts/test_wiki_diagnose.py` covers what wiki-daily prints then.
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
WORKFLOWS = ROOT / ".github" / "workflows"
ACTION = "anthropics/claude-code-action@"
CLAUDE_WORKFLOWS = ("claude.yml", "claude-review.yml", "wiki-daily.yml")
TOKEN_INPUT = "${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}"
PRESENT = "steps.secret.outputs.present == 'true'"
# claude.yml also skips a fork's PR (security review of #180, F1). Only the
# presence check and the fork check gate a Claude step; no API preflight.
NOT_FORK = "steps.fork.outputs.cross_repo != 'true'"
GATE = {
    "claude.yml": f"{PRESENT} && {NOT_FORK}",
    "claude-review.yml": PRESENT,
    "wiki-daily.yml": PRESENT,
}


def _files() -> list[Path]:
    return sorted([*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")])


def _steps(path: Path) -> list[tuple[str, list[dict[str, Any]]]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    jobs = data["jobs"]
    assert isinstance(jobs, dict)
    return [(name, job.get("steps", [])) for name, job in jobs.items()]


def _claude_jobs(path: Path) -> list[tuple[str, list[dict[str, Any]]]]:
    return [
        (name, steps)
        for name, steps in _steps(path)
        if any(str(s.get("uses", "")).startswith(ACTION) for s in steps)
    ]


def test_the_claude_workflows_are_the_ones_that_use_the_action() -> None:
    using = {p.name for p in _files() if ACTION in p.read_text(encoding="utf-8")}
    assert using == set(CLAUDE_WORKFLOWS)


@pytest.mark.parametrize("path", _files(), ids=lambda p: p.name)
def test_no_workflow_uses_the_api_key_or_the_workspace_header(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert not re.search(r"secrets\s*\.\s*ANTHROPIC_API_KEY\b", text)
    assert "secrets['ANTHROPIC_API_KEY']" not in text
    assert 'secrets["ANTHROPIC_API_KEY"]' not in text
    assert "anthropic_api_key" not in text
    assert "ANTHROPIC_CUSTOM_HEADERS" not in text
    assert "ANTHROPIC_WORKSPACE_ID" not in text
    assert "anthropic-workspace-id" not in text
    # The old API preflight: a direct Messages API call with the key.
    assert "x-api-key" not in text
    assert "api.anthropic.com" not in text
    assert "steps.preflight" not in text


@pytest.mark.parametrize("name", CLAUDE_WORKFLOWS)
def test_every_claude_step_takes_the_subscription_token(name: str) -> None:
    jobs = _claude_jobs(WORKFLOWS / name)
    assert jobs
    for _, steps in jobs:
        for step in steps:
            if not str(step.get("uses", "")).startswith(ACTION):
                continue
            with_ = step["with"]
            assert with_["claude_code_oauth_token"] == TOKEN_INPUT
            assert "anthropic_api_key" not in with_
    # The model stays pinned by full id.
    assert "claude-opus-5-5" in (WORKFLOWS / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("name", CLAUDE_WORKFLOWS)
def test_the_secret_check_checks_the_token_and_gates_the_claude_step(name: str) -> None:
    for job, steps in _claude_jobs(WORKFLOWS / name):
        checks = [s for s in steps if s.get("id") == "secret"]
        assert len(checks) == 1, job
        check = checks[0]
        assert check["env"] == {"TOKEN": TOKEN_INPUT}
        run = check["run"]
        # Skips cleanly: the step itself never fails on a missing token.
        assert 'if [ -n "$TOKEN" ]' in run
        assert 'echo "present=false" >> "$GITHUB_OUTPUT"' in run
        assert "CLAUDE_CODE_OAUTH_TOKEN is not set; skipping" in run
        assert "exit 1" not in run
        index = steps.index(check)
        for i, step in enumerate(steps):
            if str(step.get("uses", "")).startswith(ACTION):
                assert i > index
                # The Claude step runs on the presence check (and, in
                # claude.yml, the fork check) with no preflight, and is never
                # allowed to fail quietly.
                assert step["if"] == GATE[name]
                # Absent entirely: `continue-on-error: ${{ true }}` or any
                # other expression would also let it fail quietly.
                assert "continue-on-error" not in step


# Any spelling of the secret reference: `secrets.X`, `secrets . X`,
# `secrets['X']`, `secrets[ "X" ]`. GitHub secret names are case-insensitive.
SECRET_REF = re.compile(
    r"secrets\s*(?:\.\s*CLAUDE_CODE_OAUTH_TOKEN\b"
    r"|\[\s*(['\"])\s*CLAUDE_CODE_OAUTH_TOKEN\s*\1\s*\])",
    re.IGNORECASE,
)

Where = tuple[Any, ...]


def _secret_uses(node: Any, where: Where = ()) -> list[Where]:
    """Every path in a parsed workflow whose key or string value names the secret."""
    found: list[Where] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str) and SECRET_REF.search(key):
                found.append((*where, key))
            found += _secret_uses(value, (*where, key))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            found += _secret_uses(value, (*where, i))
    elif isinstance(node, str):
        found += [where] * len(SECRET_REF.findall(node))
    return found


def _allowed_secret_uses(data: dict[str, Any]) -> list[Where]:
    allowed: list[Where] = []
    for job, body in data["jobs"].items():
        for i, step in enumerate(body.get("steps", [])):
            if step.get("id") == "secret":
                allowed.append(("jobs", job, "steps", i, "env", "TOKEN"))
            if str(step.get("uses", "")).startswith(ACTION):
                allowed.append(("jobs", job, "steps", i, "with", "claude_code_oauth_token"))
    return allowed


def test_the_secret_pattern_matches_every_spelling() -> None:
    for spelling in (
        "${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}",
        "${{secrets . CLAUDE_CODE_OAUTH_TOKEN}}",
        "${{ secrets['CLAUDE_CODE_OAUTH_TOKEN'] }}",
        '${{ secrets[ "CLAUDE_CODE_OAUTH_TOKEN" ] }}',
        "${{ SECRETS.claude_code_oauth_token }}",
    ):
        assert SECRET_REF.search(spelling), spelling
    assert not SECRET_REF.search("CLAUDE_CODE_OAUTH_TOKEN is not set; skipping")
    assert not SECRET_REF.search("${{ secrets.CLAUDE_CODE_OAUTH_TOKEN_OLD }}")


@pytest.mark.parametrize("path", _files(), ids=lambda p: p.name)
def test_the_secret_appears_only_where_the_claude_steps_need_it(path: Path) -> None:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    uses = _secret_uses(data)
    if path.name not in CLAUDE_WORKFLOWS:
        assert uses == []
        return
    # Exactly two: the presence check's env.TOKEN and the action's input.
    # Not in workflow-level or job-level env, and not in any other step.
    allowed = _allowed_secret_uses(data)
    assert len(allowed) == 2
    assert sorted(uses, key=repr) == sorted(allowed, key=repr)


def _secret_check_scripts() -> list[tuple[str, str]]:
    scripts = []
    for name in CLAUDE_WORKFLOWS:
        for _, steps in _claude_jobs(WORKFLOWS / name):
            for step in steps:
                if step.get("id") == "secret":
                    scripts.append((name, step["run"]))
    return scripts


def _run_check(script: str, token: str, tmp_path: Path) -> tuple[int, str]:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is not on PATH")
    out = tmp_path / f"github_output_{bool(token)}"
    out.write_text("", encoding="utf-8")
    env = {"PATH": os.environ.get("PATH", ""), "TOKEN": token, "GITHUB_OUTPUT": str(out)}
    # The same shell GitHub uses for `shell: bash`.
    done = subprocess.run(
        [bash, "--noprofile", "--norc", "-eo", "pipefail", "-c", script],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    return done.returncode, out.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("name", "script"), [pytest.param(n, s, id=n) for n, s in _secret_check_scripts()]
)
def test_the_secret_check_really_skips_without_the_token(
    name: str, script: str, tmp_path: Path
) -> None:
    code, output = _run_check(script, "", tmp_path)
    assert code == 0, name
    assert output.split() == ["present=false"], name
    code, output = _run_check(script, "dummy-not-a-token", tmp_path)
    assert code == 0, name
    assert output.split() == ["present=true"], name


def test_every_claude_workflow_has_a_secret_check_script() -> None:
    assert sorted(name for name, _ in _secret_check_scripts()) == sorted(CLAUDE_WORKFLOWS)


# Round 2 (security review of PR #174): without the scrub, every command the
# model runs inherits CLAUDE_CODE_OAUTH_TOKEN, and `gh ... --jq '$ENV...'`
# prints it. The scrub needs bubblewrap, which the runner image lacks.
BWRAP_PROBE = "bwrap --ro-bind / / --dev /dev --proc /proc true"
# Follow-up to #180 (security review): `./.git/config` is where the action
# writes its App token when use_commit_signing is on (claude.yml and
# claude-review.yml); every Claude workflow denies it.
CORE_DENY = (
    "Read(//proc/**)",
    "Read(~/.claude/**)",
    "Read(~/.claude.json)",
    "Read(./.git/config)",
)


def _bubblewrap_runs() -> list[str]:
    runs = []
    for name in CLAUDE_WORKFLOWS:
        for _, steps in _claude_jobs(WORKFLOWS / name):
            for step in steps:
                if re.search(r"apt-get install\b[^\n]*\bbubblewrap\b", step.get("run", "")):
                    runs.append(step["run"])
    return runs


@pytest.mark.parametrize("name", CLAUDE_WORKFLOWS)
def test_the_env_scrub_is_on_with_bubblewrap_before_every_claude_step(name: str) -> None:
    path = WORKFLOWS / name
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    for job, steps in _claude_jobs(path):
        env = data["jobs"][job].get("env", {})
        assert env.get("CLAUDE_CODE_SUBPROCESS_ENV_SCRUB") == "1", job
        claude = [i for i, s in enumerate(steps) if str(s.get("uses", "")).startswith(ACTION)]
        for i in claude:
            # Nothing on the step turns the scrub back off.
            assert "CLAUDE_CODE_SUBPROCESS_ENV_SCRUB" not in steps[i].get("env", {})
            installs = [
                j
                for j, s in enumerate(steps[:i])
                if re.search(r"apt-get install\b[^\n]*\bbubblewrap\b", s.get("run", ""))
            ]
            assert len(installs) == 1, job
            install = steps[installs[0]]
            # Runs whenever the Claude step does, cannot be skipped past, and
            # probes bwrap as an unprivileged user after installing it.
            assert install.get("if") == steps[i].get("if")
            assert install.get("continue-on-error") is not True
            run = install["run"]
            assert run.index("apt-get install") < run.index(BWRAP_PROBE)
    assert not re.search(r"CLAUDE_CODE_SUBPROCESS_ENV_SCRUB\W+0", path.read_text("utf-8"))


def test_the_three_bubblewrap_steps_are_identical() -> None:
    runs = _bubblewrap_runs()
    assert len(runs) == len(CLAUDE_WORKFLOWS)
    assert len(set(runs)) == 1


@pytest.mark.parametrize("name", CLAUDE_WORKFLOWS)
def test_every_claude_step_gets_a_deny_list(name: str) -> None:
    path = WORKFLOWS / name
    text = path.read_text(encoding="utf-8")
    for entry in CORE_DENY:
        assert f'"{entry}"' in text, entry
    assert '"disableBypassPermissionsMode": "disable"' in text or (
        'disableBypassPermissionsMode: "disable"' in text
    )
    # The runner's temp directory: an expression in the two inline settings,
    # a jq variable in wiki-daily's generated file.
    assert '"Read(/${{ runner.temp }}/**)"' in text or '"Read(\\($tmp)/**)"' in text
    for _, steps in _claude_jobs(path):
        for step in steps:
            if str(step.get("uses", "")).startswith(ACTION):
                assert step["with"].get("settings")


@pytest.mark.parametrize("name", ["claude.yml", "claude-review.yml"])
def test_only_the_owners_comments_reach_the_prompt(name: str) -> None:
    # Tag mode puts the PR's or issue's comments in the prompt; anyone can
    # comment on a draft PR. The pinned action filters by login.
    for _, steps in _claude_jobs(WORKFLOWS / name):
        for step in steps:
            if str(step.get("uses", "")).startswith(ACTION):
                actors = step["with"]["include_comments_by_actor"]
                assert actors == "${{ github.repository_owner }},claude[bot]"


def test_the_responder_commits_only_through_the_actions_file_tools() -> None:
    for _, steps in _claude_jobs(WORKFLOWS / "claude.yml"):
        for step in steps:
            if str(step.get("uses", "")).startswith(ACTION):
                assert step["with"]["use_commit_signing"] is True


@pytest.mark.parametrize("name", ["claude.yml", "claude-review.yml"])
def test_the_inline_settings_are_valid_json(name: str) -> None:
    for _, steps in _claude_jobs(WORKFLOWS / name):
        for step in steps:
            if str(step.get("uses", "")).startswith(ACTION):
                raw = step["with"]["settings"].replace("${{ runner.temp }}", "/tmp/x")
                deny = json.loads(raw)["permissions"]["deny"]
                assert set(CORE_DENY) <= set(deny)
                assert "Read(//tmp/x/**)" in deny


# Follow-up to #180 (security review): the PR review is read-only. Tag mode
# (track_progress) grants acceptEdits and a commit path backed by the
# action's App token; use_commit_signing swaps shell git for the action's
# file tools, and the deny list removes those and every edit tool. The
# checkout's .git/config, where the action writes its token, is unreadable
# (CORE_DENY, in every Claude workflow).
REVIEW_DENY = (
    "Edit",
    "Write",
    "MultiEdit",
    "NotebookEdit",
    "mcp__github_file_ops__commit_files",
    "mcp__github_file_ops__delete_files",
)


def _action_step(name: str) -> dict[str, Any]:
    found = [
        step
        for _, steps in _claude_jobs(WORKFLOWS / name)
        for step in steps
        if str(step.get("uses", "")).startswith(ACTION)
    ]
    assert len(found) == 1
    return found[0]


def test_the_review_cannot_edit_commit_or_push() -> None:
    with_ = _action_step("claude-review.yml")["with"]
    assert with_["use_commit_signing"] is True
    raw = with_["settings"].replace("${{ runner.temp }}", "/tmp/x")
    deny = json.loads(raw)["permissions"]["deny"]
    for entry in (*REVIEW_DENY, *CORE_DENY):
        assert entry in deny, entry
    allowed = re.search(r'--allowedTools "([^"]*)"', with_["claude_args"])
    assert allowed
    # Nothing that writes: no edit tool, no shell git, no file-ops tool.
    for tool in allowed.group(1).split(","):
        assert not tool.startswith(("Edit", "Write", "MultiEdit", "NotebookEdit")), tool
        assert not tool.startswith("Bash(git"), tool
        assert "file_ops" not in tool, tool


def test_the_review_cannot_read_comments_around_the_actor_filter() -> None:
    # F2: `gh pr view --comments` would print every comment, around
    # include_comments_by_actor. Since M13-59 the review runs no shell
    # command at all: its diff is .pr-review/pr.diff, written before the
    # action (test_workflow_claude_review_diff.py), and CI results come from
    # the action's get_ci_status and get_workflow_run_details tools. The
    # scrub and bubblewrap stay (test_the_env_scrub_is_on_with_bubblewrap_
    # before_every_claude_step), so a command added back cannot read the
    # token.
    with_ = _action_step("claude-review.yml")["with"]
    args = with_["claude_args"]
    assert "gh pr view" not in args
    assert "gh pr checks" not in args
    # M13-59: no shell at all; the diff is a file the workflow wrote.
    assert not [
        tool
        for tool in _allowed_tools(_action_step("claude-review.yml"))
        if tool.startswith("Bash")
    ]
    assert "mcp__github_ci__get_ci_status" in args
    assert "mcp__github_ci__get_workflow_run_details" in args
    # The prompt does not send the model to a command it no longer has.
    assert "gh pr view" not in with_["prompt"]
    assert "gh pr checks" not in with_["prompt"]
    assert "mcp__github_ci__get_ci_status" in with_["prompt"]


# Follow-up to #180 (security review): gh's --jq and --template read
# environment variables (`--jq '$ENV.GH_TOKEN'`), and the env scrub keeps
# GitHub tokens, the action's App token (contents: write) among them, so a
# gh command that takes --jq, --template or --json would print it. `gh pr
# diff` takes none of them (gh 2.97.0 answers "unknown flag" to each); it is
# the only gh command any Claude workflow allows.
GH_ALLOWED = {"Bash(gh pr diff:*)"}
ALLOW_FLAG = re.compile(r"--allowed-?tools\b", re.IGNORECASE)


def _allowed_tools(step: dict[str, Any]) -> list[str]:
    args = str(step["with"].get("claude_args", ""))
    found = re.findall(r'--allowed-?tools[ =]"([^"]*)"', args, re.IGNORECASE)
    # Every allow flag has the quoted form, so none escapes the scan.
    assert len(found) == len(ALLOW_FLAG.findall(args))
    tools = [tool.strip() for group in found for tool in group.split(",")]
    settings = str(step["with"].get("settings", ""))
    if settings.lstrip().startswith("{"):
        raw = settings.replace("${{ runner.temp }}", "/tmp/x")
        tools += json.loads(raw)["permissions"].get("allow", [])
    return tools


def _gh_violations(tools: list[str]) -> list[str]:
    bad = []
    for tool in tools:
        # A bare Bash, or a wildcard, would allow every gh command.
        if tool in ("Bash", "Bash(*)", "Bash(:*)"):
            bad.append(tool)
            continue
        command = re.fullmatch(r"Bash\((.*)\)", tool)
        # Also a gh run behind an environment assignment (`X=1 gh ...`).
        runs_gh = bool(command and re.match(r"(?:\S+=\S*\s+)*gh\b", command.group(1)))
        if runs_gh and tool not in GH_ALLOWED:
            bad.append(tool)
    return bad


@pytest.mark.parametrize("name", CLAUDE_WORKFLOWS)
def test_no_claude_workflow_allows_a_gh_command_that_reads_the_environment(name: str) -> None:
    text = (WORKFLOWS / name).read_text(encoding="utf-8")
    # No settings allow list anywhere (wiki-daily builds its settings with
    # jq, so it is checked as text); --allowedTools is the only allow path.
    assert not re.search(r"""(^|[\s"{,])allow"?\s*:""", text, re.MULTILINE)
    tools = _allowed_tools(_action_step(name))
    assert tools
    assert _gh_violations(tools) == []


def test_constructed_the_gh_scan_catches_a_command_that_reads_the_environment() -> None:
    # Constructed: the scan flags `gh pr checks`, any other gh command, a
    # bare Bash, and a gh behind an environment assignment.
    def flagged(allowed: str) -> bool:
        step = {"with": {"claude_args": f'--allowedTools "Read,{allowed}"'}}
        return bool(_gh_violations(_allowed_tools(step)))

    assert flagged("Bash(gh pr checks:*)")
    assert flagged("Bash(gh api:*)")
    assert flagged("Bash")
    assert flagged("Bash(GH_PAGER=cat gh pr view:*)")
    assert not flagged("Bash(gh pr diff:*)")
    assert not flagged("Bash(uv run python repo/scripts/check_wiki.py wiki)")


# F1: claude.yml does not run on a fork's PR.
def _responder_steps() -> list[dict[str, Any]]:
    jobs = _claude_jobs(WORKFLOWS / "claude.yml")
    assert len(jobs) == 1
    return jobs[0][1]


def test_the_responder_checks_for_a_fork_before_anything_runs() -> None:
    steps = _responder_steps()
    ids = [s.get("id") for s in steps]
    fork = ids.index("fork")
    # Nothing before it but the secret check.
    assert ids[:fork] == ["secret"]
    check = steps[fork]
    # Asked only on PR events: a review comment, or a comment on an issue
    # that is a PR. A plain issue skips the step.
    cond = " ".join(check["if"].split())
    assert cond.startswith(f"{PRESENT} && (")
    assert "github.event_name == 'pull_request_review_comment'" in cond
    assert "(github.event_name == 'issue_comment' && github.event.issue.pull_request)" in cond
    assert "github.event_name == 'issues'" not in cond
    assert check["env"]["GH_TOKEN"] == "${{ github.token }}"
    number = "${{ github.event.pull_request.number || github.event.issue.number }}"
    assert check["env"]["N"] == number
    assert "--json isCrossRepository --jq .isCrossRepository" in check["run"]
    # Everything after it, checkout included, waits on it. A skipped check
    # leaves the output empty, and `!= 'true'` lets a plain issue through.
    for step in steps[fork + 1 :]:
        assert step["if"] == GATE["claude.yml"], step.get("name", step.get("uses"))


def _run_fork_check(tmp_path: Path, gh_body: str) -> tuple[int, str]:
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is not on PATH")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(f"#!{bash}\n{gh_body}\n", encoding="utf-8")
    gh.chmod(0o755)
    out = tmp_path / "github_output"
    out.write_text("", encoding="utf-8")
    script = next(s for s in _responder_steps() if s.get("id") == "fork")["run"]
    env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        "N": "7",
        "GITHUB_REPOSITORY": "owner/repo",
        "GITHUB_OUTPUT": str(out),
    }
    # The same shell GitHub uses for `shell: bash`.
    done = subprocess.run(
        [bash, "--noprofile", "--norc", "-eo", "pipefail", "-c", script],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    return done.returncode, out.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("gh_body", "ok", "output"),
    [
        pytest.param("echo true", True, ["cross_repo=true"], id="fork"),
        pytest.param("echo false", True, ["cross_repo=false"], id="same-repo"),
        pytest.param("echo maybe", False, [], id="unexpected-answer"),
        pytest.param("echo", False, [], id="empty-answer"),
        pytest.param("exit 1", False, [], id="gh-fails"),
    ],
)
def test_constructed_the_fork_check_fails_closed(
    tmp_path: Path, gh_body: str, ok: bool, output: list[str]
) -> None:
    # Constructed: a stand-in `gh` on PATH answers; no network.
    code, written = _run_fork_check(tmp_path, gh_body)
    assert (code == 0) is ok
    assert written.split() == output
