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

M21-07 adds the `talents` kind, WoW Forever Builds' talent calculator: its
class and race words by the game's ids and its own check of a talent code
are `[talents]` of the same file, and a template may mark a placeholder
`optional` (the race), whose query pair is then left out when its value is
not known. The code itself is `talentlink`'s.

Every builder is a pure function: no network, no file but the shipped one.
It fails closed: a name holding anything the site's rule does not allow, a
region the site has no pages for, a client with no room, or an id out of
bounds gives `NoLink` with the reason in the player's words, never a
malformed address. Every address is checked once more before it is
returned: https, the site's origin exactly, no user name or password, and
only the characters a URL parser leaves as they are.

One link is not a template: Purist's look (`purist_look`, M21-06), whose
address carries the character's race, body type, class, barber-shop
choices and item ids in its `#` part, in the format of the site's own
script. Its fields are `[look]` in the same file, each with the line of
the site's script it was read from.
"""

from __future__ import annotations

import functools
import re
import tomllib
import unicodedata
import urllib.parse
from collections.abc import Mapping, Sequence
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
    "Look",
    "LookExample",
    "LookLink",
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
    "purist_look",
    "raiderio",
    "room_of",
    "talents",
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
_GAME = re.compile(r"[a-z]{1,16}")
_PETS = re.compile(r"[0-9]{1,9}\.[0-9]{1,9}\.[0-9]{1,9}")
_MAX_PASTED = 512
# Placeholders the builder fills itself, never from a slug rule: `class`
# and `race` from `[talents]`'s ids (M21-07), `ranks` a talent code checked
# against `[talents] ranks`.
_FIXED = ("region", "locale", "ruleset", "id", "class", "race", "ranks")
_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_GAME_ID = re.compile(r"[1-9][0-9]{0,8}")
# What a talent code may hold whatever `[talents] ranks` allows: digits and
# `-`, so a looser pattern in the file can never put `&`, `/` or `#` into an
# address.
_RANKS = re.compile(r"[0-9-]{0,256}")
# The placeholders a template may mark optional: the builder knows how to
# leave out only these (`_talent_values`).
_OPTIONAL = frozenset({"race"})
_APOSTROPHES = ("'", chr(0x2019))  # the typewriter and the typographic apostrophe


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


class LookLink(_Model):
    """The address of a character's look on an outfit site (M21-06): as
    `Link`, plus what the address leaves out of the record, each in the
    player's words (empty when nothing is)."""

    link: str
    site: str
    site_name: str
    label: str
    url: str
    left_out: list[str]


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
    kind: Literal["profile", "guild", "item", "spell", "dungeon", "talents"]
    regions: list[str] | None = None
    fields: dict[str, str] = {}
    rooms: dict[str, str]
    # Placeholders that may be unknown: each stands alone as the value of a
    # query pair after the first (`&race={race}`), and the pair is left out
    # of the address when its value is not known (M21-07).
    optional: list[str] = []


class Talents(_Model):
    """`[talents]` (M21-07): a site's words for the game's classes and races,
    by the game's ids (`ChrClasses` and `ChrRaces`), and the check its own
    script makes of a talent code."""

    classes: dict[str, str]
    races: dict[str, str]
    ranks: str = Field(min_length=2, max_length=128)

    @model_validator(mode="after")
    def _plain(self) -> Talents:
        for what, table in (("class", self.classes), ("race", self.races)):
            for key, slug in table.items():
                if not _GAME_ID.fullmatch(key) or not _SLUG.fullmatch(slug) or len(slug) > 32:
                    raise ValueError(f"talents {what} {key!r}: {slug!r}")
        try:
            re.compile(self.ranks)
        except re.error as exc:
            raise ValueError(f"talents ranks {self.ranks!r} ({exc})") from None
        return self


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


class LookExample(_Model):
    """One look the tests rebuild: what `purist_look` is given and the
    address it must return, byte for byte."""

    recorded: str
    version: str
    name: str | None = None
    race_id: int
    sex: int
    class_id: int
    choices: list[list[int]] = []  # [option, choice], as the record lists them
    items: list[list[int]] = []  # [inventory slot, item id], as the record lists them
    address: str


