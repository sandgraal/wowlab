-- WowLab: the crew book (docs/LAB_PLAN.md §20.1, ADR-0036, ADR-0026).
--
-- Runs in the game client only; the Lab never loads or runs this file (L3).
-- It remembers the players the owner groups with, in the account-wide saved
-- variable WowLabCrewDB (its own schema, 1; the per-character schema does not
-- change), and says so in the owner's own chat frame when a rated or noted
-- player joins the group again. Nothing is sent anywhere: no chat message,
-- whisper or addon message (tests/addon/test_lab_addon.py refuses the calls).
--
-- Off until turned on (ADR-0036 term 2). While `on` is absent or false no
-- roster event is registered, no unit is read and nothing is created; a book
-- that loaded as nil stays nil. Only `/wowlab crew on` creates the book or
-- sets `on`. Every function that reads the client starts with the on test
-- and then the combat test (crewOn, crewBlocked): nothing is read while
-- InCombatLockdown() is true or cannot be asked, and the pass runs again at
-- PLAYER_REGEN_ENABLED.
--
-- Not section code: `/wowlab skip` does not reach this file; its switch is
-- `on`. Like Core.lua's sections, the first pass after entering the world
-- waits FIRST_PASS_DELAY seconds, so `/wowlab crew off` can stop it first.
--
-- Every value read (name, realm, class, level, role, guild, the instance's
-- name, type and id) goes through `secret` before it is used: a secret name
-- or realm skips the member, any other secret value leaves its field as it
-- was, and each skip adds one to `skipped`. Each member is read inside its
-- own pcall, so one bad value cannot end the pass. A name that is nil or the
-- client's UNKNOWNOBJECT text is skipped without counting and tried again on
-- the next pass. No GUID is read or stored.
--
-- API status: every client API used is listed in lab/addon/README.md; all of
-- them are [verify] for this file until the owner's in-game check (M19-07).

local _, ns = ...

-- The book's own version number (§20.1).
local SCHEMA = 1
-- The drop order (§20.1 "Limits"): at most MAX_PLAYERS players. When the book
-- is full, the unrated, un-noted player with the oldest `last` is dropped
-- first (makeRoom); a rated or noted player is never dropped, and when every
-- kept player is rated or noted, new members and new ratings are refused and
-- the owner is told once per session.
local MAX_PLAYERS = 5000
-- The note rule (§20.1 "Limits"): control characters and | are removed byte
-- by byte (multi-byte text is left alone), spaces are trimmed, and what is
-- left may be at most NOTE_MAX_BYTES bytes. A longer note is refused, never
-- cut (cleanNote, noteFits).
local NOTE_MAX_BYTES = 200
-- The debounce the sections use, and the first pass's wait (Core.lua).
local DEBOUNCE = 2
local FIRST_PASS_DELAY = 15
-- The unit tokens walked: raid1 to raid40 in a raid, party1 to party4 in a party.
local MAX_RAID = 40
local MAX_PARTY = 4

-- The sentence of ADR-0036 term 2 (§20.5), printed when the book is turned on.
local TERMS = "The crew book keeps, for each player you group with, their name, realm, guild, class, level and role, "
    .. "when you first and last grouped, how many times and in which instance, and your own rating and note. "
    .. "It keeps them in WowLab.lua (and the game's backup, WowLab.lua.bak) in this game account's "
    .. "SavedVariables folder, and in wowlab's snapshots of it. Nothing is sent anywhere. "
    .. "The book knows names, not people: a player who renames or moves realm appears as a new entry, "
    .. "and someone else may later take a name you rated."
local KEPT = "Kept at the next /reload or logout; a crash loses it."

-- Session state, never saved.
local pending = false -- a debounced pass is waiting
local armed = false -- the roster events are registered
local firstScheduled = false -- the first pass after entering the world is on its timer
local firstDone = false -- ... and has run
local greeted = false -- the login line was printed
local fullSaid = false -- "the book is full" was printed
local rosterAbsent = nil -- why the roster event could not be registered
-- The instance of the current pass (readWhere): open world true or false,
-- or nil when it could not be read; the name and id when in an instance.
local passOpenWorld = nil
local passWhere = nil
local passWhereId = nil

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

local function say(text)
    print("|cff33ff99WowLab|r: " .. text)
end

-- A field as it is printed: line breaks removed and | doubled, at the moment
-- it is printed, whatever was done when it was stored (a record can also come
-- from a desk edit, a restored snapshot or a merge).
local function shown(text)
    if type(text) ~= "string" then
        return ""
    end
    local flat = string.gsub(text, "[\r\n]", "")
    local safe = string.gsub(flat, "|", "||")
    return safe
