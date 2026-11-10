# Probe from review of m16/04-snap-export-import; reproduces: `_open_checked_folder` compares
# two stats taken through the same (swapped) path, so a folder swapped for a link into an
# install between the check and the open is not refused and the export file is created there.
"""Constructed (L8 hostile/boundary). The export's folder is swapped for a
link into an install's WTF just after the first destination check returns.
`_open_checked_folder` docstring: "a folder swapped in between is refused".
It opens `target.parent` and compares it with `resolved.parent.stat()`, both
read through the swapped link at the same moment, so they always agree; the
file is then created inside the install (the second check refuses and
removes it, but the install's folder was written).

Positive control: with no swap the export succeeds and nothing appears in
the install.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from wowlab_core import guard, snaparchive
from wowlab_core.snapshot import SnapshotError, SnapshotStore

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX symlinks")


def _setup(tmp_path: Path) -> tuple[SnapshotStore, str, Path, Path]:
    src = tmp_path / "source"
    (src / "_f_" / "WTF").mkdir(parents=True)
    (src / "_f_" / "WTF" / "Config.wtf").write_bytes(b"constructed\n")
    store = SnapshotStore(tmp_path / "store")
    manifest = store.create(src, ["_f_/WTF"], label="constructed", flavor_folder="_f_")
    install = tmp_path / "install"
    wtf = install / "_flavor_" / "WTF"
    wtf.mkdir(parents=True)
    (install / ".build.info").write_bytes(b"constructed\n")
    out = tmp_path / "out"
    out.mkdir()
    return store, manifest.id, wtf, out


def _watch_install(monkeypatch: pytest.MonkeyPatch, wtf: Path) -> list[list[str]]:
    seen: list[list[str]] = []
    real_open = snaparchive.os.open

    def spy_open(*a: object, **k: object) -> int:
        fd = real_open(*a, **k)  # type: ignore[arg-type]
        seen.append(sorted(p.name for p in wtf.iterdir()))
        return fd

    monkeypatch.setattr(snaparchive.os, "open", spy_open)
    return seen


def test_a_folder_swapped_after_the_check_is_refused_before_a_file_is_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, sid, wtf, out = _setup(tmp_path)
    real = guard._refuse_inside_any_install
    calls = {"n": 0}

    def racing(path: Path, what: str) -> Path:
        calls["n"] += 1
        checked = real(path, what)
        if calls["n"] == 1:  # checked as outside; now swap the folder
            out.rename(out.with_name("out.moved"))
            out.symlink_to(wtf, target_is_directory=True)
        return checked

    monkeypatch.setattr(guard, "_refuse_inside_any_install", racing)
    seen = _watch_install(monkeypatch, wtf)
    with pytest.raises((SnapshotError, guard.GuardError)):
        snaparchive.export_snapshot(store, sid, out / "carried.zip")
    assert not any("carried.zip" in listing for listing in seen), (
        f"the export file was created inside the install: {seen}"
    )


def test_positive_control_no_swap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store, sid, wtf, out = _setup(tmp_path)
    seen = _watch_install(monkeypatch, wtf)
    snaparchive.export_snapshot(store, sid, out / "carried.zip")
    assert (out / "carried.zip").is_file()
    assert all(listing == [] for listing in seen)
