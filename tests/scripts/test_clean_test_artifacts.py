"""`make test-clean` (scripts/clean_test_artifacts.py, M13-81), on temp directories.

Every test builds its own temp root and checkout under `tmp_path`: nothing
here looks at the real `$TMPDIR`, the real `.pytest_cache` or the real uv
cache. git runs for real (in a throwaway repository); uv is replaced by a
fake runner. The cases named `constructed` are constructed layouts, not
captures: there is no external format here.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
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
    # A directory a test left read-only, and a symlink out of the tree.
    locked = mine / "pytest-7" / "test_locked0"
    _fill(locked, 10)
    locked.chmod(0o500)
    outside = tmp_path / "outside"
    _fill(outside, 50)
    (mine / "pytest-current").symlink_to(outside, target_is_directory=True)
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
    # 3010 bytes of files plus the symlink itself (its lstat size).
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

    # Constructed: asked directly to remove a directory whose name is not the
    # expected one, it refuses and the directory stays.
    notes: list[str] = []
    wrong = layout["temproot"] / "pytest-of-bob"
    assert clean.remove(wrong, "pytest-of-alice", layout["repo"], subprocess.run, notes) is False
    assert (wrong / "data.bin").is_file()
    assert notes and "not 'pytest-of-alice'" in notes[0]


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
    assert clean.remove(inside, ".pytest_cache", repo, subprocess.run, notes) is False
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