end

-- The note rule, as stored in game: control characters and | removed (byte
-- by byte: %c is the bytes 0 to 31 and 127, so multi-byte text is left
-- alone), then spaces trimmed.
local function cleanNote(text)
    if type(text) ~= "string" then
        return ""
    end
    local removed = string.gsub(text, "[%c|]", "")
    local trimmed = string.match(removed, "^%s*(.-)%s*$")
    return trimmed or ""
end

-- The note rule's limit, measured in bytes after cleanNote.
local function noteFits(note)
    return #note <= NOTE_MAX_BYTES
end

local function plural(count, one, many)
    if count == 1 then
        return "1 " .. one
    end
    return count .. " " .. many
end

-- A stamp `date` can print: a number of seconds after 1970 and before 2100.
local function printable(stamp)
    if type(stamp) ~= "number" then
        return false
    end
    if stamp > 0 and stamp < 4102444800 then
        return true
    end
    return false
end

-- How many players the book keeps.
local function countPlayers()
    local kept = 0
    for _ in pairs(WowLabCrewDB.players) do
        kept = kept + 1
    end
    return kept
end

-- Both tables can be walked (a desk edit could have left something else).
local function bookIntact()
    local kept = 0
    for _ in pairs(WowLabCrewDB.players) do
        kept = kept + 1
    end
    for _ in pairs(WowLabCrewDB.current) do
        kept = kept + 1
    end
    return kept >= 0
end

local function addSkipped()
    WowLabCrewDB.skipped = (ns.Number(WowLabCrewDB.skipped) or 0) + 1
end

-- Why the book cannot record at all, or nil.
local function featureMissing()
    if not crewOn() then
        return
    end
    if crewBlocked() then
        return
    end
    if rosterAbsent ~= nil then
        return rosterAbsent
    end
    if type(UnitName) ~= "function" then
        return "the client has no UnitName function, so no one is recorded"
    end
    if type(UnitIsUnit) ~= "function" then
        return "the client has no UnitIsUnit function, so no one is recorded"
    end
    if type(IsInGroup) ~= "function" or type(IsInRaid) ~= "function" then
        return "the client has no IsInGroup or IsInRaid function, so no one is recorded"
    end
    return nil
end

-- Why a field is never filled, or nil.
local function fieldsMissing()
    if not crewOn() then
        return
    end
    if crewBlocked() then
        return
    end
    local missing = ""
    if type(GetNormalizedRealmName) ~= "function" then
        missing = missing .. " Same-realm players are skipped: the client has no GetNormalizedRealmName function."
    end
    if type(UnitClass) ~= "function" then
        missing = missing .. " No class: the client has no UnitClass function."
    end
    if type(UnitLevel) ~= "function" then
        missing = missing .. " No level: the client has no UnitLevel function."
    end
    if type(UnitGroupRolesAssigned) ~= "function" then
        missing = missing .. " No role: the client has no UnitGroupRolesAssigned function."
    end
    if type(GetGuildInfo) ~= "function" then
        missing = missing .. " No guild: the client has no GetGuildInfo function."
    end
    if type(GetInstanceInfo) ~= "function" then
        missing = missing .. " No instance: the client has no GetInstanceInfo function."
    end
    if type(time) ~= "function" then
        missing = missing .. " No dates: the client has no time function."
    end
    if type(UnitIsPlayer) ~= "function" then
        missing = missing .. " Your target cannot be rated: the client has no UnitIsPlayer function."
    end
    if type(issecretvalue) ~= "function" then
        missing = missing .. " Values are not tested for secrets: the client has no issecretvalue function."
    end
    if missing == "" then
        return nil
    end
    return missing
end

