"""The first run of `wowlab doctor` (M13-04, docs/LAB_PLAN.md §15.2, ADR-0031).

The install is the captured tree (`fixtures/macos/`) copied into `tmp_path`
by `test_cli`'s `root` fixture; the user data directory (the snapshot store)
is redirected into `tmp_path`, the process table is a fake, and wago.tools
is replayed from `fixtures/wago/`. Nothing here reads or writes a real
install, and nothing reaches the network.

Inputs labelled `constructed` are the edge cases the ticket names, built on
the copy: a WTF folder under a OneDrive or Dropbox folder or holding a
Syncthing marker, links and a stand-in junction in WTF, WTF itself a link,
a very large WTF (a sparse file, and lowered thresholds), a second flavor
folder, a running client, and names carrying terminal control sequences.
"""

# ruff: noqa: F811  (fixtures imported from test_cli are requested by name)
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from test_cli import (
    ACCOUNT,
    FLAVOR,
    SV_DIR,
    _running,
    _state,
    flavor,  # noqa: F401
    idle,  # noqa: F401  (autouse: no client in the process table)
    ok,
    replayed,  # noqa: F401  (autouse: recorded game data only)
    root,  # noqa: F401
    run,
    user_data,  # noqa: F401  (autouse: the store lives in tmp_path)
)

from wowlab_core import cli, firstrun, install, snapshot
from wowlab_core.snapshot import SnapshotStore

SECOND = "_constructed_"  # a second flavor folder (constructed)
SECRET = b"outside the install: never captured\n"

windows = sys.platform == "win32"
needs_symlinks = pytest.mark.skipif(windows, reason="symlinks need privileges on Windows")


# ─── helpers ─────────────────────────────────────────────────────────────────


def _report(*args: str, input: str | None = None, code: int = 0) -> cli.DoctorReport:
    result = run("doctor", *args, "--json", input=input)
    assert result.exit_code == code, (result.stdout, result.stderr, result.exception)
    return cli.DoctorReport.model_validate_json(result.stdout)


def _first(report: cli.DoctorReport) -> firstrun.FirstRunReport:
    assert report.first_run is not None
    return report.first_run


def _lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.strip()]


def _manifests() -> tuple[snapshot.Manifest, ...]:
    return SnapshotStore().list()


def _ask_interactively(monkeypatch: pytest.MonkeyPatch) -> None:
    """Input counts as a terminal, so a first run asks."""
    monkeypatch.setattr(cli, "_interactive", lambda: True)


def _never_ask(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_a: Any, **_k: Any) -> Any:
        raise AssertionError("a first run asked when it must not")

    monkeypatch.setattr(cli.typer, "confirm", refuse)
    monkeypatch.setattr(cli.typer, "prompt", refuse)


def _second_flavor(root: Path, flavor: Path) -> Path:
    """A second flavor folder, a copy of the first under another product (constructed)."""
    second = root / SECOND
    shutil.copytree(flavor, second)
    info = second / install.FLAVOR_INFO
    info.write_bytes(info.read_bytes().replace(b"wow_classic_beta", b"wow_constructed"))
    return second


def _outside(tmp_path: Path) -> Path:
    outside = tmp_path / "outside"
    (outside / "deeper").mkdir(parents=True)
    (outside / "Secret.lua").write_bytes(SECRET)
    (outside / "deeper" / "More.lua").write_bytes(SECRET)
    return outside


def _stored(store: SnapshotStore) -> list[bytes]:
    return [
        store.read_object(shard.name + obj.name)
        for shard in store.objects_dir.iterdir()
        if shard.is_dir()
        for obj in shard.iterdir()
    ]


# ─── the acceptance run ──────────────────────────────────────────────────────


