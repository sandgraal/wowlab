-- WowLab: the Lab's data-broker addon (docs/LAB_PLAN.md §13.1, ADR-0026).
--
-- Runs in the game client only; the Lab never loads or runs this file. It
-- gathers each section during the session (on entering the world and on the
-- section's change events) and writes the versioned tables (schema 1) into
-- its SavedVariables at PLAYER_LOGOUT. `/wowlab save` refreshes the tables in
-- memory; the client writes the file at the next /reload, logout or clean
-- exit, and a crash writes nothing. Only three things are carried from one
-- session to the next: the probe count, the last barber-shop record
-- (Customization.lua) and the skip list, all read back from the file at
-- ADDON_LOADED.
--
-- The owner can switch a section off (`/wowlab skip <section>`, M11-21): a
-- client assertion inside a section crashes the client, `pcall` cannot catch
-- it, and a crash writes nothing, so the addon cannot mark the culprit
-- itself. The skip list is read at ADDON_LOADED, before any section registers
-- an event or is gathered.
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
-- probe = { loads = <n>, lost = true? }: carried across sessions, with customization.
ns.probe = nil
-- Section keys the owner switched off, as saved in the file (`skip`). A key
-- switched back on stays off for the rest of the session (section.off).
ns.skip = {}

-- The absent reason of a switched-off section.
local SWITCHED_OFF = "switched off by the owner"

-- C_Timer.After: confirmed by forever-addon-kit on 69893, re-verify in M11-03
-- (the kit's trait walker schedules with it). C_EventUtils.IsEventValid: [verify].
local timerAfter = type(C_Timer) == "table" and type(C_Timer.After) == "function" and C_Timer.After or nil
local isEventValid = type(C_EventUtils) == "table" and type(C_EventUtils.IsEventValid) == "function"
    and C_EventUtils.IsEventValid or nil

-- Helpers ---------------------------------------------------------------------

-- Type checks for every value the addon stores from a client API, a function
-- argument or the loaded file: each returns `value` when it has that type and
-- nil otherwise, so a table (or a function) the client hands back is never
-- written whole. A value may instead be stored directly inside an
-- `if type(value) == "number" then` (or "string", "boolean") block.
-- tests/addon/test_lab_addon.py checks every stored value this way.
function ns.Number(value)
    if type(value) == "number" then
        return value
    end
    return nil
end

function ns.String(value)
    if type(value) == "string" then
        return value
    end
    return nil
end

function ns.Bool(value)
    if type(value) == "boolean" then
        return value
    end
    return nil
end

function ns.Absent(reason)
    return { absent = ns.String(reason) }
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

-- Removes `fn` from `event`'s handlers, and unregisters the event from the
-- frame when no handler is left.
local function off(event, fn)
    local list = handlers[event]
    if not list then
        return
    end
    local kept = {}
    for _, other in ipairs(list) do
        if other ~= fn then
            kept[#kept + 1] = other
        end
    end
    if #kept > 0 then
        handlers[event] = kept
        return
    end
    handlers[event] = nil
    pcall(frame.UnregisterEvent, frame, event)
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
--   carry = function(saved) ... end,   -- at ADDON_LOADED: a record to keep from
--                                      -- the loaded WowLabCharDB, or nil
-- }
--
-- A section registers its events at ADDON_LOADED, after the saved skip list
-- is read, so a switched-off section (section.off) never registers one, is
-- never carried and never gathered: not on an event, not on entering the
-- world, not by `/wowlab save`, not at PLAYER_LOGOUT. It is written as
-- absent with SWITCHED_OFF (a section with no gather keeps its not_gathered
-- reason).
local pending = false

-- The first on-world pass after ADDON_LOADED (login or /reload) runs
-- FIRST_PASS_DELAY seconds after PLAYER_ENTERING_WORLD, on its own timer, and
-- is announced in chat, so the owner can `/wowlab skip` a section that
-- crashes the client before it runs. Until it has run, the change-event
-- debounce gathers nothing: whatever it would have gathered is still dirty
-- and the first pass takes it. Without C_Timer there is no window: the pass
-- runs at once, as before.
local FIRST_PASS_DELAY = 10
local firstPassStarted = false
local firstPassDone = false

local function gather(section, event)
    if section.off then
        return
    end
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
        if firstPassDone then
            gatherDirty()
        end
    end)
end

-- Starts the first on-world pass: once, on its own timer, never merged into a
-- pending debounce. A section switched off in the meantime is no longer dirty
-- (switchOff) and gather refuses it anyway.
local function startFirstPass()
    if firstPassStarted then
        return
    end
    firstPassStarted = true
    say("recording in 10 s. To switch a section off first: /wowlab skip <section>  (/wowlab skip lists them)")
    timerAfter(FIRST_PASS_DELAY, function()
        firstPassDone = true
        gatherDirty()
    end)
end

function ns.Section(section)
    ns.sections[#ns.sections + 1] = section
end

-- Registers the section's change events (at ADDON_LOADED, never for a
-- switched-off section). One handler serves all its events, so switching the
-- section off later removes exactly that handler.
local function listen(section)
    local function onEvent(fired)
        if section.off then
            return
        end
        if section.immediate then
            gather(section, fired)
        else
            section.dirty = true
            schedule()
        end
    end
    section.listener = onEvent
    for _, event in ipairs(section.events or {}) do
        local registered = ns.On(event, onEvent)
        if not registered then
            section.events_unregistered = section.events_unregistered or {}
            local missing = section.events_unregistered
            missing[#missing + 1] = event
        end
    end
end

local function sectionByKey(key)
    for _, section in ipairs(ns.sections) do
        if section.key == key then
            return section
        end
    end
    return nil
end

-- Switches a section off for the rest of the session and adds it to the skip
-- list: its handler is removed from every event (an event no other section
-- uses is unregistered), a pending gather is dropped, and what it gathered
-- this session is forgotten.
local function switchOff(section)
    section.off = true
    section.dirty = false
    ns.skip[section.key] = true
    ns.state[section.key] = nil
    if section.listener then
        for _, event in ipairs(section.events or {}) do
            off(event, section.listener)
        end
        section.listener = nil
    end
end

-- At ADDON_LOADED: switches off every section the loaded file lists in
-- `skip`. A string that is not a section key is ignored.
local function loadSkip(saved)
    local list = type(saved) == "table" and saved.skip or nil
    if type(list) ~= "table" then
        return
    end
    for _, value in ipairs(list) do
        if type(value) == "string" then
            local section = sectionByKey(value)
            if section then
                switchOff(section)
            end
        end
    end
end

-- Writes the skip list into `db` in section order, or removes it when empty.
local function putSkip(db)
    local list = {}
    for _, section in ipairs(ns.sections) do
        if ns.skip[section.key] then
            list[#list + 1] = ns.String(section.key)
        end
    end
    if #list > 0 then
        db.skip = list
    else
        db.skip = nil
    end
end

-- The keys of the sections `pick` accepts, comma-separated.
local function keyList(pick)
    local text = ""
    for _, section in ipairs(ns.sections) do
        if pick(section) then
            text = text == "" and section.key or text .. ", " .. section.key
        end
    end
    return text
end

-- Tables ----------------------------------------------------------------------

local function clientInfo()
    if type(GetBuildInfo) ~= "function" then
        return ns.Absent("GetBuildInfo missing")
    end
    -- The third return is the build's compile date; it is not recorded.
    local version, build, _, interface = GetBuildInfo()
    return { version = ns.String(version), build = ns.String(build), interface = ns.Number(interface) }
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
        -- A section with no gather (collections.appearances, M11-20) keeps
        -- its own reason even when switched off: it never gathers anyway.
        local record
        if section.off and section.gather then
            record = ns.Absent(SWITCHED_OFF)
        else
            record = ns.state[section.key] or ns.Absent(section.not_gathered or "not gathered this session")
        end
        place(db, section.path, record)
    end
    putSkip(db)
    WowLabCharDB = db
    -- Account-wide table: empty until M11-03 shows which data reads the same
    -- from every character (docs/LAB_PLAN.md §13.1).
    WowLabDB = { schema = ns.SCHEMA }
end

-- Probe -----------------------------------------------------------------------

-- At ADDON_LOADED: the value the file held, plus one. A WowLabCharDB that
-- loaded as a table without a probe count sets `lost` for this load only; a
-- later load that finds the probe clears it (docs/LAB_PLAN.md §13.1). A file
-- that did not load at all looks like a first run (loads = 1); §13.4's
-- two-snapshot check catches that from `loads` not going up.
local function loadProbe(saved)
    local probe = { loads = 1 }
    if type(saved) == "table" then
        local prior = saved.probe
        if type(prior) == "table" and type(prior.loads) == "number" then
            probe.loads = ns.Number(prior.loads + 1)
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
    -- The skip list first: a switched-off section is not carried and
    -- registers no event.
    loadSkip(WowLabCharDB)
    for _, section in ipairs(ns.sections) do
        if section.carry and not section.off then
            local ok, record = pcall(section.carry, WowLabCharDB)
            if ok and type(record) == "table" then
                ns.state[section.key] = record
            end
        end
    end
    for _, section in ipairs(ns.sections) do
        if not section.off then
            listen(section)
        end
    end
    -- Keep the count and the skip list in the live table at once, so they
    -- are saved even if a later step fails before PLAYER_LOGOUT.
    if type(WowLabCharDB) ~= "table" then
        WowLabCharDB = { schema = ns.SCHEMA }
    end
    WowLabCharDB.probe = copyProbe()
    putSkip(WowLabCharDB)
end)

ns.On("PLAYER_ENTERING_WORLD", function()
    for _, section in ipairs(ns.sections) do
        if section.on_world and not section.off and not (section.heavy and ns.state[section.key]) then
            section.dirty = true
        end
    end
    if timerAfter and not firstPassDone then
        startFirstPass()
    else
        schedule()
    end
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
        if section.on_world and not section.off then
            gather(section)
        end
    end
    ns.Write()
end

local function isSection()
    return true
end

local function isSkipped(section)
    return ns.skip[section.key] == true
end

local function isBackOnAtReload(section)
    return section.off and not ns.skip[section.key]
end

-- `/wowlab skip [section]` and `/wowlab unskip <section>`. The typed key is
-- only compared with the section keys; it is never stored or printed.
local function skipCommand(command, key)
    if key == "" then
        if command == "unskip" then
            say("/wowlab unskip <section>  sections: " .. keyList(isSection))
            return
        end
        local skipped = keyList(isSkipped)
        say("switched off: " .. (skipped == "" and "no section" or skipped))
        local later = keyList(isBackOnAtReload)
        if later ~= "" then
            say("switched back on from the next /reload or login: " .. later)
        end
        say("sections: " .. keyList(isSection))
        return
    end
    local section = sectionByKey(key)
    if not section then
        say("no such section. Sections: " .. keyList(isSection))
        return
    end
    if command == "skip" then
        switchOff(section)
        say(section.key .. " switched off now; /reload or log out to save that (a crash writes nothing).")
    elseif ns.skip[section.key] then
        ns.skip[section.key] = nil
        say(section.key .. " switched back on from the next /reload or login; /reload or log out to save that.")
    elseif section.off then
        say(section.key .. " is already switched back on from the next /reload or login.")
        return
    else
        say(section.key .. " is not switched off.")
        return
    end
    if type(WowLabCharDB) == "table" then
        putSkip(WowLabCharDB)
    end
end

SLASH_WOWLAB1 = "/wowlab"
SlashCmdList.WOWLAB = function(message)
    -- The typed text is compared, never stored.
    local command, key = string.match(message or "", "^%s*(%S*)%s*(%S*)")
    command = string.lower(command or "")
    key = string.lower(key or "")
    if command == "skip" or command == "unskip" then
        if not ns.probe then
            say("not loaded yet.")
            return
        end
        skipCommand(command, key)
    elseif command == "save" then
        if not ns.probe then
            say("not loaded yet.")
            return
        end
        ns.Refresh()
        say("tables refreshed in memory; the file is written at the next /reload or logout.")
    else
        say("/wowlab save  refreshes the tables in memory (written at the next /reload or logout).")
        say("/wowlab skip <section>  switches a section off; /wowlab unskip <section>  back on; /wowlab skip  lists.")
    end
end
