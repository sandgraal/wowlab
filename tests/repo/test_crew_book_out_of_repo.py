"""No crew record is tracked in the repository (M19-04).

`docs/LAB_PLAN.md` §20.5 term 3, ADR-0036 term 3: the lab-addon's crew book
(`WowLabCrewDB`) holds other players' names, realms, guilds and the player's
notes, and never enters the repository. Every tracked file is scanned, and
every member of a gzip, bzip2 or xz stream or a zip archive in it (found by
content, not by suffix, and recursively), for the name in any ASCII casing,
in UTF-8 or UTF-16. A compressed signature the scan cannot unpack (zstd, 7z),
or a stream that will not unpack, fails closed. The name may appear only in:

- the addon's sources (`.lua` and `.toc` under `lab/addon/`) and the Lab's
  (`lab/<one folder>/src/`), matched by path segment;
- `docs/`, the rest of `lab/addon/`, the capture tool that refuses the book
  (`LAB_TOOL_SOURCES`) and the labelled constructed test files of
  `CONSTRUCTED_TEST_FILES`; in these, every `WowLabCrewDB =` assignment at
  column 0 must be one the capture tool accepts (the empty shape or the
  `= nil` line), so a real book pasted into one still fails;
- a real fixture with a provenance row in `lab/core/tests/fixtures/README.md`
  that the capture tool accepts as a whole (`crew_book_problems`), as the L8
  paragraph of §20.5 allows for an empty book.

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
import zipfile
from collections.abc import Iterable, Iterator
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]
CAPTURE_TOOL = ROOT / "scripts" / "lab_capture.py"
NAME_RE = re.compile(rb"wowlabcrewdb", re.IGNORECASE)
NAME_UTF16_RE = re.compile(rb"\x00".join(bytes([c]) for c in b"wowlabcrewdb"), re.IGNORECASE)
# An assignment at column 0, in any casing: checked in the allowed non-source files.
COLUMN_0_RE = re.compile(rb"(?:^|(?<=[\r\n]))wowlabcrewdb[ \t]*=", re.IGNORECASE)
LAB_TOOL_SOURCES = frozenset({"scripts/lab_capture.py"})
CONSTRUCTED_TEST_FILES = frozenset(
    {
        "tests/scripts/test_lab_capture_crew.py",
        "tests/repo/test_crew_book_out_of_repo.py",
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


def _members(label: str, data: bytes, nesting: int = 0) -> Iterator[tuple[str, bytes]]:
    """`data` itself, then every member of a compressed stream or archive it is, recursively."""
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
            yield from _members(f"{label}!gunzip", unpacked, nesting + 1)
        elif BZIP2_RE.match(data):
            yield from _members(
                f"{label}!bunzip2", _bounded(bz2.BZ2Decompressor(), data), nesting + 1
            )
        elif data.startswith(XZ_MAGIC):
            yield from _members(
                f"{label}!unxz", _bounded(lzma.LZMADecompressor(), data), nesting + 1
            )
        elif zipfile.is_zipfile(io.BytesIO(data)):
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                for info in archive.infolist():
                    if info.file_size > MAX_UNPACKED:
                        raise OSError("too large")
                    member = archive.read(info)
                    yield from _members(f"{label}!{info.filename}", member, nesting + 1)
    except (OSError, EOFError, ValueError, lzma.LZMAError, zipfile.BadZipFile):
        yield f"{label}!<cannot unpack>", FAIL_CLOSED


def _names_it(content: bytes) -> bool:
    return bool(NAME_RE.search(content) or NAME_UTF16_RE.search(content))


def _source(parts: list[str]) -> bool:
    """The addon's `.lua` and `.toc` files, and anything under `lab/<one folder>/src/`."""
    if len(parts) < 3 or parts[0] != "lab":
        return False
    if parts[1] == "addon":
        return parts[-1].casefold().endswith((".lua", ".toc"))
    return len(parts) > 3 and parts[2] == "src"


def _checked_place(path: str) -> bool:
    """Allowed to name the book, but not to hold one."""
    return (
        path.startswith(("docs/", "lab/addon/"))
        or path in LAB_TOOL_SOURCES
        or path in CONSTRUCTED_TEST_FILES
    )


