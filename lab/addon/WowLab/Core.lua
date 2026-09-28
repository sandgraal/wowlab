-- WowLab: the Lab's data-broker addon (docs/LAB_PLAN.md §13.1, ADR-0026).
--
-- Runs in the game client only; the Lab never loads or runs this file. It
-- gathers each section during the session (on entering the world and on the
-- section's change events) and writes the versioned tables (schema 1) into
-- its SavedVariables at PLAYER_LOGOUT. `/wowlab save` refreshes the tables in
-- memory; the client writes the file at the next /reload, logout or clean
-- exit, and a crash writes nothing.
--
-- Privacy (ADR-0026): no names, realms, GUIDs, guild or chat of anyone, the
-- owner included; no text the owner typed (loadout, equipment-set or pet
-- names); no Battle.net identity; no wall-clock time. Every unit token is the
-- literal "player". tests/addon/test_lab_addon.py checks this statically.
--
-- Availability is decided by testing for the API itself, never by
-- WOW_PROJECT_ID, the interface number or the flavor. A section the client
-- cannot provide is written as { absent = "<reason>" }, never guessed.
--
-- API status: every client API used is listed in lab/addon/README.md, either
-- "confirmed by forever-addon-kit on 69893, re-verify in M11-03" or [verify].

local ADDON_NAME, ns = ...

ns.SCHEMA = 1

-- Sections, in the order they register (the file order in WowLab.toc).
ns.sections = {}
-- Last gathered record per section key.
ns.state = {}
-- probe = { loads = <n>, lost = true? }: the only state carried between sessions.
ns.probe = nil

-- C_Timer.After: confirmed by forever-addon-kit on 69893, re-verify in M11-03
-- (the kit's trait walker schedules with it). C_EventUtils.IsEventValid: [verify].
local timerAfter = type(C_Timer) == "table" and type(C_Timer.After) == "function" and C_Timer.After or nil
local isEventValid = type(C_EventUtils) == "table" and type(C_EventUtils.IsEventValid) == "function"
    and C_EventUtils.IsEventValid or nil

-- Helpers ---------------------------------------------------------------------

function ns.Absent(reason)
    return { absent = reason }
end

-- Returns `tbl[key]` when `tbl` is a table and that field is a function.
function ns.Fn(tbl, key)
    if type(tbl) == "table" and type(tbl[key]) == "function" then
        return tbl[key]
    end
    return nil
end

-- Packs varargs with their count, so nil holes survive.
function ns.Pack(...)
    local packed = { ... }
    packed.n = select("#", ...)
    return packed
end

-- Calls `fn` in protected mode; returns its results, or nothing on error.
function ns.Call(fn, ...)
    local results = ns.Pack(pcall(fn, ...))
    if not results[1] then
        return nil
    end
    return unpack(results, 2, results.n)
end

-- Copies an array of numbers, dropping anything that is not a number.
function ns.Numbers(list)
    local out = {}
    if type(list) == "table" then
        for _, value in ipairs(list) do
            if type(value) == "number" then
                out[#out + 1] = value
            end
        end
    end
    return out
end

local function say(message)
    print("|cff33ff99WowLab|r: " .. message)
end

-- Events ----------------------------------------------------------------------

local frame = CreateFrame("Frame")
local handlers = {}

-- Registers `fn` for `event`. Returns false when the client does not know the
-- event (registering an unknown event is an error in modern clients).
function ns.On(event, fn)
    if not handlers[event] then
        if isEventValid and not ns.Call(isEventValid, event) then
            return false
        end
        local ok = pcall(frame.RegisterEvent, frame, event)
        if not ok then
            return false
        end
        handlers[event] = {}
    end
    local list = handlers[event]
    list[#list + 1] = fn
    return true
end

frame:SetScript("OnEvent", function(_, event, ...)
    local list = handlers[event]
    if not list then
        return
    end
    for _, fn in ipairs(list) do
        fn(event, ...)
    end
end)

-- Sections --------------------------------------------------------------------

-- spec = {
--   key = "gear",                  -- unique; state key
--   path = { "gear" },             -- where it goes in WowLabCharDB
--   events = { "EVENT", ... },     -- change events
--   gather = function(event) ... end,  -- returns the record (a table)
--   on_world = true,               -- gather on entering the world
--   heavy = true,                  -- on entering the world, only the first time
--   immediate = true,              -- gather on the event itself, not debounced
--   not_gathered = "reason",       -- absent reason if never gathered this session
-- }
local pending = false

local function gather(section, event)
    local ok, record = pcall(section.gather, event)
    if not ok then
        record = ns.Absent("error: " .. tostring(record))
    elseif type(record) ~= "table" then
        record = ns.Absent("the section gathered nothing")
    end
    if section.events_unregistered then
        record.events_unregistered = section.events_unregistered
    end
    ns.state[section.key] = record
    section.dirty = false
end

local function gatherDirty()
    for _, section in ipairs(ns.sections) do
        if section.dirty then
            gather(section)
        end
    end
end

local function schedule()
    if not timerAfter then
        gatherDirty()
        return
    end
    if pending then
        return
    end
    pending = true
    timerAfter(2, function()
        pending = false
        gatherDirty()
    end)
end

function ns.Section(section)
    ns.sections[#ns.sections + 1] = section
    for _, event in ipairs(section.events or {}) do
        local registered = ns.On(event, function(fired)
            if section.immediate then
                gather(section, fired)
            else
                section.dirty = true
                schedule()
            end
        end)
        if not registered then
            section.events_unregistered = section.events_unregistered or {}
            local missing = section.events_unregistered
            missing[#missing + 1] = event
        end
    end
end

-- Tables ----------------------------------------------------------------------

local function clientInfo()
    if type(GetBuildInfo) ~= "function" then
        return ns.Absent("GetBuildInfo missing")
    end
    -- The third return is the build's compile date; it is not recorded.
    local version, build, _, interface = GetBuildInfo()
    return { version = version, build = build, interface = interface }
end

local function place(db, path, record)
    local node = db
    for i = 1, #path - 1 do
        local key = path[i]
        if type(node[key]) ~= "table" then
            node[key] = {}
        end
        node = node[key]
    end
    node[path[#path]] = record
end

local function copyProbe()
    local probe = { loads = ns.probe.loads }
    if ns.probe.lost then
        probe.lost = true
    end
    return probe
end

-- Builds WowLabCharDB from what was gathered this session.
function ns.Write()
    local db = { schema = ns.SCHEMA, probe = copyProbe(), client = clientInfo() }
    for _, section in ipairs(ns.sections) do
        local record = ns.state[section.key]
            or ns.Absent(section.not_gathered or "not gathered this session")
        place(db, section.path, record)
    end
    WowLabCharDB = db
    -- Account-wide table: empty until M11-03 shows which data reads the same
    -- from every character (docs/LAB_PLAN.md §13.1).
    WowLabDB = { schema = ns.SCHEMA }
end

-- Probe -----------------------------------------------------------------------

-- At ADDON_LOADED: the value the file held, plus one. A capture file that
-- loaded without a probe count means the SavedVariables loader lost it
-- (docs/LAB_PLAN.md §13.4); a file that did not load at all looks like a
-- first run, and the Lab catches that from `loads` not going up.
local function loadProbe(saved)
    local probe = { loads = 1 }
    if type(saved) == "table" then
        local prior = saved.probe
        if type(prior) == "table" and type(prior.loads) == "number" then
            probe.loads = prior.loads + 1
            if prior.lost == true then
                probe.lost = true
            end
        else
            probe.lost = true
        end
    end
    return probe
end

ns.On("ADDON_LOADED", function(_, name)
    if name ~= ADDON_NAME or ns.probe then
        return
    end
    ns.probe = loadProbe(WowLabCharDB)
    -- Keep the count in the live table at once, so it is saved even if a
    -- later step fails before PLAYER_LOGOUT.
    if type(WowLabCharDB) ~= "table" then
        WowLabCharDB = { schema = ns.SCHEMA }
    end
    WowLabCharDB.probe = copyProbe()
end)

ns.On("PLAYER_ENTERING_WORLD", function()
    for _, section in ipairs(ns.sections) do
        if section.on_world and not (section.heavy and ns.state[section.key]) then
            section.dirty = true
        end
    end
    schedule()
end)

ns.On("PLAYER_LOGOUT", function()
    if not ns.probe then
        return
    end
    gatherDirty()
    ns.Write()
end)

-- Slash command ---------------------------------------------------------------

function ns.Refresh()
    for _, section in ipairs(ns.sections) do
        if section.on_world then
            gather(section)
        end
    end
    ns.Write()
end

SLASH_WOWLAB1 = "/wowlab"
SlashCmdList.WOWLAB = function(message)
    -- The typed text is compared, never stored.
    local command = string.lower(string.match(message or "", "^%s*(%S*)") or "")
    if command == "save" then
        if not ns.probe then
            say("not loaded yet.")
            return
        end
        ns.Refresh()
        say("tables refreshed in memory; the file is written at the next /reload or logout.")
    else
        say("/wowlab save  refreshes the tables in memory (written at the next /reload or logout).")
    end
end