class Look(_Model):
    """`[look]`: an outfit site's share link (M21-06). The address is the
    origin, `path`, `#` and the hash `<format>|<name>|<race>.<sex>.<classic>.
    <sheathed>.<class>.<game>|<pets>|<option>:<choice>,…|<item>,…`."""

    site: str
    label: str = Field(min_length=1, max_length=64)
    path: str
    format: int = Field(ge=1, le=99)
    rooms: dict[str, str]  # room → the site's key for the game
    classic: Literal[0, 1]
    sheathed: Literal[0, 1, 2]
    pets: str
    name: str  # a [slugs] rule
    slots: dict[str, int]  # the client's inventory slot → the site's display slot
    max_choices: int = Field(ge=1, le=256)
    example: list[LookExample] = Field(min_length=1)


class LinkSites(_Model):
    limits: Limits
    sites: dict[str, Site]
    locales: dict[str, str]
    rulesets: dict[str, str]
    slugs: dict[str, SlugRule]
    links: dict[str, Template]
    pasted: dict[str, Pasted]
    talents: Talents | None = None
    look: Look
    example: list[Example]

    @model_validator(mode="after")
    def _look(self) -> LinkSites:
        look = self.look
        if look.site not in self.sites or look.name not in self.slugs:
            raise ValueError("look: its site or its name rule is not listed")
        if not look.path.startswith("/") or not _TEMPLATE_TEXT.fullmatch(look.path):
            raise ValueError(f"look: {look.path!r} is not a path a URL parser keeps")
        for room, game in look.rooms.items():
            if not _ROOM.fullmatch(room) or not _GAME.fullmatch(game):
                raise ValueError(f"look: room {room!r} or game {game!r}")
        if not _PETS.fullmatch(look.pets):
            raise ValueError(f"look: pets {look.pets!r}")
        for slot, shown in look.slots.items():
            if not re.fullmatch(r"[1-9][0-9]?", slot) or not 1 <= shown <= 99:
                raise ValueError(f"look: slot {slot!r} → {shown}")
        if len(set(look.slots.values())) != len(look.slots):
            raise ValueError("look: two slots share a display slot")
        return self

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
                if names & {"class", "race", "ranks"} and self.talents is None:
                    raise ValueError(
                        f"link {key!r}: a class, race or talent code in the address and no "
                        "[talents]"
                    )
                if not _TEMPLATE_TEXT.fullmatch(_PLACEHOLDER.sub("", path)):
                    raise ValueError(f"link {key!r}: {path!r} holds text a URL parser changes")
                for name in t.optional:
                    if name not in _OPTIONAL:
                        raise ValueError(f"link {key!r}: {name!r} cannot be optional")
                    if name not in names or len(_optional_pairs(path, name)) != 1:
                        raise ValueError(
                            f"link {key!r}: optional {name!r} is not one query pair after the first"
                        )
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


def _optional_pairs(path: str, name: str) -> list[str]:
    """Each query pair `&<key>={name}` of `path` after its `?`, or none when
    `{name}` is anywhere else in it too."""
    pattern = r"&[a-z][a-z0-9_]{0,31}=\{" + re.escape(name) + r"\}(?=&|$)"
    pairs = [m for m in re.finditer(pattern, path) if 0 <= path.find("?") < m.start()]
    if len(pairs) != path.count("{" + name + "}"):
        return []
    return [m.group(0) for m in pairs]


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
        return dressing.room_for(dressing.load_sites(), version)
    except dressing.SitesError:
        return None


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
    class_id: int | None = None,
    race_id: int | None = None,
    ranks: str | None = None,
    sites: LinkSites | None = None,
) -> Link | NoLink:
    """The address of one link kind (a key of `[links]`), or why there is
    none. Never raises. `version` is the client's (it picks the room);
    `names` holds the template's name fields (`first`, `second`, `guild`,
    `dungeon`); `class_id` and `race_id` are the game's ids, written as
    `[talents]` names them, and `ranks` a talent code `[talents] ranks`
    accepts (M21-07). `sites` is the shipped file unless a test gives
    another."""
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
        path = _talent_values(path, t, sites, values, class_id, race_id, ranks, site.name)
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


def _game_slug(table: Mapping[str, str], value: int | None) -> str | None:
    if type(value) is not int or not 1 <= value <= 999_999_999:
        return None
    return table.get(str(value))


