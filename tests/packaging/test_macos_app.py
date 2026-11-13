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
import os
import re
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
PYINSTALLER = "pyinstaller==6.22.3"
SPEC_SHA256 = "636731bca366c78fca23afa93dd4f9bfb0a64a7d38e13295ce937c36560257a0"


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
    # No network module, no update check: the command and a subprocess for open.
    assert imported == {"__future__", "subprocess", "sys", "collections.abc", "wowlab_core.cli"}


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


APP_OK = "needs.app.result == 'success' && needs.app_smoke.result == 'success'"


def test_the_release_publishes_the_wheel_whatever_became_of_the_app() -> None:
    release = _jobs(RELEASE)["release"]
    assert release["needs"] == ["version", "test", "build", "smoke", "app", "app_smoke"]
    # Every job but the two app jobs has to have succeeded.
    assert " ".join(release["if"].split()) == (
        "${{ !cancelled() && needs.version.result == 'success' && "
        "needs.test.result == 'success' && needs.build.result == 'success' && "
        "needs.smoke.result == 'success' }}"
    )
    assert release["environment"] == "release"


def test_the_release_adds_the_app_only_after_checking_and_attesting_it() -> None:
    release = _jobs(RELEASE)["release"]
    steps = release["steps"]
    env = release["env"]
    assert env["APP_SHA256"] == "${{ needs.app.outputs.app_sha256 }}"
    assert env["APP_ZIP"] == "wowlab-${{ needs.version.outputs.version }}-macos-arm64.zip"
    assert env["APP_BUILT"] == "${{ needs.app.result }}"
    assert env["APP_SMOKED"] == "${{ needs.app_smoke.result }}"
    names = [s.get("name") for s in steps]
    checked = steps[names.index("The app is the one its build hashed")]
    download = steps[steps.index(checked) - 1]
    assert download["uses"].startswith("actions/download-artifact@")
    assert download["with"] == {"name": "app", "path": "app"}
    assert download["if"] == APP_OK
    assert checked["if"] == APP_OK
    run = checked["run"]
    assert 'printf \'%s  %s\\n\' "$APP_SHA256" "$APP_ZIP" > "$RUNNER_TEMP/expected-app"' in run
    assert '(cd app && sha256sum -c "$RUNNER_TEMP/expected-app")' in run
    assert 'cat "$RUNNER_TEMP/expected-app" >> release/SHA256SUMS' in run
    # After the wheel and the sdist are checked against SHA256SUMS as built.
    assert names.index("The files are the ones the build hashed") < steps.index(checked)
    attest = steps[names.index("Attest the app")]
    assert attest["if"] == APP_OK
    assert attest["uses"].startswith("actions/attest-build-provenance@")
    assert attest["with"] == {
        "subject-path": "release/wowlab-${{ needs.version.outputs.version }}-macos-arm64.zip"
    }
    assert steps.index(checked) < steps.index(attest) < names.index("Create the release")
    missing = steps[names.index("Report a release without the app")]
    assert missing["if"] == "needs.app.result != 'success' || needs.app_smoke.result != 'success'"
    assert "::warning" in missing["run"]
    create = steps[names.index("Create the release")]["run"]
    assert (
        'if [ "$APP_BUILT" = "success" ] && [ "$APP_SMOKED" = "success" ]; '
        'then app+=("release/$APP_ZIP"); fi'
    ) in create


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