def _indexed_fixtures(index: bytes) -> frozenset[str]:
    """The paths in the fixture index's first column (`| \\`path\\` | ...`)."""
    found = re.findall(rb"^\| `([^`]+)` \|", index, re.MULTILINE)
    return frozenset(FIXTURES + p.decode("utf-8", errors="replace") for p in found)


def _assignment_problems(path: str, data: bytes) -> list[str]:
    problems: list[str] = []
    for match in COLUMN_0_RE.finditer(data):
        start = match.start()
        exact = data.startswith(lab_capture.CREW_BOOK_NAME, start)
        if not (exact and lab_capture.crew_book_assignment_ok(data, start)):
            line = data.count(b"\n", 0, start) + 1
            problems.append(f"{path}:{line}: a crew book the capture tool would refuse")
    return problems


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


def scan(entries: Iterable[tuple[str, bytes]], index: bytes | None = None) -> list[str]:
    """Every problem in (tracked path, bytes) pairs: never a key, only where.

    `index` is the fixture index's text; None reads it from the tree."""
    if index is None:
        index = (ROOT / FIXTURE_INDEX).read_bytes()
    indexed = _indexed_fixtures(index)
    problems: list[str] = []
    for path, data in entries:
        parts = path.split("/")
        if _source(parts):
            continue
        if _checked_place(path):
            problems.extend(_assignment_problems(path, data))
            if path in CONSTRUCTED_TEST_FILES:
                problems.extend(_key_problems(path, data))
            continue
        for label, content in _members(path, data):
            if not _names_it(content):
                continue
            if label == path and path in indexed and not lab_capture.crew_book_problems(data):
                continue  # a real capture of an empty book, with its provenance row
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

PLANTED_BOOK = b'\r\nWowLabCrewDB = {\r\n["schema"] = 1,\r\n}\r\n'
PLANTED_FULL = (
    b'\r\nWowLabCrewDB = {\r\n["players"] = {\r\n["Inventeda-Fakerealm"] = {\r\n'
    b'["level"] = 60,\r\n},\r\n},\r\n}\r\n'
)
PLANTED_KEY = "Plantedname" + "-" + "Plantedrealm"  # real-looking, not in the list
NO_INDEX = b""


def test_constructed_a_planted_file_fails() -> None:
    problems = scan([("lab/core/tests/fixtures/macos/planted.lua", PLANTED_BOOK)], NO_INDEX)
    assert problems == [
        "lab/core/tests/fixtures/macos/planted.lua: names the crew book outside the allowed places"
    ]


def test_constructed_a_planted_file_in_another_casing_or_utf16_fails() -> None:
    assert scan([("tests/planted.txt", PLANTED_BOOK.lower())], NO_INDEX)
    for encoding in ("utf-16-le", "utf-16-be", "utf-16"):
        assert scan([("tests/planted.txt", PLANTED_FULL.decode().encode(encoding))], NO_INDEX)


def test_constructed_a_planted_src_folder_outside_a_source_tree_fails() -> None:
    for path in (
        "lab/core/tests/fixtures/macos/src/WowLab.lua",
        "lab/core/tests/src/WowLab.lua",
        "lab/addon/WowLab/tests/Book.txt",
    ):
        assert scan([(path, PLANTED_FULL)], NO_INDEX), path


def test_constructed_a_planted_gz_fails() -> None:
    planted = gzip.compress(PLANTED_BOOK)
    assert NAME_RE.search(planted) is None  # only the unpacked bytes hold the name
    problems = scan([("lab/core/tests/fixtures/wago/planted.json.gz", planted)], NO_INDEX)
    assert problems == [
        "lab/core/tests/fixtures/wago/planted.json.gz!gunzip: "
        "names the crew book outside the allowed places"
    ]


def test_constructed_planted_bzip2_and_xz_fail() -> None:
    assert scan([("tests/planted.bz2", bz2.compress(PLANTED_BOOK))], NO_INDEX) == [
        "tests/planted.bz2!bunzip2: names the crew book outside the allowed places"
    ]
    # xz may keep a short input's bytes as they are, so the raw file can fail too.
    xz_problems = scan([("tests/planted.xz", lzma.compress(PLANTED_BOOK))], NO_INDEX)
    assert "tests/planted.xz!unxz: names the crew book outside the allowed places" in xz_problems


