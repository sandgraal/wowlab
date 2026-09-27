"""Test-side edit helpers for the `luadata` serializer graders (M10-12T).

Not a serializer: nothing here lays out bytes. These helpers only build and
edit documents through the public model, the way a caller of M10-12 would,
and build the synthetic install trees the sibling-style graders need. The
seam they rely on is pinned in the docstring of
`test_luadata_serializer_fixtures.py`.

Edits walk by position, never by object identity: the parser shares
identical immutable nodes, so `is` does not identify a place in a document.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from _luadata_oracle import FIXTURES, NUMBER, POSITIONAL, STRING

SV_DIR = "macos/forever/WTF/Account/90000001#6/SavedVariables"
RARESCANNER = f"{SV_DIR}/RareScanner.lua"
SYNDICATOR = f"{SV_DIR}/Syndicator.lua"
DBM = f"{SV_DIR}/DBM-StatusBarTimers.lua"

# The real install markers, copied into every synthetic install so any way
# of recognising a flavor folder sees a plausible one.
BUILD_INFO = FIXTURES / "macos/.build.info"
FLAVOR_INFO = FIXTURES / "macos/forever/.flavor.info"

ACCOUNT = "WTF/Account/90000001#6"
ACCOUNT_SV = f"{ACCOUNT}/SavedVariables"
CHARACTER_SV = f"{ACCOUNT}/1/Labcharb-Labrealmd/SavedVariables"


def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


# ── building new nodes: every trivia slot `None`, left to the serializer ────


def string(lua: Any, text: str) -> Any:
    """A new string value. ASCII with no quote or backslash, so the literal
    is the text in double quotes (§4.2) and no escaping is being graded."""
    assert text.isascii() and '"' not in text and "\\" not in text
    return lua.LuaString(lead=None, raw=b'"' + text.encode("ascii") + b'"')


def number(lua: Any, raw: str) -> Any:
    return lua.LuaNumber(lead=None, raw=raw)


def boolean(lua: Any, value: bool) -> Any:
    return lua.LuaBool(lead=None, value=value)


def table(lua: Any, *entries: Any) -> Any:
    return lua.LuaTable(lead=None, entries=tuple(entries), close_lead=None)


def _entry(lua: Any, style: str, key: Any, value: Any) -> Any:
    return lua.Entry(
        lead=None,
        style=lua.KeyStyle(style),
        key=key,
        key_close_lead=None,
        eq_lead=None,
        value=value,
        sep_lead=None,
        sep=None,
        comment=None,
        duplicate=False,
    )


def positional(lua: Any, value: Any) -> Any:
    return _entry(lua, POSITIONAL, None, value)


def keyed(lua: Any, key: str, value: Any) -> Any:
    """A new `["key"] = value` entry."""
    return _entry(lua, STRING, string(lua, key), value)


def numbered(lua: Any, key: str, value: Any) -> Any:
    """A new `[number] = value` entry; `key` is the number's text."""
    return _entry(lua, NUMBER, number(lua, key), value)


def assignment(lua: Any, name: str, value: Any) -> Any:
    return lua.Assignment(lead=None, name=name, eq_lead=None, value=value)


def document(lua: Any, *assignments: Any) -> Any:
    return lua.LuaDocument(assignments=tuple(assignments), tail=None)


def reference_document(lua: Any) -> Any:
    """The document of `docs/LAB_FORMATS.md` §4.2's example, built with no
    source: every slot is the serializer's to fill."""
    return document(
        lua,
        assignment(
            lua,
            "MyAddonDB",
            table(
                lua,
                keyed(
                    lua,
                    "profileKeys",
                    table(lua, keyed(lua, "Name - Realm", string(lua, "Default"))),
                ),
                keyed(
                    lua,
                    "list",
                    table(
                        lua,
                        positional(lua, string(lua, "first")),
                        positional(lua, string(lua, "second")),
                    ),
                ),
                numbered(lua, "42", boolean(lua, True)),
                keyed(lua, "scale", number(lua, "0.8500000238418579")),
            ),
        ),
        assignment(lua, "OtherVar", string(lua, "text")),
    )


def reference_text(*, indent: bytes, comments: bool, eol: bytes) -> bytes:
    """`reference_document` laid out in one style: §4.2's example with its
    indentation unit (`b"\\t"`, or `b""` for none), its `-- [n]` comments or
    none, and its line ending. Not a serializer: one fixed document."""
    one, two = indent, indent * 2
    c1, c2 = (b" -- [1]", b" -- [2]") if comments else (b"", b"")
    lines = [
        b"",
        b"MyAddonDB = {",
        one + b'["profileKeys"] = {',
        two + b'["Name - Realm"] = "Default",',
        one + b"},",
        one + b'["list"] = {',
        two + b'"first",' + c1,
        two + b'"second",' + c2,
        one + b"},",
        one + b"[42] = true,",
        one + b'["scale"] = 0.8500000238418579,',
        b"}",
        b'OtherVar = "text"',
    ]
    return b"".join(line + eol for line in lines)


# ── editing parsed documents, by position ───────────────────────────────────


