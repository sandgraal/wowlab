"""Long paths on Windows: the extended-length spelling at the system call (M13-15).

With `LongPathsEnabled` off, the default on many player PCs, Windows refuses
a plain path of `MAX_PATH` (260 UTF-16 units, the terminator included) or
more, and creates no folder whose plain path reaches 248. It reports the
refusal as "file not found" or "path not found" (WinError 2 or 3), which
Python raises as `FileNotFoundError`: to a walk, a file past the limit looks
like one removed while it walked. Only an extended-length path reaches such a
file whatever the setting says: `\\\\?\\C:\\...` for a drive path,
`\\\\?\\UNC\\server\\share\\...` for a network one (not `\\\\?\\` glued to
`\\\\server`). Windows does no normalisation on such a path, so it is only
ever built from the absolute, normalised path (`ntpath.abspath`, which is
what Windows itself makes of a plain path before opening it).

The rule in the Lab: paths stay plain everywhere they are kept, compared,
shown, journaled or written into a manifest, and become extended-length only
as the argument of one system call (`extended`). A path shorter than
`PLAIN_LIMIT` keeps its spelling, so nothing changes for any path Windows
already opens plainly. An `OSError` raised by a call made here names the
plain path again (`plain_errors`), so a `\\\\?\\` prefix never reaches a
message, a journal note or `doctor`'s output. A caller that hands a root or
a flavor already in `\\\\?\\` form gets it back plain (`plain`) when it has a
plain spelling that names the same path.

Off Windows every function here is the call it wraps, with the path as given.

This module only reads: it lists folders, `lstat`s, `stat`s, resolves and
opens for reading. A caller that writes (`guard` into an install, L2) spells
the path with `extended` or `xpath` and makes the call itself.
"""

from __future__ import annotations

import contextlib
import ntpath
import os
import re
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO

__all__ = [
    "PLAIN_LIMIT",
    "entries",
    "exists",
    "extended",
    "is_dir",
    "is_file",
    "is_junction",
    "is_symlink",
    "iterdir",
    "lexists",
    "lstat",
    "open_read",
    "plain",
    "plain_errors",
    "read_bytes",
    "readlink",
    "realpath",
    "resolve",
    "samefile",
    "stat",
    "utf16_units",
    "xpath",
]

ON_WINDOWS = sys.platform == "win32"

PLAIN_LIMIT = 240
"""A path whose absolute form is shorter, in UTF-16 units (`utf16_units`, the
unit Windows counts in), keeps its spelling. Windows' tightest
plain limit is `CreateDirectoryW`'s 248 (`MAX_PATH` less room for an 8.3
name); `scandir` adds `\\\\*` to the folder it lists. The margin covers both."""

_EXTENDED = "\\\\?\\"
_EXTENDED_UNC = "\\\\?\\UNC\\"
_DRIVE_ABSOLUTE = re.compile(r"[A-Za-z]:\\")
_EXTENDED_DRIVE = re.compile(r"\\\\\?\\[A-Za-z]:\\")


def _is_device(text: str) -> bool:
    """Already a device or extended-length path (`\\\\?\\`, `\\\\.\\`, `\\??\\`,
    in either slash): Windows takes it as it is, and so does this module."""
    head = text[:4].replace("/", "\\")
    return head in (_EXTENDED, "\\\\.\\", "\\??\\")


def utf16_units(text: str) -> int:
    """`len(text)` as Windows counts it, in UTF-16 code units: a character
    outside the Basic Multilingual Plane (an emoji, a CJK Extension B
    ideograph) counts two. `MAX_PATH` and `PLAIN_LIMIT` are in these units."""
    return len(text.encode("utf-16-le", "surrogatepass")) // 2


