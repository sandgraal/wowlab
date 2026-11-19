"""No crew record is tracked in the repository (M19-04).

`docs/LAB_PLAN.md` §20.5 term 3, ADR-0036 term 3: the lab-addon's crew book
(`WowLabCrewDB`) holds other players' names, realms, guilds and the player's
notes, and never enters the repository. Every tracked file is scanned, and
every member of a gzip stream or a zip archive in it (found by content, not
by suffix, and recursively), for the name in any ASCII casing. It may appear
only in:

- the addon and Lab sources (`lab/addon/`, `lab/<app>/src/`) and `docs/`;
- the capture tool that refuses it (`LAB_TOOL_SOURCES`);
- the labelled constructed test files of `CONSTRUCTED_TEST_FILES`.

In those test files every player-shaped key (a capitalised word, a hyphen
and a capitalised word, the client's key shape) and every bracketed string
key must come from `CONSTRUCTED_KEYS` or be one of the book's own field
names (`FIELD_KEYS`). A failure names the file and the line, never the key.

CONSTRUCTED INPUT. The planted files below are built in memory; nothing is
written to the tree. The planted real-looking key is assembled at run time,
so this file never holds it.
"""

from __future__ import annotations

import fnmatch
import gzip
import io
import re
import stat
import subprocess
import zipfile
from collections.abc import Iterator
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NAME_RE = re.compile(rb"wowlabcrewdb", re.IGNORECASE)
ALLOWED_PREFIXES = ("lab/addon/", "docs/")
SOURCE_GLOB = "lab/*/src/*"
LAB_TOOL_SOURCES = frozenset({"scripts/lab_capture.py"})
CONSTRUCTED_TEST_FILES = frozenset(
    {
        "tests/scripts/test_lab_capture_crew.py",
        "tests/repo/test_crew_book_out_of_repo.py",
    }
)
# The one fixed list of constructed player keys: invented and obviously so.
CONSTRUCTED_KEYS = frozenset(
    {
        "Inventeda-Fakerealm",
        "Inventedb-Fakerealm",
        "Inventedc-Otherfakerealm",
        "Inventedd-Otherfakerealm",
    }
)
# The book's own field names (§20.1), allowed as bracketed keys.
FIELD_KEYS = frozenset(
    {
        "schema",
        "on",
        "started",
        "probe",
        "loads",
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
    }
)
# A player key as the client writes it: name, hyphen, realm, both capitalised.
PLAYER_KEY_RE = re.compile(r"(?<![\w-])([^\W\d_][\w']*)-([^\W\d_]\w*)(?![\w-])")
# A bracketed string key as the client writes it: bracket, quote, key, quote,
# bracket, nothing between. A player key holds no comma, so text with one is
# taken for a Python list, not a key.
BRACKET_KEY_RE = re.compile(r"""\[(["'])([^"'\[\],\r\n]*)\1\]""")
MAX_UNPACKED = 512 * 1024 * 1024
MAX_NESTING = 4


def _members(label: str, data: bytes, nesting: int = 0) -> Iterator[tuple[str, bytes]]:
    """`data` itself, then every member of a gzip stream or zip archive it is, recursively."""
    yield label, data
    if nesting >= MAX_NESTING:
        return
    if data.startswith(b"\x1f\x8b"):
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
                unpacked = stream.read(MAX_UNPACKED + 1)
        except (OSError, EOFError):
            yield f"{label}!<unreadable gzip>", b"wowlabcrewdb (unreadable: fail closed)"
            return
        if len(unpacked) > MAX_UNPACKED:
            yield f"{label}!<too large>", b"wowlabcrewdb (too large: fail closed)"
            return
        yield from _members(f"{label}!gunzip", unpacked, nesting + 1)
    elif zipfile.is_zipfile(io.BytesIO(data)):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                for info in archive.infolist():
                    if info.file_size > MAX_UNPACKED:
                        yield f"{label}!<too large>", b"wowlabcrewdb (too large: fail closed)"
                        continue
                    member = archive.read(info)
                    yield from _members(f"{label}!{info.filename}", member, nesting + 1)
        except (OSError, zipfile.BadZipFile, EOFError):
            yield f"{label}!<unreadable zip>", b"wowlabcrewdb (unreadable: fail closed)"


def _name_allowed(path: str) -> bool:
    return (
        path.startswith(ALLOWED_PREFIXES)
        or fnmatch.fnmatch(path, SOURCE_GLOB)
        or path in LAB_TOOL_SOURCES
        or path in CONSTRUCTED_TEST_FILES
    )


