"""dressing: the address of an outside dressing room for one character (M15-02).

docs/LAB_PLAN.md §16.3 (amendment of 2026-10-19). `build` turns what
`wowlab char gear --json` reports (the identity M15-01 records and the
equipped slots' item and bonus ids) into the address of the site's dressing
room, so the player can open it in their own browser and see the character
in its gear. It is a pure function: no network, no file but the shipped
`dressing_sites.toml`, nothing from the UI. The Lab never requests the
address; the page shows it as a link the player may click (ADR-0019 and
ADR-0031, amendments of 2026-10-19).

Everything about the site is data in `dressing_sites.toml`: its origin, its
rooms, which client version opens which room, the room's slots and the hash
codec, with the examples it was checked on. Nothing here names a flavor, a
product code or a build (L6): the room is chosen from the client's version as
`.build.info` gives it.

It fails closed. When the room for this client is unknown, the record has no
identity, no equipped item has a slot in the room, or any value is outside
what the room can be given (an item id of 0 or below, more bonus ids than an
item carries, a race id past any the game has, a value the codec cannot
write), the result is `DressingItems`: the item ids as text, in slot order,
and a note saying why. Never a malformed address: every hash is decoded again
and compared with what was meant before it is returned.
"""

from __future__ import annotations

import functools
import re
import tomllib
from collections.abc import Mapping, Sequence
from importlib import resources
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from wowlab_core import gear, labaddon

__all__ = [
    "SITES_FILE",
    "DressingItems",
    "DressingLink",
    "Sites",
    "SitesError",
    "UncodableError",
    "build",
    "decode",
    "encode",
    "level_of",
    "load_sites",
    "parse_sites",
    "room_for",
]

SITES_FILE = "dressing_sites.toml"
_SIZE_LIMIT = 64 * 1024  # the shipped file is about 8 KiB
_VERSION = re.compile(r"[0-9]{1,9}(?:\.[0-9]{1,9}){0,7}")
_LABEL = re.compile(r"[a-z0-9][a-z0-9-]{0,31}")
_PATH = re.compile(r"(?:/[a-z0-9-]{1,64}){1,4}")
_FIELD = re.compile(r"([a-z_]{1,32})(?:\.([0-9]{1,2}))?")
_HASH = re.compile(r"[0-9A-Za-z]{1,4096}")
DELIMITER = "|"


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


# ─── the result ──────────────────────────────────────────────────────────────


class DressingLink(_Model):
    """The room's address for this character, the site's name, and what the
    address holds and leaves out, in words."""

    url: str
    site: str
    note: str


class DressingItems(_Model):
    """No address: the equipped item ids as text, in the client's slot
    order, to paste into a dressing room by hand, and why there is no link."""

    items: list[str]
    note: str


# ─── the shipped file ────────────────────────────────────────────────────────


class SitesError(ValueError):
    """`dressing_sites.toml` cannot be used (a packaging fault)."""


class Site(_Model):
    name: str
    origin: str
    default_level: int


class Band(_Model):
    from_: str  # `from` in the file
    below: str
    room: str


class Limits(_Model):
    race_id: int
    item_id: int
    bonus_id: int
    bonus_ids_per_item: int
    level: int


class Slot(_Model):
    room: int
    game: int
    name: str


class Group(_Model):
    repeat: int
    segments: list[str]


