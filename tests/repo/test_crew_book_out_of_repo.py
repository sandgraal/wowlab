"""No crew record is tracked in the repository (M19-04).

`docs/LAB_PLAN.md` §20.5 term 3, ADR-0036 term 3: the lab-addon's crew book
(`WowLabCrewDB`) holds other players' names, realms, guilds and the player's
notes, and never enters the repository. Every tracked file, wherever it
lives, goes through one pipeline: every member of a gzip, bzip2 or xz stream
or a zip archive in it is unpacked (found by content, not by suffix, and
recursively; a signature it cannot unpack, zstd or 7z, or a stream that will
not unpack, fails closed), and each member is searched for the name in any
ASCII casing and in UTF-16. A compressed member or UTF-16 text naming the
book fails in every place. Plain text naming it is then judged by place:

- sources (`lab/addon/WowLab/*.lua` and `*.toc`, the `.py` files under
  `lab/<one folder>/src/`, by path segment), `docs/`, the rest of
  `lab/addon/`, the capture tool (`LAB_TOOL_SOURCES`) and the constructed
  test files (`CONSTRUCTED_TEST_FILES`): every assignment to the name,
  anywhere on a line and whatever precedes it, must be one the capture tool
  accepts (the empty shape or the `= nil` line), or a table whose every key,
  bracketed (either quote, any length) or bare, is in `CONSTRUCTED_KEYS` or
  `FIELD_KEYS`, with no guess at what a player key looks like; a `.toc`
  never assigns it. The constructed test files, Python source, are graded
  whole by the key check below instead;
- anywhere else: only a real fixture with a provenance row in
  `lab/core/tests/fixtures/README.md` that the capture tool accepts as a
  whole (`crew_book_problems`), as the L8 paragraph of §20.5 allows.

In the constructed test files every player-shaped key (a capitalised word,
a hyphen and a capitalised word, the client's key shape) and every bracketed
string key must come from `CONSTRUCTED_KEYS` or be one of the book's own
field names (`FIELD_KEYS`). A failure names the file and the line, never the
key.

CONSTRUCTED INPUT. The planted files below are built in memory; nothing is
written to the tree. The planted real-looking key is assembled at run time,
so this file never holds it.
"""

from __future__ import annotations

