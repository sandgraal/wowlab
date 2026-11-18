"""TEMPORARY diagnostic for M15-19; removed before the final run."""

from __future__ import annotations

import os
from pathlib import Path


def _tree(tmp_path: Path) -> Path:
    root = tmp_path / "World of Warcraft"
    (root / "_forever_" / "WTF").mkdir(parents=True)
    (root / "_forever_" / "WTF" / "Config.wtf").write_bytes(b'SET a "1"\n')
    app = root / "_forever_" / "World of Warcraft.app" / "Contents" / "MacOS"
    app.mkdir(parents=True)
    (app / "World of Warcraft").write_bytes(b"constructed")
    (root / "Data" / "data").mkdir(parents=True)
    (root / "Data" / "data" / "data.000").write_bytes(b"0123456789")
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
