# Probe from review of m16/93t-cleanup-crash-graders; reproduces the crash seam's file-level half never firing on POSIX, where the store writes through dir_fd.
"""Constructed (L8: boundary; a two-file tree in `tmp_path`, no install).

The M16-93T graders (`test_guard_cleanup_crash_m16_93.py`) kill a child
"at the first sign of the pre-write snapshot landing: a call to
`SnapshotStore.create`, `add_snapshot` or `put_object`, or any write-open,
rename, replace or link into the store's `objects/` or `manifests/`". The
second half is the part that does not depend on which store method M16-69
calls. On POSIX `snapshot._WRITE_BY_DIR_FD` is decided at import (before
the child patches `os`) and is true, so the store stages under `tmp/` and
renames into `objects/` and `manifests/` with `dir_fd=` / `dst_dir_fd=`:
the seam skips every `os.open` with a `dir_fd`, and its rename check reads
`dst` (a bare name) against the child's cwd. A whole snapshot lands and the
child lives.

This probe runs the graders' own `CRASH_SCRIPT` seam with the three method
patches left out and takes a snapshot through `SnapshotStore.create`
(standing in for any entry point M16-69 might take it through other than
those three names). Expected: the child dies (exit 19) and no manifest is
stored. Positive control: the same child with `_WRITE_BY_DIR_FD` forced
off (the path-based store writes, as on Windows) dies at the first rename
into `objects/`.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_guard_cleanup_crash_m16_93 import CRASH_SCRIPT, DIED_AT_SNAPSHOT

METHOD_PATCH = (
    'for name in ("create", "add_snapshot", "put_object"):\n'
    "    setattr(snapshot.SnapshotStore, name, die)\n"
)
ROUTE = 'if args["route"] == "transaction":'


def seam_only(force_path_writes: bool) -> str:
    assert METHOD_PATCH in CRASH_SCRIPT and ROUTE in CRASH_SCRIPT, "the graders' seam moved"
    script = CRASH_SCRIPT.replace(METHOD_PATCH, "").split(ROUTE, 1)[0]
    if force_path_writes:
        script += "snapshot._WRITE_BY_DIR_FD = False\n"
    script += (
        "m = snapshot.SnapshotStore(store).create(\n"
        '    Path(args["root"]), ["sub"], label="probe", kind="user")\n'
        'sys.stdout.write("survived " + m.id + "\\n")\n'
        "sys.exit(3)\n"
    )
    return script


def run_child(tmp_path: Path, force_path_writes: bool) -> tuple[int, str, Path]:
    tag = "control" if force_path_writes else "probe"
    root = tmp_path / tag / "inst"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "a.txt").write_bytes(b"constructed\n")
    user_data = tmp_path / tag / "ud"
    store = user_data / "snapshots"
    script = tmp_path / tag / "child.py"
    script.write_text(seam_only(force_path_writes), encoding="utf-8")
    args = {
        "user_data": str(user_data),
        "flavor": str(root),
        "store": str(store),
        "mode": "die-at-snapshot",
        "root": str(root),
    }
    done = subprocess.run(
        [sys.executable, str(script), json.dumps(args)],
        capture_output=True,
        text=True,
        cwd=tmp_path / tag,
        timeout=120,
        check=False,
    )
    return done.returncode, done.stdout + done.stderr[-2000:], store


@pytest.mark.parametrize(
    "force_path_writes",
    [
        pytest.param(False, id="constructed-store-as-it-writes-here"),
        pytest.param(True, id="constructed-positive-control-path-writes"),
    ],
)
def test_constructed_seam_kills_a_snapshot_written_into_objects_or_manifests(
    tmp_path: Path, force_path_writes: bool
) -> None:
    code, out, store = run_child(tmp_path, force_path_writes)
    manifests = list((store / "manifests").glob("*.json")) if (store / "manifests").is_dir() else []
    assert code == DIED_AT_SNAPSHOT, (
        "a snapshot written into objects/ and manifests/ landed without the seam firing",
        code,
        out,
        [m.name for m in manifests],
    )
    assert not manifests