import bz2
import gzip
import importlib.util
import io
import lzma
import re
import stat
import subprocess
import sys
import time
import zipfile
from collections.abc import Iterable, Iterator
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
CAPTURE_TOOL = ROOT / "scripts" / "lab_capture.py"
NAME_RE = re.compile(rb"wowlabcrewdb", re.IGNORECASE)
NAME_UTF16_RE = re.compile(rb"\x00".join(bytes([c]) for c in b"wowlabcrewdb"), re.IGNORECASE)
# An assignment to the name, anywhere on a line and whatever precedes it
# (`_G.WowLabCrewDB = {`, `> WowLabCrewDB = {`, an indented block), in any casing.
ASSIGNMENT_RE = re.compile(rb"wowlabcrewdb\s*=(?!=)", re.IGNORECASE)
# Every place the name may be assigned: `= value` (whatever precedes the
# name, so `_G.WowLabCrewDB = {` counts), or, where the name starts a chain
# (not after a word character, `.` or `:`), followed by `.`, `[` or `:` on
# the same line (a field or index assignment, `WowLabCrewDB.players = {…}`).
SITE_RE = re.compile(
    rb"(?P<name>wowlabcrewdb)\s*(?P<assign>=)(?!=)"
    rb"|(?<![\w.:])(?P<iname>wowlabcrewdb)[ \t]*[.\[:]",
    re.IGNORECASE,
)
# An index path straight to `=`: `.field`, `:field` and `[…]` parts only. It
# is matched only up to the next site and at most MAX_INDEX_PATH bytes.
INDEX_PATH_RE = re.compile(
    rb"(?P<path>(?:[ \t]*(?:[.:][ \t]*[A-Za-z_]\w*|\[[^\]\r\n]*\]))+)[ \t]*=(?!=)"
)
# What a path looks like while it is still being written: used to tell a path
# cut off by the cap (fail closed) from prose (a mention).
INDEX_PATH_PREFIX_RE = re.compile(rb"(?:[ \t]*(?:[.:][ \t]*[A-Za-z_]\w*|\[[^\]\r\n]*\]?))*[ \t]*")
MAX_INDEX_PATH = 256
INDEX_PART_RE = re.compile(rb"[.:][ \t]*(?P<field>[A-Za-z_]\w*)|(?P<bracket>\[[^\]\r\n]*\])")
# After the assignment's `=`: a comment, a long string, a line break, or any
# other non-blank byte (`_value_end` walks the value with these).
VALUE_TOKEN_RE = re.compile(rb"--|\[=*\[|\r\n|[\r\n]|[^ \t\f\v]")
# Inside a book: comments, long strings, a bracket, quoted strings and braces.
BOOK_TOKEN_RE = re.compile(rb"--|\[=*\[|\[|[\"'{}]")
# Quoted strings, each quote read on its own, a backslash escaping the next byte.
QUOTED_STRING_RE = {
    b'"': re.compile(rb'"(?:[^"\\\r\n]|\\.)*"'),
    b"'": re.compile(rb"'(?:[^'\\\r\n]|\\.)*'"),
}
# A bracketed key: a quoted string (either quote, any length) or a number.
# Anything else in brackets (a computed key, a long string, an unterminated
# string) is not a plain key and counts against the book.
KEY_RE = re.compile(
    rb"\[\s*(?:\"(?P<double>(?:[^\"\\\r\n]|\\.)*)\""
    rb"|'(?P<single>(?:[^'\\\r\n]|\\.)*)'"
    rb"|(?P<number>-?[0-9][0-9.eExX+-]*))\s*\]"
)
# A bare `name =` key, in code.
NAMED_KEY_RE = re.compile(rb"(?<![\w.:])([A-Za-z_]\w*)[ \t]*=(?!=)")
LAB_TOOL_SOURCES = frozenset({"scripts/lab_capture.py"})
CONSTRUCTED_TEST_FILES = frozenset(
    {
        "tests/scripts/test_lab_capture_crew.py",
        "tests/repo/test_crew_book_out_of_repo.py",
        # M19-01: the constructed Crew.lua sources the addon grader checks.
        "tests/addon/_crew_constructed.py",
        # M19-02: the reader's constructed books and the tests that read them.
        "lab/core/tests/parser/_crew_build.py",
        "lab/core/tests/parser/test_crewbook_constructed.py",
        "lab/core/tests/test_cli_crew.py",
    }
)
FIXTURES = "lab/core/tests/fixtures/"
FIXTURE_INDEX = FIXTURES + "README.md"
# The one fixed list of constructed player keys: invented and obviously so.
CONSTRUCTED_KEYS = frozenset(
    {
        "Inventeda-Fakerealm",
        "Inventedb-Fakerealm",
        "Inventedc-Otherfakerealm",
        "Inventedd-Otherfakerealm",
    }
)
# The book's own field names (§20.1, and §13.1's probe), allowed as bracketed keys.
FIELD_KEYS = frozenset(
    {
        "schema",
        "on",
        "started",
        "probe",
        "loads",
        "lost",
        "skipped",
        "current",
        "players",
        "class",
        "level",
        "role",
        "guild",
        "met",
        "first",
        "last",
        "where",
        "where_id",
        "rating",
        "note",
        # Schema 2 (§21.2; M20-04): the per-player counters and fields, the
        # match list and its entries' fields, the once-per-match marker, the
        # near-full time and the scoreboard probe's block.
        "allied",
        "faced",
        "allied_won",
        "faced_won",
        "pvp_last",
        "pvp_map",
        "pvp_side",
        "matches",
        "at",
        "map",
        "map_id",
        "kind",
        "won",
        "team",
        "foes",
        "matched",
        "warned",
        "pvp",
    }
)
# A player key as the client writes it: name, hyphen, realm, both capitalised.
PLAYER_KEY_RE = re.compile(r"(?<![\w-])([^\W\d_][\w']*)-([^\W\d_][\w']*)(?![\w-])")
# In the constructed test files (Python source): a bracketed string key,
# bracket, quote, key, the same quote, bracket; each quote read on its own, so
# a key holding the other quote is seen whole. Text with a comma is taken for
# a Python list (`["--sv", "x"]`), not a key: a player key holds no comma.
BRACKET_KEY_RE = re.compile(r"""\[(?:"([^",\r\n]*)"|'([^',\r\n]*)')\]""")
MAX_UNPACKED = 512 * 1024 * 1024
MAX_NESTING = 4
# Members unpacked per tracked file, all archives in it together; past it, fail closed.
MAX_MEMBERS = 1000
# Compressed formats the standard library of 3.12 cannot unpack: fail closed.
UNPACKABLE = ((b"\x28\xb5\x2f\xfd", "zstd"), (b"7z\xbc\xaf\x27\x1c", "7z"))
BZIP2_RE = re.compile(rb"BZh[1-9]1AY&SY")
XZ_MAGIC = b"\xfd7zXZ\x00"
FAIL_CLOSED = b"wowlabcrewdb (fail closed)"


