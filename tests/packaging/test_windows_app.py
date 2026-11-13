"""Grades for the Windows app build (M17-09; ADR-0038, ADR-0031 amendment of
2026-11-12).

The Windows app is built from the same spec and launcher as the macOS app,
whose pins and launcher rules `test_macos_app.py` grades. Here: the
Windows jobs in release.yml and windows-app.yml are the same steps,
read-only, and build a one-folder x64 `wowlab.exe` from the checked wheel;
the smoke job runs the downloaded zip with no arguments; the two attach jobs
are the same steps, never run at once, and each accepts the SHA256SUMS the
other may have left, whichever ran first (run against a stand-in `gh`); the
smoke script's Windows browser is a one-line `.cmd`; and the launcher
applies `PYTHONIOENCODING` in a frozen build, which ignores it.

Nothing here builds the app, and no test needs Windows: the frozen build is
graded on windows-latest by the workflows.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest
from test_macos_app import (
    APP_LINE,
    APP_WORKFLOW,
    EXPECTED,
    OTHER_BYTES,
    OTHER_LINE,
    OTHER_ZIP,
    RELEASE,
    ROOT,
    WITH_APP,
    ZIP,
    ZIP_BYTES,
    _jobs,
    _run_attach,
    _uploads,
    _workflow,
    app_smoke,
    launcher,
    posix_only,
)

WINDOWS_WORKFLOW = ROOT / ".github" / "workflows" / "windows-app.yml"
WIN_ZIP_ENV = "wowlab-${{ needs.version.outputs.version }}-windows-x64.zip"
MAC_ZIP_ENV = "wowlab-${{ needs.version.outputs.version }}-macos-arm64.zip"


# ─── the build and smoke jobs ────────────────────────────────────────────────


@pytest.mark.parametrize("job", ["windows_app", "windows_app_smoke"])
def test_the_release_and_the_pull_request_build_the_windows_app_the_same_way(job: str) -> None:
    release, by_hand = _jobs(RELEASE)[job], _jobs(WINDOWS_WORKFLOW)[job]
    assert release["steps"] == by_hand["steps"]
    for key in ("runs-on", "timeout-minutes", "outputs", "name", "defaults"):
        assert release.get(key) == by_hand.get(key), key
    assert set(release["env"]) == set(by_hand["env"])
    assert release["runs-on"] == "windows-latest"  # x64
    assert release["defaults"] == {"run": {"shell": "bash"}}


def test_the_windows_workflow_runs_on_a_pull_request_or_by_hand_and_publishes_nothing() -> None:
    data = _workflow(WINDOWS_WORKFLOW)
    triggers = data.get("on", data.get(True))
    assert set(triggers) == {"pull_request", "workflow_dispatch"}
    paths = triggers["pull_request"]["paths"]
    # Every file the app is built or smoke-tested from, so a change to any
    # of them is built on windows-latest before it merges.
    for path in (
        "packaging/**",
        "scripts/app_smoke.py",
        "scripts/record_url.py",
        ".github/workflows/windows-app.yml",
        ".github/workflows/release.yml",
        "pyproject.toml",
        "uv.lock",
    ):
        assert path in paths, path
    assert data["permissions"] == {"contents": "read"}
    jobs = data["jobs"]
    assert set(jobs) == {"wheel", "windows_app", "windows_app_smoke"}
    for name, job in jobs.items():
        assert "permissions" not in job, name
        assert "environment" not in job, name
    text = WINDOWS_WORKFLOW.read_text(encoding="utf-8")
    assert "secrets." not in text
    assert "github.token" not in text
    assert "gh release" not in text
    assert "attest" not in text


def test_the_windows_workflow_builds_the_wheel_as_the_macos_one_does() -> None:
    # And so as release.yml's `build` does (test_macos_app.py checks that).
    assert _jobs(WINDOWS_WORKFLOW)["wheel"] == _jobs(APP_WORKFLOW)["wheel"]


def test_the_windows_build_freezes_the_checked_wheel_into_one_folder() -> None:
    jobs = _jobs(RELEASE)
    app, smoke = jobs["windows_app"], jobs["windows_app_smoke"]
    assert app["needs"] == ["version", "build"]
    assert smoke["needs"] == ["version", "windows_app"]
    for job in (app, smoke):
        assert "permissions" not in job
        assert "environment" not in job
        assert "if" not in job
    steps = app["steps"]
    names = [s.get("name") for s in steps]
    runs = [" ".join(s.get("run", "").split()) for s in steps]
    check = names.index("The wheel is the one the build hashed")
    assert steps[check - 1]["with"] == {"name": "release", "path": "${{ runner.temp }}/release"}
    assert 'sha256sum -c "$RUNNER_TEMP/expected-wheel"' in runs[check]
    assert app["env"]["WHEEL_SHA256"] == "${{ needs.build.outputs.wheel_sha256 }}"
    # The same clean environment as the macOS build, with Windows' paths.
    mac_venv = next(
        s["run"]
        for s in jobs["app"]["steps"]
        if s.get("name") == "A clean virtual environment with the wheel and PyInstaller"
    )
    venv = steps[names.index("A clean virtual environment with the wheel and PyInstaller")]["run"]
    assert venv == mac_venv.replace("venv/bin/python", "venv/Scripts/python.exe")
    build = runs[names.index("PyInstaller, one folder, no UPX")]
    assert '"$GITHUB_WORKSPACE/packaging/wowlab.spec"' in build
    assert "--onefile" not in build and "-F " not in build
    assert 'test -f "$RUNNER_TEMP/dist/wowlab/wowlab.exe"' in build
    assert 'test -d "$RUNNER_TEMP/dist/wowlab/_internal"' in build
    assert "0x8664" in build  # an x64 executable
    assert steps[names.index("PyInstaller, one folder, no UPX")]["working-directory"] == (
        "${{ runner.temp }}"
    )
    # The build job never runs what it built.
    assert not any("app_smoke.py" in r or "wowlab.exe --" in r for r in runs)
    zip_step = steps[names.index("Zip the app and record its SHA-256")]
    assert zip_step["id"] == "zip"
    assert zip_step["working-directory"] == "${{ runner.temp }}/dist"
    assert '-m zipfile -c "$RUNNER_TEMP/app/$APP_ZIP" wowlab' in zip_step["run"]
    assert steps[-1]["uses"].startswith("actions/upload-artifact@")
    assert steps[-1]["with"]["name"] == "windows-app"
    assert app["outputs"] == {"app_sha256": "${{ steps.zip.outputs.app_sha256 }}"}
    assert app["env"]["APP_ZIP"] == WIN_ZIP_ENV


def test_the_windows_smoke_job_runs_the_downloaded_zip_against_the_tag() -> None:
    smoke = _jobs(RELEASE)["windows_app_smoke"]
    steps = smoke["steps"]
    names = [s.get("name") for s in steps]
    check = names.index("The zip is the one the build hashed")
    assert steps[check - 1]["with"]["name"] == "windows-app"
    assert 'sha256sum -c "$RUNNER_TEMP/expected-app"' in steps[check]["run"]
    assert smoke["env"]["APP_SHA256"] == "${{ needs.windows_app.outputs.app_sha256 }}"
    assert smoke["env"]["APP_ZIP"] == WIN_ZIP_ENV
    run = " ".join(steps[names.index("Unzip it and run the smoke test")]["run"].split())
    assert '-m zipfile -e "$RUNNER_TEMP/app/$APP_ZIP" "$RUNNER_TEMP/unzipped"' in run
    assert (
        'scripts/app_smoke.py --exe "$RUNNER_TEMP/unzipped/wowlab/wowlab.exe" --version "$VERSION"'
    ) in run


def test_the_release_never_waits_on_the_windows_app() -> None:
    release = _jobs(RELEASE)["release"]
    assert "windows" not in str(release["needs"])
    assert "windows-x64" not in str(release)


# ─── the two attach jobs ─────────────────────────────────────────────────────


def test_the_two_attach_jobs_are_the_same_steps_but_for_their_zip() -> None:
    jobs = _jobs(RELEASE)
    mac, win = jobs["app_release"], jobs["windows_app_release"]
    assert win["needs"] == ["version", "build", "windows_app", "windows_app_smoke", "release"]
    assert "if" not in win and "continue-on-error" not in win and "environment" not in win
    assert win["permissions"] == mac["permissions"]
    assert win["runs-on"] == mac["runs-on"] == "ubuntu-latest"
    assert set(win["env"]) == set(mac["env"])
    assert win["env"]["APP_ZIP"] == mac["env"]["OTHER_ZIP"] == WIN_ZIP_ENV
    assert mac["env"]["APP_ZIP"] == win["env"]["OTHER_ZIP"] == MAC_ZIP_ENV
    # Each zip's hash comes from its own build job's output.
    assert win["env"]["APP_SHA256"] == "${{ needs.windows_app.outputs.app_sha256 }}"
    assert mac["env"]["APP_SHA256"] == "${{ needs.app.outputs.app_sha256 }}"
    assert len(win["steps"]) == len(mac["steps"])
    for w, m in zip(win["steps"], mac["steps"], strict=True):
        if w.get("uses", "").startswith("actions/download-artifact@"):
            assert w["with"] == {"name": "windows-app", "path": "app"}
            assert m["with"] == {"name": "app", "path": "app"}
        elif w.get("uses", "").startswith("actions/attest-build-provenance@"):
            assert w["uses"] == m["uses"]
            assert w["with"] == {"subject-path": f"app/{WIN_ZIP_ENV}"}
        else:
            assert w == m


def test_the_two_attach_jobs_never_run_at_once() -> None:
    data = _workflow(RELEASE)
    jobs = data["jobs"]
    group = {"group": "release-apps-${{ github.ref }}", "cancel-in-progress": False}
    assert jobs["app_release"]["concurrency"] == group
    assert jobs["windows_app_release"]["concurrency"] == group
    # A job group equal to the workflow's own would wait on its own run.
    assert data["concurrency"]["group"] != group["group"]
    assert [n for n, j in jobs.items() if "concurrency" in j] == [
        "app_release",
        "windows_app_release",
    ]


# ── run: both attach jobs against a stand-in `gh`, in either order ──
# `constructed`, as in test_macos_app.py: a release's assets as files, each
# zip a few bytes.

WIN_ZIP = OTHER_ZIP
WIN_BYTES = OTHER_BYTES
WIN_LINE = OTHER_LINE


def _run_windows(tmp_path: Path, published: dict[str, bytes], **kw: Any) -> Any:
    return _run_attach(
        tmp_path,
        published,
        job="windows_app_release",
        zip_name=WIN_ZIP,
        zip_bytes=WIN_BYTES,
        other_zip=ZIP,
        artifact=kw.pop("artifact", WIN_BYTES),
        **kw,
    )


def _sub(tmp_path: Path, name: str) -> Path:
    folder = tmp_path / name
    folder.mkdir()
    return folder


def _ok(ran: list[str]) -> bool:
    return not any(r.endswith("(failed)") for r in ran)


@posix_only
def test_constructed_mac_first_then_windows(tmp_path: Path) -> None:
    ran, after, _ = _run_attach(_sub(tmp_path, "mac"), {"SHA256SUMS": EXPECTED})
    assert _ok(ran), ran
    ran, after, calls = _run_windows(_sub(tmp_path, "win"), after)
    assert _ok(ran), ran
    assert after == {
        "SHA256SUMS": EXPECTED + APP_LINE + WIN_LINE,
        ZIP: ZIP_BYTES,
        WIN_ZIP: WIN_BYTES,
    }
    assert _uploads(calls) == [WIN_ZIP, "SHA256SUMS"]
    # The macOS line was checked against the macOS zip on the release.
    assert (next(c for c in calls if ZIP in c)[:7]) == [
        "release",
        "download",
        "v9.9.9",
        "--repo",
        "owner/repo",
        "--pattern",
        ZIP,
    ]


@posix_only
def test_constructed_windows_first_then_mac(tmp_path: Path) -> None:
    ran, after, _ = _run_windows(_sub(tmp_path, "win"), {"SHA256SUMS": EXPECTED})
    assert _ok(ran), ran
    assert after["SHA256SUMS"] == EXPECTED + WIN_LINE
    ran, after, calls = _run_attach(_sub(tmp_path, "mac"), after)
    assert _ok(ran), ran
    assert after == {
        "SHA256SUMS": EXPECTED + WIN_LINE + APP_LINE,
        ZIP: ZIP_BYTES,
        WIN_ZIP: WIN_BYTES,
    }
    assert _uploads(calls) == [ZIP, "SHA256SUMS"]


@posix_only
@pytest.mark.parametrize(
    "sums",
    [EXPECTED + APP_LINE + WIN_LINE, EXPECTED + WIN_LINE + APP_LINE],
    ids=["mac-then-windows", "windows-then-mac"],
)
@pytest.mark.parametrize("job", ["app_release", "windows_app_release"])
def test_constructed_a_rerun_after_both_uploads_nothing(
    tmp_path: Path, sums: bytes, job: str
) -> None:
    published = {"SHA256SUMS": sums, ZIP: ZIP_BYTES, WIN_ZIP: WIN_BYTES}
    if job == "app_release":
        ran, after, calls = _run_attach(tmp_path, published)
    else:
        ran, after, calls = _run_windows(tmp_path, published)
    assert _ok(ran), ran
    assert after == published
    assert _uploads(calls) == []


@posix_only
def test_constructed_windows_alone_when_the_mac_build_failed(tmp_path: Path) -> None:
    # The macOS build or smoke test failed: app_release was skipped, and the
    # Windows app is attached on its own.
    ran, after, calls = _run_windows(tmp_path, {"SHA256SUMS": EXPECTED})
    assert _ok(ran), ran
    assert after == {"SHA256SUMS": EXPECTED + WIN_LINE, WIN_ZIP: WIN_BYTES}
    assert _uploads(calls) == [WIN_ZIP, "SHA256SUMS"]


@posix_only
@pytest.mark.parametrize(
    ("published", "why"),
    [
        # The other app's line, but no zip of that name on the release.
        ({"SHA256SUMS": EXPECTED + WIN_LINE}, "zip missing"),
        # The other app's line, and a different zip of that name.
        ({"SHA256SUMS": EXPECTED + WIN_LINE, WIN_ZIP: b"other bytes\n"}, "zip differs"),
        # The other app's line twice.
        (
            {"SHA256SUMS": EXPECTED + WIN_LINE + WIN_LINE, WIN_ZIP: WIN_BYTES},
            "twice",
        ),
        # This app's line twice.
        ({"SHA256SUMS": WITH_APP + APP_LINE, ZIP: ZIP_BYTES}, "own line twice"),
        # A line for a zip neither build makes.
        (
            {"SHA256SUMS": EXPECTED + f"{'e' * 64}  wowlab-9.9.9-linux.zip\n".encode()},
            "unknown zip",
        ),
        # The wheel's and the sdist's lines after an app line.
        (
            {"SHA256SUMS": WIN_LINE + EXPECTED, WIN_ZIP: WIN_BYTES},
            "build lines not first",
        ),
        # No newline after the last line.
        ({"SHA256SUMS": EXPECTED + WIN_LINE.rstrip(b"\n"), WIN_ZIP: WIN_BYTES}, "no newline"),
        # A Windows line ending.
        (
            {"SHA256SUMS": EXPECTED + WIN_LINE.replace(b"\n", b"\r\n"), WIN_ZIP: WIN_BYTES},
            "crlf",
        ),
    ],
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_constructed_sums_the_builds_did_not_write_are_refused(
    tmp_path: Path, published: dict[str, bytes], why: str
) -> None:
    ran, after, calls = _run_attach(tmp_path, published)
    assert ran[-1] == "The release's SHA256SUMS is one this job may write (failed)", why
    assert after == published
    assert _uploads(calls) == []


@posix_only
def test_constructed_a_windows_artifact_that_is_not_the_built_zip_is_refused(
    tmp_path: Path,
) -> None:
    ran, after, calls = _run_windows(tmp_path, {"SHA256SUMS": EXPECTED}, artifact=b"swapped\n")
    assert ran[-1] == "The zip is the one the build hashed (failed)"
    assert after == {"SHA256SUMS": EXPECTED}
    assert calls == []


# ─── the smoke script's browser on Windows ───────────────────────────────────


def test_the_browser_is_the_recorder_itself_off_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app_smoke, "WINDOWS", False)
    assert app_smoke.browser_for(tmp_path) == str(app_smoke.RECORDER)
    assert list(tmp_path.iterdir()) == []


def test_the_browser_on_windows_is_a_one_line_cmd_that_runs_the_recorder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # webbrowser runs the whole BROWSER value as one program, and on Windows
    # a .py file is not one (PR #525's note): a .cmd is, and `%*` passes the
    # URL whole (`%1` would end at its `=`).
    monkeypatch.setattr(app_smoke, "WINDOWS", True)
    monkeypatch.setattr(app_smoke.sys, "executable", r"C:\py\python.exe")
    browser = Path(app_smoke.browser_for(tmp_path))
    assert browser == tmp_path / "record_url.cmd"
    assert browser.read_bytes() == (f'@"C:\\py\\python.exe" "{app_smoke.RECORDER}" %*\r\n'.encode())


class _Server:
    """A stand-in for the server process `_stop` stops (constructed)."""

    def __init__(self, *, refuse: bool = False, code: int = 3221225786) -> None:
        self.signals: list[object] = []
        self.terminated = False
        self.refuse = refuse
        self.code = code
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode

    def send_signal(self, sig: object) -> None:
        if self.refuse:
            raise OSError("no console")
        self.signals.append(sig)

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: float | None = None) -> int:
        self.returncode = self.code
        return self.code

    def kill(self) -> None:
        raise AssertionError("not killed")

    def communicate(self) -> tuple[bytes, bytes]:
        return b"", b""


def test_constructed_windows_stops_the_server_with_ctrl_break(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(app_smoke, "WINDOWS", True)
    monkeypatch.setattr(app_smoke.signal, "CTRL_BREAK_EVENT", 1, raising=False)
    server = _Server()
    assert app_smoke._stop(server) == 3221225786  # type: ignore[arg-type]
    assert server.signals == [1]
    assert not server.terminated


def test_constructed_windows_without_a_console_terminates_and_says_so(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(app_smoke, "WINDOWS", True)
    monkeypatch.setattr(app_smoke.signal, "CTRL_BREAK_EVENT", 1, raising=False)
    server = _Server(refuse=True, code=1)
    assert app_smoke._stop(server) == 1  # type: ignore[arg-type]
    assert server.terminated
    assert "Ctrl-Break could not be sent" in capsys.readouterr().out


# ─── the launcher in a frozen build: PYTHONIOENCODING ────────────────────────


class _Recon:
    def __init__(self, fail: Exception | None = None) -> None:
        self.calls: list[dict[str, str]] = []
        self.fail = fail

    def reconfigure(self, **options: str) -> None:
        self.calls.append(options)
        if self.fail is not None:
            raise self.fail


def test_pythonioencoding_reconfigures_the_standard_streams() -> None:
    streams = {name: _Recon() for name in ("stdin", "stdout", "stderr")}
    launcher.apply_io_encoding({"PYTHONIOENCODING": "utf-8"}, streams.items())
    assert {n: s.calls for n, s in streams.items()} == {
        "stdin": [{"encoding": "utf-8"}],
        "stdout": [{"encoding": "utf-8"}],
        "stderr": [{"encoding": "utf-8"}],
    }


def test_pythonioencoding_errors_leave_stderr_its_own_handler() -> None:
    streams = {name: _Recon() for name in ("stdout", "stderr")}
    launcher.apply_io_encoding({"PYTHONIOENCODING": "utf-8:replace"}, streams.items())
    assert streams["stdout"].calls == [{"encoding": "utf-8", "errors": "replace"}]
    assert streams["stderr"].calls == [{"encoding": "utf-8"}]
    only_errors = _Recon()
    launcher.apply_io_encoding({"PYTHONIOENCODING": ":replace"}, [("stdout", only_errors)])
    assert only_errors.calls == [{"errors": "replace"}]


@pytest.mark.parametrize("environ", [{}, {"PYTHONIOENCODING": ""}])
def test_no_pythonioencoding_changes_nothing(environ: dict[str, str]) -> None:
    stream = _Recon()
    launcher.apply_io_encoding(environ, [("stdout", stream)])
    assert stream.calls == []


def test_a_stream_that_cannot_be_reconfigured_is_left_alone() -> None:
    failing = _Recon(LookupError("no such codec"))
    after = _Recon()
    launcher.apply_io_encoding(
        {"PYTHONIOENCODING": "no-such-codec"},
        [("stdout", failing), ("stdin", None), ("stderr", after)],
    )
    assert after.calls == [{"encoding": "no-such-codec"}]


def test_a_real_text_stream_is_reencoded() -> None:
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252", errors="backslashreplace")
    launcher.apply_io_encoding({"PYTHONIOENCODING": "utf-8"}, [("stdout", stream)])
    stream.write("\u00e9\u2713")
    stream.flush()
    assert raw.getvalue() == "\u00e9\u2713".encode()


@pytest.mark.parametrize("frozen", [True, False])
def test_main_applies_pythonioencoding_only_when_frozen(
    monkeypatch: pytest.MonkeyPatch, frozen: bool
) -> None:
    applied: list[object] = []
    monkeypatch.setattr(launcher, "apply_io_encoding", lambda *a: applied.append(a))
    if frozen:
        monkeypatch.setattr(launcher.sys, "frozen", True, raising=False)
    else:
        monkeypatch.delattr(launcher.sys, "frozen", raising=False)
    import wowlab_core.cli

    ran: list[list[str]] = []
    monkeypatch.setattr(
        wowlab_core.cli, "app", lambda args, prog_name: ran.append(list(args)), raising=True
    )
    assert launcher.main(["--version"]) == 0
    assert ran == [["--version"]]
    assert len(applied) == (1 if frozen else 0)
