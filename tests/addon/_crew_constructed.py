"""Constructed crew-book sources for the lab-addon grader (M19-01; schema 2, M20-04).

CONSTRUCTED INPUT, labelled as such (L8, ADR-0036 term 3): nothing here is
addon code, nothing is installed or run, and no line holds a player's name,
realm or key. tests/addon/test_lab_addon.py loads this module by path and
grades these sources with the rules it builds from `BOOK`; it does not name
the book itself.
"""

from __future__ import annotations

# The crew book's saved variable (docs/LAB_PLAN.md §20.1), written whole.
BOOK = "WowLabCrewDB"

# Pinned helper texts that name the book (rendered tokens).
# Follow-up (M19-10 security review): a book whose schema is not this file's
# is left alone, so crewOn, crewLoaded and the slash handler's head all test
# it against the pinned file-level constant CREW_SCHEMA (`local CREW_SCHEMA =
# 1`, in the grader).
CREW_ON = (
    "local function crewOn ( ) "
    'return type ( WowLabCrewDB ) == "table" and WowLabCrewDB . schema == CREW_SCHEMA '
    "and WowLabCrewDB . on == true end"
)
CREW_BOOK_SCHEMA_TEST = (
    'if type ( WowLabCrewDB ) == "table" and WowLabCrewDB . schema ~= CREW_SCHEMA then'
)
CREW_PROBE_1 = (
    "local function crewLoaded ( _ , name ) "
    'if name ~= "WowLab" or type ( WowLabCrewDB ) ~= "table" '
    "or WowLabCrewDB . schema ~= CREW_SCHEMA then return end "
    # Fix round 5: the shape of Core.lua's loadProbe (§13.1), so the
    # unrelaxed M11-13 stored-value check accepts it.
    "local probe = { loads = 1 } "
    "local prior = WowLabCrewDB . probe "
    'if type ( prior ) == "table" and type ( prior . loads ) == "number" then '
    "probe . loads = ns . Number ( prior . loads + 1 ) "
    "else probe . lost = true end "
    "WowLabCrewDB . probe = probe end"
)

# M20-04 (docs/LAB_PLAN.md §21.2, "Migration"; amends #631's pins). Schema 2:
# the one pinned migration. It turns a schema 1 book into schema 2 by adding
# nothing but the new number and an empty `matches`, and leaves a book of any
# other schema alone. It is called from exactly two places, both pinned: the
# probe handler, only when the book is on (CREW_PROBE_2), and the head of the
# slash handler, only for the word `on` (CREW_COMMAND_HEAD_2 in the grader),
# before the schema refusal is tested. A book that is off is not touched.
CREW_MIGRATE = (
    "local function crewMigrate ( ) "
    'if type ( WowLabCrewDB ) ~= "table" or WowLabCrewDB . schema ~= 1 then return end '
    "WowLabCrewDB . schema = CREW_SCHEMA "
    "WowLabCrewDB . matches = { } end"
)
# The probe handler at schema 2: it migrates a book that is on, then raises
# `probe.loads` in place (so `probe.pvp` survives a load) for a book of this
# schema only.
CREW_PROBE_2 = (
    "local function crewLoaded ( _ , name ) "
    'if name ~= "WowLab" or type ( WowLabCrewDB ) ~= "table" then return end '
    "if WowLabCrewDB . on == true then crewMigrate ( ) end "
    "if WowLabCrewDB . schema ~= CREW_SCHEMA then return end "
    "local prior = WowLabCrewDB . probe "
    'if type ( prior ) == "table" and type ( prior . loads ) == "number" then '
    "prior . loads = ns . Number ( prior . loads + 1 ) "
    "prior . lost = nil "
    "else WowLabCrewDB . probe = { loads = 1 , lost = true } end end"
)
# The scoreboard probe's only two stores (§21.2, `probe.pvp`): a list of the
# snapshot taken after entry and, once a winner is reported, the second.
CREW_PVP_ENTRY_STORE = "WowLabCrewDB . probe . pvp = { pvpSnapshot ( ) }"
CREW_PVP_WINNER_STORE = "WowLabCrewDB . probe . pvp [ 2 ] = pvpSnapshot ( )"


# Constructed (M19-01): a Crew.lua at schema 1 that uses every relaxation and
# keeps every rule. Not addon code; never installed or run. It holds no
# player's name. M20-04 keeps it, unchanged, as the proof that the schema 1
# rules (CREW_PINS_1 in the grader) still hold the real schema 1 file until
# M20-05; `_CREW_SOURCE` below is the schema 2 source built from it.
_CREW_READ = """local function readGroup(count)
    if not crewOn() then
        return
    end
    if crewBlocked() then
        return
    end
"""
_CREW_MEMBER = '        local unit = "party" .. i\n'
_CREW_COMMAND_HEAD = "function ns.CrewCommand(word)\n"
_CREW_SOURCE_1 = (
    """local _, ns = ...

local CREW_SCHEMA = 1

local function crewOn()
    return type(WowLabCrewDB) == "table" and WowLabCrewDB.schema == CREW_SCHEMA
        and WowLabCrewDB.on == true
end

local function crewBlocked()
    if type(InCombatLockdown) ~= "function" then
        return true
    end
    local ok, locked = pcall(InCombatLockdown)
    if not ok or locked then
        return true
    end
    return false
end

local function secret(value)
    if type(issecretvalue) ~= "function" then
        return false
    end
    local ok, hidden = pcall(issecretvalue, value)
    return not ok or hidden == true
end

local function nameAbsent()
    if not crewOn() then
        return
    end
    if crewBlocked() then
        return
    end
    if type(UnitFullName) ~= "function" then
        return "the client has no UnitFullName function"
    end
    if type(GetGuildInfo) ~= "function" then
        return "the client has no GetGuildInfo function"
    end
    return nil
end

local function makeRoom()
    if not crewOn() then
        return
    end
    if crewBlocked() then
        return
    end
    local kept = 0
    for _ in pairs(WowLabCrewDB.players) do
        kept = kept + 1
    end
    return kept < 5000
end

local function levelOf(member)
    if not crewOn() then
        return
    end
    if crewBlocked() then
        return
    end
    local level = UnitLevel(member)
    if not secret(level) then
        WowLabCrewDB.lastLevel = ns.Number(level)
    end
end

"""
    + _CREW_READ
    + """    local home = GetNormalizedRealmName()
    local book = WowLabCrewDB.players
    for i = 1, count do
"""
    + _CREW_MEMBER
    + """        levelOf(unit)
        local me = UnitIsUnit(unit, "player")
        local name, realm = UnitFullName(unit)
        if not secret(me) and not me and not secret(name) and not secret(realm) then
            local key = name .. "-" .. realm
            if book[key] == nil and makeRoom() then
                book[key] = { met = 0 }
            end
            local entry = book[key]
            entry.name = ns.String(name)
            entry.realm = ns.String(realm)
            local _, classFile = UnitClass(unit)
            if not secret(classFile) then
                entry.class = ns.String(classFile)
            end
            local role = UnitGroupRolesAssigned(unit)
            if not secret(role) and role ~= "NONE" then
                entry.role = ns.String(role)
            end
            local _, kind = GetInstanceInfo()
            if kind ~= "none" then
                entry.where_kind = ns.String(kind)
            end
            local other = UnitName(unit)
            if not secret(other) and other ~= nil then
                entry.alias = ns.String(other)
            end
            local guid = UnitGUID(unit)
            if not secret(guid) and type(guid) == "string" then
                entry.guid = ns.String(guid)
            end
            local faction = UnitFactionGroup(unit)
            if not secret(faction) then
                entry.faction = ns.String(faction)
            end
            local honor = UnitHonorLevel(unit)
            if not secret(honor) then
                entry.honor_level = ns.Number(honor)
            end
            local done = pcall(function()
                local guild = GetGuildInfo(unit)
                if not secret(guild) then
                    entry.guild = ns.String(guild)
                end
            end)
            entry.read = ns.Bool(done)
            entry.last = ns.Number(time())
            entry.day = ns.String(date("%Y-%m-%d", entry.last))
            book[name .. "-" .. realm].seen = true
            WowLabCrewDB.current[key] = true
        end
    end
    if not secret(home) then
        WowLabCrewDB.home = ns.String(home)
    end
    local first = "raid" .. 1
    local level = UnitLevel(first)
    local target = "target"
    local targetName = UnitName(target)
    if not secret(level) and not secret(targetName) then
        WowLabCrewDB.level = ns.Number(level)
    end
    local isPlayer = UnitIsPlayer("target")
    if not secret(isPlayer) and isPlayer and not secret(targetName) and targetName ~= UNKNOWNOBJECT then
        WowLabCrewDB.targetSeen = true
    end
    if IsInGroup() and IsInRaid() then
        WowLabCrewDB.inRaid = true
    end
    levelOf("target")
    levelOf(first)
end

local function countBook()
    if not crewOn() then
        return
    end
    if crewBlocked() then
        return
    end
    local players = WowLabCrewDB.players
    local kept = 0
    for _ in pairs(players) do
        kept = kept + 1
    end
    for key in pairs(WowLabCrewDB.current) do
        kept = kept + #key
    end
    local oldest
    for key, entry in pairs(players) do
        if oldest == nil or entry.last < players[oldest].last then
            oldest = key
        end
    end
    if oldest ~= nil and kept > 5000 then
        players[oldest] = nil
    end
    return kept
end

local function onRoster()
    if not crewOn() then
        return
    end
    if crewBlocked() then
        return
    end
    readGroup(4)
    print(countBook())
end

local function onWorld()
    if not crewOn() then
        return
    end
    if crewBlocked() then
        return
    end
    ns.On("GROUP_ROSTER_UPDATE", onRoster)
end

local function crewLoaded(_, name)
    if name ~= "WowLab" or type(WowLabCrewDB) ~= "table" or WowLabCrewDB.schema ~= CREW_SCHEMA then
        return
    end
    local probe = { loads = 1 }
    local prior = WowLabCrewDB.probe
    if type(prior) == "table" and type(prior.loads) == "number" then
        probe.loads = ns.Number(prior.loads + 1)
    else
        probe.lost = true
    end
    WowLabCrewDB.probe = probe
end

ns.On("ADDON_LOADED", crewLoaded)
ns.On("PLAYER_ENTERING_WORLD", onWorld)

"""
    + _CREW_COMMAND_HEAD
    + """    if type(word) ~= "string" then
        return
    end
    if type(WowLabCrewDB) == "table" and WowLabCrewDB.schema ~= CREW_SCHEMA then
        print("constructed: this book is from another version")
        return
    end
    if type(UnitFullName) ~= "function" then
        print("the client has no UnitFullName function")
    end
    if word == "on" then
        if type(WowLabCrewDB) ~= "table" then
            WowLabCrewDB = { schema = CREW_SCHEMA, players = {}, current = {}, started = ns.Number(time()) }
        end
        WowLabCrewDB.on = true
        print(date("%Y-%m-%d", WowLabCrewDB.started))
        ns.On("GROUP_ROSTER_UPDATE", onRoster)
        return
    end
    if word == "off" and type(WowLabCrewDB) == "table" then
        WowLabCrewDB.on = false
        for key in pairs(WowLabCrewDB.current) do
            WowLabCrewDB.current[key] = nil
        end
    end
    print(nameAbsent() or "Crew book.")
    return "constructed"
end
"""
)
_CREW_TAIL = '    return "constructed"\nend\n'  # the end of the file: code is added after it
_CREW_GATE_BODY = (
    "    if not crewOn() then\n        return\n    end\n"
    "    if crewBlocked() then\n        return\n    end\n"
)
assert _CREW_SOURCE_1.endswith(_CREW_TAIL)