def _capture_tool() -> ModuleType:
    """The capture tool, loaded from its file once (as tests/scripts do)."""
    loaded = sys.modules.get("lab_capture")
    if loaded is not None:
        return loaded
    spec = importlib.util.spec_from_file_location("lab_capture", CAPTURE_TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look their module up here
    spec.loader.exec_module(module)
    return module


lab_capture = _capture_tool()


def _bounded(decompressor: bz2.BZ2Decompressor | lzma.LZMADecompressor, data: bytes) -> bytes:
    unpacked = decompressor.decompress(data, max_length=MAX_UNPACKED + 1)
    if len(unpacked) > MAX_UNPACKED:
        raise OSError("too large")
    return unpacked


def _members(
    label: str, data: bytes, nesting: int = 0, budget: list[int] | None = None
) -> Iterator[tuple[str, bytes]]:
    """`data` itself, then every member of a compressed stream or archive it is,
    recursively; at most MAX_MEMBERS for one tracked file, then fail closed."""
    if budget is None:
        budget = [MAX_MEMBERS]
    if budget[0] <= 0:
        yield f"{label}!<more than {MAX_MEMBERS} members>", FAIL_CLOSED
        return
    budget[0] -= 1
    yield label, data
    for magic, kind in UNPACKABLE:
        if data.startswith(magic):
            yield f"{label}!<{kind}, cannot unpack>", FAIL_CLOSED
            return
    if nesting >= MAX_NESTING:
        if data.startswith((b"\x1f\x8b", XZ_MAGIC)) or BZIP2_RE.match(data):
            yield f"{label}!<nested too deep>", FAIL_CLOSED
        return
    try:
        if data.startswith(b"\x1f\x8b"):
            with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
                unpacked = stream.read(MAX_UNPACKED + 1)
            if len(unpacked) > MAX_UNPACKED:
                raise OSError("too large")
            yield from _members(f"{label}!gunzip", unpacked, nesting + 1, budget)
        elif BZIP2_RE.match(data):
            yield from _members(
                f"{label}!bunzip2", _bounded(bz2.BZ2Decompressor(), data), nesting + 1, budget
            )
        elif data.startswith(XZ_MAGIC):
            yield from _members(
                f"{label}!unxz", _bounded(lzma.LZMADecompressor(), data), nesting + 1, budget
            )
        elif zipfile.is_zipfile(io.BytesIO(data)):
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                infos = archive.infolist()
                if len(infos) > budget[0]:
                    # Counted before anything is read: no member is unpacked.
                    yield f"{label}!<more than {MAX_MEMBERS} members>", FAIL_CLOSED
                    return
                for info in infos:
                    if info.file_size > MAX_UNPACKED:
                        raise OSError("too large")
                    member = archive.read(info)
                    yield from _members(f"{label}!{info.filename}", member, nesting + 1, budget)
    except (OSError, EOFError, ValueError, lzma.LZMAError, zipfile.BadZipFile):
        yield f"{label}!<cannot unpack>", FAIL_CLOSED


def _place(path: str) -> str:
    """Where a tracked path stands, by path segment:

    - `source`: the addon's own `lab/addon/WowLab/*.lua` and `*.toc`, and the
      `.py` files under `lab/<one folder>/src/`;
    - `checked`: `docs/`, every other file under `lab/addon/`, the capture tool;
    - `constructed`: the labelled constructed test files;
    - `other`: everything else."""
    parts = path.split("/")
    name = parts[-1].casefold()
    in_addon = parts[:3] == ["lab", "addon", "WowLab"] and len(parts) == 4
    if in_addon and name.endswith((".lua", ".toc")):
        return "source"
    if len(parts) > 3 and parts[0] == "lab" and parts[2] == "src" and name.endswith(".py"):
        return "source"
    if path in CONSTRUCTED_TEST_FILES:
        return "constructed"
    if path.startswith(("docs/", "lab/addon/")) or path in LAB_TOOL_SOURCES:
        return "checked"
    return "other"


def _indexed_fixtures(index: bytes) -> frozenset[str]:
    """The paths in the fixture index's first column (`| \\`path\\` | ...`)."""
    found = re.findall(rb"^\| `([^`]+)` \|", index, re.MULTILINE)
    return frozenset(FIXTURES + p.decode("utf-8", errors="replace") for p in found)


def _line(data: bytes, at: int) -> int:
    return data.count(b"\n", 0, at) + 1


def _value_end(data: bytes, start: int, limit: int) -> int | None:
    """End of the value assigned at `start` (just after its `=`), whatever
    form it takes (`{…}`, `({…})`, `nil or {…}`, a comment before the brace):
    the first line break at bracket depth 0 after something other than
    blanks and comments; strings and comments are skipped. A value never runs
    past `limit`, the next assignment site: None when its brackets, a string
    or a comment are still open there (the caller fails the file and stops,
    so no byte is walked twice)."""
    depth = 0
    seen = False
    at = start
    while (token := VALUE_TOKEN_RE.search(data, at, limit)) is not None:
        text = token.group(0)
        first = text[:1]
        at = token.end()
        if first in b"\r\n":
            if depth <= 0 and seen:
                return token.start()
            continue
        seen = seen or text != b"--"  # a comment alone is not yet the value
        if text == b"--":
            at = lab_capture._skip_comment(data, token.start())
        elif first == b"[" and len(text) > 1:
            at = lab_capture._skip_long(data, token.start())
        elif first in (b'"', b"'"):
            string = QUOTED_STRING_RE[first].match(data, token.start(), limit)
            at = string.end() if string else _end_of_line(data, token.start(), limit)
        elif first in b"{(":
            depth += 1
        elif first in b"})":
            depth -= 1
        if at < 0 or at > limit:
            return None
    return limit if depth <= 0 else None


_LINE_BREAK_RE = re.compile(rb"[\r\n]")


def _end_of_line(data: bytes, at: int, limit: int) -> int:
    """The next line break at or after `at`; past `limit` (limit + 1) when
    there is none before it, so a caller bounded by `limit` stops."""
    found = _LINE_BREAK_RE.search(data, at, limit)
    return limit + 1 if found is None else found.start()


def _index_path_problems(path_bytes: bytes) -> int:
    """How many parts of an index path (`.players`, `["players"]`, `[1]`) are
    not a fixed name."""
    bad = 0
    for part in INDEX_PART_RE.finditer(path_bytes):
        if part.group("field") is not None:
            bad += part.group("field").decode("ascii") not in FIELD_KEYS
            continue
        key = KEY_RE.fullmatch(part.group("bracket"))
        if key is None:
            bad += 1
        elif key.group("number") is None:
            quoted = key.group("double") if key.group("double") is not None else key.group("single")
            bad += quoted.decode("utf-8", errors="replace") not in FIELD_KEYS | CONSTRUCTED_KEYS
    return bad


def _book_key_problems(data: bytes, start: int, end: int) -> int:
    """How many table keys in `data[start:end]` are not a fixed name: a
    bracketed string key not in CONSTRUCTED_KEYS or FIELD_KEYS, whatever its
    shape, quoting or length; an unterminated or computed bracketed key; and
    a bare `name =` key not in FIELD_KEYS. Strings and comments are skipped."""
    bad = 0
    at = start
    while at < end:
        token = BOOK_TOKEN_RE.search(data, at, end)
        code_end = end if token is None else token.start()
        for named in NAMED_KEY_RE.finditer(data, at, code_end):
            if named.group(1).decode("ascii") not in FIELD_KEYS:
                bad += 1
        if token is None:
            break
        text = token.group(0)
        first = text[:1]
        at = token.end()
        if first == b"-":
            at = lab_capture._skip_comment(data, token.start())
        elif first == b"[" and len(text) > 1:
            at = lab_capture._skip_long(data, token.start())
        elif first == b"[":
            key = KEY_RE.match(data, token.start(), end)
            if key is None:
                bad += 1  # a computed, long-string or unterminated key
                at = _end_of_line(data, token.start(), end)
                continue
            if key.group("number") is None:
                quoted = (
                    key.group("double") if key.group("double") is not None else key.group("single")
                )
                if quoted.decode("utf-8", errors="replace") not in FIELD_KEYS | CONSTRUCTED_KEYS:
                    bad += 1
            at = key.end()
        elif first in (b'"', b"'"):
            string = QUOTED_STRING_RE[first].match(data, token.start(), end)
            at = string.end() if string else _end_of_line(data, token.start(), end)
        if at < 0:
            break
    return bad


def _assignment_problems(path: str, data: bytes) -> list[str]:
    """An assignment to the name, anywhere on a line and whatever precedes it,
    must be one the capture tool accepts (the empty shape or the nil line), or
    a table whose every key is a fixed name (CONSTRUCTED_KEYS or FIELD_KEYS).
    A `.toc` never assigns it."""
    problems: list[str] = []
    is_toc = path.casefold().endswith(".toc")
    sites: list[tuple[int, int, bytes]] = []  # (name start, value start, index path)
    candidates = list(SITE_RE.finditer(data))
    for number, match in enumerate(candidates):
        if match.group("assign") is not None:
            sites.append((match.start(), match.end(), b""))
            continue
        # The name then `.`, `[` or `:`: a statement only when an index path
        # of fields and bracketed keys runs straight to `=` on the same line.
        # Anything else after the name (prose, a call, a comparison) is a
        # mention, not an assignment site. The match never runs past the next
        # candidate or MAX_INDEX_PATH bytes, so no byte is read twice.
        after = match.end("iname")
        following = candidates[number + 1].start() if number + 1 < len(candidates) else len(data)
        bound = min(following, after + MAX_INDEX_PATH)
        chain = INDEX_PATH_RE.match(data, after, bound)
        if chain is not None:
            sites.append((match.start(), chain.end(), chain.group("path")))
            continue
        cut = bound == after + MAX_INDEX_PATH and bound < following
        prefix = INDEX_PATH_PREFIX_RE.match(data, after, bound)
        if cut and prefix is not None and prefix.end() == bound:
            line = data.count(b"\n", 0, match.start()) + 1
            problems.append(f"{path}:{line}: a crew-book index path over {MAX_INDEX_PATH} bytes")
            return problems  # fail closed, and stop
    line, counted = 1, 0
    for number, (start, value, index_path) in enumerate(sites):
        line += data.count(b"\n", counted, start)
        counted = start
        if is_toc:
            # A .toc names the variable on its SavedVariables line; it never assigns one.
            problems.append(f"{path}:{line}: a crew-book assignment in a .toc")
            continue
        exact = data.startswith(lab_capture.CREW_BOOK_NAME, start)
        if not index_path and exact and lab_capture.crew_book_assignment_ok(data, start):
            continue
        # Any other value, whatever its form, is checked: no form is "not a
        # table, therefore fine". It never runs past the next site.
        limit = sites[number + 1][0] if number + 1 < len(sites) else len(data)
        end = _value_end(data, value, limit)
        if end is None:
            problems.append(f"{path}:{line}: a crew-book value that never closes")
            return problems  # fail closed, and stop: one pass over the file
        if _index_path_problems(index_path) or _book_key_problems(data, value, end):
            problems.append(f"{path}:{line}: a crew book holding a key not in the fixed lists")
    return problems


def _key_problems(path: str, data: bytes) -> list[str]:
    text = data.decode("utf-8", errors="replace")
    problems: list[str] = []
    for number, line in enumerate(text.splitlines(), 1):
        for match in PLAYER_KEY_RE.finditer(line):
            first, second = match.group(1), match.group(2)
            shaped = not (first[0].islower() or second[0].islower())
            if shaped and match.group(0) not in CONSTRUCTED_KEYS:
                problems.append(f"{path}:{number}: a player key not in CONSTRUCTED_KEYS")
        for match in BRACKET_KEY_RE.finditer(line):
            key = match.group(1) if match.group(1) is not None else match.group(2)
            if key not in FIELD_KEYS and key not in CONSTRUCTED_KEYS:
                problems.append(f"{path}:{number}: a bracketed key not in the fixed lists")
    return problems


def scan(entries: Iterable[tuple[str, bytes]], index: bytes | None = None) -> list[str]:
    """Every problem in (tracked path, bytes) pairs: never a key, only where.

    One pipeline for every path: unpack every member, search each for the name
    in ASCII (any casing) and in UTF-16, then decide by place. A compressed
    member or UTF-16 text naming the book fails everywhere. Plain text naming
    it: a source, checked or constructed file passes the assignment check; any
    other file passes only as an indexed fixture the capture tool accepts
    whole. `index` is the fixture index's text; None reads it from the tree."""
    if index is None:
        index = (ROOT / FIXTURE_INDEX).read_bytes()
    indexed = _indexed_fixtures(index)
    problems: list[str] = []
    for path, data in entries:
        place = _place(path)
        if place == "constructed":
            problems.extend(_key_problems(path, data))
        for label, content in _members(path, data):
            if NAME_UTF16_RE.search(content):
                problems.append(f"{label}: names the crew book in UTF-16")
            if not NAME_RE.search(content):
                continue
            if label != path:
                problems.append(f"{label}: names the crew book inside a compressed member")
            elif place in ("source", "checked"):
                problems.extend(_assignment_problems(path, data))
            elif place == "constructed":
                continue  # graded whole by _key_problems above
            elif not (path in indexed and not lab_capture.crew_book_problems(data)):
                problems.append(f"{label}: names the crew book outside the allowed places")
    return problems


def _blob(sha: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(ROOT), "cat-file", "blob", sha], capture_output=True, check=True
    ).stdout


