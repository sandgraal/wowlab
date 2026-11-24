"""M21-12: the crew book's region and each player's name by GUID (schema 3).

Nothing here runs Lua (L3). Two halves, as in test_m21_11_api_fixes.py:

1. Source pins. `readRegion`, `resolveNames` and the schema 3 migration are
   pinned token for token, with how they are reached (once each, from the
   end of the gated roster pass) and the cap per login.
2. Stub clients. Each pinned function has a line-for-line Python model, and
   a stub client stands in for the game: what `GetCurrentRegionName()` and
   `GetPlayerInfoByGUID(guid)` give, and whether the player is in combat.
   Each case the ticket names is played through the model: out of combat
   only, a GUID the client no longer knows gives nothing and is asked again
   at a later login, and at most MAX_RESOLVES GUIDs per login.

The model proves the code as pinned. What the real client gives is the
owner's check of 2026-11-23 (Forever launch client, US): `GetCurrentRegionName()`
returned "US" and `GetPlayerInfoByGUID` returned
"Warlock", "WARLOCK", "Undead", "Scourge", 3, "Tay Soulrend", "", 22. Whether
the client answers for a GUID it met long ago, and that the eighth value is
the level, are not yet confirmed ([verify] in lab/addon/README.md).

CONSTRUCTED INPUT: every stub value and GUID below is invented; the one
answer shaped like the owner's is the owner's own test character's name,
which the owner gave in session.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import test_lab_addon as g

CREW = g.ADDON / "Crew.lua"


def _flow(path: Path) -> g._Flow:
    return g._Flow(path, g._tokens(path))


# ─── 1. pins ────────────────────────────────────────────────────────────────

GATE = "if not crewOn ( ) then return end if crewBlocked ( ) then return end"
READ_REGION = (
    f"function readRegion ( ) {GATE} "
    "if WowLabCrewDB . region ~= nil then return end "
    'if type ( GetCurrentRegionName ) ~= "function" then return end '
    "local region = GetCurrentRegionName ( ) "
    "if secret ( region ) then addSkipped ( ) end "
    'if not secret ( region ) and type ( region ) == "string" and region ~= "" then '
    "WowLabCrewDB . region = ns . String ( region ) end end"
)
RESOLVE_NAMES = (
    f"function resolveNames ( ) {GATE} "
    'if type ( GetPlayerInfoByGUID ) ~= "function" then return end '
    'local now = nil if type ( time ) == "function" then now = ns . Number ( time ( ) ) end '
    "for _ , entry in pairs ( WowLabCrewDB . players ) do "
    "if resolveAsked >= MAX_RESOLVES then return end "
    "local guid = nil "
    'if type ( entry ) == "table" and entry . resolved_name == nil then '
    "guid = ns . String ( entry . guid ) end "
    "if guid ~= nil and resolveTried [ guid ] == nil "
    'and string . find ( guid , "^Player%-" ) ~= nil then '
    "resolveTried [ guid ] = true resolveAsked = resolveAsked + 1 "
    "local _ , _ , _ , _ , _ , name , realm , eighth = GetPlayerInfoByGUID ( guid ) "
    "if secret ( name ) or secret ( realm ) or secret ( eighth ) then addSkipped ( ) end "
    'if not secret ( name ) and type ( name ) == "string" and name ~= "" '
    "and name ~= UNKNOWNOBJECT then "
    "entry . resolved_name = ns . String ( name ) entry . resolved_at = now "
    'if not secret ( realm ) and type ( realm ) == "string" then '
    "entry . resolved_realm = ns . String ( realm ) end "
    'if not secret ( eighth ) and type ( eighth ) == "number" then '
    "entry . resolved_eighth = ns . Number ( eighth ) end end end end end"
)
PASS_TAIL = "pcall ( readRegion ) pcall ( resolveNames ) end"
MAX_RESOLVES = 100
MAX_RESOLVES_DECL = f"local MAX_RESOLVES = {MAX_RESOLVES}"
SESSION_STATE = ("local resolveTried = { }", "local resolveAsked = 0")


def pin_problems(crew: Path = CREW) -> list[str]:
    """Why Crew.lua (or an edited copy) is not the pinned M21-12 code."""
    problems: list[str] = []
    flow = _flow(crew)
    text = g._render(flow.tokens)
    if g._crew_pins(flow.tokens) is not g.CREW_PINS_3:
        problems.append("Crew.lua: not graded at schema 3")
    for label, pin in (("readRegion", READ_REGION), ("resolveNames", RESOLVE_NAMES)):
        if g._body(flow, label) != pin:
            problems.append(f"Crew.lua: {label} is not the pinned one")
        # Defined once, and reached only through the pass's one pcall.
        if text.count(f" {label} ") != 2 or text.count(f"pcall ( {label} )") != 1:
            problems.append(f"Crew.lua: {label} is reached other than from the pass")
    if not g._body(flow, "pass").endswith(PASS_TAIL):
        problems.append("Crew.lua: the pass does not end by asking the region and the names")
    problems += g._crew_constant_problems(flow, "MAX_RESOLVES", MAX_RESOLVES_DECL)
    for decl in SESSION_STATE:
        if text.count(decl) != 1:
            problems.append(f"Crew.lua: the session state {decl!r} is not declared once")
    # The two session values change only in resolveNames, never stored in
    # the book.
    resolve = g._body(flow, "resolveNames")
    for name, store in (
        ("resolveTried", "resolveTried [ guid ] = true"),
        ("resolveAsked", "resolveAsked = resolveAsked + 1"),
    ):
        # Its declaration and its one store, nothing else.
        stores = re.findall(rf"(?<![\w.]){name}(?: \[ [^]]* \])? = (?!=)", text)
        if len(stores) != 2 or text.count(store) != 1 or store not in resolve:
            problems.append(f"Crew.lua: {name} is written other than in resolveNames")
    if "WowLabCrewDB . resolve" in text:
        problems.append("Crew.lua: the session state is stored in the book")
    # The region and the four name fields are stored only by the pinned two.
    for stored, owner in (
        ("WowLabCrewDB . region =", "readRegion"),
        ("entry . resolved_name =", "resolveNames"),
        ("entry . resolved_realm =", "resolveNames"),
        ("entry . resolved_eighth =", "resolveNames"),
        ("entry . resolved_at =", "resolveNames"),
    ):
        # A store, not an `==` test.
        count = len(re.findall(re.escape(stored) + r"(?!=)", text))
        if count != 1 or stored not in g._body(flow, owner):
            problems.append(f"Crew.lua: {stored!r} is written other than in {owner}")
    return problems


def test_m21_12_the_changes_are_the_pinned_ones() -> None:
    assert pin_problems() == []


def test_m21_12_the_real_file_keeps_crew_lua_s_own_rules() -> None:
    """The new calls are Crew.lua's name calls: behind the on and combat
    tests, each the whole value of a local, every value secret-tested."""
    text = CREW.read_text(encoding="utf-8")
    assert {"GetPlayerInfoByGUID", "GetCurrentRegionName"} <= g.CREW_NAME_CALLS
    assert "GetPlayerInfoByGUID" not in g.UNIT_FIRST_ARG  # its argument is a GUID
    assert g._crew_problems(CREW, text) == []


def test_m21_12_the_migration_is_the_pinned_one() -> None:
    flow = _flow(CREW)
    assert "local " + g._body(flow, "crewMigrate") == g.CREW_MIGRATE_3
    assert g.CREW_PINS_3.pinned["crewMigrate"] == g.CREW_MIGRATE_3


# Constructed: each edit breaks one rule and must fail the pins or Crew.lua's
# own rules.
EDITS: dict[str, tuple[str, str]] = {
    "resolve: asked in combat (no combat test)": (
        "local function resolveNames()\n    if not crewOn() then\n        return\n    end\n"
        "    if crewBlocked() then\n        return\n    end\n",
        "local function resolveNames()\n    if not crewOn() then\n        return\n    end\n",
    ),
    "resolve: no cap": (
        "        if resolveAsked >= MAX_RESOLVES then\n            return\n        end\n",
        "",
    ),
    "resolve: a GUID asked again in the session": (
        "            resolveTried[guid] = true\n",
        "",
    ),
    "resolve: the name stored untested": (
        '            if not secret(name) and type(name) == "string" and name ~= "" '
        "and name ~= UNKNOWNOBJECT then\n",
        '            if type(name) == "string" then\n',
    ),
    "resolve: a nil answer stored": (
        "                entry.resolved_name = ns.String(name)\n",
        '                entry.resolved_name = ns.String(name) or ""\n',
    ),
    "resolve: the cap raised": (
        "local MAX_RESOLVES = 100\n",
        "local MAX_RESOLVES = 100000\n",
    ),
    "resolve: asked outside the pass": (
        "    pcall(nearFull)\n",
        "    pcall(nearFull)\n    pcall(resolveNames)\n",
    ),
    "region: asked every pass": (
        "    if WowLabCrewDB.region ~= nil then\n        return\n    end\n",
        "",
    ),
    "migration: a schema 2 book left at 2": (
        "    if WowLabCrewDB.schema ~= 1 and WowLabCrewDB.schema ~= 2 then\n",
        "    if WowLabCrewDB.schema ~= 1 then\n",
    ),
}


@pytest.mark.parametrize("tag", sorted(EDITS))
def test_m21_12_constructed_edits_fail(tag: str, tmp_path: Path) -> None:
    old, new = EDITS[tag]
    real = CREW.read_text(encoding="utf-8")
    assert real.count(old) == 1, old
    edited = tmp_path / CREW.name
    text = real.replace(old, new, 1)
    edited.write_text(text, encoding="utf-8")
    assert pin_problems(edited) or g._crew_problems(edited, text), tag


# ─── 2. stub clients ────────────────────────────────────────────────────────

SECRET = object()  # a value the client calls secret
UNKNOWN = "Unknown"  # the client's UNKNOWNOBJECT text (enUS)


def _is_secret(value: Any) -> bool:
    return value is SECRET


@dataclass
class Client:
    """What the game gives: the region, and GetPlayerInfoByGUID's eight
    values per GUID it knows now (a GUID it does not know gives nothing)."""

    region: Any = "US"
    known: dict[str, tuple[Any, ...]] = field(default_factory=dict)
    in_combat: bool = False
    has_info: bool = True
    has_region: bool = True
    asked: list[str] = field(default_factory=list)

    def info(self, guid: str) -> tuple[Any, ...]:
        self.asked.append(guid)
        return self.known.get(guid, ())


@dataclass
class Session:
    """One login or /reload: the session state, never saved."""

    tried: set[str] = field(default_factory=set)
    asked: int = 0


@dataclass
class Book:
    """WowLabCrewDB as the two functions touch it."""

    on: bool = True
    region: Any = None
    skipped: int = 0
    players: dict[str, dict[str, Any]] = field(default_factory=dict)


def read_region(book: Book, c: Client) -> None:
    """readRegion, line for line."""
    if not book.on or c.in_combat:
        return
    if book.region is not None:
        return
    if not c.has_region:
        return
    region = c.region
    if _is_secret(region):
        book.skipped += 1
    if not _is_secret(region) and isinstance(region, str) and region != "":
        book.region = region


def resolve_names(book: Book, c: Client, s: Session, now: int = 1_800_000_000) -> None:
    """resolveNames, line for line."""
    if not book.on or c.in_combat:
        return
    if not c.has_info:
        return
    for entry in book.players.values():
        if s.asked >= MAX_RESOLVES:
            return
        guid = None
        if isinstance(entry, dict) and entry.get("resolved_name") is None:
            got = entry.get("guid")
            guid = got if isinstance(got, str) else None
        if guid is not None and guid not in s.tried and guid.startswith("Player-"):
            s.tried.add(guid)
            s.asked += 1
            values = (*c.info(guid), None, None, None, None, None, None, None, None)
            name, realm, eighth = values[5], values[6], values[7]
            if _is_secret(name) or _is_secret(realm) or _is_secret(eighth):
                book.skipped += 1
            if not _is_secret(name) and isinstance(name, str) and name != "" and name != UNKNOWN:
                entry["resolved_name"] = name
                entry["resolved_at"] = now
                if not _is_secret(realm) and isinstance(realm, str):
                    entry["resolved_realm"] = realm
                if not _is_secret(eighth) and isinstance(eighth, (int, float)):
                    entry["resolved_eighth"] = eighth


TAY_GUID = "Player-9998-0000000E"
# The owner's check of 2026-11-23, as GetPlayerInfoByGUID returned it.
TAY_INFO = ("Warlock", "WARLOCK", "Undead", "Scourge", 3, "Tay Soulrend", "", 22)


def _book(*guids: str) -> Book:
    return Book(players={f"Invented{i}-Second": {"met": 1, "guid": g} for i, g in enumerate(guids)})


def test_m21_12_the_owners_answer_is_kept_as_given() -> None:
    book = Book(players={"Tay-Soulrend": {"met": 1, "guid": TAY_GUID}})
    client = Client(known={TAY_GUID: TAY_INFO})
    resolve_names(book, client, Session(), now=1_800_000_000)
    assert book.players["Tay-Soulrend"] == {
        "met": 1,
        "guid": TAY_GUID,
        "resolved_name": "Tay Soulrend",
        "resolved_realm": "",
        "resolved_eighth": 22,
        "resolved_at": 1_800_000_000,
    }
    read_region(book, client)
    assert book.region == "US"


def test_m21_12_nothing_is_asked_in_combat() -> None:
    book = Book(players={"Tay-Soulrend": {"guid": TAY_GUID}})
    client = Client(known={TAY_GUID: TAY_INFO}, in_combat=True)
    resolve_names(book, client, Session())
    read_region(book, client)
    assert client.asked == [] and book.region is None
    assert "resolved_name" not in book.players["Tay-Soulrend"]
    # Out of combat again: the next pass asks.
    client.in_combat = False
    resolve_names(book, client, Session())
    assert book.players["Tay-Soulrend"]["resolved_name"] == "Tay Soulrend"


def test_m21_12_a_book_that_is_off_is_not_touched() -> None:
    book = Book(on=False, players={"Tay-Soulrend": {"guid": TAY_GUID}})
    client = Client(known={TAY_GUID: TAY_INFO})
    resolve_names(book, client, Session())
    read_region(book, client)
    assert client.asked == [] and book.region is None


def test_m21_12_an_unknown_guid_is_kept_empty_and_asked_at_a_later_login() -> None:
    book = _book("Player-9998-00000001")
    client = Client()
    first = Session()
    resolve_names(book, client, first)
    entry = book.players["Invented0-Second"]
    assert client.asked == ["Player-9998-00000001"]
    assert "resolved_name" not in entry and "resolved_at" not in entry
    # The same session does not ask it again, however many passes run.
    resolve_names(book, client, first)
    assert client.asked == ["Player-9998-00000001"]
    # A later login (a new session) asks again; the client knows it now.
    client.known["Player-9998-00000001"] = (*TAY_INFO[:5], "Invented Second", "", 10)
    resolve_names(book, client, Session())
    assert client.asked == ["Player-9998-00000001"] * 2
    assert entry["resolved_name"] == "Invented Second"


def test_m21_12_at_most_the_cap_is_asked_per_login() -> None:
    guids = [f"Player-9998-{i:08X}" for i in range(MAX_RESOLVES + 50)]
    book = _book(*guids)
    client = Client(
        known={g: (*TAY_INFO[:5], f"Name{i} Second", "", 5) for i, g in enumerate(guids)}
    )
    session = Session()
    resolve_names(book, client, session)
    resolve_names(book, client, session)  # a second pass in the same login
    assert len(client.asked) == MAX_RESOLVES
    resolved = [e for e in book.players.values() if "resolved_name" in e]
    assert len(resolved) == MAX_RESOLVES
    # The next login asks the rest, and never one already named.
    resolve_names(book, client, Session())
    assert len(client.asked) == MAX_RESOLVES + 50
    assert len(set(client.asked)) == len(client.asked)
    assert all("resolved_name" in e for e in book.players.values())


@pytest.mark.parametrize(
    ("answer", "stored", "skipped"),
    [
        ((*TAY_INFO[:5], SECRET, "", 22), {}, 1),
        ((*TAY_INFO[:5], "", "", 22), {}, 0),
        ((*TAY_INFO[:5], UNKNOWN, "", 22), {}, 0),
        ((*TAY_INFO[:5], None, None, None), {}, 0),
        (
            (*TAY_INFO[:5], "Tay Soulrend", SECRET, SECRET),
            {"resolved_name": "Tay Soulrend", "resolved_at": 1},
            1,
        ),
        (
            (*TAY_INFO[:5], "Tay Soulrend", "Invented Realm", "22"),
            {"resolved_name": "Tay Soulrend", "resolved_realm": "Invented Realm", "resolved_at": 1},
            0,
        ),
    ],
    ids=[
        "constructed-secret-name",
        "constructed-empty-name",
        "constructed-unknown-text",
        "constructed-nothing",
        "constructed-secret-realm-and-level",
        "constructed-realm-and-text-level",
    ],
)
def test_m21_12_what_is_kept_of_an_answer(
    answer: tuple[Any, ...], stored: dict[str, Any], skipped: int
) -> None:
    book = _book("Player-9998-00000002")
    resolve_names(book, Client(known={"Player-9998-00000002": answer}), Session(), now=1)
    entry = book.players["Invented0-Second"]
    assert {k: v for k, v in entry.items() if k.startswith("resolved_")} == stored
    assert book.skipped == skipped


def test_m21_12_only_player_guids_without_a_name_are_asked() -> None:
    book = Book(
        players={
            "A-One": {"guid": "Creature-0-1-2-3-4-5"},
            "B-Two": {"guid": TAY_GUID, "resolved_name": "Already Named"},
            "C-Three": {"met": 1},
            "D-Four": {"guid": 42},
            "E-Five": {"guid": "Player-9998-00000003"},
        }
    )
    client = Client(known={TAY_GUID: TAY_INFO})
    resolve_names(book, client, Session())
    assert client.asked == ["Player-9998-00000003"]
    assert book.players["B-Two"]["resolved_name"] == "Already Named"


def test_m21_12_a_client_without_the_calls_records_nothing() -> None:
    book = Book(players={"Tay-Soulrend": {"guid": TAY_GUID}})
    client = Client(known={TAY_GUID: TAY_INFO}, has_info=False, has_region=False)
    resolve_names(book, client, Session())
    read_region(book, client)
    assert client.asked == [] and book.region is None


@pytest.mark.parametrize(
    ("given", "kept", "skipped"),
    [("US", "US", 0), (SECRET, None, 1), ("", None, 0), (None, None, 0), (3, None, 0)],
    ids=[
        "constructed-us",
        "constructed-secret",
        "constructed-empty",
        "constructed-nil",
        "constructed-number",
    ],
)
def test_m21_12_the_region_is_kept_once_as_given(given: Any, kept: Any, skipped: int) -> None:
    book = Book()
    read_region(book, Client(region=given))
    assert (book.region, book.skipped) == (kept, skipped)
    # Once kept, a later answer does not replace it.
    book.region = "US"
    read_region(book, Client(region="EU"))
    assert book.region == "US"
