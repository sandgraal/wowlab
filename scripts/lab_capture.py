#!/usr/bin/env python3
"""Capture and scrub real install files into the fixture staging area (M10-02).

    uv run python scripts/lab_capture.py --root <install> \
        --out lab/core/tests/fixtures/incoming [--dry-run]

Copies the capture set of `docs/handoffs/M10-03.md` out of a World of Warcraft
install and scrubs identity from it per `docs/LAB_PLAN.md` §8:

- account folder, realm and character names become stable pseudonyms, in
  paths and in file contents (same input, same pseudonym, whole capture set);
- identity CVars have their value blanked;
- the owner's own `Player-<n>-<hex>` GUIDs become pseudonym GUIDs;
- a file whose scrubbed bytes or output path still contain an email address,
  a BattleTag, an unmapped player, account or guild GUID, a surviving identity
  string in any casing or embedding, an unblanked identity CVar, or a
  `<someone>-<own realm>` name is refused: nothing is written for it and the
  exit status is non-zero.

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
from collections.abc import Callable, Iterable, Sequence
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

# Loose on purpose: a false refusal costs one candidate file, a miss is a leak.
# No top-level domain is required (`user@localhost`); non-ASCII is accepted.
EMAIL_RE = re.compile(
    rb"[A-Za-z0-9._%+\-\x80-\xff]+@[A-Za-z0-9\-\x80-\xff]+(?:\.[A-Za-z0-9\-\x80-\xff]+)*"
)
# Name#1234: a name character, '#', four or more digits, whatever follows.
BATTLETAG_RE = re.compile(rb"[A-Za-z0-9\x80-\xff]#[0-9]{4,}")
GUID_RE = re.compile(rb"Player-[0-9]+-[0-9A-Fa-f]+", re.IGNORECASE)
# [verify] spellings recalled by reviewers, not yet seen in a capture (M10-03).
ACCOUNT_GUID_RE = re.compile(rb"BNetAccount-[0-9]+-[0-9A-Fa-f]+", re.IGNORECASE)
GUILD_GUID_RE = re.compile(rb"Guild-[0-9]+-[0-9A-Fa-f]+", re.IGNORECASE)
# Combat log: a player GUID immediately followed by its quoted unit name.
GUID_NAME_RE = re.compile(rb'(Player-[0-9]+-[0-9A-Fa-f]+),"([^"\r\n]*)"', re.IGNORECASE)
# AceDB `factionrealm` keys ("Horde - <realm>") are client vocabulary, not a player.
FACTION_WORDS = frozenset({b"horde", b"alliance", b"neutral"})
SOCIAL_MACRO_RE = re.compile(
    rb"(?:\A|(?<=[\r\n]))/(?:w|whisper|invite|inv|tar|target)(?=[ \t])", re.IGNORECASE
)
NAME_REALM_SHAPE_RE = re.compile(
    rb'"([A-Za-z\x80-\xff]{2,24})(?: - |-)([A-Za-z\x80-\xff][^"\r\n]{1,40})"'
)

_WORD = rb"A-Za-z0-9\x80-\xff"
_WORD_BYTES = frozenset(
    b"abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
) | frozenset(range(0x80, 0x100))
_LINE_START = rb"(?:\A(?:\xef\xbb\xbf)?|(?<=[\r\n]))[ \t]*"
# Spans the owner does not author and the scrubber therefore never rewrites:
# the CVar name of a SET line, and a TOC directive key (except open `X-` keys).
SET_NAME_RE = re.compile(_LINE_START + rb"SET[ \t]+([^ \t\r\n]+)", re.IGNORECASE)
TOC_KEY_RE = re.compile(_LINE_START + rb"##[ \t]*+(?![Xx]-)([^:\r\n]*[^:\r\n \t])[ \t]*:")
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
CONFIG_NAMES = frozenset({"config.wtf", "config-cache.wtf"})
# Blizzard's exported interface code is not a fixture (fixtures/README.md).
EXPORTED_ADDON_PREFIX = "blizzard_"


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


# Every spelling a realm takes: the folder's, the `Name-Realm` normalisations,
# the web slug, the underscore variant, and the apostrophe as Lua escapes it.
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


def _shaped(real: str, stem: str, accented: str, index: int) -> str:
    """A pseudonym that keeps the real name's separators and, if any, one non-ASCII letter.

    This reveals the run of spaces, hyphens and apostrophes in the real name
    (so: its word count) and whether it had a non-ASCII letter. Nothing else.
    """
    real = _nfc(real)
    base = accented if any(ord(c) > 0x7F for c in real) else stem
    upper = real.isupper()
    out: list[str] = []
    words = 0
    for position, run in enumerate(re.split(r"([ \-']+)", real)):
        if position % 2:
            out.append(run)
        elif run:
            word = base + _letters(index) if words == 0 else "Part" + _letters(words)
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
        self._survivors: dict[bytes, str] = {}  # ASCII-lowered form -> mode
        self._realm_pseudonyms: set[bytes] = set()
        self._partner_words: set[bytes] = set(FACTION_WORDS)

        for i, realm in enumerate(self._ordered(realms)):
            self._add_name(realm, _shaped(realm, "Labrealm", "Labréalm", i), "realm")
        for i, character in enumerate(self._ordered(characters)):
            self._add_name(character, _shaped(character, "Labchar", "Labchár", i), "character")
        for i, extra in enumerate(self._ordered(extras)):
            self._add_name(extra, _shaped(extra, "Labname", "Labnamé", i), "extra name")
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
        realm_forms = sorted(self._realm_pseudonyms, key=len, reverse=True)
        self._foreign_partner = (
            re.compile(
                rb"([A-Za-z\x80-\xff]+)(?: - |-)(?:%s)" % b"|".join(map(re.escape, realm_forms)),
                re.IGNORECASE,
            )
            if realm_forms
            else None
        )

    @staticmethod
    def _ordered(names: Iterable[str]) -> list[str]:
        return sorted({_nfc(n) for n in names}, key=lambda n: (n.casefold(), n))

    def _check(self, real: str, category: str) -> None:
        shown = repr(real) if self._show_names else f"a {category} name ({len(real)} characters)"
        if len(real) < 2:
            raise CaptureError(f"{shown} is too short to replace safely")
        if real.casefold() in RESERVED_WORDS:
            raise CaptureError(f"{shown} is a format keyword; replacing it would corrupt files")

    def _token(self, text: str, replacement: str, mode: str) -> None:
        raw = text.encode("utf-8")
        if len(text) >= 2:
            self._tokens.setdefault(raw, Token(raw, replacement.encode("utf-8"), mode))

    def _survivor_form(self, text: str, mode: str | None = None) -> None:
        if len(text) < 2:
            return
        chosen = mode or ("substring" if len(text) >= EMBEDDED_MIN_CHARS else "word")
        lowered = text.encode("utf-8").lower()
        if self._survivors.get(lowered) != "substring":
            self._survivors[lowered] = chosen

    def _add_name(self, real: str, pseudonym: str, category: str) -> None:
        if real in self.names:
            return  # e.g. a character named like a realm: the first category wins
        self._check(real, category)
        self.names[real] = pseudonym
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
            encoded = replacement.encode("utf-8")
            if category == "realm":
                self._realm_pseudonyms.add(encoded)
            last_word = re.split(rb"[^A-Za-z\x80-\xff]+", encoded.lower())[-1]
            self._partner_words.add(last_word)

    def _add_account(self, real: str, index: int) -> None:
        if real in self.names:
            return
        numbered = re.fullmatch(r"([0-9]+)#([0-9]+)", real)
        if not numbered:
            self._add_name(real, _shaped(real, "LABACCOUNT", "LABACCÓUNT", index), "account")
            return
        number = str(90000001 + index)
        self.names[real] = f"{number}#{numbered.group(2)}"
        self._token(real, self.names[real], "substring")
        self._survivor_form(real, "substring")
        if len(numbered.group(1)) >= 5:
            # The bare account number, but never inside a longer number.
            self._token(numbered.group(1), number, "number")
            self._survivor_form(numbered.group(1), "number")

    # ── scrubbing ──

    @staticmethod
    def _protected(data: bytes, *, config: bool, toc: bool) -> list[tuple[int, int]]:
        spans: list[tuple[int, int]] = []
        if config:
            spans.extend(m.span(1) for m in SET_NAME_RE.finditer(data))
        if toc:
            spans.extend(m.span(1) for m in TOC_KEY_RE.finditer(data))
        return sorted(spans)

    def scrub(self, data: bytes, *, blank_cvars: bool = False, toc: bool = False) -> ScrubResult:
        """Return `data` with identity replaced, plus every edit and any reason to refuse.

        `blank_cvars` marks a Config.wtf-style file, `toc` a TOC file.
        """
        # Three passes over the ORIGINAL bytes, highest priority first. Matches
        # within a pass never overlap each other; a match that overlaps a
        # protected span or an edit from an earlier pass is dropped.
        protected = self._protected(data, config=blank_cvars, toc=toc)
        edits: list[Edit] = []
        owned: list[tuple[int, int]] = list(protected)

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
            owned[:] = sorted([*protected, *((e.offset, e.end) for e in edits)])

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
        cursor = 0
        for edit in edits:
            out += data[cursor : edit.offset]
            out += edit.new
            cursor = edit.end
        out += data[cursor:]
        scrubbed = bytes(out)
        problems, notes = self.inspect(scrubbed, config=blank_cvars, toc=toc)
        return ScrubResult(scrubbed, tuple(edits), tuple(problems), tuple(notes))

    def inspect(
        self, scrubbed: bytes, *, config: bool = False, toc: bool = False
    ) -> tuple[list[str], list[str]]:
        """(reasons these bytes must not be emitted, notes for the eye). Never quotes a match."""
        problems: list[str] = []
        notes: list[str] = []
        protected = self._protected(scrubbed, config=config, toc=toc)

        def report(
            into: list[str],
            label: str,
            matches: Iterable[re.Match[bytes]],
            *,
            vocabulary_exempt: bool = False,
        ) -> None:
            # Only the identity-string checks skip the protected spans (a CVar
            # name or TOC key that merely spells like a name). Emails, tags,
            # GUIDs and identity CVars are judged everywhere.
            hits = [
                m
                for m in matches
                if not (vocabulary_exempt and _overlaps(protected, m.start(), m.end()))
            ]
            if hits:
                first = hits[0].start()
                line = scrubbed.count(b"\n", 0, first) + scrubbed.count(b"\r", 0, first) + 1
                line -= scrubbed.count(b"\r\n", 0, first)
                into.append(f"{label} x{len(hits)} (first at byte {first}, line {line})")

        report(problems, "email address", EMAIL_RE.finditer(scrubbed))
        report(problems, "BattleTag", BATTLETAG_RE.finditer(scrubbed))
        report(
            problems,
            "unmapped player GUID",
            (m for m in GUID_RE.finditer(scrubbed) if m.group(0) not in self._pseudo_guids),
        )
        report(problems, "account GUID", ACCOUNT_GUID_RE.finditer(scrubbed))
        report(problems, "guild GUID", GUILD_GUID_RE.finditer(scrubbed))
        report(problems, "unblanked identity CVar", self._cvar_unblanked.finditer(scrubbed))
        if self._identity is not None:
            report(
                problems,
                "surviving identity string",
                self._identity.finditer(scrubbed),
                vocabulary_exempt=True,
            )
        if self._survivor is not None:
            # bytes.lower() folds ASCII only and keeps every offset.
            report(
                problems,
                "surviving identity string (other casing or embedded)",
                self._survivor.finditer(scrubbed.lower()),
                vocabulary_exempt=True,
            )
        if self._foreign_partner is not None:
            report(
                problems,
                "someone else's name on an own realm",
                (
                    m
                    for m in self._foreign_partner.finditer(scrubbed)
                    if m.group(1).lower() not in self._partner_words
                ),
            )
        report(
            notes,
            "Name-Realm-shaped string that is not a pseudonym pair",
            (m for m in NAME_REALM_SHAPE_RE.finditer(scrubbed) if not self._is_pair(m)),
        )
        report(notes, "whisper/invite/target macro line", SOCIAL_MACRO_RE.finditer(scrubbed))
        return problems, notes

    def _is_pair(self, match: re.Match[bytes]) -> bool:
        if match.group(1).lower() not in self._partner_words - FACTION_WORDS:
            return False
        rest = match.group(2).lower()
        return any(rest.startswith(realm.lower()) for realm in self._realm_pseudonyms)

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


def _overlaps(spans: list[tuple[int, int]], start: int, end: int) -> bool:
    """Does [start, end) intersect any of these sorted, disjoint spans?"""
    at = bisect.bisect_right(spans, (start, sys.maxsize))
    if at and spans[at - 1][1] > start:
        return True
    return at < len(spans) and spans[at][0] < end


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


def newest(paths: Sequence[Path]) -> Path | None:
    def latest(path: Path) -> float:
        return max([_mtime(path), *(_mtime(e) for e in (children(path) if path.is_dir() else []))])

    return max(paths, key=lambda p: (latest(p), p.name), default=None)


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
    return subdirs(descend(flavor_folder, "WTF", "Account"))


def realm_dirs(account: Path) -> list[Path]:
    return [d for d in subdirs(account) if d.name.casefold() != SAVED_VARIABLES_DIR.casefold()]


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
        character: Path | None = None
        if account is not None:
            for name in ACCOUNT_FILES:
                self.add(flavor, child(account, name))
            for pattern in ACCOUNT_GLOBS:
                for path in files(account):
                    if PurePosixPath(path.name.casefold()).match(pattern):
                        self.add(flavor, path)
            characters = [c for realm in realm_dirs(account) for c in subdirs(realm)]
            wanted = self.args.character
            if wanted:
                characters = [
                    c
                    for c in characters
                    if _nfc(f"{c.parent.name}/{c.name}").casefold() == _nfc(wanted).casefold()
                ]
            character = newest(characters)
            if character is not None:
                for name in CHARACTER_FILES:
                    self.add(flavor, child(character, name))

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
        self, flavor: Flavor, account: Path | None, character: Path | None
    ) -> None:
        pool: list[Path] = []
        for owner in (account, character):
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
    configs: list[Path] = []

    # Every folder under the root that has WTF/Account, whether or not it is
    # still an installed flavor: an uninstalled product leaves WTF/ behind, and
    # a file in one flavor can name a character that lives in another.
    for folder in subdirs(root):
        config = descend(folder, "WTF", "Config.wtf")
        if config is not None:
            configs.append(config)
        for account in account_dirs(folder):
            accounts.add(account.name)
            configs.extend(p for p in files(account) if p.name.casefold() in CONFIG_NAMES)
            for realm in realm_dirs(account):
                realms.add(realm.name)
                for character in subdirs(realm):
                    characters.add(character.name)
                    pairs.add((character.name, realm.name))
                    configs.extend(p for p in files(character) if p.name.casefold() in CONFIG_NAMES)

    guids: set[bytes] = {g.encode() for g in args.own_guid or []}
    for config in configs:
        try:
            data = read_bytes(config)
        except OSError:
            continue  # its identity CVars stay unharvested; the file itself will be refused
        for m in _HARVEST_RE.finditer(data):
            if m.group(1).lower() == b"realmname":
                # A realm whose folder is gone (deleted character) is still a realm.
                value = m.group(2).decode("utf-8", errors="replace").strip()
                if len(value) >= 2:
                    realms.add(value)
            elif GUID_RE.fullmatch(m.group(2)):
                guids.add(m.group(2))

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
        f"identity map: {len(identity.names)} names, {len(identity.guids)} own GUIDs, "
        f"{len(identity.cvars)} CVars blanked"
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
