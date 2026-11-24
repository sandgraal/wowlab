"""M21-12: the crew book's region and each player's name by GUID (schema 3).

Nothing here runs Lua (L3). Two halves, as in test_m21_11_api_fixes.py:

1. Source pins. `readRegion`, `resolveNames` and the schema 3 migration are
   pinned token for token, with how they are reached (from the end of the
   gated roster pass and of the scoreboard pass) and what marks a player as
   met this session (`resolveSeen`, in `readMember` and `recordMatch`).
2. Stub clients. Each pinned function has a line-for-line Python model, and
   a stub client stands in for the game: what `GetCurrentRegionName()` and
   `GetPlayerInfoByGUID(guid)` give, and whether the player is in combat.
   The stub names only players it has seen this session (`known`), as the
   client is believed to: a new login starts with a new, empty stub, and a
   `deferred` GUID is named only from its second ask (the client's answer
   arriving later; the 71037 event list has no event for it). Each case the
   ticket and its fix round name is played through the model: out of
   combat only; a GUID the client does not name gives nothing and goes to
   the back of the queue; players met this session are asked first and not
   limited; one retry a few seconds later in the same session; the limits
   per pass and per login; and more than 100 players the client cannot name
   never starve a player it can.

The model proves the code as pinned. What the real client gives is the
owner's check of 2026-11-23 (Forever launch client, US): `GetCurrentRegionName()`
returned "US" and `GetPlayerInfoByGUID` returned
"Warlock", "WARLOCK", "Undead", "Scourge", 3, "Tay Soulrend", "", 22. Whether
the client answers for a GUID it met in an earlier session, whether a second
ask collects a late answer, and that the eighth value is the level are not
yet confirmed ([verify] in lab/addon/README.md).

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
    "WowLabCrewDB . region = ns . String ( region ) "
    'if type ( time ) == "function" then WowLabCrewDB . region_at = ns . Number ( time ( ) ) end '
    "end end"
)
RESOLVE_NAMES = (
    f"function resolveNames ( ) {GATE} "
    'if type ( GetPlayerInfoByGUID ) ~= "function" then return end '
    'local now = nil if type ( time ) == "function" then now = ns . Number ( time ( ) ) end '
    "local seen , fresh , again , older = { } , { } , { } , { } "
    "local olderRoom = MAX_RESOLVES - backlogAsked "
    "for _ , entry in pairs ( WowLabCrewDB . players ) do "
    "local guid = nil "
    'if type ( entry ) == "table" and entry . resolved_name == nil then '
    "guid = ns . String ( entry . guid ) end "
    'if guid ~= nil and string . find ( guid , "^Player%-" ) ~= nil then '
    "local asked = resolveTried [ guid ] local due = asked == nil "
    "if asked ~= nil and resolveRetried [ guid ] == nil and now ~= nil "
    "and now - asked >= RETRY_SECONDS then due = true end "
    "local tried = ns . Number ( entry . resolve_tried_at ) "
    "if due and resolveSeen [ guid ] == true then seen [ # seen + 1 ] = entry "
    "elseif due and asked ~= nil then again [ # again + 1 ] = entry "
    "elseif due and tried == nil then fresh [ # fresh + 1 ] = entry "
    "elseif due and olderRoom > 0 then local last = older [ # older ] "
    "if # older < olderRoom or tried < ( ns . Number ( last . resolve_tried_at ) or 0 ) then "
    "if # older >= olderRoom then older [ # older ] = nil end "
    "local at = # older + 1 "
    "while at > 1 and ( ns . Number ( older [ at - 1 ] . resolve_tried_at ) or 0 ) > tried do "
    "older [ at ] = older [ at - 1 ] at = at - 1 end "
    "older [ at ] = entry end end end end "
    "local queue , kinds = { } , { } "
    "for i = 1 , # seen do queue [ # queue + 1 ] = seen [ i ] kinds [ # queue ] = 1 end "
    "for i = 1 , # fresh do queue [ # queue + 1 ] = fresh [ i ] kinds [ # queue ] = 2 end "
    "for i = 1 , # again do queue [ # queue + 1 ] = again [ i ] kinds [ # queue ] = 2 end "
    "for i = 1 , # older do queue [ # queue + 1 ] = older [ i ] kinds [ # queue ] = 3 end "
    "local passRoom = MAX_RESOLVES "
    "for i = 1 , # queue do local entry = queue [ i ] local kind = kinds [ i ] "
    "local guid = ns . String ( entry . guid ) "
    "if guid ~= nil and ( kind == 1 or passRoom > 0 ) then "
    "if kind ~= 1 then passRoom = passRoom - 1 end "
    "if kind == 3 then backlogAsked = backlogAsked + 1 end "
    "if resolveTried [ guid ] == nil then resolveTried [ guid ] = now or 0 "
    "else resolveRetried [ guid ] = true end "
    "resolveAsked = resolveAsked + 1 "
    "local _ , _ , _ , _ , _ , name , realm , eighth = GetPlayerInfoByGUID ( guid ) "
    "if secret ( name ) or secret ( realm ) or secret ( eighth ) then addSkipped ( ) end "
    "entry . resolve_tried_at = now "
    'if not secret ( name ) and type ( name ) == "string" and name ~= "" '
    "and name ~= UNKNOWNOBJECT then "
    "entry . resolved_name = ns . String ( name ) entry . resolved_at = now "
    "entry . resolve_tried_at = nil "
    'if not secret ( realm ) and type ( realm ) == "string" then '
    "entry . resolved_realm = ns . String ( realm ) end "
    'if not secret ( eighth ) and type ( eighth ) == "number" then '
    "entry . resolved_eighth = ns . Number ( eighth ) end end end end end"
)
PASS_TAIL = "pcall ( readRegion ) pcall ( resolveNames ) end"
PVP_PASS_TAIL = "if pvpWon ( ) then pcall ( recordMatch ) pcall ( resolveNames ) end end"
MEMBER_SEEN = "entry . guid = ns . String ( guid ) resolveSeen [ guid ] = true end"
ROW_SEEN = 'if type ( score . guid ) == "string" then resolveSeen [ score . guid ] = true end'
MAX_RESOLVES = 100
RETRY_SECONDS = 5
CONSTANTS = {
    "MAX_RESOLVES": f"local MAX_RESOLVES = {MAX_RESOLVES}",
    "RETRY_SECONDS": f"local RETRY_SECONDS = {RETRY_SECONDS}",
}
SESSION_STATE = (
    "local resolveTried = { }",
    "local resolveRetried = { }",
    "local resolveSeen = { }",
    "local resolveAsked = 0",
    "local backlogAsked = 0",
)


def _stores(text: str, piece: str) -> int:
    """How many times `piece` is a store (`piece` then `=`, never `==`)."""
    return len(re.findall(re.escape(piece) + r" = (?!=)", text))


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
    # readRegion from the roster pass only; resolveNames from it and from the
    # scoreboard pass, each through pcall.
    if text.count(" readRegion ") != 2 or text.count("pcall ( readRegion )") != 1:
        problems.append("Crew.lua: readRegion is reached other than from the pass")
    if text.count(" resolveNames ") != 3 or text.count("pcall ( resolveNames )") != 2:
        problems.append("Crew.lua: resolveNames is reached other than from the two passes")
    if not g._body(flow, "pass").endswith(PASS_TAIL):
        problems.append("Crew.lua: the pass does not end by asking the region and the names")
    if not g._body(flow, "pvpPass").endswith(PVP_PASS_TAIL):
        problems.append("Crew.lua: the scoreboard pass does not ask the names after the match")
    if MEMBER_SEEN not in g._body(flow, "readMember"):
        problems.append("Crew.lua: a group member's GUID is not marked as met this session")
    if ROW_SEEN not in g._body(flow, "recordMatch"):
        problems.append("Crew.lua: a scoreboard row's GUID is not marked as met this session")
    for name, decl in CONSTANTS.items():
        problems += g._crew_constant_problems(flow, name, decl)
    for decl in SESSION_STATE:
        if text.count(decl) != 1:
            problems.append(f"Crew.lua: the session state {decl!r} is not declared once")
    # The session state: written only where pinned, never stored in the book.
    resolve = g._body(flow, "resolveNames")
    for name, writes in (
        ("resolveTried", {"resolveTried [ guid ]": resolve}),
        ("resolveRetried", {"resolveRetried [ guid ]": resolve}),
        ("resolveAsked", {"resolveAsked": resolve}),
        ("backlogAsked", {"backlogAsked": resolve}),
    ):
        found = re.findall(rf"(?<![\w.]){name}(?: \[ [^]]* \])? = (?!=)", text)
        ((piece, body),) = writes.items()
        if len(found) != 2 or _stores(body, piece) != 1:
            problems.append(f"Crew.lua: {name} is written other than in resolveNames")
    seen = re.findall(r"(?<![\w.])resolveSeen(?: \[ [^]]* \])? = (?!=)", text)
    if len(seen) != 3:
        problems.append("Crew.lua: resolveSeen is written other than where met")
    if "WowLabCrewDB . resolve" in text:
        problems.append("Crew.lua: the session state is stored in the book")
    # The region and the name fields are stored only by the pinned two.
    for stored, owner, count in (
        ("WowLabCrewDB . region", "readRegion", 1),
        ("WowLabCrewDB . region_at", "readRegion", 1),
        ("entry . resolved_name", "resolveNames", 1),
        ("entry . resolved_realm", "resolveNames", 1),
        ("entry . resolved_eighth", "resolveNames", 1),
        ("entry . resolved_at", "resolveNames", 1),
        ("entry . resolve_tried_at", "resolveNames", 2),
    ):
        if _stores(text, stored) != count or _stores(g._body(flow, owner), stored) != count:
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


def test_m21_12_the_client_has_no_event_for_a_late_answer() -> None:
    """Why the retry asks again: the launch build's event list has no event
    that delivers a GUID's name later (fix round 1)."""
    events = (g.ADDON_ROOT / "forever_api" / "event.txt").read_text(encoding="utf-8").split()
    # GM_PLAYER_INFO is the GM tools' and UNIT_NAME_UPDATE names a unit
    # token, not a GUID.
    assert not [e for e in events if "BY_GUID" in e or e.startswith("GET_PLAYER_INFO")]
    assert "UNIT_NAME_UPDATE" in events


