"""`wowlab snap restore` of a snapshot taken with `--screenshots` (M13-30).

`snap create --screenshots` also captures `Screenshots/`, whose file-map row
(`screenshots`) has Edit `n/a`: the write gate never writes there. A whole
restore sets the screenshots aside, lists them in `RestoreReport.not_restored`
(apart from the Edit `no` skips) with what the disk holds now, and restores
the WTF, Interface and Fonts files by name, with the gate checking every
entry a whole restore would; naming a screenshot with `--paths` is refused
with the gate's reason (exit 3).

The install is the captured tree as `test_cli.py` builds it (copied into
`tmp_path`, the user data directory redirected, the process table a fake).
Inputs labelled `constructed` in the test names (L8): a font file under
`Fonts/` and screenshot files under `Screenshots/`, which the capture does
not have, edited copies of captured files standing for a later session, a
symbolic link to a folder outside the install, an addon's `build.sh`, a file
named `aux.lua`, a captured `.toc` renamed in case only, an edit made inside
a patched `cli._confirm`, and snapshot manifests forged with another flavor's
paths (re-fingerprinted so the store accepts them). Nothing reads or writes a
real install.
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from test_cli import (
    CAPTURE,
    FLAVOR,
    SYNDICATOR,
    _flip_syndicator,
    _json,
    _plain,
    _state,
    flavor,  # noqa: F401
    idle,  # noqa: F401  (autouse: no client in the process table)
    ok,
    replayed,  # noqa: F401  (autouse: recorded game data only)
    root,  # noqa: F401
    run,
    user_data,  # noqa: F401
)

from wowlab_core import cli, guard, profiles
from wowlab_core.snapshot import (
    Entry,
    Manifest,
    SnapshotStore,
    manifest_bytes,
    tree_fingerprint,
)

TOC = "Interface/AddOns/DBM-Brawlers/DBM-Brawlers.toc"
FONT = "Fonts/LABFONT.TTF"
SHOT_A = "Screenshots/WoWScrnShot_101626_120000.jpg"
SHOT_B = "Screenshots/WoWScrnShot_101626_120500.tga"
FONT_BYTES = b"constructed font bytes\x00\x01"
JPEG = b"\xff\xd8\xff\xe0constructed jpeg\xff\xd9"
TGA = b"\x00\x00\x02constructed tga"
LATER_JPEG = b"constructed later jpeg"


def _add(flavor: Path, rel: str, data: bytes) -> None:
    path = flavor / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _snapshot(*, shots: bool) -> Manifest:
    args = ["snap", "create", "-m", "constructed", "--json"]
    return Manifest.model_validate_json(ok(*args, *(["--screenshots"] if shots else [])).stdout)


def _screenshots_snapshot(flavor: Path) -> Manifest:
    """A font and two screenshots added to the capture, then `snap create --screenshots`."""
    for rel, data in ((FONT, FONT_BYTES), (SHOT_A, JPEG), (SHOT_B, TGA)):
        _add(flavor, rel, data)
    manifest = _snapshot(shots=True)
    held = {e.path.split("/", 1)[1] for e in manifest.entries}
    assert {SHOT_A, SHOT_B, FONT, TOC, SYNDICATOR} <= held
    return manifest


def _later_session(flavor: Path) -> None:
    """WTF, Interface and Fonts files and both screenshots changed since the snapshot."""
    _flip_syndicator(flavor)
    toc = flavor / TOC
    toc.write_bytes(toc.read_bytes() + b"\n## X-Constructed: 1\n")
    (flavor / FONT).write_bytes(b"constructed later font")
    (flavor / SHOT_A).write_bytes(LATER_JPEG)
    (flavor / SHOT_B).unlink()


# ─── the whole restore ───────────────────────────────────────────────────────


def test_whole_restore_of_a_screenshots_snapshot_restores_the_rest_constructed(
    root: Path, flavor: Path
) -> None:
    a = _screenshots_snapshot(flavor)
    syndicator = (flavor / SYNDICATOR).read_bytes()
    toc = (flavor / TOC).read_bytes()
    _later_session(flavor)

    plan = _json(cli.RestoreReport, "snap", "restore", a.id, "--dry-run")
    assert sorted(i.path for i in plan.plan) == sorted([FONT, TOC, SYNDICATOR])
    assert plan.skipped == []
    assert [(u.path, u.entry_id, u.disk) for u in plan.not_restored] == [
        (SHOT_A, "screenshots", "differs"),
        (SHOT_B, "screenshots", "missing"),
    ]
    assert not plan.applied and plan.dry_run

    text = _plain(ok("snap", "restore", a.id, "--dry-run").stdout)
    assert "3 change(s)" in text
    assert (
        '  Not restored: 2 screenshot(s) wowlab never writes (file-map Edit "n/a"; --paths '
        f"cannot restore them either). They stay in snapshot {a.id}; 2 differ from or are "
        "missing on disk:"
    ) in text
    assert f"    {SHOT_A}  [differs]" in text
    assert f"    {SHOT_B}  [missing]" in text
    assert "name one with --paths" not in text
    assert "Skipped" not in text

    done = _json(cli.RestoreReport, "snap", "restore", a.id, "--yes")
    assert done.applied and done.transaction is not None
    assert [u.path for u in done.not_restored] == [SHOT_A, SHOT_B]
    assert (flavor / SYNDICATOR).read_bytes() == syndicator
    assert (flavor / TOC).read_bytes() == toc
    assert (flavor / FONT).read_bytes() == FONT_BYTES
    # The screenshots are as the later session left them: never written.
    assert (flavor / SHOT_A).read_bytes() == LATER_JPEG
    assert not (flavor / SHOT_B).exists()
    (record,) = guard.history()
    assert record.id == done.transaction
    assert sorted(p.path for p in record.paths) == sorted([FONT, TOC, SYNDICATOR])


def test_whole_restore_text_restores_and_names_the_screenshots_constructed(
    root: Path, flavor: Path
) -> None:
    a = _screenshots_snapshot(flavor)
    _later_session(flavor)
    out = _plain(ok("snap", "restore", a.id, "--yes").stdout)
    assert "Not restored: 2 screenshot(s) wowlab never writes" in out
    assert "Restored 3 file(s)." in out
    assert (flavor / FONT).read_bytes() == FONT_BYTES


def test_unchanged_screenshots_are_counted_not_listed_constructed(root: Path, flavor: Path) -> None:
    a = _screenshots_snapshot(flavor)
    _flip_syndicator(flavor)
    report = _json(cli.RestoreReport, "snap", "restore", a.id, "--dry-run")
    assert [u.disk for u in report.not_restored] == ["same", "same"]
    text = _plain(ok("snap", "restore", a.id, "--dry-run").stdout)
    assert "  The snapshot also holds 2 screenshot(s); all still match the disk." in text
    assert "Not restored" not in text
    assert SHOT_A not in text


def test_a_screenshot_of_another_size_differs_without_being_read_constructed(
    root: Path, flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Size first: a screenshot whose size changed is `differs` with no read."""
    a = _screenshots_snapshot(flavor)
    (flavor / SHOT_A).write_bytes(JPEG + b"longer")
    reads: list[int] = []
    real_read = os.read

    def counting_read(fd: int, n: int) -> bytes:
        reads.append(fd)
        return real_read(fd, n)

    (entry,) = [e for e in a.entries if e.path.endswith(SHOT_A)]
    monkeypatch.setattr(profiles.os, "read", counting_read)
    assert profiles.disk_state(root, FLAVOR, entry) == "differs"
    assert reads == []
    (same,) = [e for e in a.entries if e.path.endswith(SHOT_B)]
    assert profiles.disk_state(root, FLAVOR, same) == "same"
    assert reads, "a file of the recorded size is read and hashed"


