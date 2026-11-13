"""Grades for the macOS app build (M17-08; ADR-0038, ADR-0031 amendment of
2026-11-12).

Four parts. The pins: PyInstaller's version in its own dependency group, the
spec file's SHA-256 and what the spec may contain (one folder, no UPX, no
hidden imports but wowlab_core's own modules, no runtime hooks, no signing
identity). The launcher: what it does with no arguments, with arguments, and
with the form `wowlab ui` starts its runs with, decided by `plan()` and
checked here without starting anything. The workflows: the app jobs in
release.yml and macos-app.yml are the same steps, read-only, and the release
publishes the wheel and the sdist whatever became of the app, and the app
only after it was hashed, smoke-tested, checked and attested. The smoke
test: `scripts/app_smoke.py` passes against the launcher run by this
interpreter (the frozen build is graded by the same script in CI), and the
recorder writes the URL it is given.

Nothing here builds the app; PyInstaller is not installed by `uv sync`.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tomllib
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
PACKAGING = ROOT / "packaging"
SPEC = PACKAGING / "wowlab.spec"
LAUNCHER = PACKAGING / "launcher.py"
SCRIPTS = ROOT / "scripts"
RELEASE = ROOT / ".github" / "workflows" / "release.yml"
APP_WORKFLOW = ROOT / ".github" / "workflows" / "macos-app.yml"

# ADR-0038 item 4: changing either is a PR that says why.
# M17-09 re-pinned the spec for a comment only: it names what the Windows
# build is (the `wowlab/` folder itself); no line PyInstaller runs changed.
PYINSTALLER = "pyinstaller==6.22.3"
SPEC_SHA256 = "cfe15166047a3d39473aeb48dc40ea0fa4b48a48d73e278bd72e2b990abd70a5"


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


launcher = _load(LAUNCHER, "wowlab_packaging_launcher")
app_smoke = _load(SCRIPTS / "app_smoke.py", "wowlab_app_smoke")


# ─── the pins ────────────────────────────────────────────────────────────────


def test_pyinstaller_is_pinned_in_a_build_group_of_its_own() -> None:
    root = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    groups = root["dependency-groups"]
    assert groups["packaging"] == [PYINSTALLER]
    assert not any("pyinstaller" in d.lower() for d in groups["dev"])
    # Never a runtime dependency, never a default group.
    core = tomllib.loads((ROOT / "lab" / "core" / "pyproject.toml").read_text(encoding="utf-8"))
    assert not any("pyinstaller" in d.lower() for d in core["project"]["dependencies"])
    assert "default-groups" not in root.get("tool", {}).get("uv", {})
    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
    name, version = PYINSTALLER.split("==")
    assert f'name = "{name}"\nversion = "{version}"' in lock


def test_the_spec_file_is_the_reviewed_one() -> None:
    digest = hashlib.sha256(SPEC.read_bytes()).hexdigest()
    assert digest == SPEC_SHA256, (
        "packaging/wowlab.spec changed: review it against ADR-0038 item 5, then "
        f"update SPEC_SHA256 to {digest} in the same PR and say why"
    )


def _calls(tree: ast.AST) -> dict[str, dict[str, ast.expr]]:
    calls: dict[str, dict[str, ast.expr]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            calls[node.func.id] = {k.arg: k.value for k in node.keywords if k.arg}
    return calls


def _assigned(tree: ast.Module) -> dict[str, str]:
    return {
        target.id: ast.unparse(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }


def test_the_spec_is_one_folder_no_upx_no_hooks_no_identity() -> None:
    tree = ast.parse(SPEC.read_text(encoding="utf-8"))
    calls = _calls(tree)
    assert {"Analysis", "PYZ", "EXE", "COLLECT", "BUNDLE"} <= set(calls)
    lit = {name: {k: ast.unparse(v) for k, v in kw.items()} for name, kw in calls.items()}
    # One folder: the EXE leaves the binaries out and COLLECT gathers them.
    assert lit["EXE"]["exclude_binaries"] == "True"
    assert lit["EXE"]["upx"] == "False"
    assert lit["COLLECT"]["upx"] == "False"
    assert lit["COLLECT"]["upx_exclude"] == "[]"
    assert "upx=True" not in SPEC.read_text(encoding="utf-8").replace(" ", "")
    # A console build of the ordinary command, named wowlab.
    assert lit["EXE"]["console"] == "True"
    assert lit["EXE"]["name"] == "'wowlab'"
    assert lit["COLLECT"]["name"] == "'wowlab'"
    # No signing identity: PyInstaller's ad-hoc signature only.
    assert lit["EXE"]["codesign_identity"] == "None"
    assert lit["EXE"]["entitlements_file"] == "None"
    # No runtime hooks and no hook folders of our own.
    assert lit["Analysis"]["runtime_hooks"] == "[]"
    assert lit["Analysis"]["hookspath"] == "[]"
    assert lit["Analysis"]["binaries"] == "[]"
    assert lit["BUNDLE"]["name"] == "'wowlab.app'"
    assert lit["BUNDLE"]["icon"] == "None"


def test_the_spec_lists_its_hidden_imports_and_freezes_the_launcher() -> None:
    tree = ast.parse(SPEC.read_text(encoding="utf-8"))
    calls = {name: {k: ast.unparse(v) for k, v in kw.items()} for name, kw in _calls(tree).items()}
    assigned = _assigned(tree)
    # The only hidden imports are wowlab_core's own modules.
    assert calls["Analysis"]["hiddenimports"] == "HIDDEN_IMPORTS"
    assert assigned["HIDDEN_IMPORTS"] == "collect_submodules('wowlab_core')"
    assert calls["Analysis"]["datas"] == "DATAS"
    assert assigned["DATAS"] == "collect_data_files('wowlab_core')"
    assert calls["Analysis"]["excludes"] == "EXCLUDES"
    excluded = ast.literal_eval(assigned["EXCLUDES"])
    assert set(excluded) == {
        "PyInstaller",
        "setuptools",
        "pkg_resources",
        "_distutils_hack",
        "distutils",
    }
    # The entry point is the launcher, from packaging/.
    analysis = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "Analysis"
    )
    assert ast.unparse(analysis.args[0]) == "[f'{HERE}/launcher.py']"


def test_nothing_in_the_library_imports_pyinstaller() -> None:
    src = ROOT / "lab" / "core" / "src"
    hits = [
        p
        for p in src.rglob("*.py")
        if re.search(r"^\s*(import|from)\s+PyInstaller\b", p.read_text("utf-8"), re.M)
    ]
    assert hits == []


def test_the_launcher_imports_only_what_it_needs() -> None:
    tree = ast.parse(LAUNCHER.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    # No network module, no update check: the command, a subprocess for open,
    # and os for PYTHONIOENCODING (M17-09).
    assert imported == {
        "__future__",
        "os",
        "subprocess",
        "sys",
        "collections.abc",
        "typing",
        "wowlab_core.cli",
    }


# ─── the launcher ────────────────────────────────────────────────────────────

EXE = "/Applications/wowlab.app/Contents/MacOS/wowlab"


@pytest.mark.parametrize("platform", ["darwin", "win32", "linux"])
def test_no_arguments_with_a_terminal_runs_ui(platform: str) -> None:
    assert launcher.plan([], platform=platform, attached=True, executable=EXE) == ("run", ["ui"])


def test_no_arguments_and_no_terminal_on_macos_hands_off_to_terminal() -> None:
    assert launcher.plan([], platform="darwin", attached=False, executable=EXE) == (
        "terminal",
        ["/usr/bin/open", "-a", "Terminal", EXE],
    )


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_no_arguments_and_no_terminal_elsewhere_runs_ui(platform: str) -> None:
    # Windows: a double-click opens a console window of its own (M17-09).
    assert launcher.plan([], platform=platform, attached=False, executable=EXE) == ("run", ["ui"])


@pytest.mark.parametrize(
    "argv",
    [
        ["--version"],
        ["doctor", "--json"],
        ["ui"],
        ["-P"],
        ["-P", "-m"],
        ["-m", "wowlab_core", "--version"],
        ["-P", "-m", "other", "--version"],
        ["explain", "-P", "-m", "wowlab_core"],
    ],
)
@pytest.mark.parametrize("attached", [True, False])
def test_arguments_pass_through_unchanged(argv: list[str], attached: bool) -> None:
    assert launcher.plan(argv, platform="darwin", attached=attached, executable=EXE) == (
        "run",
        argv,
    )


@pytest.mark.parametrize("attached", [True, False])
def test_the_ui_run_form_runs_the_command_it_names(attached: bool) -> None:
    # `wowlab ui` starts its runs as [sys.executable, "-P", "-m", "wowlab_core", ...],
    # and in a frozen build sys.executable is the launcher.
    plan = launcher.plan(
        ["-P", "-m", "wowlab_core", "doctor", "--json"],
        platform="darwin",
        attached=attached,
        executable=EXE,
    )
    assert plan == ("run", ["doctor", "--json"])
    # Even with no command after it: the command's own help, never the hand-off.
    assert launcher.plan(
        ["-P", "-m", "wowlab_core"], platform="darwin", attached=False, executable=EXE
    ) == ("run", [])


PSN = "-psn_0_1234567"


def test_a_finder_psn_alone_with_no_terminal_hands_off_to_terminal() -> None:
    # LaunchServices may add a process serial number; the console bootloader
    # passes it on, and click would refuse it ("No such option: -p").
    assert launcher.plan([PSN], platform="darwin", attached=False, executable=EXE) == (
        "terminal",
        ["/usr/bin/open", "-a", "Terminal", EXE],
    )


def test_a_finder_psn_alone_with_a_terminal_runs_ui() -> None:
    assert launcher.plan([PSN], platform="darwin", attached=True, executable=EXE) == (
        "run",
        ["ui"],
    )


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        ([PSN, "--version"], ["--version"]),
        ([PSN, "doctor", "--json"], ["doctor", "--json"]),
        ([PSN, "-P", "-m", "wowlab_core", "doctor"], ["doctor"]),
        # Only a leading one is dropped; anywhere else it is an argument.
        (["explain", PSN], ["explain", PSN]),
    ],
)
@pytest.mark.parametrize("attached", [True, False])
def test_a_finder_psn_before_real_arguments_is_dropped(
    argv: list[str], expected: list[str], attached: bool
) -> None:
    assert launcher.plan(argv, platform="darwin", attached=attached, executable=EXE) == (
        "run",
        expected,
    )


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_a_psn_argument_is_only_dropped_on_macos(platform: str) -> None:
    assert launcher.plan([PSN], platform=platform, attached=True, executable=EXE) == (
        "run",
        [PSN],
    )


def test_main_drops_a_finder_psn_and_hands_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(launcher.sys, "platform", "darwin")
    monkeypatch.setattr(launcher, "_attached", lambda: False)
    monkeypatch.setattr(launcher.sys, "executable", EXE)
    spawned: list[list[str]] = []
    ran: list[list[str]] = []

    def spawn(args: list[str]) -> int:
        spawned.append(args)
        return 0

    assert launcher.main([PSN], run=ran.append, spawn=spawn) == 0
    assert spawned == [["/usr/bin/open", "-a", "Terminal", EXE]]
    assert ran == []


def test_the_ui_run_form_matches_what_the_ui_starts() -> None:
    from wowlab_core import ui

    argv = ui._default_wowlab_argv()
    assert tuple(argv[1:]) == launcher.UI_RUN_PREFIX


def test_main_hands_off_once_and_runs_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(launcher.sys, "platform", "darwin")
    monkeypatch.setattr(launcher, "_attached", lambda: False)
    monkeypatch.setattr(launcher.sys, "executable", EXE)
    spawned: list[list[str]] = []
    ran: list[list[str]] = []

    def spawn(args: list[str]) -> int:
        spawned.append(args)
        return 0

    assert launcher.main([], run=ran.append, spawn=spawn) == 0
    assert spawned == [["/usr/bin/open", "-a", "Terminal", EXE]]
    assert ran == []


def test_main_runs_ui_inside_a_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(launcher.sys, "platform", "darwin")
    monkeypatch.setattr(launcher, "_attached", lambda: True)
    ran: list[list[str]] = []

    def spawn(args: list[str]) -> int:
        raise AssertionError("no second Terminal window")

    assert launcher.main([], run=ran.append, spawn=spawn) == 0
    assert ran == [["ui"]]


class _Stream:
    def __init__(self, tty: bool | Exception) -> None:
        self.tty = tty

    def isatty(self) -> bool:
        if isinstance(self.tty, Exception):
            raise self.tty
        return self.tty


@pytest.mark.parametrize(
    ("stdin", "stdout", "attached"),
    [
        (_Stream(False), _Stream(False), False),
        (_Stream(True), _Stream(False), True),
        (_Stream(False), _Stream(True), True),
        (None, None, False),
        (_Stream(ValueError("closed")), _Stream(False), False),
    ],
)
def test_attached_means_stdin_or_stdout_is_a_terminal(
    monkeypatch: pytest.MonkeyPatch, stdin: Any, stdout: Any, attached: bool
) -> None:
    monkeypatch.setattr(launcher.sys, "stdin", stdin)
    monkeypatch.setattr(launcher.sys, "stdout", stdout)
    assert launcher._attached() is attached


def test_the_launcher_runs_the_ordinary_command() -> None:
    # Unfrozen, with this interpreter: --version is the wheel's.
    from wowlab_core import __version__

    done = subprocess.run(
        [sys.executable, str(LAUNCHER), "--version"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == f"wowlab {__version__}"


# ─── the workflows ───────────────────────────────────────────────────────────


def _jobs(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data["jobs"]


def _workflow(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


@pytest.mark.parametrize("job", ["app", "app_smoke"])
def test_the_release_and_the_pull_request_build_the_app_the_same_way(job: str) -> None:
    release, by_hand = _jobs(RELEASE)[job], _jobs(APP_WORKFLOW)[job]
    assert release["steps"] == by_hand["steps"]
    for key in ("runs-on", "timeout-minutes", "outputs", "name"):
        assert release.get(key) == by_hand.get(key), key
    assert set(release["env"]) == set(by_hand["env"])
    assert release["runs-on"] == "macos-latest"  # Apple silicon (arm64)


def test_the_app_workflow_runs_on_a_pull_request_or_by_hand_and_publishes_nothing() -> None:
    data = _workflow(APP_WORKFLOW)
    triggers = data.get("on", data.get(True))
    assert set(triggers) == {"pull_request", "workflow_dispatch"}
    assert "packaging/**" in triggers["pull_request"]["paths"]
    assert data["permissions"] == {"contents": "read"}
    jobs = data["jobs"]
    assert set(jobs) == {"wheel", "app", "app_smoke"}
    for name, job in jobs.items():
        assert "permissions" not in job, name
        assert "environment" not in job, name
    text = APP_WORKFLOW.read_text(encoding="utf-8")
    assert "secrets." not in text
    assert "github.token" not in text
    assert "gh release" not in text
    assert "attest" not in text


def test_the_app_workflow_builds_the_wheel_as_the_release_does() -> None:
    release_build = _jobs(RELEASE)["build"]["steps"]
    app_wheel = _jobs(APP_WORKFLOW)["wheel"]["steps"]
    command = next(s["run"] for s in release_build if s.get("name") == "uv build")
    assert next(s["run"] for s in app_wheel if s.get("name") == "uv build") == command


def test_the_app_jobs_build_from_the_checked_wheel_and_never_publish() -> None:
    jobs = _jobs(RELEASE)
    app, smoke = jobs["app"], jobs["app_smoke"]
    assert app["needs"] == ["version", "build"]
    assert smoke["needs"] == ["version", "app"]
    for job in (app, smoke):
        assert "permissions" not in job
        assert "environment" not in job
        assert "if" not in job
    steps = app["steps"]
    names = [s.get("name") for s in steps]
    runs = [" ".join(s.get("run", "").split()) for s in steps]
    # The wheel is checked against the build job's output before anything uses it.
    check = names.index("The wheel is the one the build hashed")
    assert steps[check - 1]["uses"].startswith("actions/download-artifact@")
    assert steps[check - 1]["with"] == {"name": "release", "path": "${{ runner.temp }}/release"}
    assert 'shasum -a 256 -c "$RUNNER_TEMP/expected-wheel"' in runs[check]
    assert app["env"]["WHEEL_SHA256"] == "${{ needs.build.outputs.wheel_sha256 }}"
    venv = runs[names.index("A clean virtual environment with the wheel and PyInstaller")]
    assert "--only-group packaging" in venv
    assert "--require-hashes" in venv
    assert '--no-deps "$RUNNER_TEMP/release/$WHEEL"' in venv
    assert "--managed-python" in venv
    assert "-e " not in venv and "--editable" not in venv
    build = runs[names.index("PyInstaller, one folder, no UPX")]
    assert '"$GITHUB_WORKSPACE/packaging/wowlab.spec"' in build
    assert "--onefile" not in build and "-F " not in build
    assert "lipo -archs" in build and "= arm64" in build
    assert steps[names.index("PyInstaller, one folder, no UPX")]["working-directory"] == (
        "${{ runner.temp }}"
    )
    # The build job never runs what it built: the smoke test is its own job.
    assert not any("app_smoke.py" in r or "MacOS/wowlab --" in r for r in runs)
    assert steps[-1]["uses"].startswith("actions/upload-artifact@")
    assert steps[-1]["with"]["name"] == "app"
    assert app["outputs"] == {"app_sha256": "${{ steps.zip.outputs.app_sha256 }}"}
    zip_step = steps[names.index("Zip the app and record its SHA-256")]
    assert zip_step["id"] == "zip"
    assert 'ditto -c -k --keepParent "$RUNNER_TEMP/dist/wowlab.app"' in zip_step["run"]
    assert app["env"]["APP_ZIP"] == "wowlab-${{ needs.version.outputs.version }}-macos-arm64.zip"


def test_the_smoke_job_runs_the_downloaded_zip_against_the_tag() -> None:
    smoke = _jobs(RELEASE)["app_smoke"]
    steps = smoke["steps"]
    names = [s.get("name") for s in steps]
    check = names.index("The zip is the one the build hashed")
    assert steps[check - 1]["with"]["name"] == "app"
    assert smoke["env"]["APP_SHA256"] == "${{ needs.app.outputs.app_sha256 }}"
    assert smoke["env"]["VERSION"] == "${{ needs.version.outputs.version }}"
    run = " ".join(steps[names.index("Unzip it and run the smoke test")]["run"].split())
    assert 'ditto -x -k "$RUNNER_TEMP/app/$APP_ZIP"' in run
    assert (
        'scripts/app_smoke.py --exe "$RUNNER_TEMP/unzipped/wowlab.app/Contents/MacOS/wowlab" '
        '--version "$VERSION"'
    ) in run


def test_the_release_never_waits_on_the_app() -> None:
    # ADR-0031 amendment of 2026-11-12: the wheel and the sdist are released
    # first; the app is attached after. `release` is as M16-43 left it.
    release = _jobs(RELEASE)["release"]
    assert release["needs"] == ["version", "test", "build", "smoke"]
    assert "if" not in release
    assert "APP_" not in str(release)
    assert "macos-arm64" not in str(release)


def test_the_app_is_attached_after_the_release_only_when_built_and_smoked() -> None:
    attach = _jobs(RELEASE)["app_release"]
    assert attach["needs"] == ["version", "build", "app", "app_smoke", "release"]
    # The implicit success(): a failed app build or smoke test fails its own
    # job, visibly, and this one is skipped; the release stands.
    assert "if" not in attach
    assert "continue-on-error" not in attach
    # No environment, by owner decision (2026-11-12): it is gated by `release`.
    assert "environment" not in attach
    assert attach["permissions"] == {
        "contents": "write",
        "id-token": "write",
        "attestations": "write",
    }
    env = attach["env"]
    # The hash comes from the build job's output, never from the artifact.
    assert env["APP_SHA256"] == "${{ needs.app.outputs.app_sha256 }}"
    assert env["APP_ZIP"] == "wowlab-${{ needs.version.outputs.version }}-macos-arm64.zip"
    assert env["WHEEL_SHA256"] == "${{ needs.build.outputs.wheel_sha256 }}"
    assert env["SDIST_SHA256"] == "${{ needs.build.outputs.sdist_sha256 }}"
    assert env["TAG"] == "${{ github.ref_name }}"
    assert "GH_TOKEN" not in env


def test_the_attach_job_checks_attests_then_uploads_and_runs_no_repository_code() -> None:
    steps = _jobs(RELEASE)["app_release"]["steps"]
    uses = [s["uses"].split("@")[0] for s in steps if "uses" in s]
    assert uses == ["actions/download-artifact", "actions/attest-build-provenance"]
    assert steps[0]["with"] == {"name": "app", "path": "app"}
    assert not any(s.get("continue-on-error") for s in steps)
    names = [s.get("name") for s in steps]
    runs = " ".join(s.get("run", "") for s in steps)
    assert not re.search(r"\buv\b|\bpython3?\b|\bscripts/|\bgit\b|\bmake\b", runs)
    checked = steps[names.index("The zip is the one the build hashed")]
    assert (
        'printf \'%s  %s\\n\' "$APP_SHA256" "$APP_ZIP" > "$RUNNER_TEMP/expected-app"'
        in (checked["run"])
    )
    assert 'sha256sum -c "$RUNNER_TEMP/expected-app"' in checked["run"]
    sums = steps[names.index("The release's SHA256SUMS is one this job may write")]
    # The wheel's and the sdist's lines come from `build`'s outputs.
    assert (
        'printf \'%s  %s\\n\' "$WHEEL_SHA256" "$WHEEL" "$SDIST_SHA256" "$SDIST" '
        '> "$RUNNER_TEMP/expected"'
    ) in sums["run"]
    attest = steps[names.index("Attest the app")]
    assert attest["with"] == {
        "subject-path": "app/wowlab-${{ needs.version.outputs.version }}-macos-arm64.zip"
    }
    assert "env" not in attest
    upload_app = steps[names.index("Upload the app, unless the release has it")]
    upload_sums = steps[names.index("Upload SHA256SUMS with the zip's line")]
    for step in (sums, upload_app, upload_sums):
        assert step["env"] == {"GH_TOKEN": "${{ github.token }}"}
    # Checked, then what the release holds, then attested, then uploaded: a
    # failed check or attestation uploads nothing.
    order = [steps.index(s) for s in (checked, sums, attest, upload_app, upload_sums)]
    assert order == sorted(order)
    assert order[-1] == len(steps) - 1


# ── app_release, run: its shell steps against a stand-in `gh` ──
# Each case is `constructed`: a release's assets as files, the zip as a few
# bytes. The job's own `run` text is executed, step by step, the way the
# runner does (`bash --noprofile --norc -eo pipefail`); `uses` steps are
# played by the harness (the artifact is put in place, the attestation is
# recorded).

FAKE_GH = Path(__file__).resolve().with_name("_fake_gh.py")
TAG = "v9.9.9"
ZIP = "wowlab-9.9.9-macos-arm64.zip"
# The Windows app's zip, which `windows_app_release` may have attached first
# (M17-09).
OTHER_ZIP = "wowlab-9.9.9-windows-x64.zip"
OTHER_BYTES = b"constructed windows zip\n"
WHEEL = "wowlab_core-9.9.9-py3-none-any.whl"
SDIST = "wowlab_core-9.9.9.tar.gz"
ZIP_BYTES = b"constructed zip\n"
WHEEL_SHA = "a" * 64
SDIST_SHA = "b" * 64


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


EXPECTED = f"{WHEEL_SHA}  {WHEEL}\n{SDIST_SHA}  {SDIST}\n".encode()
APP_LINE = f"{_sha(ZIP_BYTES)}  {ZIP}\n".encode()
OTHER_LINE = f"{_sha(OTHER_BYTES)}  {OTHER_ZIP}\n".encode()
WITH_APP = EXPECTED + APP_LINE


def _run_attach(
    tmp_path: Path,
    published: dict[str, bytes],
    *,
    artifact: bytes = ZIP_BYTES,
    corrupt: str | None = None,
    job: str = "app_release",
    zip_name: str = ZIP,
    zip_bytes: bytes = ZIP_BYTES,
    other_zip: str = OTHER_ZIP,
) -> tuple[list[str], dict[str, bytes], list[list[str]]]:
    """Run an attach job's steps (`app_release` unless `job` names
    `windows_app_release`, with its own zip); the steps that ran (the last one
    failed if the job failed), the release's assets after, and the `gh`
    calls. `artifact` is what the download step puts in place."""
    ws, temp, assets, bin_dir = (tmp_path / n for n in ("ws", "temp", "assets", "bin"))
    for folder in (ws, temp, assets, bin_dir):
        folder.mkdir()
    for name, data in published.items():
        (assets / name).write_bytes(data)
    gh = bin_dir / "gh"
    gh.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE_GH}" "$@"\n', encoding="utf-8")
    gh.chmod(0o755)
    if shutil.which("sha256sum") is None:
        shim = bin_dir / "sha256sum"
        shim.write_text('#!/bin/sh\nexec shasum -a 256 "$@"\n', encoding="utf-8")
        shim.chmod(0o755)
    log = tmp_path / "gh.log"
    log.touch()
    env = {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "RUNNER_TEMP": str(temp),
        "GITHUB_REPOSITORY": "owner/repo",
        "TAG": TAG,
        "WHEEL": WHEEL,
        "SDIST": SDIST,
        "WHEEL_SHA256": WHEEL_SHA,
        "SDIST_SHA256": SDIST_SHA,
        "APP_ZIP": zip_name,
        "APP_SHA256": _sha(zip_bytes),
        "OTHER_ZIP": other_zip,
        "FAKE_GH_ASSETS": str(assets),
        "FAKE_GH_LOG": str(log),
    }
    if corrupt:
        env["FAKE_GH_CORRUPT_UPLOAD"] = corrupt
    ran: list[str] = []
    for i, step in enumerate(_jobs(RELEASE)[job]["steps"]):
        name = step.get("name") or step["uses"].split("@")[0]
        ran.append(name)
        if step.get("uses", "").startswith("actions/download-artifact@"):
            (ws / "app").mkdir()
            (ws / "app" / zip_name).write_bytes(artifact)
            continue
        if "uses" in step:  # the attestation: nothing to run here
            continue
        script = tmp_path / f"step{i}.sh"
        script.write_text(step["run"], encoding="utf-8")
        done = subprocess.run(
            ["bash", "--noprofile", "--norc", "-eo", "pipefail", str(script)],
            cwd=ws,
            env=env,
            capture_output=True,
            timeout=60,
            check=False,
        )
        if done.returncode != 0:
            ran[-1] += " (failed)"
            break
    after = {p.name: p.read_bytes() for p in assets.iterdir()}
    calls = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    return ran, after, calls


def _uploads(calls: list[list[str]]) -> list[str]:
    return [Path(c[-1]).name for c in calls if c[:2] == ["release", "upload"]]


posix_only = pytest.mark.skipif(os.name == "nt", reason="the job's steps are bash")


@posix_only
def test_constructed_attach_to_a_fresh_release(tmp_path: Path) -> None:
    ran, after, calls = _run_attach(tmp_path, {"SHA256SUMS": EXPECTED})
    assert not any(r.endswith("(failed)") for r in ran), ran
    assert after == {"SHA256SUMS": WITH_APP, ZIP: ZIP_BYTES}
    assert _uploads(calls) == [ZIP, "SHA256SUMS"]
    clobber = next(c for c in calls if c[:2] == ["release", "upload"] and "SHA256SUMS" in c[-1])
    assert "--clobber" in clobber
    # Read back after writing.
    assert calls[-1][:2] == ["release", "download"]


@posix_only
def test_constructed_a_rerun_after_success_uploads_nothing(tmp_path: Path) -> None:
    ran, after, calls = _run_attach(tmp_path, {"SHA256SUMS": WITH_APP, ZIP: ZIP_BYTES})
    assert not any(r.endswith("(failed)") for r in ran), ran
    assert after == {"SHA256SUMS": WITH_APP, ZIP: ZIP_BYTES}
    assert _uploads(calls) == []


@posix_only
def test_constructed_a_rerun_after_the_zip_went_up_writes_only_the_sums(tmp_path: Path) -> None:
    ran, after, calls = _run_attach(tmp_path, {"SHA256SUMS": EXPECTED, ZIP: ZIP_BYTES})
    assert not any(r.endswith("(failed)") for r in ran), ran
    assert after == {"SHA256SUMS": WITH_APP, ZIP: ZIP_BYTES}
    assert _uploads(calls) == ["SHA256SUMS"]


@posix_only
def test_constructed_a_different_zip_on_the_release_is_refused(tmp_path: Path) -> None:
    other = b"another zip\n"
    ran, after, calls = _run_attach(tmp_path, {"SHA256SUMS": EXPECTED, ZIP: other})
    assert ran[-1] == "Upload the app, unless the release has it (failed)"
    assert after == {"SHA256SUMS": EXPECTED, ZIP: other}
    assert _uploads(calls) == []


@posix_only
def test_constructed_missing_sums_are_written_whole(tmp_path: Path) -> None:
    ran, after, calls = _run_attach(tmp_path, {})
    assert not any(r.endswith("(failed)") for r in ran), ran
    assert after == {"SHA256SUMS": WITH_APP, ZIP: ZIP_BYTES}
    assert _uploads(calls) == [ZIP, "SHA256SUMS"]


@posix_only
@pytest.mark.parametrize(
    "sums",
    [
        b"",
        EXPECTED + b"extra line\n",
        f"{'c' * 64}  {WHEEL}\n{SDIST_SHA}  {SDIST}\n".encode(),
        EXPECTED + f"{'d' * 64}  {ZIP}\n".encode(),
    ],
    ids=["empty", "extra", "other-wheel-hash", "other-zip-line"],
)
def test_constructed_foreign_sums_are_refused_before_any_upload(
    tmp_path: Path, sums: bytes
) -> None:
    ran, after, calls = _run_attach(tmp_path, {"SHA256SUMS": sums})
    assert ran[-1] == "The release's SHA256SUMS is one this job may write (failed)"
    assert after == {"SHA256SUMS": sums}
    assert _uploads(calls) == []


@posix_only
def test_constructed_an_artifact_that_is_not_the_built_zip_is_refused(tmp_path: Path) -> None:
    ran, after, calls = _run_attach(tmp_path, {"SHA256SUMS": EXPECTED}, artifact=b"swapped\n")
    assert ran[-1] == "The zip is the one the build hashed (failed)"
    assert after == {"SHA256SUMS": EXPECTED}
    assert calls == []


@posix_only
def test_constructed_sums_that_read_back_different_fail_the_job(tmp_path: Path) -> None:
    ran, after, _ = _run_attach(tmp_path, {"SHA256SUMS": EXPECTED}, corrupt="SHA256SUMS")
    assert ran[-1] == "Upload SHA256SUMS with the zip's line (failed)"
    assert after["SHA256SUMS"] != WITH_APP


# ─── the smoke test and the recorder ─────────────────────────────────────────


def test_the_recorder_is_an_executable_file() -> None:
    recorder = SCRIPTS / "record_url.py"
    assert recorder.read_text(encoding="utf-8").startswith("#!/usr/bin/env python3\n")
    if os.name != "nt":
        assert recorder.stat().st_mode & stat.S_IXUSR
    assert recorder == app_smoke.RECORDER


def test_the_recorder_writes_the_url_and_exits_0(tmp_path: Path) -> None:
    target = tmp_path / "url.txt"
    url = "http://127.0.0.1:5000/?boot=" + "k" * 43
    done = subprocess.run(
        [sys.executable, str(SCRIPTS / "record_url.py"), url],
        env={**os.environ, "WOWLAB_SMOKE_URL_FILE": str(target)},
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert target.read_text(encoding="utf-8") == url
    assert sorted(p.name for p in tmp_path.iterdir()) == ["url.txt"]


@pytest.mark.parametrize("args", [[], ["a", "b"]])
def test_the_recorder_refuses_anything_but_one_url(tmp_path: Path, args: list[str]) -> None:
    done = subprocess.run(
        [sys.executable, str(SCRIPTS / "record_url.py"), *args],
        env={**os.environ, "WOWLAB_SMOKE_URL_FILE": str(tmp_path / "u")},
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert done.returncode == 2
    assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(os.name == "nt", reason="the stand-in executable is a POSIX shell script")
def test_the_smoke_test_passes_against_the_unfrozen_launcher(tmp_path: Path) -> None:
    # A stand-in for the frozen executable: this interpreter running the
    # launcher, so the smoke script and the launcher's argument handling are
    # graded on every platform CI runs; the frozen app runs the same script.
    from wowlab_core import __version__

    exe = tmp_path / "wowlab"
    exe.write_text(
        f'#!/bin/sh\nexec "{sys.executable}" "{LAUNCHER}" "$@"\n',
        encoding="utf-8",
    )
    exe.chmod(0o755)
    done = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "app_smoke.py"),
            "--exe",
            str(exe),
            "--version",
            __version__,
        ],
        capture_output=True,
        text=True,
        timeout=240,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    for line in (
        f"ok: --version -> wowlab {__version__}",
        f"ok: -P -m wowlab_core --version -> wowlab {__version__}",
        "ok: -P -m wowlab_core with PYTHONIOENCODING=utf-8 writes UTF-8",
        "ok: GET of the link -> 200",
        "ok: a wrong boot key -> 403",
        "ok: doctor ran through the page",
        "ok: wowlab ui stopped on Ctrl-C (exit 0)",
        "app_smoke: every check passed",
    ):
        assert line in done.stdout, done.stdout


def test_constructed_the_smoke_test_fails_on_the_wrong_version(tmp_path: Path) -> None:
    exe = tmp_path / "wowlab"
    if os.name == "nt":
        pytest.skip("the stand-in executable is a POSIX shell script")
    exe.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{LAUNCHER}" "$@"\n', encoding="utf-8")
    exe.chmod(0o755)
    assert app_smoke.main(["--exe", str(exe), "--version", "0.0.0-not-this"]) == 1


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        ("http://127.0.0.1:50123/?boot=" + "A" * 43, True),
        ("http://localhost:50123/?boot=" + "A" * 43, False),
        ("http://127.0.0.1:50123/?token=" + "A" * 43, False),
        ("http://127.0.0.1:50123/?boot=short", False),
        ("https://127.0.0.1:50123/?boot=" + "A" * 43, False),
    ],
)
def test_constructed_only_a_loopback_link_with_a_boot_key_is_accepted(url: str, ok: bool) -> None:
    assert (app_smoke.URL.fullmatch(url) is not None) is ok
