#!/usr/bin/env python3
"""Capture and scrub real install files into the fixture staging area (M10-02).

    uv run python scripts/lab_capture.py --root <install> \
        --out lab/core/tests/fixtures/incoming [--dry-run]

Copies the capture set of `docs/handoffs/M10-03.md` out of a World of Warcraft
install and scrubs identity from it per `docs/LAB_PLAN.md` §8:

- account folder, realm and character names become stable pseudonyms, in
  paths and in file contents (same input, same pseudonym, whole capture set).
  Both account layouts are read: `<account>/<Realm>/<Name>/`, and the Forever
  beta's `<account>/<digits>/<Name>-<Realm>/`, where the digits-only folder is
  a grouping level that is kept as it is and never used as a name;
- identity CVars have their value blanked;
- the owner's own `Player-<n>-<hex>` GUIDs become pseudonym GUIDs;
- a file whose scrubbed bytes or output path still contain an email address,
  a BattleTag, an unmapped player, account, guild or community GUID, a
  surviving identity string in any casing or embedding (CVar names included),
  an unblanked identity CVar, or someone else's name joined to an own realm
  is refused: nothing is written for it and the exit status is non-zero.

How it edits: byte-level, targeted replacement only. Every edit is an
(offset, old bytes, new bytes) triple against the original file; every byte
outside an edit is copied through untouched. Nothing here parses a format and
re-serializes it: a fixture produced by the parser under test would prove
nothing about that parser.

What the detectors do not look for, because the client's SavedVariables
writer does not emit them: Lua numeric escapes (`\\64` for `@`), URL escapes
(`%40`), spelled-out forms (`(at)`), and strings assembled by concatenation.

Invariants: the install is opened read-only and nothing is created inside it
(L1); the only writes are regular files under `--out`, and nothing is ever
deleted. Flavor folders, product codes and versions are discovered from
`.flavor.info` and `.build.info`, never named here (L6). Standard library only.
"""

from __future__ import annotations

import argparse
import bisect
import re
import sys
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

# ─── deny-lists and detectors ────────────────────────────────────────────────

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
# CVar names worth a look by eye when they are not on the list above. Only
# names are ever printed, never values.
REVIEW_CVAR_RE = re.compile(
    r"account|character|guid|realmname|drivername|club|guild|bnet|friend", re.IGNORECASE
)

