"""`wowlab snap restore` of a snapshot taken with `--screenshots` (M13-30).

`snap create --screenshots` also captures `Screenshots/`, whose file-map row
has Edit `n/a`: the write gate never writes there. A whole restore leaves the
screenshots out, lists them in `RestoreReport.unrestorable` apart from the
Edit `no` skips, and restores the WTF, Interface and Fonts files; naming a
screenshot with `--paths` is refused with the gate's reason (exit 3).

The install is the captured tree as `test_cli.py` builds it (copied into
`tmp_path`, the user data directory redirected, the process table a fake).
Inputs labelled `constructed` in the test names (L8): a font file under
`Fonts/` and screenshot files under `Screenshots/`, which the capture does
not have, edited copies of captured files standing for a later session, a
symbolic link to a folder outside the install, and an addon's `build.sh`.
Nothing reads or writes a real install.
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from test_cli import (
    CAPTURE,
    SYNDICATOR,
    _flip_syndicator,
    _json,
    _plain,
    flavor,  # noqa: F401
    idle,  # noqa: F401  (autouse: no client in the process table)
    ok,
    replayed,  # noqa: F401  (autouse: recorded game data only)
    root,  # noqa: F401
    run,
    user_data,  # noqa: F401
)

from wowlab_core import cli, guard
from wowlab_core.snapshot import Manifest

TOC = "Interface/AddOns/DBM-Brawlers/DBM-Brawlers.toc"
FONT = "Fonts/LABFONT.TTF"
SHOT_A = "Screenshots/WoWScrnShot_101626_120000.jpg"
SHOT_B = "Screenshots/WoWScrnShot_101626_120500.tga"
FONT_BYTES = b"constructed font bytes\x00\x01"
JPEG = b"\xff\xd8\xff\xe0constructed jpeg\xff\xd9"
TGA = b"\x00\x00\x02constructed tga"


def _screenshots_snapshot(flavor: Path) -> Manifest:
    """A font and two screenshots added to the capture, then `snap create --screenshots`."""
    for rel, data in ((FONT, FONT_BYTES), (SHOT_A, JPEG), (SHOT_B, TGA)):
        path = flavor / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    result = ok("snap", "create", "--screenshots", "-m", "with shots", "--json")
    manifest = Manifest.model_validate_json(result.stdout)
    held = {e.path.split("/", 1)[1] for e in manifest.entries}
    assert {SHOT_A, SHOT_B, FONT, TOC, SYNDICATOR} <= held
    return manifest


def _later_session(flavor: Path) -> tuple[bytes, bytes]:
    """WTF, Interface and Fonts files and both screenshots changed since the snapshot."""
    _flip_syndicator(flavor)
    toc = flavor / TOC
    toc.write_bytes(toc.read_bytes() + b"\n## X-Constructed: 1\n")
    (flavor / FONT).write_bytes(b"constructed later font")
    (flavor / SHOT_A).write_bytes(b"constructed later jpeg")
    (flavor / SHOT_B).unlink()
    return (flavor / SHOT_A).read_bytes(), (flavor / FONT).read_bytes()


def test_whole_restore_of_a_screenshots_snapshot_restores_the_rest_constructed(
    root: Path, flavor: Path
) -> None:
    a = _screenshots_snapshot(flavor)
    syndicator = (flavor / SYNDICATOR).read_bytes()
    toc = (flavor / TOC).read_bytes()
    later_shot, _ = _later_session(flavor)

    plan = _json(cli.RestoreReport, "snap", "restore", a.id, "--dry-run")
    assert sorted(i.path for i in plan.plan) == sorted([FONT, TOC, SYNDICATOR])
    assert plan.skipped == []
    assert [(u.path, u.entry_id) for u in plan.unrestorable] == [
        (SHOT_A, "screenshots"),
        (SHOT_B, "screenshots"),
    ]
    assert not plan.applied and plan.dry_run

    text = _plain(ok("snap", "restore", a.id, "--dry-run").stdout)
    assert "3 change(s)" in text
    assert (
        '  Not restored: 2 file(s) the write gate never writes (file-map Edit "n/a": '
        "screenshots, captured with snap create --screenshots; --paths cannot restore "
        "them either):"
    ) in text
    assert f"    {SHOT_A}  [screenshots]" in text
    assert "name one with --paths" not in text
    assert "Skipped" not in text

    done = _json(cli.RestoreReport, "snap", "restore", a.id, "--yes")
    assert done.applied and done.transaction is not None
    assert [u.path for u in done.unrestorable] == [SHOT_A, SHOT_B]
    assert (flavor / SYNDICATOR).read_bytes() == syndicator
    assert (flavor / TOC).read_bytes() == toc
    assert (flavor / FONT).read_bytes() == FONT_BYTES
    # The screenshots are as the later session left them: never written.
    assert (flavor / SHOT_A).read_bytes() == later_shot
    assert not (flavor / SHOT_B).exists()
    (record,) = guard.history()
    assert record.id == done.transaction
    assert sorted(p.path for p in record.paths) == sorted([FONT, TOC, SYNDICATOR])


def test_whole_restore_text_restores_and_names_the_screenshots_constructed(
    root: Path, flavor: Path
) -> None:
    a = _screenshots_snapshot(flavor)
    _later_session(flavor)
    result = ok("snap", "restore", a.id, "--yes")
    out = _plain(result.stdout)
    assert "Not restored: 2 file(s) the write gate never writes" in out
    assert "Restored 3 file(s)." in out
    assert (flavor / FONT).read_bytes() == FONT_BYTES


def test_restore_naming_a_screenshot_is_refused_by_the_gate_constructed(
    root: Path, flavor: Path
) -> None:
    a = _screenshots_snapshot(flavor)
    _later_session(flavor)
    result = run("snap", "restore", a.id, "--paths", SHOT_A, "--yes")
    assert result.exit_code == 3, (result.stdout, result.stderr)
    err = _plain(result.stderr)
    assert "refused by the write gate" in err
    assert f"{SHOT_A!r} is outside the allowlist" in err
    assert (flavor / SHOT_A).read_bytes() == b"constructed later jpeg"
    assert guard.history() == ()


def test_restore_naming_a_screenshot_with_others_is_refused_whole_constructed(
    root: Path, flavor: Path
) -> None:
    a = _screenshots_snapshot(flavor)
    _later_session(flavor)
    font_now = (flavor / FONT).read_bytes()
    result = run("snap", "restore", a.id, "--paths", FONT, "--paths", SHOT_A, "--dry-run")
    assert result.exit_code == 3, (result.stdout, result.stderr)
    assert "is outside the allowlist" in _plain(result.stderr)
    assert (flavor / FONT).read_bytes() == font_now


def test_whole_restore_with_only_screenshots_changed_writes_nothing_constructed(
    root: Path, flavor: Path
) -> None:
    a = _screenshots_snapshot(flavor)
    (flavor / SHOT_A).write_bytes(b"constructed later jpeg")
    result = ok("snap", "restore", a.id, "--yes")
    out = _plain(result.stdout)
    assert "0 change(s)" in out
    assert "Not restored: 2 file(s)" in out
    assert (
        "Nothing to restore: what the write gate can write in _classic_beta_ already "
        f"matches snapshot {a.id}; nothing was changed."
    ) in out
    assert guard.history() == ()
    report = _json(cli.RestoreReport, "snap", "restore", a.id, "--yes")
    assert report.plan == [] and not report.applied and len(report.unrestorable) == 2


def test_edit_no_skips_and_screenshots_are_reported_apart_constructed(
    root: Path, flavor: Path
) -> None:
    bak = "WTF/Account/90000001#6/SavedVariables/RareScanner.lua.bak"
    a = _screenshots_snapshot(flavor)
    (flavor / bak).write_bytes(b"constructed later backup\n")
    _later_session(flavor)
    plan = _json(cli.RestoreReport, "snap", "restore", a.id, "--dry-run")
    assert [(s.path, s.entry_id) for s in plan.skipped] == [(bak, "savedvariables-backup")]
    assert [u.path for u in plan.unrestorable] == [SHOT_A, SHOT_B]
    text = _plain(ok("snap", "restore", a.id, "--dry-run").stdout)
    (skipped_line,) = [ln for ln in text.splitlines() if ln.startswith("  Skipped ")]
    assert "name one with --paths to restore it" in skipped_line
    assert SHOT_A not in text.split("  Not restored:", 1)[0]
    ok("snap", "restore", a.id, "--yes")
    assert (flavor / bak).read_bytes() == b"constructed later backup\n"
    assert (flavor / FONT).read_bytes() == FONT_BYTES


def test_many_screenshots_are_counted_and_the_text_names_ten_constructed(
    root: Path, flavor: Path
) -> None:
    shots = flavor / "Screenshots"
    shots.mkdir()
    for n in range(13):
        (shots / f"WoWScrnShot_101626_1200{n:02d}.jpg").write_bytes(JPEG + bytes([n]))
    a = Manifest.model_validate_json(ok("snap", "create", "--screenshots", "--json").stdout)
    _flip_syndicator(flavor)
    text = _plain(ok("snap", "restore", a.id, "--dry-run").stdout)
    assert "Not restored: 13 file(s)" in text
    assert sum("[screenshots]" in ln for ln in text.splitlines()) == 10
    assert "    and 3 more (--json lists every one)" in text
    report = _json(cli.RestoreReport, "snap", "restore", a.id, "--dry-run")
    assert len(report.unrestorable) == 13
    assert [i.path for i in report.plan] == [SYNDICATOR]


def test_a_snapshot_without_screenshots_lists_none(root: Path, flavor: Path) -> None:
    a = Manifest.model_validate_json(ok("snap", "create", "--json").stdout)
    _flip_syndicator(flavor)
    report = _json(cli.RestoreReport, "snap", "restore", a.id, "--yes")
    assert report.unrestorable == [] and report.applied
    assert (flavor / SYNDICATOR).read_bytes() == (CAPTURE / "forever" / SYNDICATOR).read_bytes()


@pytest.mark.skipif(sys.platform == "win32", reason="symbolic links need privileges on Windows")
def test_an_unchanged_link_is_left_alone_as_a_whole_restore_leaves_it_constructed(
    root: Path, flavor: Path, tmp_path: Path
) -> None:
    """A whole restore leaves an unchanged link alone; with the screenshots set
    aside the rest is restored by name, which must not turn that into a refusal."""
    work = tmp_path / "work-constructed"
    work.mkdir()
    (work / "MyAddon.toc").write_bytes(b"## Interface: 1\n")
    link = flavor / "Interface" / "AddOns" / "MyAddon-dev"
    link.symlink_to(work, target_is_directory=True)
    a = _screenshots_snapshot(flavor)
    assert any(e.kind == "symlink" for e in a.entries)
    _later_session(flavor)
    report = _json(cli.RestoreReport, "snap", "restore", a.id, "--yes")
    assert report.applied
    assert sorted(i.path for i in report.plan) == sorted([FONT, TOC, SYNDICATOR])
    assert link.is_symlink() and link.readlink() == work


@pytest.mark.skipif(sys.platform == "win32", reason="symbolic links need privileges on Windows")
def test_a_changed_link_refuses_the_restore_as_a_whole_restore_does_constructed(
    root: Path, flavor: Path, tmp_path: Path
) -> None:
    work = tmp_path / "work-constructed"
    other = tmp_path / "other-constructed"
    work.mkdir()
    other.mkdir()
    link = flavor / "Interface" / "AddOns" / "MyAddon-dev"
    link.symlink_to(work, target_is_directory=True)
    a = _screenshots_snapshot(flavor)
    link.unlink()
    link.symlink_to(other, target_is_directory=True)
    _later_session(flavor)
    font_now = (flavor / FONT).read_bytes()
    result = run("snap", "restore", a.id, "--yes")
    assert result.exit_code == 3, (result.stdout, result.stderr)
    assert "restore never creates one" in _plain(result.stderr)
    assert (flavor / FONT).read_bytes() == font_now
    assert guard.history() == ()


def test_an_unchanged_executable_is_left_alone_constructed(root: Path, flavor: Path) -> None:
    script = flavor / "Interface" / "AddOns" / "DBM-Brawlers" / "build.sh"
    script.write_bytes(b"#!/bin/sh\necho constructed\n")
    a = _screenshots_snapshot(flavor)
    _later_session(flavor)
    report = _json(cli.RestoreReport, "snap", "restore", a.id, "--yes")
    assert report.applied
    assert script.read_bytes() == b"#!/bin/sh\necho constructed\n"


def test_a_changed_executable_refuses_the_restore_constructed(root: Path, flavor: Path) -> None:
    script = flavor / "Interface" / "AddOns" / "DBM-Brawlers" / "build.sh"
    script.write_bytes(b"#!/bin/sh\necho constructed\n")
    a = _screenshots_snapshot(flavor)
    script.write_bytes(b"#!/bin/sh\necho constructed later\n")
    _later_session(flavor)
    result = run("snap", "restore", a.id, "--yes")
    assert result.exit_code == 3, (result.stdout, result.stderr)
    assert "is an executable; guard never writes one" in _plain(result.stderr)
    assert guard.history() == ()