# Constructed (M20-04): the schema 2 Crew.lua, docs/LAB_PLAN.md §21.2 short of
# opponents: the limit constants, the migration, the near-full line and
# `warned`, and the scoreboard probe writing `probe.pvp`. Built from
# _CREW_SOURCE_1 by the edits below, so M19-01's edit cases still apply to it.
# Not addon code; never installed or run. It holds no player's name.
_CREW_PVP_STATE = """local MAX_PLAYERS = 20000
local MAX_MATCHES = 500
local NEAR_FULL_PERCENT = 90
local NEAR_FULL_SECONDS = 604800

local pvpEntered = false
local pvpWinnerRead = false
local pvpRequested = false
local pvpScoreEvents = 0
local pvpCompleteEvents = 0
"""
_CREW_MIGRATE_SOURCE = """local function crewMigrate()
    if type(WowLabCrewDB) ~= "table" or WowLabCrewDB.schema ~= 1 then
        return
    end
    WowLabCrewDB.schema = CREW_SCHEMA
    WowLabCrewDB.matches = {}
end
"""
_CREW_PVP_INSTANCE = """local function pvpInstance()
    if not crewOn() then
        return
    end
    if crewBlocked() then
        return
    end
    if type(GetInstanceInfo) ~= "function" then
        return false
    end
    local _, kind = GetInstanceInfo()
    if secret(kind) then
        return false
    end
    return kind == "pvp" or kind == "arena"
end
"""
_CREW_PVP_WON = """local function pvpWon()
    if not crewOn() then
        return
    end
    if crewBlocked() then
        return
    end
    if type(GetBattlefieldWinner) ~= "function" then
        return false
    end
    local ok, winner = pcall(GetBattlefieldWinner)
    if ok and not secret(winner) then
        return winner ~= nil
    end
    return false
end
"""
# What one probe run stores, and nothing else (§21.2): whether each candidate
# call exists; the first row's field names and their type names (the table
# form) or the type name at each position (the several-values form); the row
# count and whether a request was made first; the two event counts; three
# whole numbers (the first row's side, the player's own, the winner); and
# three yes-or-no answers about the first row's name. A row's GUID field is
# never indexed (the walk hands each value to `type` only).
_CREW_PVP_SNAPSHOT = """local function pvpSnapshot()
    if not crewOn() then
        return
    end
    if crewBlocked() then
        return
    end
    local snap = { calls = {}, positions = {}, fields = {} }
    snap.calls.GetScoreInfo = type(C_PvP) == "table" and type(C_PvP.GetScoreInfo) == "function"
    snap.calls.GetNumBattlefieldScores = type(GetNumBattlefieldScores) == "function"
    snap.calls.GetBattlefieldScore = type(GetBattlefieldScore) == "function"
    snap.calls.RequestBattlefieldScoreData = type(RequestBattlefieldScoreData) == "function"
    snap.calls.GetBattlefieldWinner = type(GetBattlefieldWinner) == "function"
    snap.calls.GetBattlefieldArenaFaction = type(GetBattlefieldArenaFaction) == "function"
    snap.calls.IsActiveBattlefieldArena = type(IsActiveBattlefieldArena) == "function"
    snap.requested = pvpRequested == true
    snap.score_events = ns.Number(pvpScoreEvents)
    snap.complete_events = ns.Number(pvpCompleteEvents)
    if snap.calls.GetNumBattlefieldScores then
        local okRows, rows = pcall(GetNumBattlefieldScores)
        if okRows and not secret(rows) and type(rows) == "number" then
            snap.rows = ns.Number(rows)
        end
    end
    local read = false
    local name = nil
    local side = nil
    if snap.calls.GetScoreInfo then
        local okRow, row = pcall(C_PvP.GetScoreInfo, 1)
        if okRow and not secret(row) and type(row) == "table" then
            for field, value in pairs(row) do
                if not secret(field) and type(field) == "string" then
                    snap.fields[field] = type(value)
                end
            end
            read = true
            name = row.name
            side = row.faction
        end
    end
    if snap.calls.GetBattlefieldScore then
        local okScore, packed = pcall(function()
            return ns.Pack(GetBattlefieldScore(1))
        end)
        if okScore and type(packed) == "table" then
            for i = 1, ns.Number(packed.n) or 0 do
                snap.positions[i] = type(packed[i])
            end
            if not read then
                name = packed[1]
                side = packed[6]
            end
        end
    end
    snap.name_string = type(name) == "string"
    snap.name_secret = secret(name)
    snap.name_dash = type(name) == "string" and not secret(name) and string.find(name, "-", 1, true) ~= nil
    if not secret(side) and type(side) == "number" then
        snap.first_side = ns.Number(side)
    end
    if snap.calls.GetBattlefieldArenaFaction then
        local okMine, mine = pcall(GetBattlefieldArenaFaction)
        if okMine and not secret(mine) and type(mine) == "number" then
            snap.own_side = ns.Number(mine)
        end
    end
    if snap.calls.GetBattlefieldWinner then
        local okWinner, winner = pcall(GetBattlefieldWinner)
        if okWinner and not secret(winner) and type(winner) == "number" then
            snap.winner = ns.Number(winner)
        end
    end
    if snap.calls.RequestBattlefieldScoreData and not pvpRequested then
        pvpRequested = pcall(RequestBattlefieldScoreData) == true
    end
    return snap
end
"""
# Twice per match: after entry, and after a winner is reported; again in the
# next match (leaving the instance resets it). Not pinned: the schedule is
# M20-05's, held by the rules on the two pinned stores.
_CREW_PVP_PASS = (
    """local function pvpPass()
    if not crewOn() then
        return
    end
    if crewBlocked() then
        return
    end
    if not pvpInstance() then
        pvpEntered = false
        pvpWinnerRead = false
        return
    end
    if not pvpEntered then
        pvpEntered = true
        pvpWinnerRead = false
        pvpRequested = false
        pvpScoreEvents = 0
        pvpCompleteEvents = 0
        WowLabCrewDB.probe.pvp = { pvpSnapshot() }
        return
    end
    if not pvpWinnerRead and pvpWon() then
        pvpWinnerRead = true
        WowLabCrewDB.probe.pvp[2] = pvpSnapshot()
    end
    if pvpWon() then
        recordMatch()
    end
end

local function onScore()
"""
    + _CREW_GATE_BODY
    + """    pvpScoreEvents = pvpScoreEvents + 1
    pvpPass()
end

local function onComplete()
"""
    + _CREW_GATE_BODY
    + """    pvpCompleteEvents = pvpCompleteEvents + 1
    pvpPass()
end

"""
)
# The near-full line (§21.1): at 90% of either limit, at most once in seven
# days, by `warned`.
_CREW_NEAR_FULL = (
    """local function nearFull()
"""
    + _CREW_GATE_BODY
    + """    if type(time) ~= "function" then
        return
    end
    local now = ns.Number(time())
    local last = ns.Number(WowLabCrewDB.warned) or 0
    local held = 0
    for _ in pairs(WowLabCrewDB.players) do
        held = held + 1
    end
    local kept = #WowLabCrewDB.matches
    if
        now ~= nil
        and now - last >= NEAR_FULL_SECONDS
        and (held * 100 >= MAX_PLAYERS * NEAR_FULL_PERCENT or kept * 100 >= MAX_MATCHES * NEAR_FULL_PERCENT)
    then
        WowLabCrewDB.warned = now
        print("constructed: the crew book is nearly full; open wowlab so nothing is lost.")
    end
end

"""
)
# M20-11 (§21.2, as built): one finished match. The scoreboard read through
# pcall after a test that the call exists, each row's values through the
# secret test, the `matched` marker read and then written before any
# counter, and the oldest match dropped before one is added. Constructed:
# no player's name or GUID; the keys are whatever the rows hold.
_MATCH_MARK = "    WowLabCrewDB.matched = { map_id = 1, at = now }\n"
_MATCH_READ = '    if type(WowLabCrewDB.matched) == "table" then\n        return\n    end\n'
_MATCH_DROP = "    while #list >= MAX_MATCHES do\n        table.remove(list, 1)\n    end\n"
_MATCH_COPY = (
    "                if not secret(field) and not secret(value) and type(field) == "
    '"string" then\n'
    "                    copy[field] = ns.Number(value)\n"
    "                end\n"
)
_MATCH_COUNTER = "            entry.allied = (ns.Number(entry.allied) or 0) + 1\n"
_CREW_MATCH = (
    """local function recordMatch()
"""
    + _CREW_GATE_BODY
    + """    if type(C_PvP) ~= "table" or type(C_PvP.GetScoreInfo) ~= "function" then
        return
    end
    if type(C_PvP.IsSoloShuffle) == "function" then
        print("constructed: a solo shuffle may be asked about")
    end
    local now = ns.Number(time())
"""
    + _MATCH_READ
    + """    local rows = {}
    for i = 1, 2 do
        local okRow, row = pcall(C_PvP.GetScoreInfo, i)
        if okRow and not secret(row) and type(row) == "table" then
            local copy = {}
            for field, value in pairs(row) do
"""
    + _MATCH_COPY
    + """            end
            rows[#rows + 1] = copy
        end
    end
"""
    + _MATCH_MARK
    + """    local team = {}
    for i = 1, #rows do
        local key = ns.String(rows[i].name)
        local entry = nil
        if key ~= nil then
            entry = WowLabCrewDB.players[key]
        end
        if type(entry) == "table" then
"""
    + _MATCH_COUNTER
    + """            team[#team + 1] = key
        end
    end
    local list = WowLabCrewDB.matches
"""
    + _MATCH_DROP
    + """    list[#list + 1] = { at = now, kind = "bg", team = team }
end

"""
)
_CREW_PROBE_SOURCE_1 = """local function crewLoaded(_, name)
    if name ~= "WowLab" or type(WowLabCrewDB) ~= "table" or WowLabCrewDB.schema ~= CREW_SCHEMA then
        return
    end
    local probe = { loads = 1 }
    local prior = WowLabCrewDB.probe
    if type(prior) == "table" and type(prior.loads) == "number" then
        probe.loads = ns.Number(prior.loads + 1)
    else
        probe.lost = true
    end
    WowLabCrewDB.probe = probe
end
"""
_CREW_PROBE_SOURCE_2 = """local function crewLoaded(_, name)
    if name ~= "WowLab" or type(WowLabCrewDB) ~= "table" then
        return
    end
    if WowLabCrewDB.on == true then
        crewMigrate()
    end
    if WowLabCrewDB.schema ~= CREW_SCHEMA then
        return
    end
    local prior = WowLabCrewDB.probe
    if type(prior) == "table" and type(prior.loads) == "number" then
        prior.loads = ns.Number(prior.loads + 1)
        prior.lost = nil
    else
        WowLabCrewDB.probe = { loads = 1, lost = true }
    end
end
"""
_CREW_HEAD_STRING_TEST = '    if type(word) ~= "string" then\n        return\n    end\n'
_CREW_HEAD_MIGRATE = (
    '    if string.match(string.lower(word), "^%s*%S+%s+on%s*$") ~= nil then\n'
    "        crewMigrate()\n    end\n"
)
_CREW_ON_WORLD_ARMS = '    ns.On("GROUP_ROSTER_UPDATE", onRoster)\nend\n\nlocal function crewLoaded'
_CREW_SCHEMA_2_EDITS: list[tuple[str, str]] = [
    ("local CREW_SCHEMA = 1\n", "local CREW_SCHEMA = 2\n" + _CREW_PVP_STATE),
    (
        "local function nameAbsent()\n",
        _CREW_MIGRATE_SOURCE
        + "\n"
        + _CREW_PVP_INSTANCE
        + "\n"
        + _CREW_PVP_WON
        + "\n"
        + _CREW_PVP_SNAPSHOT
        + "\nlocal function nameAbsent()\n",
    ),
    (_CREW_PROBE_SOURCE_1, _CREW_PROBE_SOURCE_2),
    (
        "local function onWorld()\n",
        _CREW_MATCH + _CREW_PVP_PASS + _CREW_NEAR_FULL + "local function onWorld()\n",
    ),
    (
        _CREW_ON_WORLD_ARMS,
        "    nearFull()\n    pvpPass()\n"
        '    ns.On("UPDATE_BATTLEFIELD_SCORE", onScore)\n'
        '    ns.On("PVP_MATCH_COMPLETE", onComplete)\n' + _CREW_ON_WORLD_ARMS,
    ),
    (_CREW_HEAD_STRING_TEST, _CREW_HEAD_STRING_TEST + _CREW_HEAD_MIGRATE),
    ("players = {}, current = {}, started", "players = {}, current = {}, matches = {}, started"),
]


