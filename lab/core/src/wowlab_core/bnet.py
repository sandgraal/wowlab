"""bnet: public character profiles from Blizzard's own API (M22-11, ADR-0040 (2)).

The player creates an API client at develop.battle.net and gives wowlab its
id and secret (`wowlab blizzard login`). With them, and only when the player
asks (a command or a click, never a timer or a start), wowlab gets a
client-credentials token and asks the profile API for one character: the
profile (level, race, class, guild, item level), the equipment, the
character media (the portrait's address on Blizzard's render host) and the
guild. Each answer is saved under the user data directory, one file per
fetch, keyed by the game, the region and the name, so `show` reads the last
answers back with no request.

What is sent: the region, the game (its namespace, from the client's
version) and the character's first and second name, in the address; the
client id and secret only to Blizzard's sign-in (token) address. The region
may come from the install's `portal` setting and the name from a character
folder; nothing else read from an install is sent.

The secret (ADR-0040 (2); the owner's rule of 2026-11-24): it is kept in
`credentials_path()`, a file of its own made 0600 in a 0700 folder under
the user config directory, never in an install; it is never logged,
printed, put in a report, an error message or a saved answer.
`Credentials` hides it from `repr`, and neither it nor a `Request` (whose
headers hold the secret or the token) can be pickled, copied or
`asdict`-ed. The token lives in this process's memory only. The first
build kept the file in the data folder; it is moved once, under the same
refusals as every write.

The network (ADR-0040 (2)): stdlib `urllib` with certificate checks on, an
opener that has an https handler and no other (no plain http, no file, no
ftp, no data URL, no proxy from the environment), and a redirect only to
the same https host; any other is refused. Answers are read up to
`MAX_REPLY_BYTES`.

Where the game's characters live is data (`bnet_games.toml`, L6), chosen
by the client's version. For Forever the namespace and the address shape
come from community forum notes and are [verify]; the owner's `@live` test
is the check (`docs/BACKLOG.md` M22-11, note of 2026-11-23).

Writes: only `credentials_path()` (and the removal of the first build's
copy in the data folder) and the files under `profiles_dir()`,
each through a temporary file beside it made with `O_EXCL` and no link
followed, then moved into place; a folder inside a game install is refused
(L1).
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import importlib.resources
import json
import os
import re
import ssl
import stat
import tomllib
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from http.client import HTTPMessage
from pathlib import Path
from typing import Any, Literal, NamedTuple, override

import platformdirs
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic_core import PydanticSerializationError

from wowlab_core import __version__
from wowlab_core.errors import WowlabError
from wowlab_core.lookstore import LookLocationError, refuse_install
from wowlab_core.region import REGIONS, Region
from wowlab_core.snapshot import make_private_dirs

__all__ = [
    "KEEP_FETCHES",
    "MAX_REPLY_BYTES",
    "RENDER_HOST",
    "SETUP_WORDS",
    "BnetAsset",
    "BnetCharacter",
    "BnetError",
    "BnetGuild",
    "BnetItem",
    "BnetNameError",
    "BnetNotConfiguredError",
    "BnetProfile",
    "BnetRefusedError",
    "BnetUnavailableError",
    "Credentials",
    "Game",
    "Name",
    "PartState",
    "Reply",
    "Request",
    "blizzard_dir",
    "character_path",
    "check_region",
    "clear_credentials",
    "config_dir",
    "credentials_path",
    "fetch_profile",
    "game_for_version",
    "load_games",
    "load_profile",
    "parse_name",
    "profiles_dir",
    "read_credentials",
    "render_url",
    "save_credentials",
    "save_profile",
    "urllib_send",
]

# ─── errors ──────────────────────────────────────────────────────────────────


class BnetError(WowlabError):
    """An error of the Blizzard profile client. Its words never hold the
    secret, the token or a byte of an answer."""


class BnetNotConfiguredError(BnetError):
    """No API client is saved: nothing was asked."""


class BnetRefusedError(BnetError):
    """Blizzard refused the request (the credentials, or too many requests)."""


class BnetUnavailableError(BnetError):
    """Blizzard's API could not be reached or answered with an error."""


class BnetNameError(BnetError, ValueError):
    """A name, region or credential that is not one wowlab sends."""


# How a player adds an API client: said by `status`, `show`, `fetch` and the
# page, in these words only. They name each way the command is run: installed
# (`wowlab`), the Mac app's own program, and a source checkout (`uv run`).
SETUP_WORDS = (
    "wowlab has no Blizzard API client yet, so it asks Blizzard for nothing. "
    "Create one at develop.battle.net (sign in, open API Access, create a client; any "
    "redirect address will do). Then, in a terminal, run `wowlab blizzard login` (with the "
    "Mac app: `/Applications/wowlab.app/Contents/MacOS/wowlab blizzard login`; from a copy "
    "of the source: `uv run wowlab blizzard login`) and paste its client ID and client "
    "secret when asked. wowlab keeps them in its own settings folder on this computer, "
    "readable by you only, never in the game folder; the secret is never typed into the page."
)

# ─── where things are ────────────────────────────────────────────────────────

