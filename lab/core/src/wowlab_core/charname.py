"""charname: a character's names in the forms wowlab reports (M21-02).

A Forever character has a player-chosen first and second name and no realm
(docs/GLOSSARY.md, "Second name"). wowlab meets the names in two forms:

- **The folder form**, `<First>-<Second>`: the character folder under a
  digits-only folder (`WTF/Account/<ACCOUNT>/<digits>/<First>-<Second>/`).
  The hyphen is a separator, not part of either name; the folder is split
  at its first hyphen (character names are believed not to contain one,
  not yet confirmed), as `layout` splits it. A folder of the retail shape
  (`<Realm>/<Name>/`), or one with no hyphen, is the first name alone.
- **The in-game form**, `<First> <Second>`: the two names joined by one
  space, as addon data spells a Forever character (Syndicator, DBM;
  docs/LAB_FORMATS.md, 2026-09-22). wowlab derives it from the folder.

A crew-book key is `Name-Realm` (docs/LAB_PLAN.md §20.1): its name part is
the in-game form as the client gave it for another player. Whether the
client gives a Forever player's second name there is not yet confirmed (the
combat log names the first name alone), so a key's name is split at its
first space and has no second name when it holds none.

Pure: no file is read.
"""

from __future__ import annotations


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
