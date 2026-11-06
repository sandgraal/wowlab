"""Remove what test runs leave behind, and say how much it was (M13-81).

`make test-clean` runs this. Do not run it while tests run: it leaves an
in-flight session alone only by the lock rule below. It cleans two places
and prunes the uv cache:

- pytest's session temp dirs under `<temp root>/pytest-of-<user>`, where the
  temp root is `$PYTEST_DEBUG_TEMPROOT` or the system temp dir and `<user>`
  is the login name with every non-word character replaced by `_`, as pytest
  names it. Only that directory is looked at. It is opened once, by
  descriptor and without following a symlink, and must be owned by the
  current user and not writable by group or others, as pytest itself
  requires before using it; otherwise nothing under it is touched. Before
  each entry is removed, and before the directory itself, the path is
  checked to still name the directory that was opened; if it does not, the
  run stops ("changed while cleaning"). A numbered session dir
  (`pytest-<n>`) whose `.lock` is younger than LOCK_TIMEOUT (3 days, pytest's
  own rule in `_pytest/pathlib.py`) belongs to a session that may still be
  running and is skipped. Everything else in it is removed, and the
  directory itself when it ends up empty. This needs POSIX (Linux, macOS):
  Windows has no `os.getuid` for the ownership check and cannot open a
  directory by descriptor, so there the directory is reported and left.
- `.pytest_cache` at the root of this checkout.
- `uv cache prune`: drops cache entries no environment uses any more.

It prints each one's size before and after. It refuses, and leaves alone, a
symlink, a target inside `.claude/worktrees` (stale agent worktrees are the
conductor's to clean), and a target inside the checkout that holds a tracked
file. It never looks at a game install.

Removal is `shutil.rmtree` with no retry, relative to the opened directory:
by `dir_fd` on Python 3.11 and later, by `fchdir` into it on 3.9 and 3.10. Where `shutil.rmtree.avoids_symlink_attacks`
is true it walks by descriptor and never follows a symlink; where it is false
the run says so. What it cannot remove (a directory a test left read-only,
say) is reported, not forced.

It runs on the system `python3` (3.9 or later, standard library only), not
under `uv run`: `uv cache prune` waits for every running uv process to let go
of the cache, and `uv run` holds it for as long as its child runs. For the
same reason a prune that has not finished after PRUNE_TIMEOUT seconds (another
uv process is using the cache) is stopped and reported, not waited on.

    python3 scripts/clean_test_artifacts.py
    python3 scripts/clean_test_artifacts.py --no-uv   # leave the uv cache alone
"""

from __future__ import annotations

import argparse
import getpass
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRUNE_TIMEOUT = 120
# pytest's LOCK_TIMEOUT (_pytest/pathlib.py, 3 days): a session dir's `.lock`
# younger than this may belong to a session still running.
LOCK_TIMEOUT = 60 * 60 * 24 * 3
# Open a directory, never through a symlink (0 where the platform lacks a flag).
OPEN_DIR = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
NUMBERED = re.compile(r"pytest-\d+")
WORKTREES = Path(".claude") / "worktrees"

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


def pytest_user(user: str | None = None) -> str:
    """The `<user>` in `pytest-of-<user>`, as pytest's TempPathFactory makes it."""
    if user is None:
        try:
            user = getpass.getuser()
        except (ImportError, KeyError, OSError):
            user = None
    return re.sub(r"[\W]", "_", user or "unknown")


def pytest_temp_root(temproot: Path | None = None, user: str | None = None) -> Path:
    """`<temp root>/pytest-of-<user>`, the directory pytest keeps sessions in."""
    if temproot is None:
        temproot = Path(os.environ.get("PYTEST_DEBUG_TEMPROOT") or tempfile.gettempdir())
    return temproot.resolve() / f"pytest-of-{pytest_user(user)}"


def tree_size(path: Path) -> int:
    """Bytes under `path`, by lstat; symlinks are counted, never followed."""
    try:
        st = path.lstat()
    except OSError:
        return 0
    if not stat.S_ISDIR(st.st_mode):
        return st.st_size
    total = 0
    stack = [path]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            if stat.S_ISDIR(info.st_mode):
                stack.append(Path(entry.path))
            else:
                total += info.st_size
    return total


def human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1000 or unit == "GB":
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1000
    raise AssertionError("unreachable")