-- The drop order (§20.1 "Limits"). Returns true when a new player may be
-- added: the book holds fewer than MAX_PLAYERS, or the unrated, un-noted
-- player with the oldest `last` was just dropped. A rated or noted player is
-- never dropped; when there is no one to drop it returns false and says so
-- once per session. Proven only by reading this code: no in-game check can
-- fill 5,000 entries.
local function makeRoom()
    if not crewOn() then
        return
    end
    if crewBlocked() then
        return
    end
    local kept = 0
    local oldest
    local oldestLast = 0
    for key, entry in pairs(WowLabCrewDB.players) do
        kept = kept + 1
        if type(entry) == "table" and entry.rating == nil and entry.note == nil then
            local last = ns.Number(entry.last) or 0
            if oldest == nil or last < oldestLast then
                oldest = key
                oldestLast = last
            end
        end
    end
    if kept < MAX_PLAYERS then
        return true
    end
    if oldest == nil then
        if not fullSaid then
            fullSaid = true
            say(
                "the crew book is full: all "
                    .. MAX_PLAYERS
                    .. " players in it are rated or noted, so no new player or rating is added."
            )
        end
        return false
    end
    local players = WowLabCrewDB.players
    local current = WowLabCrewDB.current
    players[oldest] = nil
    current[oldest] = nil
    return true
end

-- The warning line for one kept player, as the book holds them now.
local function lineFor(label)
    if not crewOn() then
        return
    end
    if crewBlocked() then
        return
    end
    local entry = WowLabCrewDB.players[label]
    if type(entry) ~= "table" then
        return "a player named " .. shown(label) .. " is no longer in the crew book."
    end
    local text = "a player named " .. shown(label)
    if type(entry.guild) == "string" and entry.guild ~= "" then
        text = text .. " <" .. shown(entry.guild) .. ">"
    end
    if entry.rating == "good" then
        text = text .. ", whom you rated good"
    elseif entry.rating == "bad" then
        text = text .. ", whom you rated bad"
    end
    local met = ns.Number(entry.met) or 0
    local last = ns.Number(entry.last)
    if met > 0 and type(date) == "function" and printable(last) then
        text = text .. ". Grouped " .. plural(met, "time", "times") .. ", the last on " .. date("%Y-%m-%d", last) .. "."
    elseif met > 0 then
        text = text .. ". Grouped " .. plural(met, "time", "times") .. "."
    else
        text = text .. ". Not grouped with before."
    end
    if type(entry.note) == "string" and entry.note ~= "" then
        text = text .. " Your note: " .. shown(entry.note)
    end
    return text .. " (The book knows names, not people.)"
end

-- The instance the owner is in now, for this pass.
local function readWhere()
    if not crewOn() then
        return
    end
    if crewBlocked() then
        return
    end
    passOpenWorld = nil
    passWhere = nil
    passWhereId = nil
    if type(GetInstanceInfo) ~= "function" then
        return
    end
    local where, kind, _, _, _, _, _, whereId = GetInstanceInfo()
    if secret(where) or secret(kind) or secret(whereId) then
        addSkipped()
        return
    end
    if kind == "none" then
        passOpenWorld = true
    elseif type(kind) == "string" then
        passOpenWorld = false
        if type(where) == "string" then
            passWhere = string.gsub(where, "|", "")
        end
        passWhereId = ns.Number(whereId)
    end
end

-- Reads one group member into the book. `unit` is "raidN" or "partyN".
local function readMember(unit)
    if not crewOn() then
        return
    end
    if crewBlocked() then
        return
    end
    local me = UnitIsUnit(unit, "player")
    if secret(me) then
        addSkipped()
        return
    end
    if not secret(me) and me then
        return
    end
    local name, realm = UnitName(unit)
    if secret(name) or secret(realm) then
        addSkipped()
        return
    end
    local players = WowLabCrewDB.players
    local current = WowLabCrewDB.current
    local entry
    local counted = false
    local label
    if
        not secret(name)
        and not secret(realm)
        and type(name) == "string"
        and name ~= ""
        and name ~= UNKNOWNOBJECT
    then
        if type(realm) == "string" and realm ~= "" then
            local key = name .. "-" .. realm
            if players[key] == nil and makeRoom() then
                players[key] = { met = 0 }
            end
            entry = players[key]
            counted = current[key] == true
            if entry ~= nil then
                current[key] = true
            end
            label = key
        elseif type(GetNormalizedRealmName) == "function" then
            -- A nil or empty realm is the owner's own realm.
            local home = GetNormalizedRealmName()
            if secret(home) then
                addSkipped()
                return
            end
            if not secret(home) and type(home) == "string" and home ~= "" then
                local key = name .. "-" .. home
                if players[key] == nil and makeRoom() then
                    players[key] = { met = 0 }
                end
                entry = players[key]
                counted = current[key] == true
                if entry ~= nil then
                    current[key] = true
                end
                label = key
            end
        end
    end
    if type(entry) ~= "table" then
        return
    end
    local now = nil
    if type(time) == "function" then
        now = ns.Number(time())
    end
    if not counted then
        -- First counted in this group: warn about a rated or noted player,
        -- with the groups and the date as they were before this one.
        if entry.rating ~= nil or entry.note ~= nil then
            say(lineFor(label))
        end
        entry.met = (ns.Number(entry.met) or 0) + 1
        entry.first = ns.Number(entry.first) or now
        if passOpenWorld == true then
            entry.where = nil
            entry.where_id = nil
        end
    end
    if passOpenWorld == false then
        entry.where = ns.String(passWhere)
        entry.where_id = ns.Number(passWhereId)
    end
    entry.last = now or ns.Number(entry.last)
    if type(UnitClass) == "function" then
        local _, classFile = UnitClass(unit)
        if secret(classFile) then
            addSkipped()
        end
        if not secret(classFile) and type(classFile) == "string" and classFile ~= "" then
            entry.class = ns.String(classFile)
        end
    end
    if type(UnitLevel) == "function" then
        local level = UnitLevel(unit)
        if secret(level) then
            addSkipped()
        end
        if not secret(level) and type(level) == "number" and level > 0 then
            entry.level = ns.Number(level)
        end
    end
    if type(UnitGroupRolesAssigned) == "function" then
        local role = UnitGroupRolesAssigned(unit)
        if secret(role) then
            addSkipped()
        end
        if not secret(role) and type(role) == "string" and role ~= "" and role ~= "NONE" then
            entry.role = ns.String(role)
        end
    end
    if type(GetGuildInfo) == "function" then
        local guild = GetGuildInfo(unit)
        if secret(guild) then
            addSkipped()
        end
        if not secret(guild) and type(guild) == "string" and guild ~= "" then
            entry.guild = ns.String(guild)
        end
    end