def _schema_2(text: str) -> str:
    for found, replacement in _CREW_SCHEMA_2_EDITS:
        assert text.count(found) == 1, found
        text = text.replace(found, replacement, 1)
    return text


_CREW_SOURCE = _schema_2(_CREW_SOURCE_1)
assert _CREW_SOURCE.endswith(_CREW_TAIL)


# Constructed edits of _CREW_SOURCE (boundary tests of the gate): each lets a
# relaxed call run with the book off, in combat, or out of the gate's reach.
_CREW_GATE_CASES: dict[str, list[tuple[str, str]]] = {
    "the on test removed": [
        (
            "    if not crewOn() then\n        return\n    end\n    if crewBlocked() then\n"
            "        return\n    end\n    local home",
            "    if crewBlocked() then\n        return\n    end\n    local home",
        )
    ],
    "the combat test removed": [
        ("    if crewBlocked() then\n        return\n    end\n    local home", "    local home")
    ],
    "the tests in the other order": [
        (
            _CREW_READ,
            "local function readGroup(count)\n    if crewBlocked() then\n        return\n    end\n"
            "    if not crewOn() then\n        return\n    end\n",
        )
    ],
    "a call before the tests": [
        (
            _CREW_READ,
            _CREW_READ.replace(
                "    if not crewOn",
                "    local early = GetNormalizedRealmName()\n    if not crewOn",
            ),
        )
    ],
    "a call at file level": [(_CREW_TAIL, _CREW_TAIL + "local early = GetNormalizedRealmName()\n")],
    "a call in a function without the tests": [
        (
            _CREW_TAIL,
            _CREW_TAIL + "local function peek()\n    local r = GetNormalizedRealmName()\n"
            "    return r\nend\n",
        )
    ],
    "a closure handed to a timer": [
        (
            "            local other = UnitName(unit)\n",
            "            C_Timer.After(2, function()\n                local late = UnitName(unit)\n"
            "            end)\n            local other = nil\n",
        )
    ],
    # Fix round 1 (code review): a literal handed to anything but `pcall(`.
    "a closure handed to coroutine.wrap": [
        (
            "            local done = pcall(function()\n",
            "            local done = coroutine.wrap(function()\n",
        )
    ],
    "a closure handed to xpcall": [
        (
            "            local done = pcall(function()\n",
            "            local done = xpcall(function()\n",
        )
    ],
    "a unit call on a party member outside the tests": [
        (
            _CREW_TAIL,
            _CREW_TAIL + 'local function peek()\n    local p = "party" .. 1\n'
            "    local l = UnitLevel(p)\n    return l\nend\n",
        )
    ],
    "a name call aliased": [
        (
            "            local other = UnitName(unit)\n",
            "            local fn = GetGuildInfo\n            local other = fn(unit)\n",
        )
    ],
    "a name call handed to pcall": [
        (
            "            local other = UnitName(unit)\n",
            "            local okRealm = pcall(GetNormalizedRealmName)\n"
            "            local other = UnitName(unit)\n",
        )
    ],
    "the on test always true": [
        (
            '    return type(WowLabCrewDB) == "table" and WowLabCrewDB.schema == CREW_SCHEMA\n'
            "        and WowLabCrewDB.on == true\n",
            "    return true\n",
        )
    ],
    "the on test without the value": [
        ("        and WowLabCrewDB.on == true\n", "        and WowLabCrewDB.on ~= nil\n")
    ],
    # Follow-up (M19-10 security review): a book of another schema is left alone.
    "the on test without the schema test": [(" and WowLabCrewDB.schema == CREW_SCHEMA\n", "\n")],
    "the on test with >= for the schema": [
        ("WowLabCrewDB.schema == CREW_SCHEMA\n", "WowLabCrewDB.schema >= CREW_SCHEMA\n")
    ],
    "the on test with ~= for the schema": [
        ("WowLabCrewDB.schema == CREW_SCHEMA\n", "WowLabCrewDB.schema ~= CREW_SCHEMA\n")
    ],
    # M20-04: the schema 2 forms; the schema 1 ones are in _CREW_GATE_CASES_1.
    "the schema constant reassigned": [
        ("local CREW_SCHEMA = 2\n", "local CREW_SCHEMA = 2\nCREW_SCHEMA = 3\n")
    ],
    "the schema constant shadowed": [
        (
            "local function nameAbsent()\n",
            "local function nameAbsent()\n    local CREW_SCHEMA = 2\n",
        )
    ],
    "the schema constant not the file's": [("local CREW_SCHEMA = 2\n", "local CREW_SCHEMA = 3\n")],
    "the schema constant as a parameter": [
        ("local function countBook()\n", "local function countBook(CREW_SCHEMA)\n")
    ],
    "the probe handler without the schema test": [
        (
            "    if WowLabCrewDB.schema ~= CREW_SCHEMA then\n        return\n    end\n"
            "    local prior",
            "    local prior",
        )
    ],
    "the slash handler without the schema test": [
        (
            '    if type(WowLabCrewDB) == "table" and WowLabCrewDB.schema ~= CREW_SCHEMA then\n'
            '        print("constructed: this book is from another version")\n'
            "        return\n    end\n",
            "",
        )
    ],
    # Follow-up (#631): the schema branch says one fixed line, then returns.
    "the schema branch with no print": [
        ('        print("constructed: this book is from another version")\n', "")
    ],
    "the schema branch printing a variable": [
        (
            '        print("constructed: this book is from another version")\n',
            "        print(word)\n",
        )
    ],
    "the schema branch printing a concatenation": [
        (
            '        print("constructed: this book is from another version")\n',
            '        print("constructed: schema " .. WowLabCrewDB.schema)\n',
        )
    ],
    "the schema branch with another statement": [
        (
            '        print("constructed: this book is from another version")\n',
            '        print("constructed: this book is from another version")\n        nameAbsent()\n',
        )
    ],
    "the schema branch with a store": [
        (
            '        print("constructed: this book is from another version")\n',
            '        print("constructed: this book is from another version")\n'
            "        WowLabCrewDB.on = false\n",
        )
    ],
    "a book written from an ungated helper": [
        (
            "local function countBook()\n",
            "local function touch()\n    WowLabCrewDB.skipped = 0\nend\n\nlocal function countBook()\n",
        )
    ],
    "a book entry written from an ungated helper": [
        (
            "local function countBook()\n",
            "local function touch(key)\n    local e = {}\n    e = WowLabCrewDB.players\n"
            "    e.note = 1\nend\n\nlocal function countBook()\n",
        )
    ],
    "the combat test never blocking": [
        ("    if not ok or locked then\n", "    if not ok and locked then\n")
    ],
    "the combat test true when it cannot ask": [
        (
            '    if type(InCombatLockdown) ~= "function" then\n        return true\n',
            '    if type(InCombatLockdown) ~= "function" then\n        return false\n',
        )
    ],
    "the book shadowed by a local": [
        ("local _, ns = ...\n", "local _, ns = ...\nlocal WowLabCrewDB = { on = true }\n")
    ],
    "InCombatLockdown shadowed": [
        (
            "local _, ns = ...\n",
            "local _, ns = ...\nlocal function InCombatLockdown()\n    return false\nend\n",
        )
    ],
    "issecretvalue shadowed": [
        ("local _, ns = ...\n", "local _, ns = ...\nlocal issecretvalue = nil\n")
    ],
    # Fix round 1 (security review, item 1): the pinned helpers rebound.
    "crewOn redefined by a function statement": [
        (_CREW_TAIL, _CREW_TAIL + "function crewOn()\n    return true\nend\n")
    ],
    "crewBlocked redefined by a function statement": [
        (_CREW_TAIL, _CREW_TAIL + "function crewBlocked()\n    return false\nend\n")
    ],
    "secret redefined by a function statement": [
        (_CREW_TAIL, _CREW_TAIL + "function secret()\n    return false\nend\n")
    ],
    "crewOn assigned": [(_CREW_TAIL, _CREW_TAIL + "crewOn = function()\n    return true\nend\n")],
    "crewOn in a multiple assignment": [
        (_CREW_TAIL, _CREW_TAIL + "local spare\ncrewOn, spare = nil, 1\n")
    ],
    "crewOn as a parameter": [
        ("local function nameAbsent()\n", "local function nameAbsent(crewOn)\n")
    ],
    "crewBlocked as a loop variable": [
        (_CREW_TAIL, _CREW_TAIL + "for crewBlocked = 1, 2 do\nend\n")
    ],
    "crewOn declared twice": [
        ("local function crewOn()\n", "local crewOn\nlocal function crewOn()\n")
    ],
    "secret always false": [("    return not ok or hidden == true\n", "    return false\n")],
    # Item 2: crew data leaving the book.
    "a name stored under ns": [
        (
            "            entry.name = ns.String(name)\n",
            "            entry.name = name\n            ns.lastName = name\n",
        )
    ],
    "a name stored in WowLabCharDB": [
        (
            "            entry.name = ns.String(name)\n",
            "            entry.name = name\n            WowLabCharDB.met = name\n",
        )
    ],
    "a key stored in WowLabDB": [
        (
            "            entry.name = ns.String(name)\n",
            "            entry.name = name\n            WowLabDB.seen = key\n",
        )
    ],
    "the book handed to ns": [(_CREW_TAIL, _CREW_TAIL + "ns.Book = WowLabCrewDB\n")],
    "a name call exported": [
        (
            _CREW_TAIL,
            _CREW_TAIL + "function ns.CrewName()\n    if not crewOn() then\n        return\n"
            "    end\n    if crewBlocked() then\n        return\n    end\n"
            '    local u = "target"\n    local n = UnitName(u)\n    return n\nend\n',
        )
    ],
    "the export returns the book's data": [
        ('    return "constructed"\nend\n', "    return WowLabCrewDB.players\nend\n")
    ],
    "a global written": [(_CREW_TAIL, _CREW_TAIL + "CrewLastName = 1\n")],
    "a global function defined": [(_CREW_TAIL, _CREW_TAIL + "function CrewPeek()\nend\n")],
    "ns.Section reached": [(_CREW_TAIL, _CREW_TAIL + "local s = ns.Section\n")],
    "ns.state reached": [(_CREW_TAIL, _CREW_TAIL + "local st = ns.state\n")],
    # Item 3: units outside the party and the raid.
    "a unit built from mouse and over": [
        (_CREW_MEMBER, '        local unit = "mouse" .. "over"\n')
    ],
    "a nameplate unit": [(_CREW_MEMBER, '        local unit = "name" .. "plate" .. i\n')],
    "a unit numbered by a parameter": [(_CREW_MEMBER, '        local unit = "party" .. count\n')],
    "a unit assigned again": [(_CREW_MEMBER, _CREW_MEMBER + '        unit = "focus"\n')],
    "the loop variable assigned": [(_CREW_MEMBER, "        i = 2\n" + _CREW_MEMBER)],
    # Fix round 2, item 6: a unit helper is held to what it is handed.
    "a unit helper handed another unit": [
        (_CREW_TAIL, _CREW_TAIL + 'local other = "mouse" .. "over"\nlevelOf(other)\n')
    ],
    "a unit helper handed the typed text": [
        (
            "        WowLabCrewDB.on = true\n",
            "        WowLabCrewDB.on = true\n        levelOf(word)\n",
        )
    ],
    "a unit helper handed a focus literal": [(_CREW_TAIL, _CREW_TAIL + 'levelOf("focus")\n')],
    "a unit helper using its parameter otherwise": [
        (
            "    local level = UnitLevel(member)\n",
            "    local level = UnitLevel(member)\n    print(member)\n",
        )
    ],
    "a unit helper with two parameters": [
        ("local function levelOf(member)\n", "local function levelOf(member, spare)\n")
    ],
    "a unit helper aliased": [(_CREW_TAIL, _CREW_TAIL + "local alias = levelOf\n")],
    "a unit in a function literal's parameter": [
        (
            _CREW_TAIL,
            _CREW_TAIL + "local peek = function(member)\n    if not crewOn() then\n        return\n"
            "    end\n    if crewBlocked() then\n        return\n    end\n"
            "    local n = UnitLevel(member)\n    if not secret(n) then\n"
            "        WowLabCrewDB.peek = n\n    end\nend\n",
        )
    ],
    # Fix round 2, item 1: the typed text and other parameters carry nothing.
    "the slash handler's string test removed": [
        (
            '    if type(word) ~= "string" then\n        return\n    end\n    if string.match',
            "    if string.match",
        )
    ],
    "a parameter used as a box": [
        (
            "local function countBook()\n",
            "local function leak(box)\n    box.n = WowLabCrewDB.players\nend\n\n"
            "local function countBook()\n",
        )
    ],
    "a parameter called": [
        (
            "local function countBook()\n",
            "local function leak(cb)\n    cb(WowLabCrewDB.players)\nend\n\nlocal function countBook()\n",
        )
    ],
    "a parameter handed to ns.Call": [
        (
            "local function countBook()\n",
            "local function leak(cb)\n    ns.Call(cb, WowLabCrewDB.players)\nend\n\n"
            "local function countBook()\n",
        )
    ],
    "a parameter handed to pcall": [
        (
            "local function countBook()\n",
            "local function leak(cb)\n    pcall(cb, WowLabCrewDB.players)\nend\n\n"
            "local function countBook()\n",
        )
    ],
    "a parameter kept in a local": [
        (
            "local function countBook()\n",
            "local function leak(box)\n    local kept = box\n    return kept\nend\n\n"
            "local function countBook()\n",
        )
    ],
    "a parameter kept in a table": [
        (
            "local function countBook()\n",
            "local function leak(box)\n    local kept = { box }\n    return #kept\nend\n\n"
            "local function countBook()\n",
        )
    ],
    "a parameter through and/or": [
        (
            "local function countBook()\n",
            "local function leak(box)\n    local kept = box and box or nil\n    return 1\nend\n\n"
            "local function countBook()\n",
        )
    ],
    "a parameter iterated": [
        (
            "local function countBook()\n",
            "local function leak(walk)\n    for v in walk do\n        print(v)\n    end\nend\n\n"
            "local function countBook()\n",
        )
    ],
    "a parameter assigned": [
        (
            "local function countBook()\n",
            "local function leak(box)\n    box = WowLabCrewDB.players\n    return 1\nend\n\n"
            "local function countBook()\n",
        )
    ],
    "a function taking ...": [
        (
            "local function countBook()\n",
            "local function leak(...)\n    print(...)\nend\n\nlocal function countBook()\n",
        )
    ],
    # Fix round 4 (security re-check): event payloads and book keys.
    "a payload stored from INSPECT_READY": [
        (
            "local function onRoster()\n",
            "local function onPayload(event, p)\n"
            + _CREW_GATE_BODY
            + "    WowLabCrewDB.seen = ns.String(p)\nend\n\n"
            "local function arm()\n"
            + _CREW_GATE_BODY
            + '    ns.On("INSPECT_READY", onPayload)\nend\n\nlocal function onRoster()\n',
        )
    ],
    "an inviter's name stored as a key": [
        (
            "local function onRoster()\n",
            "local function onInvite(event, p)\n"
            + _CREW_GATE_BODY
            + "    WowLabCrewDB.players[ns.String(p)] = { met = 0 }\nend\n\n"
            "local function arm()\n"
            + _CREW_GATE_BODY
            + '    ns.On("PARTY_INVITE_REQUEST", onInvite)\nend\n\nlocal function onRoster()\n',
        )
    ],
    "a roster handler reading its payload": [
        ("local function onRoster()\n", "local function onRoster(event, unit)\n"),
        ("    readGroup(4)\n", "    readGroup(4)\n    WowLabCrewDB.lastUnit = unit\n"),
    ],
    "a roster handler comparing the event": [
        ("local function onRoster()\n", "local function onRoster(event)\n"),
        (
            "    readGroup(4)\n",
            '    readGroup(4)\n    WowLabCrewDB.flag = event == "UNIT_NAME_UPDATE"\n',
        ),
    ],
    "a roster handler as a function literal": [
        (
            '    ns.On("GROUP_ROSTER_UPDATE", onRoster)\nend\n',
            '    ns.On("GROUP_ROSTER_UPDATE", function(...)\n        onRoster()\n    end)\nend\n',
        )
    ],
    "an event §20.1 does not name": [
        (
            '    ns.On("GROUP_ROSTER_UPDATE", onRoster)\nend\n',
            '    ns.On("PLAYER_TARGET_CHANGED", onRoster)\nend\n',
        )
    ],
    "an event that is not a literal": [
        (
            '    ns.On("GROUP_ROSTER_UPDATE", onRoster)\nend\n',
            '    local e = "GROUP_ROSTER_UPDATE"\n    ns.On(e, onRoster)\nend\n',
        )
    ],
    "the probe handler registered inside a gated function": [
        (
            '    ns.On("GROUP_ROSTER_UPDATE", onRoster)\nend\n',
            '    ns.On("GROUP_ROSTER_UPDATE", crewLoaded)\nend\n',
        )
    ],
    "a parameter handed to ns.Number": [
        (
            "local function countBook()\n",
            "local function leak(box)\n    local s = ns.Number(box)\n    return 1\nend\n\n"
            "local function countBook()\n",
        )
    ],
    "a constructor with a bracket key": [
        (
            "            WowLabCrewDB.current[key] = true\n",
            "            WowLabCrewDB.extra = { [key] = true }\n",
        )
    ],
    "the players table rebuilt": [
        (
            "        WowLabCrewDB.on = false\n",
            "        WowLabCrewDB.on = false\n        WowLabCrewDB.players = { met = 1 }\n",
        )
    ],
    "the players table created full": [("players = {}, current", "players = { 1 }, current")],
    "the book read through brackets": [
        ("    local book = WowLabCrewDB.players\n", '    local book = WowLabCrewDB["players"]\n')
    ],
    # Fix round 5, item 1.
    "the players table replaced through brackets": [
        (_CREW_READ, _CREW_READ + '    local fake = {}\n    WowLabCrewDB["players"] = fake\n')
    ],
    "the current table replaced through brackets": [
        (_CREW_READ, _CREW_READ + '    WowLabCrewDB["current"] = {}\n')
    ],
    # Fix round 5, item 4: the running "oldest" takes walk keys only.
    # Fix round 3, item 3: the ungated probe handler, pinned.
    "the probe handler creating the book": [
        (
            '    if name ~= "WowLab" or type(WowLabCrewDB) ~= "table" then\n        return\n    end\n',
            '    if name ~= "WowLab" then\n        return\n    end\n'
            '    if type(WowLabCrewDB) ~= "table" then\n        WowLabCrewDB = {}\n    end\n',
        )
    ],
    "the probe handler counting other addons": [('    if name ~= "WowLab" or type', "    if type")],
    "the probe handler turning the book on": [
        (
            "        prior.lost = nil\n",
            "        prior.lost = nil\n        WowLabCrewDB.on = true\n",
        )
    ],
    "the probe handler for another event": [
        ('ns.On("ADDON_LOADED", crewLoaded)\n', 'ns.On("PLAYER_LOGOUT", crewLoaded)\n')
    ],
    "a second ADDON_LOADED registration": [
        (
            'ns.On("ADDON_LOADED", crewLoaded)\n',
            'ns.On("ADDON_LOADED", crewLoaded)\nns.On("ADDON_LOADED", onWorld)\n',
        )
    ],
    "the probe handler's local assigned again": [
        (
            'ns.On("ADDON_LOADED", crewLoaded)\n',
            'crewLoaded = onWorld\nns.On("ADDON_LOADED", crewLoaded)\n',
        )
    ],
    "an ungated handler that is not the probe": [
        (
            'ns.On("ADDON_LOADED", crewLoaded)\n',
            "local function counter()\n    WowLabCrewDB.probe = 1\nend\n\n"
            'ns.On("ADDON_LOADED", counter)\n',
        )
    ],
    # Item 4: where Crew.lua may register an event.
    "a roster event registered at load": [
        ('ns.On("PLAYER_ENTERING_WORLD", onWorld)\n', 'ns.On("GROUP_ROSTER_UPDATE", onRoster)\n')
    ],
    # Fix round 3: one file-level registration per event, not one in all.
    "a second file-level registration of one event": [
        (
            'ns.On("PLAYER_ENTERING_WORLD", onWorld)\n',
            'ns.On("PLAYER_ENTERING_WORLD", onWorld)\nns.On("PLAYER_ENTERING_WORLD", onRoster)\n',
        )
    ],
    "a file-level handler without the gate": [
        (
            'ns.On("PLAYER_ENTERING_WORLD", onWorld)\n',
            'ns.On("PLAYER_ENTERING_WORLD", nameAbsentLater)\n',
        ),
        (
            "local function onWorld()\n",
            "local function nameAbsentLater()\n    print(1)\nend\n\nlocal function onWorld()\n",
        ),
    ],
    "a file-level handler as a literal": [
        (
            'ns.On("PLAYER_ENTERING_WORLD", onWorld)\n',
            'ns.On("PLAYER_ENTERING_WORLD", function()\n    onWorld()\nend)\n',
        )
    ],
    "a registration in an ungated function": [
        (
            "local function onWorld()\n",
            'local function early()\n    ns.On("GROUP_ROSTER_UPDATE", onRoster)\nend\n\nlocal function onWorld()\n',
        )
    ],
    # Item 4: only the slash handler turns the book on or creates it.
    "the book turned on at load": [(_CREW_TAIL, _CREW_TAIL + "WowLabCrewDB.on = true\n")],
    "the book created at load": [
        (_CREW_TAIL, _CREW_TAIL + "WowLabCrewDB = { schema = 1, on = true }\n")
    ],
    "the book turned on in a gated function": [
        (_CREW_READ, _CREW_READ + "    WowLabCrewDB.on = true\n")
    ],
    "the book turned on through a bracket": [
        (_CREW_TAIL, _CREW_TAIL + 'WowLabCrewDB["on"] = true\n')
    ],
    "the book stored under a key not written out": [
        (_CREW_TAIL, _CREW_TAIL + 'local k = "on"\nWowLabCrewDB[k] = true\n')
    ],
    "the book aliased": [(_CREW_READ, _CREW_READ + "    local db = WowLabCrewDB\n")],
    # Decision c: the secret-value test before every use.
    "a realm stored before the test": [
        (
            "        local name, realm = UnitFullName(unit)\n",
            "        local name, realm = UnitFullName(unit)\n        WowLabCrewDB.lastRealm = realm\n",
        )
    ],
    "a name compared before the test": [
        (
            "        if not secret(me) and not me",
            "        if name ~= nil and not secret(me) and not me",
        )
    ],
    "an or in the test": [("not secret(realm) then\n", 'not secret(realm) or realm == "" then\n')],
    "a call result not bound to a local": [
        (
            "            local _, classFile = UnitClass(unit)\n",
            "            local classFile = select(2, UnitClass(unit))\n",
        )
    ],
    "a value used in the else branch": [
        (
            "                entry.alias = ns.String(other)\n            end\n",
            "                entry.alias = ns.String(other)\n            else\n                entry.raw = other\n"
            "            end\n",
        )
    ],
    "the test's answer ignored": [
        (
            "            if not secret(classFile) then\n                entry.class = ns.String(classFile)\n"
            "            end\n",
            "            local hidden = secret(classFile)\n            entry.class = classFile\n",
        )
    ],
    "a value assigned again": [
        (
            "        local name, realm = UnitFullName(unit)\n",
            "        local name, realm = UnitFullName(unit)\n        realm = home\n",
        )
    ],
    "a call tested directly": [
        (
            '        local me = UnitIsUnit(unit, "player")\n',
            '        local me = false\n        if UnitIsUnit(unit, "player") then\n'
            "            me = true\n        end\n",
        )
    ],
}