def test_whole_restore_with_only_screenshots_changed_writes_nothing_constructed(
    root: Path, flavor: Path
) -> None:
    a = _screenshots_snapshot(flavor)
    (flavor / SHOT_A).write_bytes(LATER_JPEG)
    out = _plain(ok("snap", "restore", a.id, "--yes").stdout)
    assert "0 change(s)" in out
    assert "Not restored: 2 screenshot(s)" in out
    assert f"They stay in snapshot {a.id}; 1 differs from or is missing on disk:" in out
    assert "Nothing to restore but the screenshots wowlab never writes; nothing was changed." in out
    assert guard.history() == ()
    report = _json(cli.RestoreReport, "snap", "restore", a.id, "--yes")
    assert report.plan == [] and not report.applied and len(report.not_restored) == 2


def test_whole_restore_with_nothing_changed_says_it_matches_constructed(
    root: Path, flavor: Path
) -> None:
    a = _screenshots_snapshot(flavor)
    out = _plain(ok("snap", "restore", a.id, "--yes").stdout)
    assert "  The snapshot also holds 2 screenshot(s); all still match the disk." in out
    assert f"Nothing to restore: {FLAVOR} already matches snapshot {a.id}." in out
    assert guard.history() == ()


def test_edit_no_skips_and_screenshots_are_reported_apart_constructed(
    root: Path, flavor: Path
) -> None:
    bak = "WTF/Account/90000001#6/SavedVariables/RareScanner.lua.bak"
    a = _screenshots_snapshot(flavor)
    (flavor / bak).write_bytes(b"constructed later backup\n")
    _later_session(flavor)
    plan = _json(cli.RestoreReport, "snap", "restore", a.id, "--dry-run")
    assert [(s.path, s.entry_id) for s in plan.skipped] == [(bak, "savedvariables-backup")]
    assert [u.path for u in plan.not_restored] == [SHOT_A, SHOT_B]
    text = _plain(ok("snap", "restore", a.id, "--dry-run").stdout)
    (skipped_line,) = [ln for ln in text.splitlines() if ln.startswith("  Skipped ")]
    assert "name one with --paths to restore it" in skipped_line
    assert SHOT_A not in text.split("  Not restored:", 1)[0]
    ok("snap", "restore", a.id, "--yes")
    assert (flavor / bak).read_bytes() == b"constructed later backup\n"
    assert (flavor / FONT).read_bytes() == FONT_BYTES


