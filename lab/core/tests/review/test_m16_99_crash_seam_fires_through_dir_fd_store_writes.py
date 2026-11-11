# Probe from review of m16/99-crash-seam-dirfd-probe; reproduces the M16-93T dir_fd probe repeating its control since CRASH_SCRIPT forces path writes.
"""Constructed (L8: boundary; a one-file tree in `tmp_path`, no install).

`review/test_m16_93t_crash_seam_misses_dir_fd_store_writes.py` was written
when the M16-93T `CRASH_SCRIPT` left `snapshot._WRITE_BY_DIR_FD` as the
store decides it at import (true on POSIX). The script now sets it to False,
so that probe's non-control case takes the path-write route and repeats its
own control (M16-99). The seam's `dir_fd` / `dst_dir_fd` resolution, the
"second net" the graders' docstring names, is then exercised by nothing.

This probe runs the same seam, with the three `SnapshotStore` method
patches left out, and sets `snapshot._WRITE_BY_DIR_FD = True` *after* the
script's own False. The child reports the flag's value once the swap is
made, and a recording layer outside the seam logs every write-open,
rename, replace and link with the descriptor arguments it carried. A
snapshot is then taken through `SnapshotStore.create`.

Expected: the swap took; the store staged through a write-open with
`dir_fd` and the call the seam killed carried a `dst_dir_fd` (the rename
into `objects/`); the child died at the seam (exit 19); no manifest is
stored. Positive control: the script's own False left in place; the child
dies at a path rename into `objects/` that carries no descriptor.

Skipped where the store cannot write through descriptors (Windows), since
forcing the flag there tests nothing the store would do.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_guard_cleanup_crash_m16_93 import CRASH_SCRIPT, DIED_AT_SNAPSHOT

from wowlab_core import snapshot

METHOD_PATCH = (
    'for name in ("create", "add_snapshot", "put_object"):\n'
    "    setattr(snapshot.SnapshotStore, name, die)\n"
)
FORCED_OFF = "snapshot._WRITE_BY_DIR_FD = False\n"
ROUTE = 'if args["route"] == "transaction":'

# Appended after the seam is installed, so it wraps the seam: each call is
# logged (to fd 2, unbuffered) before the seam decides whether to kill it.
RECORDER = r"""
def say(line):
    os.write(2, (line + "\n").encode())


say("flag " + repr(snapshot._WRITE_BY_DIR_FD))


def recording(name, inner, keys):
    def call(*a, **k):
        given = sorted(key for key in keys if k.get(key) is not None)
        say("call " + name + " " + ",".join(given or ["-"]) + " " + repr(a[:2]))
        return inner(*a, **k)

    return call


_seam_os_open = os.open


def recorded_open(path, flags, *a, **k):
    if flags & WRITE_FLAGS:
        given = "dir_fd" if k.get("dir_fd") is not None else "-"
        say("call open " + given + " " + repr(path))
    return _seam_os_open(path, flags, *a, **k)


os.open = recorded_open
for _name in ("replace", "rename", "link"):
    setattr(os, _name, recording(_name, getattr(os, _name), ("src_dir_fd", "dst_dir_fd")))


def die(*a, **k):
    say("died")
    os._exit(19)


m = snapshot.SnapshotStore(store).create(
    Path(args["root"]), ["sub"], label="probe", kind="user")
say("survived " + m.id)
sys.exit(3)
"""


def seam_script(force_dir_fd: bool) -> str:
    assert METHOD_PATCH in CRASH_SCRIPT, "the graders' method patch moved"
    assert ROUTE in CRASH_SCRIPT, "the graders' route split moved"
    head, _ = CRASH_SCRIPT.replace(METHOD_PATCH, "").split(ROUTE, 1)
    assert FORCED_OFF in head, "the graders' script no longer forces path writes"
    assert head.index(FORCED_OFF) < head.index("os.open = os_open"), "the seam moved"
    if force_dir_fd:
        head += "snapshot._WRITE_BY_DIR_FD = True\n"
    return head + RECORDER


def run_child(tmp_path: Path, force_dir_fd: bool) -> tuple[int, list[str], Path]:
    tag = "dirfd" if force_dir_fd else "control"
    root = tmp_path / tag / "inst"
    (root / "sub").mkdir(parents=True)
    (root / "sub" / "a.txt").write_bytes(b"constructed\n")
    user_data = tmp_path / tag / "ud"
    store = user_data / "snapshots"
    script = tmp_path / tag / "child.py"
    script.write_text(seam_script(force_dir_fd), encoding="utf-8")
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
    return done.returncode, done.stderr.splitlines(), store


@pytest.mark.skipif(
    not snapshot._WRITE_BY_DIR_FD, reason="the store writes by path on this platform"
)
@pytest.mark.parametrize(
    "force_dir_fd",
    [
        pytest.param(True, id="constructed-store-forced-to-descriptor-writes"),
        pytest.param(False, id="constructed-positive-control-path-writes"),
    ],
)
def test_constructed_seam_kills_a_snapshot_written_through_descriptors(
    tmp_path: Path, force_dir_fd: bool
) -> None:
    code, lines, store = run_child(tmp_path, force_dir_fd)
    manifests = list((store / "manifests").glob("*.json")) if (store / "manifests").is_dir() else []
    assert f"flag {force_dir_fd!r}" in lines, ("the flag swap did not take", lines[-40:])
    assert code == DIED_AT_SNAPSHOT, ("the snapshot landed past the seam", code, lines[-40:])
    assert not manifests, [m.name for m in manifests]
    assert "died" in lines, lines[-40:]
    calls = [line for line in lines if line.startswith("call ")]
    fatal = lines[lines.index("died") - 1]
    assert fatal.startswith("call "), ("the seam killed something it did not record", lines[-40:])
    descriptor_opens = [c for c in calls if c.startswith("call open dir_fd ")]
    if force_dir_fd:
        assert descriptor_opens, ("no write-open went through dir_fd", calls)
        assert "dst_dir_fd" in fatal.split()[2], ("the kill was not a dst_dir_fd move", fatal)
    else:
        assert not descriptor_opens, ("the control wrote through dir_fd", calls)
        assert fatal.split()[2] == "-", ("the control's kill carried a descriptor", fatal)
        assert "objects" in fatal or "manifests" in fatal, fatal
