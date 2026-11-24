"""links: the address of every outside page wowlab links to (M21-03).

ADR-0019's amendment of 2026-11-23: a link the player clicks may carry a
character's first and second name, region and guild name, and game ids. It
opens in the player's own browser when, and only when, they click it; the
Lab requests nothing, fetches nothing and embeds nothing from it, and the
page's CSP does not change.

Everything about a site is data in the shipped `link_sites.toml`: its
origin, how a name becomes part of its addresses (case, spaces,
apostrophes, accents, hyphens), the address of each link kind per "room",
the shapes of the addresses a player pastes, and the examples the tests
rebuild byte for byte. Which room a client version is in comes from
`dressing_sites.toml`'s band table (`dressing.room_for`), so there is one
table of rooms; nothing here names a flavor, a product or a build (L6).

`dressing` stays beside this module rather than on it: its hash codec, slot
table and examples are its own, and moving a reviewed builder that has
nothing to share but an origin would only churn it. The page checks the
dressing room's address against the Wowhead origin listed here, and a test
keeps the two files' origins equal.

Every builder is a pure function: no network, no file but the shipped one.
It fails closed: a name holding anything the site's rule does not allow, a
region the site has no pages for, a client with no room, or an id out of
bounds gives `NoLink` with the reason in the player's words, never a
malformed address. Every address is checked once more before it is
returned: https, the site's origin exactly, no user name or password, and
only the characters a URL parser leaves as they are.
"""

from __future__ import annotations

import functools
import re
import string
import tomllib
import unicodedata
import urllib.parse
from collections.abc import Mapping
from importlib import resources
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from wowlab_core import dressing
from wowlab_core.errors import WowlabError

__all__ = [
    "SITES_FILE",
    "WOWHEAD_BONUS",
    "Link",
    "LinkError",
    "LinkSites",
    "LinkSitesError",
    "NoLink",
    "PastedLink",
    "armory_character",
    "armory_guild",
    "build",
    "check_pasted",
    "journey",
    "keystone_dungeon",
    "load_link_sites",
    "origins",
    "parse_link_sites",
    "profiles",
    "raiderio",
    "room_of",
    "wowhead_item",
    "wowhead_spell",
]

SITES_FILE = "link_sites.toml"
WOWHEAD_BONUS = False
"""Whether a Wowhead item address carries `?bonus=<id>`: off until the
owner's check (M21-O2) confirms Wowhead's Forever pages read it. Taken over
from M22-06's `twink`, which builds its Wowhead links here."""
_SIZE_LIMIT = 64 * 1024  # the shipped file is about 17 KiB
_KEY = re.compile(r"[a-z][a-z0-9_]{0,31}")
_ORIGIN = re.compile(r"https://[a-z0-9]+(?:[.-][a-z0-9]+)*\.[a-z]{2,24}")
_REGION = re.compile(r"[a-z]{2}")
_NAME_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_ROOM = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")  # a room label, as dressing_sites.toml's
_PLACEHOLDER = re.compile(r"\{([a-z_]{1,16})\}")
# What an address may hold after its origin: unreserved characters, `/`,
# `?`, `=`, `&` and percent escapes. A URL parser writes every one of them
# back as it is, so the page's round-trip check holds for any built address.
_TAIL = re.compile(r"(?:[A-Za-z0-9._~/?=&-]|%[0-9A-F]{2})*")
_TEMPLATE_TEXT = re.compile(r"(?:[A-Za-z0-9._~/?=&-]|%[0-9A-F]{2})*")
_MAX_PASTED = 512
_FIXED = ("region", "locale", "ruleset", "id")
_APOSTROPHES = ("'", chr(0x2019))  # the typewriter and the typographic apostrophe
_ASCII_LETTERS = frozenset(string.ascii_letters)


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


# ─── the result ──────────────────────────────────────────────────────────────


class Link(_Model):
    """An address to show as a link: which template made it (`link`), the
    site's key and name, what the page shows (`label`) and the address."""

    link: str
    site: str
    site_name: str
    label: str
    url: str