_FOLDER = "blizzard"
_CREDENTIALS = "client.json"
_PROFILES = "profiles"
_WHAT = "Blizzard API settings and profiles"
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_BINARY = getattr(os, "O_BINARY", 0)
_O_NONBLOCK = getattr(os, "O_NONBLOCK", 0)
# The largest settings file and saved answer read back.
_MAX_FILE_BYTES = 8 * 1024 * 1024
# The largest saved fetch written: below what is read back, so every saved
# fetch can be read again (`save_profile` drops the answers' raw parts first).
_MAX_SAVED_BYTES = 6 * 1024 * 1024
# Saved fetches kept per character; older ones are removed.
KEEP_FETCHES = 20


def blizzard_dir() -> Path:
    """`<user data dir>/wowlab/blizzard`, read at call time: the saved
    profiles' folder (and, in the first build, the API client's)."""
    return platformdirs.user_data_path("wowlab") / _FOLDER


def config_dir() -> Path:
    """`<user config dir>/wowlab/blizzard`, read at call time: the API
    client's folder (ADR-0040 (2): the user config directory)."""
    return platformdirs.user_config_path("wowlab") / _FOLDER


def credentials_path() -> Path:
    """The file that holds the API client's id and secret."""
    return config_dir() / _CREDENTIALS


def _old_credentials_path() -> Path:
    """Where the first build kept the API client: the data folder. Read once
    when it is there, moved to `credentials_path()`, then removed."""
    return blizzard_dir() / _CREDENTIALS


def profiles_dir() -> Path:
    """The folder of saved answers, one subfolder per character."""
    return blizzard_dir() / _PROFILES


# ─── credentials ─────────────────────────────────────────────────────────────

# What a client id or secret may be: Blizzard's are 32 letters and digits;
# anything else (a space, a quote, a line break) is refused before it is kept.
_CREDENTIAL = re.compile(r"[A-Za-z0-9_-]{8,128}")


class Credentials:
    """An API client's id and secret. `repr` and `str` never show the
    secret; `secret()` is the one way to read it. It cannot be pickled or
    copied (`__reduce_ex__` refuses), so the secret never reaches a
    serialized form by accident."""

    __slots__ = ("_secret", "client_id")

    def __init__(self, client_id: str, secret: str) -> None:
        if not _CREDENTIAL.fullmatch(client_id):
            raise BnetNameError(
                "a client ID is 8 to 128 letters, digits, '-' or '_', as develop.battle.net "
                "shows it; that is not one"
            )
        if not _CREDENTIAL.fullmatch(secret):
            raise BnetNameError(
                "a client secret is 8 to 128 letters, digits, '-' or '_', as "
                "develop.battle.net shows it; that is not one"
            )
        self.client_id = client_id
        self._secret = secret

    def secret(self) -> str:
        return self._secret

    @override
    def __repr__(self) -> str:
        return f"Credentials(client_id={self.client_id!r}, secret=<hidden>)"

    @override
    def __str__(self) -> str:
        return self.__repr__()

    @override
    def __reduce_ex__(self, protocol: Any) -> Any:
        raise TypeError("an API client's credentials are never pickled or copied")


def _refuse_place(folder: Path) -> None:
    """Refuse a folder inside a game install, or one that is a link."""
    try:
        refuse_install(folder, _WHAT)
    except LookLocationError as exc:
        raise BnetError(f"refused: {exc}; nothing was written or removed") from None
    if folder.is_symlink():
        raise BnetError(f"{folder} is a link; wowlab writes only into a real folder")


def _refuse_file(path: Path) -> None:
    """Refuse a file that is a link or not a regular file."""
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise BnetError(f"{path} is not a plain file; it was left alone")


def _write_private(path: Path, data: bytes, *, outside: bool = False) -> None:
    """`data` into `path`, whole, 0600, through a new temporary file beside
    it (`O_EXCL`, no link followed) moved into place. Refused, nothing
    written, when the folder is in a game install or is a link, or `path`
    is a link or not a regular file. With `outside` (the config folder),
    a folder made outside the user data directory is made 0700 too."""
    folder = path.parent
    _refuse_place(folder)
    make_private_dirs(folder, outside=outside)  # 0700 (M16-78)
    _refuse_place(folder)
    if not folder.is_dir():
        raise BnetError(f"{folder} is not a folder wowlab made")
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise BnetError(f"{path} is not a plain file; nothing was written")
    tmp = folder / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW | _O_BINARY, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def _read_private(path: Path, *, owner_only: bool = False) -> bytes:
    """The bytes of `path`, opened without following a link and without
    waiting (a pipe in its place is refused, never waited on); a file that
    is not a regular one, or over `_MAX_FILE_BYTES`, is refused, and with
    `owner_only` (on POSIX) one that other users may read."""
    fd = os.open(path, os.O_RDONLY | _O_NOFOLLOW | _O_BINARY | _O_NONBLOCK)
    with os.fdopen(fd, "rb") as handle:
        st = os.fstat(handle.fileno())
        if not stat.S_ISREG(st.st_mode):
            raise BnetError(f"{path} is not a plain file; it was not read")
        if st.st_size > _MAX_FILE_BYTES:
            raise BnetError(f"{path} is larger than wowlab reads; it was not read")
        if owner_only and os.name == "posix" and st.st_mode & 0o077:
            raise BnetError(
                f"{path} can be read by other users of this computer, so wowlab does not use "
                "it; run `wowlab blizzard login` again to keep the client readable by you only"
            )
        return handle.read(_MAX_FILE_BYTES + 1)[:_MAX_FILE_BYTES]