class Codec(_Model):
    version: int
    alphabet: str
    largest_value: int
    zero_run: str
    delimiter: str
    delimiter_run: str
    trim_trailing_zeros: bool
    template: list[str]
    segments: int
    key: list[str]
    long: list[str]
    groups: dict[str, Group]

    @model_validator(mode="after")
    def _consistent(self) -> Codec:
        top = self.largest_value
        markers = (self.zero_run, self.delimiter, self.delimiter_run)
        if not 2 <= top < len(self.alphabet) or len(set(self.alphabet)) != len(self.alphabet):
            raise ValueError("the alphabet must hold distinct characters past largest_value")
        if not self.alphabet.isascii() or not self.alphabet.isalnum():
            raise ValueError("the alphabet must be ASCII letters and digits")
        if any(len(m) != 1 or m not in self.alphabet[top + 1 :] for m in markers):
            raise ValueError("each marker is one alphabet character past largest_value")
        if len(set(markers)) != 3:
            raise ValueError("the three markers must differ")
        if set(self.key) & set(self.long):
            raise ValueError("a field cannot be both key and long")
        expanded = _expand(self)
        if len(expanded) != self.segments:
            raise ValueError(
                f"the template expands to {len(expanded)} segments, not {self.segments}"
            )
        for at, segment in enumerate(expanded):
            if segment == DELIMITER:
                continue
            if _kind(self, segment) == "long" and expanded[at + 1 : at + 2] != [DELIMITER]:
                raise ValueError(f"the long field {segment} must be followed by the delimiter")
        if self.trim_trailing_zeros and (
            expanded[-1] == DELIMITER or _kind(self, expanded[-1]) != "key"
        ):
            raise ValueError("trailing zeros may be trimmed only when the template ends in a key")
        names = [s for s in expanded if s != DELIMITER and not _in_group(self, s)]
        if len(names) != len(set(names)):
            raise ValueError("a field outside a group appears twice in the template")
        return self


class Example(_Model):
    site: str
    room: str
    recorded: str
    client: str
    built_from: str
    address: str
    showed: str


class Sites(_Model):
    site: Site
    rooms: dict[str, str]
    band: list[Band]
    limits: Limits
    slot: list[Slot]
    codec: Codec
    example: list[Example]

    @model_validator(mode="after")
    def _consistent(self) -> Sites:
        if not re.fullmatch(r"https://[a-z0-9.-]{1,253}", self.site.origin):
            raise ValueError(f"the origin {self.site.origin!r} is not https://<host>")
        for label, path in self.rooms.items():
            if not _LABEL.fullmatch(label) or not _PATH.fullmatch(path):
                raise ValueError(f"room {label!r}: {path!r} is not a label and a path")
        for band in self.band:
            if band.room not in self.rooms:
                raise ValueError(f"a band names the room {band.room!r}, which [rooms] lacks")
            low, high = _version(band.from_), _version(band.below)
            if low is None or high is None or not low < high:
                raise ValueError(f"the band {band.from_!r} to {band.below!r} is not a range")
        rooms = [s.room for s in self.slot]
        games = [s.game for s in self.slot]
        if len(set(rooms)) != len(rooms) or len(set(games)) != len(games):
            raise ValueError("a room slot or a game slot is listed twice")
        expanded = set(_expand(self.codec))
        for s in self.slot:
            if f"item.{s.room}" not in expanded or f"bonus.{s.room}" not in expanded:
                raise ValueError(f"room slot {s.room} has no item and bonus in the template")
        return self


def _expand(codec: Codec) -> list[str]:
    out: list[str] = []
    for segment in codec.template:
        group = codec.groups.get(segment)
        if group is None:
            out.append(segment)
        else:
            out += group.segments * group.repeat
    return out


def _in_group(codec: Codec, name: str) -> bool:
    return any(name in g.segments for g in codec.groups.values())


def _kind(codec: Codec, segment: str) -> str:
    match = _FIELD.fullmatch(segment)
    if match is None:
        raise ValueError(f"{segment!r} is not a field name")
    base = match.group(1)
    if base in codec.key:
        return "key"
    if base in codec.long:
        return "long"
    raise ValueError(f"the field {segment!r} has no kind")


def parse_sites(text: str) -> Sites:
    """The sites file from TOML text. Raises `SitesError`."""
    try:
        data: dict[str, Any] = tomllib.loads(text)
        for band in data.get("band", []):
            if isinstance(band, dict) and "from" in band:
                band["from_"] = band.pop("from")
        return Sites.model_validate(data)
    except (tomllib.TOMLDecodeError, ValidationError, ValueError) as exc:
        raise SitesError(str(exc)) from exc


@functools.cache
def load_sites() -> Sites:
    """The shipped `dressing_sites.toml`. Raises `SitesError` naming it."""
    where = resources.files("wowlab_core").joinpath(SITES_FILE)
    try:
        data = where.read_bytes()
        if len(data) > _SIZE_LIMIT:
            raise SitesError(f"larger than {_SIZE_LIMIT} bytes")
        return parse_sites(data.decode("utf-8"))
    except (OSError, UnicodeDecodeError, SitesError) as exc:
        raise SitesError(f"{where}: {exc}") from exc


