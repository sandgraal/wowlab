"""linkstore: the outside addresses a player saved for their characters (M21-03).

Some sites' addresses cannot be built: ForeverTwinking's carry the site's
own character number and a server-made build code, and a Warcraft Logs
report has a code only the site knows. The player pastes one for a
character once (`wowlab char link set`, or the Character tab), and wowlab
keeps it here and shows it as a link. Each address is checked against
`link_sites.toml`'s `[pasted]` shapes (`links.check_pasted`) before it is
kept, and again whenever the file is read, so a hand-edited file can never
put another address on the page.

One small file, ``<user data>/wowlab/links.json``, never in an install (L1):
the folder is refused, before and after it is created, when it or any
folder above it holds ``.build.info`` or ``.flavor.info``. A character is
keyed by the flavor folder, the account folder and the character as the
`char` commands name it (``<realm or digits folder>/<character folder>``).
A character keeps at most one address per site and kind (a ForeverTwinking
profile and a ForeverTwinking build are two kinds); a new one replaces it.

The file is written whole, under a temporary name beside it, then renamed
over it: it is the old file or the new one, never half of either. Two
writes at the same moment: the later rename wins. Reading is bounded: a
file over ``MAX_STORE_BYTES`` is refused unread.
"""

from __future__ import annotations

import json
import os
import stat
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import platformdirs
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from wowlab_core import links
from wowlab_core.errors import WowlabError
from wowlab_core.lookstore import LookLocationError, refuse_install
from wowlab_core.snapshot import make_private_dirs

__all__ = [
    "MAX_CHARACTERS",
    "MAX_STORE_BYTES",
    "STORE_FORMAT",
    "CharacterKey",
    "CharacterLinks",
    "LinkStoreError",
    "Refused",
    "StoreFile",
    "Stored",
    "StoredLink",
    "checked",
    "default_path",
    "forget",
    "links_for",
    "read",
    "save",
    "write",
]

STORE_FORMAT: Literal[1] = 1
MAX_STORE_BYTES = 256 * 1024
MAX_CHARACTERS = 1000
_MAX_LINKS = 16
_TEXT = Field(min_length=1, max_length=512)


class LinkStoreError(WowlabError):
    """The saved links cannot be read or written."""


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class CharacterKey(_Model):
    """A character as the `char` commands name it."""

    flavor: str = _TEXT
    account: str = _TEXT
    character: str = _TEXT


class StoredLink(_Model):
    """One saved address: its site and kind, as `link_sites.toml` names
    them, the address, and when it was saved (UTC, ISO 8601)."""

    site: str = Field(min_length=1, max_length=32)
    kind: str = Field(min_length=1, max_length=16)
    url: str = _TEXT
    saved_at: str = Field(min_length=1, max_length=40)


class CharacterLinks(_Model):
    key: CharacterKey
    links: list[StoredLink] = Field(max_length=_MAX_LINKS)


class StoreFile(_Model):
    format: Literal[1] = STORE_FORMAT
    characters: list[CharacterLinks] = Field(default=[], max_length=MAX_CHARACTERS)


class Refused(_Model):
    """A saved entry wowlab does not show: which, and why."""

    key: CharacterKey
    site: str
    kind: str
    why: str


class Stored(_Model):
    """The file as read (`file`, every entry, which a change writes back
    with only its own character's change applied), the entries that check
    out (`shown`, the only ones ever shown as links) and those that do not
    (`refused`)."""

    path: str
    file: StoreFile
    shown: StoreFile
    refused: list[Refused]


def default_path() -> Path:
    """``<user data dir>/wowlab/links.json``."""
    return platformdirs.user_data_path("wowlab") / "links.json"


