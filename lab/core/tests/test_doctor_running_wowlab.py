"""`doctor` names the wowlab that is running and where it runs from (M16-122).

The owner's `wowlab` on PATH was an installed release while the clone was at
`main`, so every fix tested looked unfixed and nothing said so. `doctor`, as
text and in `--json`, names the running wowlab's version, where its code is
loaded from and whether it is an editable install of a source checkout; it
says in one line when that checkout is at another version, and when the
current folder is in a checkout it does not run from, naming the `uv run`
command that runs that checkout.

Constructed (boundary): every source checkout and Python environment here is
a folder built in `tmp_path` (a checkout is a folder holding
`lab/core/pyproject.toml` for wowlab-core), and what the probe would read
(the version, the install record's `direct_url.json`, the current folder) is
given to `_running_wowlab`, or patched over `_wowlab_probe` for a whole run.
The install for the CLI runs is the committed capture (`fixtures/macos/`)
copied into `tmp_path`. The user data directory is redirected and the
process table holds no client. Nothing reads or writes a real install, and
no network request is made.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import platformdirs
import psutil
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, install

CAPTURE = Path(__file__).resolve().parent / "fixtures" / "macos"
EDITABLE = json.dumps({"url": "file:///x/lab/core", "dir_info": {"editable": True}})
WHEEL = json.dumps({"url": "file:///x/wowlab_core-0.1.0-py3-none-any.whl", "archive_info": {}})

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COLUMNS", "1000")
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: iter(()))
    redirected = tmp_path / "userdata"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: redirected / "wowlab")
    monkeypatch.delenv(install.ENV_ROOT, raising=False)
    monkeypatch.chdir(tmp_path)


def _checkout(folder: Path, version: str) -> Path:
    """A constructed source checkout at `folder`: its project file and the
    package folder; returns the package folder."""
    core = folder / "lab" / "core"
    package = core / "src" / "wowlab_core"
    package.mkdir(parents=True)
    (core / "pyproject.toml").write_text(
        f'[project]\nname = "wowlab-core"\nversion = "{version}"\n', encoding="utf-8"
    )
    return package


def _environment(folder: Path) -> Path:
    """A constructed Python environment holding an installed copy; returns
    its package folder."""
    package = folder / "lib" / "python3.12" / "site-packages" / "wowlab_core"
    package.mkdir(parents=True)
    return package


def _text(w: cli.RunningWowlab, capsys: pytest.CaptureFixture[str]) -> list[str]:
    """What `_print_wowlab` prints, unwrapped (COLUMNS is wide)."""
    cli._print_wowlab(w)
    return capsys.readouterr().out.splitlines()


# ─── the three ways it runs ──────────────────────────────────────────────────


def test_installed_copy_names_its_environment_constructed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env = tmp_path / "tools" / "wowlab-core"
    package = _environment(env)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    w = cli._running_wowlab("0.1.0rc1", package, WHEEL, True, elsewhere, env)
    assert w.install == "installed"
    assert w.version == "0.1.0rc1"
    assert w.package == str(package)
    assert w.location == str(env)
    assert w.checkout is None and w.checkout_version is None
    assert w.other_checkout is None and w.other_checkout_version is None
    assert _text(w, capsys) == [f"wowlab: version 0.1.0rc1, installed in {env}"]


def test_installed_copy_with_no_direct_url_is_installed_constructed(tmp_path: Path) -> None:
    """A record with no direct_url.json (installed from an index) is a copy."""
    env = tmp_path / "env"
    w = cli._running_wowlab("0.1.0", _environment(env), None, True, tmp_path, env)
    assert w.install == "installed"


def test_editable_install_names_its_checkout_constructed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clone = tmp_path / "clone"
    package = _checkout(clone, "0.1.0")
    w = cli._running_wowlab("0.1.0", package, EDITABLE, True, clone / "lab", clone / ".venv")
    assert w.install == "editable"
    assert w.checkout == str(clone) == w.location
    assert w.checkout_version == "0.1.0"
    assert w.other_checkout is None
    # The current folder is in the checkout it runs from: nothing more to say.
    assert _text(w, capsys) == [
        f"wowlab: version 0.1.0, an editable install of the source checkout at {clone}"
    ]


def test_not_installed_says_so_constructed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clone = tmp_path / "clone"
    package = _checkout(clone, "0.1.0")
    w = cli._running_wowlab("0.0.0+unknown", package, None, False, clone, tmp_path / "py")
    assert w.install == "not_installed"
    assert w.checkout == str(clone)
    lines = _text(w, capsys)
    assert lines[0] == f"wowlab: version 0.0.0+unknown, run from {clone}, with no install record"


# ─── the one-line warnings ───────────────────────────────────────────────────


def test_editable_checkout_at_another_version_says_so_in_one_line_constructed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    clone = tmp_path / "clone"
    package = _checkout(clone, "0.2.0")
    w = cli._running_wowlab("0.1.0", package, EDITABLE, True, clone, clone / ".venv")
    assert w.checkout_version == "0.2.0"
    lines = _text(w, capsys)
    assert len(lines) == 2
    assert lines[1] == (
        "  The checkout is at version 0.2.0 now, but this run reports 0.1.0: to run the "
        f"checkout as it is, run `uv run --project {clone} wowlab doctor`."
    )


def test_installed_copy_run_inside_a_checkout_names_the_command_for_it_constructed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The owner's case: the installed release, run inside the clone."""
    env = tmp_path / "tools" / "wowlab-core"
    clone = tmp_path / "clone"
    _checkout(clone, "0.1.0")
    w = cli._running_wowlab("0.1.0rc1", _environment(env), WHEEL, True, clone / "lab", env)
    assert w.other_checkout == str(clone)
    assert w.other_checkout_version == "0.1.0"
    lines = _text(w, capsys)
    assert lines == [
        f"wowlab: version 0.1.0rc1, installed in {env}",
        "  The current folder is in a wowlab source checkout (version 0.1.0), which this "
        "wowlab does not run from: to run that checkout, run "
        f"`uv run --project {clone} wowlab doctor`.",
    ]