end

-- One pass over the group.
local function pass()
    if not crewOn() then
        return
    end
    if crewBlocked() then
        return
    end
    if not firstDone or featureMissing() ~= nil or not bookIntact() then
        return
    end
    local inGroup = IsInGroup()
    local inRaid = IsInRaid()
    if secret(inGroup) or secret(inRaid) then
        addSkipped()
        return
    end
    if not inGroup then
        local current = WowLabCrewDB.current
        for key in pairs(WowLabCrewDB.current) do
            current[key] = nil
        end
    else
        readWhere()
        if inRaid then
            for i = 1, MAX_RAID do
                local unit = "raid" .. i
                local ok = pcall(function()
                    readMember(unit)
                end)
                if not ok then
                    addSkipped()
                end
            end
        else
            for i = 1, MAX_PARTY do
                local unit = "party" .. i
                local ok = pcall(function()
                    readMember(unit)
                end)
                if not ok then
                    addSkipped()
                end
            end
        end
    end
    if not greeted then
        greeted = true
        say("Crew book on: " .. plural(countPlayers(), "player", "players") .. ". /wowlab crew off to stop.")
    end
end

-- A pass after `delay` seconds, once however many events ask for it.
local function schedule(delay)
    if not crewOn() then
        return
    end
    if crewBlocked() then
        return
    end
    if pending then
        return
    end
    if type(C_Timer) ~= "table" or type(C_Timer.After) ~= "function" then
        firstDone = true
        pcall(pass)
        return
    end
    pending = true
    local after = C_Timer.After
    after(delay, function()
        pending = false
        firstDone = true
        pcall(pass)
    end)
end

-- The first pass waits FIRST_PASS_DELAY seconds; later ones the debounce.
local function kick()
    if not crewOn() then
        return
    end
    if crewBlocked() then
        return
    end
    if not firstScheduled then
        firstScheduled = true
        schedule(FIRST_PASS_DELAY)
    elseif firstDone then
        schedule(DEBOUNCE)
    end
end

local function onRoster()
    if not crewOn() then
        return
    end
    if crewBlocked() then
        return
    end
    kick()
end

local function onRegen()
    if not crewOn() then
        return
    end
    if crewBlocked() then
        return
    end
    kick()
end

-- Registers the roster events, once per session, and asks for a pass.
local function onWorld()
    if not crewOn() then
        return
    end
    if crewBlocked() then
        return
    end
    if not armed then
        armed = true
        if not ns.On("GROUP_ROSTER_UPDATE", onRoster) then
            rosterAbsent = "the client does not know the GROUP_ROSTER_UPDATE event, so no one is recorded"
        end
        ns.On("UNIT_NAME_UPDATE", onRoster)
        ns.On("PLAYER_REGEN_ENABLED", onRegen)
    end
    kick()
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