def _read_bytes(path: Path) -> bytes | None:
    """The file's bytes, or None when there is none."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0))
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise LinkStoreError(f"{path}: {exc.strerror or exc}") from exc
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise LinkStoreError(f"{path} is not a regular file")
        if st.st_size > MAX_STORE_BYTES:
            raise LinkStoreError(f"{path} is over the {MAX_STORE_BYTES}-byte limit")
        chunks: list[bytes] = []
        total = 0
        while total <= MAX_STORE_BYTES:
            chunk = os.read(fd, MAX_STORE_BYTES + 1 - total)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
    except OSError as exc:
        raise LinkStoreError(f"{path}: {exc.strerror or exc}") from exc
    finally:
        os.close(fd)
    data = b"".join(chunks)
    if len(data) > MAX_STORE_BYTES:
        raise LinkStoreError(f"{path} is over the {MAX_STORE_BYTES}-byte limit")
    return data


def read(path: Path | None = None) -> Stored:
    """The saved links. No file: none. Each address is checked again
    against `link_sites.toml`; one that does not check out, or whose site
    and kind are not the shape it matches, is left out and listed in
    `refused`. Raises `LinkStoreError` for a file that cannot be read or is
    not a links file."""
    path = default_path() if path is None else path
    try:
        refuse_install(path.parent, "saved links")
    except LookLocationError as exc:
        raise LinkStoreError(str(exc)) from None
    data = _read_bytes(path)
    if data is None:
        return Stored(path=str(path), file=StoreFile(), shown=StoreFile(), refused=[])
    try:
        stored = StoreFile.model_validate_json(data)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"]) or "(top level)"
        raise LinkStoreError(
            f"{path} is not a links file ({exc.error_count()} problem(s); first at "
            f"{where}: {first['msg']})"
        ) from None
    shown, refused = checked(stored)
    return Stored(path=str(path), file=stored, shown=shown, refused=refused)


def checked(file: StoreFile) -> tuple[StoreFile, list[Refused]]:
    """The entries of `file` that check out against `link_sites.toml`
    today, and the others with why. Nothing is dropped from `file`
    itself: an entry wowlab no longer shows stays in it."""
    kept: list[CharacterLinks] = []
    refused: list[Refused] = []
    for entry in file.characters:
        good: list[StoredLink] = []
        for link in entry.links:
            try:
                made = links.check_pasted(link.url, link.site)
            except (links.LinkError, links.LinkSitesError) as exc:
                refused.append(Refused(key=entry.key, site=link.site, kind=link.kind, why=str(exc)))
                continue
            why = None
            if made.kind != link.kind:
                why = f"the address is a {made.label} address, not a {link.kind} address"
            elif made.url != link.url:
                why = f"the address is not kept as wowlab keeps it (that would be {made.url})"
            if why is not None:
                refused.append(Refused(key=entry.key, site=link.site, kind=link.kind, why=why))
                continue
            good.append(link)
        if good:
            kept.append(CharacterLinks(key=entry.key, links=good))
    return StoreFile(characters=kept), refused


def links_for(file: StoreFile, key: CharacterKey) -> list[StoredLink]:
    """The links saved for one character."""
    for entry in file.characters:
        if entry.key == key:
            return list(entry.links)
    return []


def _with(file: StoreFile, key: CharacterKey, kept: list[StoredLink]) -> StoreFile:
    others = [e for e in file.characters if e.key != key]
    if kept:
        others.append(CharacterLinks(key=key, links=kept))
    return StoreFile(characters=others)


def save(
    file: StoreFile, key: CharacterKey, pasted: links.PastedLink, *, now: datetime | None = None
) -> tuple[StoreFile, StoredLink, StoredLink | None]:
    """The file with `pasted` saved for `key`, the link saved, and the one
    it replaces (same site and kind), if any. Raises `LinkStoreError` past
    the limits."""
    when = (now or datetime.now(UTC)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    new = StoredLink(site=pasted.site, kind=pasted.kind, url=pasted.url, saved_at=when)
    current = links_for(file, key)
    old = next((s for s in current if (s.site, s.kind) == (new.site, new.kind)), None)
    kept = [s for s in current if s is not old] + [new]
    if len(kept) > _MAX_LINKS:
        raise LinkStoreError(f"a character keeps at most {_MAX_LINKS} saved addresses")
    if old is None and not current and len(file.characters) >= MAX_CHARACTERS:
        raise LinkStoreError(
            f"wowlab keeps saved addresses for at most {MAX_CHARACTERS} characters"
        )
    return _with(file, key, kept), new, old


def forget(
    file: StoreFile, key: CharacterKey, site: str, kind: str | None = None
) -> tuple[StoreFile, list[StoredLink]]:
    """The file without `key`'s links of `site` (of `kind` only, when
    given), and the links dropped."""
    current = links_for(file, key)
    dropped = [s for s in current if s.site == site and (kind is None or s.kind == kind)]
    kept = [s for s in current if s not in dropped]
    return _with(file, key, kept), dropped


def _encoded(file: StoreFile) -> bytes:
    data = json.dumps(file.model_dump(mode="json"), indent=1, ensure_ascii=True) + "\n"
    return data.encode("ascii")


def write(file: StoreFile, path: Path | None = None) -> None:
    """Write the file whole: a temporary file beside it, renamed over it.
    The folder is refused before and after it is made when it is in an
    install. Raises `LinkStoreError`."""
    path = default_path() if path is None else path
    data = _encoded(file)
    if len(data) > MAX_STORE_BYTES:
        raise LinkStoreError(f"the saved addresses would pass the {MAX_STORE_BYTES}-byte limit")
    folder = path.parent
    try:
        refuse_install(folder, "saved links")
        make_private_dirs(folder)
        refuse_install(folder, "saved links")
    except LookLocationError as exc:
        raise LinkStoreError(str(exc)) from None
    except OSError as exc:
        raise LinkStoreError(f"{folder}: {exc.strerror or exc}") from exc
    temporary = folder / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        with temporary.open("xb") as out:
            out.write(data)
            out.flush()
            os.fsync(out.fileno())
        temporary.replace(path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise LinkStoreError(f"{path}: {exc.strerror or exc}") from exc