def test_editable_install_run_inside_a_different_checkout_constructed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    mine = tmp_path / "mine"
    theirs = tmp_path / "theirs"
    package = _checkout(mine, "0.1.0")
    _checkout(theirs, "0.1.0")
    w = cli._running_wowlab("0.1.0", package, EDITABLE, True, theirs, mine / ".venv")
    assert w.checkout == str(mine)
    assert w.other_checkout == str(theirs)
    lines = _text(w, capsys)
    assert len(lines) == 2
    assert lines[1].endswith(f"`uv run --project {theirs} wowlab doctor`.")


def test_a_folder_that_is_not_a_wowlab_checkout_is_not_one_constructed(tmp_path: Path) -> None:
    """A pyproject.toml at lab/core for another project, or one that does
    not parse, makes no checkout."""
    other = tmp_path / "other"
    (other / "lab" / "core").mkdir(parents=True)
    (other / "lab" / "core" / "pyproject.toml").write_text(
        '[project]\nname = "something-else"\nversion = "9"\n', encoding="utf-8"
    )
    broken = tmp_path / "broken"
    (broken / "lab" / "core").mkdir(parents=True)
    (broken / "lab" / "core" / "pyproject.toml").write_text("[project\n", encoding="utf-8")
    env = tmp_path / "env"
    package = _environment(env)
    for cwd in (other, broken):
        w = cli._running_wowlab("0.1.0", package, WHEEL, True, cwd, env)
        assert w.other_checkout is None


# ─── the whole command ───────────────────────────────────────────────────────


def _install(tmp_path: Path) -> Path:
    root = tmp_path / "World of Warcraft"
    shutil.copytree(CAPTURE, root, symlinks=True)
    return root


def _probe(monkeypatch: pytest.MonkeyPatch, env: Path, cwd: Path) -> None:
    probe = ("0.1.0rc1", _environment(env), WHEEL, True, cwd, env)
    monkeypatch.setattr(cli, "_wowlab_probe", lambda: probe)


def test_doctor_text_and_json_name_the_running_wowlab_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _install(tmp_path)
    env = tmp_path / "tools" / "wowlab-core"
    clone = tmp_path / "clone"
    _checkout(clone, "0.1.0")
    _probe(monkeypatch, env, clone)
    text = runner.invoke(cli.app, ["doctor", "--root", str(root)], terminal_width=1000).stdout
    assert f"wowlab: version 0.1.0rc1, installed in {env}\n" in text
    assert f"`uv run --project {clone} wowlab doctor`" in text
    # wowlab's own lines come just before the snapshot store's.
    lines = text.splitlines()
    store = next(i for i, line in enumerate(lines) if line.startswith("Snapshot store:"))
    assert lines[store - 2].startswith("wowlab: version 0.1.0rc1")
    out = runner.invoke(cli.app, ["doctor", "--root", str(root), "--json"]).stdout
    assert json.loads(out)["wowlab"] == {
        "version": "0.1.0rc1",
        "package": str(env / "lib" / "python3.12" / "site-packages" / "wowlab_core"),
        "install": "installed",
        "location": str(env),
        "checkout": None,
        "checkout_version": None,
        "other_checkout": str(clone),
        "other_checkout_version": "0.1.0",
    }


def test_doctor_redact_covers_the_paths_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "Users" / "Jaina Proudmoore"
    home.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    root = _install(tmp_path)
    env = home / ".local" / "share" / "uv" / "tools" / "wowlab-core"
    clone = home / "src" / "wowlab"
    _checkout(clone, "0.1.0")
    _probe(monkeypatch, env, clone)
    for args in (["--redact"], ["--redact", "--json"]):
        out = runner.invoke(
            cli.app, ["doctor", "--root", str(root), *args], terminal_width=1000
        ).stdout
        assert str(home) not in out
        assert "Jaina" not in out
    report = json.loads(
        runner.invoke(cli.app, ["doctor", "--root", str(root), "--redact", "--json"]).stdout
    )
    w = report["wowlab"]
    assert w["install"] == "installed"
    assert w["version"] == "0.1.0rc1"
    for key in ("package", "location", "other_checkout"):
        assert w[key].startswith("<HOME>"), w[key]