def _talent_values(
    path: str,
    t: Template,
    sites: LinkSites,
    values: dict[str, str],
    class_id: int | None,
    race_id: int | None,
    ranks: str | None,
    site_name: str,
) -> str:
    """Fill `values` with the class, race and talent code `path` holds, and
    return `path` without an optional pair whose value is not known. Raises
    `_RefusedError`."""
    talents = sites.talents
    if talents is None:  # the file's check: no template here then holds one
        return path
    if "{class}" in path:
        slug = _game_slug(talents.classes, class_id)
        if slug is None and class_id is None:
            raise _RefusedError("the character's class is not known")
        if slug is None:
            raise _RefusedError(f"{site_name} has no page for the character's class")
        values["class"] = slug
    if "{race}" in path:
        slug = _game_slug(talents.races, race_id)
        if slug is not None:
            values["race"] = slug
        elif "race" in t.optional:
            path = path.replace(_optional_pairs(path, "race")[0], "", 1)
        else:
            raise _RefusedError("the character's race is not known to the site")
    if "{ranks}" in path:
        if (
            not isinstance(ranks, str)
            or not _RANKS.fullmatch(ranks)
            or not re.fullmatch(talents.ranks, ranks)
        ):
            raise _RefusedError(f"the talent code is not one {site_name} takes")
        values["ranks"] = ranks
    return path


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


def talents(
    class_id: int | None, race_id: int | None, ranks: str | None, *, version: str | None
) -> Link | NoLink:
    """A character's talents in WoW Forever Builds' talent calculator, from
    the talent code `talentlink` makes (M21-07). A race the site does not
    name is left out of the address; a class it does not name gives no
    link."""
    return build("talents", version=version, class_id=class_id, race_id=race_id, ranks=ranks)


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


# ─── a character's look (M21-06) ─────────────────────────────────────────────

# What a built hash may hold: the format, a name of letters and percent
# escapes, the model part, the pets, the option:choice pairs and the item
# ids. Every character is one the URL parser leaves as it is in a fragment,
# so the page's round-trip check holds.
_LOOK_HASH = re.compile(
    r"[0-9]{1,2}\|(?:[A-Za-z]|%[0-9A-F]{2}){0,256}"
    r"\|[0-9]{1,10}\.[01]\.[01]\.[012]\.[0-9]{1,10}\.[a-z]{1,16}"
    r"\|[0-9]{1,9}\.[0-9]{1,9}\.[0-9]{1,9}"
    r"\|(?:[0-9]{1,10}:[0-9]{1,10}(?:,[0-9]{1,10}:[0-9]{1,10}){0,255})?"
    r"\|(?:[0-9]{1,10}(?:,[0-9]{1,10}){0,98})?"
)


def _an_id(value: object, limit: int) -> bool:
    return type(value) is int and 1 <= value <= limit


_MAX_PAIRS = 256  # more than any barber shop's options or any client's slots


def _listed(value: object) -> list[object] | None:
    """`value` as a list when it is a list or tuple of at most `_MAX_PAIRS`
    entries, else None."""
    if not isinstance(value, (list, tuple)) or len(value) > _MAX_PAIRS:
        return None
    return list(value)


def _pairs(value: object) -> list[tuple[object, object]] | None:
    """`value` as (first, second) pairs when it is a list or tuple of
    two-entry lists or tuples, else None."""
    listed = _listed(value)
    if listed is None:
        return None
    out: list[tuple[object, object]] = []
    for pair in listed:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            return None
        out.append((pair[0], pair[1]))
    return out


def _listed_names(value: object) -> dict[int, str]:
    """The slot names given, those that are an int slot and plain text."""
    if not isinstance(value, Mapping):
        return {}
    return {
        k: v
        for k, v in value.items()
        if type(k) is int and isinstance(v, str) and 0 < len(v) <= 64 and v.isprintable()
    }


