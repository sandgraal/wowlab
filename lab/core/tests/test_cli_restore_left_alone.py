"""`wowlab snap restore`: a whole restore says what it leaves alone (M16-11).

A whole restore puts back every file the snapshot holds and never deletes
the files added under its folders since it was taken. The text plan lists
them under a "Left alone" heading; `--json` and the plan are unchanged. When
the command restores the rest by name (set-aside screenshots, skipped
executables or Edit `no` files) it lists them itself, by the gate's rule, and
its dry-run plan still equals its commit plan.

The install is the captured tree as `test_cli.py` builds it (copied into
`tmp_path`, the user data directory redirected, the process table a fake).
Inputs labelled `constructed` in the test names (L8): files added since the
snapshot (an addon file, a SavedVariables file, a font), a screenshot, an
addon's `helper.sh`, a `.DS_Store`, a symbolic link to a file outside the
install, and edited copies of captured files standing for a later session.
Nothing reads or writes a real install.
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

import os
from pathlib import Path

import pytest
from test_cli import (
    SV_DIR,
    SYNDICATOR,
    _flip_syndicator,
    _json,
    _plain,
    flavor,  # noqa: F401
    idle,  # noqa: F401  (autouse: no client in the process table)
    ok,
    replayed,  # noqa: F401  (autouse: recorded game data only)
    root,  # noqa: F401
    user_data,  # noqa: F401
)

from wowlab_core import cli, guard
from wowlab_core.snapshot import Manifest

ADDON = "Interface/AddOns/DBM-Brawlers"
TOC = f"{ADDON}/DBM-Brawlers.toc"
HELPER = f"{ADDON}/helper.sh"
DS = f"{ADDON}/.DS_Store"
SHOT = "Screenshots/WoWScrnShot_101626_120000.jpg"

ADDED = {
    f"{ADDON}/Added.lua": b"-- constructed: added since the snapshot\n",
    f"{SV_DIR}/AddedSince.lua": b'AddedSinceDB = {\n\t["x"] = 1,\n}\n',
    "Fonts/ADDED.TTF": b"\x00\x01\x00\x00constructed font",
}


def _snapshot(*extra: str) -> Manifest:
    made = ok("snap", "create", "-m", "constructed", "--json", *extra)
    return Manifest.model_validate_json(made.stdout)


def _add(flavor: Path, files: dict[str, bytes]) -> dict[str, tuple[bytes, int, int]]:
    """Write `files` under the flavor; return (bytes, inode, mtime) of each."""
    for rel, data in files.items():
        path = flavor / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return _files(flavor, files)


def _files(flavor: Path, files: dict[str, bytes]) -> dict[str, tuple[bytes, int, int]]:
    out = {}
    for rel in files:
        st = (flavor / rel).lstat()
        out[rel] = ((flavor / rel).read_bytes(), st.st_ino, st.st_mtime_ns)
    return out


def _left_alone(text: str) -> list[str]:
    """The paths under the "Left alone" heading of a text plan."""
    lines = _plain(text).splitlines()
    (at,) = [i for i, ln in enumerate(lines) if ln.startswith("  Left alone: ")]
    out: list[str] = []
    for ln in lines[at + 1 :]:
        if not ln.startswith("    "):
            break
        out.append(ln.strip())
    return out


def test_whole_restore_lists_added_files_and_never_touches_them_constructed(
    root: Path, flavor: Path
) -> None:
    a = _snapshot()
    saved = (flavor / SYNDICATOR).read_bytes()
    _flip_syndicator(flavor)
    before = _add(flavor, ADDED)

    text = ok("snap", "restore", a.id, "--dry-run").stdout
    heading = next(ln for ln in _plain(text).splitlines() if "Left alone" in ln)
    assert heading == (
        "  Left alone: 3 file(s) added since the snapshot was taken. A whole restore "
        "never deletes them; name one with --paths to delete it:"
    )
    assert _left_alone(text) == sorted(ADDED)

    # --json and the plan are as before: only what the restore changes.
    plan = _json(cli.RestoreReport, "snap", "restore", a.id, "--dry-run")
    assert [i.path for i in plan.plan] == [SYNDICATOR]
    assert "left" not in plan.model_dump_json().lower()

    done = ok("snap", "restore", a.id, "--yes")
    assert _left_alone(done.stdout) == sorted(ADDED)
    assert (flavor / SYNDICATOR).read_bytes() == saved
    assert _files(flavor, ADDED) == before
    (record,) = guard.history()
    assert [p.path for p in record.paths] == [SYNDICATOR]


def test_restore_by_paths_has_no_left_alone_heading_constructed(root: Path, flavor: Path) -> None:
    a = _snapshot()
    _flip_syndicator(flavor)
    _add(flavor, ADDED)
    text = _plain(ok("snap", "restore", a.id, "--paths", SYNDICATOR, "--dry-run").stdout)
    assert "Left alone" not in text


def test_nothing_to_restore_still_lists_what_is_left_alone_constructed(
    root: Path, flavor: Path
) -> None:
    a = _snapshot()
    _add(flavor, ADDED)
    result = ok("snap", "restore", a.id, "--yes")
    assert _left_alone(result.stdout) == sorted(ADDED)
    assert "Nothing to restore" in _plain(result.stdout)
    assert guard.history() == ()


def test_no_heading_when_nothing_was_added_constructed(root: Path, flavor: Path) -> None:
    a = _snapshot()
    _flip_syndicator(flavor)
    assert "Left alone" not in _plain(ok("snap", "restore", a.id, "--dry-run").stdout)


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlinks")
def test_an_added_link_is_not_listed_constructed(root: Path, flavor: Path, tmp_path: Path) -> None:
    a = _snapshot()
    _flip_syndicator(flavor)
    outside = tmp_path / "outside.lua"
    outside.write_bytes(b"-- constructed: outside the install\n")
    (flavor / ADDON / "Linked.lua").symlink_to(outside)
    _add(flavor, {f"{ADDON}/Added.lua": ADDED[f"{ADDON}/Added.lua"]})
    text = ok("snap", "restore", a.id, "--dry-run").stdout
    assert _left_alone(text) == [f"{ADDON}/Added.lua"]


def test_restored_by_name_around_screenshots_lists_the_same_and_commits_constructed(
    root: Path, flavor: Path
) -> None:
    """Screenshots set aside: the command restores the rest by name and
    lists the added files itself; the commit plan equals the dry-run plan."""
    _add(flavor, {SHOT: b"\xff\xd8constructed jpeg"})
    a = _snapshot("--screenshots")
    saved = (flavor / SYNDICATOR).read_bytes()
    _flip_syndicator(flavor)
    (flavor / SHOT).write_bytes(b"constructed later jpeg")
    added = {**ADDED, "Screenshots/WoWScrnShot_101626_130000.jpg": b"\xff\xd8added"}
    before = _add(flavor, added)

    plan = _json(cli.RestoreReport, "snap", "restore", a.id, "--dry-run")
    assert [n.path for n in plan.not_restored] == [SHOT]
    text = ok("snap", "restore", a.id, "--dry-run").stdout
    assert _left_alone(text) == sorted(added)

    done = ok("snap", "restore", a.id, "--yes")
    assert _left_alone(done.stdout) == sorted(added)
    assert (flavor / SYNDICATOR).read_bytes() == saved
    assert _files(flavor, added) == before
    (record,) = guard.history()
    assert record.state == "committed"
    assert [p.path for p in record.paths] == [SYNDICATOR]


def test_restored_by_name_around_skipped_files_commits_constructed(
    root: Path, flavor: Path
) -> None:
    """A changed executable and a changed Edit `no` file are skipped, an
    added file is listed, and the commit succeeds with the same plan."""
    (flavor / HELPER).write_bytes(b"#!/bin/sh\necho constructed\n")
    (flavor / DS).write_bytes(b"constructed metadata")
    a = _snapshot()
    saved = (flavor / TOC).read_bytes()
    (flavor / TOC).write_bytes(saved + b"## X-Constructed: 1\n")
    (flavor / HELPER).write_bytes(b"#!/bin/sh\necho constructed later\n")
    (flavor / DS).write_bytes(b"constructed metadata, later")
    before = _add(flavor, ADDED)

    plan = _json(cli.RestoreReport, "snap", "restore", a.id, "--dry-run")
    assert [i.path for i in plan.plan] == [TOC]
    assert sorted(s.path for s in plan.skipped) == sorted([DS, HELPER])
    assert _left_alone(ok("snap", "restore", a.id, "--dry-run").stdout) == sorted(ADDED)

    done = _json(cli.RestoreReport, "snap", "restore", a.id, "--yes")
    assert done.applied and [i.path for i in done.plan] == [TOC]
    assert (flavor / TOC).read_bytes() == saved
    assert _files(flavor, ADDED) == before


def test_help_says_a_whole_restore_never_deletes_added_files() -> None:
    text = " ".join(_plain(ok("snap", "restore", "--help").stdout).split())
    assert "files added under the snapshot's folders since it was taken are never deleted" in text
