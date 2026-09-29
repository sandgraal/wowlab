"""lookstore: saved looks as JSON under the user data directory (docs/LAB_PLAN.md §13.2, M11-06).

A saved look is one file, ``<user data>/wowlab/looks/<name>.json``, holding a
``SavedLook``: the ``looks.Look`` and the build it was checked against when it
was saved. Nothing here reads or writes an install (L1): the directory is
refused, before and after it is created, when it or any folder above it holds
``.build.info`` or ``.flavor.info``. Nothing here checks a look either; that
is ``looks.Customizations.check``, run by the caller against the tables of a
build it chose (L6).

Names are a letter or digit, then letters, digits, ``.``, ``_`` or ``-``, 64
characters at most (the rule ``profiles`` uses), so a name is always a plain
file name. Two names that differ only in case are the same look, because the
user data directory is case-insensitive on the usual macOS and Windows
volumes.

A save never replaces a look unless asked to: the file is written under a
temporary name beside the target and then linked into place, which fails if
the name is taken; with ``replace=True`` it is renamed over the old file. On a
filesystem without hard links the link falls back to a check followed by the
rename. Either way the file appears whole or not at all.

Reading is bounded: a file over ``MAX_LOOK_BYTES`` is refused unread, and a
file that does not validate is reported by name, never guessed at.
"""

from __future__ import annotations

import errno
import os
import re
import stat
import uuid
from pathlib import Path
from typing import Literal

import platformdirs
from pydantic import BaseModel, ConfigDict, ValidationError

from wowlab_core.install import BUILD_INFO, FLAVOR_INFO
from wowlab_core.looks import Look

__all__ = [
    "LOOKS_FORMAT",
    "MAX_LOOK_BYTES",
    "DamagedLook",
    "LookExistsError",
    "LookLocationError",
    "LookNotFoundError",
    "LookStore",
    "LookStoreError",
    "SavedLook",
    "check_name",
    "default_looks_dir",
    "refuse_install",
]

LOOKS_FORMAT: Literal[1] = 1
MAX_LOOK_BYTES = 1 << 20
_SUFFIX = ".json"
# ERROR_CANT_RESOLVE_FILENAME: Windows' word for a symlink loop (pathlib reads it too)
_CANT_RESOLVE_FILENAME = 1921
_NAME_RE = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
# errno values meaning "this filesystem cannot make a hard link"
_NO_HARD_LINKS = frozenset(
    code
    for code in (
        getattr(errno, "EPERM", None),
        getattr(errno, "EOPNOTSUPP", None),
        getattr(errno, "ENOTSUP", None),
        getattr(errno, "EXDEV", None),
        getattr(errno, "EMLINK", None),
    )
    if code is not None
)


class LookStoreError(Exception):
    """A saved look cannot be read or written."""


class LookExistsError(LookStoreError):
    pass


class LookNotFoundError(LookStoreError):
    pass


class LookLocationError(LookStoreError):
    """The looks directory is inside an install, or is not a directory."""


class SavedLook(BaseModel):
    """One saved look. ``saved_build`` is the full version string whose tables
    checked it when it was saved; a later check may use another build.

    ``origin`` (M11-23, additive, format stays 1): ``typed`` for a look given
    on the command line (``looks save``), ``imported`` for one read from a
    character's lab-addon record (``looks import-char``). A file without it
    predates import and was typed. It decides how an id the build lacks is
    worded: a typed id is something to check, an imported one may be a
    hotfix."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    format: Literal[1] = LOOKS_FORMAT
    saved_build: str
    origin: Literal["typed", "imported"] = "typed"
    look: Look


class DamagedLook(BaseModel):
    """A file in the looks directory that is not a saved look."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    file: str
    error: str


def default_looks_dir() -> Path:
    """``<user data dir>/wowlab/looks``."""
    return platformdirs.user_data_path("wowlab") / "looks"


def check_name(name: str) -> str:
    """A look name: a letter or digit, then letters, digits, `.`, `_`, `-`; 64 at most."""
    if not _NAME_RE.match(name):
        raise LookStoreError(
            f"{name!r} is not a look name (a letter or digit, then letters, digits, "
            "'.', '_' or '-'; 64 characters at most)"
        )
    return name


def refuse_install(path: Path, what: str = "saved looks") -> None:
    """Raise ``LookLocationError`` if ``path``, with every symlink resolved, is
    an install or inside one: it or a folder above it holds ``.build.info`` or
    ``.flavor.info``. ``what`` names what never lives there, for the message.
    Reads only; ``looks page`` runs it on its output file too (M11-07). A path
    that cannot be resolved (a symlink loop) is refused too."""
    try:
        resolved = path.resolve()
    except (RuntimeError, OSError) as exc:
        raise LookLocationError(f"{path} cannot be resolved ({exc})") from None
    try:
        # Non-strict resolve() does not see every loop: on Windows a path
        # through two links that name each other resolves without an error.
        # A strict walk does; only a loop is refused here, a missing tail is
        # the usual case for a file about to be written.
        os.path.realpath(path, strict=True)
    except OSError as exc:
        if exc.errno == errno.ELOOP or getattr(exc, "winerror", 0) == _CANT_RESOLVE_FILENAME:
            raise LookLocationError(f"{path} cannot be resolved ({exc})") from None
    for candidate in (resolved, *resolved.parents):
        for marker in (BUILD_INFO, FLAVOR_INFO):
            if (candidate / marker).exists():
                raise LookLocationError(
                    f"{path} is inside a game install ({candidate} holds {marker}); "
                    f"{what} never live in an install (L1)"
                )