def _credentials_bytes(credentials: Credentials) -> bytes:
    body = {"client_id": credentials.client_id, "client_secret": credentials.secret()}
    return (json.dumps(body) + "\n").encode("ascii")


def _parse_credentials(path: Path, data: bytes) -> Credentials:
    try:
        body = json.loads(data.decode("utf-8"))
        client_id = body["client_id"]
        secret = body["client_secret"]
        if not isinstance(client_id, str) or not isinstance(secret, str):
            raise TypeError
        return Credentials(client_id, secret)
    except (ValueError, KeyError, TypeError, UnicodeDecodeError):
        raise BnetError(
            f"{path} does not hold a client ID and secret wowlab can use; run "
            "`wowlab blizzard login` again"
        ) from None


def _read_file(path: Path) -> bytes | None:
    """The settings file's bytes, None when there is none."""
    try:
        return _read_private(path, owner_only=True)
    except FileNotFoundError:
        return None
    except OSError as exc:
        why = f" ({exc.strerror})" if exc.strerror else ""
        raise BnetError(f"{path} could not be read{why}") from None


def _guarded_folder(folder: Path) -> bool:
    """Whether `folder` is there (a link counts), refused first when it is
    in a game install or a link."""
    if not (folder.exists() or folder.is_symlink()):
        return False
    _refuse_place(folder)
    return True


def _move_old_credentials() -> None:
    """The first build's place for the API client (the data folder), under
    the same refusals as every write: its folder in a game install, or a
    link, is refused before anything is read, written or removed; a file
    there is moved to `credentials_path()` (unless one is there already,
    which wins) and removed. Nothing happens when the old folder is not
    there."""
    if not _guarded_folder(blizzard_dir()):
        return
    old = _old_credentials_path()
    _refuse_file(old)
    data = _read_file(old)
    if data is None:
        return
    if not credentials_path().exists():
        creds = _parse_credentials(old, data)
        _write_private(credentials_path(), _credentials_bytes(creds), outside=True)
    old.unlink()


def save_credentials(credentials: Credentials) -> Path:
    """Keep the API client's id and secret in `credentials_path()` (0600),
    replacing what was there; return the path."""
    _move_old_credentials()
    path = credentials_path()
    _write_private(path, _credentials_bytes(credentials), outside=True)
    return path


def read_credentials() -> Credentials | None:
    """The saved API client, or None when none is saved. A file that cannot
    be read as one, or that other users can read, raises `BnetError` and
    never quotes the file."""
    _move_old_credentials()
    path = credentials_path()
    if not _guarded_folder(path.parent):
        return None
    data = _read_file(path)
    return None if data is None else _parse_credentials(path, data)


def clear_credentials() -> bool:
    """Remove the saved API client; whether there was one. Refused, nothing
    removed, where a write is refused: a folder in a game install or that
    is a link, or a file that is a link or not a regular file."""
    _move_old_credentials()
    path = credentials_path()
    if not _guarded_folder(path.parent):
        return False
    _refuse_file(path)
    try:
        path.unlink()
    except FileNotFoundError:
        return False
    return True


# ─── games, regions and names ────────────────────────────────────────────────


