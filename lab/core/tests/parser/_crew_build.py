"""Constructed crew books for the tests of `crewbook` and `wowlab crew`
(M19-02). CONSTRUCTED INPUT, every byte of it.

ADR-0036 term 3 keeps every real crew record out of the repository, so the
reader is graded on books built here at run time (docs/LAB_PLAN.md §20.5,
"L8 and the reader"), plus an owner-run `@live` test. Each book is the
account `WowLab.lua` of the captured install `fixtures/macos-70888/` (real
bytes, holding `WowLabDB` only) with one constructed `WowLabCrewDB`
assignment appended in the client's own style: bracketed string keys, one
entry per line, `{` and `}` on lines of their own, the file's CRLF line
breaks. The record's shape is §20.1's; at schema 2 (M20-03), §21.2's.

Every player key used anywhere is one of the four invented keys below
(the fixed list of the repository test of M19-04). Hostile and generated
keys are assembled from them at run time, so no other key is written in
this file. Guild, note and instance names are invented and obviously fake.
Nothing here is ever written into the tree: the tests write to `tmp_path`.
"""

from __future__ import annotations

import shutil
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from wowlab_core import crewbook, luadata

assert crewbook.CREW_VARIABLE == "WowLabCrewDB"

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
CAPTURE = FIXTURES / "macos-70888"  # a whole captured flavor, with its .build.info
FLAVOR = "_forever_"  # the capture's flavor folder; tests may name it (L6)
ACCOUNT = "LABACCOUNTA"
ACCOUNT_FILE = CAPTURE / "forever" / "WTF" / "Account" / ACCOUNT / "SavedVariables" / "WowLab.lua"

# The fixed list of constructed player keys (M19-04's `CONSTRUCTED_KEYS`).
KEY_A = "Inventeda-Fakerealm"
KEY_B = "Inventedb-Fakerealm"
KEY_C = "Inventedc-Otherfakerealm"
KEY_D = "Inventedd-Otherfakerealm"

NL = b"\r\n"  # the captured file's line break

# Values of the record's fields (§20.1), invented.
GUILD = "Invented Guild of Testing"
WHERE = "Invented Test Dungeon"
NOTE = "invented note: kept the group together"
T0 = 1_794_950_000  # time() values, as the addon stores them
T1 = 1_795_640_000


def _q(text: str) -> bytes:
    """`text` as a Lua 5.1 string literal the client could write: `\\`, `"`
    and every control byte escaped (`\\n` for a line break, `\\ddd` for the
    rest), every other byte as it is (UTF-8)."""
    out = bytearray(b'"')
    for byte in text.encode("utf-8", "surrogateescape"):
        if byte in (0x22, 0x5C):
            out += b"\\" + bytes([byte])
        elif byte == 0x0A:
            out += b"\\n"
        elif byte < 0x20 or byte == 0x7F:
            out += b"\\" + str(byte).encode("ascii")
        else:
            out.append(byte)
    return bytes(out) + b'"'


def _value(value: Any) -> bytes:
    if value is True:
        return b"true"
    if value is False:
        return b"false"
    if isinstance(value, (int, float)):
        return repr(value).encode("ascii")
    if isinstance(value, str):
        return _q(value)
    if isinstance(value, Mapping):
        return _table(value)
    if isinstance(value, (list, tuple)):
        return _list(value)
    raise TypeError(type(value))


def lua_value(value: Any) -> bytes:
    """`value` written as the builder writes any value of a book."""
    return _value(value)


def _table(entries: Mapping[Any, Any]) -> bytes:
    """A table as the client writes it: one bracketed entry per line."""
    lines = [b"{"]
    for key, value in entries.items():
        k = _q(key) if isinstance(key, str) else str(key).encode("ascii")
        lines.append(b"".join((b"[", k, b"] = ", _value(value), b",")))
    lines.append(b"}")
    return NL.join(lines)