def tracked() -> list[tuple[str, bytes]]:
    """Every tracked path with its bytes: the working-tree file where it is a
    regular file, the indexed blob otherwise (a link, a deleted file)."""
    listing = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-s", "-z"], capture_output=True, check=True
    ).stdout
    entries: list[tuple[str, bytes]] = []
    for record in listing.split(b"\0"):
        if not record:
            continue
        meta, _, raw_path = record.partition(b"\t")
        mode, sha, _stage = meta.decode().split()
        path = raw_path.decode("utf-8", errors="surrogateescape")
        if mode == "160000":
            continue  # a gitlink has no content here
        on_disk = ROOT / path
        try:
            regular = stat.S_ISREG(on_disk.lstat().st_mode)
        except OSError:
            regular = False
        entries.append((path, on_disk.read_bytes() if regular else _blob(sha)))
    return entries


# ─── the repository ──────────────────────────────────────────────────────────


def test_no_crew_book_outside_the_allowed_places() -> None:
    entries = tracked()
    assert len(entries) > 100
    assert scan(entries) == []


def test_no_tracked_file_has_a_signature_the_scan_cannot_unpack() -> None:
    for path, data in tracked():
        for magic, kind in UNPACKABLE:
            assert not data.startswith(magic), (path, kind)


def test_the_fixed_files_exist() -> None:
    for path in (*LAB_TOOL_SOURCES, *CONSTRUCTED_TEST_FILES):
        assert (ROOT / path).is_file(), path