def _key_problems(path: str, data: bytes) -> list[str]:
    text = data.decode("utf-8", errors="replace")
    problems: list[str] = []
    for number, line in enumerate(text.splitlines(), 1):
        for match in PLAYER_KEY_RE.finditer(line):
            first, second = match.group(1), match.group(2)
            if (
                first[0].isupper()
                and second[0].isupper()
                and match.group(0) not in CONSTRUCTED_KEYS
            ):
                problems.append(f"{path}:{number}: a player key not in CONSTRUCTED_KEYS")
        for match in BRACKET_KEY_RE.finditer(line):
            key = match.group(2)
            if key not in FIELD_KEYS and key not in CONSTRUCTED_KEYS:
                problems.append(f"{path}:{number}: a bracketed key not in the fixed lists")
    return problems


def scan(entries: Iterator[tuple[str, bytes]] | list[tuple[str, bytes]]) -> list[str]:
    """Every problem in (tracked path, bytes) pairs: never a key, only where."""
    problems: list[str] = []
    for path, data in entries:
        if not _name_allowed(path):
            for label, content in _members(path, data):
                if NAME_RE.search(content):
                    problems.append(f"{label}: names the crew book outside the allowed places")
        if path in CONSTRUCTED_TEST_FILES:
            problems.extend(_key_problems(path, data))
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


def test_the_fixed_files_exist() -> None:
    for path in (*LAB_TOOL_SOURCES, *CONSTRUCTED_TEST_FILES):
        assert (ROOT / path).is_file(), path


def test_the_constructed_keys_are_player_shaped() -> None:
    for key in CONSTRUCTED_KEYS:
        assert PLAYER_KEY_RE.fullmatch(key), key


# ─── the scan, shown failing on planted content (constructed, in memory) ─────

PLANTED_BOOK = b'\r\nWowLabCrewDB = {\r\n["schema"] = 1,\r\n}\r\n'
PLANTED_KEY = "Plantedname" + "-" + "Plantedrealm"  # real-looking, not in the list


def test_constructed_a_planted_file_fails() -> None:
    problems = scan([("lab/core/tests/fixtures/macos/planted.lua", PLANTED_BOOK)])
    assert problems == [
        "lab/core/tests/fixtures/macos/planted.lua: names the crew book outside the allowed places"
    ]


def test_constructed_a_planted_file_in_another_casing_fails() -> None:
    assert scan([("tests/planted.txt", PLANTED_BOOK.lower())])


def test_constructed_a_planted_gz_fails() -> None:
    planted = gzip.compress(PLANTED_BOOK)
    assert NAME_RE.search(planted) is None  # only the unpacked bytes hold the name
    problems = scan([("lab/core/tests/fixtures/wago/planted.json.gz", planted)])
    assert problems == [
        "lab/core/tests/fixtures/wago/planted.json.gz!gunzip: "
        "names the crew book outside the allowed places"
    ]


def test_constructed_a_planted_zip_member_and_nested_gz_fail() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("inner/WowLab.lua", PLANTED_BOOK)
        archive.writestr("inner/nested.gz", gzip.compress(PLANTED_BOOK))
    problems = scan([("tests/planted.bin", buffer.getvalue())])
    assert problems == [
        "tests/planted.bin!inner/WowLab.lua: names the crew book outside the allowed places",
        "tests/planted.bin!inner/nested.gz!gunzip: names the crew book outside the allowed places",
    ]


def test_constructed_an_unreadable_gz_fails_closed() -> None:
    assert scan([("tests/planted.gz", b"\x1f\x8b" + b"\x00" * 20)])


def test_constructed_a_planted_real_looking_key_fails() -> None:
    path = "tests/scripts/test_lab_capture_crew.py"
    original = (ROOT / path).read_bytes()
    assert scan([(path, original)]) == []
    as_player_key = original.replace(b"Inventeda-Fakerealm", PLANTED_KEY.encode(), 1)
    problems = scan([(path, as_player_key)])
    assert problems and all("a player key not in CONSTRUCTED_KEYS" in p for p in problems)
    assert all(PLANTED_KEY not in p for p in problems)  # where, never the key
    as_bracket_key = original.replace(b'["schema"]', b'["' + b"plantedkey" + b'"]', 1)
    assert [p for p in scan([(path, as_bracket_key)]) if "bracketed key" in p]


def test_constructed_the_allowed_places_are_not_flagged() -> None:
    for path in (
        "lab/addon/WowLab/Crew.lua",
        "lab/core/src/wowlab_core/crewbook.py",
        "docs/LAB_PLAN.md",
        "scripts/lab_capture.py",
    ):
        assert scan([(path, PLANTED_BOOK)]) == [], path
    # Not a source tree: a Lab app's tests are not allowed by the source rule.
    assert scan([("lab/core/tests/test_crewbook.py", PLANTED_BOOK)])