# ─── the codec ───────────────────────────────────────────────────────────────


class UncodableError(ValueError):
    """A value the hash cannot hold."""


def _long(codec: Codec, value: int) -> str:
    top = codec.largest_value
    digits: list[int] = []
    while value > top:
        digits.append(value % top)
        value //= top
    digits.append(value)
    return "".join(codec.alphabet[d] for d in reversed(digits))


def _count(codec: Codec, marker: str, count: int) -> str:
    """`marker` and `count`, the run-length form of both passes."""
    top = codec.largest_value
    if count <= top:
        return marker + codec.alphabet[count]
    if count - top <= top:
        return marker * 2 + codec.alphabet[count - top]
    raise UncodableError(f"a run of {count} is longer than the hash can write")


def encode(codec: Codec, values: Mapping[str, int]) -> str:
    """The hash of `values` (field name: value; an absent field is 0).
    Raises `UncodableError` for a value the hash cannot hold."""
    top = codec.largest_value
    parts: list[str] = []
    for segment in _expand(codec):
        if segment == DELIMITER:
            parts.append(codec.delimiter)
            continue
        value = values.get(segment, 0)
        if value < 0:
            raise UncodableError(f"{segment} is {value}, below 0")
        if _kind(codec, segment) == "key":
            if value > top:
                raise UncodableError(f"{segment} is {value}, more than one character holds ({top})")
            parts.append(codec.alphabet[value])
        else:
            parts.append(_long(codec, value))
    zero = codec.alphabet[0]
    pair = zero + codec.delimiter
    plain = "".join(parts)
    if codec.trim_trailing_zeros:
        # The template ends `| separate_shoulders`, so this drops at most that
        # one key field, which `decode` reads back as 0.
        plain = plain.rstrip(zero)
    text = re.sub(
        f"{re.escape(zero)}{{2,}}", lambda m: _count(codec, codec.zero_run, len(m.group())), plain
    )
    return re.sub(
        f"(?:{re.escape(pair)}){{2,}}",
        lambda m: _count(codec, codec.delimiter_run, len(m.group()) // 2),
        text,
    )


def _meant(codec: Codec, values: Mapping[str, int]) -> dict[str, int]:
    """What `decode` should read back from `encode(codec, values)`."""
    out: dict[str, int] = {}
    seen: dict[str, int] = {}
    for segment in _expand(codec):
        if segment == DELIMITER:
            continue
        if _in_group(codec, segment):
            seen[segment] = seen.get(segment, 0) + 1
            out[f"{segment}#{seen[segment]}"] = 0
        else:
            out[segment] = values.get(segment, 0)
    return out


def _runs(codec: Codec, text: str, marker: str, unit: str) -> str:
    """`text` with every run-length form of `marker` written out as `unit`s."""
    top = codec.largest_value
    out: list[str] = []
    at = 0
    while at < len(text):
        char = text[at]
        if char != marker:
            out.append(char)
            at += 1
            continue
        extra = 0
        if text[at + 1 : at + 2] == marker:
            extra = top
            at += 1
        index = codec.alphabet.find(text[at + 1 : at + 2]) if at + 1 < len(text) else -1
        if not 0 <= index <= top:
            raise UncodableError(f"a run marker at {at} is not followed by a count")
        out.append(unit * (index + extra))
        at += 2
    return "".join(out)


def decode(codec: Codec, text: str) -> dict[str, int]:
    """The values a hash holds, every field of the template (groups as
    `<name>#<n>`). Raises `UncodableError` for a text the template does not read."""
    top = codec.largest_value
    zero = codec.alphabet[0]
    plain = _runs(codec, text, codec.delimiter_run, zero + codec.delimiter)
    plain = _runs(codec, plain, codec.zero_run, zero)
    values: dict[str, int] = {}
    at = 0
    seen: dict[str, int] = {}
    for segment in _expand(codec):
        if segment == DELIMITER:
            if plain[at : at + 1] != codec.delimiter:
                raise UncodableError(f"no delimiter at {at}")
            at += 1
            continue
        if _in_group(codec, segment):
            seen[segment] = seen.get(segment, 0) + 1
            name = f"{segment}#{seen[segment]}"
        else:
            name = segment
        if _kind(codec, segment) == "key":
            if at == len(plain) and codec.trim_trailing_zeros:
                values[name] = 0  # a trailing 0 the encoder left out
                continue
            index = codec.alphabet.find(plain[at : at + 1])
            if not 0 <= index <= top:
                raise UncodableError(f"no value at {at}")
            values[name] = index
            at += 1
            continue
        end = plain.find(codec.delimiter, at)
        digits = plain[at:end] if end >= 0 else ""
        if not digits:
            raise UncodableError(f"no long value at {at}")
        number = 0
        for char in digits:
            index = codec.alphabet.find(char)
            if not 0 <= index <= top:
                raise UncodableError(f"{char!r} at {at} is not a value")
            number = number * top + index
        values[name] = number
        at = end
    if at != len(plain):
        raise UncodableError(f"{len(plain) - at} characters past the template's end")
    return values


# ─── the builder ─────────────────────────────────────────────────────────────


def _version(text: str | None) -> tuple[int, ...] | None:
    if text is None or not _VERSION.fullmatch(text.strip()):
        return None
    return tuple(int(p) for p in text.strip().split("."))


def room_for(sites: Sites, version: str | None) -> str | None:
    """The room label for a client version, or None when no band covers it."""
    parsed = _version(version)
    if parsed is None:
        return None
    for band in sites.band:
        low, high = _version(band.from_), _version(band.below)
        assert low is not None and high is not None  # Sites checked both
        if low <= parsed < high:
            return band.room
    return None


def level_of(record: labaddon.CharDB) -> int | None:
    """The character's level as the record holds it (`talents.legacy`'s
    `player_level`, `UnitLevel("player")` at the save), or None."""
    talents = getattr(record, "talents", None)
    legacy = getattr(talents, "legacy", None)
    level = getattr(legacy, "player_level", None)
    return level if type(level) is int else None


def _items(slots: Sequence[gear.GearSlot]) -> list[str]:
    ordered = sorted(slots, key=lambda s: s.slot)
    return [str(s.item_id) for s in ordered if s.item_id is not None]


def _slot_words(s: gear.GearSlot) -> str:
    return f"slot {s.slot} {s.slot_name}" if s.slot_name else f"slot {s.slot}"


_PASTE = "The item ids are listed instead, in slot order, to paste into a dressing room yourself."


def _fallback(slots: Sequence[gear.GearSlot], why: str) -> DressingItems:
    return DressingItems(items=_items(slots), note=f"No dressing-room link: {why}. {_PASTE}")


def build(
    identity: labaddon.CharIdentity,
    slots: Sequence[gear.GearSlot],
    *,
    version: str | None,
    level: int | None,
    sites: Sites | None = None,
) -> DressingLink | DressingItems:
    """The dressing-room address for a character, or the item ids when there
    is none. Never raises.

    `identity` and `slots` are `CharGearReport`'s `identity` and `slots`;
    `version` is the client's version as discovery read it from
    `.build.info` (`Flavor.version`), which picks the room; `level` is the
    record's (`level_of`), and when it is None or out of bounds the room is
    given the site's default (`[site] default_level`, 100) and the note says
    so. The gender is `identity.sex` (0 or 1, `Enum.UnitSex`), which the room
    reads the same way. Each placed item passes its item id and its first
    bonus id; the room takes one bonus id per slot, so the note names any
    item that carries more [verify]. Customization is left at the room's
    defaults, specialization and enchants at 0. `sites` is the shipped file
    unless a test gives another."""
    if sites is None:
        try:
            sites = load_sites()
        except SitesError as exc:
            return _fallback(slots, f"the shipped {SITES_FILE} cannot be used ({exc})")
    room = room_for(sites, version)
    if room is None:
        known = "unknown" if version is None else version[:64]
        return _fallback(
            slots, f"no dressing room is listed in {SITES_FILE} for client version {known}"
        )
    limits = sites.limits
    for name in ("race_id", "sex", "class_id"):
        if getattr(identity, name) is None:
            reason = getattr(identity, f"{name}_absent")
            return _fallback(
                slots,
                f"the room needs the race, body type and class, and the record has no {name}: "
                f"{reason}",
            )
    race, sex, klass = identity.race_id, identity.sex, identity.class_id
    assert race is not None and sex is not None and klass is not None  # checked above
    if not 1 <= race <= limits.race_id:
        return _fallback(slots, f"race id {race} is not one the room can show")
    if sex not in (0, 1):
        return _fallback(slots, f"body type {sex} is not one the room can show")
    if klass < 1:
        return _fallback(slots, f"class id {klass} is not one the room can show")
    notes: list[str] = []
    if level is not None and 1 <= level <= limits.level:
        level_words = f"level {level} (the record's)"
    else:
        level_words = f"level {sites.site.default_level}, the room's default (the record has none)"
        if level is not None:
            level_words = (
                f"level {sites.site.default_level}, the room's default (the record's {level} "
                "is out of bounds)"
            )
        level = sites.site.default_level
    values: dict[str, int] = {
        "version": sites.codec.version,
        "race": race,
        "gender": sex,
        "class": klass,
        "level": level,
    }
    by_game = {s.game: s for s in sites.slot}
    placed: list[gear.GearSlot] = []
    left_out: list[str] = []
    seen_slots: set[int] = set()
    for s in sorted(slots, key=lambda s: s.slot):
        if s.slot in seen_slots:
            return _fallback(slots, f"slot {s.slot} is listed twice")
        seen_slots.add(s.slot)
        where = by_game.get(s.slot)
        if where is None:
            left_out.append(_slot_words(s))
            continue
        if s.item_id is None:
            notes.append(f"{_slot_words(s)} is left out: its link has no item id")
            continue
        if not 1 <= s.item_id <= limits.item_id:
            return _fallback(
                slots, f"{_slot_words(s)} has item id {s.item_id}, which the room cannot show"
            )
        if len(s.bonus_ids) > limits.bonus_ids_per_item:
            return _fallback(
                slots,
                f"{_slot_words(s)} carries {len(s.bonus_ids)} bonus ids, more than an item "
                f"carries ({limits.bonus_ids_per_item})",
            )
        if any(not 1 <= b <= limits.bonus_id for b in s.bonus_ids):
            return _fallback(slots, f"{_slot_words(s)} has a bonus id the room cannot take")
        values[f"item.{where.room}"] = s.item_id
        if s.bonus_ids:
            values[f"bonus.{where.room}"] = s.bonus_ids[0]
        if len(s.bonus_ids) > 1:
            notes.append(
                f"{_slot_words(s)} carries {len(s.bonus_ids)} bonus ids and only the first, "
                f"{s.bonus_ids[0]}, is passed (the room takes one per item [verify])"
            )
        placed.append(s)
    if not placed:
        return _fallback(
            slots,
            "no equipped item goes in a slot the room has (it has none for the neck, rings or "
            "trinkets)",
        )
    try:
        text = encode(sites.codec, values)
        back = decode(sites.codec, text)
    except UncodableError as exc:
        return _fallback(slots, f"the room's address cannot hold this character ({exc})")
    if not _HASH.fullmatch(text) or back != _meant(sites.codec, values):
        return _fallback(slots, "the room's address did not read back as built")
    url = f"{sites.site.origin}{sites.rooms[room]}#{text}"
    count = f"{len(placed)} equipped item" + ("" if len(placed) == 1 else "s")
    note = (
        f"Opens {sites.site.name}'s {room} dressing room in your browser when you click it; "
        f"wowlab itself sends nothing. The address holds race {race}, body type {sex}, class "
        f"{klass}, {level_words}, and the item ids of {count}, each with its bonus id when "
        "it has one."
    )
    if left_out:
        note += f" Left out, with no slot in the room: {', '.join(left_out)}."
    if notes:
        note += " " + "; ".join(n[0].upper() + n[1:] for n in notes) + "."
    note += (
        " The look is the room's default for the race (the record's customization is not "
        "passed). Whether the room draws this client's items is [verify]."
    )
    return DressingLink(url=url, site=sites.site.name, note=note)