class LookStore:
    """The saved looks in one directory (default: ``default_looks_dir()``)."""

    def __init__(self, root: Path | None = None) -> None:
        self._root = Path(root) if root is not None else default_looks_dir()

    @property
    def root(self) -> Path:
        return self._root

    def path_for(self, name: str) -> Path:
        return self._root / f"{check_name(name)}{_SUFFIX}"

    def _files(self) -> list[Path]:
        """Every ``*.json`` directly in the directory, by name. No directory: none."""
        if not self._root.exists():
            return []
        refuse_install(self._root)
        if not self._root.is_dir():
            raise LookLocationError(f"{self._root} is not a directory")
        return sorted(p for p in self._root.iterdir() if p.name.endswith(_SUFFIX))

    def _existing(self, name: str) -> Path | None:
        """The file holding ``name``, compared without case, if any."""
        wanted = f"{name}{_SUFFIX}".casefold()
        for path in self._files():
            if path.name.casefold() == wanted:
                return path
        return None

    def read(self, path: Path) -> SavedLook:
        """The saved look in ``path``, a file of this store (see ``locate``)."""
        # Non-blocking, and checked on the open descriptor: a FIFO (or a file
        # swapped for one) is refused instead of blocking the read.
        try:
            fd = os.open(
                path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
            )
            try:
                st = os.fstat(fd)
                if not stat.S_ISREG(st.st_mode):
                    raise LookStoreError(f"{path} is not a regular file")
                if st.st_size > MAX_LOOK_BYTES:
                    raise LookStoreError(
                        f"{path} is {st.st_size} bytes, over the {MAX_LOOK_BYTES}-byte "
                        "limit for a look"
                    )
                chunks: list[bytes] = []
                total = 0
                while total <= MAX_LOOK_BYTES:
                    chunk = os.read(fd, MAX_LOOK_BYTES + 1 - total)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    total += len(chunk)
                data = b"".join(chunks)
            finally:
                os.close(fd)
        except OSError as exc:
            raise LookStoreError(f"{path}: {exc.strerror or exc}") from exc
        if len(data) > MAX_LOOK_BYTES:
            raise LookStoreError(f"{path} is over the {MAX_LOOK_BYTES}-byte limit for a look")
        try:
            saved = SavedLook.model_validate_json(data)
        except ValidationError as exc:
            first = exc.errors()[0]
            where = ".".join(str(p) for p in first["loc"]) or "(top level)"
            raise LookStoreError(
                f"{path} is not a saved look ({exc.error_count()} problem(s); first at "
                f"{where}: {first['msg']})"
            ) from None
        stem = path.name[: -len(_SUFFIX)]
        if saved.look.name.casefold() != stem.casefold():
            raise LookStoreError(f"{path} holds a look named {saved.look.name!r}, not {stem!r}")
        return saved

    def locate(self, name: str) -> Path:
        """The file holding look ``name``, compared without case."""
        check_name(name)
        path = self._existing(name)
        if path is None:
            raise LookNotFoundError(f"no saved look {name!r} in {self._root}")
        return path

    def load(self, name: str) -> SavedLook:
        return self.read(self.locate(name))

    def listing(self) -> tuple[list[SavedLook], list[DamagedLook]]:
        """Every saved look by name, and every ``*.json`` that is not one."""
        found, damaged = self.entries()
        return [saved for _, saved in found], damaged

    def entries(self) -> tuple[list[tuple[Path, SavedLook]], list[DamagedLook]]:
        """``listing`` with each saved look's file."""
        found: list[tuple[Path, SavedLook]] = []
        damaged: list[DamagedLook] = []
        for path in self._files():
            try:
                found.append((path, self.read(path)))
            except LookStoreError as exc:
                damaged.append(DamagedLook(file=path.name, error=str(exc)))
        return found, damaged

    def save(self, saved: SavedLook, *, replace: bool = False) -> Path:
        """Write ``saved`` as ``<name>.json``; refuse a taken name unless ``replace``."""
        name = check_name(saved.look.name)
        refuse_install(self._root)
        self._root.mkdir(parents=True, exist_ok=True)
        refuse_install(self._root)
        existing = self._existing(name)
        if existing is not None and not replace:
            raise LookExistsError(
                f"a look named {existing.name[: -len(_SUFFIX)]!r} is already saved "
                f"({existing}); pass --replace to overwrite it"
            )
        # Replacing keeps the name the file already has on a case-insensitive volume.
        final = existing if existing is not None else self._root / f"{name}{_SUFFIX}"
        body = saved.model_dump_json(indent=2).encode("utf-8") + b"\n"
        tmp = self._root / f".{name}.{uuid.uuid4().hex}.tmp"
        try:
            with tmp.open("xb") as handle:
                handle.write(body)
            if replace:
                tmp.replace(final)
            else:
                self._publish_new(tmp, final)
        finally:
            tmp.unlink(missing_ok=True)
        return final

    def _publish_new(self, tmp: Path, final: Path) -> None:
        try:
            final.hardlink_to(tmp)
        except FileExistsError:
            raise LookExistsError(
                f"a look is already saved as {final}; pass --replace to overwrite it"
            ) from None
        except OSError as exc:
            if exc.errno not in _NO_HARD_LINKS:
                raise
            if final.exists():
                raise LookExistsError(
                    f"a look is already saved as {final}; pass --replace to overwrite it"
                ) from None
            tmp.replace(final)