# Constructed: each edit breaks one rule and must fail the pins or Crew.lua's
# own rules.
EDITS: dict[str, tuple[str, str]] = {
    "resolve: asked in combat (no combat test)": (
        "local function resolveNames()\n    if not crewOn() then\n        return\n    end\n"
        "    if crewBlocked() then\n        return\n    end\n",
        "local function resolveNames()\n    if not crewOn() then\n        return\n    end\n",
    ),
    "resolve: no limit per pass": (
        "        if guid ~= nil and (kind == 1 or passRoom > 0) then\n",
        "        if guid ~= nil then\n",
    ),
    "resolve: no limit per login": (
        "    local olderRoom = MAX_RESOLVES - backlogAsked\n",
        "    local olderRoom = MAX_RESOLVES\n",
    ),
    "resolve: asked again at once": (
        "now - asked >= RETRY_SECONDS then\n",
        "now - asked >= 0 then\n",
    ),
    "resolve: a miss not sent to the back": (
        "            entry.resolve_tried_at = now\n            if not secret(name)",
        "            if not secret(name)",
    ),
    "resolve: the name stored untested": (
        '            if not secret(name) and type(name) == "string" and name ~= "" '
        "and name ~= UNKNOWNOBJECT then\n",
        '            if type(name) == "string" then\n',
    ),
    "resolve: the cap raised": (
        "local MAX_RESOLVES = 100\n",
        "local MAX_RESOLVES = 100000\n",
    ),
    "resolve: asked outside the passes": (
        "    pcall(nearFull)\n",
        "    pcall(nearFull)\n    pcall(resolveNames)\n",
    ),
    "resolve: a member not marked as met": (
        "            resolveSeen[guid] = true\n",
        "",
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
    """What the game gives in one session: the region, and
    GetPlayerInfoByGUID's eight values for each GUID it has seen this
    session (`known`; any other GUID gives nothing). A `deferred` GUID gives
    nothing at its first ask and its answer from the second on (the
    client's answer arriving later). A new login is a new Client."""

    region: Any = "US"
    known: dict[str, tuple[Any, ...]] = field(default_factory=dict)
    deferred: dict[str, tuple[Any, ...]] = field(default_factory=dict)
    in_combat: bool = False
    has_info: bool = True
    has_region: bool = True
    asked: list[str] = field(default_factory=list)

    def info(self, guid: str) -> tuple[Any, ...]:
        first = guid not in self.asked
        self.asked.append(guid)
        if guid in self.deferred:
            return () if first else self.deferred[guid]
        return self.known.get(guid, ())


@dataclass
class Session:
    """One login or /reload: the session state, never saved."""

    tried: dict[str, int] = field(default_factory=dict)  # resolveTried
    retried: set[str] = field(default_factory=set)  # resolveRetried
    seen: set[str] = field(default_factory=set)  # resolveSeen
    asked: int = 0  # resolveAsked
    backlog: int = 0  # backlogAsked


@dataclass
class Book:
    """WowLabCrewDB as the functions touch it."""

    on: bool = True
    region: Any = None
    region_at: int | None = None
    skipped: int = 0
    players: dict[str, dict[str, Any]] = field(default_factory=dict)


def read_region(book: Book, c: Client, now: int | None = 1_800_000_000) -> None:
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
        book.region_at = now


def _number(value: Any) -> int | float | None:
    """ns.Number."""
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def meet(book: Book, s: Session, key: str, guid: str) -> None:
    """readMember's or recordMatch's part: the GUID stored and marked as met
    this session."""
    book.players.setdefault(key, {"met": 1})["guid"] = guid
    s.seen.add(guid)


def resolve_names(book: Book, c: Client, s: Session, now: int | None = 1_800_000_000) -> None:
    """resolveNames, line for line."""
    if not book.on or c.in_combat:
        return
    if not c.has_info:
        return
    seen: list[dict[str, Any]] = []
    fresh: list[dict[str, Any]] = []
    again: list[dict[str, Any]] = []
    older: list[dict[str, Any]] = []
    older_room = MAX_RESOLVES - s.backlog
    for entry in book.players.values():
        guid = None
        if isinstance(entry, dict) and entry.get("resolved_name") is None:
            got = entry.get("guid")
            guid = got if isinstance(got, str) else None
        if guid is not None and guid.startswith("Player-"):
            asked = s.tried.get(guid)
            due = asked is None
            if (
                asked is not None
                and guid not in s.retried
                and now is not None
                and now - asked >= RETRY_SECONDS
            ):
                due = True
            tried = _number(entry.get("resolve_tried_at"))
            if due and guid in s.seen:
                seen.append(entry)
            elif due and asked is not None:
                again.append(entry)
            elif due and tried is None:
                fresh.append(entry)
            elif due and older_room > 0:
                assert tried is not None
                last = older[-1] if older else None
                if len(older) < older_room or (
                    last is not None and tried < (_number(last.get("resolve_tried_at")) or 0)
                ):
                    if len(older) >= older_room:
                        older.pop()
                    at = len(older)
                    while at > 0 and (_number(older[at - 1].get("resolve_tried_at")) or 0) > tried:
                        at -= 1
                    older.insert(at, entry)
    queue = [(e, 1) for e in seen] + [(e, 2) for e in fresh + again] + [(e, 3) for e in older]
    pass_room = MAX_RESOLVES
    for entry, kind in queue:
        guid = entry.get("guid")
        guid = guid if isinstance(guid, str) else None
        if guid is not None and (kind == 1 or pass_room > 0):
            if kind != 1:
                pass_room -= 1
            if kind == 3:
                s.backlog += 1
            if guid not in s.tried:
                s.tried[guid] = now or 0
            else:
                s.retried.add(guid)
            s.asked += 1
            values = (*c.info(guid), None, None, None, None, None, None, None, None)
            name, realm, eighth = values[5], values[6], values[7]
            if _is_secret(name) or _is_secret(realm) or _is_secret(eighth):
                book.skipped += 1
            entry["resolve_tried_at"] = now
            if not _is_secret(name) and isinstance(name, str) and name != "" and name != UNKNOWN:
                entry["resolved_name"] = name
                entry["resolved_at"] = now
                entry["resolve_tried_at"] = None
                if not _is_secret(realm) and isinstance(realm, str):
                    entry["resolved_realm"] = realm
                if not _is_secret(eighth) and _number(eighth) is not None:
                    entry["resolved_eighth"] = eighth
            # Lua stores nil as "no key".
            for k in [k for k, v in entry.items() if v is None]:
                del entry[k]


TAY_GUID = "Player-9998-0000000E"
# The owner's check of 2026-11-23, as GetPlayerInfoByGUID returned it.
TAY_INFO = ("Warlock", "WARLOCK", "Undead", "Scourge", 3, "Tay Soulrend", "", 22)
T = 1_800_000_000


def _info(name: str) -> tuple[Any, ...]:
    return (*TAY_INFO[:5], name, "", 10)


def _book(*guids: str) -> Book:
    return Book(players={f"Invented{i}-Second": {"met": 1, "guid": g} for i, g in enumerate(guids)})


def test_m21_12_the_owners_answer_is_kept_as_given() -> None:
    book = Book()
    session = Session()
    meet(book, session, "Tay-Soulrend", TAY_GUID)
    client = Client(known={TAY_GUID: TAY_INFO})
    resolve_names(book, client, session, now=T)
    assert book.players["Tay-Soulrend"] == {
        "met": 1,
        "guid": TAY_GUID,
        "resolved_name": "Tay Soulrend",
        "resolved_realm": "",
        "resolved_eighth": 22,
        "resolved_at": T,
    }
    read_region(book, client, now=T)
    assert (book.region, book.region_at) == ("US", T)


def test_m21_12_nothing_is_asked_in_combat() -> None:
    book = Book()
    session = Session()
    meet(book, session, "Tay-Soulrend", TAY_GUID)
    client = Client(known={TAY_GUID: TAY_INFO}, in_combat=True)
    resolve_names(book, client, session)
    read_region(book, client)
    assert client.asked == [] and book.region is None
    assert "resolved_name" not in book.players["Tay-Soulrend"]
    # Out of combat again: the next pass asks.
    client.in_combat = False
    resolve_names(book, client, session)
    assert book.players["Tay-Soulrend"]["resolved_name"] == "Tay Soulrend"


def test_m21_12_a_book_that_is_off_is_not_touched() -> None:
    book = Book(on=False, players={"Tay-Soulrend": {"guid": TAY_GUID}})
    client = Client(known={TAY_GUID: TAY_INFO})
    resolve_names(book, client, Session())
    read_region(book, client)
    assert client.asked == [] and book.region is None


def test_m21_12_a_miss_keeps_its_time_and_is_asked_once_more_later_the_same_session() -> None:
    book = _book("Player-9998-00000001")
    client = Client(deferred={"Player-9998-00000001": _info("Late Answer")})
    session = Session()
    resolve_names(book, client, session, now=T)
    entry = book.players["Invented0-Second"]
    assert entry == {"met": 1, "guid": "Player-9998-00000001", "resolve_tried_at": T}
    # A pass too soon does not ask again.
    resolve_names(book, client, session, now=T + RETRY_SECONDS - 1)
    assert client.asked == ["Player-9998-00000001"]
    # One more ask at a pass a few seconds later collects the late answer.
    resolve_names(book, client, session, now=T + RETRY_SECONDS)
    assert client.asked == ["Player-9998-00000001"] * 2
    assert entry["resolved_name"] == "Late Answer" and "resolve_tried_at" not in entry


def test_m21_12_a_miss_is_retried_once_only_per_session() -> None:
    book = _book("Player-9998-00000002")
    client = Client()  # never names it
    session = Session()
    for step in range(5):
        resolve_names(book, client, session, now=T + step * 60)
    assert client.asked == ["Player-9998-00000002"] * 2
    assert book.players["Invented0-Second"]["resolve_tried_at"] == T + 60
    # The next login asks it again, as one of the oldest misses.
    resolve_names(book, Client(), Session(), now=T + 3600)
    assert book.players["Invented0-Second"]["resolve_tried_at"] == T + 3600


def test_m21_12_more_than_the_cap_of_players_it_cannot_name_never_starve_a_new_one() -> None:
    """Fix round 1: 150 players met in earlier sessions that the client
    cannot name, at every login; a player met this session is named in the
    same pass, and one never asked comes before the old misses."""
    old = [f"Player-9998-{i:08X}" for i in range(150)]
    book = _book(*old)
    for login in range(3):
        client = Client()  # a cold start: it knows no one
        session = Session()
        now = T + login * 86_400
        resolve_names(book, client, session, now=now)  # 15 s after entering the world
        new_guid = f"Player-9997-{login:08X}"
        meet(book, session, f"Newcomer{login}-Second", new_guid)
        client.known[new_guid] = _info(f"Newcomer{login} Second")
        resolve_names(book, client, session, now=now + 1)  # the next roster pass
        assert book.players[f"Newcomer{login}-Second"]["resolved_name"] == f"Newcomer{login} Second"
        # Never more than the cap of old misses in one login, and never a
        # player twice in a pass.
        assert session.backlog <= MAX_RESOLVES
        assert len(client.asked) == len(set(client.asked))


def test_m21_12_the_old_misses_are_asked_oldest_first_across_logins() -> None:
    """Each login asks at most the cap of old misses, the oldest asks
    first, so every one of them is asked again within a few logins."""
    old = [f"Player-9998-{i:08X}" for i in range(250)]
    book = _book(*old)
    resolve_names(book, Client(), Session(), now=T)  # first pass: 100 never asked
    resolve_names(book, Client(), Session(), now=T + 10)  # another login: 100 more
    resolve_names(book, Client(), Session(), now=T + 20)  # the last 50 never asked, then 50 old
    times = sorted(e["resolve_tried_at"] for e in book.players.values())
    assert times.count(T + 20) == 100 and times.count(T + 10) == 100 and times.count(T) == 50
    # The next login takes the 50 asked at T first, then the oldest of T + 10.
    client = Client()
    resolve_names(book, client, Session(), now=T + 30)
    asked_at = [e for e in book.players.values() if e["resolve_tried_at"] == T + 30]
    assert len(client.asked) == MAX_RESOLVES and len(asked_at) == MAX_RESOLVES
    assert not [e for e in book.players.values() if e["resolve_tried_at"] == T]


def test_m21_12_players_met_this_session_are_not_limited() -> None:
    """A full battleground's scoreboard: every row met now is asked in the
    one pass, beyond the limit, and before the never-asked."""
    book = _book(*[f"Player-9998-{i:08X}" for i in range(MAX_RESOLVES)])
    client = Client()
    session = Session()
    rows = [f"Player-9997-{i:08X}" for i in range(80)]
    for i, guid in enumerate(rows):
        meet(book, session, f"Row{i}-Second", guid)
        client.known[guid] = _info(f"Row{i} Second")
    resolve_names(book, client, session)
    assert client.asked[:80] == rows
    assert len(client.asked) == 80 + MAX_RESOLVES
    assert all(book.players[f"Row{i}-Second"].get("resolved_name") for i in range(80))


@pytest.mark.parametrize(
    ("answer", "stored", "skipped"),
    [
        ((*TAY_INFO[:5], SECRET, "", 22), {"resolve_tried_at": 1}, 1),
        ((*TAY_INFO[:5], "", "", 22), {"resolve_tried_at": 1}, 0),
        ((*TAY_INFO[:5], UNKNOWN, "", 22), {"resolve_tried_at": 1}, 0),
        ((*TAY_INFO[:5], None, None, None), {"resolve_tried_at": 1}, 0),
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
    assert {k: v for k, v in entry.items() if k.startswith("resolve")} == stored
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
