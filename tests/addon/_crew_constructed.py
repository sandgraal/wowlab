"""Constructed crew-book sources for the lab-addon grader (M19-01).

CONSTRUCTED INPUT, labelled as such (L8, ADR-0036 term 3): nothing here is
addon code, nothing is installed or run, and no line holds a player's name,
realm or key. tests/addon/test_lab_addon.py loads this module by path and
grades these sources with the rules it builds from `BOOK`; it does not name
the book itself. tests/repo/test_crew_book_out_of_repo.py lists this file
among its constructed test files and grades every line of it.
"""

from __future__ import annotations

# The crew book's saved variable (docs/LAB_PLAN.md §20.1), written whole.
BOOK = "WowLabCrewDB"

# Pinned helper texts that name the book (rendered tokens).
CREW_ON = (
    "local function crewOn ( ) "
    'return type ( WowLabCrewDB ) == "table" and WowLabCrewDB . on == true end'
)
CREW_PROBE = (
    "local function crewLoaded ( _ , name ) "
    'if name ~= "WowLab" or type ( WowLabCrewDB ) ~= "table" then return end '
    # Fix round 5: the shape of Core.lua's loadProbe (§13.1), so the
    # unrelaxed M11-13 stored-value check accepts it.
    "local probe = { loads = 1 } "
    "local prior = WowLabCrewDB . probe "
    'if type ( prior ) == "table" and type ( prior . loads ) == "number" then '
    "probe . loads = ns . Number ( prior . loads + 1 ) "
    "else probe . lost = true end "
    "WowLabCrewDB . probe = probe end"
)


# Constructed (M19-01): a Crew.lua that uses every relaxation and keeps every
# rule. Not addon code; never installed or run. It holds no player's name.
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
_CREW_SOURCE = (
    """local _, ns = ...

local function crewOn()
    return type(WowLabCrewDB) == "table" and WowLabCrewDB.on == true
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
            if book[key] == nil then
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
    if name ~= "WowLab" or type(WowLabCrewDB) ~= "table" then
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
    if type(UnitFullName) ~= "function" then
        print("the client has no UnitFullName function")
    end
    if word == "on" then
        if type(WowLabCrewDB) ~= "table" then
            WowLabCrewDB = { schema = 1, players = {}, current = {}, started = ns.Number(time()) }
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
            '    return type(WowLabCrewDB) == "table" and WowLabCrewDB.on == true\n',
            "    return true\n",
        )
    ],
    "the on test without the value": [
        (" and WowLabCrewDB.on == true", " and WowLabCrewDB.on ~= nil")
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
            '    if type(word) ~= "string" then\n        return\n    end\n    if type(UnitFullName)',
            "    if type(UnitFullName)",
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
    "a key from a parameter": [
        (
            "local function countBook()\n",
            "local function keep(k)\n"
            + _CREW_GATE_BODY
            + "    WowLabCrewDB.players[k] = { met = 0 }\nend\n\nlocal function countBook()\n",
        )
    ],
    "a key from another client call": [
        (
            '            book[name .. "-" .. realm].seen = true\n',
            "            local zone = GetInstanceInfo()\n            book[zone] = entry\n",
        )
    ],
    "a key with a piece not from a name call": [
        (
            '            book[name .. "-" .. realm].seen = true\n',
            '            local zone = GetInstanceInfo()\n            book[zone .. "-" .. realm] = entry\n',
        )
    ],
    "a key from a walk over another table": [
        (
            '            book[name .. "-" .. realm].seen = true\n',
            "            for slot in pairs(entry) do\n                book[slot] = entry\n            end\n",
        )
    ],
    "a key stored in a multiple assignment": [
        (
            '            book[name .. "-" .. realm].seen = true\n',
            "            local zone = GetInstanceInfo()\n            book[zone], entry.x = entry, 1\n",
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
    "a book table handed on": [
        ("    readGroup(4)\n", "    readGroup(4)\n    print(WowLabCrewDB.players)\n")
    ],
    "a book alias assigned again": [
        (
            "    local book = WowLabCrewDB.players\n",
            "    local book = WowLabCrewDB.players\n    book = {}\n",
        )
    ],
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
    "a guild name as a key part": [
        (
            '            book[name .. "-" .. realm].seen = true\n',
            "            local g = GetGuildInfo(unit)\n            if not secret(g) then\n"
            '                book[g .. "-" .. realm].seen = true\n            end\n',
        )
    ],
    # Fix round 5, item 4: the running "oldest" takes walk keys only.
    "an oldest taken from elsewhere": [
        ("            oldest = key\n", '            oldest = "x"\n')
    ],
    "an oldest declared with a value": [
        ("    local oldest\n", "    local oldest = WowLabCrewDB.home\n")
    ],
    "an oldest set in a multiple assignment": [
        ("            oldest = key\n", "            oldest, kept = key, 1\n")
    ],
    "an oldest taken from an entry's field": [
        ("            oldest = key\n", "            oldest = entry.last\n")
    ],
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
            "        probe.lost = true\n",
            "        probe.lost = true\n        WowLabCrewDB.on = true\n",
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