def test_the_constructed_keys_are_player_shaped() -> None:
    for key in CONSTRUCTED_KEYS:
        assert PLAYER_KEY_RE.fullmatch(key), key


# ─── the scan, shown failing on planted content (constructed, in memory) ─────

NAME = "WowLab" + "CrewDB"  # assembled, so these lines hold no assignment to scan
PLANTED_KEY = "Plantedname" + "-" + "Plantedrealm"  # real-looking, not in the list
PLANTED_BOOK = f'\r\n{NAME} = {{\r\n["schema"] = 1,\r\n}}\r\n'.encode()
# A full book whose only key is a constructed one, and the same with a real-looking key.
BOOK_LINES = (
    "{",
    '["players"] = {',
    '["Inventeda-Fakerealm"] = {',
    '["level"] = 60,',
    "},",
    "},",
    "}",
)
PLANTED_FULL = ("\r\n" + NAME + " = " + "\r\n".join(BOOK_LINES) + "\r\n").encode()
PLANTED_REAL = PLANTED_FULL.replace(b"Inventeda-Fakerealm", PLANTED_KEY.encode())
NO_INDEX = b""
OUTSIDE = "names the crew book outside the allowed places"


def _real_book_variants() -> dict[str, bytes]:
    """The reviewers' shapes of a pasted real book in a document (security, round 2)."""
    lines = PLANTED_REAL.decode().strip().split("\r\n")
    return {
        "indented one space": ("```\n" + "\n".join(" " + x for x in lines) + "\n```\n").encode(),
        "quoted with >": ("\n".join("> " + x for x in lines) + "\n").encode(),
        "_G. prefix": ("_G." + PLANTED_REAL.decode().strip() + "\n").encode(),
        "column 0": PLANTED_REAL,
        "another casing": PLANTED_REAL.replace(NAME.encode(), NAME.lower().encode()),
        "name and = on two lines": PLANTED_REAL.replace(b" = {", b"\r\n= {", 1),
    }


def test_constructed_a_planted_file_fails() -> None:
    problems = scan([("lab/core/tests/fixtures/macos/planted.lua", PLANTED_BOOK)], NO_INDEX)
    assert problems == [f"lab/core/tests/fixtures/macos/planted.lua: {OUTSIDE}"]


def test_constructed_a_planted_file_in_another_casing_fails() -> None:
    assert scan([("tests/planted.txt", PLANTED_BOOK.lower())], NO_INDEX)


def test_constructed_utf16_fails_in_every_place() -> None:
    for path in (
        "tests/planted.txt",
        "docs/a.md",
        "lab/addon/notes.txt",
        "lab/addon/WowLab/Crew.lua",
    ):
        for encoding in ("utf-16-le", "utf-16-be", "utf-16"):
            planted = PLANTED_FULL.decode().encode(encoding)
            assert f"{path}: names the crew book in UTF-16" in scan([(path, planted)], NO_INDEX)


def test_constructed_a_compressed_member_fails_in_every_place() -> None:
    planted = gzip.compress(PLANTED_FULL)
    for path in (
        "docs/a.gz",
        "lab/addon/notes.txt",
        "lab/addon/fixtures/WowLab.lua.gz",
        "lab/addon/WowLab/Crew.lua",
        "lab/core/src/wowlab_core/crewbook.py",
    ):
        assert scan([(path, planted)], NO_INDEX) == [
            f"{path}!gunzip: names the crew book inside a compressed member"
        ], path


def test_constructed_paths_that_are_not_sources_fail() -> None:
    for path in (
        "lab/core/tests/fixtures/macos/src/WowLab.lua",
        "lab/core/tests/src/WowLab.lua",
        "lab/newthing/src/WowLab.lua",
        "lab/core/src/wowlab_core/data/b.txt",
        "lab/core/tests/test_crewbook.py",
    ):
        assert scan([(path, PLANTED_FULL)], NO_INDEX) == [f"{path}: {OUTSIDE}"], path


def test_constructed_a_real_book_fails_in_addon_files_that_are_not_sources() -> None:
    for path in ("lab/addon/tests/fixtures/WowLab.lua", "lab/addon/README.md"):
        assert scan([(path, PLANTED_REAL)], NO_INDEX), path
        assert scan([(path, PLANTED_FULL)], NO_INDEX) == [], path  # control: constructed keys