# M19-01's cases in their schema 1 form, for the tags whose text the schema 2
# source does not hold. With _CREW_GATE_CASES they grade _CREW_SOURCE_1, so
# the schema 1 rules (CREW_PINS_1) are shown unchanged until M20-05.
_CREW_GATE_CASES_1: dict[str, list[tuple[str, str]]] = {
    "the schema constant reassigned": [
        ("local CREW_SCHEMA = 1\n", "local CREW_SCHEMA = 1\nCREW_SCHEMA = 2\n")
    ],
    "the schema constant not the file's": [("local CREW_SCHEMA = 1\n", "local CREW_SCHEMA = 3\n")],
    "the probe handler without the schema test": [
        (" or WowLabCrewDB.schema ~= CREW_SCHEMA then\n", " then\n")
    ],
    "the slash handler's string test removed": [
        (
            '    if type(word) ~= "string" then\n        return\n    end\n    if type(WowLabCrewDB)',
            "    if type(WowLabCrewDB)",
        )
    ],
    "the probe handler creating the book": [
        (
            '    if name ~= "WowLab" or type(WowLabCrewDB) ~= "table" or WowLabCrewDB.schema ~= CREW_SCHEMA then\n'
            "        return\n    end\n",
            '    if name ~= "WowLab" then\n        return\n    end\n'
            '    if type(WowLabCrewDB) ~= "table" then\n        WowLabCrewDB = {}\n    end\n',
        )
    ],
    "the probe handler turning the book on": [
        (
            "        probe.lost = true\n",
            "        probe.lost = true\n        WowLabCrewDB.on = true\n",
        )
    ],
}