class Game(BaseModel):
    """One game's place in the profile API (`bnet_games.toml`)."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    label: str = Field(pattern=r"^[a-z][a-z0-9-]{0,31}$")
    namespace: str = Field(pattern=r"^[a-z0-9-]*\{region\}[a-z0-9-]*$")
    character: str = Field(pattern=r"^/profile/wow/character/[a-z{}/-]+$")
    from_version: str = Field(alias="from", pattern=r"^\d+(\.\d+)*$")
    below: str = Field(pattern=r"^\d+(\.\d+)*$")


class _Games(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    game: list[Game]


def load_games(text: str | None = None) -> list[Game]:
    """The games of `bnet_games.toml` (or of `text`)."""
    if text is None:
        text = (importlib.resources.files("wowlab_core") / "bnet_games.toml").read_text(
            encoding="utf-8"
        )
    games = _Games.model_validate(tomllib.loads(text)).game
    for g in games:
        if "{first}" not in g.character:
            raise ValueError(f"game {g.label!r}: its character path has no {{first}}")
        rest = g.character.replace("{first}", "").replace("{second}", "")
        if "{" in rest or "}" in rest:
            raise ValueError(f"game {g.label!r}: its character path names an unknown field")
    return games


def _parts(version: str) -> tuple[int, ...] | None:
    pieces = version.split(".")
    if not all(p.isascii() and p.isdigit() for p in pieces):
        return None
    return tuple(int(p) for p in pieces)


def game_for_version(version: str | None, games: Sequence[Game] | None = None) -> Game | None:
    """The game a client version belongs to, or None when no entry covers it."""
    if version is None:
        return None
    have = _parts(version)
    if have is None:
        return None
    for g in load_games() if games is None else games:
        low, high = _parts(g.from_version), _parts(g.below)
        if low is not None and high is not None and low <= have < high:
            return g
    return None


_NAME_MAX = 24


class Name(NamedTuple):
    first: str
    second: str | None

    @property
    def game_name(self) -> str:
        """The in-game form: first, space, second."""
        return self.first if self.second is None else f"{self.first} {self.second}"


def parse_name(text: str) -> Name:
    """A character's first and second name from `First-Second` (the folder
    and crew-book form) or `First Second` (the in-game form). Each name is
    1 to 24 letters; anything else is refused, so the address wowlab builds
    from it holds letters only. The text is NFC-normalized first, so a
    name typed with combining accents is the same name as the precomposed one."""
    stripped = unicodedata.normalize("NFC", text).strip()
    first, sep, second = stripped.replace(" ", "-", 1).partition("-")
    parts = [first, second] if sep else [first]
    for part in parts:
        if not (1 <= len(part) <= _NAME_MAX) or not all(ch.isalpha() for ch in part):
            raise BnetNameError(
                "a character's name is its first name, then a space or '-' and its second "
                "name, each 1 to 24 letters"
            )
    return Name(first, second if sep else None)


def _slug(part: str) -> str:
    return urllib.parse.quote(part.lower(), safe="")


def character_path(game: Game, name: Name) -> str:
    """The profile path of `name` in `game`."""
    if "{second}" in game.character and name.second is None:
        raise BnetNameError(
            "Blizzard names this game's characters by their first and second name; give both"
        )
    path = game.character.replace("{first}", _slug(name.first))
    return path.replace("{second}", _slug(name.second or ""))


# ─── hosts ───────────────────────────────────────────────────────────────────

# Blizzard's documented hosts: the token address, and the API per region.
_OAUTH_HOST: Mapping[Region, str] = {
    "us": "oauth.battle.net",
    "eu": "oauth.battle.net",
    "kr": "oauth.battle.net",
    "tw": "oauth.battle.net",
    "cn": "oauth.battlenet.com.cn",
}
_API_HOST: Mapping[Region, str] = {
    "us": "us.api.blizzard.com",
    "eu": "eu.api.blizzard.com",
    "kr": "kr.api.blizzard.com",
    "tw": "tw.api.blizzard.com",
    "cn": "gateway.battlenet.com.cn",
}
_LOCALE: Mapping[Region, str] = {
    "us": "en_US",
    "eu": "en_GB",
    "kr": "ko_KR",
    "tw": "zh_TW",
    "cn": "zh_CN",
}
# The one host a portrait address may name (ADR-0040 (1)).
RENDER_HOST = "render.worldofwarcraft.com"
MAX_REPLY_BYTES = 4 * 1024 * 1024
_TIMEOUT_S = 20.0


def check_region(value: str) -> Region:
    for r in REGIONS:
        if value == r:
            return r
    raise BnetNameError("a region is one of " + ", ".join(REGIONS))


# ─── the transport ───────────────────────────────────────────────────────────


class Request:
    """One request: method, https URL, headers and body. The headers hold
    the secret or the token, so it is a plain class, not a dataclass
    (`dataclasses.asdict` cannot reach them), `repr` shows the method and
    URL only, and it cannot be pickled or copied."""

    __slots__ = ("body", "headers", "method", "url")

    def __init__(
        self, method: str, url: str, headers: Mapping[str, str], body: bytes | None = None
    ) -> None:
        self.method = method
        self.url = url
        self.headers = headers
        self.body = body

    @override
    def __repr__(self) -> str:
        return f"Request({self.method} {self.url})"

    @override
    def __reduce_ex__(self, protocol: Any) -> Any:
        raise TypeError("a request to Blizzard is never pickled or copied")


@dataclass(frozen=True)
class Reply:
    """One answer: the HTTP status and up to `MAX_REPLY_BYTES` of its body
    (`too_large` when there was more)."""

    status: int
    body: bytes
    too_large: bool = False


Send = Callable[[Request], Reply]


class _SameHostRedirect(urllib.request.HTTPRedirectHandler):
    """Follows a redirect only to https on the same host; refuses any other."""

    @override
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        joined = urllib.parse.urljoin(req.full_url, newurl)
        old = urllib.parse.urlsplit(req.full_url)
        new = urllib.parse.urlsplit(joined)
        if (
            new.scheme != "https"
            or new.hostname != old.hostname
            or new.port != old.port
            or new.username is not None
            or new.password is not None
        ):
            raise BnetRefusedError(
                "Blizzard's answer pointed to another address, which wowlab does not follow; "
                "nothing was fetched from it"
            )
        return super().redirect_request(req, fp, code, msg, headers, joined)


def _opener() -> urllib.request.OpenerDirector:
    """An opener that speaks https only (certificates checked), follows a
    redirect only to the same host, and takes no proxy from the
    environment."""
    opener = urllib.request.OpenerDirector()
    for handler in (
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
        _SameHostRedirect(),
        urllib.request.HTTPDefaultErrorHandler(),
        urllib.request.HTTPErrorProcessor(),
        urllib.request.UnknownHandler(),
    ):
        opener.add_handler(handler)
    return opener


def _check_url(url: str) -> None:
    parts = urllib.parse.urlsplit(url)
    allowed = set(_OAUTH_HOST.values()) | set(_API_HOST.values())
    if parts.scheme != "https" or parts.hostname not in allowed or parts.port is not None:
        raise BnetRefusedError("wowlab asks Blizzard's own https addresses only; that is not one")
    if parts.username is not None or parts.password is not None:
        raise BnetRefusedError("wowlab asks Blizzard's own https addresses only; that is not one")


def urllib_send(request: Request) -> Reply:
    """Send `request` with the standard library (the default transport)."""
    _check_url(request.url)
    req = urllib.request.Request(
        request.url, data=request.body, headers=dict(request.headers), method=request.method
    )
    try:
        with _opener().open(req, timeout=_TIMEOUT_S) as answer:
            body = answer.read(MAX_REPLY_BYTES + 1)
            return Reply(int(answer.status), body[:MAX_REPLY_BYTES], len(body) > MAX_REPLY_BYTES)
    except urllib.error.HTTPError as exc:
        with contextlib.suppress(OSError):
            exc.close()
        return Reply(int(exc.code), b"")
    except BnetError:
        raise
    except (urllib.error.URLError, OSError, ValueError) as exc:
        reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
        kind = (
            "the connection timed out"
            if isinstance(reason, TimeoutError)
            else ("the address could not be found or reached")
        )
        if isinstance(reason, ssl.SSLError):
            kind = "its certificate could not be checked"
        raise BnetUnavailableError(f"could not reach Blizzard's API: {kind}") from None


# ─── what is shown ───────────────────────────────────────────────────────────

PartStatus = Literal["ok", "not_found", "error", "not_asked"]


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class PartState(_Model):
    """How one part of a profile was got: `ok`, `not_found` (Blizzard has
    none: a character below level 10, or a portrait not made yet),
    `error` (`detail` says why) or `not_asked`. `fetched_at` is when it
    was asked (UTC, ISO 8601); `from_earlier` when the newest fetch did not
    get it and this is an earlier fetch's."""

    status: PartStatus
    fetched_at: str | None = None
    detail: str | None = None
    from_earlier: bool = False