def _index(lua: Any, table_value: Any, step: str | int) -> int:
    """Position in `table_value.entries` of string key `step`, or of the
    `step`-th positional entry (1-based)."""
    assert isinstance(table_value, lua.LuaTable), f"not a table at {step!r}"
    if isinstance(step, int):
        found = [i for i, e in enumerate(table_value.entries) if e.style == POSITIONAL]
        return found[step - 1]
    hits = [
        i
        for i, e in enumerate(table_value.entries)
        if e.style == STRING and isinstance(e.key, lua.LuaString) and e.key.value == step
    ]
    assert len(hits) == 1, f"{step!r}: {len(hits)} entries"
    return hits[0]


def at(lua: Any, doc: Any, name: str, *steps: str | int) -> Any:
    [a] = [a for a in doc.assignments if a.name == name]
    value = a.value
    for step in steps:
        value = value.entries[_index(lua, value, step)].value
    return value


def edit(lua: Any, doc: Any, name: str, steps: tuple[str | int, ...], change: Any) -> Any:
    """`doc` with the value at `name` then `steps` replaced by
    `change(old_value)`. Rebuilt by position along the path only."""

    def down(value: Any, rest: tuple[str | int, ...]) -> Any:
        if not rest:
            return change(value)
        i = _index(lua, value, rest[0])
        entries = list(value.entries)
        entries[i] = entries[i]._replace(value=down(entries[i].value, rest[1:]))
        return value._replace(entries=tuple(entries))

    [i] = [i for i, a in enumerate(doc.assignments) if a.name == name]
    assignments = list(doc.assignments)
    assignments[i] = assignments[i]._replace(value=down(assignments[i].value, steps))
    return doc._replace(assignments=tuple(assignments))


def append(lua: Any, doc: Any, name: str, steps: tuple[str | int, ...], *new: Any) -> Any:
    """`doc` with `new` entries after the last entry of the table at `steps`.
    The table's `close_lead` goes to the serializer too (seam: those bytes
    hold the old last entry's comment and the closing indentation)."""

    def change(value: Any) -> Any:
        assert isinstance(value, lua.LuaTable)
        return value._replace(entries=value.entries + tuple(new), close_lead=None)

    return edit(lua, doc, name, steps, change)


def set_leaf(
    lua: Any, doc: Any, name: str, steps: tuple[str | int, ...], new: Any, *, keep_lead: bool
) -> Any:
    """`doc` with one scalar replaced by `new`, which either keeps the old
    value's leading bytes or leaves them to the serializer (`lead=None`)."""

    def change(value: Any) -> Any:
        assert not isinstance(value, lua.LuaTable)
        return new._replace(lead=value.lead) if keep_lead else new._replace(lead=None)

    return edit(lua, doc, name, steps, change)


# ── expected bytes, from the source text ────────────────────────────────────


def replace_once(source: bytes, old: bytes, new: bytes) -> bytes:
    assert source.count(old) == 1, f"{old!r} occurs {source.count(old)} times"
    return source.replace(old, new)


def replace_nth_after(source: bytes, marker: bytes, old: bytes, new: bytes, n: int) -> bytes:
    """Replace the `n`-th (1-based) occurrence of `old` after the unique
    `marker`."""
    assert source.count(marker) == 1, f"{marker!r} occurs {source.count(marker)} times"
    at_ = source.index(marker)
    for _ in range(n):
        at_ = source.index(old, at_ + 1)
    return source[:at_] + new + source[at_ + len(old) :]


def insert_after_line(source: bytes, marker: bytes, new: bytes) -> bytes:
    """Insert `new` after the line break that ends the line holding the
    unique `marker`."""
    assert source.count(marker) == 1, f"{marker!r} occurs {source.count(marker)} times"
    cut = source.index(b"\n", source.index(marker)) + 1
    return source[:cut] + new + source[cut:]


# ── synthetic installs ──────────────────────────────────────────────────────


def install(root: Path, flavors: dict[str, dict[str, bytes]]) -> Path:
    """An install under `root`: the real `.build.info`, and per flavor folder
    the real `.flavor.info` and the given files (paths relative to the
    flavor folder). Returns the install root."""
    root.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(BUILD_INFO, root / ".build.info")
    for flavor, files in flavors.items():
        folder = root / flavor
        folder.mkdir()
        shutil.copyfile(FLAVOR_INFO, folder / ".flavor.info")
        (folder / ACCOUNT_SV).mkdir(parents=True)
        for rel, data in files.items():
            path = folder / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
    return root


def tree_state(root: Path) -> dict[str, tuple[str, bytes, int]]:
    """Every path under `root` with its kind, bytes and mtime."""
    state: dict[str, tuple[str, bytes, int]] = {}
    for folder, dirs, files in os.walk(root):
        for d in dirs:
            p = Path(folder) / d
            state[p.relative_to(root).as_posix()] = ("dir", b"", p.stat().st_mtime_ns)
        for f in files:
            p = Path(folder) / f
            state[p.relative_to(root).as_posix()] = ("file", p.read_bytes(), p.stat().st_mtime_ns)
    return state