# Constructed edits of _CREW_SOURCE (M20-04): each breaks one schema 2 rule
# and is refused for that rule's own reason, the second item: a part of a
# problem the grader names (`_crew_problems` with CREW_PINS_2).
_PVP_PASS_HEAD = "local function pvpPass()\n" + _CREW_GATE_BODY
_NEAR_FULL_STORE = "        WowLabCrewDB.warned = now\n"
_NEAR_FULL_PRINT = (
    '        print("constructed: the crew book is nearly full; open wowlab so nothing is lost.")\n'
)
_SNAPSHOT_NAME = "            name = row.name\n"
_NEW_BOOK = (
    "            WowLabCrewDB = { schema = CREW_SCHEMA, players = {}, current = {}, matches = {}, "
    "started = ns.Number(time()) }\n"
)
_ENTRY_READ = "            entry.read = ns.Bool(done)\n"
_KEPT = "    local kept = #WowLabCrewDB.matches\n"
_WINNER_STORE = "        WowLabCrewDB.probe.pvp[2] = pvpSnapshot()\n"
_MIGRATE_TAIL = "    WowLabCrewDB.matches = {}\nend\n"
_SCHEMA_BRANCH = (
    '        print("constructed: this book is from another version")\n        return\n    end\n'
)
_CREW_SCHEMA_2_CASES: dict[str, tuple[list[tuple[str, str]], str]] = {
    # The schema constant.
    "the schema constant left at 1": (
        [("local CREW_SCHEMA = 2\n", "local CREW_SCHEMA = 1\n")],
        "is not one file-level `local CREW_SCHEMA = 2`",
    ),
    # The migration.
    "the migration adding a field": (
        [(_MIGRATE_TAIL, "    WowLabCrewDB.matches = {}\n    WowLabCrewDB.warned = 0\nend\n")],
        "crewMigrate is not the pinned one",
    ),
    "the migration for a book of any schema": (
        [(" or WowLabCrewDB.schema ~= 1 then\n", " then\n")],
        "crewMigrate is not the pinned one",
    ),
    "the migration giving matches an entry": (
        [(_MIGRATE_TAIL, "    WowLabCrewDB.matches = { 1 }\nend\n")],
        "crewMigrate is not the pinned one",
    ),
    "the migration at load for a book that is off": (
        [
            (
                "    if WowLabCrewDB.on == true then\n        crewMigrate()\n    end\n",
                "    crewMigrate()\n",
            )
        ],
        "ns.On called where Crew.lua may not register",
    ),
    "the migration called from another function": (
        [("    nearFull()\n    pvpPass()\n", "    crewMigrate()\n    nearFull()\n    pvpPass()\n")],
        "crewMigrate used outside its two pinned calls",
    ),
    "the migration called at file level": (
        [(_CREW_TAIL, _CREW_TAIL + "crewMigrate()\n")],
        "crewMigrate used outside its two pinned calls",
    ),
    "the migration aliased": (
        [(_CREW_TAIL, _CREW_TAIL + "local again = crewMigrate\n")],
        "crewMigrate is bound or used other than called",
    ),
    "the slash handler migrating for any word": (
        [('"^%s*%S+%s+on%s*$"', '"^%s*%S+"')],
        "does not start with the pinned string and schema tests",
    ),
    "the slash handler migrating after the schema refusal": (
        [(_CREW_HEAD_MIGRATE, ""), (_SCHEMA_BRANCH, _SCHEMA_BRANCH + _CREW_HEAD_MIGRATE)],
        "does not start with the pinned string and schema tests",
    ),
    "the slash handler migrating without the word test": (
        [(_CREW_HEAD_MIGRATE, "    crewMigrate()\n")],
        "does not start with the pinned string and schema tests",
    ),
    # The limits.
    "the player limit not 20,000": (
        [("local MAX_PLAYERS = 20000\n", "local MAX_PLAYERS = 5000\n")],
        "MAX_PLAYERS is not one file-level",
    ),
    "the match limit not 500": (
        [("local MAX_MATCHES = 500\n", "local MAX_MATCHES = 600\n")],
        "MAX_MATCHES is not one file-level",
    ),
    "the player limit as an expression": (
        [("local MAX_PLAYERS = 20000\n", "local MAX_PLAYERS = 2 * 10000\n")],
        "MAX_PLAYERS is not one file-level",
    ),
    "the player limit assigned again": (
        [("    readGroup(4)\n", "    readGroup(4)\n    MAX_PLAYERS = 1\n")],
        "MAX_PLAYERS is assigned again",
    ),
    "the match limit shadowed": (
        [(_KEPT, "    local MAX_MATCHES = 1\n" + _KEPT)],
        "MAX_MATCHES is declared more than once",
    ),
    "the seven days not a literal": (
        [("local NEAR_FULL_SECONDS = 604800\n", "local NEAR_FULL_SECONDS = 7 * 86400\n")],
        "NEAR_FULL_SECONDS is not one file-level",
    ),
    "the share not 90": (
        [("local NEAR_FULL_PERCENT = 90\n", "local NEAR_FULL_PERCENT = 80\n")],
        "NEAR_FULL_PERCENT is not one file-level",
    ),
    # The near-full line and `warned`.
    "warned stored twice": (
        [("    local held = 0\n", "    WowLabCrewDB.warned = now\n    local held = 0\n")],
        "warned is stored 2 times",
    ),
    "warned stored after the line": (
        [(_NEAR_FULL_STORE + _NEAR_FULL_PRINT, _NEAR_FULL_PRINT + _NEAR_FULL_STORE)],
        "is not stored as",
    ),
    "warned stored without the line": ([(_NEAR_FULL_PRINT, "")], "is not stored as"),
    "warned stored outside the gate": (
        [
            (_NEAR_FULL_STORE + _NEAR_FULL_PRINT, ""),
            (
                _CREW_TAIL,
                _CREW_TAIL
                + "local function stamp()\n    local now = 1\n"
                + _NEAR_FULL_STORE
                + _NEAR_FULL_PRINT
                + "end\n",
            ),
        ],
        "is not stored as",
    ),
    "warned set in a constructor": (
        [("matches = {}, started", "matches = {}, warned = 0, started")],
        "warned set in a constructor",
    ),
    "the near-full line without the seven days": (
        [("        and now - last >= NEAR_FULL_SECONDS\n", "        and now - last >= 0\n")],
        "is not under a test of the seven days",
    ),
    "the near-full line at another share": (
        [("held * 100 >= MAX_PLAYERS * NEAR_FULL_PERCENT", "held * 100 >= MAX_PLAYERS * 80")],
        "is not under a test of the seven days",
    ),
    "the near-full line for the players only": (
        [(" or kept * 100 >= MAX_MATCHES * NEAR_FULL_PERCENT", "")],
        "is not under a test of the seven days",
    ),
    # The probe.
    "the probe in combat": (
        [
            (
                _PVP_PASS_HEAD,
                "local function pvpPass()\n    if not crewOn() then\n        return\n    end\n",
            )
        ],
        "written outside the gate and the slash handler",
    ),
    "a scoreboard call not through pcall": (
        [(_PVP_PASS_HEAD, _PVP_PASS_HEAD + "    local n = GetNumBattlefieldScores()\n")],
        "GetNumBattlefieldScores called other than through pcall",
    ),
    "a scoreboard call named in a string": (
        [(_PVP_PASS_HEAD, _PVP_PASS_HEAD + '    local f = ns.Fn(ns, "GetBattlefieldScore")\n')],
        "a scoreboard call named in a string",
    ),
    "a scoreboard call shadowed": (
        [(_PVP_PASS_HEAD, _PVP_PASS_HEAD + "    local GetBattlefieldWinner = 1\n")],
        "GetBattlefieldWinner declared as a local",
    ),
    "a probe event §21.2 does not name": (
        [
            (
                '    ns.On("PVP_MATCH_COMPLETE", onComplete)\n',
                '    ns.On("PVP_RATED_STATS_UPDATE", onComplete)\n',
            )
        ],
        "ns.On called where Crew.lua may not register",
    ),
    # Fix round 1 (security review): every GetBattlefield... global and any
    # C_PvP member, not only the seven candidates.
    "a scoreboard value stored before its secret test": (
        [
            (
                _PVP_PASS_HEAD,
                _PVP_PASS_HEAD + "    local okStat, stat = pcall(GetBattlefieldStatData, 1, 1)\n"
                '    if okStat and type(stat) == "number" then\n'
                "        WowLabCrewDB.stat = ns.Number(stat)\n    end\n",
            )
        ],
        "stat (from the scoreboard) used before the secret test",
    ),
    "a battlefield call named in a string": (
        [(_PVP_PASS_HEAD, _PVP_PASS_HEAD + '    local f = ns.Fn(ns, "GetBattlefieldStatus")\n')],
        "a scoreboard call named in a string",
    ),
    # Fix round 1 (code review): `probe.pvp` and the later fields through a
    # table constructor.
    # Fix round 2 (code review): the book assigned whole only from a
    # constructor written in place.
    "a new book from a local holding a match": (
        [
            (
                _NEW_BOOK,
                "            local fresh = { schema = CREW_SCHEMA, players = {}, current = {}, "
                "matches = {} }\n"
                "            fresh.matches[1] = { won = true }\n"
                "            WowLabCrewDB = fresh\n",
            )
        ],
        "WowLabCrewDB assigned other than from a constructor",
    ),
    "a new book from a call": (
        [
            (
                _NEW_BOOK,
                "            WowLabCrewDB = nameAbsent()\n",
            )
        ],
        "WowLabCrewDB assigned other than from a constructor",
    ),
    "a new book from an expression": (
        [
            (
                "matches = {}, started = ns.Number(time()) }\n",
                "matches = {}, started = ns.Number(time()) } or nil\n",
            )
        ],
        "WowLabCrewDB assigned other than from a constructor",
    ),
    "a probe event registered at load": (
        [(_CREW_TAIL, _CREW_TAIL + 'ns.On("PVP_MATCH_COMPLETE", onComplete)\n')],
        "ns.On called where Crew.lua may not register",
    ),
    # The fields of M20-07 and M20-08; `matches`; `schema`.
    "a counter raised where no marker is written": (
        [(_ENTRY_READ, _ENTRY_READ + "            entry.allied = 1\n")],
        "a counter is raised without `matched` read",
    ),
    "a later field as a bracket key": (
        [(_PVP_PASS_HEAD, _PVP_PASS_HEAD + '    local e = {}\n    e["faced_won"] = 1\n')],
        "'faced_won' as a bracket key",
    ),
    "a match entry written": (
        [(_KEPT, _KEPT + "    WowLabCrewDB.matches[1] = { won = true }\n")],
        "a store into WowLabCrewDB.matches not through a list",
    ),
    "a new book created with a match": (
        [("matches = {}, started", "matches = { 1 }, started")],
        "matches created other than empty",
    ),
    # M20-11 (§21.2, as built): what stays about the client and the machine.
    "the marker written after the counters": (
        [(_MATCH_MARK, ""), (_MATCH_DROP, _MATCH_DROP + _MATCH_MARK)],
        "a counter is raised without `matched` read",
    ),
    "the marker written only sometimes": (
        [(_MATCH_MARK, "    if now ~= nil then\n    " + _MATCH_MARK + "    end\n")],
        "a counter is raised without `matched` read",
    ),
    "the marker never read": (
        [(_MATCH_READ, "")],
        "a counter is raised without `matched` read",
    ),
    "a match added without the oldest dropped": (
        [(_MATCH_DROP, "")],
        "a match stored without the oldest dropped first",
    ),
    "a row value stored without its secret test": (
        [(_MATCH_COPY, "                copy[field] = ns.Number(value)\n")],
        "(from the scoreboard) used before the secret test",
    ),
    "a scoreboard row read outside the gate": (
        [
            (
                "local function recordMatch()\n" + _CREW_GATE_BODY,
                "local function recordMatch()\n    if not crewOn() then\n        return\n    end\n",
            )
        ],
        "used outside the gate",
    ),
    "a scoreboard row read without pcall": (
        [
            (
                "local okRow, row = pcall(C_PvP.GetScoreInfo, i)",
                "local okRow, row = true, C_PvP.GetScoreInfo(i)",
            )
        ],
        "called other than through pcall",
    ),
    # Fix round 1 (code review, #664): the read decides; the 20,000 limit.
    "the marker read but deciding nothing": (
        [(_MATCH_READ, "    local seen = WowLabCrewDB.matched\n")],
        "a counter is raised without `matched` read",
    ),
    "a new player added without makeRoom": (
        [("if book[key] == nil and makeRoom() then", "if book[key] == nil then")],
        "a new player added without makeRoom()",
    ),
    "a drop of a key that is not an entry": (
        [
            (
                "        if oldest == nil or entry.last < players[oldest].last then\n"
                "            oldest = key\n",
                "        if oldest == nil or entry.last < players[oldest].last then\n"
                '            oldest = "x"\n',
            )
        ],
        "a player dropped who is not an entry of the book",
    ),
    "the schema stored outside the migration": (
        [(_PVP_PASS_HEAD, _PVP_PASS_HEAD + "    WowLabCrewDB.schema = CREW_SCHEMA\n")],
        "schema stored outside the migration",
    ),
    "a new book created at another schema": (
        [("WowLabCrewDB = { schema = CREW_SCHEMA,", "WowLabCrewDB = { schema = 1,")],
        "schema set other than to CREW_SCHEMA",
    ),
}