class NoLink(_Model):
    """No address, and why, in the player's words."""

    link: str
    site: str
    site_name: str
    label: str
    why: str


class PastedLink(_Model):
    """An address the player pasted, checked against one of the
    `[pasted]` shapes: the shape's key, site, kind and label, and the
    address as wowlab keeps it."""

    shape: str
    site: str
    site_name: str
    kind: str
    label: str
    url: str


class LinkError(WowlabError):
    """A pasted address wowlab does not keep, with why."""


# ─── the shipped file ────────────────────────────────────────────────────────


class LinkSitesError(ValueError):
    """`link_sites.toml` cannot be used (a packaging fault)."""


class Site(_Model):
    name: str = Field(min_length=1, max_length=64)
    origin: str


class SlugRule(_Model):
    case: Literal["lower", "keep"]
    spaces: str = Field(max_length=8)
    apostrophes: str = Field(max_length=8)
    hyphens: str = Field(max_length=8)
    accents: Literal["percent", "refuse"]
    max_length: int = Field(ge=1, le=256)
    # Names the site spells its own way: the name as the game writes it
    # (compared without case) and the site's slug or slugs for it. A name
    # with several (a dungeon in wings) gets no single link.
    names: dict[str, list[str]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _plain(self) -> SlugRule:
        for name in ("spaces", "apostrophes", "hyphens"):
            value = getattr(self, name)
            if value != "refuse" and not _TEMPLATE_TEXT.fullmatch(value):
                raise ValueError(f"{name} is {value!r}: 'refuse' or plain address text")
        folded = [unicodedata.normalize("NFC", n).casefold() for n in self.names]
        if len(set(folded)) != len(folded):
            raise ValueError("a name is listed twice in names")
        for name, slugs in self.names.items():
            if not name or not slugs or any(not _NAME_SLUG.fullmatch(x) for x in slugs):
                raise ValueError(f"names: {name!r} needs slugs of a-z, 0-9 and single hyphens")
        return self


class Template(_Model):
    site: str
    label: str = Field(min_length=1, max_length=64)
    kind: Literal["profile", "guild", "item", "spell", "dungeon"]
    regions: list[str] | None = None
    fields: dict[str, str] = {}
    rooms: dict[str, str]


class Pasted(_Model):
    site: str
    kind: str = Field(pattern=r"^[a-z]{1,16}$")
    label: str = Field(min_length=1, max_length=64)
    path: str = Field(min_length=2, max_length=256)
    drop_after_path: bool
    bare: str | None = Field(default=None, max_length=256)
    build: str | None = None


class Example(_Model):
    link: str | None = None
    pasted: str | None = None
    recorded: str
    given: dict[str, str | int]
    address: str


class Limits(_Model):
    id: int = Field(ge=1)


class LinkSites(_Model):
    limits: Limits
    sites: dict[str, Site]
    locales: dict[str, str]
    rulesets: dict[str, str]
    slugs: dict[str, SlugRule]
    links: dict[str, Template]
    pasted: dict[str, Pasted]
    example: list[Example]

    @model_validator(mode="after")
    def _consistent(self) -> LinkSites:
        for key, site in self.sites.items():
            if not _KEY.fullmatch(key) or not _ORIGIN.fullmatch(site.origin):
                raise ValueError(f"site {key!r}: {site.origin!r} is not https://<host>")
        for region, locale in self.locales.items():
            if not _REGION.fullmatch(region) or not re.fullmatch(r"[a-z]{2}-[a-z]{2}", locale):
                raise ValueError(f"locale {region!r}: {locale!r}")
        for word, slug in self.rulesets.items():
            if not re.fullmatch(r"[a-z-]{1,24}", word) or not re.fullmatch(r"[a-z-]{1,24}", slug):
                raise ValueError(f"ruleset {word!r}: {slug!r}")
        for key, t in self.links.items():
            if not _KEY.fullmatch(key) or t.site not in self.sites:
                raise ValueError(f"link {key!r} names no listed site")
            for name, rule in t.fields.items():
                if name in _FIXED or not _KEY.fullmatch(name) or rule not in self.slugs:
                    raise ValueError(f"link {key!r}: field {name!r} has no slug rule")
            for region in t.regions or ():
                if not _REGION.fullmatch(region):
                    raise ValueError(f"link {key!r}: region {region!r}")
            for room, path in t.rooms.items():
                if not _ROOM.fullmatch(room) or not path.startswith("/"):
                    raise ValueError(f"link {key!r}: room {room!r} is not a label and a path")
                names = set(_PLACEHOLDER.findall(path))
                if not names <= {*_FIXED, *t.fields}:
                    raise ValueError(f"link {key!r}: {path!r} has an unknown placeholder")
                if ("region" in names or "locale" in names) and t.regions is None:
                    raise ValueError(f"link {key!r}: a region in the address and no regions")
                if not _TEMPLATE_TEXT.fullmatch(_PLACEHOLDER.sub("", path)):
                    raise ValueError(f"link {key!r}: {path!r} holds text a URL parser changes")
        for key, p in self.pasted.items():
            if not _KEY.fullmatch(key) or p.site not in self.sites:
                raise ValueError(f"pasted {key!r} names no listed site")
            for pattern in (p.path, p.bare):
                if pattern is not None:
                    try:
                        re.compile(pattern)
                    except re.error as exc:
                        raise ValueError(f"pasted {key!r}: {pattern!r} ({exc})") from None
            if not p.path.startswith("/"):
                raise ValueError(f"pasted {key!r}: the path must start with /")
            if (p.bare is None) != (p.build is None):
                raise ValueError(f"pasted {key!r}: bare and build go together")
            if p.build is not None and (
                set(_PLACEHOLDER.findall(p.build)) != {"code"} or not p.build.startswith("/")
            ):
                raise ValueError(f"pasted {key!r}: build must be a path holding {{code}}")
        for ex in self.example:
            if (ex.link is None) == (ex.pasted is None):
                raise ValueError("an example names a link or a pasted shape, not both")
            if ex.link is not None and ex.link not in self.links:
                raise ValueError(f"an example names the link {ex.link!r}, which is not listed")
            if ex.pasted is not None and ex.pasted not in self.pasted:
                raise ValueError(f"an example names the shape {ex.pasted!r}, which is not listed")
        shown = {ex.link for ex in self.example} | {ex.pasted for ex in self.example}
        missing = (set(self.links) | set(self.pasted)) - shown
        if missing:
            raise ValueError(f"no example for {', '.join(sorted(missing))}")
        return self


def parse_link_sites(text: str) -> LinkSites:
    """The sites file from TOML text. Raises `LinkSitesError`."""
    if len(text) > _SIZE_LIMIT:
        raise LinkSitesError(f"larger than {_SIZE_LIMIT} characters")
    try:
        data: dict[str, Any] = tomllib.loads(text)
        return LinkSites.model_validate(data)
    # RecursionError: tomllib on deeply nested arrays; MemoryError and
    # TypeError: anything else a damaged file reaches.
    except (
        tomllib.TOMLDecodeError,
        ValidationError,
        ValueError,
        TypeError,
        RecursionError,
        MemoryError,
    ) as exc:
        raise LinkSitesError(f"{type(exc).__name__}: {exc}"[:500]) from exc


@functools.cache
def load_link_sites() -> LinkSites:
    """The shipped `link_sites.toml`. Raises `LinkSitesError` naming it."""
    where = resources.files("wowlab_core").joinpath(SITES_FILE)
    try:
        data = where.read_bytes()
        if len(data) > _SIZE_LIMIT:
            raise LinkSitesError(f"larger than {_SIZE_LIMIT} bytes")
        return parse_link_sites(data.decode("utf-8"))
    except (OSError, UnicodeDecodeError, LinkSitesError) as exc:
        raise LinkSitesError(f"{where}: {exc}") from exc


def origins(sites: LinkSites | None = None) -> dict[str, str]:
    """Each site's key and origin: the page's allowlist."""
    sites = load_link_sites() if sites is None else sites
    return {key: site.origin for key, site in sites.sites.items()}


# ─── the builder ─────────────────────────────────────────────────────────────


def room_of(version: str | None) -> str | None:
    """The room of a client version, by `dressing_sites.toml`'s band table,
    or None when no band covers it or that file cannot be used."""
    try:
        table = dressing.load_sites()
    except dressing.SitesError:
        return None
    # The room of each version is kept beside the table it was read from
    # (M21-05: one per address, 150,000 for a crew list of 50,000 players),
    # and read again whenever the table is another object.
    held = _ROOMS.get(version)
    if held is not None and held[0] is table:
        return held[1]
    room = dressing.room_for(table, version)
    if len(_ROOMS) >= _ROOMS_HELD:
        _ROOMS.clear()
    _ROOMS[version] = (table, room)
    return room


_ROOMS: dict[str | None, tuple[dressing.Sites, str | None]] = {}
_ROOMS_HELD = 64


class _RefusedError(Exception):
    """A value the address cannot carry; the message is the reason."""


def _slug(value: str, rule: SlugRule, what: str) -> str:
    """`value` as the rule writes it in an address. Raises `_RefusedError`."""
    if not isinstance(value, str) or not value:
        raise _RefusedError(f"the {what} is empty")
    text = unicodedata.normalize("NFC", value)
    for name, slugs in rule.names.items():
        if unicodedata.normalize("NFC", name).casefold() == text.casefold():
            if len(slugs) == 1:
                return slugs[0]
            raise _RefusedError(
                f"{name} has {len(slugs)} maps there, one for each part, so no single link"
            )
    if len(text) > rule.max_length:
        raise _RefusedError(f"the {what} is longer than {rule.max_length} characters")
    if text != text.strip(" ") or "  " in text:
        raise _RefusedError(f"the {what} starts or ends with a space, or holds two in a row")
    out: list[str] = []
    for char in text:
        if char == " ":
            replace = rule.spaces
        elif char in _APOSTROPHES:
            replace = rule.apostrophes
        elif char == "-":
            replace = rule.hyphens
        elif char in _ASCII_LETTERS:
            # An ASCII letter is unreserved: `quote` would give it back as
            # it is (M21-05: a crew list of 50,000 players builds 150,000
            # addresses, and `quote` per letter was most of the time).
            out.append(char.lower() if rule.case == "lower" else char)
            continue
        elif char.isalpha() and unicodedata.category(char) in ("Lu", "Ll", "Lt", "Lo"):
            if not char.isascii() and rule.accents == "refuse":
                raise _RefusedError(f"the {what} holds a letter the site's addresses do not take")
            letter = char.lower() if rule.case == "lower" else char
            out.append(urllib.parse.quote(letter, safe=""))
            continue
        else:
            raise _RefusedError(f"the {what} holds a character a name never has")
        if replace == "refuse":
            word = {" ": "a space", "-": "a hyphen"}.get(char, "an apostrophe")
            raise _RefusedError(f"the {what} holds {word}, which the site's addresses do not take")
        out.append(replace)
    slug = "".join(out)
    if not slug:
        raise _RefusedError(f"the {what} is empty once written for the site")
    return slug


_WORDS = {
    "first": "first name",
    "second": "second name",
    "guild": "guild name",
    "dungeon": "dungeon name",
}


def _checked(url: str, origin: str) -> bool:
    """Whether `url` is the origin followed by text a URL parser keeps, with
    no `.` or `..` path segment."""
    if not url.startswith(origin):
        return False
    tail = url[len(origin) :]
    if tail and not tail.startswith("/"):
        return False
    if not _TAIL.fullmatch(tail):
        return False
    path = tail.split("?", 1)[0]
    if any(part in (".", "..") for part in path.split("/")):
        return False
    parts = urllib.parse.urlsplit(url)
    return (
        parts.scheme == "https"
        and f"https://{parts.netloc}" == origin
        and parts.username is None
        and parts.password is None
        and parts.fragment == ""
    )


def build(
    link: str,
    *,
    version: str | None,
    region: str | None = None,
    ruleset: str | None = None,
    id: int | None = None,
    names: Mapping[str, str | None] | None = None,
    sites: LinkSites | None = None,
) -> Link | NoLink:
    """The address of one link kind (a key of `[links]`), or why there is
    none. Never raises. `version` is the client's (it picks the room);
    `names` holds the template's name fields (`first`, `second`, `guild`,
    `dungeon`). `sites` is the shipped file unless a test gives another."""
    if sites is None:
        try:
            sites = load_link_sites()
        except LinkSitesError:
            return NoLink(link=link, site="", site_name="", label="", why=_UNUSABLE)
    t = sites.links.get(link)
    if t is None:
        return NoLink(link=link, site="", site_name="", label="", why="no such link")
    site = sites.sites[t.site]

    def none(why: str) -> NoLink:
        return NoLink(link=link, site=t.site, site_name=site.name, label=t.label, why=why)

    room = room_of(version)
    path = t.rooms.get(room) if room is not None else None
    if version is None:
        return none("the game version is not known")
    if path is None:
        return none(f"{site.name} has no pages wowlab knows for this game version")
    values: dict[str, str] = {}
    try:
        if t.regions is not None:
            if region is None:
                raise _RefusedError("the character's region is not known")
            if region not in t.regions:
                raise _RefusedError(f"{site.name} has no pages for the {region} region")
            values["region"] = region
            if "{locale}" in path:
                locale = sites.locales.get(region)
                if locale is None:
                    raise _RefusedError(f"{site.name} has no pages for the {region} region")
                values["locale"] = locale
        if "{ruleset}" in path:
            slug = sites.rulesets.get(ruleset) if ruleset is not None else None
            if slug is None:
                raise _RefusedError(
                    "the character's ruleset (Normal, PvP, Roleplay or Roleplay PvP) is not known"
                )
            values["ruleset"] = slug
        if "{id}" in path:
            if type(id) is not int or not 1 <= id <= sites.limits.id:
                raise _RefusedError("the id is not one the game uses")
            values["id"] = str(id)
        given = names or {}
        for name, rule in t.fields.items():
            value = given.get(name)
            if value is None:
                raise _RefusedError(f"the {_WORDS.get(name, name)} is not known")
            values[name] = _slug(value, sites.slugs[rule], _WORDS.get(name, name))
    except _RefusedError as exc:
        return none(str(exc))
    url = site.origin + _PLACEHOLDER.sub(lambda m: values[m.group(1)], path)
    if not _checked(url, site.origin):
        return none("its address did not check out")
    return Link(link=link, site=t.site, site_name=site.name, label=t.label, url=url)


_UNUSABLE = "the shipped list of sites cannot be used"


def armory_character(
    first: str | None, second: str | None, region: str | None, *, version: str | None
) -> Link | NoLink:
    """A Forever character's page on Blizzard's armory. No second name, no
    region, or `cn`: no link, and why."""
    return build(
        "armory_character", version=version, region=region, names={"first": first, "second": second}
    )


def armory_guild(guild: str | None, region: str | None, *, version: str | None) -> Link | NoLink:
    """A guild's page on Blizzard's armory."""
    return build("armory_guild", version=version, region=region, names={"guild": guild})


def journey(
    first: str | None, second: str | None, region: str | None, *, version: str | None
) -> Link | NoLink:
    """The Journey Companion's lookup of a character."""
    return build(
        "journey", version=version, region=region, names={"first": first, "second": second}
    )


def raiderio(
    first: str | None,
    second: str | None,
    region: str | None,
    ruleset: str | None,
    *,
    version: str | None,
) -> Link | NoLink:
    """A character's Raider.IO profile; none while the ruleset is unknown."""
    return build(
        "raiderio",
        version=version,
        region=region,
        ruleset=ruleset,
        names={"first": first, "second": second},
    )


def wowhead_item(
    item_id: int | None, *, version: str | None, bonus: int | None = None
) -> Link | NoLink:
    """An item's Wowhead page. `bonus` (a suffix's bonus id, from M22-06's
    twink panel) is added as `?bonus=<id>` only while `WOWHEAD_BONUS` is
    True, and only when it is an id the game uses; otherwise the item
    alone."""
    made = build("wowhead_item", version=version, id=item_id)
    if not isinstance(made, Link) or not WOWHEAD_BONUS or bonus is None:
        return made
    if type(bonus) is not int or not 1 <= bonus <= load_link_sites().limits.id:
        return made
    url = f"{made.url}?bonus={bonus}"
    if not _checked(url, load_link_sites().sites[made.site].origin):
        return made
    return made.model_copy(update={"url": url})


def wowhead_spell(spell_id: int | None, *, version: str | None) -> Link | NoLink:
    """A spell's Wowhead page."""
    return build("wowhead_spell", version=version, id=spell_id)


def keystone_dungeon(name: str | None, *, version: str | None) -> Link | NoLink:
    """A dungeon's map on Keystone.guru, by the dungeon's name."""
    return build("keystone_dungeon", version=version, names={"dungeon": name})


def profiles(
    first: str | None,
    second: str | None,
    region: str | None,
    *,
    version: str | None,
    ruleset: str | None = None,
) -> list[Link | NoLink]:
    """A character's profile links: the armory, the Journey Companion and
    Raider.IO, in that order. A ForeverTwinking address is never built: it
    is one the player pasted for a character of their own."""
    return [
        armory_character(first, second, region, version=version),
        journey(first, second, region, version=version),
        raiderio(first, second, region, ruleset, version=version),
    ]


# ─── pasted addresses ────────────────────────────────────────────────────────


def _shapes_words(sites: LinkSites, site: str | None) -> str:
    shown = [
        f"{sites.sites[p.site].name} {p.kind}"
        for p in sites.pasted.values()
        if site is None or p.site == site
    ]
    return ", ".join(dict.fromkeys(shown))


def check_pasted(text: str, site: str | None = None, sites: LinkSites | None = None) -> PastedLink:
    """The address the player pasted, checked against the `[pasted]` shapes
    (of `site` only, when given), as wowlab keeps it. Raises `LinkError`
    saying why it is not kept. A shape with `drop_after_path` keeps the
    address without its `?` or `#` part; one with `bare` takes the code
    alone too."""
    sites = load_link_sites() if sites is None else sites
    if site is not None and site not in {p.site for p in sites.pasted.values()}:
        known = ", ".join(sorted({p.site for p in sites.pasted.values()}))
        raise LinkError(f"wowlab keeps pasted addresses for these sites only: {known}")
    if not isinstance(text, str):
        raise LinkError("the address is not text")
    given = text.strip()
    if not given:
        raise LinkError("the address is empty")
    if len(given) > _MAX_PASTED:
        raise LinkError(f"the address is longer than {_MAX_PASTED} characters")
    if not given.isascii() or any(c.isspace() or ord(c) < 0x20 or ord(c) == 0x7F for c in given):
        raise LinkError("the address holds a space, a control character or a letter past ASCII")
    shapes = [(k, p) for k, p in sites.pasted.items() if site is None or p.site == site]
    for key, p in shapes:
        if p.bare is not None and p.build is not None and re.fullmatch(p.bare, given):
            origin = sites.sites[p.site].origin
            url = origin + p.build.replace("{code}", given)
            if _checked(url, origin):
                return _pasted(sites, key, p, url)
    for key, p in shapes:
        origin = sites.sites[p.site].origin
        if not given.startswith(origin + "/"):
            continue
        rest = given[len(origin) :]
        cut = min((i for i in (rest.find("?"), rest.find("#")) if i >= 0), default=-1)
        if cut >= 0:
            if not p.drop_after_path:
                continue
            rest = rest[:cut]
        if not re.fullmatch(p.path, rest):
            continue
        url = origin + rest
        if _checked(url, origin):
            return _pasted(sites, key, p, url)
    raise LinkError(
        "this is not an address wowlab keeps; it takes the address of a "
        f"{_shapes_words(sites, site)} page, copied from the browser's address bar"
    )


def _pasted(sites: LinkSites, key: str, p: Pasted, url: str) -> PastedLink:
    return PastedLink(
        shape=key,
        site=p.site,
        site_name=sites.sites[p.site].name,
        kind=p.kind,
        label=p.label,
        url=url,
    )
