"""process: is a World of Warcraft client running? (docs/LAB_PLAN.md §6.7, M10-09)

Scope (L7, ADR-0023): this module lists processes through `psutil` and reads
five things from each one: `pid`, `name()`, `exe()`, `cmdline()` and
`status()`. Nothing else is read or called. No process memory, no handles
opened on the client by this code, no signals, no open-file or connection
listing, no `ctypes`. The one thing it sets is a temporary instance attribute
named `cmdline` (below). Tests hold the module to exactly that surface.

`cmdline()` and Windows. On Windows psutil implements `cmdline()` by opening
the target with PROCESS_VM_READ and reading its PEB with ReadProcessMemory.
That is a process memory read and out of scope whatever it is used for, so on
Windows `psutil.Process.cmdline()` must not execute at all. There are three
ways it could, and this is what is done about each:

- this module calling it: it does not when `sys.platform == "win32"`;
- psutil's public `exe()` calling it: `exe()` is not a thin wrapper. When the
  platform layer denies access or returns "", it calls `self.cmdline()` and
  returns argv[0] if that is an executable file. So for the length of every
  `exe()` call, on every platform, `cmdline` is shadowed on the instance with
  a stub that returns an empty list, and the instance is put back exactly as
  it was in a `finally`. This uses only public names; no private psutil
  attribute is read. Only real `psutil.Process` objects are touched; an
  injected fake is never modified;
- psutil's public `name()` calling it: it does so only on POSIX (to repair a
  truncated name). On Windows `name()` is the file name of the platform
  layer's exe and never reaches `cmdline()`.

On Windows, then, the calls made on a process are `pid`, `name()`, `exe()`
(platform layer only) and `status()`, plus one assignment and one deletion of
the instance attribute `cmdline`. On other platforms `cmdline()` is a kernel
query, and this module calls it itself only when `exe()` gave nothing.

Fail closed: a process about which nothing could be learned is reported as
`unknown`, and `guard` treats unknown as running (ADR-0021). `argv[0]` is
chosen by the process, so it may add a match and may never clear a process;
the shadowing above is also what guarantees that a path labelled
`exe_source="exe"` came from the operating system and not from psutil's
argv[0] guess. An error psutil did not classify makes that one process
`unknown`.

Threads: `psutil.process_iter()` returns cached `Process` objects shared by
every caller in the interpreter, and the shadow is a set-call-restore sequence
on those shared objects, so `running_clients` holds one module-level lock for
the whole inspection loop and concurrent calls run one after another. The lock
is not re-entrant: a `process_iter` callable must not call back into this
module.

Nothing here knows a flavor (L6). Install roots and flavor folder names come
from the caller, which gets them from discovery (`install`, M10-05).
"""

from __future__ import annotations

import sys
import threading
import unicodedata
from collections.abc import Callable, Iterable, Sequence
from enum import StrEnum
from pathlib import Path
from typing import Literal, Protocol

import psutil
from pydantic import BaseModel, ConfigDict

__all__ = [
    "KNOWN_CLIENT_NAMES",
    "ClientProcess",
    "ClientState",
    "ProcessIter",
    "ProcessLike",
    "client_state",
    "running_clients",
]

# Process names of the client executable, as the operating system reports them
# (docs/LAB_PLAN.md §6.7). These are executable names, not flavor folders. The
# Forever client's name is recorded by M10-03; until then callers pass what
# discovery finds through `extra_names`, and the path match covers the rest.
KNOWN_CLIENT_NAMES: tuple[str, ...] = (
    "Wow.exe",
    "WowClassic.exe",
    "WowT.exe",
    "WowB.exe",
    "World of Warcraft",
    "World of Warcraft Classic",
)

# Some platforms cut the kernel's copy of a process name (15 bytes on Linux,
# 16 on macOS). psutil repairs it from the command line when it may read one;
# when it may not, a name this long can be a prefix of the real one.
_TRUNCATED_NAME_MIN = 15

# psutil status values (plain strings) for a process that no longer runs and
# so holds no file open.
_GONE_STATUSES = frozenset({psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD})


