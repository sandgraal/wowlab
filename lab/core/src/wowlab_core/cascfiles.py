"""cascfiles: where `wowlab casc cat` puts a decoded game file (docs/LAB_PLAN.md
§17.1 "What it writes", M15-06).

Decoded files go under the user data directory, never into an install (L1):
`casc/<product>-<version>/`, one folder per product and build, both parts
discovered strings checked here as safe folder names (`build_dir`), so two
products that share a version string never share a folder and a build's
folder is never another's. By default a file is
`files/<fdid>-<ckey8>.bin` there (`default_path`): `ckey8` is the first
eight hex digits of its content key, so a patched file lands beside the old
one. Nothing in that folder is ever overwritten (L5): a file already there
with the same bytes (its size and MD5 are the content key's) is "already
extracted" and written again by nobody; one there with other bytes is
refused, never replaced (`write_new`).

Two first-level names in the build folder are reserved, whatever follows
them and in any letter case, with trailing dots and spaces ignored as
Windows ignores them: `files/`, the default place, which only `default_path`
names, and `characters/`, which belongs to `char model` and is re-made on
request (§17.6). `--out` (`check_out`) may name any other path inside the
build folder, relative to it or absolute; refused: a path outside it (`..`,
another build's folder, the rest of the user data directory, anywhere
else), a reserved name, a part that ends in a dot or a space or holds a
`:` (Windows would read it as another name, or as a stream), a path in an
install (`lookstore.refuse_install`: it or a folder above it holds
`.build.info` or `.flavor.info`), a path through a link (a symlink or a
junction) or through a file anywhere below the user data directory's
`casc/` folder, and a folder or anything that is not a regular file.

The checks run before the file is decoded and again after its folder is
made. The bytes go to a new temp file beside the target, published under
the target's name by a hard link, which never replaces; on a file system
without hard links, by a rename, which on Windows refuses an existing name
and elsewhere follows a last check that the name is free. The final name is
never written to directly, so a run killed midway leaves at most a temp
file (`.<name>.<32 hex digits>.tmp`), and each write first removes such
files older than a day from the folder it writes into.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
import time
import uuid
from collections.abc import Buffer
from pathlib import Path

from wowlab_core.errors import WowlabError
from wowlab_core.lookstore import LookLocationError, refuse_install
from wowlab_core.snapshot import make_private_dirs, user_data_dir

__all__ = [
    "CASC_DIR",
    "CHARACTERS_DIR",
    "FILES_DIR",
    "CascOutError",
    "already_there",
    "build_dir",
    "casc_dir",
    "check_out",
    "default_name",
    "default_path",
    "same_file",
    "write_new",
]

CASC_DIR = "casc"
FILES_DIR = "files"
CHARACTERS_DIR = "characters"
_WHAT = "extracted game files"
# A product code (`wow`, `wow_classic_beta`) or a version string
# (`1.60.1.70450`): letters, digits, `_` and `.`, starting with a letter or a
# digit and ending with a letter, a digit or `_` (Windows drops a trailing
# dot). No `-` (it joins the two), no separator, never `.` or `..`.
_SAFE_PART = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9_.]{0,62}[A-Za-z0-9_])?")
_DEFAULT_NAME = re.compile(r"[0-9]+-[0-9a-f]{8}\.bin")
# A temp file `write_new` makes beside its target, and how old one must be
# before a later write removes it (a run killed midway leaves it behind).
_TEMP_NAME = re.compile(r"\..+\.[0-9a-f]{32}\.tmp")
_TEMP_AGE_S = 24 * 60 * 60
_CONTENT_KEY = re.compile(r"[0-9a-f]{32}")
_FDID_LIMIT = 1 << 31
_HASH_CHUNK = 1 << 20


class CascOutError(WowlabError):
    """A decoded file cannot go where it was asked to go; nothing was written."""


def _safe_part(text: str, what: str) -> str:
    if not _SAFE_PART.fullmatch(text) or ".." in text:
        raise CascOutError(
            f"the {what} {text!r} is not a safe folder name (letters, digits, '_' and '.' "
            "only, not ending in '.'), so wowlab will not make a folder for it"
        )
    return text


def casc_dir() -> Path:
    """`<user data directory>/casc`, read at call time."""
    return user_data_dir() / CASC_DIR


def build_dir(product: str, version: str) -> Path:
    """`casc/<product>-<version>/` under the user data directory: the one
    folder a build's decoded files go into. Each part is checked as a safe
    folder name; a part that is not one is refused."""
    name = f"{_safe_part(product, 'product')}-{_safe_part(version, 'version')}"
    return casc_dir() / name


def default_name(fdid: int, content_key: str) -> str:
    """`<fdid>-<ckey8>.bin`: the file data id and the content key's first
    eight hex digits."""
    if not 0 <= fdid < _FDID_LIMIT:
        raise CascOutError(f"{fdid} is not a file data id")
    if not _CONTENT_KEY.fullmatch(content_key):
        raise CascOutError(f"{content_key!r} is not a content key")
    return f"{fdid}-{content_key[:8]}.bin"


def default_path(product: str, version: str, fdid: int, content_key: str) -> Path:
    """Where `casc cat` writes a file by default."""
    return build_dir(product, version) / FILES_DIR / default_name(fdid, content_key)


def _within(path: Path, folder: Path) -> bool:
    return path == folder or folder in path.parents


def _link_reason(path: Path) -> str | None:
    """Why `path` cannot be passed through or written: it is a symlink, or
    on Windows a junction (never followed), a part above it is a file, or
    it cannot be looked at (the worst case). None when it is none of these,
    or does not exist."""
    try:
        st = path.lstat()
    except FileNotFoundError:
        return None
    except NotADirectoryError:
        return "has a part that is a file, not a folder"
    except (OSError, ValueError) as exc:
        return f"cannot be looked at ({exc})"
    if stat.S_ISLNK(st.st_mode):
        return f"is reached through a link ({path}); a link is never followed"
    try:
        junction = path.is_junction()
    except OSError as exc:
        return f"cannot be looked at ({exc})"
    return f"is reached through a link ({path}); a link is never followed" if junction else None


def _refuse(target: Path, why: str) -> CascOutError:
    return CascOutError(f"refused: {target} {why}; nothing was written")


def _reserved(part: str) -> str:
    """A path part as Windows compares it: trailing dots and spaces dropped,
    letter case folded."""
    return part.rstrip(" .").casefold()


def check_out(out: Path | str, folder: Path, *, default: bool = False) -> Path:
    """The absolute path `out` names inside the build folder `folder`, or
    `CascOutError` for why a file will not go there; reads only. A relative
    `out` is taken inside `folder`. Refused: a path outside `folder` as
    written or with every link resolved; `folder` itself; a part that ends
    in a dot or a space or holds a `:`; a path under `characters/`, and one
    under `files/` unless `default` (then only `files/<fdid>-<ckey8>.bin`);
    a path in an install; a path through a link or a file anywhere from the
    user data directory's `casc/` folder down, or that is itself a link; a
    folder; anything else that is not a regular file."""
    given = Path(out)
    target = Path(os.path.normpath(folder / given if not given.is_absolute() else given))
    folder = Path(os.path.normpath(folder.absolute()))
    target = target if target.is_absolute() else Path(os.path.normpath(target.absolute()))
    if not _within(target, folder) or target == folder:
        raise _refuse(
            target, f"is not a file inside {folder}, the one folder this build's files go to"
        )
    parts = target.relative_to(folder).parts
    for part in parts:
        if part != part.rstrip(" .") or ":" in part:
            raise _refuse(
                target,
                f"has a part ({part!r}) that ends in a dot or a space or holds a ':', "
                "which Windows reads as another name",
            )
    first = _reserved(parts[0])
    if first == CHARACTERS_DIR:
        raise _refuse(target, f"is in {folder / CHARACTERS_DIR}, which is not for extracted files")
    if first == FILES_DIR and not (
        default and len(parts) == 2 and _DEFAULT_NAME.fullmatch(parts[1])
    ):
        raise _refuse(
            target,
            f"is in {folder / FILES_DIR}, where casc cat puts each file under its own name; "
            "--out names a path outside it",
        )
    try:
        refuse_install(target, _WHAT)
    except LookLocationError as exc:
        raise CascOutError(f"refused: {exc}; nothing was written") from exc
    top = casc_dir().absolute()
    for step in (target, *target.parents):
        if not _within(step, top):
            break
        why = _link_reason(step)
        if why is not None:
            raise _refuse(target, why)
    try:
        final, home = target.resolve(), folder.resolve()
    except (OSError, RuntimeError) as exc:
        raise _refuse(target, f"cannot be resolved ({exc})") from exc
    if not _within(final, home) or final == home:
        raise _refuse(target, f"resolves to {final}, outside {home}")
    if os.path.lexists(target):
        if target.is_dir():
            raise _refuse(target, "is a folder; --out names the file to write")
        if not target.is_file():
            raise _refuse(target, "exists and is not a regular file")
    return target


def same_file(path: Path, size: int, content_key: str) -> bool:
    """Whether the regular file at `path` (not a link) holds `size` bytes
    whose MD5 is `content_key`. A file of another size is not read."""
    st = path.lstat()
    if not stat.S_ISREG(st.st_mode) or st.st_size != size:
        return False
    digest = hashlib.md5(usedforsecurity=False)
    with path.open("rb") as handle:
        now = os.fstat(handle.fileno())
        if (now.st_dev, now.st_ino) != (st.st_dev, st.st_ino):
            return False  # swapped since the lstat: not the file looked at
        while chunk := handle.read(_HASH_CHUNK):
            digest.update(chunk)
    return digest.hexdigest() == content_key


def already_there(target: Path, size: int, content_key: str) -> bool:
    """False when nothing is at `target`; True when the same bytes are;
    else `CascOutError` (another file there is never replaced)."""
    if not os.path.lexists(target):
        return False
    if _link_reason(target) is not None or not target.is_file():
        raise _refuse(target, "exists and is not a regular file")
    if same_file(target, size, content_key):
        return True
    raise _refuse(
        target,
        "exists and holds other bytes than this file's; wowlab never overwrites a file it "
        "extracted, so remove it yourself or choose another --out",
    )


def write_new(
    data: Buffer, target: Path, folder: Path, content_key: str, *, default: bool = False
) -> bool:
    """Write `data`, whose MD5 is `content_key`, to `target` inside the
    build folder `folder` (as `check_out` returned it; `default` for
    `default_path`'s). Returns True when written, False when the same bytes
    were already there (nothing is written). Another file there is refused,
    never replaced. The folder is made 0700 under the user data directory,
    `target` is checked again once it exists, temp files older than a day
    are removed from it, and the bytes are written once, to a new temp file
    beside the target, then published under its name without replacing
    anything: by a hard link, or where there are none by a rename."""
    view = memoryview(data).cast("B")
    size = view.nbytes
    if hashlib.md5(view, usedforsecurity=False).hexdigest() != content_key:
        raise CascOutError(f"the bytes for {target.name} do not match their content key")
    target = check_out(target, folder, default=default)
    if already_there(target, size, content_key):
        return False
    parent = target.parent
    make_private_dirs(parent)  # 0700 under the user data directory (M16-78)
    target = check_out(target, folder, default=default)  # again, now the folders exist
    _sweep(parent)
    tmp = parent / f".{target.name}.{uuid.uuid4().hex}.tmp"
    try:
        with tmp.open("xb") as handle:
            handle.write(view)
        try:
            target.hardlink_to(tmp)
        except FileExistsError:
            # Another run put it there meanwhile: the same bytes, or refused.
            return _raced(target, size, content_key)
        except OSError:
            # No hard links here (FAT, exFAT). Windows' rename refuses an
            # existing name; elsewhere it would replace one, so the name is
            # checked free first (the window between is the last-check one).
            if os.path.lexists(target):
                return _raced(target, size, content_key)
            try:
                tmp.rename(target)
            except FileExistsError:
                return _raced(target, size, content_key)
    finally:
        tmp.unlink(missing_ok=True)
    return True


def _raced(target: Path, size: int, content_key: str) -> bool:
    """After a publish found `target` taken: False (nothing written) when it
    holds the same bytes; else refused, never replaced."""
    if already_there(target, size, content_key):
        return False
    raise _refuse(target, "appeared and was removed again while it was written")


def _sweep(folder: Path, now: float | None = None) -> None:
    """Remove from `folder` (not below it) the temp files `write_new` makes
    (`.<name>.<32 hex digits>.tmp`) that are regular files, not links, and
    were last changed more than a day ago: what a run killed midway leaves.
    A file that cannot be looked at or removed is left."""
    cutoff = (time.time() if now is None else now) - _TEMP_AGE_S
    try:
        names = [e.name for e in os.scandir(folder)]
    except OSError:
        return
    for name in names:
        if not _TEMP_NAME.fullmatch(name):
            continue
        path = folder / name
        try:
            st = path.lstat()
            if stat.S_ISREG(st.st_mode) and st.st_mtime < cutoff:
                path.unlink()
        except OSError:
            continue