def test_constructed_a_toc_never_assigns_the_book() -> None:
    path = "lab/addon/WowLab/Book.toc"
    assert scan([(path, PLANTED_FULL)], NO_INDEX) == [f"{path}:2: a crew-book assignment in a .toc"]
    saved_variables = f"## SavedVariables: WowLabDB, {NAME}\n".encode()
    assert scan([(path, saved_variables)], NO_INDEX) == []  # control


def test_constructed_a_real_book_fails_in_a_source_file() -> None:
    for path in ("lab/addon/WowLab/Crew.lua", "lab/core/src/wowlab_core/crewbook.py"):
        assert scan([(path, PLANTED_REAL)], NO_INDEX), path
        # Control: source code that names the variable and creates the empty table.
        code = f"local book = {NAME}\nif not {NAME} then\n  {NAME} = {{ schema = 1 }}\nend\n"
        assert scan([(path, code.encode())], NO_INDEX) == [], path


def test_constructed_a_pasted_real_book_fails_in_a_doc() -> None:
    for case, planted in _real_book_variants().items():
        problems = scan([("docs/a.md", b"prose\n" + planted)], NO_INDEX)
        assert problems, case
        assert all("a crew book holding a key not in the fixed lists" in p for p in problems)
        assert all(PLANTED_KEY not in p for p in problems)  # where, never the key


def _doc_book(*entries: str) -> bytes:
    """A book in a document, its `players` table holding `entries` (Lua text)."""
    lines = (f"{NAME} = {{", '["players"] = {', *entries, "},", "}")
    return ("prose\n```\n" + "\n".join(lines) + "\n```\n").encode()


# Written with `<`, `>` and `~` for `[`, `]` and `-`, translated at run time,
# so this file holds none of these keys for its own key check to find.
_LUA = str.maketrans("<>~", "[]-")
# Keys that do not fit any "looks like a player" guess (code and security
# review, round 3). Every one is invented; none is in CONSTRUCTED_KEYS.
FOREIGN_KEYS = {
    "a realm with a space": '<"Fakeperson~Fake Land"> = { <"note"> = "x", },',
    "a realm with a hyphen": '<"Fakeperson~Fake~Land"> = { <"note"> = "x", },',
    "a region suffix": '<"Fakeperson~Fakeland~EU"> = { <"note"> = "x", },',
    "lower case": '<"fakeperson~fakeland"> = { <"note"> = "x", },',
    "an apostrophe in double quotes": '<"Fakeperson~Kel\'Fakezad"> = { <"note"> = "x", },',
    "a double quote in single quotes": '<\'Fakeperson~"Fakeland\'> = { <"note"> = "x", },',
    "a name alone": '<"Fakeperson"> = true,',
    "an over-long key": '<"' + "Fakeperson" * 30 + '"> = true,',
    "an unterminated key": '<"Fakeperson~Fakeland> = true,',
    "a computed key": '<"Fake" .. "person"> = true,',
    "a long-string key": "< <<Fakeperson~Fakeland>> > = true,",
    "an escaped quote in a key": '<"Fake\\"person"> = true,',
    "a bare name key": "Fakeperson = true,",
    "a nested key under a field": '<"class"> = { <"Fakeperson~Fakeland"> = 1, },',
}


@pytest.mark.parametrize("case", sorted(FOREIGN_KEYS))
def test_constructed_a_book_key_not_in_the_fixed_lists_fails(case: str) -> None:
    planted = _doc_book(FOREIGN_KEYS[case].translate(_LUA))
    for path in ("docs/x.md", "lab/addon/README.md", "lab/addon/WowLab/Crew.lua"):
        problems = scan([(path, planted)], NO_INDEX)
        assert problems == [f"{path}:3: a crew book holding a key not in the fixed lists"], path


# Value forms other than a brace right after `=` (code re-check, round 3);
# the same `<`, `>`, `~` spelling as FOREIGN_KEYS.
VALUE_FORMS = {
    "a line comment before the brace": "= ~~ c\n{",
    "a long comment before the brace": "= ~~<<c>> {",
    "in parentheses": "= ({",
    "behind nil or": "= nil or {",
}


@pytest.mark.parametrize("case", sorted(VALUE_FORMS))
def test_constructed_any_value_form_is_checked(case: str) -> None:
    opener = VALUE_FORMS[case].translate(_LUA)
    closer = "})" if "(" in opener else "}"
    entry = '<"Fakeperson~Fake Land"> = { <"note"> = "x", },'.translate(_LUA)
    planted = f"prose\n```\n{NAME} {opener}\n{entry}\n{closer}\n```\n".encode()
    assert scan([("docs/x.md", planted)], NO_INDEX) == [
        "docs/x.md:3: a crew book holding a key not in the fixed lists"
    ]
    # Control: the same form holding only fixed names passes.
    fixed = entry.replace("Fakeperson~Fake Land".translate(_LUA), "Inventeda-Fakerealm")
    control = f"prose\n```\n{NAME} {opener}\n{fixed}\n{closer}\n```\n".encode()
    assert scan([("docs/x.md", control)], NO_INDEX) == []


# Field and index assignments (security check, round 4), same spelling.
FIELD_FORMS = {
    "a field after an empty book": (
        '{NAME} = {{}}\n{NAME}.players = {{<"Fakeperson~Fake Land"> = {{ <"note"> = "x" }}}}'
    ),
    "an index path holding the key": (
        '{NAME}<"players"><"Fakeperson~Fake Land"> = {{ <"note"> = "x" }}'
    ),
    "an index alone": '{NAME}<"players"> = {{ <"Fakeperson~Fake Land"> = true }}',
    "a field in another casing": '{lower}.players = {{ <"Fakeperson~Fake Land"> = true }}',
    "a field not on the lists": "{NAME}.friends = {{ }}",
    "spaces around the dot": '{NAME} . players = {{ <"Fakeperson~Fake Land"> = true }}',
}