class BnetCharacter(_Model):
    """The profile's summary, each field None when the answer lacks it.
    `ruleset` is the realm type's name as Blizzard gives it, and
    `ruleset_slug` its `slug` (`normal`, `pvp`, `roleplay`, `roleplay-pvp`:
    the keys the Raider.IO link's `[rulesets]` table takes; whether a Forever
    profile carries it is [verify])."""

    name: str | None = None
    surname: str | None = None
    level: int | None = None
    race: str | None = None
    character_class: str | None = None
    active_spec: str | None = None
    faction: str | None = None
    gender: str | None = None
    ruleset: str | None = None
    ruleset_slug: str | None = None
    realm: str | None = None
    guild: str | None = None
    average_item_level: int | None = None
    equipped_item_level: int | None = None
    achievement_points: int | None = None
    last_login: str | None = None


class BnetItem(_Model):
    """One equipped item."""

    slot: str | None = None
    slot_name: str | None = None
    item_id: int | None = None
    name: str | None = None
    quality: str | None = None
    item_level: int | None = None
    enchantments: list[str] = Field(default_factory=list)


class BnetAsset(_Model):
    """One picture of the character on Blizzard's render host: its kind
    (`avatar`, `inset`, `main-raw`, ...) and its https address. An address
    on any other host is dropped."""

    key: str
    url: str


class BnetGuild(_Model):
    name: str | None = None
    faction: str | None = None
    member_count: int | None = None
    achievement_points: int | None = None
    realm: str | None = None
    created: str | None = None


class BnetProfile(_Model):
    """One character's public profile as Blizzard gave it: the game,
    region and name asked, when the newest fetch was made, and each part
    with its own state and time."""

    game: str
    region: Region
    name: str
    first_name: str
    second_name: str | None
    fetched_at: str
    character: BnetCharacter | None = None
    character_state: PartState
    equipment: list[BnetItem] = Field(default_factory=list)
    equipment_state: PartState
    portrait: list[BnetAsset] = Field(default_factory=list)
    portrait_state: PartState
    guild: BnetGuild | None = None
    guild_state: PartState


class _Record(_Model):
    """One saved fetch: the profile and the answers it was read from."""

    version: Literal[1] = 1
    profile: BnetProfile
    raw: dict[str, Any] = Field(default_factory=dict)


# ─── reading an answer ───────────────────────────────────────────────────────

_TEXT_MAX = 200


def _text(value: object) -> str | None:
    """A string, or an object's `name` (or `type`) string, capped."""
    if isinstance(value, Mapping):
        value = value.get("name", value.get("type"))
    if isinstance(value, str) and value:
        return value[:_TEXT_MAX]
    return None


def _int(value: object) -> int | None:
    if isinstance(value, Mapping):
        value = value.get("value")
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if -(2**53) < value < 2**53 else None


