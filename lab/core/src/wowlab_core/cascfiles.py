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
refused, never replaced (`write_new`). One subfolder, `characters/`, belongs
to `char model`, which re-makes its files on request (§17.6); `--out` never
names a path in it.

`--out` (`check_out`) may name another path inside the same build folder,
relative to it or absolute; a path outside it (`..`, another build's
folder, the rest of the user data directory, anywhere else) is refused, as
is a path in an install (`lookstore.refuse_install`: it or a folder above it
holds `.build.info` or `.flavor.info`), a path through a link (a symlink or
a junction) anywhere below the user data directory's `casc/` folder, and a
folder or anything that is not a regular file. The checks run before the
file is decoded, again after its folder is made, and the file is created
new beside its name and linked into place, so it appears whole or not at
all and never replaces what another writer put there meanwhile.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
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
# digit. No `-` (it joins the two), no separator, never `.` or `..`.
_SAFE_PART = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.]{0,63}")
_CONTENT_KEY = re.compile(r"[0-9a-f]{32}")
_FDID_LIMIT = 1 << 31
_HASH_CHUNK = 1 << 20


class CascOutError(WowlabError):
    """A decoded file cannot go where it was asked to go; nothing was written."""


def _safe_part(text: str, what: str) -> str:
    if not _SAFE_PART.fullmatch(text) or ".." in text:
        raise CascOutError(
            f"the {what} {text!r} is not a safe folder name (letters, digits, '_' and '.' "
            "only), so wowlab will not make a folder for it"
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


def _is_link(path: Path) -> bool:
    """`path` itself is a symlink, or on Windows a junction (not followed)."""
    try:
        st = path.lstat()
    except FileNotFoundError:
        return False
    except (OSError, ValueError):
        return True  # cannot be looked at: treated as the worst case
    if stat.S_ISLNK(st.st_mode):
        return True
    try:
        return path.is_junction()
    except OSError:
        return True


def _refuse(target: Path, why: str) -> CascOutError:
    return CascOutError(f"refused: {target} {why}; nothing was written")


def check_out(out: Path | str, folder: Path) -> Path:
    """The absolute path `out` names inside the build folder `folder`, or
    `CascOutError` for why a file will not go there; reads only. A relative
    `out` is taken inside `folder`. Refused: a path outside `folder` as
    written or with every link resolved; `folder` itself; a path in its
    `characters/` subfolder; a path in an install; a path through a link
    anywhere from the user data directory's `casc/` folder down, or that is
    itself one; a folder; anything else that is not a regular file."""
    given = Path(out)
    target = Path(os.path.normpath(folder / given if not given.is_absolute() else given))
    folder = Path(os.path.normpath(folder.absolute()))
    target = target if target.is_absolute() else Path(os.path.normpath(target.absolute()))
    if not _within(target, folder) or target == folder:
        raise _refuse(
            target, f"is not a file inside {folder}, the one folder this build's files go to"
        )
    # By name in any letter case: a file system may not tell `Characters/` apart.
    if target.relative_to(folder).parts[0].casefold() == CHARACTERS_DIR:
        raise _refuse(target, f"is in {folder / CHARACTERS_DIR}, which is not for extracted files")
    try:
        refuse_install(target, _WHAT)
    except LookLocationError as exc:
        raise CascOutError(f"refused: {exc}; nothing was written") from exc
    top = casc_dir().absolute()
    for part in (target, *target.parents):
        if not _within(part, top):
            break
        if _is_link(part):
            raise _refuse(target, f"is reached through a link ({part}); a link is never followed")
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
    if _is_link(target) or not target.is_file():
        raise _refuse(target, "exists and is not a regular file")
    if same_file(target, size, content_key):
        return True
    raise _refuse(
        target,
        "exists and holds other bytes than this file's; wowlab never overwrites a file it "
        "extracted, so remove it yourself or choose another --out",
    )


def write_new(data: Buffer, target: Path, folder: Path, content_key: str) -> bool:
    """Write `data`, whose MD5 is `content_key`, to `target` inside the
    build folder `folder` (as `check_out` returned it, or `default_path`).
    Returns True when written, False when the same bytes were already there
    (nothing is written). Another file there is refused, never replaced.
    The folder is made 0700 under the user data directory, `target` is
    checked again once it exists, and the bytes are written to a new file
    beside it and linked into place, so the file appears whole and a file
    that appeared meanwhile is never replaced."""
    view = memoryview(data).cast("B")
    size = view.nbytes
    if hashlib.md5(view, usedforsecurity=False).hexdigest() != content_key:
        raise CascOutError(f"the bytes for {target.name} do not match their content key")
    target = check_out(target, folder)
    if already_there(target, size, content_key):
        return False
    parent = target.parent
    make_private_dirs(parent)  # 0700 under the user data directory (M16-78)
    target = check_out(target, folder)  # again, now that the folders exist
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
            # A file system with no hard links (FAT, exFAT): create the name
            # itself, new, and remove it again if the write does not finish.
            return _write_exclusive(view, target, size, content_key)
    finally:
        tmp.unlink(missing_ok=True)
    return True


def _raced(target: Path, size: int, content_key: str) -> bool:
    """After a create found `target` taken: False (nothing written) when it
    holds the same bytes; else refused, never replaced."""
    if already_there(target, size, content_key):
        return False
    raise _refuse(target, "appeared and was removed again while it was written")


def _write_exclusive(view: memoryview, target: Path, size: int, content_key: str) -> bool:
    """`write_new`'s way where a hard link cannot be made: True when
    written, False when the same bytes appeared there first."""
    try:
        handle = target.open("xb")
    except FileExistsError:
        return _raced(target, size, content_key)
    done = False
    try:
        with handle:
            handle.write(view)
        done = True
    finally:
        if not done:
            target.unlink(missing_ok=True)
    return True