def test_only_skipped_files_and_screenshots_changed_says_both_constructed(
    root: Path, flavor: Path
) -> None:
    bak = "WTF/Account/90000001#6/SavedVariables/RareScanner.lua.bak"
    a = _screenshots_snapshot(flavor)
    (flavor / bak).write_bytes(b"constructed later backup\n")
    (flavor / SHOT_A).write_bytes(LATER_JPEG)
    out = _plain(ok("snap", "restore", a.id, "--yes").stdout)
    assert (
        "Nothing to restore but the skipped files and the screenshots wowlab never writes; "
        "nothing was changed."
    ) in out
    (flavor / SHOT_A).write_bytes(JPEG)
    out = _plain(ok("snap", "restore", a.id, "--yes").stdout)
    assert "Nothing to restore but the skipped files; nothing was changed." in out
    assert guard.history() == ()


@pytest.mark.skipif(sys.platform == "win32", reason="symbolic links need privileges on Windows")
def test_screenshots_behind_a_link_are_not_read_and_counted_apart_constructed(
    root: Path, flavor: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Screenshots/ replaced by a symbolic link to a folder holding the same
    files: nothing behind the link is read, and the screenshots are reported
    `behind_link`, not `differs`."""
    a = _screenshots_snapshot(flavor)
    moved = tmp_path / "moved-screenshots-constructed"
    (flavor / "Screenshots").rename(moved)
    (flavor / "Screenshots").symlink_to(moved, target_is_directory=True)
    _flip_syndicator(flavor)
    opened: list[str] = []
    real_open = os.open

    def watching_open(path: object, *args: object, **kwargs: object) -> int:
        opened.append(str(path))
        return real_open(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(profiles.os, "open", watching_open)
    report = _json(cli.RestoreReport, "snap", "restore", a.id, "--dry-run")
    assert [(u.path, u.disk) for u in report.not_restored] == [
        (SHOT_A, "behind_link"),
        (SHOT_B, "behind_link"),
    ]
    assert not any("WoWScrnShot" in p for p in opened)
    assert [i.path for i in report.plan] == [SYNDICATOR]
    text = _plain(ok("snap", "restore", a.id, "--dry-run").stdout)
    assert (
        f"They stay in snapshot {a.id}; 2 not read (Screenshots/ is now a link, wowlab does "
        "not follow it):"
    ) in text
    assert f"    {SHOT_A}  [behind_link]" in text
    assert "differ" not in text
    done = _json(cli.RestoreReport, "snap", "restore", a.id, "--yes")
    assert done.applied
    assert (flavor / "Screenshots").is_symlink()
    assert (moved / SHOT_A.split("/")[1]).read_bytes() == JPEG


def test_one_changed_and_one_unread_are_both_counted_constructed(root: Path, flavor: Path) -> None:
    lines = cli._not_restored_lines(
        "S1",
        [
            cli.NotRestoredPath(path=SHOT_A, entry_id="screenshots", what="w", disk="missing"),
            cli.NotRestoredPath(path=SHOT_B, entry_id="screenshots", what="w", disk="behind_link"),
            cli.NotRestoredPath(
                path="Screenshots/c.jpg", entry_id="screenshots", what="w", disk="same"
            ),
        ],
    )
    assert lines[0].endswith(
        "They stay in snapshot S1; 1 differs from or is missing on disk; 1 not read "
        "(Screenshots/ is now a link, wowlab does not follow it):"
    )
    assert lines[0].startswith("  Not restored: 3 screenshot(s) wowlab never writes")
    assert lines[1:] == [f"    {SHOT_A}  [missing]", f"    {SHOT_B}  [behind_link]"]


@pytest.mark.skipif(sys.platform == "win32", reason="permission bits are not kept on Windows")
def test_an_executable_whose_mode_changed_after_the_plan_rolls_back_constructed(
    root: Path, flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unchanged executable is left unnamed and read again before commit,
    bytes and permission bits both."""
    script = flavor / "Interface" / "AddOns" / "DBM-Brawlers" / "build.sh"
    script.write_bytes(b"#!/bin/sh\necho constructed\n")
    script.chmod(0o644)
    a = _screenshots_snapshot(flavor)
    _flip_syndicator(flavor)
    real = cli._confirm

    def confirm(question: str, yes: bool) -> None:
        script.chmod(0o755)
        real(question, yes)

    monkeypatch.setattr(cli, "_confirm", confirm)
    result = run("snap", "restore", a.id, "--yes")
    assert result.exit_code == 1, (result.stdout, result.stderr)
    assert "the files changed after the plan was shown" in _plain(result.stderr)
    (record,) = guard.history()
    assert record.state != "committed"


def test_many_screenshots_are_counted_and_the_text_names_ten_constructed(
    root: Path, flavor: Path
) -> None:
    shots = flavor / "Screenshots"
    shots.mkdir()
    for n in range(13):
        (shots / f"WoWScrnShot_101626_1200{n:02d}.jpg").write_bytes(JPEG + bytes([n]))
    a = _snapshot(shots=True)
    for n in range(13):
        (shots / f"WoWScrnShot_101626_1200{n:02d}.jpg").unlink()
    _flip_syndicator(flavor)
    text = _plain(ok("snap", "restore", a.id, "--dry-run").stdout)
    assert "Not restored: 13 screenshot(s)" in text
    assert sum(ln.endswith("  [missing]") for ln in text.splitlines()) == 10
    assert "    and 3 more (--json lists every one)" in text
    report = _json(cli.RestoreReport, "snap", "restore", a.id, "--dry-run")
    assert len(report.not_restored) == 13
    assert [i.path for i in report.plan] == [SYNDICATOR]


def test_a_snapshot_without_screenshots_lists_none(root: Path, flavor: Path) -> None:
    a = _snapshot(shots=False)
    _flip_syndicator(flavor)
    report = _json(cli.RestoreReport, "snap", "restore", a.id, "--yes")
    assert report.not_restored == [] and report.applied
    assert (flavor / SYNDICATOR).read_bytes() == (CAPTURE / "forever" / SYNDICATOR).read_bytes()


# ─── --paths naming a screenshot ─────────────────────────────────────────────


def test_restore_naming_a_screenshot_is_refused_by_the_gate_constructed(
    root: Path, flavor: Path
) -> None:
    a = _screenshots_snapshot(flavor)
    _later_session(flavor)
    result = run("snap", "restore", a.id, "--paths", SHOT_A, "--yes")
    assert result.exit_code == 3, (result.stdout, result.stderr)
    lines = _plain(result.stderr).splitlines()
    assert lines[-2] == (
        f"wowlab: refused by the write gate: {SHOT_A!r} is outside the allowlist "
        "(WTF/, Interface/, Fonts/)"
    )
    assert lines[-1] == (
        f'wowlab never writes Screenshots/ (file-map Edit "n/a"); the file stays in '
        f"snapshot {a.id}."
    )
    assert (flavor / SHOT_A).read_bytes() == LATER_JPEG
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
    assert "wowlab never writes Screenshots/" in _plain(result.stderr)
    assert (flavor / FONT).read_bytes() == font_now


def test_another_refused_path_gets_no_screenshot_note_constructed(root: Path, flavor: Path) -> None:
    a = _screenshots_snapshot(flavor)
    result = run("snap", "restore", a.id, "--paths", "Logs/x.log", "--dry-run")
    assert result.exit_code == 3, (result.stdout, result.stderr)
    assert "Screenshots" not in _plain(result.stderr)


# ─── parity with a whole restore ─────────────────────────────────────────────


BAK = "WTF/Account/90000001#6/SavedVariables/RareScanner.lua.bak"


def _restore_with_edit_at_confirm(
    flavor: Path, monkeypatch: pytest.MonkeyPatch, rel: str, *, shots: bool, skip: bool
) -> tuple[int, str, bytes]:
    if shots:
        _add(flavor, SHOT_A, JPEG)
    manifest = _snapshot(shots=shots)
    then = (flavor / rel).read_bytes()
    _flip_syndicator(flavor)  # one file to restore, so the plan is not empty
    if skip:  # an Edit `no` file changed too, so the plan skips something
        (flavor / BAK).write_bytes(b"constructed later backup\n")
    real = cli._confirm

    def confirm(question: str, yes: bool) -> None:
        (flavor / rel).write_bytes(b"constructed edit between the plan and the confirm\n")
        real(question, yes)

    monkeypatch.setattr(cli, "_confirm", confirm)
    result = run("snap", "restore", manifest.id, "--yes")
    return result.exit_code, _plain(result.stderr), then


@pytest.mark.parametrize("shots", [False, True], ids=["plain", "screenshots"])
@pytest.mark.parametrize(
    ("rel", "skip"),
    [(TOC, False), (TOC, True), (BAK, False)],
    ids=["toc", "toc-bak-skipped", "bak"],
)
def test_a_change_after_the_plan_rolls_back_constructed(
    root: Path, flavor: Path, monkeypatch: pytest.MonkeyPatch, rel: str, shots: bool, skip: bool
) -> None:
    """A file the plan found unchanged (an addon's `.toc`, or an Edit `no`
    backup) that changes before the confirm rolls the restore back, whether
    or not screenshots are set aside or something is skipped (both make the
    restore go by name)."""
    code, err, then = _restore_with_edit_at_confirm(
        flavor, monkeypatch, rel, shots=shots, skip=skip
    )
    assert code == 1, err
    assert "the files changed after the plan was shown" in err
    (record,) = guard.history()
    assert record.state != "committed"
    assert (flavor / rel).read_bytes() != then  # the edit is not overwritten by a commit


def _outcome(flavor: Path, *, shots: bool, change: Callable[[], None]) -> tuple[int, str]:
    """Snapshot, apply `change` to the disk, restore whole: exit code and stderr."""
    if shots:
        _add(flavor, SHOT_A, JPEG)
    manifest = _snapshot(shots=shots)
    change()
    _flip_syndicator(flavor)
    result = run("snap", "restore", manifest.id, "--yes")
    return result.exit_code, _plain(result.stderr)


def _parity(
    tmp_path: Path,
    root: Path,
    flavor: Path,
    setup: Callable[[], None],
    change: Callable[[], None],
) -> tuple[int, str]:
    """The same sequence without and then with screenshots, from the same
    tree: the exit codes must agree."""
    setup()
    pristine = tmp_path / "pristine-constructed"
    shutil.copytree(root, pristine, symlinks=True)
    plain = _outcome(flavor, shots=False, change=change)
    shutil.rmtree(root)
    shutil.copytree(pristine, root, symlinks=True)
    with_shots = _outcome(flavor, shots=True, change=change)
    assert with_shots[0] == plain[0], (plain, with_shots)
    return with_shots


def test_a_reserved_name_is_refused_as_a_whole_restore_refuses_it_constructed(
    tmp_path: Path, root: Path, flavor: Path
) -> None:
    aux = flavor / "Interface" / "AddOns" / "DBM-Brawlers" / "aux.lua"
    code, err = _parity(
        tmp_path, root, flavor, lambda: aux.write_bytes(b"-- constructed\n"), lambda: None
    )
    assert code == 3
    assert "reserved device name" in err


def test_a_case_renamed_file_is_treated_as_a_whole_restore_treats_it_constructed(
    tmp_path: Path, root: Path, flavor: Path
) -> None:
    toc = flavor / TOC
    lower = toc.with_name(toc.name.lower())

    def rename() -> None:
        toc.rename(toc.with_name("tmp-constructed"))
        toc.with_name("tmp-constructed").rename(lower)

    _parity(tmp_path, root, flavor, lambda: None, rename)


# ─── links and executables ───────────────────────────────────────────────────


@pytest.mark.skipif(sys.platform == "win32", reason="symbolic links need privileges on Windows")
def test_an_unchanged_link_is_left_alone_as_a_whole_restore_leaves_it_constructed(
    root: Path, flavor: Path, tmp_path: Path
) -> None:
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


@pytest.mark.skipif(sys.platform == "win32", reason="symbolic links need privileges on Windows")
def test_a_link_changed_after_the_plan_rolls_back_constructed(
    root: Path, flavor: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unchanged link is left unnamed; it is read again before commit."""
    work = tmp_path / "work-constructed"
    other = tmp_path / "other-constructed"
    work.mkdir()
    other.mkdir()
    link = flavor / "Interface" / "AddOns" / "MyAddon-dev"
    link.symlink_to(work, target_is_directory=True)
    a = _screenshots_snapshot(flavor)
    _flip_syndicator(flavor)
    real = cli._confirm

    def confirm(question: str, yes: bool) -> None:
        link.unlink()
        link.symlink_to(other, target_is_directory=True)
        real(question, yes)

    monkeypatch.setattr(cli, "_confirm", confirm)
    result = run("snap", "restore", a.id, "--yes")
    assert result.exit_code == 1, (result.stdout, result.stderr)
    assert "the files changed after the plan was shown" in _plain(result.stderr)
    (record,) = guard.history()
    assert record.state != "committed"


def test_an_unchanged_junction_entry_is_same_on_the_lstat_walk_constructed(
    root: Path, flavor: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On Windows `lstat` reports a junction as a folder; the walk Windows
    uses (`_DIR_FD_WALK` off) counts it as a link, as the gate does. A real
    folder stands in for the junction, with `is_junction` and `readlink`
    patched to say what Windows would."""
    target = str(tmp_path / "work-constructed")
    junction = flavor / "Interface" / "AddOns" / "MyAddon-dev"
    junction.mkdir()
    entry = Entry(
        path=f"{FLAVOR}/Interface/AddOns/MyAddon-dev",
        kind="symlink",
        sha256=None,
        size=0,
        mode=0o777,
        target=target,
    )
    monkeypatch.setattr(profiles, "_DIR_FD_WALK", False)
    real_is_junction = Path.is_junction
    real_readlink = Path.readlink
    monkeypatch.setattr(Path, "is_junction", lambda p: p == junction or real_is_junction(p))
    monkeypatch.setattr(
        Path, "readlink", lambda p: Path(target) if p == junction else real_readlink(p)
    )
    assert profiles.disk_state(root, FLAVOR, entry) == "same"
    other = entry.model_copy(update={"target": target + "-other"})
    assert profiles.disk_state(root, FLAVOR, other) == "differs"


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


# ─── a forged snapshot holding another flavor's paths ────────────────────────


def _forge(base: Manifest, extra: str) -> str:
    """`base` plus one entry at `extra` (install-relative), re-fingerprinted."""
    (anchor,) = [e for e in base.entries if e.path == f"{FLAVOR}/{SYNDICATOR}"]
    entries = tuple(
        sorted((*base.entries, anchor.model_copy(update={"path": extra})), key=lambda e: e.path)
    )
    fingerprint = tree_fingerprint(base.subtrees, base.excluded, entries)
    forged = Manifest(
        **{
            **base.model_dump(),
            "id": f"20260921T130000.000000Z-{fingerprint}",
            "created_at": "2026-09-21T13:00:00.000000Z",
            "label": "constructed: forged",
            "entries": entries,
        }
    )
    store = SnapshotStore()
    (store.manifests_dir / f"{forged.id}.json").write_bytes(manifest_bytes(forged))
    assert store.show(forged.id) == forged, "the store accepts it; refusing is guard's job"
    return forged.id


@pytest.mark.parametrize("dry", [True, False], ids=["dry-run", "yes"])
@pytest.mark.parametrize(
    "extra",
    ["_retail_/WTF/Config.wtf", f"{FLAVOR}x/WTF/Config.wtf"],
    ids=["other-flavor", "folder-prefix"],
)
def test_a_screenshots_snapshot_holding_another_flavors_path_is_refused_constructed(
    root: Path, flavor: Path, extra: str, dry: bool
) -> None:
    forged = _forge(_screenshots_snapshot(flavor), extra)
    _later_session(flavor)
    before = _state(root)
    result = run("snap", "restore", forged, "--dry-run" if dry else "--yes")
    assert result.exit_code == 3, (result.stdout, result.stderr)
    assert "which is not in this flavor" in _plain(result.stderr)
    assert _state(root) == before
    assert guard.history() == ()