def _within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _tracked(repo: Path, path: Path, run: Runner) -> bool | None:
    """Whether git tracks a file under `path`; None when git cannot say."""
    try:
        done = run(
            ["git", "-C", str(repo), "ls-files", "-z", "--", str(path.relative_to(repo))],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if done.returncode != 0:
        return None
    return bool(done.stdout)


def refusal(path: Path, repo: Path, run: Runner) -> str | None:
    """Why `path` must not be removed, or None when it may be."""
    if path.is_symlink():
        return "it is a symlink"
    repo = repo.resolve()
    resolved = path.resolve()
    if _within(resolved, repo / WORKTREES):
        return f"it is inside {WORKTREES.as_posix()}"
    if _within(resolved, repo):
        tracked = _tracked(repo, resolved, run)
        if tracked is None:
            return "git could not say whether it holds a tracked file"
        if tracked:
            return "it holds a file git tracks"
    return None


def ownership_refusal(st: os.stat_result) -> str | None:
    """Why the pytest dir is not ours to clean: pytest's own check (POSIX only)."""
    getuid = getattr(os, "getuid", None)
    if getuid is None:
        return "the ownership check needs POSIX (no os.getuid here)"
    if st.st_uid != getuid():
        return f"it is owned by uid {st.st_uid}, not {getuid()}"
    if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        return f"it is writable by group or others (mode {stat.S_IMODE(st.st_mode):o})"
    return None


def _fresh(lock: os.stat_result, now: float | None) -> bool:
    return lock.st_mtime > (time.time() if now is None else now) - LOCK_TIMEOUT


def in_flight(session: Path, now: float | None = None) -> bool:
    """Whether a numbered session dir holds a fresh `.lock` (pytest's rule)."""
    if not NUMBERED.fullmatch(session.name):
        return False
    try:
        return _fresh((session / ".lock").lstat(), now)
    except OSError:
        return False


def _in_flight_at(dir_fd: int, name: str) -> bool:
    """`in_flight`, for the entry `name` of the directory open as `dir_fd`."""
    if not NUMBERED.fullmatch(name):
        return False
    try:
        return _fresh(os.stat(f"{name}/.lock", dir_fd=dir_fd, follow_symlinks=False), None)
    except OSError:
        return False


def _rmtree(
    path: Path | str, out: list[str], dir_fd: int | None = None, base: Path | None = None
) -> bool:
    """shutil.rmtree, no retry and no chmod; report what it could not remove."""
    failed: list[str] = []

    def record(_func: Callable[..., object], name: str, _exc: object) -> None:
        failed.append(name)

    if sys.version_info >= (3, 12):  # this script also runs on 3.9
        shutil.rmtree(path, onexc=record, dir_fd=dir_fd)
    elif sys.version_info >= (3, 11):
        shutil.rmtree(path, onerror=record, dir_fd=dir_fd)
    else:  # Python 3.9 and 3.10: no dir_fd (see _rmtree_in)
        shutil.rmtree(path, onerror=record)
    if failed:
        shown = path if base is None else base / path
        first = failed[0] if base is None else base / failed[0]
        out.append(f"could not remove {len(failed)} entries under {shown}, first {first}")
    return not failed


def _rmtree_in(dir_fd: int, name: str, base: Path, out: list[str]) -> bool:
    """Remove the entry `name` of the directory open as `dir_fd`, never by its path.

    On 3.11 and later, where rmtree walks by descriptor, `rmtree(name,
    dir_fd=...)`. Otherwise (3.9 and 3.10, the macOS system python3, or a
    platform where `avoids_symlink_attacks` is False and `dir_fd` raises
    NotImplementedError) the process changes into the opened directory with
    `fchdir` and removes `name` relative to it, then changes back.
    """
    if sys.version_info >= (3, 11) and shutil.rmtree.avoids_symlink_attacks:
        try:
            return _rmtree(name, out, dir_fd=dir_fd, base=base)
        except NotImplementedError:
            pass
    saved = os.open(".", os.O_RDONLY)
    try:
        os.fchdir(dir_fd)
        return _rmtree(name, out, base=base)
    finally:
        os.fchdir(saved)
        os.close(saved)


def remove(path: Path, repo: Path, run: Runner, out: list[str]) -> bool | None:
    """Remove the directory `path` if it may be; True removed, None absent, False refused."""
    if not path.exists() and not path.is_symlink():
        return None
    reason = refusal(path, repo, run)
    if reason is None and not path.is_dir():
        reason = "it is not a directory"
    if reason is not None:
        out.append(f"refused {path}: {reason}; left as it is")
        return False
    return _rmtree(path, out)


def _same(dir_fd: int, path: Path) -> bool:
    """Whether `path` still names the directory open as `dir_fd`."""
    try:
        return os.path.samestat(os.fstat(dir_fd), os.lstat(path))
    except OSError:
        return False


def clean_pytest_dir(path: Path, repo: Path, run: Runner, out: list[str]) -> bool | None:
    """Clean `pytest-of-<user>`, leaving running sessions; True done, None absent, False not."""
    if not path.exists() and not path.is_symlink():
        return None
    reason = refusal(path, repo, run)
    if reason is not None:
        out.append(f"refused {path}: {reason}; nothing under it was touched")
        return False
    try:
        dir_fd = os.open(path, OPEN_DIR)
    except OSError as err:
        out.append(
            f"refused {path}: it cannot be opened as a directory without following "
            f"a link ({err.strerror}); nothing under it was touched"
        )
        return False
    try:
        return _clean_open_dir(dir_fd, path, out)
    finally:
        os.close(dir_fd)


def _clean_open_dir(dir_fd: int, path: Path, out: list[str]) -> bool:
    reason = ownership_refusal(os.fstat(dir_fd))
    if reason is not None:
        out.append(f"refused {path}: {reason}; nothing under it was touched")
        return False
    changed = f"{path} changed while cleaning; nothing more removed"
    try:
        names = sorted(os.listdir(dir_fd))
    except OSError as err:
        out.append(f"could not list {path}: {err}")
        return False
    ok = True
    kept: list[str] = []
    for name in names:
        if not _same(dir_fd, path):
            out.append(changed)
            return False
        if _in_flight_at(dir_fd, name):
            kept.append(name)
            continue
        try:
            st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
        except OSError as err:
            out.append(f"could not read {path / name}: {err}")
            ok = False
            continue
        if stat.S_ISDIR(st.st_mode):
            ok = _rmtree_in(dir_fd, name, path, out) and ok
            continue
        if stat.S_ISLNK(st.st_mode):
            # `pytest-current` points at the newest session: kept while that
            # session runs. Only the link is removed, never its target.
            try:
                target = os.readlink(name, dir_fd=dir_fd)
            except OSError:
                target = ""
            if target and _in_flight_at(dir_fd, Path(target).name):
                continue
        try:
            os.unlink(name, dir_fd=dir_fd)
        except OSError as err:
            out.append(f"could not remove {path / name}: {err}")
            ok = False
    for name in kept:
        out.append(f"left {path / name}: a test session may still be running (fresh .lock)")
    if not _same(dir_fd, path):
        out.append(changed)
        return False
    try:
        if not os.listdir(dir_fd):
            path.rmdir()
    except OSError as err:
        out.append(f"could not remove {path}: {err}")
        ok = False
    return ok


def uv_binary() -> str:
    return os.environ.get("UV") or "uv"


def uv_cache_dir(run: Runner) -> Path | None:
    try:
        done = run([uv_binary(), "cache", "dir"], capture_output=True, text=True, check=False)
    except OSError:
        return None
    if done.returncode != 0 or not done.stdout.strip():
        return None
    return Path(done.stdout.strip())


def uv_prune(run: Runner, out: list[str]) -> bool:
    try:
        done = run(
            [uv_binary(), "cache", "prune"],
            capture_output=True,
            text=True,
            check=False,
            timeout=PRUNE_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        out.append(
            f"uv cache prune did not finish in {PRUNE_TIMEOUT} s: another uv process is "
            "using the cache. Run `make test-clean` again when nothing else runs uv."
        )
        return False
    except OSError as err:
        out.append(f"uv cache prune could not run: {err}")
        return False
    if done.returncode != 0:
        out.append(f"uv cache prune failed ({done.returncode}): {done.stderr.strip()}")
        return False
    return True


def main(argv: Sequence[str] | None = None, run: Runner = subprocess.run) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        epilog="The pytest temp dir is cleaned on POSIX only (Linux, macOS): its "
        "ownership check needs os.getuid. Do not run this while tests run.",
    )
    parser.add_argument("--repo", type=Path, default=ROOT, help="the checkout (default: this one)")
    parser.add_argument("--temproot", type=Path, help="pytest's temp root (default: as pytest)")
    parser.add_argument("--user", help="the user in pytest-of-<user> (default: as pytest)")
    parser.add_argument("--no-uv", action="store_true", help="leave the uv cache alone")
    args = parser.parse_args(argv)

    repo: Path = args.repo.resolve()
    pytest_dir = pytest_temp_root(args.temproot, args.user)
    notes: list[str] = []
    if not shutil.rmtree.avoids_symlink_attacks:
        print(
            "note: shutil.rmtree on this platform does not guard against symlink "
            "races (avoids_symlink_attacks is False)",
            file=sys.stderr,
        )
    rows = []
    ok = True
    before = tree_size(pytest_dir)
    if clean_pytest_dir(pytest_dir, repo, run, notes) is False:
        ok = False
    rows.append(("pytest temp dirs", pytest_dir, before, tree_size(pytest_dir)))
    cache_dir = repo / ".pytest_cache"
    before = tree_size(cache_dir)
    if remove(cache_dir, repo, run, notes) is False:
        ok = False
    rows.append((".pytest_cache", cache_dir, before, tree_size(cache_dir)))

    if not args.no_uv:
        cache = uv_cache_dir(run)
        if cache is None:
            notes.append("uv not found or `uv cache dir` failed; the uv cache was left alone")
            ok = False
        else:
            before = tree_size(cache)
            if not uv_prune(run, notes):
                ok = False
            rows.append(("uv cache", cache, before, tree_size(cache)))

    for label, path, before, after in rows:
        print(f"{label:<17} {human(before):>9} -> {human(after):>9}  {path}")
    for note in notes:
        print(note, file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
