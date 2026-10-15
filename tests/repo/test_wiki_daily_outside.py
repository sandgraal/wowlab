"""The wiki-daily check that nothing outside the wiki changed sees ignored paths (M13-16).

Found in the M12-14 security review of #172: the check compared
`git status --porcelain`, which does not list ignored paths such as `.venv/`
and `__pycache__/`, so a planted `.pth` or `.pyc` would run on the next
`uv run` undetected. The improve job now snapshots every path under `repo/`
(a sha256 per regular file, symlink targets, directories, executable bits,
and .git's config and hooks) before the model and compares it straight after,
and every later step that runs repository code depends on that comparison.
Round 2 (security review): post steps run whatever the check decided, so the
job's last step removes repo/.git and repo/.venv (actions/checkout's post
then runs no git in repo/), and improve never saves the uv cache that
publish restores.

Static tests read `.github/workflows/wiki-daily.yml` as data. The
`constructed` tests run the workflow's own step scripts with bash on an
invented checkout in `tmp_path`, plant a file, and check the verdict; the
planted executables write a marker file if anything runs them.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import stat
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "wiki-daily.yml"
SHELL = "/usr/bin/bash --noprofile --norc -eo pipefail {0}"
SYSTEM_PATH = "export PATH=/usr/sbin:/usr/bin:/sbin:/bin"
SUDO = "/usr/bin/sudo"
OUTSIDE_OK = "steps.outside.outcome == 'success'"
# What runs code from the checkout: uv, a python interpreter, a repo script.
RUNS_REPO_CODE = re.compile(r"\buv (?:run|sync)\b|\bpython3?\b|scripts/\w+\.py")
# Tools the step scripts call; each planted copy writes a marker if run.
PLANTED_TOOLS = [
    "bash",
    "cat",
    "cmp",
    "cut",
    "diff",
    "find",
    "git",
    "grep",
    "head",
    "readlink",
    "rm",
    "sed",
    "sh",
    "sha256sum",
    "sort",
    "tr",
    "wc",
    "xargs",
]


def _steps() -> list[dict[str, Any]]:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    steps = data["jobs"]["improve"]["steps"]
    assert isinstance(steps, list)
    return steps


def _index(steps: list[dict[str, Any]], step_id: str) -> int:
    return next(i for i, s in enumerate(steps) if s.get("id") == step_id)


def _step(step_id: str) -> dict[str, Any]:
    steps = _steps()
    return steps[_index(steps, step_id)]


def _conjuncts(cond: str) -> list[str]:
    cond = " ".join(cond.split())
    if cond.startswith("${{") and cond.endswith("}}"):
        cond = cond[3:-2].strip()
    assert "||" not in cond, cond
    return [c.strip() for c in cond.split("&&")]


def _runs_repo_code(step: dict[str, Any]) -> bool:
    code = [ln for ln in step.get("run", "").splitlines() if not ln.lstrip().startswith("#")]
    return bool(RUNS_REPO_CODE.search("\n".join(code)))


def _snapshot_function(step: dict[str, Any]) -> str:
    m = re.search(r"^snapshot\(\) \{\n.*?^\}\n", step["run"], re.S | re.M)
    assert m is not None, step["name"]
    return m.group(0)


# ─── static: the comparison ───────────────────────────────────────────────────


def test_the_snapshot_function_is_the_same_before_and_after() -> None:
    before = _snapshot_function(_step("before"))
    assert before == _snapshot_function(_step("outside"))
    # Every path under the checkout, ignored ones included: only git's own
    # object store and index are left out, and files are compared by content.
    pruned = re.findall(r"-path (\S+)", before)
    assert pruned == ["./.git/objects", "./.git/index"]
    assert "xargs -0 -r sha256sum --" in before
    assert "readlink" in before and "-perm -100" in before
    assert "-mtime" not in before and "-newer" not in before


def test_the_snapshot_is_taken_after_the_last_repository_code_before_the_model() -> None:
    steps = _steps()
    before, claude = _index(steps, "before"), _index(steps, "claude")
    assert before < claude
    # Every step that ran repository code before the model ran before the
    # snapshot, and nothing between the snapshot and the model does.
    for i, step in enumerate(steps[:claude]):
        if _runs_repo_code(step):
            assert i < before, step["name"]
    step = steps[before]
    assert step["if"] == "steps.plan.outcome == 'success'"
    assert step.get("continue-on-error") is not True
    assert step["shell"] == SHELL
    run = step["run"]
    assert run.splitlines()[0] == SYSTEM_PATH
    assert not _runs_repo_code(step)
    # Both hashes leave as step outputs, which the model cannot rewrite.
    assert 'echo "tree=$(sha256sum <' in run
    assert 'echo "status=$(sha256sum <' in run


def test_the_check_follows_the_model_directly_and_runs_nothing_from_repo() -> None:
    steps = _steps()
    claude = _index(steps, "claude")
    step = steps[claude + 1]
    assert step["id"] == "outside"
    assert step["name"] == "Nothing outside the wiki changed"
    assert step["if"] == "always() && steps.plan.outcome == 'success'"
    assert step.get("continue-on-error") is not True
    # An absolute shell, then the system PATH before any command: the venv's
    # bin directory is on the job's PATH from the prompt step on.
    assert step["shell"] == SHELL
    assert step["env"] == {
        "TREE": "${{ steps.before.outputs.tree }}",
        "STATUS": "${{ steps.before.outputs.status }}",
    }
    run = step["run"]
    assert run.splitlines()[0] == SYSTEM_PATH
    assert not _runs_repo_code(step)
    # The tree is compared first, against the step output; git runs only
    # after that, with no global or system config and no fsmonitor.
    tree = run.index('if [ -z "$TREE" ]')
    assert run.index("snapshot repo >") < tree < run.index("status repo >")
    assert "GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null git" in run
    assert "-c core.fsmonitor=false status --porcelain" in run


def test_the_gate_command_writes_no_bytecode() -> None:
    steps = _steps()
    prompt = _index(steps, "prompt")
    assert prompt < _index(steps, "claude")
    assert 'echo "PYTHONDONTWRITEBYTECODE=1"' in steps[prompt]["run"]


# ─── static: the step order ───────────────────────────────────────────────────


def test_every_later_step_that_runs_code_depends_on_the_check() -> None:
    steps = _steps()
    outside = _index(steps, "outside")
    later = steps[outside + 1 :]
    assert any(_runs_repo_code(s) for s in later)
    for step in later:
        if step.get("id") in ("gate", "leave"):
            continue  # each pinned on its own below
        if "run" in step:
            assert OUTSIDE_OK in _conjuncts(step["if"]), step["name"]
        else:
            # Artifact uploads only, gated on what the gate result decided.
            assert step["uses"].startswith("actions/upload-artifact@"), step["name"]
            assert "steps.gate.outputs." in step["if"], step["name"]
    # Nothing between the model and the check.
    assert steps[outside - 1]["id"] == "claude"


def test_the_gate_result_branches_on_the_check_before_running_anything() -> None:
    step = _step("gate")
    assert step["if"] == "always() && steps.plan.outcome == 'success'"
    assert step["shell"] == SHELL
    assert step["env"]["OUTSIDE"] == "${{ steps.outside.outcome }}"
    run = step["run"]
    lines = run.splitlines()
    assert lines[0] == SYSTEM_PATH
    assert lines[1] == 'if [ "$OUTSIDE" != success ]; then'
    branch = run[: run.index("\nfi\n")]
    assert "git" not in branch.replace("check_wiki", "") and "cat " not in branch
    assert "exit 1" in branch


def test_the_last_step_leaves_nothing_in_repo_for_the_post_steps() -> None:
    # Round 2 (security review): actions/checkout's post has no post-if and
    # runs git in repo/ through PATH, which has repo/.venv/bin; git reads
    # repo/.git/config. It returns early when repo/.git/config is gone.
    steps = _steps()
    last = steps[-1]
    assert last["id"] == "leave"
    assert last["if"] == "always()"
    assert last.get("continue-on-error") is not True
    assert last["shell"] == SHELL
    # Round 3: as root, so a planted mode-0500 directory keeps nothing,
    # then a loud check that both are gone (a symlink counts as present).
    assert last["run"].splitlines() == [
        SYSTEM_PATH,
        f"{SUDO} /usr/bin/rm -rf -- repo/.git repo/.venv",
        "[ ! -e repo/.git ] && [ ! -L repo/.git ] && [ ! -e repo/.venv ] && [ ! -L repo/.venv ]",
    ]
    # The checkout whose post that is lives in repo/.
    checkouts = [s for s in steps if s.get("uses", "").startswith("actions/checkout@")]
    assert [s["with"]["path"] for s in checkouts] == ["repo"]


def test_nothing_in_improve_is_added_to_path() -> None:
    # Round 3: repo/.venv/bin on PATH made every later step, post steps
    # included, look its tools up there first. `uv run` finds the venv
    # through UV_PROJECT.
    for step in _steps():
        run = step.get("run", "")
        assert "GITHUB_PATH" not in run, step.get("name")
        code = [ln for ln in run.splitlines() if not ln.lstrip().startswith("#")]
        assert not any(".venv/bin" in ln for ln in code), step.get("name")
    assert 'echo "UV_PROJECT=${GITHUB_WORKSPACE}/repo"' in _step("prompt")["run"]


def test_improve_restores_the_uv_cache_but_never_saves_it() -> None:
    # Round 2 (security review): a cache saved by improve after the model ran
    # would be restored in publish, which has contents: write, before its sync.
    setups = [s for s in _steps() if s.get("uses", "").startswith("astral-sh/setup-uv@")]
    assert len(setups) == 1
    assert setups[0]["with"]["save-cache"] in (False, "false")


def test_publish_runs_only_after_improve_succeeded_and_from_its_own_checkout() -> None:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    publish = data["jobs"]["publish"]
    # A failed check fails improve (no continue-on-error on it), and publish
    # needs improve's success; it rebuilds from a fresh checkout and sync on
    # its own runner, and takes only page text from improve.
    assert "needs.improve.result == 'success'" in " ".join(publish["if"].split())
    uses = [s.get("uses", "") for s in publish["steps"]]
    assert uses[0].startswith("actions/checkout@")
    first = next(s for s in publish["steps"] if _runs_repo_code(s))
    assert first["run"] == "uv sync --frozen"


# ─── constructed: the step scripts, run ───────────────────────────────────────


def _tool(name: str) -> str:
    found = shutil.which(name, path="/usr/sbin:/usr/bin:/sbin:/bin")
    if found is None:
        pytest.skip(f"{name} is not in the system PATH")
    return found


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        [_tool("git"), *args], cwd=cwd, check=True, capture_output=True, env=_git_env(cwd)
    )


def _git_env(home: Path) -> dict[str, str]:
    return {"PATH": "/usr/bin:/bin", "HOME": str(home), "GIT_CONFIG_NOSYSTEM": "1"}


def _workspace(tmp_path: Path) -> Path:
    """An invented checkout of main with a venv and bytecode, as on the runner."""
    ws = tmp_path / "ws"
    repo = ws / "repo"
    (repo / "scripts").mkdir(parents=True)
    (repo / "scripts" / "tool.py").write_text("print('tool')\n", encoding="utf-8")
    (repo / ".gitignore").write_text(".venv/\n__pycache__/\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@invalid", "commit", "-qm", "base")
    site = repo / ".venv" / "lib" / "python3.12" / "site-packages"
    (site / "pkg").mkdir(parents=True)
    (site / "pkg" / "__init__.py").write_text("x = 1\n", encoding="utf-8")
    (site / "_pkg.pth").write_text(str(site) + "\n", encoding="utf-8")
    (repo / ".venv" / "bin").mkdir()
    (repo / ".venv" / "bin" / "python").symlink_to("/usr/bin/python3")
    cache = repo / "scripts" / "__pycache__"
    cache.mkdir()
    (cache / "tool.cpython-312.pyc").write_bytes(b"\x00" * 64)
    for d in ("runner", "report", "wiki", "planted"):
        (ws / d).mkdir()
    # Outside the checkout and first on PATH: proves the scripts reset PATH
    # without changing what they compare.
    _plant_tools(ws / "planted", _marker(ws))
    return ws


def _run(
    ws: Path, step_id: str, env: dict[str, str], sudo: Path | None = None
) -> subprocess.CompletedProcess[str]:
    bash = shutil.which("bash", path="/usr/bin:/bin")
    if bash is None:
        pytest.skip("bash is not in /usr/bin or /bin")
    _tool("sha256sum")
    _tool("git")
    for name in ("output", "summary"):
        (ws / "runner" / name).touch()
    full = {
        # Pessimistically, the venv's bin directory early on PATH (the job
        # no longer adds it) and planted tools ahead of it.
        "PATH": os.pathsep.join(
            [str(ws / "planted"), str(ws / "repo" / ".venv" / "bin"), os.environ.get("PATH", "")]
        ),
        "HOME": str(ws / "runner"),
        "RUNNER_TEMP": str(ws / "runner"),
        "GITHUB_OUTPUT": str(ws / "runner" / "output"),
        "GITHUB_STEP_SUMMARY": str(ws / "runner" / "summary"),
        **env,
    }
    script = _step(step_id)["run"]
    if sudo is not None:
        # Tests never use real sudo: a stub stands in for /usr/bin/sudo.
        assert script.count(SUDO) == 1
        script = script.replace(SUDO, shlex.quote(str(sudo)))
    # The same flags as the step's `shell:`.
    done = subprocess.run(
        [bash, "--noprofile", "--norc", "-eo", "pipefail", "-c", script],
        cwd=ws,
        env=full,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    # No step script ever runs a planted tool, whatever it finds.
    marker = _marker(ws)
    assert not marker.exists(), marker.read_text(encoding="utf-8")
    return done


def _before(ws: Path) -> dict[str, str]:
    done = _run(ws, "before", {})
    assert done.returncode == 0, done.stderr
    out = (ws / "runner" / "output").read_text(encoding="utf-8").split()
    values = dict(line.split("=", 1) for line in out)
    assert set(values) == {"tree", "status"}
    assert all(re.fullmatch(r"[0-9a-f]{64}", v) for v in values.values())
    return {"TREE": values["tree"], "STATUS": values["status"]}


def _marker(ws: Path) -> Path:
    return ws / "runner" / "planted-ran"


def _plant_tools(bin_dir: Path, marker: Path) -> None:
    """Every tool the scripts use, planted in `bin_dir`; each writes `marker` if run."""
    for name in PLANTED_TOOLS:
        path = bin_dir / name
        path.write_text(f"#!/bin/sh\necho {name} >> '{marker}'\n", encoding="utf-8")
        path.chmod(0o755)


def _site(ws: Path) -> Path:
    return ws / "repo" / ".venv" / "lib" / "python3.12" / "site-packages"


def _pyc(ws: Path) -> Path:
    return ws / "repo" / "scripts" / "__pycache__" / "tool.cpython-312.pyc"


def _same_size_same_mtime(path: Path) -> None:
    st = path.stat()
    path.write_bytes(b"\x01" * st.st_size)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))


def _chmod_x(path: Path) -> None:
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _retarget(path: Path) -> None:
    path.unlink()
    path.symlink_to("/tmp/elsewhere/python3")


PLANTS: dict[str, Callable[[Path], object]] = {
    "a .pth in site-packages": lambda ws: (_site(ws) / "zz.pth").write_text("import os\n"),
    "a .pyc in a new __pycache__": lambda ws: (
        (_site(ws) / "pkg" / "__pycache__").mkdir(),
        (_site(ws) / "pkg" / "__pycache__" / "__init__.cpython-312.pyc").write_bytes(b"\0" * 16),
    ),
    "a .pyc rewritten, same size and mtime": lambda ws: _same_size_same_mtime(_pyc(ws)),
    "a site-packages module rewritten, same size and mtime": lambda ws: _same_size_same_mtime(
        _site(ws) / "pkg" / "__init__.py"
    ),
    "a tool in the venv's bin": lambda ws: _plant_tools(ws / "repo" / ".venv" / "bin", _marker(ws)),
    "the venv's python retargeted": lambda ws: _retarget(ws / "repo" / ".venv" / "bin" / "python"),
    "an executable bit": lambda ws: _chmod_x(_site(ws) / "pkg" / "__init__.py"),
    "an empty directory": lambda ws: (_site(ws) / "evil").mkdir(),
    "a git hook": lambda ws: (ws / "repo" / ".git" / "hooks" / "pre-commit").write_text("x\n"),
    "a tracked file": lambda ws: (ws / "repo" / "scripts" / "tool.py").write_text("print(2)\n"),
    "an untracked file": lambda ws: (ws / "repo" / "new.py").write_text("x\n"),
}


@pytest.mark.parametrize("plant", list(PLANTS), ids=list(PLANTS))
def test_constructed_a_planted_path_fails_the_check(tmp_path: Path, plant: str) -> None:
    ws = _workspace(tmp_path)
    expected = _before(ws)
    PLANTS[plant](ws)
    done = _run(ws, "outside", expected)
    assert done.returncode == 1, done.stdout + done.stderr
    assert "::error::the checkout of main changed during the run (a file under repo/" in (
        done.stdout
    )
    # Nothing planted ran, not even while the check looked at it.
    assert not _marker(ws).exists()


def test_constructed_a_git_config_hook_is_caught_before_git_runs(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    expected = _before(ws)
    marker = ws / "runner" / "fsmonitor-ran"
    hook = ws / "runner" / "fsmonitor.sh"
    hook.write_text(f"#!/bin/sh\ntouch '{marker}'\n", encoding="utf-8")
    hook.chmod(0o755)
    with (ws / "repo" / ".git" / "config").open("a", encoding="utf-8") as f:
        f.write(f"[core]\n\tfsmonitor = {hook}\n")
    done = _run(ws, "outside", expected)
    assert done.returncode == 1
    assert "(a file under repo/, ignored paths included)" in done.stdout
    assert not marker.exists()


def test_constructed_a_staged_change_fails_on_git_status(tmp_path: Path) -> None:
    # The index is not in the listing (git rewrites it); git status sees it.
    ws = _workspace(tmp_path)
    expected = _before(ws)
    _git(ws / "repo", "rm", "-q", "--cached", "scripts/tool.py")
    done = _run(ws, "outside", expected)
    assert done.returncode == 1
    assert "::error::the checkout of main changed during the run (git status)" in done.stdout


def test_constructed_an_unchanged_checkout_passes(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    expected = _before(ws)
    # Times are not content: a touched file, and the index refresh that a
    # touched tracked file causes on `git status`, change nothing.
    for path in (_pyc(ws), ws / "repo" / "scripts" / "tool.py"):
        os.utime(path, (1_000_000_000, 1_000_000_000))
    done = _run(ws, "outside", expected)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "repo/ is as it was before the model ran" in done.stdout


@pytest.mark.parametrize("missing", ["TREE", "STATUS"])
def test_constructed_no_snapshot_fails_closed(tmp_path: Path, missing: str) -> None:
    ws = _workspace(tmp_path)
    expected = _before(ws)
    expected[missing] = ""
    done = _run(ws, "outside", expected)
    assert done.returncode == 1


@pytest.mark.parametrize("outcome", ["failure", "cancelled", ""])
def test_constructed_the_gate_result_reports_a_changed_checkout_and_runs_nothing(
    tmp_path: Path, outcome: str
) -> None:
    ws = _workspace(tmp_path)
    _plant_tools(ws / "repo" / ".venv" / "bin", _marker(ws))
    (ws / "report" / "check.txt").write_text("planted report\n", encoding="utf-8")
    done = _run(
        ws, "gate", {"OUTSIDE": outcome, "CODE": "", "LEAKS": "skipped", "CLAUDE": "success"}
    )
    assert done.returncode == 1
    assert "::error::the checkout of main changed outside the wiki" in done.stdout
    report = (ws / "report" / "check.txt").read_text(encoding="utf-8")
    assert report.startswith("check_wiki did not run: the checkout of main changed")
    assert "planted" not in report
    # The reason leaves as the gate report (the issue quotes it); no pages.
    assert (ws / "runner" / "output").read_text(encoding="utf-8").split() == ["report=true"]
    assert "the gate did not run" in (ws / "runner" / "summary").read_text(encoding="utf-8")
    assert not _marker(ws).exists()


# Stand-ins for /usr/bin/sudo (tests never use real sudo). `root` acts like
# root, for which directory modes do not matter, by giving the owner rwx
# first; `user` runs the command as given; `noop` runs nothing. On macOS the
# step's /usr/bin/rm is /bin/rm.
_RUN_CMD = 'cmd=$1; shift; [ -x "$cmd" ] || cmd=/bin/$(basename "$cmd"); exec "$cmd" "$@"\n'
SUDO_STUBS = {
    "root": (
        'for p in "$@"; do\n'
        '  if [ -d "$p" ] && [ ! -L "$p" ]; then /bin/chmod -R u+rwx "$p"; fi\n'
        "done\n" + _RUN_CMD
    ),
    "user": _RUN_CMD,
    "noop": "exit 0\n",
}


def _sudo(ws: Path, kind: str) -> Path:
    stub = ws / "runner" / f"sudo-{kind}"
    stub.write_text("#!/bin/sh\n" + SUDO_STUBS[kind], encoding="utf-8")
    stub.chmod(0o755)
    return stub


def _read_only(ws: Path) -> None:
    """Round 3: a planted .git/config and venv tools under mode-0500 directories."""
    repo = ws / "repo"
    _plant_tools(repo / ".venv" / "bin", _marker(ws))
    with (repo / ".git" / "config").open("a", encoding="utf-8") as f:
        f.write("[core]\n\tfsmonitor = /nonexistent/planted\n")
    (repo / ".venv" / "bin").chmod(0o500)
    (repo / ".git").chmod(0o500)


def _symlinked(ws: Path) -> None:
    """.git and .venv as symlinks to directories outside repo/."""
    repo = ws / "repo"
    for name in (".git", ".venv"):
        target = ws / f"elsewhere{name}"
        (repo / name).rename(target)
        (repo / name).symlink_to(target, target_is_directory=True)


@pytest.mark.parametrize(
    ("setup", "sudo", "removed"),
    [
        pytest.param(None, "root", True, id="plain"),
        pytest.param(_read_only, "root", True, id="read-only-as-root"),
        pytest.param(_symlinked, "root", True, id="symlinked"),
        # Without root the read-only case fails, loudly; and if nothing was
        # removed, the closing check fails the step.
        pytest.param(_read_only, "user", False, id="read-only-as-user-fails"),
        pytest.param(None, "noop", False, id="nothing-removed-fails"),
    ],
)
def test_constructed_the_last_step_removes_git_and_the_venv_and_runs_nothing(
    tmp_path: Path, setup: Callable[[Path], None] | None, sudo: str, removed: bool
) -> None:
    ws = _workspace(tmp_path)
    if setup is not None:
        setup(ws)
    done = _run(ws, "leave", {}, sudo=_sudo(ws, sudo))
    repo = ws / "repo"
    left = [n for n in (".git", ".venv") if (repo / n).exists() or (repo / n).is_symlink()]
    if removed:
        assert done.returncode == 0, done.stdout + done.stderr
        assert left == []
    else:
        assert done.returncode != 0
        assert left != []
    # The rest stays: setup-uv's post reads the project. A symlink's target
    # outside repo/ is not followed.
    assert (repo / "scripts" / "tool.py").is_file()
    if setup is _symlinked:
        assert (ws / "elsewhere.git" / "config").is_file()
        assert (ws / "elsewhere.venv" / "bin").is_dir()