class ClientState(StrEnum):
    """`guard` refuses on `RUNNING` and on `UNKNOWN`."""

    RUNNING = "running"
    UNKNOWN = "unknown"
    NOT_RUNNING = "not_running"


class ProcessLike(Protocol):
    """The whole of `psutil.Process` this module is allowed to touch."""

    @property
    def pid(self) -> int: ...
    def name(self) -> str: ...
    def exe(self) -> str: ...
    def cmdline(self) -> list[str]: ...
    def status(self) -> str: ...


ProcessIter = Callable[[], Iterable[ProcessLike]]


class ClientProcess(BaseModel):
    """One process that is, or cannot be shown not to be, a game client."""

    model_config = ConfigDict(frozen=True)

    pid: int
    state: Literal[ClientState.RUNNING, ClientState.UNKNOWN]
    # How a RUNNING process was recognised; None for UNKNOWN.
    matched_by: Literal["path", "name"] | None = None
    name: str | None = None
    exe: Path | None = None
    # "cmdline" means `exe()` was unavailable and the path is argv[0], which a
    # process chooses for itself. It is only ever used to add a match.
    exe_source: Literal["exe", "cmdline"] | None = None
    # The install root (as the caller spelled it) the executable lives under.
    install_root: Path | None = None
    # Set only when the executable sits under `<install_root>/<flavor folder>/`
    # for a flavor folder the caller named.
    flavor_folder: str | None = None
    status: str | None = None


class _GoneError(Exception):
    """The process exited (or is a zombie) while it was being inspected."""


def _system_processes() -> Iterable[ProcessLike]:
    return psutil.process_iter()


def _cmdline_is_a_listing_call() -> bool:
    """False on Windows, where `psutil.Process.cmdline()` is not a listing
    call: it opens the target with PROCESS_VM_READ and reads its PEB with
    ReadProcessMemory. That is a process memory read (L7, ADR-0023), so this
    module does not call it there. This switch covers the module's own call
    only; `_os_reported_exe` is what keeps psutil's `exe()` from making the
    same call behind it. The cost on Windows is the argv[0] fallback, which
    could only ever add a match."""
    return sys.platform != "win32"


_SHADOWED = "cmdline"
_ABSENT = object()

# psutil.process_iter() hands every caller the same cached Process objects, so
# two threads inspecting at once could interleave the set and the delete of the
# shadow: one thread's exe() would run unshadowed (on Windows, the PEB read)
# and the other's delete would raise. One inspection runs at a time.
_SHADOW_LOCK = threading.Lock()


def _no_argv() -> list[str]:
    return []


def _os_reported_exe(proc: ProcessLike) -> str:
    """`proc.exe()` restricted to what the platform layer reports.

    `psutil.Process.exe()` falls back to `self.cmdline()` and returns argv[0]
    when the platform denies access or has no path. That fallback must not run:
    on Windows it is a process memory read (L7), and everywhere it would hand
    back a path the process chose for itself under a label that says the
    operating system reported it. While `exe()` runs, `cmdline` is shadowed on
    the instance so the fallback sees an empty command line and gives up,
    re-raising the original AccessDenied or returning "". The shadow is always
    removed: psutil caches Process objects between `process_iter` calls.

    Only a real `psutil.Process` (by type, so a mock with a spec does not
    count) has that fallback, and only one is ever touched. Any other injected
    object is simply asked for its exe and is never modified. Afterwards the
    instance holds exactly what it held before: the shadow is deleted if there
    was no instance attribute `cmdline`, and the previous value is put back if
    there was. A `psutil.Process` that refuses the assignment is not asked for
    its exe at all. Callers hold `_SHADOW_LOCK`; the set-call-restore sequence
    is not safe to interleave on psutil's shared, cached objects.
    """
    if not issubclass(type(proc), psutil.Process):
        return proc.exe()
    held = vars(proc)
    previous = held.get(_SHADOWED, _ABSENT)
    try:
        setattr(proc, _SHADOWED, _no_argv)
    except (AttributeError, TypeError):
        return ""
    try:
        return proc.exe()
    finally:
        if previous is _ABSENT:
            delattr(proc, _SHADOWED)
        else:
            setattr(proc, _SHADOWED, previous)


