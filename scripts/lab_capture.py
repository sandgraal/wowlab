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
  a BattleTag, an unmapped player GUID, a surviving identity string or an
  unblanked identity CVar is refused: nothing is written for it and the exit
  status is non-zero.

How it edits: byte-level, targeted replacement only. Every edit is an
(offset, old bytes, new bytes) triple against the original file; every byte
outside an edit is copied through untouched. Nothing here parses a format and
re-serializes it: a fixture produced by the parser under test would prove
nothing about that parser.

Invariants: the install is opened read-only and nothing is created inside it
(L1); the only writes are regular files under `--out`. Flavor folders,
product codes and versions are discovered from `.flavor.info` and
`.build.info`, never named here (L6). Standard library only.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

# ─── deny-lists and detectors ────────────────────────────────────────────────

# CVars whose value identifies the account or a character. `portal` stays
# (it is a region, and parsers want a populated line). Extend as found; the
# format reference (docs/LAB_FORMATS.md §5) points here.
IDENTITY_CVARS: tuple[str, ...] = ("accountName", "accountList", "lastCharacterGuid")

# An identity string equal to one of these would rewrite format keywords.
RESERVED_WORDS = frozenset({"set", "bind", "end", "ver", "true", "false", "nil", "player"})

EMAIL_RE = re.compile(rb"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
# Name#1234: a name character, '#', four to six digits, then no further
# alphanumeric. Deliberately loose about the name; a false refusal is cheap.
BATTLETAG_RE = re.compile(rb"[A-Za-z0-9\x80-\xff]#[0-9]{4,6}(?![0-9A-Za-z])")
GUID_RE = re.compile(rb"Player-[0-9]+-[0-9A-Fa-f]+")
# Combat log: a player GUID immediately followed by its quoted unit name.
GUID_NAME_RE = re.compile(rb'(Player-[0-9]+-[0-9A-Fa-f]+),"([^"\r\n]*)"')

_WORD = rb"A-Za-z0-9\x80-\xff"

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

    @property
    def end(self) -> int:
        return self.offset + len(self.old)


@dataclass(frozen=True)
class ScrubResult:
    data: bytes
    edits: tuple[Edit, ...]
    problems: tuple[str, ...]  # non-empty means refuse

    def count(self, reason: str) -> int:
        return sum(1 for e in self.edits if e.reason == reason)


def _letters(index: int) -> str:
    """0 -> a, 25 -> z, 26 -> ba: an unbounded, letters-only suffix."""
    out = ""
    n = index + 1
    while n:
        n, rem = divmod(n - 1, 26)
        out = chr(ord("a") + rem) + out
    return out


def _delimited(text: bytes, mode: str) -> bytes:
    escaped = re.escape(text)
    if mode == "substring":
        return escaped
    boundary = _WORD + (rb"." if mode == "number" else b"")
    return b"(?<![" + boundary + b"])" + escaped + b"(?![" + boundary + b"])"


def _realm_forms(realm: str) -> list[str]:
    """A realm as the folder spells it and as `Name-Realm` strings normalise it."""
    forms = [realm]
    for strip in (" ", " -", " -'"):
        squeezed = realm.translate({ord(c): None for c in strip})
        if squeezed and squeezed not in forms:
            forms.append(squeezed)
    return forms


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
    ) -> None:
        self.names: dict[str, str] = {}
        self._tokens: dict[bytes, Token] = {}
        self._survivor_forms: set[bytes] = set()
        self._number_forms: set[bytes] = set()

        for i, realm in enumerate(self._ordered(realms)):
            self._add_name(realm, "Labrealm" + _letters(i), _realm_forms(realm))
        for i, character in enumerate(self._ordered(characters)):
            self._add_name(character, "Labchar" + _letters(i), [character])
        for i, extra in enumerate(self._ordered(extras)):
            self._add_name(extra, "Labname" + _letters(i), [extra])
        for i, account in enumerate(self._ordered(accounts)):
            self._add_account(account, i)

        self.guids: dict[bytes, bytes] = {
            guid: b"Player-9999-%08X" % (i + 1) for i, guid in enumerate(sorted(set(guids)))
        }
        self._pseudo_guids = frozenset(self.guids.values())

        self.cvars = tuple(dict.fromkeys(cvars))
        names = b"|".join(re.escape(c.encode()) for c in self.cvars)
        self._cvar_value = re.compile(
            rb'^[ \t]*SET[ \t]+(?:%s)[ \t]+"([^"\r\n]*)"' % names, re.IGNORECASE | re.MULTILINE
        )
        self._cvar_line = re.compile(
            rb"^[ \t]*SET[ \t]+(?:%s)(?![%s])([^\r\n]*)" % (names, _WORD + b"_"),
            re.IGNORECASE | re.MULTILINE,
        )

        ordered = sorted(self._tokens.values(), key=lambda t: (-len(t.text), t.text))
        self._identity = (
            re.compile(b"|".join(_delimited(t.text, t.mode) for t in ordered)) if ordered else None
        )
        survivors = [_delimited(f, "word") for f in sorted(self._survivor_forms, key=len)]
        self._survivor_word = (
            re.compile(b"|".join(reversed(survivors)), re.IGNORECASE) if survivors else None
        )

    @staticmethod
    def _ordered(names: Iterable[str]) -> list[str]:
        return sorted(set(names), key=lambda n: (n.casefold(), n))

    def _check(self, real: str) -> None:
        if len(real) < 2:
            raise CaptureError(f"identity string {real!r} is too short to replace safely")
        if real.casefold() in RESERVED_WORDS:
            raise CaptureError(
                f"identity string {real!r} is a format keyword; replacing it would corrupt files"
            )

    def _token(self, text: str, replacement: str, mode: str) -> None:
        raw = text.encode("utf-8")
        self._tokens.setdefault(raw, Token(raw, replacement.encode("utf-8"), mode))

    def _add_name(self, real: str, pseudonym: str, forms: Sequence[str]) -> None:
        if real in self.names:
            return  # e.g. a character named like a realm: the first category wins
        self._check(real)
        self.names[real] = pseudonym
        # Exact spelling: replaced wherever it occurs, even inside a longer
        # word. Over-replacing is ugly; under-replacing is a leak.
        for form in forms:
            self._token(form, pseudonym, "substring")
        # Other casings (addons lower-case keys): only as a whole word, or
        # `true`/`SET` could be hit through a name's case variant.
        for form in forms:
            self._token(form.lower(), pseudonym.lower(), "word")
            self._token(form.upper(), pseudonym.upper(), "word")
            for cased in (form, form.lower(), form.upper(), form.title(), form.casefold()):
                self._survivor_forms.add(cased.encode("utf-8"))

    def _add_account(self, real: str, index: int) -> None:
        if real in self.names:
            return
        self._check(real)
        numbered = re.fullmatch(r"([0-9]+)#([0-9]+)", real)
        if numbered:
            number = str(90000001 + index)
            self.names[real] = f"{number}#{numbered.group(2)}"
            self._token(real, self.names[real], "substring")
            if len(numbered.group(1)) >= 5:
                # The bare account number, but never inside a longer number.
                self._token(numbered.group(1), number, "number")
                self._number_forms.add(numbered.group(1).encode())
            self._survivor_forms.add(real.encode("utf-8"))
        else:
            self._add_name(real, "LABACCOUNT" + _letters(index).upper(), [real])

    # ── scrubbing ──

    def scrub(self, data: bytes, *, blank_cvars: bool = False) -> ScrubResult:
        """Return `data` with identity replaced, plus every edit and any reason to refuse."""
        edits: list[Edit] = []

        def claim(start: int, end: int, new: bytes, reason: str) -> None:
            if start == end or data[start:end] == new:
                return
            if any(start < e.end and e.offset < end for e in edits):
                return  # an earlier, higher-priority edit owns these bytes
            edits.append(Edit(start, data[start:end], new, reason))

        if blank_cvars:
            for m in self._cvar_value.finditer(data):
                claim(m.start(1), m.end(1), b"", "cvar")
        for m in GUID_RE.finditer(data):
            if m.group(0) in self.guids:
                claim(m.start(), m.end(), self.guids[m.group(0)], "guid")
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
        return ScrubResult(scrubbed, tuple(edits), tuple(self.problems(scrubbed)))

    def problems(self, scrubbed: bytes) -> list[str]:
        """Every reason these bytes must not be emitted. Never quotes the match."""
        found: list[str] = []

        def report(label: str, matches: Iterator[re.Match[bytes]]) -> None:
            hits = list(matches)
            if hits:
                first = hits[0].start()
                line = scrubbed.count(b"\n", 0, first) + 1
                found.append(f"{label} x{len(hits)} (first at byte {first}, line {line})")

        report("email address", EMAIL_RE.finditer(scrubbed))
        report("BattleTag", BATTLETAG_RE.finditer(scrubbed))
        report(
            "unmapped player GUID",
            (m for m in GUID_RE.finditer(scrubbed) if m.group(0) not in self._pseudo_guids),
        )
        report(
            "unblanked identity CVar",
            (m for m in self._cvar_line.finditer(scrubbed) if m.group(1).strip() != b'""'),
        )
        if self._survivor_word is not None:
            report("surviving identity string", self._survivor_word.finditer(scrubbed))
        if self._number_forms:
            numbers = b"|".join(_delimited(n, "number") for n in sorted(self._number_forms))
            report("surviving account number", re.finditer(numbers, scrubbed))
        exact = [t.text for t in self._tokens.values() if t.mode == "substring"]
        if exact:
            pattern = b"|".join(re.escape(t) for t in sorted(exact, key=len, reverse=True))
            report("surviving identity string (exact)", re.finditer(pattern, scrubbed))
        return found

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