def test_a_first_run_on_the_fixture_install_ends_with_a_verified_snapshot_and_the_undo_line(
    root: Path,
) -> None:
    before = _state(root)
    result = ok("doctor", "--yes")
    out = result.stdout
    lines = _lines(out)

    # The trust model comes first, before the report and its request.
    assert lines[0].startswith("First run: wowlab has no snapshot of this install yet.")
    trust = out[: out.index("Install:")]
    assert "It reads your install and writes nothing there" in trust
    assert "reading one makes the service download it" in trust
    assert "asks first (unless you pass --yes)" in trust
    assert "wago.tools, a community-run public site of game data" in trust
    assert "takes a snapshot before it writes, so `wowlab undo` can put things back" in trust
    assert "never runs code from your files" in trust
    assert "Nothing is uploaded." in trust
    assert "downloads wago.tools' public list of game versions" in trust
    assert "--offline to skip that" in trust
    assert f"Snapshots are kept in wowlab's own store, {SnapshotStore().path}" in trust

    # The size and estimate come before the snapshot.
    scan_at = out.index(
        f"Flavor {FLAVOR} (wow_classic_beta, 1.60.1.69913): a snapshot would hold WTF, Interface"
    )
    assert "it is estimated to take about" in out[scan_at:]
    assert scan_at < out.index("Taking a snapshot of")

    (manifest,) = _manifests()
    assert manifest.label == firstrun.SNAPSHOT_LABEL
    assert manifest.flavor_folder == FLAVOR
    assert manifest.client_running is False
    assert f"Snapshot {manifest.id} (first run):" in out
    assert "Verified the store (wowlab snap verify): " in out
    assert "  all sound" in out
    assert lines[-1].startswith("To undo: this run changed nothing in your install.")
    assert (
        f"Snapshot {manifest.id} holds WTF, Interface and Fonts as they were on disk" in lines[-1]
    )
    assert "(SavedVariables as of your last logout or /reload)" in lines[-1]
    assert f"`wowlab snap restore {manifest.id} --dry-run` shows what restoring it" in lines[-1]
    assert "a restore also removes anything added there since" in lines[-1]
    assert "asks first (unless you pass --yes)" in lines[-1]
    assert "`wowlab undo`" in lines[-1]

    assert _state(root) == before, "the install is only read (L1)"
    assert SnapshotStore().verify().ok
    assert not SnapshotStore().path.is_relative_to(root)
    ok("snap", "restore", manifest.id, "--dry-run")  # the undo line's command is real


def test_a_first_run_json_validates_and_takes_the_snapshot_with_yes(root: Path) -> None:
    before = _state(root)
    report = _report("--yes")
    first = _first(report)
    assert first.outcome == "taken"
    assert first.chosen == FLAVOR
    assert first.client_running is False
    assert first.verify is not None and first.verify.ok
    assert first.snapshot is not None
    (manifest,) = _manifests()
    assert first.snapshot.id == manifest.id
    assert first.snapshot.files == sum(1 for e in manifest.entries if e.kind == "file")
    assert first.store_path == str(SnapshotStore().path)
    assert first.undo == firstrun.undo_line(first.snapshot)
    (scan,) = first.flavors
    assert scan.folder == FLAVOR
    assert scan.files == first.snapshot.files
    assert scan.bytes == first.snapshot.bytes
    assert scan.estimated_seconds == firstrun.estimate_seconds(scan.bytes, scan.files)
    assert (scan.links, scan.sync, scan.large) == ([], [], False)
    assert report.problems == []
    assert _state(root) == before