def _fold(text: str) -> str:
    """Comparison key for names and path parts: case-insensitive, and blind to
    Unicode normalisation form (APFS and HFS+ hand back NFD where a caller
    may hold NFC). A false match only ever makes `guard` more cautious."""
    return unicodedata.normalize("NFC", text).casefold()


def _no_bare_string(value: object, parameter: str) -> None:
    """A `str` is an iterable of characters; `extra_names="Wow.exe"` would
    make every process called "w" a client."""
    if isinstance(value, (str, bytes)):
        raise TypeError(
            f"{parameter} takes an iterable of values, not a bare {type(value).__name__}"
        )


def _read_name(proc: ProcessLike) -> str | None:
    try:
        return proc.name() or None
    except psutil.NoSuchProcess as exc:
        raise _GoneError from exc
    except psutil.AccessDenied:
        return None


def _read_path(proc: ProcessLike) -> tuple[Path, Literal["exe", "cmdline"]] | None:
    try:
        exe = _os_reported_exe(proc)
    except psutil.NoSuchProcess as exc:
        raise _GoneError from exc
    except psutil.AccessDenied:
        exe = ""
    if exe:
        return Path(exe), "exe"
    if not _cmdline_is_a_listing_call():
        return None
    try:
        argv = proc.cmdline()
    except psutil.NoSuchProcess as exc:
        raise _GoneError from exc
    except psutil.AccessDenied:
        return None
    if argv and argv[0] and Path(argv[0]).is_absolute():
        return Path(argv[0]), "cmdline"
    return None


def _read_status(proc: ProcessLike) -> str | None:
    try:
        return proc.status()
    except psutil.NoSuchProcess as exc:
        raise _GoneError from exc
    except psutil.AccessDenied:
        return None


def _parts_under(path: Path, root: Path) -> tuple[str, ...] | None:
    """Parts of `path` below `root`, or None. Case-insensitive on purpose:
    Windows and default macOS volumes are, and a false match only ever makes
    `guard` more cautious."""
    path_parts, root_parts = path.parts, root.parts
    if len(path_parts) <= len(root_parts):
        return None
    for ours, theirs in zip(root_parts, path_parts, strict=False):
        if _fold(ours) != _fold(theirs):
            return None
    return path_parts[len(root_parts) :]


def _root_spellings(root: Path) -> tuple[Path, ...]:
    """The root as given and with symlinks resolved. Reads only (L1)."""
    absolute = root.absolute()
    if len(absolute.parts) < 2:
        raise ValueError(f"not an install root: {root!s}")
    resolved = absolute.resolve()
    return (absolute,) if resolved == absolute else (absolute, resolved)


def _could_be_truncated(name: str, names: Sequence[str]) -> bool:
    if len(name) < _TRUNCATED_NAME_MIN:
        return False
    folded = _fold(name)
    return any(len(k) > len(name) and _fold(k).startswith(folded) for k in names)