# An identity string equal to one of these would rewrite format keywords.
RESERVED_WORDS = frozenset({"set", "bind", "end", "ver", "true", "false", "nil", "player"})
# Words the client writes itself as vocabulary (chat-cache.txt channel and
# message types). An `--extra-name` equal to one of them, in any casing, is
# refused: `--extra-name GUILD` would rewrite the client's GUILD token.
CLIENT_VOCABULARY = RESERVED_WORDS | frozenset(
    word.casefold()
    for word in [
        "SAY",
        "PARTY",
        "PARTY_LEADER",
        "RAID",
        "RAID_LEADER",
        "RAID_WARNING",
        "GUILD",
        "OFFICER",
        "WHISPER",
        "WHISPER_INFORM",
        "YELL",
        "EMOTE",
        "TEXT_EMOTE",
        "CHANNEL",
        "SYSTEM",
        "LOOT",
        "MONEY",
        "INSTANCE_CHAT",
        "INSTANCE_CHAT_LEADER",
        "BN_WHISPER",
        "ACHIEVEMENT",
        "GUILD_ACHIEVEMENT",
        "COMBAT_XP_GAIN",
        "COMBAT_HONOR_GAIN",
        "COMBAT_FACTION_CHANGE",
        "SKILL",
        "BG_SYSTEM_NEUTRAL",
        "OPENING",
        "TRADESKILLS",
        "PET_INFO",
        "COMBAT_MISC_INFO",
    ]
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
# Words that stand next to a realm without being a player: AceDB `factionrealm`
# keys ("Horde - <realm>"), region tags ("<realm>-US"), DataStore's literal
# account key ("Default.<realm>.<name>").
FACTION_WORDS = frozenset({"horde", "alliance", "neutral"})
VOCABULARY_PARTNERS = FACTION_WORDS | {"us", "eu", "kr", "tw", "cn", "default"}
_LETTERS = rb"A-Za-z\x80-\xff"
# What joins a name to a realm: "Name-Realm", "Name - Realm", DataStore's
# "Default.Realm.Name", and the other single-character joints addons use. Any
# run of blanks around the joint character counts ("Horde  - Realm" is still
# the same key), line breaks included ("Jaina -\nRealm"), so neither a stray
# space nor a wrapped line turns a refusal into a note. A `/` right after a
# line break starts a macro's slash command and is not a joint. En and em
# dashes are joints too: the detectors read a copy of the bytes in which each
# is replaced by " - " (the same three bytes long, so every offset holds).
# Bare blanks, commas and words ("Jaina Area52", "jaina of area52") are not
# joints; those neighbours get a note instead (see Identity.inspect).
_JOINT = rb"(?:[ \t\r\n]*(?:[-.|:_]|(?<![\r\n])/)[ \t\r\n]*)"
_DASHES = (b"\xe2\x80\x93", b"\xe2\x80\x94")  # U+2013, U+2014 in UTF-8
_UNIT_TOKENS = (
    "player|target|focus|mouseover|cursor|pet|none|vehicle|npc|softenemy|softfriend|"
    "softinteract|party|raid|arena|boss|nameplate"
)
# Slash commands that take a player's name, `@Name` / `target=Name` unit
# references that are not unit tokens (blanks allowed: `[@ Name]`,
# `[target = Name]`), and a `/run` or `/script` line that calls an API taking
# a name. A macro line starts at a line break, or after a literal `\n` escape
# inside a Lua string (a macro body stored in SavedVariables).
_MACRO_LINE = rb"(?:\A|(?<=[\r\n])|(?<=\\n))[ \t]*/"
SOCIAL_MACRO_RE = re.compile(
    _MACRO_LINE + rb"(?:w|whisper|who|tell|t|invite|inv|ginvite|tar|targetexact"
    rb"|target|friend|ignore|focus|assist|follow|pr|cw"
    # [verify] The commands below are from reviewer memory, not from a capture.
    rb"|guildinvite|promote|gpromote|gdemote|gkick|guildremove|uninvite|kick|unignore"
    rb"|removefriend|inspect|duel|fol)(?=[ \t])"
    rb"|" + _MACRO_LINE + rb"(?:run|script)[ \t][^\r\n]*?"
    rb"(?:SendChatMessage|InviteUnit|TargetUnit|GuildInvite|AddFriend|BNSendWhisper)"
    rb"|(?:@[ \t]*|target[ \t]*=[ \t]*)(?!(?:%s)(?:target|pet)*[0-9]*(?![%s]))[%s]+"
    % (_UNIT_TOKENS.encode(), _LETTERS, _LETTERS),
    re.IGNORECASE,
)
NAME_REALM_SHAPE_RE = re.compile(
    rb'"([A-Za-z\x80-\xff]{2,24})(?: - |-)([A-Za-z\x80-\xff][^"\r\n]{1,40})"'
)

_WORD = rb"A-Za-z0-9\x80-\xff"
_WORD_BYTES = frozenset(
    b"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
) | frozenset(range(0x80, 0x100))
_LINE_START = rb"(?:\A(?:\xef\xbb\xbf)?|(?<=[\r\n]))[ \t]*"
# Spans the owner does not author and the scrubber therefore never rewrites.
# Only text that is SHAPED like vocabulary qualifies; `## Notes for <name>:`
# or `SET <name>_pos` with stray punctuation is ordinary text and is scrubbed.
#
# - The CVar name of a SET line, when it is an identifier. The set of CVars is
#   open, so identity strings are still hunted inside it (whole words, and
#   anything of EMBEDDED_MIN_CHARS or more): a hit refuses the file.
# - A TOC directive key, when it is one the client defines (the closed list of
#   docs/LAB_FORMATS.md §3, with an optional locale or game-type suffix). Such
#   a key is vocabulary whatever it spells, so it is exempt from the identity
#   hunt. Any other key, `X-` keys included, is ordinary text.
SET_NAME_RE = re.compile(
    _LINE_START + rb"SET[ \t]++([A-Za-z0-9_.]++)(?=[ \t\r\n]|\Z)", re.IGNORECASE
)
TOC_KEY_RE = re.compile(
    _LINE_START + rb"##[ \t]*+((?i:Interface|Title|Notes|Author|Version|SavedVariables"
    rb"|SavedVariablesPerCharacter|SavedVariablesMachine|Dependencies|RequiredDeps|Dep"
    rb"|OptionalDeps|LoadOnDemand|LoadWith|LoadManagers|DefaultState|IconTexture|IconAtlas"
    rb"|AddonCompartmentFunc|AddonCompartmentFuncOnEnter|AddonCompartmentFuncOnLeave"
    rb"|Category|Group|AllowLoad|AllowLoadGameType|OnlyBetaAndPTR"
    # [verify] The six below are spelled from reviewer memory, not yet from a
    # capture or docs/LAB_FORMATS.md §3 (M10-03 confirms).
    rb"|LoadSavedVariablesFirst|UseSecureEnvironment|AllowAddOnTableAccess|LoadFirst"
    rb"|OptionalDep|RequiredDep)"
    # The suffix is a closed list too, and case-sensitive: a free suffix would
    # be a place for a four-letter name to hide. [verify] `-BCC` is the legacy
    # suffix as recalled by the domain reviewer (`## Interface-BCC:`).
    rb"(?:-(?:enUS|enGB|deDE|esES|esMX|frFR|itIT|koKR|ptBR|ptPT|ruRU|zhCN|zhTW"
    rb"|Mainline|Classic|Vanilla|TBC|BCC|Wrath|Cata|Mists))?)[ \t]*+:"
)
# A lower- or upper-cased identity string this long is replaced even inside a
# longer word; shorter ones only as a whole word.
EMBEDDED_MIN_CHARS = 5

SAVED_VARIABLES_DIR = "SavedVariables"
ACCOUNT_FILES = ("SavedVariables.lua", "config-cache.wtf", "bindings-cache.wtf", "macros-cache.txt")
ACCOUNT_GLOBS = ("edit-mode-cache*",)
CHARACTER_FILES = (
    "config-cache.wtf",
    "bindings-cache.wtf",
    "macros-cache.txt",
    "AddOns.txt",
    "layout-local.txt",
    "chat-cache.txt",
)
# More per-account and per-character caches, found on the Forever beta by the
# M10-03 capture. `.old` twins (the previous write) are never captured.
ACCOUNT_CACHE_FILES = (
    "character-list-order.txt",
    "chat-frontend-cache.txt",
    "flagged-cache-account.txt",
    "tts-cache-account.txt",
)
CHARACTER_CACHE_FILES = (
    "click-bindings-cache.txt",
    "flagged-cache-character.txt",
    "tts-cache-character.txt",
    "edit-mode-cache-character.txt",
)
OLD_SUFFIX = ".old"
CONFIG_NAMES = frozenset({"config.wtf", "config-cache.wtf"})
# Blizzard's exported interface code is not a fixture (fixtures/README.md).
EXPORTED_ADDON_PREFIX = "blizzard_"


Span = tuple[int, int]
# A partner word is letters AND digits: "Jaina9" is a word, not an absent
# neighbour. A word that is only digits ("Realm-2", a profile copy) is nobody
# and is looked past.
#
# Backward searches run in a window of _WINDOW bytes and start only at a word
# boundary (a lookbehind sees past the window's start). A word or a joint that
# the window cuts is read as a person: the safe direction.
_WINDOW = 160
_BEYOND_AFTER = re.compile(_JOINT + rb"([%s]+)" % _WORD)
_BEYOND_BEFORE = re.compile(rb"(?<![%s])([%s]+)%s\Z" % (_WORD, _WORD, _JOINT))
_PAREN_BEFORE = re.compile(rb"(?<![%s])([%s]+) ?\(\Z" % (_WORD, _WORD))
_CUT_BEFORE = re.compile(rb"[%s]*%s" % (_WORD, _JOINT))  # fullmatch over a whole window
_JOINT_BYTES = frozenset(bytes([b]) for b in b"-.|:/_")  # one-byte slices
_NOT_SEPARATOR = _WORD_BYTES | frozenset(b"\r\n")
# The nearest word across a short separator that is not a joint (" ", ", ",
# " of ", "~", "'s "), in any casing: up to 4 bytes that are neither word
# bytes nor line breaks.
_NEAR_WINDOW = 64
_NEAR_BEFORE = re.compile(rb"(?<![%s])([%s]+)[^%s\r\n]{1,4}\Z" % (_WORD, _WORD, _WORD))
_NEAR_CUT = re.compile(rb"[%s]*[^%s\r\n]{1,4}" % (_WORD, _WORD))  # fullmatch over a whole window
_NEAR_AFTER = re.compile(rb"[^%s\r\n]{1,4}([%s]+)" % (_WORD, _WORD))
# Nothing but blanks and joint characters (fullmatch; dashes already replaced).
_JOINT_ONLY = re.compile(rb"[ \t\-.|:/_]*")
# A word that starts with a capital (or a non-ASCII byte, which may be one),
# at a word boundary, for the same-line note.
_CAPITALISED_RE = re.compile(rb"(?<![%s])[A-Z\x80-\xff][%s]*" % (_WORD, _WORD))
_LINE_BREAKS_RE = re.compile(rb"\r\n|\r|\n")


class _Lines:
    """Where each line of a byte string starts and ends. CRLF, CR and LF each end one."""

    def __init__(self, data: bytes) -> None:
        self._size = len(data)
        self._breaks = [m.span() for m in _LINE_BREAKS_RE.finditer(data)]
        self._ends = [end for _, end in self._breaks]

    @property
    def count(self) -> int:
        return len(self._breaks) + 1

    def index(self, position: int) -> int:
        return bisect.bisect_right(self._ends, position)

    def span(self, index: int) -> Span:
        start = self._breaks[index - 1][1] if index else 0
        end = self._breaks[index][0] if index < len(self._breaks) else self._size
        return start, end


def _fold(word: str) -> str:
    """Comparison key blind to case and normal form. On str: bytes fold ASCII only."""
    return unicodedata.normalize("NFC", word).casefold()


class CaptureError(Exception):
    """A problem with the arguments or the install that stops the whole run."""


# ─── identity map ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Token:
    """One byte string to replace, and how it must be delimited to count."""

    text: bytes
    replacement: bytes
    mode: str  # "substring" | "word" | "number"


@dataclass(frozen=True)
class Edit:
    """One targeted replacement, in offsets of the original bytes."""

    offset: int
    old: bytes
    new: bytes
    reason: str  # "cvar" | "guid" | "identity"
    embedded: bool = False  # a word character touched it: review by eye

    @property
    def end(self) -> int:
        return self.offset + len(self.old)


@dataclass(frozen=True)
class ScrubResult:
    data: bytes
    edits: tuple[Edit, ...]
    problems: tuple[str, ...]  # non-empty means refuse
    notes: tuple[str, ...] = ()  # non-fatal, for the owner's eye

    def count(self, reason: str) -> int:
        return sum(1 for e in self.edits if e.reason == reason)

    @property
    def embedded(self) -> int:
        return sum(1 for e in self.edits if e.embedded)


def _letters(index: int) -> str:
    """0 -> a, 25 -> z, 26 -> ba: an unbounded, letters-only suffix."""
    out = ""
    n = index + 1
    while n:
        n, rem = divmod(n - 1, 26)
        out = chr(ord("a") + rem) + out
    return out


def _end_assertion(text: bytes, mode: str) -> bytes:
    if mode == "substring":
        return b""
    boundary = _WORD + (rb"." if mode == "number" else b"")
    return b"(?<![" + boundary + b"]" + re.escape(text) + b")(?![" + boundary + b"])"


def _trie_regex(tokens: dict[bytes, str]) -> re.Pattern[bytes] | None:
    """One pattern for many literals: shared prefixes, longest match first.

    A flat alternation of several hundred names is tried branch by branch at
    every byte of an 8 MiB file. A prefix tree lets the engine reject a
    position on its first byte, and puts each token's delimiter rule at the
    point where that token ends.
    """
    if not tokens:
        return None
    root = _TrieNode()
    for text, mode in tokens.items():
        node = root
        for byte in text:
            node = node.next.setdefault(byte, _TrieNode())
        node.end = _end_assertion(text, mode)

    def emit(node: _TrieNode) -> bytes:
        branches = [re.escape(bytes([key])) + emit(node.next[key]) for key in sorted(node.next)]
        if node.end is not None:
            branches.append(node.end)  # last, so a longer token is tried first
        return branches[0] if len(branches) == 1 else b"(?:" + b"|".join(branches) + b")"

    return re.compile(emit(root))


@dataclass
class _TrieNode:
    next: dict[int, _TrieNode] = field(default_factory=dict)
    end: bytes | None = None  # the delimiter rule of the token that ends here


def _nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def _slug(text: str) -> str:
    folded = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    return folded.lower().translate({ord(" "): "-", ord("'"): None, ord("("): None, ord(")"): None})


def _strip(chars: str) -> Callable[[str], str]:
    table = {ord(c): None for c in chars}
    return lambda text: text.translate(table)


# Every spelling a realm might take: the folder's; without spaces, and without
# spaces and hyphens (what `Name-Realm` strings are reported to use) [verify];
# and the tool's own guesses, which are NOT client normalisations: apostrophe
# stripped, the web slug, the underscore variant, the Lua-escaped apostrophe.
# Each is a function, so the pseudonym's form is derived exactly the way the
# real name's was and the corpus keeps the relation between the spellings.
REALM_TRANSFORMS: tuple[Callable[[str], str], ...] = (
    lambda text: text,
    _strip(" "),
    _strip(" -"),
    _strip(" -'"),
    _slug,
    lambda text: text.replace(" ", "_"),
    lambda text: text.replace("'", "\\'"),
)
PLAIN_TRANSFORMS: tuple[Callable[[str], str], ...] = (lambda text: text,)


def _realm_forms(realm: str) -> list[str]:
    forms: list[str] = []
    for transform in REALM_TRANSFORMS:
        for normal in ("NFC", "NFD"):
            form = unicodedata.normalize(normal, transform(_nfc(realm)))
            if form and form not in forms:
                forms.append(form)
    return forms


def _shaped(real: str, stem: str, index: int) -> str:
    """A pseudonym that keeps the real name's separators. Always ASCII.

    This reveals the run of spaces, hyphens and apostrophes in the real name
    (so: its word count). Nothing else. A non-ASCII name gets an ASCII
    pseudonym on purpose: the detectors work on bytes, where case folding is
    ASCII-only, so a pseudonym must never need non-ASCII folding to be
    recognised. The corpus gets its non-ASCII coverage from the "non-ASCII
    strings" SavedVariables pick, whose note is recomputed after the scrub.
    """
    real = _nfc(real)
    upper = real.isupper()
    out: list[str] = []
    words = 0
    for position, run in enumerate(re.split(r"([ \-']+)", real)):
        if position % 2:
            out.append(run)
        elif run:
            word = stem + _letters(index) if words == 0 else "Part" + _letters(words)
            out.append(word.upper() if upper else word)
            words += 1
    return "".join(out)


class Identity:
    """Real identity strings, their pseudonyms, and the scrub built from them.

    Pseudonyms are assigned in sorted order of the real names over the whole
    install, so one run maps a name the same way in every file and every path,
    and a dry run followed by a real run agrees with itself.
    """

    def __init__(
        self,
        *,
        accounts: Iterable[str] = (),
        realms: Iterable[str] = (),
        characters: Iterable[str] = (),
        extras: Iterable[str] = (),
        guids: Iterable[bytes] = (),
        cvars: Iterable[str] = IDENTITY_CVARS,
        show_names: bool = False,
    ) -> None:
        self.names: dict[str, str] = {}
        self._show_names = show_names
        self._tokens: dict[bytes, Token] = {}
        self._survivors: dict[bytes, str] = {}  # ASCII-lowered long form -> mode
        self._short_survivors: dict[bytes, str] = {}  # ASCII-lowered short form -> "substring"
        self._realm_pseudonyms: set[bytes] = set()
        self._partner_words: set[str] = set()  # every letter run of every pseudonym, casefolded
        self.counts: Counter[str] = Counter()

        for i, realm in enumerate(self._ordered(realms)):
            self._add_name(realm, _shaped(realm, "Labrealm", i), "realm")
        for i, character in enumerate(self._ordered(characters)):
            self._add_name(character, _shaped(character, "Labchar", i), "character")
        for i, extra in enumerate(self._ordered(extras)):
            self._add_name(extra, _shaped(extra, "Labname", i), "extra name")
        for i, account in enumerate(self._ordered(accounts)):
            self._add_account(account, i)

        canonical = sorted({guid.upper() for guid in guids})
        self.guids: dict[bytes, bytes] = {
            guid: b"Player-9999-%08X" % (i + 1) for i, guid in enumerate(canonical)
        }
        self._pseudo_guids = frozenset(self.guids.values())

        self.cvars = tuple(dict.fromkeys(cvars))
        names = b"|".join(re.escape(c.encode()) for c in self.cvars)
        self._cvar_value = re.compile(
            _LINE_START + rb'SET[ \t]+(?:%s)[ \t]+"([^"\r\n]*)"' % names, re.IGNORECASE
        )
        # Anchor-free: wherever `SET <identity cvar>` stands, it must be blank.
        self._cvar_unblanked = re.compile(
            rb'SET[ \t]+(?:%s)(?![A-Za-z0-9_])(?![ \t]+""[ \t]*(?:[\r\n]|\Z))' % names,
            re.IGNORECASE,
        )

        self._identity = _trie_regex({t.text: t.mode for t in self._tokens.values()})
        self._survivor = _trie_regex(self._survivors)
        self._short_survivor = _trie_regex(self._short_survivors)
        # Every own-realm pseudonym in the output is found with one prefix-tree
        # pattern (its three casings are separate literals), and what stands on
        # either side of it is then read in a bounded window. No pattern here
        # walks a long run of letters more than once.
        self._own_realm = _trie_regex(dict.fromkeys(self._realm_pseudonyms, "substring"))

    @staticmethod
    def _ordered(names: Iterable[str]) -> list[str]:
        return sorted({_nfc(n) for n in names}, key=lambda n: (n.casefold(), n))

    def _check(self, real: str, category: str) -> None:
        shown = repr(real) if self._show_names else f"a {category} name ({len(real)} characters)"
        if len(real) < 2:
            raise CaptureError(f"{shown} is too short to replace safely")
        if real.casefold() in RESERVED_WORDS:
            raise CaptureError(f"{shown} is a format keyword; replacing it would corrupt files")
        if category in ("realm", "character") and not any(c.isalpha() for c in real):
            # Replaced as a substring, a name like "70" rewrites 170, -270.0 and 1703.
            raise CaptureError(
                f"{shown} has no letters, so it cannot be told apart from an ordinary number; "
                "nothing is captured"
            )
        if category == "extra name" and real.casefold() in CLIENT_VOCABULARY:
            value = repr(real) if self._show_names else f"a value ({len(real)} characters)"
            raise CaptureError(
                f"--extra-name: {value} is a word the client writes itself (a chat type or "
                "channel token); replacing it would corrupt files"
            )

    def _token(self, text: str, replacement: str, mode: str) -> None:
        raw = text.encode("utf-8")
        if len(text) >= 2:
            self._tokens.setdefault(raw, Token(raw, replacement.encode("utf-8"), mode))

    def _survivor_form(self, text: str, mode: str | None = None) -> None:
        if len(text) < 2:
            return
        lowered = text.encode("utf-8").lower()
        if mode is None and len(text) < EMBEDDED_MIN_CHARS:
            # Found anywhere; whether a hit refuses depends on what it touches.
            self._short_survivors[lowered] = "substring"
        elif self._survivors.get(lowered) != "substring":
            self._survivors[lowered] = mode or "substring"

    def _add_name(self, real: str, pseudonym: str, category: str) -> None:
        if real in self.names:
            return  # e.g. a character named like a realm: the first category wins
        self._check(real, category)
        self.names[real] = pseudonym
        self.counts[category] += 1
        transforms = REALM_TRANSFORMS if category == "realm" else PLAIN_TRANSFORMS
        pairs: list[tuple[str, str]] = []
        for transform in transforms:
            for normal in ("NFC", "NFD"):  # a folder listed in NFD, contents written in NFC
                pair = (
                    unicodedata.normalize(normal, transform(real)),
                    unicodedata.normalize(normal, transform(pseudonym)),
                )
                if pair not in pairs:
                    pairs.append(pair)
        # Exact spelling first: replaced wherever it occurs, even inside a
        # longer word. Over-replacing is ugly; under-replacing is a leak.
        for form, replacement in pairs:
            self._token(form, replacement, "substring")
        # Other casings (addons lower-case keys). Short ones only as a whole
        # word, or `true`/`SET` could be hit through a name's case variant.
        for form, replacement in pairs:
            mode = "substring" if len(form) >= EMBEDDED_MIN_CHARS else "word"
            self._token(form.lower(), replacement.lower(), mode)
            self._token(form.upper(), replacement.upper(), mode)
            for cased in (form, form.lower(), form.upper(), form.title(), form.casefold()):
                self._survivor_form(cased)
        for _form, replacement in pairs:
            for cased in (replacement, replacement.lower(), replacement.upper()):
                if category == "realm":
                    # All three casings: bytes-level IGNORECASE folds ASCII only.
                    self._realm_pseudonyms.add(cased.encode("utf-8"))
                self._partner_words.update(_fold(run) for run in re.findall(r"[^\W\d_]+", cased))

    def _add_account(self, real: str, index: int) -> None:
        if real in self.names:
            return
        numbered = re.fullmatch(r"([0-9]+)#([0-9]+)", real)
        if not numbered:
            self._add_name(real, _shaped(real, "LABACCOUNT", index), "account")
            return
        number = str(90000001 + index)
        self.names[real] = f"{number}#{numbered.group(2)}"
        self.counts["account"] += 1
        self._token(real, self.names[real], "substring")
        self._survivor_form(real, "substring")
        if len(numbered.group(1)) >= 5:
            # The bare account number, but never inside a longer number.
            self._token(numbered.group(1), number, "number")
            self._survivor_form(numbered.group(1), "number")

    # ── scrubbing ──

    @staticmethod
    def _vocabulary(data: bytes, *, config: bool, toc: bool) -> tuple[list[Span], list[Span]]:
        """(CVar-name spans, client-defined TOC key spans): never edited. See SET_NAME_RE."""
        cvar_names = sorted(m.span(1) for m in SET_NAME_RE.finditer(data)) if config else []
        toc_keys = sorted(m.span(1) for m in TOC_KEY_RE.finditer(data)) if toc else []
        return cvar_names, toc_keys

    def scrub(self, data: bytes, *, blank_cvars: bool = False, toc: bool = False) -> ScrubResult:
        """Return `data` with identity replaced, plus every edit and any reason to refuse.

        `blank_cvars` marks a Config.wtf-style file, `toc` a TOC file.
        """
        # Three passes over the ORIGINAL bytes, highest priority first. Matches
        # within a pass never overlap each other; a match that overlaps a
        # vocabulary span or an edit from an earlier pass is dropped.
        cvar_names, toc_keys = self._vocabulary(data, config=blank_cvars, toc=toc)
        vocabulary = sorted([*cvar_names, *toc_keys])
        edits: list[Edit] = []
        owned: list[Span] = list(vocabulary)

        def claim(start: int, end: int, new: bytes, reason: str) -> None:
            if start == end or data[start:end] == new:
                return
            if _overlaps(owned, start, end):
                return
            touching = (start > 0 and data[start - 1] in _WORD_BYTES) or (
                end < len(data) and data[end] in _WORD_BYTES
            )
            edits.append(
                Edit(start, data[start:end], new, reason, reason == "identity" and touching)
            )

        def close_pass() -> None:
            owned[:] = sorted([*vocabulary, *((e.offset, e.end) for e in edits)])

        if blank_cvars:
            for m in self._cvar_value.finditer(data):
                claim(m.start(1), m.end(1), b"", "cvar")
            close_pass()
        for m in GUID_RE.finditer(data):
            if m.group(0).upper() in self.guids:
                claim(m.start(), m.end(), self.guids[m.group(0).upper()], "guid")
        close_pass()
        if self._identity is not None:
            for m in self._identity.finditer(data):
                claim(m.start(), m.end(), self._tokens[m.group(0)].replacement, "identity")

        edits.sort(key=lambda e: e.offset)
        out = bytearray()
        written: list[Span] = []  # where each replacement landed, in output offsets
        cursor = 0
        for edit in edits:
            out += data[cursor : edit.offset]
            written.append((len(out), len(out) + len(edit.new)))
            out += edit.new
            cursor = edit.end
        out += data[cursor:]
        scrubbed = bytes(out)
        problems, notes = self.inspect(scrubbed, config=blank_cvars, toc=toc, written=written)

        # A short exact-case name replaced inside a longer name in another
        # casing ("Al" in "xtHrAlLx") splits it before the scan above can see
        # it. So the long forms are also hunted in the ORIGINAL bytes: every
        # hit there must lie inside one edit (it was replaced whole) or inside
        # a client-defined TOC key.
        if self._survivor is not None:
            replaced = [(e.offset, e.end) for e in edits]
            split = [
                m
                for m in self._survivor.finditer(data.lower())
                if not _inside(replaced, m.start(), m.end())
                and not _overlaps(toc_keys, m.start(), m.end())
            ]
            if split:
                problems.append(_located("identity string not replaced whole", data, split))
        return ScrubResult(scrubbed, tuple(edits), tuple(problems), tuple(notes))

    def inspect(
        self,
        scrubbed: bytes,
        *,
        config: bool = False,
        toc: bool = False,
        written: Sequence[Span] = (),
    ) -> tuple[list[str], list[str]]:
        """(reasons these bytes must not be emitted, notes for the eye). Never quotes a match.

        `written` is where this scrub put its replacements, if it made any.
        """
        problems: list[str] = []
        notes: list[str] = []
        cvar_names, toc_keys = self._vocabulary(scrubbed, config=config, toc=toc)
        pseudonyms = sorted(written)

        def report(into: list[str], label: str, matches: Iterable[re.Match[bytes]]) -> None:
            hits = list(matches)
            if hits:
                into.append(_located(label, scrubbed, hits))

        def outside(
            spans: list[Span], matches: Iterable[re.Match[bytes]]
        ) -> Iterator[re.Match[bytes]]:
            return (m for m in matches if not _overlaps(spans, m.start(), m.end()))

        report(problems, "email address", EMAIL_RE.finditer(scrubbed))
        report(problems, "BattleTag", BATTLETAG_RE.finditer(scrubbed))
        report(
            problems,
            "unmapped player GUID",
            (m for m in GUID_RE.finditer(scrubbed) if m.group(0) not in self._pseudo_guids),
        )
        report(problems, "account GUID", ACCOUNT_GUID_RE.finditer(scrubbed))
        report(problems, "guild GUID", GUILD_GUID_RE.finditer(scrubbed))
        report(problems, "community GUID", CLUB_GUID_RE.finditer(scrubbed))
        report(problems, "unblanked identity CVar", self._cvar_unblanked.finditer(scrubbed))

        lowered = scrubbed.lower()  # folds ASCII only and keeps every offset
        if self._identity is not None:
            # Exact spellings. Exempt in both vocabulary spans: a CVar called
            # AlwaysCompareItems is not a character called Al. The scans below
            # still look inside CVar names.
            report(
                problems,
                "surviving identity string",
                outside(sorted([*cvar_names, *toc_keys]), self._identity.finditer(scrubbed)),
            )
        long_hits: list[Span] = []
        if self._survivor is not None:
            found = list(outside(toc_keys, self._survivor.finditer(lowered)))
            long_hits = [m.span() for m in found]
            report(problems, "surviving identity string (other casing or embedded)", found)
        if self._short_survivor is not None:
            # Names under EMBEDDED_MIN_CHARS characters, in any casing, anywhere.
            # Inside a pseudonym they are the pseudonym's own letters. As a whole
            # word, or glued to a pseudonym or to another identity hit, they
            # refuse. Anywhere else ("mara" in "marathon") they are counted.
            masked = bytearray(lowered)
            for start, end in pseudonyms:
                masked[start:end] = bytes(end - start)  # NUL: no name contains it
            hits = self._short_survivor.finditer(bytes(masked))
            short = list(outside(toc_keys, hits) if toc_keys else hits)
            # A file can hold a million loose hits of a two-letter name, so
            # "touches" is two set lookups, not a search.
            starts = {s for s, _ in pseudonyms} | {s for s, _ in long_hits}
            ends = {e for _, e in pseudonyms} | {e for _, e in long_hits}
            starts.update(m.start() for m in short)
            ends.update(m.end() for m in short)
            size = len(scrubbed)
            glued: list[re.Match[bytes]] = []
            loose: list[re.Match[bytes]] = []
            for m in short:
                begin, stop = m.span()
                whole_word = (begin == 0 or scrubbed[begin - 1] not in _WORD_BYTES) and (
                    stop == size or scrubbed[stop] not in _WORD_BYTES
                )
                (glued if whole_word or begin in ends or stop in starts else loose).append(m)
            report(problems, "surviving short identity string (whole word or glued)", glued)
            report(notes, "short identity string inside a longer word", loose)

        if self._own_realm is not None:
            # Who stands next to each own realm. Beyond any adjacency check, and
            # left to the owner's eye: a name and the realm in separate fields
            # of one record (`["name"] = "Jaina", ["realm"] = "<realm>"`).
            view = scrubbed
            for dash in _DASHES:
                view = view.replace(dash, b" - ")  # same length: offsets hold
            foreign: list[re.Match[bytes]] = []
            tolerated: list[re.Match[bytes]] = []
            unexplained: list[re.Match[bytes]] = []
            nearby: list[re.Match[bytes]] = []
            lines: _Lines | None = None
            strangers: dict[int, bool] = {}  # line index -> holds an unexplained capitalised word
            for m in self._own_realm.finditer(view):
                verdict = self._partner(view, m)
                if verdict == "foreign":
                    foreign.append(m)
                elif verdict == "vocabulary":
                    tolerated.append(m)
                # A name joined by something that is not a joint ("Jaina Area52",
                # "jaina, area52", "Jaina\nArea52") is not refused, it is noted:
                # a capitalised word nobody accounts for on the realm's line (or
                # on the next or previous line, when only blanks and joints stand
                # between the realm and that line break), else a foreign word of
                # any casing right next to it. At most one of the two per realm.
                lines = lines or _Lines(view)
                if self._capitalised_near(view, lines, strangers, m):
                    unexplained.append(m)
                elif self._stranger_adjacent(view, m):
                    nearby.append(m)
            report(problems, "someone else's name on an own realm", foreign)
            report(notes, "own realm next to a faction, region or 'Default' word", tolerated)
            report(notes, "own realm near an unexplained capitalised word", unexplained)
            report(notes, "own realm next to an unexplained lowercase word", nearby)
        report(
            notes,
            "Name-Realm-shaped string that is not a pseudonym pair",
            (m for m in NAME_REALM_SHAPE_RE.finditer(scrubbed) if not self._is_pair(m)),
        )
        report(
            notes, "whisper/invite/target macro line or @Name", SOCIAL_MACRO_RE.finditer(scrubbed)
        )
        return problems, notes

    def _partner(self, scrubbed: bytes, match: re.Match[bytes]) -> str:
        """Who stands next to this own-realm pseudonym: "own", "vocabulary", or "foreign".

        Looks at `<word><joint><realm>`, `<word> (<realm>)` and
        `<realm><joint><word>`, and keeps looking one segment further for as
        long as the word is vocabulary (a faction, a region, `Default`) or
        only digits: "Jaina - Horde - <realm>", "Jaina-US-<realm>",
        "Default.<realm>.Jaina" and "<realm>-US-Jaina" all end on Jaina. A word
        or a joint that the backward window cuts makes the verdict foreign: the
        safe direction.
        """
        cut = False

        def before(position: int) -> re.Match[bytes] | None:
            nonlocal cut
            low = max(0, position - _WINDOW)
            if scrubbed[low:position].rstrip(b" \t\r\n")[-1:] not in _JOINT_BYTES:
                return None  # no joint character: no match and no cut (the common case)
            found = _BEYOND_BEFORE.search(scrubbed, low, position)
            if found is None and low > 0 and _CUT_BEFORE.fullmatch(scrubbed, low, position):
                cut = True  # "<a word or blanks longer than the window><joint><realm>"
            return found

        def after(position: int) -> re.Match[bytes] | None:
            return _BEYOND_AFTER.match(scrubbed, position)

        def walk(
            found: re.Match[bytes],
            step: Callable[[int], re.Match[bytes] | None],
            edge: Callable[[re.Match[bytes]], int],
        ) -> str:
            """Read one side outward; stop at the first word that is a person or an own name."""
            chain = [self._kind(found.group(1))]
            beyond: re.Match[bytes] | None = found
            while chain[-1] in ("vocabulary", "number") and beyond is not None:
                # The faction, region or number is fine; the segment beyond it may not be.
                beyond = step(edge(beyond))
                if cut:
                    return "foreign"
                if beyond is not None:
                    chain.append(self._kind(beyond.group(1)))
            if chain[-1] == "foreign":
                return "foreign"
            # A plain number next to the realm ("<realm>-2") is nobody: as good as own.
            return "vocabulary" if "vocabulary" in chain else "own"

        verdicts: set[str] = set()
        leading = before(match.start())
        if cut:
            return "foreign"
        if leading is None and scrubbed[max(0, match.start() - 2) : match.start()].endswith(
            (b"(", b"( ")
        ):
            low = max(0, match.start() - _WINDOW)
            leading = _PAREN_BEFORE.search(scrubbed, low, match.start())
        if leading is not None:
            verdicts.add(walk(leading, before, lambda m: m.start()))
        trailing = after(match.end())
        if trailing is not None:
            verdicts.add(walk(trailing, after, lambda m: m.end()))
        return next((v for v in ("foreign", "vocabulary", "own") if v in verdicts), "own")

    @staticmethod
    def _word(raw: bytes) -> str:
        return _fold(raw.decode("utf-8", errors="replace"))

    def _kind(self, raw: bytes) -> str:
        """ "own" (a pseudonym word), "vocabulary", "number" (digits only) or "foreign"."""
        word = self._word(raw)
        if word in self._partner_words:
            return "own"
        if word in VOCABULARY_PARTNERS:
            return "vocabulary"
        return "number" if raw.isdigit() else "foreign"

    def _stranger(self, raw: bytes) -> bool:
        """A word nobody accounts for: not a pseudonym, vocabulary, a number or a keyword."""
        return (
            self._kind(raw) == "foreign"
            and not raw[:1].isdigit()  # `0x511`, `3rd`: character names hold no digit
            and self._word(raw) not in RESERVED_WORDS
        )

    def _capitalised_near(
        self, view: bytes, lines: _Lines, strangers: dict[int, bool], match: re.Match[bytes]
    ) -> bool:
        """Is there a capitalised stranger on this realm's line, or across a joint-only line end?

        Each line is read once however many realms it holds (`strangers`
        remembers), so a file of many short lines or one long line is linear.
        """

        def stranger_on(index: int) -> bool:
            if index not in strangers:
                start, end = lines.span(index)
                strangers[index] = any(
                    self._stranger(m.group(0)) for m in _CAPITALISED_RE.finditer(view, start, end)
                )
            return strangers[index]

        index = lines.index(match.start())
        start, end = lines.span(index)
        if stranger_on(index):
            return True
        if (
            index > 0
            and _JOINT_ONLY.fullmatch(view, start, match.start())
            and stranger_on(index - 1)
        ):
            return True
        return (
            index + 1 < lines.count
            and _JOINT_ONLY.fullmatch(view, match.end(), end) is not None
            and stranger_on(index + 1)
        )

    def _stranger_adjacent(self, view: bytes, match: re.Match[bytes]) -> bool:
        """Is the nearest word across a short non-joint separator, on either side, a stranger?"""
        low = max(0, match.start() - _NEAR_WINDOW)
        leading: re.Match[bytes] | None = None
        if match.start() and view[match.start() - 1] not in _NOT_SEPARATOR:
            leading = _NEAR_BEFORE.search(view, low, match.start())
            if leading is None and low > 0 and _NEAR_CUT.fullmatch(view, low, match.start()):
                return True  # a word longer than the window: assume a person
        trailing = _NEAR_AFTER.match(view, match.end())
        return any(self._stranger(w.group(1)) for w in (leading, trailing) if w is not None)

    def _is_pair(self, match: re.Match[bytes]) -> bool:
        """Is every word of this quoted string a pseudonym or a faction/region/'Default' word?"""
        runs = re.findall(rb"[%s]+" % _LETTERS, match.group(0))
        return all(self._kind(run) != "foreign" for run in runs)

    def scrub_path(self, rel: PurePosixPath) -> tuple[PurePosixPath, list[str]]:
        """Pseudonymise each path component; report anything that must not be a path."""
        parts: list[str] = []
        problems: list[str] = []
        for part in rel.parts:
            result = self.scrub(part.encode("utf-8"))
            text = result.data.decode("utf-8", errors="replace")
            if text in {"", ".", ".."} or "/" in text or "\\" in text:
                problems.append("unsafe path component after scrubbing")
            problems.extend(f"in path: {p}" for p in result.problems)
            parts.append(text)
        return PurePosixPath(*parts), problems


def _overlaps(spans: list[Span], start: int, end: int) -> bool:
    """Does [start, end) intersect any of these sorted, disjoint spans?"""
    at = bisect.bisect_right(spans, (start, sys.maxsize))
    if at and spans[at - 1][1] > start:
        return True
    return at < len(spans) and spans[at][0] < end


def _inside(spans: list[Span], start: int, end: int) -> bool:
    """Does one of these sorted, disjoint spans contain all of [start, end)?"""
    at = bisect.bisect_right(spans, (start, sys.maxsize))
    return bool(at) and spans[at - 1][0] <= start and end <= spans[at - 1][1]


def _located(label: str, data: bytes, hits: Sequence[re.Match[bytes]]) -> str:
    """`<label> x<count> (first at byte, line)`. Never the matched text."""
    first = hits[0].start()
    line = data.count(b"\n", 0, first) + data.count(b"\r", 0, first) + 1
    line -= data.count(b"\r\n", 0, first)
    return f"{label} x{len(hits)} (first at byte {first}, line {line})"


# ─── reading the install (read-only) ─────────────────────────────────────────


def read_bytes(path: Path, max_lines: int | None = None) -> bytes:
    """Read a file, or its first `max_lines` lines, opened read-only."""
    with path.open("rb") as handle:
        if max_lines is None:
            return handle.read()
        return b"".join(line for _, line in zip(range(max_lines), handle, strict=False))


def children(directory: Path) -> list[Path]:
    try:
        return sorted(directory.iterdir(), key=lambda p: p.name)
    except OSError:
        return []


def child(directory: Path, name: str) -> Path | None:
    """Case-insensitive lookup of one directory entry (installs vary in case)."""
    wanted = name.casefold()
    for entry in children(directory):
        if entry.name.casefold() == wanted:
            return entry
    return None


def descend(directory: Path, *names: str) -> Path | None:
    current: Path | None = directory
    for name in names:
        if current is None:
            return None
        current = child(current, name)
    return current


def subdirs(directory: Path | None) -> list[Path]:
    if directory is None:
        return []
    return [p for p in children(directory) if p.is_dir() and not p.is_symlink()]


def files(directory: Path | None) -> list[Path]:
    if directory is None:
        return []
    return [p for p in children(directory) if p.is_file() and not p.is_symlink()]


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _latest(path: Path) -> float:
    return max([_mtime(path), *(_mtime(e) for e in (children(path) if path.is_dir() else []))])


def newest(paths: Sequence[Path]) -> Path | None:
    return max(paths, key=lambda p: (_latest(p), p.name), default=None)


def newest_unit(units: Sequence[tuple[Path, ...]]) -> tuple[Path, ...] | None:
    return max(units, key=lambda u: (max(_latest(p) for p in u), u[0].name), default=None)


@dataclass(frozen=True)
class Flavor:
    folder: Path
    product: str
    version: str

    @property
    def name(self) -> str:
        return self.folder.name


def build_versions(root: Path) -> dict[str, str]:
    """`Product` -> `Version` from `.build.info`, located by header name."""
    path = child(root, ".build.info")
    if path is None:
        return {}
    lines = read_bytes(path).decode("utf-8-sig", errors="replace").splitlines()
    if not lines:
        return {}
    header = [cell.split("!", 1)[0] for cell in lines[0].split("|")]
    if "Product" not in header or "Version" not in header:
        return {}
    versions: dict[str, str] = {}
    for line in lines[1:]:
        cells = line.split("|")
        cells += [""] * (len(header) - len(cells))
        row = dict(zip(header, cells, strict=False))
        if row["Product"]:
            versions[row["Product"]] = row["Version"] or "unknown"
    return versions


def discover_flavors(root: Path) -> list[Flavor]:
    versions = build_versions(root)
    flavors: list[Flavor] = []
    for folder in subdirs(root):
        info = child(folder, ".flavor.info")
        if info is None or not info.is_file():
            continue
        lines = read_bytes(info).decode("utf-8-sig", errors="replace").splitlines()
        product = lines[1].strip() if len(lines) > 1 else ""
        flavors.append(Flavor(folder, product, versions.get(product, "unknown")))
    return flavors


def account_dirs(flavor_folder: Path) -> list[Path]:
    """Account folders. `WTF/Account/SavedVariables/` is not an account."""
    return [
        d
        for d in subdirs(descend(flavor_folder, "WTF", "Account"))
        if d.name.casefold() != SAVED_VARIABLES_DIR.casefold()
    ]


def realm_dirs(account: Path) -> list[Path]:
    """Children of an account that hold characters: realms, and digits-only groups."""
    return [d for d in subdirs(account) if d.name.casefold() != SAVED_VARIABLES_DIR.casefold()]


def is_group(name: str) -> bool:
    """A digits-only child of an account folder: a grouping level, never a name.

    The Forever beta keeps characters under `<account>/<digits>/<Name>-<Realm>/`
    [verify: the number looks like a region or realm-list id]. The number
    identifies no person, so it is neither scrubbed nor used as a pseudonym
    source, and it stays in output paths as the layout's real structure.
    """
    return re.fullmatch(r"[0-9]+", name) is not None


def split_character_folder(name: str) -> tuple[str, str] | None:
    """`<Name>-<Realm>` -> (name, realm), split on the FIRST hyphen.

    A character name never contains a hyphen; a realm name may (`Azjol-Nerub`).
    """
    character, hyphen, realm = name.partition("-")
    return (character, realm) if hyphen else None


def character_units(account: Path) -> list[tuple[Path, ...]]:
    """Each character of an account once, as its folder(s), the main folder first.

    Retail shape: `<account>/<Realm>/<Name>/`, one folder. Forever beta shape:
    `<account>/<digits>/<Name>-<Realm>/` holds the caches and SavedVariables,
    and a retail-shaped twin `<account>/<Realm>/<Name>/` beside it holds only
    AddOns.txt; the twin is matched by name and by any spelling of the realm.
    """
    parents = realm_dirs(account)
    retail = [c for realm in parents if not is_group(realm.name) for c in subdirs(realm)]
    twinned: set[Path] = set()
    units: list[tuple[Path, ...]] = []
    for group in (p for p in parents if is_group(p.name)):
        for folder in subdirs(group):
            twin = _twin(folder.name, [c for c in retail if c not in twinned])
            if twin is None:
                units.append((folder,))
            else:
                twinned.add(twin)
                units.append((folder, twin))
    units.extend((c,) for c in retail if c not in twinned)
    return units


def _twin(folder_name: str, retail: Sequence[Path]) -> Path | None:
    split = split_character_folder(folder_name)
    if split is None:
        return None
    name, realm = split
    for candidate in retail:
        forms = {_fold(form) for form in _realm_forms(candidate.parent.name)}
        if _fold(candidate.name) == _fold(name) and _fold(realm) in forms:
            return candidate
    return None


def character_labels(unit: Sequence[Path]) -> Iterator[str]:
    """What `--character` accepts for this character: REALM/NAME, or a `<Name>-<Realm>` folder."""
    for folder in unit:
        yield f"{folder.parent.name}/{folder.name}"
        if is_group(folder.parent.name):
            yield folder.name


# ─── choosing the capture set ────────────────────────────────────────────────


@dataclass
class Item:
    src: Path
    rel: PurePosixPath  # relative to the install root, real names
    flavor: str
    version: str
    kind: str
    notes: list[str] = field(default_factory=list)
    max_lines: int | None = None


def kind_of(name: str) -> str:
    lowered = name.casefold()
    exact = {
        ".build.info": "build-info",
        ".flavor.info": "flavor-info",
        "config.wtf": "config-wtf",
        "config-cache.wtf": "config-wtf",
        "bindings-cache.wtf": "bindings",
        "macros-cache.txt": "macros",
        "addons.txt": "addons-txt",
        "layout-local.txt": "layout-local",
        "chat-cache.txt": "chat-cache",
        "character-list-order.txt": "character-list-order",
        "chat-frontend-cache.txt": "chat-frontend-cache",
        "flagged-cache-account.txt": "flagged-cache",
        "flagged-cache-character.txt": "flagged-cache",
        "tts-cache-account.txt": "tts-cache",
        "tts-cache-character.txt": "tts-cache",
        "click-bindings-cache.txt": "click-bindings-cache",
    }
    if lowered in exact:
        return exact[lowered]
    if lowered.endswith((".lua", ".lua.bak")):
        return "savedvariables"
    if lowered.endswith(".toc"):
        return "toc"
    if lowered.startswith("wowcombatlog"):
        return "combatlog"
    if lowered.startswith("edit-mode-cache"):
        return "edit-mode-cache"
    return "other"


@dataclass(frozen=True)
class SvStats:
    """Shape of a SavedVariables file, measured on raw bytes (nothing is parsed)."""

    size: int
    depth: int
    array_comments: int
    non_ascii: int
    escapes: int
    signed_or_long: int
    non_finite: int
    scalar_only: bool


_HIGH_BYTES = bytes(range(0x80, 0x100))
_TABS_RE = re.compile(rb"^\t+", re.MULTILINE)
_ESCAPES_RE = re.compile(rb"\|c[0-9A-Fa-f]{8}|\|H")
_SIGNED_OR_LONG_RE = re.compile(rb"= -[0-9]|\.[0-9]{10,}")
_NON_FINITE_RE = re.compile(
    rb"(?:=[ \t]*|^[ \t]*)-?(?:1\.#[A-Za-z]+|inf|nan(?:\(ind\))?)[ \t]*,",
    re.IGNORECASE | re.MULTILINE,
)


def sv_stats(data: bytes) -> SvStats:
    return SvStats(
        size=len(data),
        depth=max((len(m.group(0)) for m in _TABS_RE.finditer(data)), default=0),
        array_comments=data.count(b"-- ["),
        non_ascii=len(data) - len(data.translate(None, _HIGH_BYTES)),
        escapes=len(_ESCAPES_RE.findall(data)),
        signed_or_long=len(_SIGNED_OR_LONG_RE.findall(data)),
        non_finite=len(_NON_FINITE_RE.findall(data)),
        scalar_only=b"{" not in data and b"=" in data,
    )


@dataclass(frozen=True)
class Verdict:
    """What a trial scrub of one candidate found. Holds no file content."""

    label: str
    problems: tuple[str, ...]
    stats: SvStats  # of the SCRUBBED bytes: what the fixture will actually contain


# (label, rank: smaller sorts first, still true of the scrubbed bytes?)
SV_CATEGORIES: tuple[tuple[str, Callable[[SvStats], float], Callable[[SvStats], bool]], ...] = (
    ("smallest file", lambda s: s.size, lambda s: True),
    ("single scalar assignment", lambda s: s.size, lambda s: s.scalar_only),
    ("largest file", lambda s: -s.size, lambda s: True),
    ("deepest nesting", lambda s: -s.depth, lambda s: s.depth >= 4),
    ("positional array comments", lambda s: -s.array_comments, lambda s: s.array_comments > 0),
    ("non-ASCII strings", lambda s: -s.non_ascii, lambda s: s.non_ascii > 0),
    ("colour and link escapes", lambda s: -s.escapes, lambda s: s.escapes > 0),
    (
        "negative numbers and long floats",
        lambda s: -s.signed_or_long,
        lambda s: s.signed_or_long > 0,
    ),
    ("non-finite number spelling", lambda s: -s.non_finite, lambda s: s.non_finite > 0),
)


class Planner:
    """Builds the list of files to capture and records why each was chosen."""

    def __init__(self, root: Path, identity: Identity, args: argparse.Namespace) -> None:
        self.root = root
        self.identity = identity
        self.args = args
        self.items: dict[Path, Item] = {}
        self.skipped: list[str] = []
        self._verdicts: dict[Path, Verdict | None] = {}

    def add(
        self,
        flavor: Flavor | None,
        src: Path | None,
        note: str | None = None,
        *,
        max_lines: int | None = None,
    ) -> None:
        if src is None or not src.is_file() or src.is_symlink():
            return
        item = self.items.get(src)
        if item is None:
            item = Item(
                src=src,
                rel=PurePosixPath(src.relative_to(self.root).as_posix()),
                flavor=flavor.name if flavor else "(install root)",
                version=flavor.version if flavor else self._root_version(),
                kind=kind_of(src.name),
                max_lines=max_lines,
            )
            self.items[src] = item
        if note and note not in item.notes:
            item.notes.append(note)

    def _root_version(self) -> str:
        versions = sorted(set(build_versions(self.root).values()))
        return ", ".join(versions) if versions else "unknown"

    def verdict(self, src: Path) -> Verdict | None:
        """Trial-scrub a candidate once, however many rankings it appears in."""
        if src not in self._verdicts:
            item = Item(src, PurePosixPath(src.relative_to(self.root).as_posix()), "", "", "")
            try:
                outcome = process(item, self.identity)
            except OSError as error:
                self._verdicts[src] = None
                self.skipped.append(f"{_scrubbed_label(item, self.identity)}: {_os_reason(error)}")
            else:
                verdict = Verdict(
                    str(outcome.label), tuple(outcome.problems), sv_stats(outcome.result.data)
                )
                self._verdicts[src] = verdict
                if verdict.problems:
                    self.skipped.append(f"{verdict.label}: {'; '.join(verdict.problems)}")
        return self._verdicts[src]

    def clean(self, src: Path) -> bool:
        verdict = self.verdict(src)
        return verdict is not None and not verdict.problems

    def first_clean(
        self,
        candidates: Iterable[Path],
        taken: set[Path],
        still_true: Callable[[SvStats], bool] = lambda s: True,
        label: str = "",
    ) -> Path | None:
        for candidate in candidates:
            if candidate in taken or not self.clean(candidate):
                continue
            verdict = self.verdict(candidate)
            assert verdict is not None
            if not still_true(verdict.stats):
                # e.g. the only non-ASCII bytes were the owner's name.
                self.skipped.append(f"{verdict.label}: no longer '{label}' once scrubbed")
                continue
            taken.add(candidate)
            return candidate
        return None

    # ── per flavor ──

    def plan_flavor(self, flavor: Flavor) -> None:
        self.add(flavor, child(flavor.folder, ".flavor.info"))
        self.add(flavor, descend(flavor.folder, "WTF", "Config.wtf"))

        account = self._pick(account_dirs(flavor.folder), self.args.account, "account")
        character: tuple[Path, ...] = ()
        if account is not None:
            for name in (*ACCOUNT_FILES, *ACCOUNT_CACHE_FILES):
                self.add(flavor, child(account, name))
            for pattern in ACCOUNT_GLOBS:
                for path in files(account):
                    lowered = path.name.casefold()
                    if PurePosixPath(lowered).match(pattern) and not lowered.endswith(OLD_SUFFIX):
                        self.add(flavor, path)
            units = character_units(account)
            wanted = self.args.character
            if wanted:
                key = _nfc(wanted).casefold()
                units = [
                    u for u in units if key in {_nfc(n).casefold() for n in character_labels(u)}
                ]
            character = newest_unit(units) or ()
            for name in (*CHARACTER_FILES, *CHARACTER_CACHE_FILES):
                for folder in character:
                    self.add(flavor, child(folder, name))

        self._plan_saved_variables(flavor, account, character)
        self._plan_tocs(flavor)
        logs = [
            p
            for p in files(child(flavor.folder, "Logs"))
            if kind_of(p.name) == "combatlog" and _size(p) > 0
        ]
        log = newest(logs)
        if log is not None:
            limit = self.args.log_lines
            self.add(flavor, log, f"first {limit} lines of the log", max_lines=limit)

    def _pick(self, candidates: list[Path], wanted: str | None, what: str) -> Path | None:
        if wanted:
            for candidate in candidates:
                if _nfc(candidate.name).casefold() == _nfc(wanted).casefold():
                    return candidate
            if candidates:
                raise CaptureError(f"--{what}: no such {what} folder in this flavor")
        return newest(candidates)

    def _plan_saved_variables(
        self, flavor: Flavor, account: Path | None, character: Sequence[Path]
    ) -> None:
        pool: list[Path] = []
        for owner in (account, *character):
            if owner is not None:
                pool.extend(files(child(owner, SAVED_VARIABLES_DIR)))
        lua = [p for p in pool if p.name.casefold().endswith(".lua")]
        backups = [p for p in pool if p.name.casefold().endswith(".lua.bak")]

        for name in self.args.sv or []:
            for path in pool:
                if path.name.casefold() == name.casefold():
                    self.add(flavor, path, "requested with --sv")

        cap = self.args.max_sv_bytes
        raw: dict[Path, SvStats] = {}
        for path in lua:
            if 0 < _size(path) <= cap:
                try:
                    raw[path] = sv_stats(read_bytes(path))
                except OSError:
                    continue  # reported if and when it is trial-scrubbed
        taken: set[Path] = set()
        for label, rank, holds in SV_CATEGORIES:
            ranked = sorted((p for p, s in raw.items() if holds(s)), key=lambda p: rank(raw[p]))
            chosen = self.first_clean(ranked, taken, holds, label)
            if chosen is not None:
                verdict = self.verdict(chosen)
                assert verdict is not None
                after = verdict.stats
                self.add(flavor, chosen, f"{label} ({after.size} bytes, depth {after.depth})")
        backup = self.first_clean(
            sorted((p for p in backups if 0 < _size(p) <= cap), key=_size), taken
        )
        self.add(flavor, backup, "previous write (.lua.bak)")

    def _plan_tocs(self, flavor: Flavor) -> None:
        addons = [
            d
            for d in subdirs(descend(flavor.folder, "Interface", "AddOns"))
            if not d.name.casefold().startswith(EXPORTED_ADDON_PREFIX)
        ]
        tocs = {d: [p for p in files(d) if p.name.casefold().endswith(".toc")] for d in addons}
        for name in self.args.toc or []:
            for addon, paths in tocs.items():
                if addon.name.casefold() == name.casefold():
                    for path in paths:
                        self.add(flavor, path, "requested with --toc")

        def bracketed(path: Path) -> bool:
            try:
                lines = read_bytes(path).splitlines()
            except OSError:
                return False
            return any(b"[" in line and not line.lstrip().startswith(b"#") for line in lines)

        taken: set[Path] = set()
        conditional = self.first_clean(
            (p for paths in tocs.values() for p in paths if bracketed(p)), taken
        )
        self.add(flavor, conditional, "bracketed load condition or variable")

        plain = [p[0] for p in tocs.values() if len(p) == 1 and not bracketed(p[0])]
        self.add(flavor, self.first_clean(plain, taken), "single-TOC addon")

        for paths in sorted((p for p in tocs.values() if len(p) >= 2), key=len):
            group = [p for p in paths if p not in taken][:3]
            if len(group) >= 2 and all(self.clean(p) for p in group):
                for path in group:
                    taken.add(path)
                    self.add(flavor, path, f"multi-TOC addon ({len(paths)} TOC files)")
                break


# ─── processing one file ─────────────────────────────────────────────────────


@dataclass
class Outcome:
    item: Item
    dest: PurePosixPath  # relative to --out/<platform>, pseudonymised
    result: ScrubResult
    problems: list[str]
    path_rewritten: bool = False
    review_cvars: tuple[str, ...] = ()

    @property
    def label(self) -> PurePosixPath:
        """The path as it may be printed: withheld if the path itself is the problem."""
        if any(p.startswith(("in path", "unsafe path")) for p in self.problems):
            return PurePosixPath(self.dest.parts[0], "<path withheld>")
        return self.dest


def _os_reason(error: OSError) -> str:
    """An OS error without its message: the message quotes the real path."""
    return f"unreadable ({type(error).__name__})"


def _scrubbed_label(item: Item, identity: Identity) -> PurePosixPath:
    dest, problems = identity.scrub_path(item.rel)
    return PurePosixPath(dest.parts[0], "<path withheld>") if problems else dest


def process(item: Item, identity: Identity, kinds: dict[str, str] | None = None) -> Outcome:
    original = read_bytes(item.src, item.max_lines)
    name = item.src.name.casefold()
    config = name in CONFIG_NAMES
    result = identity.scrub(original, blank_cvars=config, toc=name.endswith(".toc"))
    dest, path_problems = identity.scrub_path(item.rel)
    path_rewritten = dest != item.rel
    if kinds and dest.parts[0] in kinds:
        # The index files fixtures under <platform>/<flavor-kind>/, not the folder name.
        dest = PurePosixPath(kinds[dest.parts[0]], *dest.parts[1:])
    review: tuple[str, ...] = ()
    if config:
        blanked = {c.casefold() for c in identity.cvars}
        found = (
            m.group(1).decode("ascii", errors="replace") for m in SET_NAME_RE.finditer(result.data)
        )
        review = tuple(
            dict.fromkeys(
                n for n in found if REVIEW_CVAR_RE.search(n) and n.casefold() not in blanked
            )
        )
    return Outcome(item, dest, result, [*result.problems, *path_problems], path_rewritten, review)


def describe_bytes(data: bytes) -> list[str]:
    notes: list[str] = []
    if data.startswith(b"\xef\xbb\xbf"):
        notes.append("UTF-8 BOM")
    crlf = data.count(b"\r\n")
    lf = data.count(b"\n") - crlf
    cr = data.count(b"\r") - crlf
    endings = [name for name, n in (("CRLF", crlf), ("LF", lf), ("CR", cr)) if n]
    if endings:
        notes.append(("mixed " if len(endings) > 1 else "") + " and ".join(endings))
    if data.startswith((b"\n", b"\r\n")):
        notes.append("leading blank line")
    if data and not data.endswith((b"\n", b"\r")):
        notes.append("no trailing newline")
    if b"COMBATANT_INFO" in data:
        notes.append("has COMBATANT_INFO")
    return notes


def provenance_row(outcome: Outcome, platform: str, captured_by: str, consent: str) -> str:
    result = outcome.result
    scrub = [
        f"{label}: {result.count(reason)}"
        for reason, label in (
            ("identity", "identity-rewritten"),
            ("cvar", "cvars-blanked"),
            ("guid", "guids-rewritten"),
        )
        if result.count(reason)
    ]
    if result.embedded:
        scrub.append(f"embedded: {result.embedded}")
    if outcome.path_rewritten and not result.count("identity"):
        scrub.insert(0, "identity-rewritten: path only")
    cells = [
        f"`{PurePosixPath(platform) / outcome.dest}`",
        outcome.item.kind,
        outcome.item.flavor,
        outcome.item.version,
        platform,
        captured_by,
        consent,
        "; ".join(scrub) or "none",
        "; ".join([*outcome.item.notes, *describe_bytes(result.data)]),
    ]
    return "| " + " | ".join(cell.replace("|", "\\|") for cell in cells) + " |"


# ─── identity discovery ──────────────────────────────────────────────────────

_HARVEST_RE = re.compile(
    _LINE_START + rb'SET[ \t]+(lastCharacterGuid|realmName)[ \t]+"([^"\r\n]*)"', re.IGNORECASE
)


def discover_identity(root: Path, flavors: Sequence[Flavor], args: argparse.Namespace) -> Identity:
    accounts: set[str] = set()
    realms: set[str] = set()
    characters: set[str] = set()
    pairs: set[tuple[str, str]] = set()  # (character, realm) folders that exist
    grouped: set[tuple[str, str]] = set()  # (character, realm) from `<digits>/<Name>-<Realm>`
    configs: list[Path] = []

    # Every folder under the root that has WTF/Account, whether or not it is
    # still an installed flavor: an uninstalled product leaves WTF/ behind, and
    # a file in one flavor can name a character that lives in another.
    #
    # Listed strictly. The lenient `children()` turns an unlistable folder into
    # an empty one, and here that would mean a shorter identity map and files
    # captured with those names still in them.
    def listed(directory: Path) -> list[Path]:
        try:
            return sorted(directory.iterdir(), key=lambda p: p.name)
        except OSError as error:
            raise CaptureError(
                "a folder between the install root and WTF/Account/<account>/<realm>/<character> "
                f"could not be listed ({type(error).__name__}); the identity map would be "
                "incomplete, so nothing is captured"
            ) from None

    def folders(entries: Iterable[Path]) -> list[Path]:
        return [p for p in entries if p.is_dir() and not p.is_symlink()]

    def named(entries: Iterable[Path], name: str) -> Path | None:
        return next((p for p in folders(entries) if p.name.casefold() == name.casefold()), None)

    def config_files(entries: Iterable[Path]) -> list[Path]:
        return [p for p in entries if p.name.casefold() in CONFIG_NAMES and p.is_file()]

    for folder in folders(listed(root)):
        wtf = named(listed(folder), "WTF")
        if wtf is None:
            continue
        in_wtf = listed(wtf)
        configs.extend(config_files(in_wtf))
        account_root = named(in_wtf, "Account")
        for account in folders(listed(account_root)) if account_root else []:
            if account.name.casefold() == SAVED_VARIABLES_DIR.casefold():
                continue  # WTF/Account/SavedVariables/ is not an account
            accounts.add(account.name)
            in_account = listed(account)
            configs.extend(config_files(in_account))
            for realm in folders(in_account):
                if realm.name.casefold() == SAVED_VARIABLES_DIR.casefold():
                    continue
                if is_group(realm.name):
                    # A grouping level, not a realm: its children are `<Name>-<Realm>`.
                    for character in folders(listed(realm)):
                        split = split_character_folder(character.name)
                        if split is None:
                            characters.add(character.name)
                        else:
                            characters.add(split[0])
                            grouped.add(split)
                        configs.extend(config_files(listed(character)))
                    continue
                realms.add(realm.name)
                for character in folders(listed(realm)):
                    characters.add(character.name)
                    pairs.add((character.name, realm.name))
                    configs.extend(config_files(listed(character)))

    guids: set[bytes] = {g.encode() for g in args.own_guid or []}
    for config in configs:
        try:
            data = read_bytes(config)
        except OSError as error:
            raise CaptureError(
                f"a Config.wtf or config-cache.wtf could not be read ({type(error).__name__}); "
                "its realmName and lastCharacterGuid would be missing from the identity map, "
                "so nothing is captured"
            ) from None
        for m in _HARVEST_RE.finditer(data):
            if m.group(1).lower() == b"realmname":
                # A realm whose folder is gone (deleted character) is still a realm.
                value = m.group(2).decode("utf-8", errors="replace").strip()
                # A value with no letters is no pseudonym source (and is blanked anyway).
                if len(value) >= 2 and any(c.isalpha() for c in value):
                    realms.add(value)
            elif GUID_RE.fullmatch(m.group(2)):
                guids.add(m.group(2))

    # A realm spelled in a `<Name>-<Realm>` folder (spaces dropped, say) that is
    # already a spelling of a known realm keeps that realm's pseudonym, so the
    # folder `Labchara-LabrealmaPartb` matches `Labchara - Labrealma Partb` inside files.
    known = {_fold(form) for realm in realms for form in _realm_forms(realm)}
    for _name, realm_name in sorted(grouped):
        if _fold(realm_name) not in known:
            realms.add(realm_name)
            known.update(_fold(form) for form in _realm_forms(realm_name))
    pairs |= grouped

    own: set[tuple[bytes, bytes]] = set()
    for character_name, realm_name in pairs:
        for normal in ("NFC", "NFD"):
            name = unicodedata.normalize(normal, character_name).encode("utf-8")
            own.update((name, form.encode("utf-8")) for form in _realm_forms(realm_name))

    def is_own(unit: bytes) -> bool:
        """`Name-Realm` or `Name-Realm-REGION`, both halves matching one character folder."""
        name, _, rest = unit.partition(b"-")
        return (name, rest) in own or (name, rest.rpartition(b"-")[0]) in own

    for flavor in flavors:
        for log in files(child(flavor.folder, "Logs")):
            if kind_of(log.name) != "combatlog":
                continue
            try:
                head = read_bytes(log, args.log_lines)
            except OSError:
                continue
            guids.update(m.group(1) for m in GUID_NAME_RE.finditer(head) if is_own(m.group(2)))

    return Identity(
        accounts=accounts,
        realms=realms,
        characters=characters,
        extras=args.extra_name or [],
        guids=guids,
        cvars=(*IDENTITY_CVARS, *(args.blank_cvar or [])),
        show_names=args.show_map,
    )


# ─── command line ────────────────────────────────────────────────────────────


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="lab_capture.py",
        description="Copy the fixture capture set out of a WoW install and scrub identity from it.",
    )
    parser.add_argument("--root", required=True, type=Path, help="install root (has .build.info)")
    parser.add_argument("--out", required=True, type=Path, help="staging directory to write into")
    parser.add_argument("--dry-run", action="store_true", help="print the plan; write nothing")
    parser.add_argument(
        "--reuse-out",
        action="store_true",
        help="run although <out>/<platform> already has files; any file there that this run "
        "does not write is listed and makes the exit status non-zero (nothing is deleted)",
    )
    parser.add_argument("--flavor", action="append", help="flavor folder to capture (default: all)")
    parser.add_argument("--account", help="account folder to capture (default: most recent)")
    parser.add_argument("--character", help="REALM/NAME to capture (default: most recent)")
    parser.add_argument("--sv", action="append", help="also capture this SavedVariables file name")
    parser.add_argument("--toc", action="append", help="also capture this addon's TOC files")
    parser.add_argument("--extra-name", action="append", help="another identity string to replace")
    parser.add_argument("--own-guid", action="append", help="a Player-<n>-<hex> GUID that is yours")
    parser.add_argument("--blank-cvar", action="append", help="another identity CVar to blank")
    parser.add_argument("--log-lines", type=int, default=2000, help="combat log lines to keep")
    parser.add_argument(
        "--max-sv-bytes",
        type=int,
        default=8 * 1024 * 1024,
        help="largest SavedVariables file the automatic selection will pick",
    )
    parser.add_argument(
        "--platform",
        help="platform column: the client that last wrote the files (macos, windows). "
        "Required when this machine is neither, e.g. Wine/Proton or a mounted drive",
    )
    parser.add_argument(
        "--kind",
        action="append",
        metavar="FOLDER=KIND",
        help="write a flavor folder's files under this name instead (the index's flavor-kind)",
    )
    parser.add_argument("--captured-by", default="owner", help="captured_by column")
    parser.add_argument("--consent", default="owner", choices=("owner", "explicit"))
    parser.add_argument(
        "--show-map",
        action="store_true",
        help="print real name -> pseudonym (keep that output off the internet)",
    )
    return parser.parse_args(argv)