def _list(values: Sequence[Any]) -> bytes:
    """A list as the client writes its array part (LAB_FORMATS §4.2): one
    positional entry per line, each with its `-- [n]` comment."""
    lines = [b"{"]
    for n, value in enumerate(values, 1):
        lines.append(_value(value) + b", -- [" + str(n).encode("ascii") + b"]")
    lines.append(b"}")
    return NL.join(lines)


def player(
    *,
    rating: str | None = None,
    note: str | None = None,
    met: int = 3,
    last: int = T1,
    where: str | None = WHERE,
    guild: str | None = GUILD,
    **more: Any,
) -> dict[str, Any]:
    """One player's entry, as §20.1 shows it; None leaves a field out."""
    entry: dict[str, Any] = {"class": "MAGE", "level": 60, "role": "HEALER"}
    if guild is not None:
        entry["guild"] = guild
    entry.update({"met": met, "first": T0, "last": last})
    if where is not None:
        entry["where"] = where
        entry["where_id"] = 2291
    if rating is not None:
        entry["rating"] = rating
    if note is not None:
        entry["note"] = note
    entry.update(more)
    return entry


# The schema `book` writes when none is given, and the one `account_file`
# and `install` bring a schema 1 book to (M20-03): every M19 test of the
# reader, the writers and the commands runs on a schema 1 and a schema 2
# book through the `book_schema` fixture below.
SCHEMA = 1

# Schema 2's fields (docs/LAB_PLAN.md §21.2), invented values.
MAP = "Invented Test Valley"
MAP_ID = 489
T2 = 1_795_650_000  # a match's time, after T1
SIDES_SHOWN = 4  # the keys a default match names, at most