def _when(value: object) -> str | None:
    """Blizzard's milliseconds since 1970 as UTC ISO 8601."""
    ms = _int(value)
    if ms is None or ms <= 0:
        return None
    try:
        return datetime.fromtimestamp(ms / 1000, tz=UTC).isoformat(timespec="seconds")
    except (OverflowError, OSError, ValueError):
        return None


def _slug_of(value: object) -> str | None:
    """An object's `slug` when it is a short lowercase slug, else None."""
    slug = value.get("slug") if isinstance(value, Mapping) else None
    if isinstance(slug, str) and re.fullmatch(r"[a-z0-9][a-z0-9-]{0,31}", slug):
        return slug
    return None


def _character(body: Mapping[str, Any]) -> BnetCharacter:
    return BnetCharacter(
        name=_text(body.get("name")),
        surname=_text(body.get("surname")),
        level=_int(body.get("level")),
        race=_text(body.get("race")),
        character_class=_text(body.get("character_class")),
        active_spec=_text(body.get("active_spec")),
        faction=_text(body.get("faction")),
        gender=_text(body.get("gender")),
        ruleset=_text(body.get("ruleset")),
        ruleset_slug=_slug_of(body.get("ruleset")),
        realm=_text(body.get("realm")),
        guild=_text(body.get("guild")),
        average_item_level=_int(body.get("average_item_level")),
        equipped_item_level=_int(body.get("equipped_item_level")),
        achievement_points=_int(body.get("achievement_points")),
        last_login=_when(body.get("last_login_timestamp")),
    )


def _items(body: Mapping[str, Any]) -> list[BnetItem]:
    rows = body.get("equipped_items")
    out: list[BnetItem] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, Mapping):
            continue
        slot = row.get("slot")
        item = row.get("item")
        enchants = row.get("enchantments")
        out.append(
            BnetItem(
                slot=_text(slot.get("type")) if isinstance(slot, Mapping) else None,
                slot_name=_text(slot),
                item_id=_int(item.get("id")) if isinstance(item, Mapping) else None,
                name=_text(row.get("name")),
                quality=_text(row.get("quality")),
                item_level=_int(row.get("level")),
                enchantments=[
                    t
                    for e in (enchants if isinstance(enchants, list) else [])
                    if isinstance(e, Mapping)
                    for t in (_text(e.get("display_string")),)
                    if t is not None
                ],
            )
        )
        if len(out) >= 64:
            break
    return out


_ASSET_KEY = re.compile(r"[a-z][a-z0-9-]{0,31}")
_RENDER_PATH = re.compile(r"/[A-Za-z0-9/._-]{1,400}")


def render_url(value: object) -> str | None:
    """`value` when it is an https address on Blizzard's render host with a
    plain path and no query, user, port or fragment; else None."""
    if not isinstance(value, str) or len(value) > 500:
        return None
    parts = urllib.parse.urlsplit(value)
    if parts.scheme != "https" or parts.netloc != RENDER_HOST:
        return None
    if parts.query or parts.fragment or not _RENDER_PATH.fullmatch(parts.path):
        return None
    if ".." in parts.path.split("/"):
        return None
    return value


def _assets(body: Mapping[str, Any]) -> list[BnetAsset]:
    rows = body.get("assets")
    out: list[BnetAsset] = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, Mapping):
            continue
        key, url = row.get("key"), render_url(row.get("value"))
        if isinstance(key, str) and _ASSET_KEY.fullmatch(key) and url is not None:
            out.append(BnetAsset(key=key, url=url))
    return out[:8]


def _guild(body: Mapping[str, Any]) -> BnetGuild:
    return BnetGuild(
        name=_text(body.get("name")),
        faction=_text(body.get("faction")),
        member_count=_int(body.get("member_count")),
        achievement_points=_int(body.get("achievement_points")),
        realm=_text(body.get("realm")),
        created=_when(body.get("created_timestamp")),
    )


def _guild_path(profile: Mapping[str, Any], api_host: str) -> str | None:
    """The guild's path from the profile's `guild.key.href`, when it is on
    the API host and under `/data/wow/guild/`; else None."""
    guild = profile.get("guild")
    key = guild.get("key") if isinstance(guild, Mapping) else None
    href = key.get("href") if isinstance(key, Mapping) else None
    if not isinstance(href, str) or len(href) > 500:
        return None
    parts = urllib.parse.urlsplit(href)
    if parts.scheme != "https" or parts.netloc != api_host:
        return None
    if parts.username is not None or parts.password is not None or parts.port is not None:
        return None
    if not re.fullmatch(r"/data/wow/guild/[A-Za-z0-9%._-]+(/[A-Za-z0-9%._-]+)?", parts.path):
        return None
    for segment in parts.path.split("/")[4:]:
        # No `.` or `..` segment, and no `.` or `/` spelled as an escape.
        if segment in (".", "..") or re.search(r"%(?:2[eEfF]|5[cC])", segment):
            return None
    return parts.path


# ─── asking ──────────────────────────────────────────────────────────────────


def _now() -> datetime:
    return datetime.now(UTC)


def _stamp(at: datetime) -> str:
    return at.astimezone(UTC).isoformat(timespec="seconds")