def running_clients(
    install_roots: Iterable[Path] = (),
    *,
    flavor_folders: Iterable[str] = (),
    extra_names: Iterable[str] = (),
    process_iter: ProcessIter | None = None,
) -> list[ClientProcess]:
    """Processes that are a game client, or might be and could not be read.

    A process is `RUNNING` when its executable lives under one of
    `install_roots` (`matched_by="path"`) or when its name, or the file name of
    its executable, is one of `KNOWN_CLIENT_NAMES` or `extra_names`
    (`matched_by="name"`). It is `UNKNOWN` when neither a name nor an
    operating-system-reported executable path could be read, when the only
    name read may be a truncated client name, or when inspecting it failed
    with an error psutil did not classify. An `argv[0]` path can add a match
    but never clears a process. Anything else is not reported. Exited and
    zombie processes are skipped.

    `process_iter` replaces `psutil.process_iter` in tests and in `guard`'s
    graders; it returns objects with the `ProcessLike` surface.
    """
    _no_bare_string(install_roots, "install_roots")
    _no_bare_string(flavor_folders, "flavor_folders")
    _no_bare_string(extra_names, "extra_names")
    roots = [(root, _root_spellings(Path(root))) for root in install_roots]
    flavors = {_fold(folder): folder for folder in flavor_folders}
    names = (*KNOWN_CLIENT_NAMES, *extra_names)
    folded_names = {_fold(n) for n in names}

    found: list[ClientProcess] = []
    with _SHADOW_LOCK:
        for proc in (process_iter or _system_processes)():
            pid = proc.pid
            try:
                found_one = _inspect(proc, pid, roots, flavors, names, folded_names)
            except _GoneError:
                continue
            except (OSError, psutil.Error):
                # Not a denial and not an exit: something psutil did not classify.
                # Nothing was learned, so the module fails closed on this process.
                found_one = ClientProcess(pid=pid, state=ClientState.UNKNOWN)
            if found_one is not None:
                found.append(found_one)
    return sorted(found, key=lambda c: c.pid)


def _inspect(
    proc: ProcessLike,
    pid: int,
    roots: Sequence[tuple[Path, tuple[Path, ...]]],
    flavors: dict[str, str],
    names: Sequence[str],
    folded_names: set[str],
) -> ClientProcess | None:
    name = _read_name(proc)
    located = _read_path(proc)
    path, source = located if located is not None else (None, None)

    # Evidence that adds a match: any path, including a self-chosen argv[0].
    if path is not None:
        for given, spellings in roots:
            for spelling in spellings:
                below = _parts_under(path, spelling)
                if below is None:
                    continue
                flavor = flavors.get(_fold(below[0])) if len(below) > 1 else None
                return _running(
                    proc,
                    pid=pid,
                    name=name,
                    matched_by="path",
                    exe=path,
                    exe_source=source,
                    install_root=given,
                    flavor_folder=flavor,
                )
    name_matches = name is not None and _fold(name) in folded_names
    if name_matches or (path is not None and _fold(path.name) in folded_names):
        return _running(proc, pid=pid, name=name, matched_by="name", exe=path, exe_source=source)

    # Evidence that clears a process: only what the operating system reports.
    # A path from `exe()` settles it. An argv[0] does not, so a process known
    # only by argv[0] is judged exactly as if no path had been read at all.
    if source == "exe":
        return None
    if name is None or _could_be_truncated(name, names):
        return ClientProcess(pid=pid, state=ClientState.UNKNOWN, name=name)
    return None


def _running(
    proc: ProcessLike,
    *,
    pid: int,
    name: str | None,
    matched_by: Literal["path", "name"],
    exe: Path | None = None,
    exe_source: Literal["exe", "cmdline"] | None = None,
    install_root: Path | None = None,
    flavor_folder: str | None = None,
) -> ClientProcess | None:
    status = _read_status(proc)
    if status in _GONE_STATUSES:
        return None
    return ClientProcess(
        pid=pid,
        state=ClientState.RUNNING,
        matched_by=matched_by,
        name=name,
        exe=exe,
        exe_source=exe_source,
        install_root=install_root,
        flavor_folder=flavor_folder,
        status=status,
    )


def client_state(
    install_roots: Iterable[Path] = (),
    *,
    flavor_folders: Iterable[str] = (),
    extra_names: Iterable[str] = (),
    process_iter: ProcessIter | None = None,
) -> ClientState:
    """One answer for `guard`: `RUNNING` beats `UNKNOWN` beats `NOT_RUNNING`."""
    clients = running_clients(
        install_roots,
        flavor_folders=flavor_folders,
        extra_names=extra_names,
        process_iter=process_iter,
    )
    if any(c.state is ClientState.RUNNING for c in clients):
        return ClientState.RUNNING
    if clients:
        return ClientState.UNKNOWN
    return ClientState.NOT_RUNNING
