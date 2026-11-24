# Probe from review of m22/16-addon-provenance; reproduces `addonsource.read_wowup` (so `wowlab addons list` and the UI's Addons panel) blocking forever on a FIFO named addons.json
"""`read_wowup` opens WowUp's `addons.json` with a plain `Path.open("rb")`
before it looks at what the path is. A FIFO in its place blocks that open
until a writer appears, so `wowlab addons list`, which before M22-16 read
only the install, now hangs on a file outside it. Every other reader that
can meet a hostile local path refuses a non-regular file without blocking
(`luadata`, `addoninstall`, `crewdb`, `snaparchive`: `O_NONBLOCK` and
`S_ISREG`).

Constructed hostile input, POSIX only (skipped where `os.mkfifo` is
missing). A watchdog opens the FIFO's write end after `BLOCKED_AFTER`
seconds and closes it, so the probe fails fast and never hangs. The
positive control reads a regular file in the same folder within the bound.

The expectation is only that the read returns within the bound and does not
claim to have read records; whether a FIFO is `unreadable` or `missing` is
the implementer's call.
"""

from __future__ import annotations

import os
import shutil
import threading
import time
from pathlib import Path

import pytest

from wowlab_core import addonsource

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "wowup" / "macos" / "addons.json"
BLOCKED_AFTER = 3.0


def _unblock(path: Path, done: threading.Event) -> None:
    if done.wait(BLOCKED_AFTER):
        return
    fd = os.open(path, os.O_WRONLY)  # the reader is waiting, so this opens
    os.close(fd)


def test_a_regular_file_is_read_within_the_bound_constructed(tmp_path: Path) -> None:
    shutil.copyfile(FIXTURE, tmp_path / "addons.json")
    start = time.monotonic()
    file = addonsource.read_wowup(tmp_path)
    assert time.monotonic() - start < BLOCKED_AFTER
    assert file.state == "read" and len(file.records) == 6


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no FIFOs here")
def test_a_fifo_named_addons_json_does_not_block_constructed(tmp_path: Path) -> None:
    fifo = tmp_path / "addons.json"
    os.mkfifo(fifo)
    done = threading.Event()
    watchdog = threading.Thread(target=_unblock, args=(fifo, done), daemon=True)
    watchdog.start()
    start = time.monotonic()
    try:
        file = addonsource.read_wowup(tmp_path)
    finally:
        done.set()
        watchdog.join(BLOCKED_AFTER + 5)
    elapsed = time.monotonic() - start
    assert elapsed < BLOCKED_AFTER, f"read_wowup blocked {elapsed:.1f} s on a FIFO"
    assert file.state != "read"
