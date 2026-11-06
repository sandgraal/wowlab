"""`make test-clean` (scripts/clean_test_artifacts.py, M13-81), on temp directories.

Every test builds its own temp root and checkout under `tmp_path`: nothing
here looks at the real `$TMPDIR`, the real `.pytest_cache` or the real uv
cache. git runs for real (in a throwaway repository); uv is replaced by a
fake runner. The cases named `constructed` are constructed layouts, not
captures: there is no external format here.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "clean_test_artifacts", SCRIPTS / "clean_test_artifacts.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


clean = _load()


def _git_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    return path


def _private(temproot: Path, user: str = "alice") -> None:
    """Make `pytest-of-<user>` 0o700, as pytest creates it, whatever the umask."""
    mine = temproot / f"pytest-of-{user}"
    if mine.is_dir() and not mine.is_symlink():
        mine.chmod(0o700)


def _fill(path: Path, size: int = 1000) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "data.bin").write_bytes(b"x" * size)


class FakeUv:
    """git runs for real; `uv cache dir` and `uv cache prune` are answered here."""

    def __init__(self, cache: Path, prune: str = "ok") -> None:
        self.cache = cache
        self.prune = prune
        self.calls: list[list[str]] = []

    def __call__(self, cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if cmd[0] == "git":
            done: subprocess.CompletedProcess[str] = subprocess.run(cmd, **kwargs)
            return done
        self.calls.append(cmd)
        if cmd[1:] == ["cache", "dir"]:
            return subprocess.CompletedProcess(cmd, 0, f"{self.cache}\n", "")
        if cmd[1:] == ["cache", "prune"]:
            assert kwargs.get("timeout") == clean.PRUNE_TIMEOUT
            if self.prune == "timeout":
                raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])
            (self.cache / "stale.bin").unlink()
            return subprocess.CompletedProcess(cmd, 0, "", "")
        raise AssertionError(f"unexpected command {cmd}")


@pytest.fixture
def layout(tmp_path: Path) -> dict[str, Path]:
    temproot = tmp_path / "tmp"
    repo = _git_repo(tmp_path / "repo")
    mine = temproot / "pytest-of-alice"
    _fill(mine / "pytest-7" / "test_x0", 3000)
    # A symlink out of the tree.
    outside = tmp_path / "outside"
    _fill(outside, 50)
    (mine / "pytest-current").symlink_to(outside, target_is_directory=True)
    _private(temproot)
    # Neighbours that must stay.
    _fill(temproot / "pytest-of-bob", 70)
    (temproot / "unrelated.txt").write_text("keep\n", encoding="utf-8")
    _fill(repo / ".pytest_cache" / "v" / "cache", 200)
    cache = tmp_path / "uv-cache"
    _fill(cache, 400)
    (cache / "stale.bin").write_bytes(b"s" * 600)
    return {
        "temproot": temproot,
        "repo": repo,
        "mine": mine,
        "outside": outside,
        "cache": cache,
    }


def test_removes_the_users_pytest_dir_and_pytest_cache_and_prints_sizes(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    uv = FakeUv(layout["cache"])
    argv = ["--repo", str(layout["repo"]), "--temproot", str(layout["temproot"]), "--user", "alice"]
    assert clean.main(argv, run=uv) == 0

    assert not layout["mine"].exists()
    assert not (layout["repo"] / ".pytest_cache").exists()
    # Not followed, not touched.
    assert (layout["outside"] / "data.bin").is_file()
    assert (layout["temproot"] / "pytest-of-bob" / "data.bin").is_file()
    assert (layout["temproot"] / "unrelated.txt").is_file()
    assert (layout["repo"] / ".git").is_dir()
    assert [c[1:] for c in uv.calls] == [["cache", "dir"], ["cache", "prune"]]

    out = capsys.readouterr().out.splitlines()
    by_label = {line.split("  ")[0].strip(): line for line in out}
    # 3000 bytes of files plus the symlink itself (its lstat size).
    assert re.search(r" 3\.\d KB ->       0 B ", by_label["pytest temp dirs"])
    assert "200 B ->       0 B" in by_label[".pytest_cache"]
    assert "1.0 KB ->     400 B" in by_label["uv cache"]


def test_constructed_a_wrong_target_name_deletes_nothing(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    # Constructed: the user is "carol", so the target is pytest-of-carol,
    # which does not exist; pytest-of-alice and pytest-of-bob stay.
    argv = ["--repo", str(layout["repo"]), "--temproot", str(layout["temproot"]), "--user", "carol"]
    assert clean.main([*argv, "--no-uv"], run=FakeUv(layout["cache"])) == 0
    assert (layout["mine"] / "pytest-7" / "test_x0" / "data.bin").is_file()
    assert (layout["temproot"] / "pytest-of-bob" / "data.bin").is_file()
    assert "0 B ->       0 B" in capsys.readouterr().out


def test_constructed_a_symlink_with_the_right_name_is_refused(tmp_path: Path) -> None:
    temproot = tmp_path / "tmp"
    target = tmp_path / "elsewhere"
    _fill(target)
    temproot.mkdir()
    (temproot / "pytest-of-alice").symlink_to(target, target_is_directory=True)
    repo = _git_repo(tmp_path / "repo")
    argv = ["--repo", str(repo), "--temproot", str(temproot), "--user", "alice", "--no-uv"]
    assert clean.main(argv) == 1
    assert (temproot / "pytest-of-alice").is_symlink()
    assert (target / "data.bin").is_file()


def test_constructed_a_pytest_cache_holding_a_tracked_file_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = _git_repo(tmp_path / "repo")
    _fill(repo / ".pytest_cache")
    subprocess.run(["git", "-C", str(repo), "add", "-f", ".pytest_cache/data.bin"], check=True)
    argv = ["--repo", str(repo), "--temproot", str(tmp_path / "tmp"), "--user", "a", "--no-uv"]
    assert clean.main(argv) == 1
    assert (repo / ".pytest_cache" / "data.bin").is_file()
    assert "holds a file git tracks" in capsys.readouterr().err


def test_constructed_nothing_under_claude_worktrees_is_removed(tmp_path: Path) -> None:
    repo = _git_repo(tmp_path / "repo")
    inside = repo / ".claude" / "worktrees" / "agent-x" / ".pytest_cache"
    _fill(inside)
    notes: list[str] = []
    assert clean.remove(inside, repo, subprocess.run, notes) is False
    assert (inside / "data.bin").is_file()
    assert ".claude/worktrees" in notes[0]


def test_a_prune_blocked_by_another_uv_process_is_reported_not_waited_on(
    layout: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    uv = FakeUv(layout["cache"], prune="timeout")
    argv = ["--repo", str(layout["repo"]), "--temproot", str(layout["temproot"]), "--user", "alice"]
    assert clean.main(argv, run=uv) == 1
    captured = capsys.readouterr()
    assert "another uv process is using the cache" in captured.err
    assert "1.0 KB ->    1.0 KB" in captured.out
    # The other targets were still cleaned.
    assert not layout["mine"].exists()


def test_the_user_is_named_as_pytest_names_it() -> None:
    assert clean.pytest_user("DOMAIN\\some user") == "DOMAIN_some_user"
    assert clean.pytest_user("") == "unknown"
    root = clean.pytest_temp_root(Path("/x"), "alice")
    assert root.name == "pytest-of-alice"


def test_the_default_temp_root_follows_pytest_debug_temproot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PYTEST_DEBUG_TEMPROOT", str(tmp_path))
    assert clean.pytest_temp_root(user="alice") == tmp_path.resolve() / "pytest-of-alice"


def test_this_sessions_own_temp_dir_is_where_the_script_looks(
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    # pytest's own base temp dir (an xdist worker's is one level deeper) sits
    # inside the directory the script removes, unless --basetemp is given.
    base = tmp_path_factory.getbasetemp().resolve()
    owners = [p for p in (base, *base.parents) if p.name.startswith("pytest-of-")]
    if not owners:
        pytest.skip("this session runs with --basetemp")
    assert clean.pytest_temp_root() == owners[0]


ROOT_USER = getattr(os, "geteuid", lambda: -1)() == 0


def _argv(temproot: Path, repo: Path) -> list[str]:
    return ["--repo", str(repo), "--temproot", str(temproot), "--user", "alice", "--no-uv"]


def test_constructed_a_read_only_dir_is_reported_never_chmodded(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Constructed: a test left a read-only directory with a file in it. As
    # root it is removable; otherwise removal fails, is reported, and the
    # directory's mode is not changed (no chmod-and-retry, security review M1).
    temproot = tmp_path / "tmp"
    locked = temproot / "pytest-of-alice" / "pytest-3" / "test_locked0"
    _fill(locked, 10)
    locked.chmod(0o500)
    repo = _git_repo(tmp_path / "repo")
    _private(temproot)
    code = clean.main(_argv(temproot, repo))
    if ROOT_USER:
        assert code == 0
        assert not (temproot / "pytest-of-alice").exists()
        return
    try:
        assert code == 1
        assert stat.S_IMODE(locked.stat().st_mode) == 0o500
        assert (locked / "data.bin").is_file()
        assert "could not remove" in capsys.readouterr().err
    finally:
        locked.chmod(0o700)


def test_constructed_a_symlinks_target_is_never_changed(tmp_path: Path) -> None:
    # Constructed: inside a session dir, a symlink to a read-only directory
    # outside the tree. The link goes; the target, its file and its mode stay
    # (security review M1: nothing follows a link).
    temproot = tmp_path / "tmp"
    session = temproot / "pytest-of-alice" / "pytest-4"
    _fill(session / "test_a0")
    outside = tmp_path / "outside"
    _fill(outside)
    outside.chmod(0o500)
    (session / "test_a0" / "link").symlink_to(outside, target_is_directory=True)
    (session / "test_a0" / "file-link").symlink_to(outside / "data.bin")
    repo = _git_repo(tmp_path / "repo")
    _private(temproot)
    try:
        assert clean.main(_argv(temproot, repo)) == 0
        assert not (temproot / "pytest-of-alice").exists()
        assert stat.S_IMODE(outside.stat().st_mode) == 0o500
        assert (outside / "data.bin").read_bytes() == b"x" * 1000
    finally:
        outside.chmod(0o700)


@pytest.mark.parametrize("mode", [0o777, 0o775, 0o757], ids=["world", "group", "others"])
def test_constructed_a_group_or_world_writable_pytest_dir_is_refused(
    tmp_path: Path, mode: int, capsys: pytest.CaptureFixture[str]
) -> None:
    # Constructed: pytest-of-alice writable by others (security review M2).
    temproot = tmp_path / "tmp"
    mine = temproot / "pytest-of-alice"
    _fill(mine / "pytest-1")
    mine.chmod(mode)
    repo = _git_repo(tmp_path / "repo")
    assert clean.main(_argv(temproot, repo)) == 1
    assert (mine / "pytest-1" / "data.bin").is_file()
    assert "writable by group or others" in capsys.readouterr().err


def test_constructed_a_pytest_dir_owned_by_another_user_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Constructed: the directory's owner is not the current uid (security
    # review M2). The current uid is faked rather than the owner changed, so
    # this runs without root.
    temproot = tmp_path / "tmp"
    mine = temproot / "pytest-of-alice"
    _fill(mine / "pytest-1")
    repo = _git_repo(tmp_path / "repo")
    _private(temproot)
    owner = mine.lstat().st_uid
    monkeypatch.setattr(os, "getuid", lambda: owner + 1)
    assert clean.main(_argv(temproot, repo)) == 1
    assert (mine / "pytest-1" / "data.bin").is_file()
    assert f"owned by uid {owner}" in capsys.readouterr().err


def test_constructed_a_running_session_is_left_by_pytests_lock_rule(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Constructed: pytest-7 holds a fresh .lock (a session still running),
    # pytest-6 a .lock older than LOCK_TIMEOUT (a crashed session), pytest-5
    # none, and pytest-current points at pytest-7 (review CODE 3).
    temproot = tmp_path / "tmp"
    mine = temproot / "pytest-of-alice"
    for n in (5, 6, 7):
        _fill(mine / f"pytest-{n}" / "test_a0")
    (mine / "pytest-7" / ".lock").write_text("", encoding="utf-8")
    stale = mine / "pytest-6" / ".lock"
    stale.write_text("", encoding="utf-8")
    old = time.time() - clean.LOCK_TIMEOUT - 60
    os.utime(stale, (old, old))
    (mine / "pytest-current").symlink_to(mine / "pytest-7", target_is_directory=True)
    (mine / "garbage-1234").mkdir()
    repo = _git_repo(tmp_path / "repo")
    _private(temproot)
    assert clean.main(_argv(temproot, repo)) == 0
    assert sorted(p.name for p in mine.iterdir()) == ["pytest-7", "pytest-current"]
    assert (mine / "pytest-7" / "test_a0" / "data.bin").is_file()
    assert "a test session may still be running" in capsys.readouterr().err


def test_constructed_the_current_link_to_a_finished_session_goes(tmp_path: Path) -> None:
    temproot = tmp_path / "tmp"
    mine = temproot / "pytest-of-alice"
    _fill(mine / "pytest-8")
    (mine / "pytest-9").mkdir()
    (mine / "pytest-9" / ".lock").write_text("", encoding="utf-8")
    (mine / "pytest-current").symlink_to(mine / "pytest-8", target_is_directory=True)
    repo = _git_repo(tmp_path / "repo")
    _private(temproot)
    assert clean.main(_argv(temproot, repo)) == 0
    assert [p.name for p in mine.iterdir()] == ["pytest-9"]


def test_in_flight_follows_the_numbered_name_and_the_lock_age(tmp_path: Path) -> None:
    for name in ("pytest-12", "pytest-x", "other"):
        (tmp_path / name).mkdir()
        (tmp_path / name / ".lock").write_text("", encoding="utf-8")
    now = time.time()
    assert clean.in_flight(tmp_path / "pytest-12", now)
    assert not clean.in_flight(tmp_path / "pytest-12", now + clean.LOCK_TIMEOUT + 1)
    assert not clean.in_flight(tmp_path / "pytest-x", now)
    assert not clean.in_flight(tmp_path / "other", now)


def test_the_lock_timeout_is_pytests_own() -> None:
    from _pytest.pathlib import LOCK_TIMEOUT

    assert clean.LOCK_TIMEOUT == LOCK_TIMEOUT == 60 * 60 * 24 * 3


def test_constructed_a_dir_swapped_for_a_link_after_the_check_removes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Constructed: right after the ownership check passes on the opened
    # directory, pytest-of-alice is moved aside and replaced by a symlink to
    # a victim directory (security review round 2). Nothing is removed from
    # either: the path no longer names the directory that was opened.
    temproot = tmp_path / "tmp"
    mine = temproot / "pytest-of-alice"
    _fill(mine / "pytest-1")
    victim = tmp_path / "victim"
    _fill(victim / "pytest-2")
    repo = _git_repo(tmp_path / "repo")
    _private(temproot)
    real_check = clean.ownership_refusal

    def check_then_swap(st: os.stat_result) -> str | None:
        reason = real_check(st)
        mine.rename(tmp_path / "moved")
        mine.symlink_to(victim, target_is_directory=True)
        return reason

    monkeypatch.setattr(clean, "ownership_refusal", check_then_swap)
    assert clean.main(_argv(temproot, repo)) == 1
    assert "changed while cleaning; nothing more removed" in capsys.readouterr().err
    assert (victim / "pytest-2" / "data.bin").is_file()
    assert (tmp_path / "moved" / "pytest-1" / "data.bin").is_file()
    assert mine.is_symlink()


@pytest.mark.parametrize("guarded", [True, False], ids=["dir-fd-raises", "unguarded"])
def test_constructed_rmtree_without_dir_fd_support_falls_back_to_fchdir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, guarded: bool
) -> None:
    # Constructed: a platform where `shutil.rmtree(..., dir_fd=...)` raises
    # NotImplementedError (avoids_symlink_attacks False on 3.11+). The session
    # dirs are still removed, relative to the opened directory, the cwd is
    # restored, and the .pytest_cache step still runs.
    temproot = tmp_path / "tmp"
    _fill(temproot / "pytest-of-alice" / "pytest-1" / "test_a0")
    repo = _git_repo(tmp_path / "repo")
    _fill(repo / ".pytest_cache")
    _private(temproot)
    real = shutil.rmtree
    calls: list[str] = []

    def no_dir_fd(path: Any, *args: Any, **kwargs: Any) -> None:
        if kwargs.get("dir_fd") is not None:
            calls.append("dir_fd")
            raise NotImplementedError("dir_fd unavailable on this platform")
        calls.append(str(path))
        real(path, *args, **kwargs)

    no_dir_fd.avoids_symlink_attacks = guarded  # type: ignore[attr-defined]
    monkeypatch.setattr(clean.shutil, "rmtree", no_dir_fd)
    cwd = Path.cwd()
    assert clean.main(_argv(temproot, repo)) == 0
    assert Path.cwd() == cwd
    assert not (temproot / "pytest-of-alice").exists()
    assert not (repo / ".pytest_cache").exists()
    assert "pytest-1" in calls  # removed by its name, relative to the opened dir