def default_platform() -> str | None:
    return {"darwin": "macos", "win32": "windows"}.get(sys.platform)


def inside(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def _kinds(args: argparse.Namespace, flavors: Sequence[Flavor]) -> dict[str, str]:
    kinds: dict[str, str] = {}
    for pair in args.kind or []:
        folder, _, kind = pair.partition("=")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", kind) or folder not in {f.name for f in flavors}:
            raise CaptureError(f"--kind {pair!r}: expected <flavor folder>=<output folder>")
        kinds[folder] = kind
    outputs = [kinds.get(f.name, f.name).casefold() for f in flavors]
    if len(set(outputs)) != len(outputs):
        raise CaptureError("--kind: two flavor folders would be written to the same output folder")
    return kinds


def run(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    out = args.out.resolve()
    if not root.is_dir():
        raise CaptureError(f"--root {args.root} is not a directory")
    if inside(out, root):
        raise CaptureError("--out is inside the install; the install is never written to (L1)")
    if inside(root, out):
        raise CaptureError("--out contains the install; choose a staging directory elsewhere")
    platform = args.platform or default_platform()
    if platform is None:
        raise CaptureError(
            "--platform is required here: say which client wrote these files (macos, windows)"
        )
    if not re.fullmatch(r"[A-Za-z0-9_-]+", platform):
        raise CaptureError("--platform must be a plain folder name")
    stage = out / platform
    existing = sorted(p for p in stage.rglob("*") if p.is_file()) if stage.is_dir() else []
    if existing and not args.reuse_out:
        raise CaptureError(
            f"{stage} already holds {len(existing)} file(s). A file an earlier run wrote and "
            "this run refuses would stay there looking scrubbed. Use an empty --out, or pass "
            "--reuse-out to have leftovers listed (nothing is ever deleted)"
        )

    flavors = discover_flavors(root)
    if args.flavor:
        wanted = {f.casefold() for f in args.flavor}
        flavors = [f for f in flavors if f.name.casefold() in wanted]
    if not flavors:
        raise CaptureError("no flavor folder (a directory with .flavor.info) found under --root")
    kinds = _kinds(args, flavors)

    identity = discover_identity(root, flavors, args)
    planner = Planner(root, identity, args)
    planner.add(None, child(root, ".build.info"))
    for flavor in flavors:
        planner.plan_flavor(flavor)

    mode = "DRY RUN, nothing will be written" if args.dry_run else f"writing under {out}"
    print(f"lab_capture: {len(planner.items)} files from {len(flavors)} flavor(s); {mode}")
    print(
        f"identity map: {len(identity.names)} names "
        f"({identity.counts['account']} accounts, {identity.counts['realm']} realms, "
        f"{identity.counts['character']} characters, {identity.counts['extra name']} extra), "
        f"{len(identity.guids)} own GUIDs, {len(identity.cvars)} CVars blanked"
    )
    if args.show_map:
        for real, pseudonym in identity.names.items():
            print(f"  {real!r} -> {pseudonym!r}")

    rows: list[str] = []
    written: set[Path] = set()
    hits: Counter[bytes] = Counter()
    refused = 0
    for item in planner.items.values():
        try:
            outcome = process(item, identity, kinds)
        except OSError as error:
            refused += 1
            label = PurePosixPath(platform) / _scrubbed_label(item, identity)
            print(f"REFUSED  {label}: {_os_reason(error)}")
            continue
        shown = PurePosixPath(platform) / outcome.label
        if outcome.problems:
            refused += 1
            print(f"REFUSED  {shown}: {'; '.join(outcome.problems)}")
            continue
        target = stage.joinpath(*outcome.dest.parts)
        if not inside(target.resolve(), out) or inside(target.resolve(), root):
            refused += 1
            print(f"REFUSED  {shown}: destination escapes --out")
            continue
        if target in written:
            refused += 1
            print(f"REFUSED  {shown}: a second source file maps to this destination")
            continue
        if not args.dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(outcome.result.data)
        written.add(target)
        result = outcome.result
        hits.update(e.new for e in result.edits if e.reason == "identity")
        verb = "would write" if args.dry_run else "wrote"
        embedded = f", {result.embedded} embedded" if result.embedded else ""
        print(f"{verb:<8} {shown} ({len(result.data)} bytes, {len(result.edits)} edits{embedded})")
        for note in result.notes:
            print(f"  look   {note}")
        if outcome.review_cvars:
            print(f"  look   CVars to review by eye: {', '.join(outcome.review_cvars)}")
        rows.append(provenance_row(outcome, platform, args.captured_by, args.consent))

    if planner.skipped:
        print("\ncandidates passed over by the automatic selection (would have been refused):")
        for line in dict.fromkeys(planner.skipped):
            print(f"  {line}")
    if hits:
        print("\nreplacements per pseudonym (an absurd count means a name is also a common word):")
        for replacement, count in sorted(hits.items(), key=lambda kv: (-kv[1], kv[0])):
            print(f"  {replacement.decode('utf-8', errors='replace')}: {count}")
    print("\nprovenance rows for lab/core/tests/fixtures/README.md:")
    for row in rows:
        print(row)

    leftovers = [p for p in existing if p not in written]
    if leftovers:
        print(
            f"\n{len(leftovers)} file(s) under {stage} were NOT written by this run; "
            "review and remove them by hand before moving anything:",
            file=sys.stderr,
        )
        for path in leftovers:
            print(f"  {path.relative_to(out).as_posix()}", file=sys.stderr)
    if refused:
        print(f"\n{refused} file(s) REFUSED; nothing was written for them", file=sys.stderr)
    return 1 if refused or leftovers else 0


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return run(args)
    except CaptureError as error:
        print(f"lab_capture: {error}", file=sys.stderr)
        return 2
    except OSError as error:
        # Never the message or a traceback: both quote real account and character paths.
        print(f"lab_capture: stopped, a file or folder was {_os_reason(error)}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