def newest(paths: Sequence[Path]) -> Path | None:
    def mtime(path: Path) -> float:
        latest = path.stat().st_mtime
        for entry in children(path) if path.is_dir() else []:
            latest = max(latest, entry.stat().st_mtime)
        return latest

    return max(paths, key=lambda p: (mtime(p), p.name), default=None)


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


def account_dirs(flavor: Flavor) -> list[Path]:
    return subdirs(descend(flavor.folder, "WTF", "Account"))


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

    path: Path
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


def sv_stats(path: Path) -> SvStats:
    data = read_bytes(path)
    return SvStats(
        path=path,
        size=len(data),
        depth=max((len(m.group(0)) for m in _TABS_RE.finditer(data)), default=0),
        array_comments=data.count(b"-- ["),
        non_ascii=len(data) - len(data.translate(None, _HIGH_BYTES)),
        escapes=len(_ESCAPES_RE.findall(data)),
        signed_or_long=len(_SIGNED_OR_LONG_RE.findall(data)),
        non_finite=len(_NON_FINITE_RE.findall(data)),
        scalar_only=b"{" not in data and b"=" in data,
    )


class Planner:
    """Builds the list of files to capture and records why each was chosen."""

    def __init__(self, root: Path, identity: Identity, args: argparse.Namespace) -> None:
        self.root = root
        self.identity = identity
        self.args = args
        self.items: dict[Path, Item] = {}
        self.skipped: list[str] = []

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

    def clean(self, src: Path, max_lines: int | None = None) -> bool:
        """Would this candidate survive the scrub? Used to pick among equals."""
        item = Item(src, PurePosixPath(src.relative_to(self.root).as_posix()), "", "", "")
        item.max_lines = max_lines
        outcome = process(item, self.identity)
        if outcome.problems:
            self.skipped.append(f"{outcome.dest}: {'; '.join(outcome.problems)}")
        return not outcome.problems

    def first_clean(self, candidates: Iterable[Path], taken: set[Path]) -> Path | None:
        for candidate in candidates:
            if candidate not in taken and self.clean(candidate):
                taken.add(candidate)
                return candidate
        return None

    # ── per flavor ──

    def plan_flavor(self, flavor: Flavor) -> None:
        self.add(flavor, child(flavor.folder, ".flavor.info"))
        self.add(flavor, descend(flavor.folder, "WTF", "Config.wtf"))

        account = self._pick(account_dirs(flavor), self.args.account, "account")
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
                    if f"{c.parent.name}/{c.name}".casefold() == wanted.casefold()
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
            if kind_of(p.name) == "combatlog" and p.stat().st_size > 0
        ]
        log = newest(logs)
        if log is not None:
            limit = self.args.log_lines
            self.add(flavor, log, f"first {limit} lines of the log", max_lines=limit)

    def _pick(self, candidates: list[Path], wanted: str | None, what: str) -> Path | None:
        if wanted:
            for candidate in candidates:
                if candidate.name.casefold() == wanted.casefold():
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
            matches = [p for p in pool if p.name.casefold() == name.casefold()]
            for path in matches:
                self.add(flavor, path, "requested with --sv")

        stats = [s for s in (sv_stats(p) for p in lua) if s.size > 0]
        cap = self.args.max_sv_bytes
        taken: set[Path] = set()
        categories: list[tuple[str, list[SvStats]]] = [
            ("smallest file", sorted(stats, key=lambda s: s.size)),
            (
                "single scalar assignment",
                sorted((s for s in stats if s.scalar_only), key=lambda s: s.size),
            ),
            (
                "largest file",
                sorted((s for s in stats if s.size <= cap), key=lambda s: -s.size),
            ),
            (
                "deepest nesting",
                sorted((s for s in stats if s.depth >= 4), key=lambda s: (-s.depth, s.size)),
            ),
            (
                "positional array comments",
                sorted((s for s in stats if s.array_comments), key=lambda s: -s.array_comments),
            ),
            (
                "non-ASCII strings",
                sorted((s for s in stats if s.non_ascii), key=lambda s: -s.non_ascii),
            ),
            (
                "colour and link escapes",
                sorted((s for s in stats if s.escapes), key=lambda s: -s.escapes),
            ),
            (
                "negative numbers and long floats",
                sorted((s for s in stats if s.signed_or_long), key=lambda s: -s.signed_or_long),
            ),
            (
                "non-finite number spelling",
                sorted((s for s in stats if s.non_finite), key=lambda s: -s.non_finite),
            ),
        ]
        by_path = {s.path: s for s in stats}
        for label, ranked in categories:
            ordered = [s.path for s in ranked if s.size <= cap]
            chosen = self.first_clean(ordered, taken)
            if chosen is not None:
                s = by_path[chosen]
                self.add(flavor, chosen, f"{label} ({s.size} bytes, depth {s.depth})")
        backup = self.first_clean(
            sorted(
                (p for p in backups if 0 < p.stat().st_size <= cap), key=lambda p: p.stat().st_size
            ),
            taken,
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
            return any(
                b"[" in line and not line.lstrip().startswith(b"#")
                for line in read_bytes(path).splitlines()
            )

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


def process(item: Item, identity: Identity, kinds: dict[str, str] | None = None) -> Outcome:
    original = read_bytes(item.src, item.max_lines)
    result = identity.scrub(original, blank_cvars=item.src.name.casefold() in CONFIG_NAMES)
    dest, path_problems = identity.scrub_path(item.rel)
    path_rewritten = dest != item.rel
    if kinds and dest.parts[0] in kinds:
        # The index files fixtures under <platform>/<flavor-kind>/, not the folder name.
        dest = PurePosixPath(kinds[dest.parts[0]], *dest.parts[1:])
    return Outcome(item, dest, result, [*result.problems, *path_problems], path_rewritten)


def describe_bytes(data: bytes) -> list[str]:
    notes: list[str] = []
    if data.startswith(b"\xef\xbb\xbf"):
        notes.append("UTF-8 BOM")
    crlf = data.count(b"\r\n")
    lf = data.count(b"\n") - crlf
    if crlf and lf:
        notes.append("mixed CRLF and LF")
    elif crlf:
        notes.append("CRLF")
    elif lf:
        notes.append("LF")
    if data.startswith((b"\n", b"\r\n")):
        notes.append("leading blank line")
    if data and not data.endswith(b"\n"):
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


def discover_identity(root: Path, flavors: Sequence[Flavor], args: argparse.Namespace) -> Identity:
    accounts: set[str] = set()
    realms: set[str] = set()
    characters: set[str] = set()
    configs: list[Path] = []
    logs: list[Path] = []

    # Every flavor of the install, not only the captured ones: a file in one
    # flavor can name a character that lives in another.
    for flavor in discover_flavors(root):
        config = descend(flavor.folder, "WTF", "Config.wtf")
        if config is not None:
            configs.append(config)
        for account in account_dirs(flavor):
            accounts.add(account.name)
            configs.extend(p for p in files(account) if p.name.casefold() in CONFIG_NAMES)
            for realm in realm_dirs(account):
                realms.add(realm.name)
                for character in subdirs(realm):
                    characters.add(character.name)
                    configs.extend(p for p in files(character) if p.name.casefold() in CONFIG_NAMES)
    for flavor in flavors:
        logs.extend(
            p for p in files(child(flavor.folder, "Logs")) if kind_of(p.name) == "combatlog"
        )

    cvars = (*IDENTITY_CVARS, *(args.blank_cvar or []))
    guids: set[bytes] = {g.encode() for g in args.own_guid or []}
    last_guid = re.compile(
        rb'^[ \t]*SET[ \t]+lastCharacterGuid[ \t]+"(Player-[0-9]+-[0-9A-Fa-f]+)"',
        re.IGNORECASE | re.MULTILINE,
    )
    for config in configs:
        guids.update(m.group(1) for m in last_guid.finditer(read_bytes(config)))
    own_names = {c.encode("utf-8") for c in characters}
    for log in logs:
        for m in GUID_NAME_RE.finditer(read_bytes(log, args.log_lines)):
            if m.group(2).split(b"-", 1)[0] in own_names:
                guids.add(m.group(1))

    return Identity(
        accounts=accounts,
        realms=realms,
        characters=characters,
        extras=args.extra_name or [],
        guids=guids,
        cvars=cvars,
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
    parser.add_argument("--platform", help="platform column (default: from this machine)")
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


def default_platform() -> str:
    return {"darwin": "macos", "win32": "windows", "cygwin": "windows"}.get(sys.platform, "linux")


def inside(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def run(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    out = args.out.resolve()
    if not root.is_dir():
        raise CaptureError(f"--root {args.root} is not a directory")
    if inside(out, root):
        raise CaptureError("--out is inside the install; the install is never written to (L1)")
    if inside(root, out):
        raise CaptureError("--out contains the install; choose a staging directory elsewhere")

    flavors = discover_flavors(root)
    if args.flavor:
        wanted = {f.casefold() for f in args.flavor}
        flavors = [f for f in flavors if f.name.casefold() in wanted]
    if not flavors:
        raise CaptureError("no flavor folder (a directory with .flavor.info) found under --root")

    platform = args.platform or default_platform()
    kinds: dict[str, str] = {}
    for pair in args.kind or []:
        folder, _, kind = pair.partition("=")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", kind) or folder not in {f.name for f in flavors}:
            raise CaptureError(f"--kind {pair!r}: expected <flavor folder>=<output folder>")
        kinds[folder] = kind
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
    refused = 0
    for item in planner.items.values():
        outcome = process(item, identity, kinds)
        shown = PurePosixPath(platform) / outcome.dest
        if outcome.problems:
            refused += 1
            print(f"REFUSED  {shown}: {'; '.join(outcome.problems)}")
            continue
        target = (out / platform).joinpath(*outcome.dest.parts)
        if not inside(target.resolve(), out) or inside(target.resolve(), root):
            refused += 1
            print(f"REFUSED  {shown}: destination escapes --out")
            continue
        if not args.dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(outcome.result.data)
        verb = "would write" if args.dry_run else "wrote"
        print(
            f"{verb:<8} {shown} ({len(outcome.result.data)} bytes, {len(outcome.result.edits)} edits)"
        )
        rows.append(provenance_row(outcome, platform, args.captured_by, args.consent))

    if planner.skipped:
        print("\ncandidates passed over by the automatic selection (would have been refused):")
        for line in dict.fromkeys(planner.skipped):
            print(f"  {line}")
    print("\nprovenance rows for lab/core/tests/fixtures/README.md:")
    for row in rows:
        print(row)
    if refused:
        print(f"\n{refused} file(s) REFUSED; nothing was written for them", file=sys.stderr)
        return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return run(args)
    except CaptureError as error:
        print(f"lab_capture: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
