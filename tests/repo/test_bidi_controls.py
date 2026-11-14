"""No tracked file holds a raw bidirectional control character (M11-37).

ruff's PLE2502 covers Python source, less U+200E LEFT-TO-RIGHT MARK, which
ruff leaves out on purpose. This covers every tracked file, the lab-addon Lua
and YAML, the docs and the harness included, for the 12 code points below:
the embeddings and overrides U+202A to U+202E, the isolates U+2066 to U+2069,
the marks U+200E and U+200F, and U+061C ARABIC LETTER MARK. The code points
are built with chr(), so this file holds none.

Each scanned file must be strict UTF-8 (round 3, security review): a byte
search for the UTF-8 forms misses a control written in another encoding, and
Python honours a coding declaration, so `# coding: utf-7` with `"+IC4-"`
decodes to U+202E and `# coding: cp1255` turns \\xfd and \\xfe into U+200E
and U+200F (ruff then skips the file and exits 0). So every scanned file
decodes as UTF-8 with no error, every Python source (a .py, .pyi or .pyw
suffix, or a python shebang) declares UTF-8 or nothing, and so does every
other scanned file whose first or second line holds a PEP 263 coding
declaration (round 4: a script run as `uv run --script`, or with no shebang
at all, is Python too). The raw bytes and the decoded text are both
searched: a NUL or a stray byte elsewhere in a file cannot hide a control,
it fails the file. Every tracked file name is checked too.

A symlink is scanned as what git commits for it, its target text (M16-104).
The index is listed with `git ls-files -s`; for a mode-120000 entry the
committed blob is read with `git cat-file --batch`, and the link on disk is
never read or followed: a broken link is not skipped, a working one does
not stand in for the file it points at, and an unstaged retarget does not
hide the committed target. A regular entry is read from the working tree
only when lstat finds a regular file there under real directories; where it
is deleted, or replaced by a link or anything else, or a parent directory is
a link, its committed blob is read instead. A gitlink (a submodule) has no
content here, so only its name is checked.

Nothing is skipped for its content or its size: pre-commit keeps every
tracked file outside the fixtures under 512 KB (cli.py under 1 MB), so each is
read whole. Under
lab/core/tests/fixtures/ only the real captures are left out, by type
(`CAPTURE_SUFFIXES`): they are the client's bytes, and a capture may hold a
control, or a byte that is not UTF-8, legitimately. Anything else there is
scanned, the fixture index README.md included, and a Python source, a
Markdown file other than README.md, or a `test*.txt` file (any case) there
fails outright: pytest would collect the first and the last (a `test*.txt`
is a doctest file to it), and the other is prose written here. pytest is
also kept out of the fixtures (`norecursedirs` in pyproject.toml, round 4);
the fixture suites live beside it and read it as data.

Outside the fixtures, only the eight pinned font files of ADR-0035 are left
out, by exact path (`PINNED_FONTS`, M16-51, M18-03): they are binary fonts, held to
their SHA-256 by lab/core/tests/test_ui_fonts.py. Any other file beside them,
another `.woff2` included, is scanned.
"""

from __future__ import annotations

