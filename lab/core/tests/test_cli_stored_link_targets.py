"""Stored link targets print plain on Windows (M13-52, owner decision 2026-10-18).

A snapshot manifest keeps a link's target as `readlink` answered it: on
Windows an absolute target comes back as `\\\\?\\C:\\...`, and that raw text
stays in the manifest, which `guard` and `profiles` compare raw to raw. Every
place a stored target is shown prints it through `longpath.plain_text`, as
`tree` shows a live link since M13-43: on Windows only, a relative target as
stored, and off Windows byte for byte what `main` printed.

The install is the captured tree (`fixtures/macos/`) copied into `tmp_path`,
as in `test_cli.py`; the user data directory is redirected there and the
process table is a fake one with no client. The links and the `\\\\?\\` text
`readlink` answers for them are constructed (L8: a boundary case), stood in
on every host by replacing `longpath.readlink` while the snapshot is taken
and `longpath.ON_WINDOWS` while it is shown. The last test makes a real
junction on Windows, where `readlink` answers with the prefix itself.
"""

from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import platformdirs
import psutil
import pytest
from typer.testing import CliRunner

from wowlab_core import cli, install, longpath, snapshot
from wowlab_core.snapshot import Manifest

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CAPTURE = FIXTURES / "macos"
FLAVOR = "_classic_beta_"  # the provenance row's flavor folder; tests may name it (L6)
EXTENDED = "\\\\?\\"

DRIVE_RAW = "\\\\?\\C:\\Games\\World of Warcraft\\Logs"
DRIVE_SHOWN = "C:\\Games\\World of Warcraft\\Logs"
UNC_RAW = "\\\\?\\UNC\\server\\share\\Logs"
UNC_SHOWN = "\\\\server\\share\\Logs"
RELATIVE = "..\\..\\Logs"

# Link names (flavor-relative) and the text `readlink` answers for each.
LINKS = {
    "WTF/drive-constructed": DRIVE_RAW,
    "WTF/unc-constructed": UNC_RAW,
    "WTF/relative-constructed": RELATIVE,
}
ADDON_LINK = "Interface/AddOns/Linked-constructed"

runner = CliRunner()


class _FakeProcess:
    """The `psutil.Process` surface `wowlab_core.process` reads."""

    def __init__(self, pid: int, name: str, exe: str) -> None:
        self._pid, self._name, self._exe = pid, name, exe

    @property
    def pid(self) -> int:
        return self._pid

    def name(self) -> str:
        return self._name

    def exe(self) -> str:
        return self._exe

    def cmdline(self) -> list[str]:
        return [self._exe]


@pytest.fixture(autouse=True)
def user_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    redirected = tmp_path / "userdata"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: redirected / "wowlab")
    return redirected / "wowlab"


@pytest.fixture(autouse=True)
def idle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A process table with no client in it."""
    table = [_FakeProcess(101, "python3", str(tmp_path / "elsewhere" / "python3"))]
    monkeypatch.setattr(psutil, "process_iter", lambda *a, **k: iter(table))


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The captured tree as an install, named by `WOWLAB_WOW_ROOT`."""
    root = tmp_path / "World of Warcraft"
    root.mkdir()
    shutil.copy2(CAPTURE / ".build.info", root / ".build.info")
    shutil.copytree(CAPTURE / "forever", root / FLAVOR)
    monkeypatch.setenv(install.ENV_ROOT, str(root))
    monkeypatch.chdir(tmp_path)
    return root


def run(*args: str) -> Any:
    return runner.invoke(cli.app, list(args))


def ok(*args: str) -> Any:
    result = run(*args)
    assert result.exit_code == 0, (result.stdout, result.stderr, result.exception)
    return result