def _literal(text: str) -> bool:
    """`text` is already absolute and spelled as an extended-length path takes
    it: a drive (`C:\\`) or network (`\\\\server\\share\\`) path, backslashes
    only, no `.` or `..` component. Prefixing it as it is keeps a name with a
    trailing dot or space that normalising would trim."""
    if "/" in text:
        return False
    if _DRIVE_ABSOLUTE.match(text):
        parts = text[3:].split("\\")
    elif text.startswith("\\\\") and not _is_device(text):
        parts = text[2:].split("\\")
        if len(parts) < 2 or not parts[0] or not parts[1]:
            return False
    else:
        return False
    return not any(part in (".", "..") for part in parts)


def extended_text(text: str, *, limit: int = PLAIN_LIMIT, literal: bool = False) -> str:
    """The Windows rule behind `extended`, on any platform (it is pure text):
    `text` unchanged when it is already a device path, or when its absolute,
    normalised form is shorter than `limit` UTF-16 units (`utf16_units`);
    otherwise that form with `\\\\?\\` (drive) or `\\\\?\\UNC\\` (network) in
    front. A relative `text` is made absolute against the working folder, as
    Windows would. With `literal`, a `text` already absolute and clean
    (`_literal`) is prefixed as it is, not normalised first, so the name of
    an entry a listing returned (`foo.`, say) is kept exactly."""
    if _is_device(text):
        return text
    full = ntpath.abspath(text)
    if utf16_units(full) < limit:
        return text
    if literal and _literal(text):
        return _EXTENDED_UNC + text[2:] if text.startswith("\\\\") else _EXTENDED + text
    if full.startswith("\\\\"):
        return full if _is_device(full) else _EXTENDED_UNC + full[2:]
    if _DRIVE_ABSOLUTE.match(full):
        return _EXTENDED + full
    return text  # not something an extended-length path can name


def plain_text(text: str) -> str:
    """The Windows rule behind `plain`, on any platform: `\\\\?\\C:\\...` as
    `C:\\...` and `\\\\?\\UNC\\server\\share\\...` as `\\\\server\\share\\...`,
    but only when that plain spelling names the same path (its extended form
    is `text` again). Anything else is returned unchanged: a name with a
    trailing dot or space, a `.` or `..` component, or a volume GUID path has
    no plain spelling."""
    if text.startswith(_EXTENDED_UNC):
        candidate = "\\\\" + text[len(_EXTENDED_UNC) :]
    elif _EXTENDED_DRIVE.match(text):
        candidate = text[len(_EXTENDED) :]
    else:
        return text
    if _drive_folded(extended_text(candidate, limit=0)) != _drive_folded(text):
        return text
    return candidate


def _drive_folded(text: str) -> str:
    """`text` with the drive letter of a `\\\\?\\C:\\` path in lower case, the
    one part of it whose case Windows may change when it normalises."""
    if _EXTENDED_DRIVE.match(text):
        return text[:4] + text[4].lower() + text[5:]
    return text


def extended(path: str | os.PathLike[str], *, always: bool = False) -> str | os.PathLike[str]:
    """`path` as the argument of one system call: on Windows its
    extended-length spelling when its absolute form is `PLAIN_LIMIT` UTF-16
    units or more. With `always`, whatever its length, and an absolute path
    is prefixed as it is (`extended_text(..., literal=True)`), so a listed
    name ending in a dot or space is the one looked at. Otherwise, and on
    every other platform, `path` itself, the same object."""
    if not ON_WINDOWS:
        return path
    text = os.fspath(path)
    spelled = extended_text(text, limit=0, literal=True) if always else extended_text(text)
    return path if spelled == text else spelled


def plain(path: str | os.PathLike[str]) -> Path:
    """`path` without an extended-length prefix, where it has a plain
    spelling (`plain_text`); on other platforms `Path(path)`."""
    text = os.fspath(path)
    return Path(plain_text(text) if ON_WINDOWS else text)


@contextlib.contextmanager
def plain_errors() -> Iterator[None]:
    """Re-raise an `OSError` with its file names plain again, so a message
    built from it names the path the caller knows."""
    try:
        yield
    except OSError as exc:
        if ON_WINDOWS:
            for attr in ("filename", "filename2"):
                value = getattr(exc, attr, None)
                if isinstance(value, str) and value.startswith(_EXTENDED):
                    setattr(exc, attr, plain_text(value))
        raise