def purist_look(
    name: str | None,
    race_id: int | None,
    sex: int | None,
    class_id: int | None,
    choices: Sequence[tuple[int, int]],
    items: Sequence[tuple[int, int | None]],
    *,
    version: str | None,
    slot_names: Mapping[int, str] | None = None,
    left_out: Sequence[str] = (),
    sites: LinkSites | None = None,
) -> LookLink | NoLink:
    """The address of a character's look on Purist, or why there is none.
    Never raises: `choices` or `items` that are not a list of pairs are
    left out with a reason, as a value that is not an id is.

    `name` is the character's name as the game shows it; `race_id`, `sex`
    (`Enum.UnitSex`, 0 or 1) and `class_id` are the record's identity;
    `choices` are the barber-shop record's (option id, choice id) pairs and
    `items` the equipped (inventory slot, item id) pairs, each as the record
    lists them; `version` is the client's (it picks the room, and the room
    the site's game key). `slot_names` gives a slot's name for what is left
    out ("slot 5 Chest"). `left_out` is what the caller already left out,
    in the player's words; it heads the result's own list.

    The hash is Purist's format 2 as its `/lib/look.js` writes it (`encode`,
    read 2026-11-24; `link_sites.toml`'s `[look]` cites each line): the name
    with each space as `%20`; race, sex, classic models, sheathed, class and
    game; no pets; the pairs in ascending option order (the order a
    JavaScript object gives integer keys, so the address is the one Purist
    itself writes); the item ids in the site's display-slot order, the
    jewellery left out (it has no look on the site). A name the site's rule
    refuses, or none, leaves the name part empty (Purist then names the
    look itself); pairs that are not ids, or an option listed twice, leave
    the whole customization part empty; both are listed in `left_out`."""
    if sites is None:
        try:
            sites = load_link_sites()
        except LinkSitesError:
            return NoLink(link="purist_look", site="", site_name="", label="", why=_UNUSABLE)
    look = sites.look
    site = sites.sites[look.site]
    limit = sites.limits.id

    def none(why: str) -> NoLink:
        return NoLink(
            link="purist_look", site=look.site, site_name=site.name, label=look.label, why=why
        )

    room = room_of(version)
    game = look.rooms.get(room) if room is not None else None
    if game is None:
        return none(f"{site.name} has no looks wowlab knows for this game version")
    if race_id is None or sex is None or class_id is None:
        return none("the record does not name the character's race, body type and class")
    if (
        not _an_id(race_id, limit)
        or not _an_id(class_id, limit)
        or type(sex) is not int
        or sex not in (0, 1)
    ):
        return none("the record's race, body type or class is not one the game uses")
    out: list[str] = [w for w in _listed(left_out) or () if isinstance(w, str)]
    label = ""
    if name is None:
        out.append("the character's name, because it is not known")
    else:
        try:
            label = _slug(name, sites.slugs[look.name], "name")
        except _RefusedError as exc:
            out.append(f"the character's name, because {exc}")
    pairs: dict[int, int] = {}
    given = _pairs(choices)
    if given is None:
        out.append("the face and hair: the barber-shop record is not a list of choices")
        given = []
    for option, choice in given:
        if (
            not isinstance(option, int)
            or not isinstance(choice, int)
            or not _an_id(option, limit)
            or not _an_id(choice, limit)
        ):
            out.append("the face and hair: the barber-shop record holds a value that is not an id")
            pairs = {}
            break
        if option in pairs:
            out.append(f"the face and hair: the barber-shop record lists option {option} twice")
            pairs = {}
            break
        pairs[option] = choice
    if len(pairs) > look.max_choices:
        out.append(f"the face and hair: more than {look.max_choices} barber-shop choices")
        pairs = {}
    shown: dict[int, int] = {}
    worn = _pairs(items)
    if worn is None:
        out.append("the gear: the record's gear is not a list of slots")
        worn = []
    names = _listed_names(slot_names)
    for slot, item in worn:
        where = look.slots.get(str(slot)) if type(slot) is int else None
        if where is None or where in shown:
            continue  # the jewellery has no look; a slot listed twice keeps its first item
        words = f"slot {slot} {names[slot]}" if slot in names else f"slot {slot}"
        if item is None:
            out.append(f"the item in {words}: its link has no item id")
        elif not isinstance(item, int) or not _an_id(item, limit):
            out.append(f"the item in {words}: its id is not one the game uses")
        else:
            shown[where] = item
    model = f"{race_id}.{sex}.{look.classic}.{look.sheathed}.{class_id}.{game}"
    text = "|".join(
        [
            str(look.format),
            label,
            model,
            look.pets,
            ",".join(f"{o}:{pairs[o]}" for o in sorted(pairs)),
            ",".join(str(shown[d]) for d in sorted(shown)),
        ]
    )
    url = f"{site.origin}{look.path}#{text}"
    parts = urllib.parse.urlsplit(url)
    if (
        not _LOOK_HASH.fullmatch(text)
        or not _checked(site.origin + look.path, site.origin)
        or parts.fragment != text
        or f"{parts.scheme}://{parts.netloc}" != site.origin
    ):
        return none("its address did not check out")
    return LookLink(
        link="purist_look",
        site=look.site,
        site_name=site.name,
        label=look.label,
        url=url,
        left_out=out,
    )


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