def test_constructed_a_signature_it_cannot_unpack_fails_closed() -> None:
    for magic, kind in UNPACKABLE:
        problems = scan([("tests/planted.bin", magic + b"\x00" * 32)], NO_INDEX)
        assert problems == [
            f"tests/planted.bin!<{kind}, cannot unpack>: "
            "names the crew book outside the allowed places"
        ]


def test_constructed_a_planted_zip_member_and_nested_gz_fail() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("inner/WowLab.lua", PLANTED_BOOK)
        archive.writestr("inner/nested.gz", gzip.compress(PLANTED_BOOK))
    problems = scan([("tests/planted.bin", buffer.getvalue())], NO_INDEX)
    assert problems == [
        "tests/planted.bin!inner/WowLab.lua: names the crew book outside the allowed places",
        "tests/planted.bin!inner/nested.gz!gunzip: names the crew book outside the allowed places",
    ]


def test_constructed_an_unreadable_gz_fails_closed() -> None:
    assert scan([("tests/planted.gz", b"\x1f\x8b" + b"\x00" * 20)], NO_INDEX)


def test_constructed_a_planted_real_looking_key_fails() -> None:
    path = "tests/scripts/test_lab_capture_crew.py"
    original = (ROOT / path).read_bytes()
    assert scan([(path, original)], NO_INDEX) == []
    as_player_key = original.replace(b"Inventeda-Fakerealm", PLANTED_KEY.encode(), 1)
    problems = scan([(path, as_player_key)], NO_INDEX)
    assert problems and all("a player key not in CONSTRUCTED_KEYS" in p for p in problems)
    assert all(PLANTED_KEY not in p for p in problems)  # where, never the key
    as_bracket_key = original.replace(b'["schema"]', b'["' + b"plantedkey" + b'"]', 1)
    assert [p for p in scan([(path, as_bracket_key)], NO_INDEX) if "bracketed key" in p]


def test_constructed_a_full_book_in_a_doc_or_addon_file_fails() -> None:
    for path in ("docs/LAB_PLAN.md", "lab/addon/README.md", "scripts/lab_capture.py"):
        problems = scan([(path, b"prose\n" + PLANTED_FULL)], NO_INDEX)
        assert problems == [f"{path}:3: a crew book the capture tool would refuse"], path
    # Another casing at column 0 is refused there too.
    assert scan([("docs/x.md", PLANTED_BOOK.lower())], NO_INDEX)


def test_constructed_the_allowed_places_are_not_flagged() -> None:
    for path in (
        "lab/addon/WowLab/Crew.lua",
        "lab/addon/WowLab/WowLab.toc",
        "lab/core/src/wowlab_core/crewbook.py",
    ):
        assert scan([(path, PLANTED_FULL)], NO_INDEX) == [], path
    for path in ("docs/LAB_PLAN.md", "scripts/lab_capture.py", "lab/addon/README.md"):
        assert scan([(path, PLANTED_BOOK)], NO_INDEX) == [], path
        assert scan([(path, b"prose naming WowLabCrewDB inline\n")], NO_INDEX) == [], path
    # Not a source tree: a Lab app's tests are not allowed by the source rule.
    assert scan([("lab/core/tests/test_crewbook.py", PLANTED_BOOK)], NO_INDEX)


def test_constructed_an_indexed_real_capture_of_an_empty_book_is_allowed() -> None:
    path = FIXTURES + "macos-99999/forever/WTF/Account/X/SavedVariables/WowLab.lua"
    index = b"| `" + path[len(FIXTURES) :].encode() + b"` | savedvariables | ... |\n"
    nil_line = b"\r\nWowLabDB = {\r\n},\r\nWowLabCrewDB = nil\r\n"
    assert scan([(path, PLANTED_BOOK)], index) == []
    assert scan([(path, nil_line)], index) == []
    # The same path without its row, or with a record, still fails.
    assert scan([(path, PLANTED_BOOK)], NO_INDEX)
    assert scan([(path, PLANTED_FULL)], index)
    assert scan([(path + ".gz", gzip.compress(PLANTED_BOOK))], index)