@pytest.fixture(autouse=True, params=[1, 2], ids=["schema_1", "schema_2"])
def book_schema(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> int:
    """Run the test once per saved-file schema (M20-03): every book built or
    appended while it runs is at this schema. Imported by name into each
    test module of the crew book's M19 tests."""
    schema = int(request.param)
    monkeypatch.setattr(sys.modules[__name__], "SCHEMA", schema)
    return schema


def match(
    team: Sequence[str] = (),
    foes: Sequence[str] = (),
    *,
    at: int = T2,
    won: bool | None = True,
    **more: Any,
) -> dict[str, Any]:
    """One entry of a schema 2 book's `matches` (§21.2); `won=None` leaves
    it out (the client gave no winner)."""
    entry: dict[str, Any] = {"at": at, "map": MAP, "map_id": MAP_ID, "kind": "bg"}
    if won is not None:
        entry["won"] = won
    entry["team"] = list(team)
    entry["foes"] = list(foes)
    entry.update(more)
    return entry


def probe_run(**more: Any) -> dict[str, Any]:
    """One run of the scoreboard probe (`probe.pvp`), in the shape of the
    `pvpSnapshot` M20-04 pinned: which calls exist, the first row's field
    names and type names, the type at each position, the counts, three
    whole numbers and three yes-or-no answers. Invented values."""
    run: dict[str, Any] = {
        "calls": {
            "GetScoreInfo": True,
            "GetNumBattlefieldScores": True,
            "GetBattlefieldScore": False,
        },
        "positions": ["string", "number", "nil"],
        "fields": {"name": "string", "faction": "number", "guid": "string"},
        "requested": True,
        "score_events": 3,
        "complete_events": 0,
        "rows": 20,
        "name_string": True,
        "name_secret": False,
        "name_dash": True,
        "first_side": 0,
        "own_side": 1,
    }
    run.update(more)
    return run


def default_matches(keys: Sequence[str]) -> list[dict[str, Any]]:
    """What a schema 2 book built here holds in `matches` when the test
    gives none: one match naming the book's first keys, the first on the
    player's team and the rest against (none when the book is empty)."""
    shown = list(keys)[:SIDES_SHOWN]
    return [match(shown[:1], shown[1:])] if shown else []


def book(
    players: Mapping[str, Any] | int | None = None,
    *,
    on: bool | None = True,
    schema: Any = None,
    current: Sequence[str] = (),
    **more: Any,
) -> bytes:
    """The crew book's top-level assignment, in the client's style;
    `on=None` leaves `on` out. `schema` None: `SCHEMA`. A schema 2 book
    also holds `matches` (`default_matches` unless given) and `warned`."""
    if schema is None:
        schema = SCHEMA
    table: dict[str, Any] = {"schema": schema}
    if on is not None:
        table["on"] = on
    table.update({"started": T0, "probe": {"loads": 12}, "skipped": 0})
    table["current"] = dict.fromkeys(current, True)
    # Anything but a mapping goes in as it is: a malformed `players`.
    table["players"] = dict(players) if isinstance(players, Mapping) else (players or {})
    if schema == 2:
        keys = list(players) if isinstance(players, Mapping) else []
        table["matches"] = default_matches(keys)
        table["warned"] = T1
    table.update(more)
    # A field given as None is left out (`warned=None`: never warned yet).
    table = {k: v for k, v in table.items() if v is not None}
    return crewbook.CREW_VARIABLE.encode("ascii") + b" = " + _table(table) + NL


_HEAD_1 = crewbook.CREW_VARIABLE.encode("ascii") + b" = {" + NL + b'["schema"] = 1,' + NL
_TAIL = NL + b"}" + NL


def at_schema(crew: bytes | None) -> bytes | None:
    """`crew` at `SCHEMA`: a book this module built at schema 1 is brought
    to schema 2 as the addon's migration leaves it plus what schema 2 adds
    (the number, `matches` naming its first keys, `warned`); anything else,
    and any book while `SCHEMA` is 1, as it is."""
    if (
        crew is None
        or SCHEMA != 2
        or len(crew) < len(_HEAD_1) + len(_TAIL)
        or not crew.startswith(_HEAD_1)
        or not crew.endswith(_TAIL)
    ):
        return crew
    # The keys only, to name them in the match; the bytes are this module's.
    loaded = luadata.parse(crew).to_python()[crewbook.CREW_VARIABLE]
    held = loaded.get("players") if isinstance(loaded, dict) else None
    keys = [k for k in held if isinstance(k, str)] if isinstance(held, dict) else []
    extra = b"".join(
        b"".join((b"[", _q(name), b"] = ", _value(value), b",", NL))
        for name, value in (("matches", default_matches(keys)), ("warned", T1))
    )
    body = crew[len(_HEAD_1) : -len(_TAIL)]
    head = _HEAD_1.replace(b'["schema"] = 1,', b'["schema"] = 2,')
    return head + body + NL + extra + b"}" + NL


def account_file(crew: bytes | None) -> bytes:
    """The captured account `WowLab.lua`, with `crew` appended when given
    (at `SCHEMA`, `at_schema`)."""
    data = ACCOUNT_FILE.read_bytes()
    crew = at_schema(crew)
    return data if crew is None else data + crew


def install(tmp_path: Path, crew: bytes | None, *, accounts: Sequence[str] = (ACCOUNT,)) -> Path:
    """A copy of the captured install in `tmp_path` whose account `WowLab.lua`
    holds `crew` (each account in `accounts`; the extra ones are copies of
    the captured account folder). Returns the install root."""
    root = tmp_path / "World of Warcraft"
    root.mkdir()
    shutil.copy2(CAPTURE / ".build.info", root / ".build.info")
    shutil.copytree(CAPTURE / "forever", root / FLAVOR)
    base = root / FLAVOR / "WTF" / "Account"
    for name in accounts:
        if name != ACCOUNT:
            shutil.copytree(base / ACCOUNT, base / name)
    for name in accounts:
        (base / name / "SavedVariables" / "WowLab.lua").write_bytes(account_file(crew))
    if ACCOUNT not in accounts:
        shutil.rmtree(base / ACCOUNT)
    return root


def many_keys(n: int) -> list[str]:
    """`n` distinct keys, each made at run time from `KEY_A`'s two parts and
    a run of letters (no key of the fixed list is changed in place)."""
    name, realm = KEY_A.split("-")
    out: list[str] = []
    for i in range(n):
        tag = ""
        j = i
        while True:
            tag += chr(ord("a") + j % 26)
            j //= 26
            if j == 0:
                break
        out.append(f"{name}{tag}-{realm}")
    return out