# How deep an answer may nest: Blizzard's are a few levels; a deeper one
# is refused, so saving it can never pass the serializer's own limit.
_MAX_JSON_DEPTH = 32


def _depth(value: object, limit: int) -> bool:
    """Whether `value` nests no deeper than `limit` (iteratively)."""
    stack: list[tuple[object, int]] = [(value, 0)]
    while stack:
        item, level = stack.pop()
        if level > limit:
            return False
        if isinstance(item, dict):
            stack.extend((v, level + 1) for v in item.values())
        elif isinstance(item, list):
            stack.extend((v, level + 1) for v in item)
    return True


def _json(reply: Reply) -> dict[str, Any] | None:
    """The answer as a JSON object, or None: too large, not JSON, nested
    deeper than `_MAX_JSON_DEPTH`, or holding text that cannot be written
    back as UTF-8 (a lone surrogate)."""
    if reply.too_large:
        return None
    try:
        body = json.loads(reply.body.decode("utf-8"))
        if not isinstance(body, dict) or not _depth(body, _MAX_JSON_DEPTH):
            return None
        json.dumps(body, ensure_ascii=False).encode("utf-8")
    except (ValueError, UnicodeError, RecursionError):
        return None
    return body


def _status_words(status: int) -> str:
    if status == 401:
        return (
            "Blizzard did not accept the token; run `wowlab blizzard login` again with the "
            "client from develop.battle.net"
        )
    if status == 403:
        return "Blizzard refused this request (HTTP 403)"
    if status == 429:
        return "Blizzard asked wowlab to slow down (too many requests); try again in a minute"
    if status >= 500:
        return f"Blizzard's API answered with an error (HTTP {status}); try again later"
    return f"Blizzard's API answered HTTP {status}"


def _token(credentials: Credentials, region: Region, send: Send) -> str:
    pair = f"{credentials.client_id}:{credentials.secret()}".encode("ascii")
    request = Request(
        "POST",
        f"https://{_OAUTH_HOST[region]}/token",
        {
            "Authorization": "Basic " + base64.b64encode(pair).decode("ascii"),
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": f"wowlab/{__version__}",
        },
        b"grant_type=client_credentials",
    )
    reply = send(request)
    if reply.status in (400, 401, 403):
        raise BnetRefusedError(
            "Blizzard refused the client ID and secret wowlab has; check them at "
            "develop.battle.net and run `wowlab blizzard login` again"
        )
    if reply.status == 429 or reply.status >= 500 or reply.status != 200:
        raise BnetUnavailableError(_status_words(reply.status))
    body = _json(reply)
    token = body.get("access_token") if body is not None else None
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9._~+/=-]{8,4096}", token):
        raise BnetUnavailableError("Blizzard's token answer was not one wowlab can read")
    return token


def fetch_profile(
    credentials: Credentials,
    game: Game,
    region: Region,
    name: Name,
    *,
    send: Send | None = None,
    now: Callable[[], datetime] = _now,
) -> tuple[BnetProfile, dict[str, Any]]:
    """Ask Blizzard for one character: a token, then the profile, and when
    there is one the equipment, the portrait and the guild. Returns the
    profile and the answers it was read from (for the saved fetch). A
    refused credential, too many requests, or an API that cannot be
    reached raises; a part Blizzard has none of is `not_found`. `send`
    is the transport, `urllib_send` by default."""
    send = send if send is not None else urllib_send  # looked up at call time
    region = check_region(region)
    path = character_path(game, name)
    host = _API_HOST[region]
    query = urllib.parse.urlencode(
        {"namespace": game.namespace.replace("{region}", region), "locale": _LOCALE[region]}
    )
    token = _token(credentials, region, send)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": f"wowlab/{__version__}",
    }
    raw: dict[str, Any] = {}
    started = _stamp(now())

    def ask(part: str, at_path: str) -> tuple[dict[str, Any] | None, PartState]:
        when = _stamp(now())
        reply = send(Request("GET", f"https://{host}{at_path}?{query}", headers))
        if reply.status == 404:
            return None, PartState(status="not_found", fetched_at=when, detail=_NOT_FOUND[part])
        if reply.status in (401, 429):
            raise BnetRefusedError(_status_words(reply.status))
        if reply.status != 200:
            return None, PartState(
                status="error", fetched_at=when, detail=_status_words(reply.status)
            )
        body = _json(reply)
        if body is None:
            return None, PartState(
                status="error", fetched_at=when, detail="the answer was not JSON wowlab reads"
            )
        raw[part] = body
        return body, PartState(status="ok", fetched_at=when)

    not_asked = PartState(status="not_asked", detail="the profile was not found")
    body, character_state = ask("character", path)
    character = _character(body) if body is not None else None
    equipment: list[BnetItem] = []
    portrait: list[BnetAsset] = []
    guild: BnetGuild | None = None
    equipment_state = portrait_state = guild_state = not_asked
    if body is not None:
        got, equipment_state = ask("equipment", path + "/equipment")
        equipment = _items(got) if got is not None else []
        got, portrait_state = ask("portrait", path + "/character-media")
        portrait = _assets(got) if got is not None else []
        guild_path = _guild_path(body, host)
        if guild_path is not None:
            got, guild_state = ask("guild", guild_path)
            guild = _guild(got) if got is not None else None
        else:
            guild_state = PartState(
                status="not_asked",
                detail="the profile names no guild"
                if body.get("guild") is None
                else "the profile's guild address is not one wowlab asks",
            )
    profile = BnetProfile(
        game=game.label,
        region=region,
        name=name.game_name,
        first_name=name.first,
        second_name=name.second,
        fetched_at=started,
        character=character,
        character_state=character_state,
        equipment=equipment,
        equipment_state=equipment_state,
        portrait=portrait,
        portrait_state=portrait_state,
        guild=guild,
        guild_state=guild_state,
    )
    return profile, raw


