"""Identifier patterns, and redaction for text a player is asked to share (M13-09).

Two users, one set of patterns (docs/LAB_PLAN.md §15.2, ADR-0031):

- `scripts/lab_capture.py`, the fixture scrub tool, takes its identifier
  detectors from here: the email, BattleTag and GUID patterns, the identity
  CVar deny-list, and the spellings a realm name takes. It keeps its own
  pseudonyms and its own refusal rules; since M13-49 (ADR-0019 amendment,
  2026-10-17) those cover the account folder, BattleTags, email addresses
  and the owner's GUIDs, not character or realm names, and it uses the realm
  spellings only to tell the owner's own unit names in a combat log from
  other players'. It loads this file by path, so the
  scrub tool still runs on the standard library alone: **this module imports
  the standard library only** (a test holds it to that).
- `wowlab doctor --redact` builds a `Redactor` from the names found in the
  install and the player's home folder, and replaces each with a placeholder
  (`<CHARACTER>`, `<REALM>`, `<ACCOUNT>`, `<HOME>`, ...) so the output can be
  pasted into an issue. It still replaces character and realm names: it is
  for another player's report, and what they hide is their choice (M13-49
  leaves it unchanged).

A `Redactor` works on text that is already text: it never parses a file, and
it never writes one. A miss is a leak and an extra placeholder costs a little
readability, so every rule leans toward replacing too much.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Iterable

# ─── identifier patterns (shared with scripts/lab_capture.py) ────────────────

# CVars whose value identifies the account, a character, a community, or the
# person (operating systems name audio devices after their owner: "Chris's
# AirPods"). `portal` stays: it is a region, and parsers want a populated
# line. PROVISIONAL until the M10-03 capture confirms and extends it; the
# format reference (docs/LAB_FORMATS.md §5) points here.
IDENTITY_CVARS: tuple[str, ...] = (
    "accountName",
    "accountList",
    "lastCharacterGuid",
    # [verify] The five below are spelled from reviewer memory, not from a capture.
    "realmName",
    "lastSelectedClubId",
    "Sound_OutputDriverName",
    "Sound_VoiceChatInputDriverName",
    "Sound_VoiceChatOutputDriverName",
)

# Loose on purpose: a false refusal costs one candidate file, a miss is a leak.
# No top-level domain is required (`user@localhost`); non-ASCII is accepted.
# Each match is anchored at the start of its run (the lookbehind), so a long
# unbroken run of letters is walked once, not once per byte.
EMAIL_RE = re.compile(
    rb"(?<![A-Za-z0-9._%+\-\x80-\xff])[A-Za-z0-9._%+\-\x80-\xff]+@"
    rb"[A-Za-z0-9\-\x80-\xff]+(?:\.[A-Za-z0-9\-\x80-\xff]+)*"
)
# Name#1234: a name character, '#', four or more digits, whatever follows.
BATTLETAG_RE = re.compile(rb"[A-Za-z0-9\x80-\xff]#[0-9]{4,}")
GUID_RE = re.compile(rb"Player-[0-9]+-[0-9A-Fa-f]+", re.IGNORECASE)
# [verify] These three families are spelled from reviewer memory, not from a
# capture (M10-03). Any of them refuses the file.
ACCOUNT_GUID_RE = re.compile(rb"BNetAccount-[0-9]+-[0-9A-Fa-f]+", re.IGNORECASE)
GUILD_GUID_RE = re.compile(rb"Guild-[0-9]+-[0-9A-Fa-f]+", re.IGNORECASE)
CLUB_GUID_RE = re.compile(rb"ClubFinder-[0-9]+-[0-9A-Fa-f-]+", re.IGNORECASE)
# Combat log: a player GUID immediately followed by its quoted unit name.
GUID_NAME_RE = re.compile(rb'(Player-[0-9]+-[0-9A-Fa-f]+),"([^"\r\n]*)"', re.IGNORECASE)


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def slug(text: str) -> str:
    folded = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    return folded.lower().translate({ord(" "): "-", ord("'"): None, ord("("): None, ord(")"): None})


def strip(chars: str) -> Callable[[str], str]:
    table = {ord(c): None for c in chars}
    return lambda text: text.translate(table)


# Every spelling a realm might take: the folder's; without spaces, and without
# spaces and hyphens (what `Name-Realm` strings are reported to use) [verify];
# and the tool's own guesses, which are NOT client normalisations: apostrophe
# stripped, the web slug, the underscore variant, the Lua-escaped apostrophe.
# Each is a function, so the pseudonym's form is derived exactly the way the
# real name's was and the corpus keeps the relation between the spellings.
def split_camel(text: str, *, capitals: bool = True) -> str:
    """A folder spelling's likely spaced form: `StormRage` -> `Storm Rage`, `Area52` -> `Area 52`.

    A space goes before each capital that follows a lower-case letter (unless
    `capitals` is off) and before a digit run that follows a letter. `PvE2`
    gives `Pv E 2`, and with `capitals` off `PvE 2`: both forms are kept.
    """
    out: list[str] = []
    for index, char in enumerate(text):
        prev = text[index - 1] if index else ""
        if (capitals and prev.islower() and char.isupper()) or (prev.isalpha() and char.isdigit()):
            out.append(" ")
        out.append(char)
    return "".join(out)


REALM_TRANSFORMS: tuple[Callable[[str], str], ...] = (
    lambda text: text,
    strip(" "),
    strip(" -"),
    strip(" -'"),
    slug,
    lambda text: text.replace(" ", "_"),
    lambda text: text.replace("'", "\\'"),
    # A second name known only from a `<First>-<Second>` folder is written with
    # its spaces dropped; SavedVariables keys may still spell it spaced.
    split_camel,
    lambda text: split_camel(text, capitals=False),
)


def realm_forms(realm: str) -> list[str]:
    forms: list[str] = []
    for transform in REALM_TRANSFORMS:
        for normal in ("NFC", "NFD"):
            form = unicodedata.normalize(normal, transform(nfc(realm)))
            if form and form not in forms:
                forms.append(form)
    return forms


# ─── redaction for sharing (wowlab doctor --redact) ──────────────────────────

CHARACTER = "<CHARACTER>"
REALM = "<REALM>"
ACCOUNT = "<ACCOUNT>"
HOME = "<HOME>"
BATTLETAG = "<BATTLETAG>"
EMAIL = "<EMAIL>"
GUID = "<GUID>"
NAME = "<NAME>"  # a name the player asked to have replaced (`--redact-name`)
LABEL = "<LABEL>"  # free text a player or a command wrote, replaced whole
TEXT = "<TEXT>"  # any other text replaced whole
USER = "<USER>"  # the login name the process runs as
HOST = "<HOST>"  # the machine's network name
PLACEHOLDERS: tuple[str, ...] = (
    CHARACTER,
    REALM,
    ACCOUNT,
    HOME,
    BATTLETAG,
    EMAIL,
    GUID,
    NAME,
    LABEL,
    TEXT,
    USER,
    HOST,
)
_PLACEHOLDER_RE = re.compile("|".join(re.escape(p) for p in PLACEHOLDERS))

# The whole BattleTag, not just the byte before `#` that BATTLETAG_RE needs
# to detect one: every name character before it goes too.
_BATTLETAG_TOKEN_RE = re.compile(rb"(?<![A-Za-z0-9\x80-\xff])[A-Za-z0-9\x80-\xff]+#[0-9]{4,}")
_GUID_RES = (GUID_RE, ACCOUNT_GUID_RE, GUILD_GUID_RE, CLUB_GUID_RE)

# A home folder named by its shape rather than by this machine's home: macOS
# `/Users/<name>`, Linux `/home/<name>` (and `/var/home/<name>`), Windows
# `C:\Users\<name>` (and `\\?\C:\Users\...`, and XP's `Documents and
# Settings`), Wine's `drive_c/users/<name>`. A run of separators counts as
# one: `str(OSError)` names its file with `repr()`, which doubles every
# backslash (`'C:\\\\Users\\\\Sean'`). The name is one path component, spaces
# included, and an apostrophe followed by a letter (`O'Brien`), so the match
# runs to the next separator, quote or line end: a home folder that ends a
# sentence takes the rest of that line with it, which costs readability and
# leaks nothing.
_HOME_SHAPE_RE = re.compile(
    r"(?:[A-Za-z]:)?[\\/]+(?:Users|home|Documents and Settings)[\\/]+"
    r"(?:[^\\/\r\n\t\"'<>|]|'(?=\w))+",
    re.IGNORECASE,
)
# What may not follow a known home folder for it to be that folder rather than
# the start of a longer name (`/home/bob` is not the home of `/home/bobby`).
_HOME_END = r"(?![\w.\-~])"
# Shorter than this, a name is replaced only as a whole word: a two-letter
# character name inside "Install" is not that character.
EMBEDDED_MIN_CHARS = 5
_NAME_EDGE_BEFORE = r"(?<![^\W_])"  # not after a letter or digit
_NAME_EDGE_AFTER = r"(?![^\W_])"


def _clean(names: Iterable[str]) -> list[str]:
    return [n for n in (name.strip() for name in names) if n]


def home_forms(home: str) -> list[str]:
    """The spellings a home folder takes in text: as given, with either
    separator, and with backslashes doubled (as `repr()`, and so
    `str(OSError)`, writes them), without a trailing separator. Empty for a
    filesystem or drive root, which names nobody."""
    forms: list[str] = []
    backslashed = home.replace("/", "\\")
    for form in (home, home.replace("\\", "/"), backslashed, backslashed.replace("\\", "\\\\")):
        trimmed = form.rstrip("\\/")
        parts = [p for p in re.split(r"[\\/]", trimmed) if p and not re.fullmatch(r"[A-Za-z]:", p)]
        if parts and trimmed not in forms:
            forms.append(trimmed)
    return forms


class Redactor:
    """Replaces identifying strings in text with placeholders.

    In order: home folders (the known ones, then any by shape), email
    addresses, BattleTags, player, account, guild and community GUIDs, then
    the names given (account folders, characters, realms in every spelling
    of `realm_forms`, any other name, the login user and the host name),
    longest first, in any casing and either Unicode normal form. A name of
    `EMBEDDED_MIN_CHARS` or more is
    replaced inside longer words too; a shorter one only as a whole word.
    Text already turned into a placeholder is never matched again.
    """

    def __init__(
        self,
        *,
        accounts: Iterable[str] = (),
        characters: Iterable[str] = (),
        realms: Iterable[str] = (),
        names: Iterable[str] = (),
        users: Iterable[str] = (),
        hosts: Iterable[str] = (),
        homes: Iterable[str] = (),
    ) -> None:
        self._passes: list[Callable[[str], str]] = []
        home_list = sorted(
            {form for home in _clean(homes) for form in home_forms(home)}, key=len, reverse=True
        )
        if home_list:
            known = re.compile(
                "(?:" + "|".join(re.escape(h) for h in home_list) + ")" + _HOME_END, re.IGNORECASE
            )
            self._passes.append(lambda part: known.sub(HOME, part))
        self._passes.append(lambda part: _HOME_SHAPE_RE.sub(HOME, part))
        self._passes.append(_pattern_pass(EMAIL_RE, EMAIL))
        # Before the names: "<CHARACTER>#1234" would keep the number, which is the account.
        self._passes.append(_pattern_pass(_BATTLETAG_TOKEN_RE, BATTLETAG))
        self._passes.extend(_pattern_pass(guid, GUID) for guid in _GUID_RES)

        # A string that is two kinds of name keeps the first kind listed here.
        forms: dict[str, str] = {}
        for placeholder, values, forms_of in (
            (ACCOUNT, accounts, _plain_forms),
            (CHARACTER, characters, _plain_forms),
            (REALM, realms, realm_forms),
            (NAME, names, _plain_forms),
            (USER, users, _plain_forms),
            (HOST, hosts, _plain_forms),
        ):
            for value in _clean(values):
                for form in forms_of(value):
                    forms.setdefault(form, placeholder)
        if forms:
            longest_first = sorted(forms, key=len, reverse=True)
            each = [(re.compile(re.escape(f), re.IGNORECASE), forms[f]) for f in longest_first]
            pattern = re.compile("|".join(_name_pattern(f) for f in longest_first), re.IGNORECASE)

            def kind(match: re.Match[str]) -> str:
                found = match.group()
                return next((p for form, p in each if form.fullmatch(found)), NAME)

            self._passes.append(lambda part: pattern.sub(kind, part))

    def __call__(self, text: str) -> str:
        for replace in self._passes:
            text = _outside_placeholders(text, replace)
        return text


def _plain_forms(name: str) -> list[str]:
    forms: list[str] = []
    for normal in ("NFC", "NFD"):
        form = unicodedata.normalize(normal, name)
        if form not in forms:
            forms.append(form)
    return forms


def _name_pattern(form: str) -> str:
    escaped = re.escape(form)
    if len(form) >= EMBEDDED_MIN_CHARS:
        return escaped
    return _NAME_EDGE_BEFORE + escaped + _NAME_EDGE_AFTER


def _outside_placeholders(text: str, replace: Callable[[str], str]) -> str:
    """`replace` applied to every stretch of `text` between placeholders."""
    out: list[str] = []
    at = 0
    for match in _PLACEHOLDER_RE.finditer(text):
        out.append(replace(text[at : match.start()]))
        out.append(match.group())
        at = match.end()
    out.append(replace(text[at:]))
    return "".join(out)


def _pattern_pass(pattern: re.Pattern[bytes], placeholder: str) -> Callable[[str], str]:
    return lambda part: _bytes_sub(pattern, placeholder, part)


def _bytes_sub(pattern: re.Pattern[bytes], placeholder: str, text: str) -> str:
    """A byte pattern applied to text, through UTF-8. Lone surrogates (bytes
    that were not UTF-8 in a file name) survive the round trip."""
    data = text.encode("utf-8", "surrogatepass")
    if pattern.search(data) is None:
        return text
    return pattern.sub(placeholder.encode("ascii"), data).decode("utf-8", "surrogatepass")