-- Rating the target: when it is another player, its key, after adding it
-- to the book (with no group counted) if `create` and there is room; or nil
-- and the reason.
local function targetKey(create)
    if not crewOn() then
        return
    end
    if crewBlocked() then
        return
    end
    if type(UnitIsPlayer) ~= "function" or type(UnitIsUnit) ~= "function" or type(UnitName) ~= "function" then
        return nil, "this client cannot say who your target is."
    end
    local unit = "target"
    local isPlayer = UnitIsPlayer(unit)
    local me = UnitIsUnit(unit, "player")
    local name, realm = UnitName(unit)
    if secret(isPlayer) or secret(me) or secret(name) or secret(realm) then
        addSkipped()
        return nil, "the client did not say who your target is."
    end
    if
        not secret(isPlayer)
        and isPlayer
        and not secret(me)
        and not me
        and not secret(name)
        and not secret(realm)
        and type(name) == "string"
        and name ~= ""
        and name ~= UNKNOWNOBJECT
    then
        local players = WowLabCrewDB.players
        if type(realm) == "string" and realm ~= "" then
            local key = name .. "-" .. realm
            if create and players[key] == nil and makeRoom() then
                players[key] = { met = 0 }
            end
            return key
        elseif type(GetNormalizedRealmName) == "function" then
            local home = GetNormalizedRealmName()
            if not secret(home) and type(home) == "string" and home ~= "" then
                local key = name .. "-" .. home
                if create and players[key] == nil and makeRoom() then
                    players[key] = { met = 0 }
                end
                return key
            end
        end
    end
    return nil, "target another player, or type their name-realm."
end

-- `/wowlab crew who`: the warning line for every kept member of the group.
local function whoLines()
    if not crewOn() then
        return
    end
    if crewBlocked() then
        return
    end
    firstDone = true
    pcall(pass)
    local any = false
    for key in pairs(WowLabCrewDB.current) do
        if WowLabCrewDB.players[key] ~= nil then
            any = true
            say(lineFor(key))
        end
    end
    if not any then
        say("no one in your group is in the crew book yet.")
    end
    return true
end

-- The `/wowlab crew` status line's reasons, when the book can be asked.
local function reasons()
    if not crewOn() then
        return
    end
    if crewBlocked() then
        return
    end
    local whole = featureMissing()
    if whole ~= nil then
        say("not recording: " .. whole .. ".")
    end
    local fields = fieldsMissing()
    if fields ~= nil then
        say("missing:" .. fields)
    end
end

ns.On("ADDON_LOADED", crewLoaded)
ns.On("PLAYER_ENTERING_WORLD", onWorld)

