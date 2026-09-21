"""process: is a World of Warcraft client running? (docs/LAB_PLAN.md §6.7, M10-09)

Scope (L7, ADR-0023): this module lists processes through `psutil` and reads
five things from each one: `pid`, `name()`, `exe()`, `cmdline()` and
`status()`. Nothing else. No process memory, no handles opened on the client
by this code, no signals, no open-file or connection listing, no `ctypes`.
A test holds the module to exactly that surface.

Fail closed: a process about which nothing could be learned is reported as
`unknown`, and `guard` treats unknown as running (ADR-0021).

Nothing here knows a flavor (L6). Install roots and flavor folder names come
from the caller, which gets them from discovery (`install`, M10-05).
"""

from __future__ import annotations

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


def _read_name(proc: ProcessLike) -> str | None:
    try:
        return proc.name() or None
    except psutil.NoSuchProcess as exc:
        raise _GoneError from exc
    except psutil.AccessDenied:
        return None


def _read_path(proc: ProcessLike) -> tuple[Path, Literal["exe", "cmdline"]] | None:
    try:
        exe = proc.exe()
    except psutil.NoSuchProcess as exc:
        raise _GoneError from exc
    except psutil.AccessDenied:
        exe = ""
    if exe:
        return Path(exe), "exe"
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
        if ours.casefold() != theirs.casefold():
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
    folded = name.casefold()
    return any(len(k) > len(name) and k.casefold().startswith(folded) for k in names)


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
    (`matched_by="name"`). It is `UNKNOWN` when access was denied to the point
    that neither a name nor a path could be read, or when only a possibly
    truncated name that prefixes a client name could be. Anything else is not
    reported. Exited and zombie processes are skipped.

    `process_iter` replaces `psutil.process_iter` in tests and in `guard`'s
    graders; it returns objects with the `ProcessLike` surface.
    """
    roots = [(root, _root_spellings(root)) for root in install_roots]
    flavors = {folder.casefold(): folder for folder in flavor_folders}
    names = (*KNOWN_CLIENT_NAMES, *extra_names)
    folded_names = {n.casefold() for n in names}

    found: list[ClientProcess] = []
    for proc in (process_iter or _system_processes)():
        try:
            found_one = _inspect(proc, roots, flavors, names, folded_names)
        except _GoneError:
            continue
        if found_one is not None:
            found.append(found_one)
    return sorted(found, key=lambda c: c.pid)


def _inspect(
    proc: ProcessLike,
    roots: Sequence[tuple[Path, tuple[Path, ...]]],
    flavors: dict[str, str],
    names: Sequence[str],
    folded_names: set[str],
) -> ClientProcess | None:
    pid = proc.pid
    name = _read_name(proc)
    located = _read_path(proc)

    if located is None:
        if name is not None and name.casefold() in folded_names:
            return _running(proc, pid=pid, name=name, matched_by="name")
        if name is None or _could_be_truncated(name, names):
            return ClientProcess(pid=pid, state=ClientState.UNKNOWN, name=name)
        return None

    path, source = located
    for given, spellings in roots:
        for spelling in spellings:
            below = _parts_under(path, spelling)
            if below is None:
                continue
            flavor = flavors.get(below[0].casefold()) if len(below) > 1 else None
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
    if path.name.casefold() in folded_names or (
        name is not None and name.casefold() in folded_names
    ):
        return _running(proc, pid=pid, name=name, matched_by="name", exe=path, exe_source=source)
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