@pytest.mark.parametrize("case", sorted(FIELD_FORMS))
def test_constructed_a_field_or_index_assignment_is_checked(case: str) -> None:
    text = FIELD_FORMS[case].format(NAME=NAME, lower=NAME.lower()).translate(_LUA)
    problems = scan([("docs/x.md", f"prose\n{text}\n".encode())], NO_INDEX)
    assert problems and all("not in the fixed lists" in p for p in problems), problems


def test_constructed_prose_and_fixed_field_assignments_pass() -> None:
    lines = (
        f'The addon keeps `{NAME}.players` and `{NAME}["current"]` per account.',
        f"Compare `{NAME}.on == true` with `{NAME}:Count()`; nothing is assigned.",
        f'{NAME}.players = {{ <"Inventeda~Fakerealm"> = {{ level = 60 }} }}'.translate(_LUA),
        f'{NAME}<"probe"><"loads"> = 3'.translate(_LUA),
    )
    planted = ("\n".join(lines) + "\n").encode()
    for path in ("docs/x.md", "lab/addon/README.md", "lab/addon/WowLab/Crew.lua"):
        assert scan([(path, planted)], NO_INDEX) == [], path


def test_constructed_an_index_path_over_the_cap_fails_closed() -> None:
    long_path = ".players" * (MAX_INDEX_PATH // 8 + 1)
    planted = f"prose\n{NAME}{long_path} = 1\n".encode()
    assert scan([("docs/x.md", planted)], NO_INDEX) == [
        "docs/x.md:2: a crew-book index path over " + f"{MAX_INDEX_PATH} bytes"
    ]
    # Control: a long sentence after a mention is prose, not a path.
    mention = f"prose\n`{NAME}.players` is {'very ' * 100}long prose = fine\n".encode()
    assert scan([("docs/x.md", mention)], NO_INDEX) == []
    # The name inside a dotted chain is not where a chain starts.
    assert scan([("docs/x.md", f"x.{NAME}.{NAME}.players\n".encode())], NO_INDEX) == []


def test_constructed_a_value_that_never_closes_fails_once_and_stops() -> None:
    planted = f"{NAME} = {{\n".encode() * 50
    assert scan([("docs/h.md", planted)], NO_INDEX) == [
        "docs/h.md:1: a crew-book value that never closes"
    ]
    unclosed_long = f"{NAME} = [[\n".encode() * 50
    assert scan([("docs/h.md", unclosed_long)], NO_INDEX) == [
        "docs/h.md:1: a crew-book value that never closes"
    ]


def _balanced_book(players: int) -> bytes:
    entry = '<"Inventeda~Fakerealm"> = { <"level"> = 60, <"note"> = "x" },'.translate(_LUA)
    return (
        f'{NAME} = {{\n<"players"> = {{\n'.translate(_LUA) + (entry + "\n") * players + "}\n}\n"
    ).encode()


def test_constructed_the_scan_is_linear_in_the_input() -> None:
    # Growth, not a budget: doubling the input may at most about double the
    # CPU time (best of three, with room for noise). Before the fix, one
    # assignment per line walked to the end of the file each time: 8.5 KB
    # took 1.46 s and 34 KB 22.88 s.
    for small, large in (
        (f"{NAME} = {{\n".encode() * 20_000, f"{NAME} = {{\n".encode() * 40_000),
        (f"{NAME} = nil\n".encode() * 20_000, f"{NAME} = nil\n".encode() * 40_000),
        (_balanced_book(10_000), _balanced_book(20_000)),
        # The name inside a dotted chain, and an index chain that never
        # closes (security check, round 5): 26 KB took 1.74 s before.
        (b"x" + f".{NAME}".encode() * 20_000 + b"\n", b"x" + f".{NAME}".encode() * 40_000 + b"\n"),
        (f"{NAME}[".encode() * 20_000, f"{NAME}[".encode() * 40_000),
        (f"{NAME}.".encode() * 20_000, f"{NAME}.".encode() * 40_000),
        (f'{NAME} = "'.encode() * 20_000, f'{NAME} = "'.encode() * 40_000),
    ):
        best: list[float] = []
        for data in (small, large):
            runs = []
            for _ in range(3):
                started = time.process_time()
                scan([("docs/h.md", data)], NO_INDEX)
                runs.append(time.process_time() - started)
            best.append(min(runs))
        first, second = best
        # The floor keeps scheduler noise on a few-millisecond scan from
        # failing it; a quadratic scan of the larger inputs takes minutes.
        assert second <= max(3 * first, 0.25), (first, second)


def test_constructed_a_book_of_fixed_names_passes() -> None:
    controls = (
        '<"Inventeda~Fakerealm"> = { <"class"> = "MAGE", <"note"> = "it\'s a test", },',
        "<'Inventedb~Fakerealm'> = { level = 60, role = \"HEALER\", },",
        '<"Inventedc~Otherfakerealm"> = { ~~ <"Fakeperson~Fakeland"> in a comment',
        '<"note"> = "a value holding <\\"Fakeperson~Fakeland\\"> is not a key", },',
        "<1> = true,",
    )
    planted = _doc_book(*(line.translate(_LUA) for line in controls))
    for path in ("docs/x.md", "lab/addon/README.md", "lab/addon/WowLab/Crew.lua"):
        assert scan([(path, planted)], NO_INDEX) == [], path
    # The specification's own example (§20.1) stays legal.
    plan = (ROOT / "docs" / "LAB_PLAN.md").read_bytes()
    assert scan([("docs/LAB_PLAN.md", plan)], NO_INDEX) == []


def test_constructed_too_many_archive_members_fail_closed() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for number in range(MAX_MEMBERS + 1):
            archive.writestr(f"m{number}.txt", b"x")
    path = "docs/many.zip"
    problems = scan([(path, buffer.getvalue())], NO_INDEX)
    assert problems == [
        path + f"!<more than {MAX_MEMBERS} members>: names the crew book inside a compressed member"
    ]
    # Control: fewer members are unpacked and hold nothing.
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for number in range(MAX_MEMBERS - 2):
            archive.writestr(f"m{number}.txt", b"x")
    assert scan([("docs/many.zip", buffer.getvalue())], NO_INDEX) == []


def test_constructed_the_member_cap_counts_nested_archives_together() -> None:
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as archive:
        for number in range(MAX_MEMBERS // 2 + 1):
            archive.writestr(f"m{number}.txt", b"x")
    outer = io.BytesIO()
    with zipfile.ZipFile(outer, "w") as archive:
        archive.writestr("a.zip", inner.getvalue())
        archive.writestr("b.zip", inner.getvalue())
    problems = scan([("docs/nested.zip", outer.getvalue())], NO_INDEX)
    assert any(f"more than {MAX_MEMBERS} members" in p for p in problems)


def test_constructed_doc_controls_pass() -> None:
    for planted in (
        PLANTED_BOOK,  # the empty shape
        f"\r\n{NAME} = nil\r\n".encode(),  # the nil line
        PLANTED_FULL,  # an example whose keys are all constructed
        f"prose naming {NAME} inline, and {NAME} == nil in a sentence\n".encode(),
    ):
        for path in ("docs/a.md", "scripts/lab_capture.py", "lab/addon/README.md"):
            assert scan([(path, planted)], NO_INDEX) == [], (path, planted)


def test_constructed_a_planted_gz_fails() -> None:
    planted = gzip.compress(PLANTED_BOOK)
    assert NAME_RE.search(planted) is None  # only the unpacked bytes hold the name
    problems = scan([("lab/core/tests/fixtures/wago/planted.json.gz", planted)], NO_INDEX)
    assert problems == [
        "lab/core/tests/fixtures/wago/planted.json.gz!gunzip: "
        "names the crew book inside a compressed member"
    ]


def test_constructed_planted_bzip2_and_xz_fail() -> None:
    assert scan([("tests/planted.bz2", bz2.compress(PLANTED_BOOK))], NO_INDEX) == [
        "tests/planted.bz2!bunzip2: names the crew book inside a compressed member"
    ]
    # xz may keep a short input's bytes as they are, so the raw file can fail too.
    xz_problems = scan([("tests/planted.xz", lzma.compress(PLANTED_BOOK))], NO_INDEX)
    assert "tests/planted.xz!unxz: names the crew book inside a compressed member" in xz_problems


def test_constructed_a_signature_it_cannot_unpack_fails_closed() -> None:
    for magic, kind in UNPACKABLE:
        for path in ("tests/planted.bin", "docs/planted.bin", "lab/addon/WowLab/Crew.lua"):
            problems = scan([(path, magic + b"\x00" * 32)], NO_INDEX)
            assert problems == [
                f"{path}!<{kind}, cannot unpack>: names the crew book inside a compressed member"
            ]


def test_constructed_a_planted_zip_member_and_nested_gz_fail() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("inner/WowLab.lua", PLANTED_BOOK)
        archive.writestr("inner/nested.gz", gzip.compress(PLANTED_BOOK))
    problems = scan([("docs/planted.bin", buffer.getvalue())], NO_INDEX)
    assert problems == [
        "docs/planted.bin!inner/WowLab.lua: names the crew book inside a compressed member",
        "docs/planted.bin!inner/nested.gz!gunzip: names the crew book inside a compressed member",
    ]


def test_constructed_an_unreadable_gz_fails_closed() -> None:
    assert scan([("tests/planted.gz", b"\x1f\x8b" + b"\x00" * 20)], NO_INDEX)


def test_constructed_a_planted_real_looking_key_fails() -> None:
    path = "tests/scripts/test_lab_capture_crew.py"
    original = (ROOT / path).read_bytes()
    assert scan([(path, original)], NO_INDEX) == []
    as_player_key = original.replace(b"Inventeda-Fakerealm", PLANTED_KEY.encode(), 1)
    problems = scan([(path, as_player_key)], NO_INDEX)
    assert problems and all("CONSTRUCTED_KEYS" in p for p in problems)
    assert all(PLANTED_KEY not in p for p in problems)  # where, never the key
    as_bracket_key = original.replace(b'["schema"]', b'["' + b"plantedkey" + b'"]', 1)
    assert [p for p in scan([(path, as_bracket_key)], NO_INDEX) if "bracketed key" in p]


def test_constructed_an_indexed_real_capture_of_an_empty_book_is_allowed() -> None:
    path = FIXTURES + "macos-99999/forever/WTF/Account/X/SavedVariables/WowLab.lua"
    index = b"| `" + path[len(FIXTURES) :].encode() + b"` | savedvariables | ... |\n"
    nil_line = f"\r\nWowLabDB = {{\r\n}},\r\n{NAME} = nil\r\n".encode()
    assert scan([(path, PLANTED_BOOK)], index) == []
    assert scan([(path, nil_line)], index) == []
    # The same path without its row, or with a record, still fails.
    assert scan([(path, PLANTED_BOOK)], NO_INDEX)
    assert scan([(path, PLANTED_FULL)], index)
    assert scan([(path + ".gz", gzip.compress(PLANTED_BOOK))], index)
