# Probe from review of m10/11-guard-tests-2; reproduces KillSwitch and
# JournalAheadSpy not seeing an install mutation made relative to a directory
# descriptor, so the write-ahead graders fail a guard using the dir_fd defence.
"""The parent-swap graders in `test_guard.py` name "opening relative to a
directory descriptor taken before the check (POSIX)" as a sanctioned defence,
and `ReplaceSpy` resolves `src_dir_fd` / `dst_dir_fd` through the descriptor
"so a guard using that defence is recorded, not failed". The write-ahead
graders added on this branch do not: `_lexical` returns None for a call with
`dir_fd`, `KillSwitch._count` skips None, and `JournalAheadSpy._check` skips
None. A guard that does `os.replace(tmp, name, src_dir_fd=d, dst_dir_fd=d)`
or `os.unlink(name, dir_fd=d)` is never killed ("DID NOT RAISE") and never
checked (the "every touched path was seen changing" positive control fails),
although it may be write-ahead and durable.

Constructed: a directory in `tmp_path` stands in for the install; no guard,
no install, no service is involved. The `*-by-path` cases are the positive
control: the same mutation spelled by path (`Path.replace` and `Path.unlink`
call `os.replace` and `os.unlink`) is seen today.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from wowlab_core.snapshot import SnapshotStore

# `os.replace` is renameat too, but only `os.rename` is listed in supports_dir_fd.
pytestmark = pytest.mark.skipif(
    os.rename not in os.supports_dir_fd or os.unlink not in os.supports_dir_fd,
    reason="dir_fd is a POSIX facility",
)

REL = "WTF/Config.wtf"
BEFORE = b"constructed: before\n"
AFTER = b"constructed: after\n"


def _graders() -> types.ModuleType:
    path = Path(__file__).resolve().parent.parent / "test_guard.py"
    spec = importlib.util.spec_from_file_location("_review_m10_11t_graders", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


G = _graders()


def _tree(tmp_path: Path) -> tuple[Path, Path, SnapshotStore]:
    install = tmp_path / "install"
    flavor = install / "_flavor_"
    (flavor / "WTF").mkdir(parents=True)
    (flavor / REL).write_bytes(BEFORE)
    return install, flavor, SnapshotStore(tmp_path / "store")


def _wtf_fd(flavor: Path) -> int:
    return os.open(flavor / "WTF", os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))


def _replace_by_path(flavor: Path) -> None:
    (flavor / "WTF" / "tmp").write_bytes(AFTER)
    (flavor / "WTF" / "tmp").replace(flavor / REL)


def _replace_by_dir_fd(flavor: Path) -> None:
    (flavor / "WTF" / "tmp").write_bytes(AFTER)
    fd = _wtf_fd(flavor)
    try:
        os.replace("tmp", "Config.wtf", src_dir_fd=fd, dst_dir_fd=fd)
    finally:
        os.close(fd)


def _unlink_by_path(flavor: Path) -> None:
    (flavor / REL).unlink()


def _unlink_by_dir_fd(flavor: Path) -> None:
    fd = _wtf_fd(flavor)
    try:
        os.unlink("Config.wtf", dir_fd=fd)
    finally:
        os.close(fd)


MUTATIONS: dict[str, Callable[[Path], None]] = {
    "replace-by-path": _replace_by_path,
    "replace-by-dir-fd": _replace_by_dir_fd,
    "unlink-by-path": _unlink_by_path,
    "unlink-by-dir-fd": _unlink_by_dir_fd,
}
CASES = [pytest.param(how, id=f"constructed-{how}") for how in MUTATIONS]


@pytest.mark.parametrize("how", CASES)
def test_kill_switch_counts_an_install_mutation_however_it_is_spelled(
    tmp_path: Path, how: str
) -> None:
    install, flavor, store = _tree(tmp_path)
    killer: Any = G.KillSwitch(install, store.path, after=1)
    with pytest.MonkeyPatch.context() as patched:
        killer.arm(patched)
        with pytest.raises(G.SimulatedCrashError):
            MUTATIONS[how](flavor)
    assert killer.killed
    target = flavor / REL
    assert not target.exists() or target.read_bytes() == AFTER, "the mutation landed"


@pytest.mark.parametrize("how", CASES)
def test_journal_ahead_spy_checks_an_install_mutation_however_it_is_spelled(
    tmp_path: Path, how: str
) -> None:
    _install, flavor, store = _tree(tmp_path)
    store.path.mkdir(parents=True)
    spy: Any = G.JournalAheadSpy(types.SimpleNamespace(path=flavor), store, {REL: None})
    with pytest.MonkeyPatch.context() as patched:
        spy.arm(patched)
        MUTATIONS[how](flavor)
    # No journal exists here, so a spy that saw the mutation reports a problem;
    # one that did not see it has checked nothing and reports nothing.
    assert spy.checked == {REL}
    assert spy.problems == [f"{REL}: not in the journal when it was changed"]
