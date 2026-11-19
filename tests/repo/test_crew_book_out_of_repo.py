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
  accepts (the empty shape or the `= nil` line), or the file's every quoted
  or bracketed player-shaped key must be in `CONSTRUCTED_KEYS`; a `.toc`
  never assigns it;
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
import zipfile
from collections.abc import Iterable, Iterator
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]
CAPTURE_TOOL = ROOT / "scripts" / "lab_capture.py"
NAME_RE = re.compile(rb"wowlabcrewdb", re.IGNORECASE)
NAME_UTF16_RE = re.compile(rb"\x00".join(bytes([c]) for c in b"wowlabcrewdb"), re.IGNORECASE)
# An assignment to the name, anywhere on a line and whatever precedes it
# (`_G.WowLabCrewDB = {`, `> WowLabCrewDB = {`, an indented block), in any casing.
ASSIGNMENT_RE = re.compile(rb"wowlabcrewdb\s*=(?!=)", re.IGNORECASE)
# A quoted or bracketed string: its text is checked for a player key.
QUOTED_RE = re.compile(r"""(["'])([^"'\r\n]{1,64})\1""")
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
PLAYER_KEY_RE = re.compile(r"(?<![\w-])([^\W\d_][\w']*)-([^\W\d_][\w']*)(?![\w-])")
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


def _player_shaped(key: str) -> bool:
    """name-realm, each part starting with a letter that is not lower case
    (the client capitalises names; scripts without case count too)."""
    match = PLAYER_KEY_RE.fullmatch(key)
    return match is not None and not (match.group(1)[0].islower() or match.group(2)[0].islower())


def _foreign_quoted_keys(data: bytes) -> list[int]:
    """Lines holding a quoted or bracketed player-shaped key not in CONSTRUCTED_KEYS."""
    text = data.decode("utf-8", errors="replace")
    return [
        number
        for number, line in enumerate(text.splitlines(), 1)
        for match in QUOTED_RE.finditer(line)
        if _player_shaped(match.group(2)) and match.group(2) not in CONSTRUCTED_KEYS
    ]


def _assignment_problems(path: str, data: bytes) -> list[str]:
    """An assignment to the name anywhere on a line, whatever precedes it,
    must be one the capture tool accepts (the empty shape or the nil line),
    or stand in a file whose every quoted player key is a constructed one."""
    problems: list[str] = []
    foreign: list[int] | None = None
    is_toc = path.casefold().endswith(".toc")
    for match in ASSIGNMENT_RE.finditer(data):
        start = match.start()
        if is_toc:
            # A .toc names the variable on its SavedVariables line; it never assigns one.
            problems.append(f"{path}:{_line(data, start)}: a crew-book assignment in a .toc")
            continue
        exact = data.startswith(lab_capture.CREW_BOOK_NAME, start)
        if exact and lab_capture.crew_book_assignment_ok(data, start):
            continue
        if foreign is None:
            foreign = _foreign_quoted_keys(data)
        if foreign:
            problems.append(
                f"{path}:{_line(data, start)}: a crew book holding a key not in CONSTRUCTED_KEYS"
            )
    return problems


def _key_problems(path: str, data: bytes) -> list[str]:
    text = data.decode("utf-8", errors="replace")
    problems: list[str] = []
    for number, line in enumerate(text.splitlines(), 1):
        for match in PLAYER_KEY_RE.finditer(line):
            if _player_shaped(match.group(0)) and match.group(0) not in CONSTRUCTED_KEYS:
                problems.append(f"{path}:{number}: a player key not in CONSTRUCTED_KEYS")
        for match in BRACKET_KEY_RE.finditer(line):
            key = match.group(2)
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
            elif place != "other":
                problems.extend(_assignment_problems(path, data))
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
        assert all("a crew book holding a key not in CONSTRUCTED_KEYS" in p for p in problems)
        assert all(PLANTED_KEY not in p for p in problems)  # where, never the key


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