def test_json_never_prompts_and_takes_nothing_without_yes(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ask_interactively(monkeypatch)
    _never_ask(monkeypatch)
    first = _first(_report())
    assert first.outcome == "not_asked"
    assert first.snapshot is None and first.verify is None
    assert first.undo == firstrun.undo_line(None)
    assert not SnapshotStore().path.exists(), "nothing written, not even the store"


def test_without_a_terminal_it_offers_but_does_not_ask(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _never_ask(monkeypatch)
    lines = _lines(ok("doctor").stdout)
    assert "To take a first snapshot: wowlab doctor --yes (or wowlab snap create)." in lines
    assert lines[-1] == firstrun.undo_line(None)
    assert _manifests() == ()


@pytest.mark.parametrize(("answer", "taken"), [("y\n", True), ("n\n", False), ("", False)])
def test_interactive_first_run_asks_before_the_snapshot(
    root: Path, monkeypatch: pytest.MonkeyPatch, answer: str, taken: bool
) -> None:
    _ask_interactively(monkeypatch)
    result = ok("doctor", input=answer)
    assert (
        f"Take a first snapshot of {FLAVOR} (wow_classic_beta, 1.60.1.69913) into "
        "wowlab's store (about"
    ) in result.stderr
    assert len(_manifests()) == (1 if taken else 0)
    last = _lines(result.stdout)[-1]
    if taken:
        assert "Verified the store" in result.stdout
        assert f"`wowlab snap restore {_manifests()[0].id} --dry-run`" in last
    else:
        assert "No snapshot was taken." in result.stdout
        assert last == firstrun.undo_line(None)


def test_yes_does_not_ask(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _ask_interactively(monkeypatch)
    _never_ask(monkeypatch)
    ok("doctor", "--yes")
    assert len(_manifests()) == 1


def test_offline_says_so_and_makes_no_request(root: Path, replayed: list[httpx.Request]) -> None:
    out = ok("doctor", "--offline", "--yes").stdout
    assert "With --offline, as now, this check sends no request at all." in out
    assert replayed == []
    assert len(_manifests()) == 1


def test_a_later_run_is_not_a_first_run(root: Path) -> None:
    ok("snap", "create")
    out = ok("doctor").stdout
    assert "First run" not in out
    assert "To undo" not in out
    assert _report().first_run is None


def test_a_snapshot_of_another_install_still_leaves_this_one_a_first_run(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = tmp_path / "Other" / "World of Warcraft"
    shutil.copytree(root, other)
    ok("snap", "create", "--root", str(other))
    assert _first(_report()).outcome == "not_asked"


def test_no_install_says_how_to_point_at_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(install.ENV_ROOT, raising=False)
    monkeypatch.setattr(install, "default_roots", lambda **k: (tmp_path / "absent",))
    result = run("doctor", "--offline")
    assert result.exit_code == 1
    out = result.stdout
    assert out.startswith("First run: wowlab has no snapshot of this install yet.")
    assert 'wowlab doctor --root "<that folder>"' in out
    assert 'export WOWLAB_WOW_ROOT="<that folder>"' in out
    assert '$env:WOWLAB_WOW_ROOT = "<that folder>"' in out
    assert "the folder that holds .build.info" in out
    assert _lines(out)[-1] == firstrun.undo_line(None)
    first = _first(_report("--offline", code=1))
    assert first.outcome == "no_install"


def test_a_root_that_is_not_an_install_says_how_to_point_at_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(install.ENV_ROOT, raising=False)
    (tmp_path / "empty").mkdir()
    result = run("doctor", "--offline", "--root", str(tmp_path / "empty"))
    assert result.exit_code == 1
    assert "is not a WoW install" in result.stdout
    assert 'wowlab doctor --root "<that folder>"' in result.stdout


def test_a_bad_flavor_fails_before_any_request(root: Path, replayed: list[httpx.Request]) -> None:
    result = run("doctor", "--flavor", "_absent_")
    assert result.exit_code == 2
    assert "no flavor folder '_absent_'" in result.stderr
    assert replayed == []
    assert _manifests() == ()


# ─── edge cases (constructed) ────────────────────────────────────────────────


def _install_under(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *parents: str) -> Path:
    """The copied install moved below `parents` (constructed sync folders)."""
    src = Path(os.environ[install.ENV_ROOT])
    dest = tmp_path.joinpath(*parents, "World of Warcraft")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(src, dest)
    monkeypatch.setenv(install.ENV_ROOT, str(dest))
    return dest


@pytest.mark.parametrize(
    ("parents", "marker", "service", "evidence"),
    [
        pytest.param(
            ("OneDrive - Constructed", "Games"),
            None,
            "OneDrive",
            'its name is "OneDrive - Constructed"',
            id="constructed-onedrive-folder-name",
        ),
        pytest.param(
            ("Library", "CloudStorage", "OneDrive-Personal"),
            None,
            "OneDrive",
            'its name is "OneDrive-Personal"',
            id="constructed-onedrive-macos-cloudstorage",
        ),
        pytest.param(
            ("Dropbox (Personal)",),
            None,
            "Dropbox",
            'its name is "Dropbox (Personal)"',
            id="constructed-dropbox-folder-name",
        ),
        pytest.param(
            ("Synced",),
            ("Synced", ".dropbox"),
            "Dropbox",
            "it holds .dropbox",
            id="constructed-dropbox-marker-file",
        ),
        pytest.param(
            ("Shared",),
            ("Shared", ".stfolder"),
            "Syncthing",
            "it holds .stfolder",
            id="constructed-syncthing-marker",
        ),
    ],
)
def test_a_synced_wtf_is_reported_and_never_refused_constructed(
    root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    parents: tuple[str, ...],
    marker: tuple[str, ...] | None,
    service: str,
    evidence: str,
) -> None:
    moved = _install_under(tmp_path, monkeypatch, *parents)
    if marker is not None:
        where = tmp_path.joinpath(*marker)
        if service == "Syncthing":
            where.mkdir()
        else:
            where.write_bytes(b"{}")
    first = _first(_report("--yes"))
    (scan,) = first.flavors
    (hint,) = scan.sync
    assert (hint.service, hint.evidence) == (service, evidence)
    assert first.outcome == "taken", "reported, never refused"
    shutil.rmtree(SnapshotStore().path)  # a first run again, for the text
    text = ok("doctor", "--yes").stdout
    assert f"WTF looks synced by {service}: " in text
    assert "wowlab still reads and snapshots it" in text
    assert text.index("looks synced") < text.index("Taking a snapshot")
    assert _lines(text)[-1].startswith("To undo:")
    assert moved.exists()


def test_a_syncthing_marker_inside_wtf_is_reported_constructed(root: Path, flavor: Path) -> None:
    (flavor / "WTF" / ".stfolder").mkdir()
    (scan,) = _first(_report()).flavors
    assert [(h.service, h.path) for h in scan.sync] == [("Syncthing", str(flavor / "WTF"))]


def test_a_dropbox_settings_folder_is_not_a_synced_folder_constructed(
    root: Path, tmp_path: Path
) -> None:
    """The Dropbox app keeps a `.dropbox` folder of settings in the home
    folder; that says nothing about what is synced (constructed)."""
    (tmp_path / ".dropbox").mkdir()
    (scan,) = _first(_report()).flavors
    assert scan.sync == []


def test_sync_conflict_copies_are_listed_constructed(root: Path, flavor: Path) -> None:
    wtf = flavor / "WTF"
    (wtf / "Config (Constructed's conflicted copy 2026-10-14).wtf").write_bytes(b"x\n")
    (wtf / "Config.sync-conflict-20261014-120000-ABCDEFG.wtf").write_bytes(b"y\n")
    (scan,) = _first(_report()).flavors
    assert sorted(scan.conflict_files) == [
        "WTF/Config (Constructed's conflicted copy 2026-10-14).wtf",
        "WTF/Config.sync-conflict-20261014-120000-ABCDEFG.wtf",
    ]
    out = ok("doctor").stdout
    assert "2 conflict copies left by a sync service (snapshotted like any other file; " in out
    assert "wowlab does not merge them)" in out


def test_one_conflict_copy_is_said_in_the_singular_constructed(root: Path, flavor: Path) -> None:
    (flavor / "WTF" / "Config.sync-conflict-20261014-120000-ABCDEFG.wtf").write_bytes(b"y\n")
    out = ok("doctor").stdout
    assert "  1 conflict copy left by a sync service (snapshotted like any other file; " in out
    assert "wowlab does not merge it)" in out


@pytest.mark.parametrize(
    ("st", "expected"),
    [
        pytest.param(SimpleNamespace(st_file_attributes=0x400000), True, id="constructed-recall"),
        pytest.param(SimpleNamespace(st_file_attributes=0x1000), True, id="constructed-offline"),
        pytest.param(SimpleNamespace(st_flags=0x40000000), True, id="constructed-dataless"),
        pytest.param(SimpleNamespace(st_file_attributes=0x20, st_flags=0), False, id="plain"),
    ],
)
def test_online_only_placeholders_are_recognised_constructed(st: Any, expected: bool) -> None:
    assert firstrun._is_placeholder(st) is expected


def test_placeholders_are_counted_and_said_constructed(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every file stands in for an online-only placeholder (constructed)."""
    monkeypatch.setattr(firstrun, "_is_placeholder", lambda st: True)
    (scan,) = _first(_report()).flavors
    assert scan.placeholders == scan.files > 0
    out = ok("doctor").stdout
    assert f"{scan.files} files online-only: their content is not on this disk" in out
    assert "The estimate does not include that download, and the snapshot fails for any " in out


@pytest.mark.parametrize(
    ("st", "expected"),
    [
        pytest.param(SimpleNamespace(st_file_attributes=0x40000), True, id="constructed-open"),
        pytest.param(SimpleNamespace(st_file_attributes=0x400000), True, id="constructed-data"),
        pytest.param(SimpleNamespace(st_flags=0x40000000), True, id="constructed-dataless"),
        pytest.param(SimpleNamespace(st_file_attributes=0x1010, st_flags=0), False, id="plain"),
    ],
)
def test_online_only_folders_are_recognised_constructed(st: Any, expected: bool) -> None:
    assert firstrun._is_online_only_dir(st) is expected


def test_an_online_only_folder_is_not_listed_and_said_constructed(
    root: Path, flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real folder stands in for one a sync service keeps online-only: the
    scan does not descend into it (listing it would download it) and says
    so (constructed)."""
    online = flavor / "WTF" / "Online"
    online.mkdir()
    (online / "Inside.lua").write_bytes(b"x = 1\n")
    inode = online.stat().st_ino
    monkeypatch.setattr(firstrun, "_is_online_only_dir", lambda st: st.st_ino == inode)
    (scan,) = _first(_report()).flavors
    assert scan.online_only_folders == ["WTF/Online"]
    out = ok("doctor").stdout
    assert (
        "  1 online-only folder, not listed (not counted above; a snapshot would make the " in out
    )
    assert "sync service download it): WTF/Online" in out


@needs_symlinks
def test_links_in_wtf_are_reported_and_never_followed_constructed(
    root: Path, flavor: Path, tmp_path: Path
) -> None:
    outside = _outside(tmp_path)
    sv = flavor / SV_DIR
    (sv / "Linked.lua").symlink_to(outside / "Secret.lua")
    (flavor / "WTF" / "LinkedFolder").symlink_to(outside, target_is_directory=True)
    before = _state(root)

    first = _first(_report("--yes"))
    (scan,) = first.flavors
    assert sorted((link.path, link.kind, link.target) for link in scan.links) == sorted(
        [
            ("WTF/LinkedFolder", "symlink", str(outside)),
            (
                f"WTF/Account/{ACCOUNT}/SavedVariables/Linked.lua",
                "symlink",
                str(outside / "Secret.lua"),
            ),
        ]
    )
    assert first.outcome == "taken"
    assert first.snapshot is not None and first.snapshot.links == 2
    (manifest,) = _manifests()
    links = {e.path: e for e in manifest.entries if e.kind == "symlink"}
    assert links[f"{FLAVOR}/WTF/LinkedFolder"].target == str(outside)
    assert SECRET not in _stored(SnapshotStore()), "what is behind a link is never stored"
    assert _state(root) == before

    shutil.rmtree(SnapshotStore().path)
    out = ok("doctor").stdout
    assert "2 links, never followed: a snapshot records each link" in out
    assert f"    WTF/LinkedFolder -> {outside} (symlink)" in out


@needs_symlinks
def test_wtf_itself_a_link_is_said_plainly_constructed(
    root: Path, flavor: Path, tmp_path: Path
) -> None:
    elsewhere = tmp_path / "Dropbox" / "WTF"
    shutil.move(flavor / "WTF", elsewhere)
    (flavor / "WTF").symlink_to(elsewhere, target_is_directory=True)
    out = ok("doctor", "--yes").stdout
    assert f"    WTF -> {elsewhere} (symlink)" in out
    assert "WTF itself is a link, so the settings and SavedVariables behind it are not" in out
    assert (
        f"WTF is a link into a folder Dropbox appears to sync ({elsewhere}). wowlab does "
        "not follow it, so your settings and SavedVariables are not in this snapshot; back "
        "up the folder it points to yourself."
    ) in out
    assert "  WTF: a link, not followed" in out
    assert "still reads and snapshots it" not in out
    (manifest,) = _manifests()
    wtf = [e for e in manifest.entries if e.path.startswith(f"{FLAVOR}/WTF")]
    assert [(e.path, e.kind) for e in wtf] == [(f"{FLAVOR}/WTF", "symlink")]


def test_a_junction_in_wtf_is_reported_and_never_followed_constructed(
    root: Path, flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real directory stands in for a junction through `snapshot`'s
    `_is_junction` seam, as `test_snapshot_junctions.py` does (constructed)."""
    junction = flavor / "WTF" / "Junction"
    junction.mkdir()
    (junction / "Inside.lua").write_bytes(SECRET)
    real_readlink = Path.readlink

    def is_junction(path: Path | os.DirEntry[str]) -> bool:
        return Path(path) == junction

    def readlink(self: Path) -> Path:
        return Path("D:/Elsewhere/WTF") if self == junction else real_readlink(self)

    monkeypatch.setattr(snapshot, "_is_junction", is_junction)
    monkeypatch.setattr(Path, "readlink", readlink)
    first = _first(_report("--yes"))
    (scan,) = first.flavors
    assert [(lk.path, lk.kind, lk.target) for lk in scan.links] == [
        ("WTF/Junction", "junction", str(Path("D:/Elsewhere/WTF")))
    ]
    (manifest,) = _manifests()
    entry = manifest.entry(f"{FLAVOR}/WTF/Junction")
    assert entry is not None and entry.kind == "symlink"
    assert manifest.entry(f"{FLAVOR}/WTF/Junction/Inside.lua") is None
    assert SECRET not in _stored(SnapshotStore())


def test_a_very_large_wtf_reports_size_and_time_before_snapshotting_constructed(
    root: Path, flavor: Path
) -> None:
    """A sparse file the size of `LARGE_BYTES`: the scan only stats it, so
    nothing is read; without --yes nothing is snapshotted (constructed)."""
    with (flavor / "WTF" / "Huge.lua").open("wb") as handle:
        handle.truncate(firstrun.LARGE_BYTES)
    first = _first(_report())
    (scan,) = first.flavors
    assert scan.large
    assert scan.bytes >= firstrun.LARGE_BYTES
    assert scan.estimated_seconds == firstrun.estimate_seconds(scan.bytes, scan.files)
    assert scan.estimated_seconds >= firstrun.LARGE_BYTES // firstrun.THROUGHPUT_BYTES
    wtf = next(s for s in scan.subtrees if s.path == "WTF")
    assert wtf.bytes >= firstrun.LARGE_BYTES
    out = ok("doctor").stdout
    assert "  WTF: " in out and " GB" in out.split("  WTF: ", 1)[1].split("\n", 1)[0]
    assert "This is large: the snapshot is estimated to take about" in out
    assert _manifests() == ()


def test_a_large_snapshot_is_announced_before_it_is_taken_constructed(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lowered thresholds make the fixture count as large (constructed)."""
    monkeypatch.setattr(firstrun, "LARGE_BYTES", 1)
    out = ok("doctor", "--yes").stdout
    assert out.index("This is large") < out.index("Taking a snapshot")
    assert out.index("it is estimated to take about") < out.index("Taking a snapshot")
    assert len(_manifests()) == 1


def test_two_flavors_are_each_listed_and_yes_needs_a_choice_constructed(
    root: Path, flavor: Path
) -> None:
    _second_flavor(root, flavor)
    report = _report("--yes", code=2)
    first = _first(report)
    assert [s.folder for s in first.flavors] == [FLAVOR, SECOND]
    assert first.outcome == "choose_flavor"
    assert _manifests() == ()
    result = run("doctor", "--yes")
    assert result.exit_code == 2
    assert f"Flavor 1 of 2: {FLAVOR} (wow_classic_beta, 1.60.1.69913)" in result.stdout
    assert f"Flavor 2 of 2: {SECOND} (wow_constructed, no version in .build.info)" in result.stdout
    assert "--yes needs --flavor to say which one to snapshot" in result.stdout
    assert _lines(result.stdout)[-1] == firstrun.undo_line(None)

    chosen = _first(_report("--yes", "--flavor", SECOND))
    assert (chosen.outcome, chosen.chosen) == ("taken", SECOND)
    (manifest,) = _manifests()
    assert manifest.flavor_folder == SECOND


@pytest.mark.parametrize(("answer", "picked"), [("2\n", SECOND), ("9\n1\n", FLAVOR), ("\n", None)])
def test_two_flavors_interactively_the_player_chooses_constructed(
    root: Path,
    flavor: Path,
    monkeypatch: pytest.MonkeyPatch,
    answer: str,
    picked: str | None,
) -> None:
    _second_flavor(root, flavor)
    _ask_interactively(monkeypatch)
    result = ok("doctor", input=answer)
    assert "Snapshot which flavor? Enter 1 to 2, or nothing for none" in result.stderr
    if picked is None:
        assert _manifests() == ()
        assert "No flavor chosen; no snapshot was taken." in result.stdout
    else:
        (manifest,) = _manifests()
        assert manifest.flavor_folder == picked
    if answer.startswith("9"):
        assert "Not a number from 1 to 2: 9" in result.stderr
    assert f"  1) {FLAVOR} (wow_classic_beta, 1.60.1.69913)" in result.stderr
    assert f"  2) {SECOND} (wow_constructed, no version in .build.info)" in result.stderr
    assert "Take a first snapshot" not in result.stderr, "choosing is the answer"
    if picked is not None:
        other = SECOND if picked == FLAVOR else FLAVOR
        line = f"{other} has no snapshot yet: wowlab snap create --flavor {other}"
        assert line in result.stdout
        assert result.stdout.index(line) < result.stdout.index("To undo:")


def test_after_the_snapshot_the_other_flavors_are_named_constructed(
    root: Path, flavor: Path
) -> None:
    _second_flavor(root, flavor)
    first = _first(_report("--yes", "--flavor", FLAVOR))
    assert first.not_snapshotted == [SECOND]


@pytest.mark.parametrize(("answer", "taken"), [("1\ny\n", True), ("1\nn\n", False)])
def test_two_flavors_with_the_game_running_say_so_and_ask_again_constructed(
    root: Path,
    flavor: Path,
    monkeypatch: pytest.MonkeyPatch,
    answer: str,
    taken: bool,
) -> None:
    """The running client is named before the flavor question, and choosing
    a flavor is then not enough: a yes/no follows (constructed)."""
    _second_flavor(root, flavor)
    _running(monkeypatch, flavor)
    _ask_interactively(monkeypatch)
    result = ok("doctor", input=answer)
    assert f"The game is running (World of Warcraft, pid 7300 in {FLAVOR})." in result.stdout
    assert "Snapshot which flavor?" in result.stderr
    assert (
        f"Take a first snapshot of {FLAVOR} (wow_classic_beta, 1.60.1.69913) into "
        "wowlab's store anyway"
    ) in result.stderr
    assert len(_manifests()) == (1 if taken else 0)
    if taken:
        assert _manifests()[0].client_running is True


def test_two_flavors_without_a_terminal_say_how_to_choose_constructed(
    root: Path, flavor: Path
) -> None:
    _second_flavor(root, flavor)
    out = ok("doctor").stdout
    assert "To take a first snapshot, choose a flavor: wowlab doctor --yes --flavor" in out
    assert _manifests() == ()


def test_a_running_game_is_said_plainly_and_the_snapshot_behaves_as_snap_create_constructed(
    root: Path, flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _running(monkeypatch, flavor)
    result = ok("doctor", "--yes")
    out = result.stdout
    assert "The game is running (World of Warcraft, pid 7300" in out
    assert "A snapshot only reads, so it can still be taken now" in out
    assert "On Windows, a file the game holds open may not be readable" in out
    assert "SavedVariables on disk are from your last logout or /reload" in out
    assert out.index("The game is running") < out.index("Taking a snapshot")
    (taken,) = _manifests()
    assert taken.client_running is True
    created = ok("snap", "create")
    assert "wowlab could not confirm the client was closed" in created.stderr
    later = [m for m in _manifests() if m.id != taken.id]
    assert [m.client_running for m in later] == [True]
    assert later[0].subtrees == taken.subtrees, "the same subtrees as snap create"


def test_a_client_that_cannot_be_checked_is_said_plainly_constructed(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_client_running", lambda inst, chosen: None)
    first = _first(_report("--yes"))
    assert first.client_running is None
    out_lines = firstrun._client_lines(None)
    assert out_lines[0].startswith("wowlab cannot tell whether the game is running.")


@needs_symlinks
def test_text_from_the_install_is_escaped_constructed(root: Path, flavor: Path) -> None:
    """A link name and target carrying ESC and OSC sequences reach the
    terminal as `\\x1b` text, never as control codes (`cli._safe`)."""
    (flavor / "WTF" / "evil\x1b[31m").symlink_to("x\x1b]0;title\x07")
    (flavor / "WTF" / "Config (a\u202eb's conflicted copy).wtf").write_bytes(b"x\n")
    result = ok("doctor", "--yes")
    for text in (result.stdout, result.stderr):
        assert "\x1b" not in text and "\x07" not in text and "\u202e" not in text
    assert "WTF/evil\\x1b[31m -> x\\x1b]0;title\\x07 (symlink)" in result.stdout
    assert "\\xe2\\x80\\xae" in result.stdout


def test_a_store_inside_the_install_is_refused_and_nothing_is_written_constructed(
    root: Path, flavor: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first snapshot goes through the same gate as `snap create`: a
    store inside an install is refused before anything is created (L1)."""
    inside = flavor / "WTF" / "store"
    monkeypatch.setattr(snapshot, "default_store_path", lambda: inside)
    before = _state(root)
    result = run("doctor", "--yes")
    assert result.exit_code == cli.EXIT_REFUSED
    assert "The snapshot was refused by the write gate" in result.stdout
    assert _lines(result.stdout)[-1] == firstrun.undo_line(None)
    assert not inside.exists()
    assert _state(root) == before
    first = _first(_report("--yes", code=cli.EXIT_REFUSED))
    assert (first.outcome, first.refused) == ("failed", True)
