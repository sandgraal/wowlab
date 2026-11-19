"""Constructed crew books for the tests of `crewbook` and `wowlab crew`
(M19-02). CONSTRUCTED INPUT, every byte of it.

ADR-0036 term 3 keeps every real crew record out of the repository, so the
reader is graded on books built here at run time (docs/LAB_PLAN.md §20.5,
"L8 and the reader"), plus an owner-run `@live` test. Each book is the
account `WowLab.lua` of the captured install `fixtures/macos-70888/` (real
bytes, holding `WowLabDB` only) with one constructed `WowLabCrewDB`
assignment appended in the client's own style: bracketed string keys, one
entry per line, `{` and `}` on lines of their own, the file's CRLF line
breaks. The record's shape is §20.1's.

Every player key used anywhere is one of the four invented keys below
(the fixed list of the repository test of M19-04). Hostile and generated
keys are assembled from them at run time, so no other key is written in
this file. Guild, note and instance names are invented and obviously fake.
Nothing here is ever written into the tree: the tests write to `tmp_path`.
"""

from __future__ import annotations

import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from wowlab_core import crewbook

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
    raise TypeError(type(value))


def _table(entries: Mapping[Any, Any]) -> bytes:
    """A table as the client writes it: one bracketed entry per line."""
    lines = [b"{"]
    for key, value in entries.items():
        k = _q(key) if isinstance(key, str) else str(key).encode("ascii")
        lines.append(b"".join((b"[", k, b"] = ", _value(value), b",")))
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


def book(
    players: Mapping[str, Any] | int | None = None,
    *,
    on: bool | None = True,
    schema: Any = 1,
    current: Sequence[str] = (),
    **more: Any,
) -> bytes:
    """The crew book's top-level assignment, in the client's style;
    `on=None` leaves `on` out."""
    table: dict[str, Any] = {"schema": schema}
    if on is not None:
        table["on"] = on
    table.update({"started": T0, "probe": {"loads": 12}, "skipped": 0})
    table["current"] = dict.fromkeys(current, True)
    # Anything but a mapping goes in as it is: a malformed `players`.
    table["players"] = dict(players) if isinstance(players, Mapping) else (players or {})
    table.update(more)
    return crewbook.CREW_VARIABLE.encode("ascii") + b" = " + _table(table) + NL


def account_file(crew: bytes | None) -> bytes:
    """The captured account `WowLab.lua`, with `crew` appended when given."""
    data = ACCOUNT_FILE.read_bytes()
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