import fnmatch
import io
import os
import re
import shutil
import stat
import subprocess
import sys
import tokenize
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BIDI = tuple(
    chr(code) for code in (*range(0x202A, 0x202F), *range(0x2066, 0x206A), 0x200E, 0x200F, 0x061C)
)
FIXTURES = "lab/core/tests/fixtures/"
FIXTURE_INDEX = FIXTURES + "README.md"
# The capture types under the fixtures (lab/core/tests/fixtures/README.md).
CAPTURE_SUFFIXES = frozenset(
    {".lua", ".wtf", ".txt", ".csv", ".json", ".gz", ".info", ".html", ".toc", ".bak"}
)
# The eight WOFF2 files of ADR-0035 (M16-51; Inter and Sora since its
# amendment of 2026-11-14, M18-03) are compressed binary fonts,
# never text, copied unchanged from pinned URLs; their bytes are held to
# their SHA-256 by lab/core/tests/test_ui_fonts.py, which also fails on any
# other file in their folder, so a changed byte fails there. Only these exact
# paths are left out; their licence texts beside them are scanned.
PINNED_FONTS = frozenset(
    "lab/core/src/wowlab_core/ui/templates/fonts/" + name + ".woff2"
    for name in (
        "ibm-plex-mono-400-latin",
        "ibm-plex-mono-400-latin-ext",
        "ibm-plex-mono-500-latin",
        "ibm-plex-mono-500-latin-ext",
        "inter-latin",
        "inter-latin-ext",
        "sora-latin",
        "sora-latin-ext",
    )
)
# The git index mode of a symlink, whose blob is the link's target text.
SYMLINK = b"120000"
# The mode of a gitlink (a submodule commit), which has no blob.
GITLINK = b"160000"
PYTHON_SUFFIXES = frozenset({".py", ".pyi", ".pyw"})
PYTHON_ENCODINGS = frozenset({"utf-8", "utf-8-sig"})
# PEP 263: a coding declaration on the first or second line.
CODING = re.compile(rb"^[ \t\f]*#.*?coding[:=][ \t]*([-\w.]+)")


def _bidi_in_bytes(data: bytes) -> list[str]:
    """The code points whose UTF-8 encoding appears anywhere in `data`."""
    return sorted(f"U+{ord(ch):04X}" for ch in BIDI if ch.encode("utf-8") in data)


def _bidi_in_text(text: str) -> list[str]:
    return sorted(f"U+{ord(ch):04X}" for ch in BIDI if ch in text)


def _bidi_in_file(path: Path) -> list[str]:
    """The code points in a file's raw bytes or, where it decodes, its UTF-8
    text (a decoding error is `_problems`'s to report)."""
    return [hit for hit in _problems(path.name, path.read_bytes()) if hit.startswith("U+")]


def _python(name: str, data: bytes) -> bool:
    """Is this a Python source: by suffix, or by a python shebang?"""
    if Path(name).suffix.lower() in PYTHON_SUFFIXES:
        return True
    first = data.split(b"\n", 1)[0]
    return first.startswith(b"#!") and b"python" in first


def _declares_coding(data: bytes) -> bool:
    """Does the first or second line hold a PEP 263 coding declaration?"""
    return any(CODING.match(line) for line in data.split(b"\n", 2)[:2])


def _problems(name: str, data: bytes) -> list[str]:
    """Why a scanned file fails: the controls found in its raw bytes or its
    decoded text, a decoding error, or a coding declaration other than UTF-8
    (in a Python source, or in any file that declares one)."""
    found = set(_bidi_in_bytes(data))
    out = []
    try:
        found.update(_bidi_in_text(data.decode("utf-8")))
    except UnicodeDecodeError as err:
        out.append(f"not UTF-8 (byte {err.start})")
    if _python(name, data) or _declares_coding(data):
        try:
            encoding = tokenize.detect_encoding(io.BytesIO(data).readline)[0]
        except SyntaxError as err:
            out.append(f"a coding declaration Python refuses ({err.msg})")
        else:
            if encoding not in PYTHON_ENCODINGS:
                out.append(f"declares the encoding {encoding}")
    return sorted(found) + out


def _git() -> str:
    git = shutil.which("git")
    if git is None:
        pytest.skip("git is not available")
    return git


