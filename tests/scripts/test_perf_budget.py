"""Grades for the nightly performance budget (M16-27, docs/LAB_PLAN.md §6.12).

The budget itself runs only in `.github/workflows/perf-nightly.yml`, never on
the PR path (§18.1 item 6). What runs here, on every PR, keeps that job from
rotting unseen:

- §6.12's table and `scripts/perf_budget.py` agree: the same five commands,
  the same baselines, the same failure factor and the same absolute target;
- the verdict fails a ratio over 1.5 times its baseline, passes one at it,
  and fails a command with no baseline (constructed ratios);
- the workflow runs only on a schedule or by hand, on `ubuntu-latest`, with
  a read-only token, and runs the script;
- the whole pipeline runs end to end on a small constructed tree in record
  mode (no budget is checked here: the PR runners are not the reference
  runner), with the home and user data directories redirected into the
  sandbox, and leaves the captured fixture tree it copied unchanged.

The pipeline's children ask the real process list whether the game client
runs, and the gate refuses when it does (M15-18). The test, not the script,
answers for them: a temporary `sitecustomize.py` on `PYTHONPATH` (which the
script's `-P` does not block, and `build_sandbox` copies) empties
`psutil.process_iter` in every child, so the run passes whether or not a
client is open on the test machine. Nothing under `scripts/` or `lab/*/src/`
learns of it, so no setting a player could make changes what the probe or the
gate answers (L2), and the nightly run keeps the real probe. To prove the
stub is what carries the test, each pipeline test starts a constructed
process named like the client first, and a second test shows the same child
commands refused by the gate without the stub.

Every ratio in the verdict tests is constructed. The smoke run copies the
captured `macos/` fixture into a temporary folder and grows it with
constructed files; it reads no real install and calls no network service.
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import time
import types
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml

from wowlab_core import process

ROOT = Path(__file__).resolve().parents[2]
PLAN = ROOT / "docs" / "LAB_PLAN.md"
WORKFLOW = ROOT / ".github" / "workflows" / "perf-nightly.yml"


def _load() -> types.ModuleType:
    path = ROOT / "scripts" / "perf_budget.py"
    spec = importlib.util.spec_from_file_location("perf_budget", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


perf_budget = _load()


def _section() -> str:
    text = PLAN.read_text(encoding="utf-8")
    start = text.index("### 6.12 ")
    end = text.index("\n## 7.", start)
    return text[start:end]


_ROW = re.compile(r"^\| `([a-z-]+)` \|.*\| ([0-9.]+) \|$")


def _tables(section: str) -> list[dict[str, float]]:
    """Every Markdown table in `section` that has command rows, in order: a
    command id -> the table's last column. A table is a run of lines that
    start with `|`; a command listed twice in one table is an error, so no
    row can hide another."""
    tables: list[dict[str, float]] = []
    rows: dict[str, float] = {}
    for line in [*section.splitlines(), ""]:
        if not line.startswith("|"):
            if rows:
                tables.append(rows)
            rows = {}
            continue
        match = _ROW.match(line)
        if match:
            assert match.group(1) not in rows, f"{match.group(1)} listed twice in one table"
            rows[match.group(1)] = float(match.group(2))
    return tables


# ─── the spec table and the script agree ────────────────────────────────────


def test_the_plan_table_matches_the_script_baselines() -> None:
    tables = _tables(_section())
    assert tables, "no baseline table in §6.12"
    for table in tables:
        assert table == perf_budget.BASELINE


def test_a_baseline_table_that_disagrees_with_the_script_is_caught() -> None:
    # Constructed: the real §6.12 with the first table's first row changed.
    lines = _section().splitlines()
    first = next(i for i, line in enumerate(lines) if _ROW.match(line))
    lines[first] = re.sub(r"\| [0-9.]+ \|$", "| 99.9 |", lines[first])
    tables = _tables("\n".join(lines))
    assert tables[0] != perf_budget.BASELINE
    assert any(table != perf_budget.BASELINE for table in tables)


def test_a_command_listed_twice_in_one_table_is_refused() -> None:
    # Constructed: one table with the same command on two rows.
    text = (
        "| Command | What is run | Baseline ratio |\n|---|---|---|\n"
        "| `version` | `wowlab --version` | 9.9 |\n"
        "| `version` | `wowlab --version` | 1.8 |\n"
    )
    with pytest.raises(AssertionError, match="listed twice"):
        _tables(text)


def test_every_command_has_a_positive_baseline() -> None:
    assert set(perf_budget.BASELINE) == set(perf_budget.COMMANDS)
    assert all(value > 0 for value in perf_budget.BASELINE.values())


def test_the_ticket_commands_are_the_ones_timed() -> None:
    assert set(perf_budget.COMMANDS) == {
        "whole-restore",
        "sv-dump-json",
        "sv-merge",
        "addons-list",
        "version",
    }


def test_the_plan_states_the_factor_and_the_absolute_target() -> None:
    section = _section()
    assert perf_budget.FAIL_FACTOR == 1.5 and "1.5 times" in section
    assert perf_budget.RESTORE_FILES == 20_000 and "20,000" in section
    assert perf_budget.RESTORE_LIMIT_S == 5.0 and "5 s" in section


# ─── the verdict (constructed ratios) ───────────────────────────────────────


def test_constructed_a_ratio_at_the_limit_passes() -> None:
    assert perf_budget.verdict({"a": 15.0}, {"a": 10.0}) == []


def test_constructed_a_ratio_over_the_limit_fails() -> None:
    misses = perf_budget.verdict({"a": 15.01, "b": 1.0}, {"a": 10.0, "b": 1.0})
    assert len(misses) == 1 and misses[0].startswith("a: ")


def test_constructed_a_command_without_a_baseline_fails() -> None:
    assert perf_budget.verdict({"a": 1.0}, {}) == ["a: no baseline"]
    assert perf_budget.verdict({"a": 1.0}, {"a": 0.0}) == ["a: no baseline"]


def test_constructed_the_calibration_loop_takes_measurable_time() -> None:
    assert perf_budget.calibrate() > 0


# ─── the workflow ───────────────────────────────────────────────────────────


def _workflow() -> dict[object, object]:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def test_the_workflow_is_off_the_pr_path() -> None:
    # PyYAML reads the bare key `on` as True.
    triggers = _workflow()[True]
    assert isinstance(triggers, dict)
    assert set(triggers) == {"schedule", "workflow_dispatch"}


def test_the_workflow_has_a_read_only_token_and_runs_the_script() -> None:
    workflow = _workflow()
    assert workflow["permissions"] == {"contents": "read"}
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict) and set(jobs) == {"budget"}
    job = jobs["budget"]
    assert job["runs-on"] == "ubuntu-latest"
    assert job["permissions"] == {"contents": "read"}
    runs = [step["run"] for step in job["steps"] if "run" in step]
    assert runs == [
        "uv sync --frozen",
        "uv run --frozen python scripts/perf_budget.py",
    ]


# ─── the pipeline, end to end, small ────────────────────────────────────────


def _tree_hash(base: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in base.rglob("*") if p.is_file()):
        digest.update(path.relative_to(base).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


# Test code only (M15-18): imported by every child Python whose PYTHONPATH
# starts with its folder. It empties the process list psutil hands the gate's
# probe; nothing in `scripts/` or `lab/*/src/` knows it exists.
PROBE_STUB = """\
# Constructed by tests/scripts/test_perf_budget.py (M15-18); test code only.
import psutil