def _symlink(link: Path, to: Path) -> None:
    try:
        link.symlink_to(to, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:  # Windows without the privilege
        pytest.skip(f"cannot create a symlink here: {exc}")


def _linked(root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Constructed: three links in `WTF/` and one addon folder that is a
    link, all to a folder outside the install; `longpath.readlink` answers
    for each the text in `LINKS` (the addon link: `DRIVE_RAW`), as Windows
    would, and for anything else what the platform answers."""
    outside = tmp_path / "outside-constructed"
    outside.mkdir()
    flavor = root / FLAVOR
    answers: dict[str, str] = {}
    for rel, text in {**LINKS, ADDON_LINK: DRIVE_RAW}.items():
        _symlink(flavor / rel, outside)
        answers[os.path.normcase(os.fspath(flavor / rel))] = text
    real: Callable[[str | os.PathLike[str]], Path] = longpath.readlink

    def readlink(path: str | os.PathLike[str]) -> Path:
        text = answers.get(os.path.normcase(os.fspath(path)))
        return Path(text) if text is not None else real(path)

    monkeypatch.setattr(longpath, "readlink", readlink)


def _snapshot(root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Manifest:
    """A snapshot of the constructed links; its manifest stores the raw text."""
    _linked(root, tmp_path, monkeypatch)
    manifest = Manifest.model_validate_json(ok("snap", "create", "-m", "links", "--json").stdout)
    stored = {e.path.removeprefix(f"{FLAVOR}/"): e.target for e in manifest.entries}
    assert {rel: stored.get(rel) for rel in LINKS} == LINKS, "the manifest keeps the raw text"
    assert stored[ADDON_LINK] == DRIVE_RAW
    return manifest


def _windows(monkeypatch: pytest.MonkeyPatch, on: bool) -> None:
    monkeypatch.setattr(longpath, "ON_WINDOWS", on)


def _link_line(rel: str, shown: str) -> str:
    return f"  {FLAVOR}/{rel} -> {shown} (link, recorded, not followed)"


# ─── snap show ───────────────────────────────────────────────────────────────


def test_snap_show_prints_a_stored_target_plain_on_windows_constructed(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = _snapshot(root, tmp_path, monkeypatch)
    _windows(monkeypatch, True)
    lines = ok("snap", "show", manifest.id).stdout.splitlines()
    assert _link_line("WTF/drive-constructed", DRIVE_SHOWN) in lines
    assert _link_line("WTF/unc-constructed", UNC_SHOWN) in lines
    assert _link_line("WTF/relative-constructed", RELATIVE) in lines, "relative: as stored"
    assert not any(EXTENDED in line for line in lines), lines


def test_snap_show_json_is_the_manifest_with_the_raw_target_constructed(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`snap show --json` is the manifest in its one canonical encoding: the
    raw target, on Windows too (the manifest is evidence of what the disk
    said; the display is what changes)."""
    manifest = _snapshot(root, tmp_path, monkeypatch)
    _windows(monkeypatch, True)
    shown = ok("snap", "show", manifest.id, "--json").stdout
    assert shown.encode("ascii") == snapshot.manifest_bytes(manifest)
    assert snapshot.SnapshotStore().show(manifest.id) == manifest
    assert snapshot.SnapshotStore().verify().ok, "the manifest verifies as written"


def test_snap_show_off_windows_prints_the_stored_text_constructed(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Off Windows a target that begins `\\\\?\\` is a name like any other:
    printed byte for byte as stored, as `main` printed it."""
    manifest = _snapshot(root, tmp_path, monkeypatch)
    _windows(monkeypatch, False)
    lines = ok("snap", "show", manifest.id).stdout.splitlines()
    for rel, text in LINKS.items():
        assert _link_line(rel, text) in lines


# ─── profiles ────────────────────────────────────────────────────────────────


def test_profile_save_and_show_print_a_stored_target_plain_on_windows_constructed(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A saved subtree that is a link is held as the link (its manifest
    entry): `profile save` and `profile show` print its target as
    `snap show` does."""
    _linked(root, tmp_path, monkeypatch)
    saved = ok("profile", "save", "linked", "--subtree", ADDON_LINK).stdout.splitlines()
    on_this_host = DRIVE_SHOWN if longpath.ON_WINDOWS else DRIVE_RAW
    assert _link_line(ADDON_LINK, on_this_host) in saved, "as this platform shows it"
    report = cli.ProfileReport.model_validate_json(ok("profile", "show", "linked", "--json").stdout)
    assert [e.target for e in report.entries if e.kind == "symlink"] == [DRIVE_RAW]
    _windows(monkeypatch, True)
    shown = ok("profile", "show", "linked").stdout.splitlines()
    assert _link_line(ADDON_LINK, DRIVE_SHOWN) in shown
    assert not any(EXTENDED in line for line in shown), shown
    again = cli.ProfileReport.model_validate_json(ok("profile", "show", "linked", "--json").stdout)
    assert again == report, "--json keeps the raw string, on Windows too"


# ─── a real junction on Windows ──────────────────────────────────────────────


@pytest.mark.skipif(sys.platform != "win32", reason="a junction is a Windows link")
def test_a_real_junction_is_stored_raw_and_shown_plain(root: Path, tmp_path: Path) -> None:
    """On Windows `readlink` answers a junction's target with the
    extended-length prefix: the manifest keeps that text, `snap show` prints
    the plain spelling, and the manifest verifies."""
    import _winapi  # Windows only

    outside = tmp_path / "outside"
    outside.mkdir()
    link = root / FLAVOR / "WTF" / "junction"
    _winapi.CreateJunction(str(outside), str(link))
    manifest = Manifest.model_validate_json(ok("snap", "create", "--json").stdout)
    entry = manifest.entry(f"{FLAVOR}/WTF/junction")
    assert entry is not None and entry.kind == "symlink"
    assert entry.target == str(longpath.readlink(link)), "the raw text, as `readlink` answers"
    assert entry.target.startswith(EXTENDED), entry.target
    plain = longpath.plain_text(entry.target)
    assert os.path.normcase(plain) == os.path.normcase(str(outside.resolve()))
    lines = ok("snap", "show", manifest.id).stdout.splitlines()
    assert _link_line("WTF/junction", plain) in lines
    assert not any(EXTENDED in line for line in lines), lines
    assert snapshot.SnapshotStore().verify().ok