# Controls (M20-04): each edit keeps every schema 2 rule.
_CREW_SCHEMA_2_CONTROLS: dict[str, list[tuple[str, str]]] = {
    "the match list type()-tested": [
        (
            _KEPT,
            '    if type(WowLabCrewDB.matches) ~= "table" then\n        return\n    end\n' + _KEPT,
        )
    ],
    "a later field read, not written": [
        (
            _ENTRY_READ,
            _ENTRY_READ + "            if entry.allied ~= nil then\n"
            "                entry.read = true\n            end\n",
        )
    ],
    # Fix round 2: the new book as the real file makes it (§20.1), at schema 2.
    "a new book written in place over several lines": [
        (
            _NEW_BOOK,
            "            local startedAt = ns.Number(time())\n"
            "            WowLabCrewDB = {\n                schema = CREW_SCHEMA,\n"
            "                on = true,\n                started = startedAt,\n"
            "                probe = { loads = 1 },\n                skipped = 0,\n"
            "                players = {},\n                current = {},\n"
            "                matches = {},\n            }\n",
        )
    ],
    "a new book with its probe count": [
        ("matches = {}, started", "matches = {}, probe = { loads = 1 }, started")
    ],
    "a battlefield call presence-tested": [
        (
            _KEPT,
            '    if type(GetBattlefieldStatus) ~= "function" then\n        return\n    end\n'
            + _KEPT,
        )
    ],
    "the probe block type()-tested": [
        (
            "    if not pvpWinnerRead and pvpWon() then\n",
            '    if type(WowLabCrewDB.probe) ~= "table" then\n        return\n    end\n'
            "    if not pvpWinnerRead and pvpWon() then\n",
        )
    ],
    "the winner store inside pcall": [
        (
            _WINNER_STORE,
            "        pcall(function()\n            WowLabCrewDB.probe.pvp[2] = pvpSnapshot()\n"
            "        end)\n",
        )
    ],
}