psutil.process_iter = lambda *args, **kwargs: iter(())
"""

# A name the probe matches whatever the install root (`process.KNOWN_CLIENT_NAMES`).
STAND_IN_NAME = "World of Warcraft"
STAND_IN_WAIT_S = 10.0


def _with_probe_stub(base: Path, env: dict[str, str]) -> dict[str, str]:
    """`env` with a folder holding `PROBE_STUB` as `sitecustomize.py` first
    on `PYTHONPATH`."""
    folder = base / "probe-stub"
    folder.mkdir()
    (folder / "sitecustomize.py").write_text(PROBE_STUB, encoding="utf-8")
    rest = env.get("PYTHONPATH")
    return {**env, "PYTHONPATH": os.pathsep.join([str(folder), *([rest] if rest else [])])}


@pytest.fixture
def client_stand_in(tmp_path_factory: pytest.TempPathFactory) -> Iterator[int]:
    """A constructed process named like the game client, running for the
    test; yields its pid once the real, unstubbed probe reports it.

    It is a copy of the interpreter running the tests, saved as
    `<tmp>/bin/World of Warcraft` with `<tmp>/lib` linked to the
    interpreter's own `lib` (where a relocatable build finds its library
    and standard library), waiting on its standard input. A copy rather than
    a link, because the operating system names a process after the file it
    executes; the interpreter rather than a system tool, because macOS kills
    a copy of one of its own executables run from elsewhere."""
    assert STAND_IN_NAME in process.KNOWN_CLIENT_NAMES
    base = tmp_path_factory.mktemp("client-stand-in")
    real = Path(os.path.realpath(sys.executable))
    (base / "bin").mkdir()
    exe = base / "bin" / STAND_IN_NAME
    shutil.copyfile(real, exe)
    exe.chmod(0o755)
    (base / "lib").symlink_to(real.parent.parent / "lib", target_is_directory=True)
    proc = subprocess.Popen(
        [str(exe), "-I", "-S", "-c", "import sys; sys.stdin.read()"],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + STAND_IN_WAIT_S
        while not any(c.pid == proc.pid for c in process.running_clients()):
            if proc.poll() is not None:
                err = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
                pytest.fail(f"the client stand-in exited {proc.returncode}: {err[-400:]}")
            if time.monotonic() > deadline:
                pytest.fail("the probe never reported the client stand-in")
            time.sleep(0.05)
        assert process.client_state() is process.ClientState.RUNNING
        yield proc.pid
    finally:
        proc.kill()
        proc.wait(timeout=STAND_IN_WAIT_S)
        if proc.stderr is not None:
            proc.stderr.close()
        if proc.stdin is not None:
            proc.stdin.close()


@pytest.mark.skipif(os.name != "posix", reason="the budget script is POSIX only")
def test_constructed_the_pipeline_runs_on_a_small_tree(
    tmp_path: Path,
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    client_stand_in: int,
) -> None:
    # The precondition (the fixture): a process named like the client runs,
    # so only the stub lets the gate in the children say "not running".
    stub_env = _with_probe_stub(tmp_path_factory.mktemp("stub"), dict(os.environ))
    monkeypatch.setenv("PYTHONPATH", stub_env["PYTHONPATH"])
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setattr(perf_budget.tempfile, "tempdir", None)
    before = _tree_hash(perf_budget.FIXTURE)
    code = perf_budget.main(["--record", "--files", "40", "--runs", "1", "--restore-runs", "1"])
    out = capsys.readouterr()
    assert code == 0, out.err
    for command in perf_budget.COMMANDS:
        assert re.search(rf"^{command} +[0-9.]+ +[0-9.]+ +[0-9.]+ ", out.out, re.M), out.out
    # The absolute check times the gate's call in its own child process.
    assert re.search(r"^gate's whole-restore check at 40 files: [0-9.]+ s wall", out.out, re.M)
    assert "(information only)" in out.out
    assert _tree_hash(perf_budget.FIXTURE) == before
    # The sandbox, user data included, was removed with its temporary folder.
    assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(os.name != "posix", reason="the budget script is POSIX only")
def test_constructed_without_the_stub_the_gate_refuses_the_same_children(
    tmp_path: Path, client_stand_in: int
) -> None:
    """The pipeline's two gated children, run as the script runs them, are
    refused while a process named like the client runs; with the stub and
    nothing else changed, both are let through. So the stub, and only the
    test's stub, is what lets the pipeline test pass, and the script's own
    run keeps the real check."""
    root, env = perf_budget.build_sandbox(tmp_path / "sb", 8)
    fill = {"root": str(root), "snapshot": perf_budget.take_snapshot(root, env, tmp_path)}
    stubbed = _with_probe_stub(tmp_path, env)
    restore = perf_budget._argv("whole-restore", fill)
    gate = [sys.executable, "-P", "-c", perf_budget.GATE_DRIVER, fill["root"], fill["snapshot"]]

    for argv in (restore, gate):
        code, _, _, err = perf_budget.run_once(argv, env, tmp_path)
        assert code != 0, f"the gate let {argv[3:5]} through with a client running"
        assert "the game client is running" in err, err[-800:]

    code, _, _, err = perf_budget.run_once(restore, stubbed, tmp_path)
    assert code == 0, err[-800:]
    assert perf_budget.measure_gate(fill, stubbed, tmp_path, 1) >= 0


def test_constructed_the_sandbox_redirects_home_and_user_data(tmp_path: Path) -> None:
    root, env = perf_budget.build_sandbox(tmp_path / "sb", 16)
    assert root.is_relative_to(tmp_path)
    for key in (
        "HOME",
        "XDG_DATA_HOME",
        "XDG_CONFIG_HOME",
        "XDG_CACHE_HOME",
        "XDG_STATE_HOME",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        # platformdirs on Windows moves only with these (M18-16).
        "WIN_PD_OVERRIDE_APPDATA",
        "WIN_PD_OVERRIDE_LOCAL_APPDATA",
        "WOWLAB_WOW_ROOT",
    ):
        assert Path(env[key]).is_relative_to(tmp_path), key
    assert env["WIN_PD_OVERRIDE_APPDATA"] == env["APPDATA"]
    assert env["WIN_PD_OVERRIDE_LOCAL_APPDATA"] == env["LOCALAPPDATA"]
    flavor = root / perf_budget.FLAVOR_FOLDER
    constructed = list(flavor.rglob("Constructed*.lua")) + list(flavor.rglob("f*.lua"))
    assert len({p for p in constructed if b"constructed" in p.read_bytes()}) == 16