def xpath(path: str | os.PathLike[str]) -> Path:
    """`extended(path)` as a `Path`, to call one `Path` method on (the same
    object when the spelling does not change). For a caller that writes."""
    spelled = extended(path)
    return spelled if isinstance(spelled, Path) else Path(spelled)


_x = xpath


# Each wrapper makes the call the caller made before (a `Path` method stays a
# `Path` method, an `os` function an `os` function), on the extended spelling.


def lstat(path: str | os.PathLike[str]) -> os.stat_result:
    with plain_errors():
        return _x(path).lstat()


def stat(path: str | os.PathLike[str]) -> os.stat_result:
    with plain_errors():
        return _x(path).stat()


def exists(path: str | os.PathLike[str]) -> bool:
    with plain_errors():
        return _x(path).exists()


def lexists(path: str | os.PathLike[str]) -> bool:
    """`os.path.lexists(path)`: something is there, a broken link included."""
    return os.path.lexists(extended(path))


def is_dir(path: str | os.PathLike[str]) -> bool:
    with plain_errors():
        return _x(path).is_dir()


def is_file(path: str | os.PathLike[str]) -> bool:
    with plain_errors():
        return _x(path).is_file()


def is_symlink(path: str | os.PathLike[str]) -> bool:
    with plain_errors():
        return _x(path).is_symlink()


def is_junction(path: str | os.PathLike[str]) -> bool:
    with plain_errors():
        return _x(path).is_junction()


def readlink(path: str | os.PathLike[str]) -> Path:
    """The link's target as stored; the spelling of the link's own path does
    not change it."""
    with plain_errors():
        return _x(path).readlink()


def samefile(a: str | os.PathLike[str], b: str | os.PathLike[str]) -> bool:
    """`Path(a).samefile(b)`: both followed, compared by device and inode."""
    with plain_errors():
        return _x(a).samefile(_x(b))


def resolve(path: str | os.PathLike[str], *, strict: bool = False) -> Path:
    """`Path(path).resolve(strict=strict)`, plain (`plain`) where the result
    has a plain spelling."""
    with plain_errors():
        if not ON_WINDOWS:
            return Path(path).resolve(strict=strict)
        return plain(_x(path).resolve(strict=strict))


def realpath(path: str | os.PathLike[str]) -> str:
    """`os.path.realpath(path)`, every link on the way resolved, past the
    plain limit too: on Windows the extended spelling is resolved (a plain
    path Windows cannot open resolves only as far up as it can, leaving the
    rest unresolved) and the result is made plain (`plain_text`) where it
    has a plain spelling. For a containment check, compare two results of
    this function, never one with a path spelled otherwise."""
    with plain_errors():
        if not ON_WINDOWS:
            return os.path.realpath(path)
        return plain_text(os.path.realpath(extended(path)))


def iterdir(path: str | os.PathLike[str]) -> list[Path]:
    """`Path(path).iterdir()`, listed at once: each child is `path / name`."""
    base = Path(path)
    with plain_errors():
        names = [child.name for child in _x(base).iterdir()]
    return [base / name for name in names]


def entries(path: str | os.PathLike[str]) -> list[os.DirEntry[str]]:
    """`os.scandir(path)`, listed at once. Each entry's cached type and
    `lstat` are good to use; its `.path` may be extended-length, so build a
    child's path as `path / entry.name`."""
    with plain_errors(), os.scandir(extended(path)) as it:
        return list(it)


def open_read(path: str | os.PathLike[str]) -> BinaryIO:
    """`Path(path).open("rb")`."""
    with plain_errors():
        return _x(path).open("rb")


def read_bytes(path: str | os.PathLike[str]) -> bytes:
    """`Path(path).read_bytes()`."""
    with plain_errors():
        return _x(path).read_bytes()
