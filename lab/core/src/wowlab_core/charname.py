"""charname: a character's names in the forms wowlab reports (M21-02).

A Forever character has a player-chosen first and second name. The armory
address names no realm (research, M21 preamble); the launch client's combat
log names one (`Roleplay PvP` for every player in the fixture). Whether a
region holds more than one, and whether a first and second name are unique
per region or per realm, is [verify] (docs/GLOSSARY.md, "Second name"). The
client reports the second name in the slot where retail reports the realm:
on the owner's launch client (US, 2026-11-23), `UnitName("target")` gave
the first and the second name as its two values, and
`GetPlayerInfoByGUID` gave the name `"<First> <Second>"` and an empty
realm (docs/DATA_SOURCES.md, breakage log). wowlab meets the names in
these forms:

- **The folder form**, `<First>-<Second>`: the character folder under a
  digits-only folder (`WTF/Account/<ACCOUNT>/<digits>/<First>-<Second>/`).
  The hyphen is a separator, not part of either name; the folder is split
  at its first hyphen (character names are believed not to contain one,
  not yet confirmed), as `layout` splits it. A folder of the retail shape
  (`<Realm>/<Name>/`), or one with no hyphen, is the first name alone.
- **The in-game form**, `<First> <Second>`: the two names joined by one
  space, as addon data and `GetPlayerInfoByGUID` spell a Forever
  character. wowlab derives it from the folder or the key.
- **The crew-book key**, `Name-Realm` as the lab-addon builds it from
  `UnitName`'s two values (docs/LAB_PLAN.md §20.1). On a flavor whose
  characters have second names (a digits-only folder holds `<First>-<Second>`
  folders, as `layout` reads the account), the key is `<First>-<Second>`:
  its "realm" part is the second name. On any other flavor it is the
  retail `<Name>-<Realm>`. A name part that already holds a space is split
  there instead, its other part then kept as the realm.

Pure: no file is read.
"""

from __future__ import annotations

from typing import NamedTuple


class KeyNames(NamedTuple):
    """A crew key's names: first and second name, the in-game form, and the
    realm (None when the key's second part is the second name)."""

    first_name: str
    second_name: str | None
    game_name: str
    realm: str | None


def is_numeric_folder(realm_folder: str) -> bool:
    """Whether a character's parent folder is the digits-only folder of the
    Forever shape (layout's `numeric_folder`)."""
    return realm_folder.isascii() and realm_folder.isdigit()


def split_folder(character_folder: str, *, numeric: bool) -> tuple[str, str | None]:
    """(first name, second name) of a character folder: split at the first
    hyphen when it is under a digits-only folder and has one; else the
    folder name alone, with no second name."""
    first, sep, second = character_folder.partition("-")
    if numeric and sep:
        return first, second
    return character_folder, None


def game_name(first: str, second: str | None) -> str:
    """The in-game form: the first name, then one space and the second name
    when there is one."""
    return first if second is None else f"{first} {second}"


def split_game_name(name: str) -> tuple[str, str | None]:
    """(first name, second name) of a name in the in-game form: split at the
    first space; no second name when it holds none or one side is empty."""
    first, sep, second = name.partition(" ")
    if sep and first and second:
        return first, second
    return name, None


def key_names(name: str, other: str, *, second_names: bool) -> KeyNames:
    """The names in a crew key's two parts (`name`, before the first hyphen,
    and `other`, after it). With `second_names` (the flavor's characters
    have second names), `other` is the second name and there is no realm;
    else it is the realm. A `name` that already holds a space is the
    in-game form: split there, `other` kept as the realm."""
    first, second = split_game_name(name)
    if second is not None:
        return KeyNames(first, second, game_name(first, second), other or None)
    if second_names and other:
        return KeyNames(name, other, game_name(name, other), None)
    return KeyNames(name, None, name, other or None)
