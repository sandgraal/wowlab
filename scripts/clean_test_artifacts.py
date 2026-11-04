"""Remove what test runs leave behind, and say how much it was (M13-81).

`make test-clean` runs this. It removes exactly two directories and prunes
the uv cache:

- pytest's session temp dirs, `<temp root>/pytest-of-<user>`, where the temp
  root is `$PYTEST_DEBUG_TEMPROOT` or the system temp dir and `<user>` is the
  login name with every non-word character replaced by `_`, as pytest names
  it. Only a directory with exactly that name is removed.
- `.pytest_cache` at the root of this checkout.
- `uv cache prune`: drops cache entries no environment uses any more.

It prints each one's size before and after. It refuses, and leaves alone, a
target with another name, a symlink, a target inside `.claude/worktrees`
(stale agent worktrees are the conductor's to clean), and a target inside
the checkout that holds a tracked file. It never looks at a game install.

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
from collections.abc import Callable, Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRUNE_TIMEOUT = 120
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


def refusal(path: Path, expected_name: str, repo: Path, run: Runner) -> str | None:
    """Why `path` must not be removed, or None when it may be."""
    if path.name != expected_name:
        return f"its name is {path.name!r}, not {expected_name!r}"
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


def _make_writable(name: str) -> None:
    path = Path(name)
    for target in (path.parent, path):
        try:
            mode = target.lstat().st_mode
            if stat.S_ISDIR(mode):
                target.chmod(mode | stat.S_IRWXU)
        except OSError:
            pass


def _rmtree(path: Path) -> None:
    """shutil.rmtree, also through directories a test left read-only."""

    def retry(func: Callable[[str], object], name: str, _exc: object) -> None:
        _make_writable(name)
        func(name)

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=retry)
    else:  # Python 3.9 to 3.11: the same handler under its older name
        shutil.rmtree(path, onerror=retry)


def remove(path: Path, expected_name: str, repo: Path, run: Runner, out: list[str]) -> bool | None:
    """Remove the directory `path` if it may be; True removed, None absent, False refused."""
    if not path.exists() and not path.is_symlink():
        return None
    reason = refusal(path, expected_name, repo, run)
    if reason is None and not path.is_dir():
        reason = "it is not a directory"
    if reason is not None:
        out.append(f"refused {path}: {reason}; left as it is")
        return False
    try:
        _rmtree(path)
    except OSError as err:
        out.append(f"failed to remove {path}: {err}")
        return False
    return True


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
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", type=Path, default=ROOT, help="the checkout (default: this one)")
    parser.add_argument("--temproot", type=Path, help="pytest's temp root (default: as pytest)")
    parser.add_argument("--user", help="the user in pytest-of-<user> (default: as pytest)")
    parser.add_argument("--no-uv", action="store_true", help="leave the uv cache alone")
    args = parser.parse_args(argv)

    repo: Path = args.repo.resolve()
    pytest_dir = pytest_temp_root(args.temproot, args.user)
    targets = [
        ("pytest temp dirs", pytest_dir, pytest_dir.name),
        (".pytest_cache", repo / ".pytest_cache", ".pytest_cache"),
    ]
    notes: list[str] = []
    rows = []
    ok = True
    for label, path, name in targets:
        before = tree_size(path)
        if remove(path, name, repo, run, notes) is False:
            ok = False
        rows.append((label, path, before, tree_size(path)))

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