def _run_git(
    root: Path, *args: str, stdin: bytes | None = None
) -> subprocess.CompletedProcess[bytes]:
    """git in `root`, with every GIT_* variable cleared so the repository is
    the one found from `root`, never one named by the environment."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    return subprocess.run(
        [_git(), *args], cwd=root, input=stdin, capture_output=True, check=False, env=env
    )


def _entries(root: Path) -> list[tuple[bytes, bytes, bytes]]:
    """Each index entry of the checkout at `root` as (mode, blob id, name),
    from `git ls-files -s -z` (M16-104: the mode says which entry is a
    symlink)."""
    done = _run_git(root, "ls-files", "-s", "-z")
    if done.returncode != 0:
        pytest.skip("not a git checkout")
    out = []
    for record in done.stdout.split(b"\0"):
        if not record:
            continue
        meta, _, name = record.partition(b"\t")
        mode, blob, _stage = meta.split(b" ")
        out.append((mode, blob, name))
    return out


def _blobs(root: Path, ids: list[bytes]) -> dict[bytes, bytes]:
    """The content of each blob id, from one `git cat-file --batch` call.
    A missing blob fails loudly: nothing in the index goes unscanned."""
    if not ids:
        return {}
    done = _run_git(root, "cat-file", "--batch", stdin=b"".join(i + b"\n" for i in ids))
    assert done.returncode == 0, done.stderr
    out: dict[bytes, bytes] = {}
    data, pos = done.stdout, 0
    for blob in ids:
        end = data.index(b"\n", pos)
        header = data[pos:end].split(b" ")
        assert len(header) == 3 and header[:2] == [blob, b"blob"], data[pos:end]
        size = int(header[2])
        out[blob] = data[end + 1 : end + 1 + size]
        pos = end + 1 + size + 1  # the content, then a newline
    return out


def _on_disk(root: Path, name: str, dirs: dict[Path, bool]) -> Path | None:
    """The working-tree file for a regular entry, when it is one: a regular
    file by lstat under real directories only, so no link, as the entry or as
    any parent directory, is followed. None where the entry is deleted or
    replaced; its committed blob is read instead. `dirs` caches the checks."""
    path = root / name
    for parent in reversed(path.relative_to(root).parents[:-1]):
        where = root / parent
        if where not in dirs:
            try:
                dirs[where] = stat.S_ISDIR(where.lstat().st_mode)
            except OSError:
                dirs[where] = False
        if not dirs[where]:
            return None
    try:
        regular = stat.S_ISREG(path.lstat().st_mode)
    except OSError:
        return None
    return path if regular else None


def _scanned(name: str) -> bool:
    """Is the file scanned (everything but a capture under the fixtures and
    the eight pinned font files)?"""
    if name in PINNED_FONTS:
        return False
    return not name.startswith(FIXTURES) or Path(name).suffix.lower() not in CAPTURE_SUFFIXES


def _misplaced(name: str) -> bool:
    """Is it a Python source, Markdown other than the index, or a file pytest
    reads as doctests (`test*.txt`, any case), under the fixtures?"""
    if not name.startswith(FIXTURES):
        return False
    suffix = Path(name).suffix.lower()
    return (
        suffix in PYTHON_SUFFIXES
        or (suffix == ".md" and name != FIXTURE_INDEX)
        or fnmatch.fnmatchcase(Path(name).name.lower(), "test*.txt")
    )


def _scan_checkout(root: Path) -> tuple[int, dict[str, list[str]]]:
    """Scan every index entry of the checkout at `root`: the number of
    entries, and the findings by name. No link is ever followed (M16-104): a
    symlink's committed blob, its target text, is scanned; so is the blob of
    a regular entry that is deleted or no longer a regular file in the
    working tree. A gitlink (a submodule) has no content here; only its name
    is checked."""
    found: dict[str, list[str]] = {}
    entries = _entries(root)
    from_blob: list[tuple[str, bytes, str]] = []
    dirs: dict[Path, bool] = {}
    for mode, blob, raw in entries:
        try:
            name = raw.decode("utf-8")
        except UnicodeDecodeError:
            found[f"file name {raw!r}"] = ["not UTF-8"]
            name = raw.decode("utf-8", "surrogateescape")
        if hits := _bidi_in_bytes(raw):
            found[f"file name {raw!r}"] = hits
        if _misplaced(name):
            found[repr(name)] = ["a Python, Markdown or test*.txt file among the captures"]
            continue
        if mode == GITLINK:
            continue
        if mode == SYMLINK:
            # Its committed bytes are the target text, whatever its name or
            # place: a symlink is never a capture or a pinned font.
            from_blob.append((name, blob, " (symlink target)"))
            continue
        if not _scanned(name):
            continue
        path = _on_disk(root, name, dirs)
        if path is None:
            from_blob.append((name, blob, " (committed blob)"))
        elif problems := _problems(name, path.read_bytes()):
            found[repr(name)] = problems
    contents = _blobs(root, sorted({blob for _, blob, _ in from_blob}))
    for name, blob, label in from_blob:
        if problems := _problems(name, contents[blob]):
            found[repr(name) + label] = problems
    return len(entries), found


def test_no_tracked_file_holds_a_bidi_control() -> None:
    count, found = _scan_checkout(ROOT)
    assert count, "git listed no tracked files"
    assert not found, found


def test_bidi_check_constructed_controls(tmp_path: Path) -> None:
    """Constructed inputs: every code point is found, beside a NUL and a byte
    that is not UTF-8 too (which now fails the file on its own); a clean file
    and the fixture rules behave."""
    assert len(set(BIDI)) == 12
    lines = b"".join(f"-- {ch} UnitName\n".encode() for ch in BIDI)
    problems = _problems("Constructed.lua", b"-- \x00\n-- caf\xe9\n" + lines)
    assert problems[:12] == sorted(f"U+{ord(ch):04X}" for ch in BIDI)
    assert problems[12:] == ["not UTF-8 (byte 11)"]
    assert _problems("Constructed.lua", lines)[:12] == problems[:12]
    assert _problems("Clean.lua", b"-- plain text \x00 caf\xc3\xa9\n") == []
    assert _problems("Clean.lua", b"-- plain text \xff\x00\n") == ["not UTF-8 (byte 14)"]
    assert _bidi_in_bytes(f"docs/a{BIDI[4]}b.md".encode()) == ["U+202E"]
    assert _scanned(FIXTURE_INDEX)
    assert _scanned("docs/LAB_PLAN.md")
    for capture in ("wago/x.csv", "wago/x.json.gz", "SavedVariables/WowLab.lua.bak", "a.WTF"):
        assert not _scanned(FIXTURES + capture), capture
    assert _scanned(FIXTURES + "notes.md")
    assert _scanned(FIXTURES + "conftest.py")
    fonts = "lab/core/src/wowlab_core/ui/templates/fonts/"
    assert len(PINNED_FONTS) == 8
    assert not _scanned(fonts + "inter-latin.woff2")
    assert _scanned(fonts + "OFL-Inter.txt")
    assert _scanned(fonts + "planted.woff2")
    assert _scanned(fonts + "alegreya-latin.woff2")
    assert _scanned(fonts.upper() + "INTER-LATIN.WOFF2")
    assert _misplaced(FIXTURES + "notes.md")
    assert _misplaced(FIXTURES + "wago/test_x.py")
    assert not _misplaced(FIXTURE_INDEX)
    assert not _misplaced("lab/core/tests/test_x.py")


def test_constructed_utf7_python_source_fails() -> None:
    """Constructed input (security review, round 3): Python decodes "+IC4-"
    under `# coding: utf-7` as U+202E, though every byte is ASCII."""
    data = b'# coding: utf-7\nx = "+IC4-"\n'
    assert data.decode("utf-7").count(BIDI[4]) == 1  # the premise
    assert _bidi_in_bytes(data) == []  # what the byte search alone saw
    assert _problems("constructed_utf7.py", data) == ["declares the encoding utf-7"]


def test_constructed_cp1255_python_source_fails() -> None:
    """Constructed input (security review, round 3): under `# coding: cp1255`
    the bytes \\xfd and \\xfe are U+200E and U+200F."""
    data = b'# -*- coding: cp1255 -*-\nx = "\xfd\xfe"\n'
    assert data.decode("cp1255").count(chr(0x200E)) == 1  # the premise
    assert _bidi_in_bytes(data) == []
    assert _problems("constructed_cp1255.py", data) == [
        "not UTF-8 (byte 30)",
        "declares the encoding cp1255",
    ]


def test_constructed_utf16_markdown_fails() -> None:
    """Constructed input (security review, round 3): a UTF-16 Markdown file
    with a byte-order mark holds U+202E as the bytes 2E 20, never E2 80 AE."""
    data = f"# notes {BIDI[4]}\n".encode("utf-16")
    assert data[:2] in {b"\xff\xfe", b"\xfe\xff"}  # the premise: a BOM
    assert _bidi_in_bytes(data) == []
    assert _problems("docs/constructed.md", data) == ["not UTF-8 (byte 0)"]


def test_constructed_python_shebang_without_suffix_is_python() -> None:
    """Constructed input: a script run by python without a .py suffix is
    held to the same coding rule."""
    data = b"#!/usr/bin/env python3\n# coding: utf-7\n"
    assert _problems("scripts/constructed", data) == ["declares the encoding utf-7"]
    # Round 4: a declaration is checked in any file, a shell script included.
    sh = b"#!/bin/sh\n# coding: utf-7\n"
    assert _problems("scripts/constructed.sh", sh) == ["declares the encoding utf-7"]
    assert _problems("scripts/constructed.sh", b"#!/bin/sh\necho hi\n") == []


def test_constructed_uv_script_without_suffix_is_checked() -> None:
    """Constructed input (security review, round 4): a script with no suffix
    run by `uv run --script` is Python, and its shebang names no python."""
    data = b'#!/usr/bin/env -S uv run --script\n# coding: utf-7\nx = "+IC4-"\n'
    assert not _python("scripts/constructed", data)  # what round 3 missed
    assert _problems("scripts/constructed", data) == ["declares the encoding utf-7"]


def test_constructed_coding_declaration_without_shebang_is_checked() -> None:
    """Constructed input (security review, round 4): no suffix, no shebang,
    a coding declaration on the first line."""
    data = b'# -*- coding: utf-7 -*-\nx = "+IC4-"\n'
    assert not _python("tools/constructed", data)
    assert _problems("tools/constructed", data) == ["declares the encoding utf-7"]
    # A declaration of UTF-8, or none, passes; so does one Python ignores
    # (on the second line after a line of code).
    assert _problems("tools/constructed", b"# coding: utf-8\nx = 1\n") == []
    assert _problems("tools/constructed", b"x = 1\n# coding: utf-7\n") == []


def test_constructed_doctest_file_under_the_fixtures_fails() -> None:
    """Constructed names (security review, round 4): pytest collects
    `test*.txt` as doctests, so none may sit among the captures."""
    for name in ("test_x.txt", "wago/test.txt", "TEST_Notes.TXT", "Test_x.txt"):
        assert _misplaced(FIXTURES + name), name
    for name in ("SavedVariables/WowLab.txt", "wago/latest.txt", "contest.txt"):
        assert not _misplaced(FIXTURES + name), name
    assert not _misplaced("docs/test_x.txt")


def test_pytest_never_collects_under_the_fixtures(tmp_path: Path) -> None:
    """Constructed tree (security review, round 4): a doctest file planted
    under lab/core/tests/fixtures/ is not collected with the repository's
    pytest config, and is collected once that directory is allowed (the
    control), so the config is what keeps it out."""
    planted = tmp_path / "lab" / "core" / "tests" / "fixtures" / "wago" / "test_planted.txt"
    planted.parent.mkdir(parents=True)
    planted.write_text(">>> 1 + 1\n2\n", encoding="utf-8")
    beside = tmp_path / "lab" / "core" / "tests" / "test_beside.txt"
    beside.write_text(">>> 2 + 2\n4\n", encoding="utf-8")

    def collected(*extra: str) -> list[str]:
        done = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "--collect-only",
                "-q",
                "-p",
                "no:cacheprovider",
                "-c",
                str(ROOT / "pyproject.toml"),
                "--rootdir",
                str(tmp_path),
                *extra,
                str(tmp_path / "lab" / "core" / "tests"),
            ],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            check=False,
        )
        assert done.returncode == 0, done.stdout + done.stderr
        return [line for line in done.stdout.splitlines() if "test_" in line and ".txt" in line]

    items = collected()
    assert any("test_beside.txt" in line for line in items), items  # doctests are collected
    assert not any("fixtures" in line for line in items), items
    allowed = collected("-o", "norecursedirs=.venv")
    assert any("fixtures/wago/test_planted.txt" in line for line in allowed), allowed


def _git_tree(root: Path, links: dict[str, str], files: dict[str, str] | None = None) -> None:
    """A constructed git repository at `root` with the given symlinks (name
    to target text) and regular files (name to text) added to its index, git
    run with GIT_* cleared. Skips where symlinks cannot be made (Windows
    without the privilege, or a file system without them) or git would not
    record them as links (core.symlinks false)."""
    assert _run_git(root, "init", "-q", str(root)).returncode == 0
    if _run_git(root, "config", "--bool", "core.symlinks").stdout.strip() == b"false":
        pytest.skip("git has core.symlinks false here, so it checks out no symlinks")
    for name, text in (files or {}).items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")
    for name, target in links.items():
        try:
            (root / name).symlink_to(target)
        except (OSError, NotImplementedError) as err:
            pytest.skip(f"symlinks cannot be made here: {err}")
    added = _run_git(root, "add", "--", *links, *(files or {}))
    assert added.returncode == 0, added.stderr


def test_constructed_broken_symlink_target_is_scanned(tmp_path: Path) -> None:
    """Constructed tree (M16-104): git commits a symlink's target text, so a
    broken link whose target holds U+202E is reported (path.is_file() was
    false for it, and the scan skipped it). Control: a broken link with a
    clean target passes."""
    _git_tree(tmp_path, {"notes.md": f"a{BIDI[4]}b.md", "clean.md": "missing.md"})
    for name in ("notes.md", "clean.md"):
        link = tmp_path / name
        assert link.is_symlink() and not link.exists(), name  # the premise: broken
    modes = {name: mode for mode, _, name in _entries(tmp_path)}
    assert modes == {b"notes.md": SYMLINK, b"clean.md": SYMLINK}
    assert _scan_checkout(tmp_path) == (2, {"'notes.md' (symlink target)": ["U+202E"]})


def test_constructed_working_symlink_to_a_clean_file_is_scanned(tmp_path: Path) -> None:
    """Constructed tree (M16-104): a working link to a clean file, whose
    target text holds U+2069, is reported (the scan followed the link and
    read only the clean file). Control: a working link with a clean target
    text to the same file passes."""
    clean = f"clean{BIDI[8]}.md"
    assert BIDI[8] == chr(0x2069)
    (tmp_path / clean).write_text("plain\n", encoding="utf-8")
    (tmp_path / "plain.md").write_text("plain\n", encoding="utf-8")
    _git_tree(tmp_path, {"notes.md": clean, "control.md": "plain.md"})
    assert (tmp_path / "notes.md").is_file()  # the premise: it resolves
    assert (tmp_path / "control.md").is_file()
    assert _scan_checkout(tmp_path) == (2, {"'notes.md' (symlink target)": ["U+2069"]})


def test_constructed_working_symlink_is_not_followed(tmp_path: Path) -> None:
    """Constructed tree (M16-104): a working link is never followed. The file
    it points at (untracked here) holds U+202E, and nothing is reported
    through the link, whose target text is clean."""
    (tmp_path / "target.md").write_text(f"x {BIDI[4]}\n", encoding="utf-8")
    _git_tree(tmp_path, {"clean.md": "target.md"})
    assert (tmp_path / "clean.md").read_text(encoding="utf-8") == f"x {BIDI[4]}\n"  # premise
    assert _scan_checkout(tmp_path) == (1, {})


def test_constructed_symlink_blob_is_read_without_a_link_on_disk(tmp_path: Path) -> None:
    """Constructed tree (M16-104): where the checkout holds no symlink for a
    mode-120000 entry (core.symlinks false writes a plain file, or the entry
    is deleted), the committed blob is read, never the file on disk."""
    _git_tree(tmp_path, {"notes.md": f"a{BIDI[4]}b.md", "plain.md": "clean.md"})
    (tmp_path / "notes.md").unlink()
    (tmp_path / "plain.md").unlink()
    (tmp_path / "plain.md").write_text(f"x {BIDI[4]}\n", encoding="utf-8")
    assert _scan_checkout(tmp_path) == (2, {"'notes.md' (symlink target)": ["U+202E"]})


def test_constructed_unstaged_retarget_still_scans_the_committed_target(tmp_path: Path) -> None:
    """Constructed tree (M16-104, security review round 1): a link whose
    committed target text holds U+202E, retargeted on disk to a clean name
    without staging, is still reported: the blob is read, not the link."""
    _git_tree(tmp_path, {"notes.md": f"a{BIDI[4]}b.md"})
    link = tmp_path / "notes.md"
    link.unlink()
    link.symlink_to("clean.md")
    assert _scan_checkout(tmp_path) == (1, {"'notes.md' (symlink target)": ["U+202E"]})


def test_constructed_regular_file_read_from_the_working_tree(tmp_path: Path) -> None:
    """Constructed tree (M16-104): an unchanged regular file is read from the
    working tree (control for the tests below), so an edit there counts."""
    _git_tree(tmp_path, {}, {"a.md": f"x {BIDI[4]}\n"})
    assert _scan_checkout(tmp_path) == (1, {"'a.md'": ["U+202E"]})
    (tmp_path / "a.md").write_text("plain\n", encoding="utf-8")
    assert _scan_checkout(tmp_path) == (1, {})


def test_constructed_regular_file_replaced_by_a_link_scans_its_blob(tmp_path: Path) -> None:
    """Constructed tree (M16-104, security review round 1): a regular file
    whose blob holds U+202E, replaced in the working tree by a link to a
    clean file, is reported from its blob; path.is_file() followed the link
    and read the clean file."""
    _git_tree(tmp_path, {}, {"a.md": f"x {BIDI[4]}\n"})
    (tmp_path / "clean.md").write_text("plain\n", encoding="utf-8")
    (tmp_path / "a.md").unlink()
    (tmp_path / "a.md").symlink_to("clean.md")
    assert (tmp_path / "a.md").is_file()  # the premise: is_file() follows it
    assert _scan_checkout(tmp_path) == (1, {"'a.md' (committed blob)": ["U+202E"]})


def test_constructed_deleted_regular_file_scans_its_blob(tmp_path: Path) -> None:
    """Constructed tree (M16-104, security review round 1): a regular file
    whose blob holds U+202E, deleted in the working tree, is reported from
    its blob; path.is_file() was false and the scan skipped it."""
    _git_tree(tmp_path, {}, {"a.md": f"x {BIDI[4]}\n", "b.md": "plain\n"})
    (tmp_path / "a.md").unlink()
    (tmp_path / "b.md").unlink()
    assert _scan_checkout(tmp_path) == (2, {"'a.md' (committed blob)": ["U+202E"]})


def test_constructed_parent_directory_link_is_not_followed(tmp_path: Path) -> None:
    """Constructed tree (M16-104, security review round 1): a tracked
    directory replaced by a link to a clean copy outside the repository is
    not read through; the entry's blob, holding U+202E, is."""
    repo, outside = tmp_path / "repo", tmp_path / "outside"
    repo.mkdir()
    _git_tree(repo, {}, {"docs/a.md": f"x {BIDI[4]}\n"})
    (outside / "docs").mkdir(parents=True)
    (outside / "docs" / "a.md").write_text("plain\n", encoding="utf-8")
    (repo / "docs" / "a.md").unlink()
    (repo / "docs").rmdir()
    (repo / "docs").symlink_to(outside / "docs")
    assert (repo / "docs" / "a.md").is_file()  # the premise: lstat alone follows the parent
    assert _scan_checkout(repo) == (1, {"'docs/a.md' (committed blob)": ["U+202E"]})


def test_constructed_gitlink_is_checked_by_name_only(tmp_path: Path) -> None:
    """Constructed tree (M16-104): a gitlink (mode 160000) has no blob, so
    only its name is checked; a control in the name is reported."""
    _git_tree(tmp_path, {})
    commit = "1" * 40
    for name in ("sub", f"s{BIDI[4]}b"):
        done = _run_git(tmp_path, "update-index", "--add", "--cacheinfo", f"160000,{commit},{name}")
        assert done.returncode == 0, done.stderr
    assert {mode for mode, _, _ in _entries(tmp_path)} == {GITLINK}
    count, found = _scan_checkout(tmp_path)
    assert count == 2
    assert list(found.values()) == [["U+202E"]]
    assert next(iter(found)).startswith("file name ")