-- `/wowlab crew ...`, called by Core.lua's slash handler with the whole typed
-- text. The name and the note are not lowercased; the typed text is only
-- compared, measured and, for a note, stored after cleanNote.
function ns.CrewCommand(word)
    if type(word) ~= "string" then
        return
    end
    local rest = string.match(word, "^%s*%S+%s*(.-)%s*$") or ""
    local verb, tail = string.match(rest, "^(%S*)%s*(.-)$")
    verb = string.lower(verb or "")
    tail = tail or ""
    if verb == "on" then
        if type(WowLabCrewDB) ~= "table" then
            WowLabCrewDB = {
                schema = SCHEMA,
                on = true,
                started = ns.Number(time()),
                probe = { loads = 1 },
                skipped = 0,
                players = {},
                current = {},
            }
            say("a new, empty crew book was started.")
        elseif WowLabCrewDB.schema ~= SCHEMA then
            say("this crew book was written by another version of WowLab; it is left as it is and stays off.")
            return
        elseif not pcall(bookIntact) then
            say("this crew book cannot be read; it is left as it is and stays off.")
            return
        else
            WowLabCrewDB.on = true
        end
        -- This notice stands for the login line in this session.
        greeted = true
        say("Crew book on. " .. TERMS)
        say("Recording starts out of combat. /wowlab crew off stops it and keeps what is stored. " .. KEPT)
        if not armed then
            armed = true
            if not ns.On("GROUP_ROSTER_UPDATE", onRoster) then
                rosterAbsent = "the client does not know the GROUP_ROSTER_UPDATE event, so no one is recorded"
            end
            ns.On("UNIT_NAME_UPDATE", onRoster)
            ns.On("PLAYER_REGEN_ENABLED", onRegen)
        end
        onWorld()
        return
    end
    if type(WowLabCrewDB) ~= "table" then
        say("the crew book is off and holds no one. /wowlab crew on starts it.")
        return
    end
    if verb == "off" then
        WowLabCrewDB.on = false
        local ok, kept = pcall(countPlayers)
        if ok then
            say(
                "Crew book off: nothing more is recorded; the "
                    .. plural(kept, "player", "players")
                    .. " in it are kept. /wowlab crew on starts it again. "
                    .. KEPT
            )
        else
            say("Crew book off: nothing more is recorded; what it holds is kept. " .. KEPT)
        end
        return
    end
    if verb == "" then
        local ok, kept = pcall(countPlayers)
        local count = "an unreadable number of players"
        if ok then
            count = plural(kept, "player", "players")
        end
        local since = ""
        local started = ns.Number(WowLabCrewDB.started)
        if type(date) == "function" and printable(started) then
            since = ", started " .. date("%Y-%m-%d", started)
        end
        if WowLabCrewDB.on == true then
            say("Crew book on: " .. count .. since .. ".")
        else
            say("Crew book off: " .. count .. " kept" .. since .. ".")
        end
        reasons()
        say("/wowlab crew on | off | who | good | bad | clear | note  (a name-realm, or your target)")
        return
    end
    if verb == "who" then
        if not whoLines() then
            say("nothing is read while the crew book is off or you are in combat.")
        end
        return
    end
    if verb ~= "good" and verb ~= "bad" and verb ~= "clear" and verb ~= "note" then
        say("/wowlab crew on | off | who | good | bad | clear | note  (a name-realm, or your target)")
        return
    end
    if WowLabCrewDB.on ~= true then
        say("the crew book is off. /wowlab crew on starts it.")
        return
    end
    -- A first word is a name only if it holds a "-"; otherwise the target.
    local first, after = string.match(tail, "^(%S*)%s*(.-)$")
    local typed = type(first) == "string" and string.find(first, "-", 1, true) ~= nil
    local text = tail
    if typed then
        text = after or ""
    end
    -- The note rule, checked before anything is read or added.
    local tidy = cleanNote(text)
    if verb == "note" and not noteFits(tidy) then
        say(
            "that note is "
                .. #tidy
                .. " bytes after removing control characters and |; a note holds at most "
                .. NOTE_MAX_BYTES
                .. " bytes (about 66 characters of Chinese, Japanese or Korean). Nothing was changed."
        )
        return
    end
    local wanted = nil
    local why = nil
    if typed then
        wanted = first
        -- A member of the group not yet in the book is added first.
        firstDone = true
        pcall(pass)
    else
        local create = verb == "good" or verb == "bad" or (verb == "note" and tidy ~= "")
        local ok, key, reason = pcall(targetKey, create)
        if ok then
            wanted = key
            why = reason
        else
            why = "your target could not be read."
        end
        if wanted == nil and why == nil then
            why = "nothing is read while you are in combat."
        end
    end
    if wanted == nil then
        say(why)
        return
    end
    -- Find the kept key: the exact one, or the only one that differs in case.
    local found
    local exact = false
    local matches = 0
    local lowered = string.lower(wanted)
    for key in pairs(WowLabCrewDB.players) do
        if key == wanted then
            found = key
            exact = true
        elseif not exact and type(key) == "string" and string.lower(key) == lowered then
            matches = matches + 1
            found = key
        end
    end
    if not exact and matches > 1 then
        say("more than one player in the crew book is named " .. shown(wanted) .. "; type the name exactly.")
        return
    end
    if found == nil then
        if verb == "clear" then
            say(shown(wanted) .. " is not in the crew book. Nothing was changed.")
        else
            say(
                shown(wanted)
                    .. " is not in the crew book or your group (or the book is full). Nothing was changed."
            )
        end
        return
    end
    local entry = WowLabCrewDB.players[found]
    if type(entry) ~= "table" then
        say(shown(found) .. " cannot be read in the crew book. Nothing was changed.")
        return
    end
    if verb == "good" then
        entry.rating = "good"
        say(shown(found) .. " rated good. " .. KEPT)
    elseif verb == "bad" then
        entry.rating = "bad"
        say(shown(found) .. " rated bad. " .. KEPT)
    elseif verb == "clear" then
        entry.rating = nil
        say(shown(found) .. ": rating removed. " .. KEPT)
    elseif tidy == "" then
        entry.note = nil
        say(shown(found) .. ": note removed. " .. KEPT)
    else
        entry.note = ns.String(tidy)
        say(shown(found) .. ": note kept. " .. KEPT)
    end
end