_NOT_FOUND = {
    "character": (
        "Blizzard has no public profile under this name in this region (a character below "
        "level 10 has none, and a new one may take a while to appear)"
    ),
    "equipment": "Blizzard has no equipment for this character",
    "portrait": (
        "Blizzard has no portrait for this character yet (it often appears hours after "
        "the character does)"
    ),
    "guild": "Blizzard has no profile for this guild",
}

# ─── the saved fetches ───────────────────────────────────────────────────────

_RECORD_NAME = re.compile(r"\A(\d{8}T\d{12}Z)-[0-9a-f]{8}\.json\Z")


def _key(game: str, region: str, name: Name) -> str:
    text = "\0".join((game, region, name.first.lower(), (name.second or "").lower()))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def _character_dir(game: str, region: str, name: Name) -> Path:
    return profiles_dir() / _key(game, region, name)


def _record_bytes(profile: BnetProfile, raw: Mapping[str, Any]) -> bytes:
    """One saved fetch as bytes, at most `_MAX_SAVED_BYTES`. An answer's raw
    part that cannot be written as JSON is dropped; then, while it is too
    large, the largest raw part. A fetch that cannot be written at all
    raises `BnetUnavailableError`."""
    kept = {k: v for k, v in raw.items() if _part_size(v) is not None}
    while True:
        try:
            data = _Record(profile=profile, raw=kept).model_dump_json().encode("utf-8")
        except (PydanticSerializationError, ValueError, UnicodeError, RecursionError):
            data = None
        if data is not None and len(data) <= _MAX_SAVED_BYTES:
            return data
        if not kept:
            raise BnetUnavailableError(
                "Blizzard's answer could not be saved as wowlab keeps fetches; nothing was saved"
            )
        del kept[max(kept, key=lambda k: _part_size(kept[k]) or 0)]


def _part_size(value: object) -> int | None:
    """The size of one raw part written as JSON, None when it cannot be."""
    try:
        return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))
    except (ValueError, TypeError, UnicodeError, RecursionError):
        return None


def save_profile(
    profile: BnetProfile, raw: Mapping[str, Any], *, at: datetime | None = None
) -> Path:
    """Save one fetch as its own file, named by `at` (now, to the
    microsecond, by default), keep the newest `KEEP_FETCHES`, and return
    its path."""
    name = Name(profile.first_name, profile.second_name)
    folder = _character_dir(profile.game, profile.region, name)
    at = (at or _now()).astimezone(UTC)
    stamp = at.strftime("%Y%m%dT%H%M%S") + f"{at.microsecond:06d}Z"
    path = folder / f"{stamp}-{uuid.uuid4().hex[:8]}.json"
    _write_private(path, _record_bytes(profile, raw))
    for old in _records(folder)[KEEP_FETCHES:]:
        old.unlink(missing_ok=True)
    return path


def _records(folder: Path) -> list[Path]:
    """The saved fetches in `folder`, newest first."""
    try:
        names = [p for p in folder.iterdir() if _RECORD_NAME.match(p.name)]
    except (FileNotFoundError, NotADirectoryError):
        return []
    return sorted(names, key=lambda p: p.name, reverse=True)


def load_profile(game: str, region: Region, name: Name) -> tuple[BnetProfile | None, int]:
    """The newest saved fetch of this character, each part that fetch did
    not get filled from the newest earlier fetch that did (`from_earlier`),
    and the number of saved fetches. (None, 0) when there is none. A file
    that cannot be read is passed over."""
    records: list[_Record] = []
    for path in _records(_character_dir(game, region, name)):
        try:
            records.append(_Record.model_validate_json(_read_private(path)))
        except (OSError, BnetError, ValidationError, ValueError):
            continue
    if not records:
        return None, 0
    newest = records[0].profile
    update: dict[str, Any] = {}
    for part, fields in _PART_FIELDS.items():
        state: PartState = getattr(newest, f"{part}_state")
        if state.status == "ok":
            continue
        for older in records[1:]:
            old_state: PartState = getattr(older.profile, f"{part}_state")
            if old_state.status == "ok":
                update[f"{part}_state"] = old_state.model_copy(update={"from_earlier": True})
                for f in fields:
                    update[f] = getattr(older.profile, f)
                break
    return newest.model_copy(update=update), len(records)


_PART_FIELDS: Mapping[str, tuple[str, ...]] = {
    "character": ("character",),
    "equipment": ("equipment",),
    "portrait": ("portrait",),
    "guild": ("guild",),
}
