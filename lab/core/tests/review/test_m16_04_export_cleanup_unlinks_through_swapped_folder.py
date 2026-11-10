# Probe from review of m16/04-snap-export-import; reproduces: export's failure cleanup
# `target.unlink()` removes by path, so a folder on the way swapped for a link into an
# install after the file was created deletes the install's file of that name (L1/L2).
"""Constructed (L8 hostile/boundary): a small tree, a constructed install with
`.build.info` and a `WTF/Config.wtf` of its own, and a swap of the export's
parent folder for a link into that install, made at the second destination
check (the moment the module's own comment names: "a folder on the way
swapped for a link into an install after the first check"). The export is
refused, as it should be, but its cleanup unlinks `<folder>/Config.wtf` by
path, through the link, and the install's own Config.wtf is gone.

Positive control: the same swap with no file of the export's name in the
install still refuses, and the install is unchanged.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from wowlab_core import guard, snaparchive
from wowlab_core.snapshot import SnapshotStore

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX symlinks")


def _setup(tmp_path: Path, name: str) -> tuple[SnapshotStore, str, Path, Path]:
    src = tmp_path / "source"
    (src / "WTF").mkdir(parents=True)
    (src / "WTF" / "Config.wtf").write_bytes(b"constructed\n")
    store = SnapshotStore(tmp_path / "store")
    manifest = store.create(src, ["WTF"], label="constructed")
    install = tmp_path / "install"
    (install / "_flavor_" / "WTF").mkdir(parents=True)
    (install / ".build.info").write_bytes(b"constructed\n")
    (install / "_flavor_" / "WTF" / "Config.wtf").write_bytes(b"SET the player's own\n")
    out = tmp_path / "out"
    out.mkdir()
    return store, manifest.id, install, out / name


def _swap_on_second_check(monkeypatch: pytest.MonkeyPatch, out: Path, install: Path) -> None:
    real = guard._refuse_inside_any_install
    calls = {"n": 0}

    def racing(path: Path, what: str) -> Path:
        calls["n"] += 1
        if calls["n"] == 2:  # the file now exists in out/; swap out/ for a link
            out.rename(out.with_name("out.moved"))
            out.symlink_to(install / "_flavor_" / "WTF", target_is_directory=True)
        return real(path, what)

    monkeypatch.setattr(guard, "_refuse_inside_any_install", racing)


def test_export_cleanup_deletes_the_installs_file_through_a_swapped_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, sid, install, dest = _setup(tmp_path, "Config.wtf")
    victim = install / "_flavor_" / "WTF" / "Config.wtf"
    _swap_on_second_check(monkeypatch, dest.parent, install)
    with pytest.raises(guard.GuardError):
        snaparchive.export_snapshot(store, sid, dest)
    assert victim.exists(), "export removed the install's own Config.wtf (L1/L2)"


def test_positive_control_no_same_named_file_in_the_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, sid, install, dest = _setup(tmp_path, "carried.zip")
    before = sorted(p.name for p in (install / "_flavor_" / "WTF").iterdir())
    _swap_on_second_check(monkeypatch, dest.parent, install)
    with pytest.raises(guard.GuardError):
        snaparchive.export_snapshot(store, sid, dest)
    assert sorted(p.name for p in (install / "_flavor_" / "WTF").iterdir()) == before
