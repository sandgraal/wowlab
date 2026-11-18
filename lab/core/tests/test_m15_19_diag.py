"""TEMPORARY diagnostic for M15-19; removed before the final run."""

from __future__ import annotations

import os
import time
from pathlib import Path


def _tree(tmp_path: Path) -> Path:
    """The sweep's _tree, with a pause between steps so each lands on a
    later clock tick (as a loaded runner does)."""
    root = tmp_path / "World of Warcraft"
    pause = 0.03
    (root / "_forever_" / "WTF").mkdir(parents=True)
    time.sleep(pause)
    (root / "_forever_" / "WTF" / "Config.wtf").write_bytes(b'SET a "1"\n')
    time.sleep(pause)
    app = root / "_forever_" / "World of Warcraft.app" / "Contents" / "MacOS"
    app.mkdir(parents=True)
    time.sleep(pause)
    (app / "World of Warcraft").write_bytes(b"constructed")
    time.sleep(pause)
    (root / "Data" / "data").mkdir(parents=True)
    time.sleep(pause)
    (root / "Data" / "data" / "data.000").write_bytes(b"0123456789")
    time.sleep(pause)
    (root / ".build.info").write_bytes(b"constructed\n")
    return root


def _look(root: Path) -> dict[str, tuple[int, int]]:
    """(DirEntry.stat mtime from the parent's listing, os.lstat mtime) per dir."""
    out: dict[str, tuple[int, int]] = {}
    stack = [root]
    while stack:
        folder = stack.pop()
        with os.scandir(folder) as it:
            children = sorted(it, key=lambda e: e.name)
        for child in children:
            if child.is_dir(follow_symlinks=False):
                entry = child.stat(follow_symlinks=False).st_mtime_ns
                real = os.lstat(child.path).st_mtime_ns
                out[str(Path(child.path).relative_to(root))] = (entry, real)
                stack.append(Path(child.path))
    return out


def test_m15_19_diag(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    first = _look(root)
    second = _look(root)
    third = _look(root)
    print("\nM15-19 DIAG  key: entry-stat(parent listing) / os.lstat(handle)")
    for key in sorted(first):
        a, b, c = first[key], second[key], third[key]
        print(
            f"M15-19 {key!r}: look1 entry={a[0]} lstat={a[1]} stale={a[0] != a[1]} | "
            f"look2 entry={b[0]} lstat={b[1]} | look3 entry={c[0]} lstat={c[1]}"
        )
    lstat_stable = all(first[k][1] == second[k][1] == third[k][1] for k in first)
    entry_stable = all(first[k][0] == second[k][0] == third[k][0] for k in first)
    print(f"M15-19 lstat stable across looks: {lstat_stable}; entry stable: {entry_stable}")


def test_m15_19_sweep_old_vs_new(tmp_path: Path) -> None:
    """The sweep's own fingerprint twice, with DirEntry.stat (old) and with
    os.lstat (new), each on a fresh paused tree."""
    import test_l1_sweep_live as sweep

    old_root = _tree(tmp_path / "old")
    real_lstat = os.lstat

    # old: the listing entry's stat, as main does
    def old_fingerprint(root: Path) -> dict[str, object]:
        found = {}
        stack = [root]
        while stack:
            folder = stack.pop()
            with os.scandir(folder) as it:
                for child in sorted(it, key=lambda e: e.name):
                    st = child.stat(follow_symlinks=False)
                    found[child.path] = st.st_mtime_ns
                    if child.is_dir(follow_symlinks=False):
                        stack.append(Path(child.path))
        return found

    a, b = old_fingerprint(old_root), old_fingerprint(old_root)
    old_moved = sorted(k for k in a if a[k] != b[k])
    new_root = _tree(tmp_path / "new")
    lines = sweep.differences(
        sweep.install_fingerprint(new_root), sweep.install_fingerprint(new_root)
    )
    print(f"\nM15-19 OLD (DirEntry.stat) moved between two looks: {old_moved}")
    for k in old_moved:
        print(f"M15-19 OLD   {k}: {a[k]} -> {b[k]} (lstat now {real_lstat(k).st_mtime_ns})")
    print(f"M15-19 NEW (os.lstat) differences between two looks: {lines}")
