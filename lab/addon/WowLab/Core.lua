-- WowLab: the Lab's data-broker addon (docs/LAB_PLAN.md §13.1, ADR-0026).
--
-- Runs in the game client only; the Lab never loads or runs this file. It
-- gathers each section during the session (on entering the world and on the
-- section's change events) and writes the versioned tables (schema 5) into
-- its SavedVariables at PLAYER_LOGOUT. `/wowlab save` refreshes the tables in
-- memory; the client writes the file at the next /reload, logout or clean
-- exit, and a crash writes nothing. Only five things are carried from one
-- session to the next: the probe count, the last barber-shop record
-- (Customization.lua), the skip list, and (schema 5) the debug switch and the
-- announced schema, all read back from the file at ADDON_LOADED.
--
-- The owner can switch a section off (`/wowlab skip <section>`, M11-21): a
-- client assertion inside a section crashes the client, `pcall` cannot catch
-- it, and a crash writes nothing, so the addon cannot mark the culprit
-- itself. The skip list is read at ADDON_LOADED, before any section registers
-- an event or is gathered.
--
-- What stays out (ADR-0026, note of 2026-11-20): nothing is ever sent (no
-- chat, addon message, mail or whisper); no dynamic lookup or dynamic code;
-- no BattleTag (BNGetInfo); nothing is gathered in combat; and a client value
-- is tested with issecretvalue before it is compared or stored where the
-- section reads it (Actions.lua, and Identity.lua's schema-5 values). Since
-- schema 5 the owner's own account is kept as the client gives it: macros,
-- every binding command, the character's GUID, name, realm and guild, a
-- crafter's GUID in an item link, and the time of each gather and of the
-- write. Every unit token outside Crew.lua is the literal "player".
-- tests/addon/test_lab_addon.py checks what stays statically.
--
-- Availability is decided by testing for the API itself, never by
-- WOW_PROJECT_ID, the interface number or the flavor. A section the client
-- cannot provide is written as { absent = "<reason>" }, never guessed.
--
-- API status: every client API used is listed in lab/addon/README.md, either
-- "confirmed by forever-addon-kit on 69893, re-verify in M11-03" or [verify].

local ADDON_NAME, ns = ...

-- Schema 2 (M11-29): schema 1 plus `events_received` and
-- `chr_model_id_absent` on customization (docs/LAB_PLAN.md §13.1: after
-- M11-04, any change is a new schema). Schema 3 (M13-05): schema 2 plus the
-- config id talents.legacy left out (`left_out_config` or
-- `left_out_config_absent`, M11-35) and three sections, `spellbook`,
-- `actionbars` and `bindings` (Actions.lua). Nothing read back at
-- ADDON_LOADED looks at the schema (the probe, the skip list and the carried
-- customization record are read by key), so an older file's probe count keeps
-- rising across the change, and the next write is schema 3 for both
-- variables. Schema 4 (M15-01): schema 3 plus one section, `identity`
-- (Identity.lua: race id, UnitSex's raw value, class id and file); nothing
-- else changed, and an older file moves to schema 4 the same way.
-- Schema 5 (M20-13; ADR-0026, note of 2026-11-20): schema 4 plus what the
-- owner's own account shows (Actions.lua's `macros` and every binding
-- command, Identity.lua's guid, name, realm and guild, Gear.lua's links as
-- the client gives them), `gathered` (time() of each section's last gather)
-- and `written_at` / `written_by` (time() of this write, and what made it);
-- and quieter: `announced` and `debug` (below), `probes` and
-- `events_received` only in a debug session, `dropped` in their place. An
-- older file moves to schema 5 the same way.
ns.SCHEMA = 5

-- Sections, in the order they register (the file order in WowLab.toc).
ns.sections = {}
-- Last gathered record per section key.
ns.state = {}
-- probe = { loads = <n>, lost = true? }: carried across sessions, with customization.
ns.probe = nil
-- Section keys the owner switched off, as saved in the file (`skip`). A key
-- switched back on stays off for the rest of the session (section.off).
ns.skip = {}
-- Debug (`/wowlab debug on|off`, schema 5): off by default. When on, the
-- research scaffolding is written this session: the `probes` on Actions.lua's
-- records and the count-only events (the control event and the barber-shop
-- names counted without gathering, Customization.lua) with their
-- `events_received`. Read from the file at ADDON_LOADED (`debug = true`, left
-- out when off); the slash command changes it from the next /reload or
-- login, never in the same session, because events register at ADDON_LOADED.
ns.debug = false
-- What `/wowlab debug on|off` asked for, saved for the next load.
local debugNext = false
-- `announced` (schema 5): the schema of the addon whose own write of the
-- tables last ran after a first pass. The chat line prints when it differs
-- from ns.SCHEMA (a new lab-addon format, or a first run) or the probe was
-- lost. It is cleared from the live table at ADDON_LOADED and put back only
-- by this session's own write, so a session the client saved without that
-- write prints the line again next time. A crash is not seen: it writes
-- nothing, so the file still holds the last clean write and its
-- `announced`. Once per schema, by choice, not per addon version: the
-- installed TOC carries `## Version` (`wowlab addon install` writes it), but
-- the addon does not read its version at run time (whether
-- C_AddOns.GetAddOnMetadata works on this client is [verify]), and the
-- schema is bumped with every change to what the addon writes.
ns.announced = nil
-- time() of each section's last gather this session, by section key.
local gatheredAt = {}
-- M20-17: true for a section whose record was gathered by the logout pass
-- (a last resort: nothing earlier this session), by section key.
local gatheredAtLogout = {}
-- M20-17 round 1: true for a section whose `again_fields` came from the
-- logout read while the rest of its record is the session's, by key.
local againAtLogout = {}

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

-- What a gather returns when it may not read now (Actions.lua: in combat, or
-- not known): never stored. `gather` below keeps the section's earlier
-- record, writes the reason only when nothing was gathered this session, and
-- leaves the section dirty.
function ns.Blocked(reason)
    return { blocked = ns.String(reason) }
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
--   count_only = { "EVENT", ... }, -- events only counted, never gathered on (M11-29);
--                                  -- a section with this list writes events_received
--   retry = { "EVENT", ... },      -- events that gather the section again only while it
--                                  -- is still dirty (M13-05: PLAYER_REGEN_ENABLED after a
--                                  -- gather combat blocked); they never mark it dirty
--   gather = function(event) ... end,  -- returns the record (a table), or
--                                      -- ns.Blocked(reason) when it may not read now
--   on_world = true,               -- gather on entering the world
--   heavy = true,                  -- on entering the world, only the first time
--   immediate = true,              -- gather on the event itself, not debounced
--   again_without = "field",       -- M20-16: a record without this field leaves the
--                                  -- section dirty, so the next pass gathers it again
--   again_fields = { "field", ... },  -- M20-17: what a logout read of such a record
--                                     -- supplies; the rest stays the session's
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
local FIRST_PASS_DELAY = 15
local firstPassStarted = false
local firstPassDone = false

-- After the first pass (schema 5): change events only mark their section
-- dirty, and the dirty sections are gathered together at most once every
-- GATHER_INTERVAL seconds, out of combat. At PLAYER_LOGOUT only a section
-- with nothing gathered this session is (gatherAtLogout, M20-17). A pass whose
-- timer ends in combat waits for PLAYER_REGEN_ENABLED, and no new timer
-- starts meanwhile. Combat is known from the client's own events
-- (PLAYER_REGEN_DISABLED and PLAYER_REGEN_ENABLED) and asked as the world is
-- entered; a section that must not read in combat (Actions.lua) still asks
-- the client itself.
local GATHER_INTERVAL = 30
local inCombat = false
-- A timer's pass waiting for the end of combat; `pending` stays set meanwhile.
local waitingForCombat = false
-- The first pass waiting for the end of combat.
local firstPassWaiting = false

local function gather(section, event)
    if section.off then
        return
    end
    local ok, record = pcall(section.gather, event)
    -- ns.Blocked (M13-05): the section may not read now. What it gathered
    -- earlier this session is kept; its reason is written only when nothing
    -- was; and it stays dirty, so the next pass (its retry event, any
    -- debounce, PLAYER_LOGOUT) tries again. A section with a retry event
    -- (Actions.lua's three) keeps its earlier record through an error the
    -- same way: without issecretvalue a secret value makes its gather raise
    -- (M13-05 round 3), and that is no reason to drop a good record.
    local blocked = ok and type(record) == "table" and type(record.blocked) == "string"
    local kept = blocked or (not ok and section.retry ~= nil)
    if kept and section.gathered then
        section.dirty = true
        return
    end
    if blocked then
        record = ns.Absent(record.blocked)
    elseif not ok then
        record = ns.Absent("error: " .. tostring(record))
    elseif type(record) ~= "table" then
        record = ns.Absent("the section gathered nothing")
    end
    ns.state[section.key] = record
    if blocked then
        section.dirty = true
    else
        section.gathered = true
        section.dirty = false
        gatheredAt[section.key] = ns.Number(time())
        -- M20-16: a record its section calls incomplete leaves it dirty, so
        -- the next pass (PLAYER_LOGOUT at the latest) gathers it again and
        -- its record replaces this one.
        if section.again_without and record[section.again_without] == nil then
            section.dirty = true
        end
    end
end

local function gatherDirty()
    for _, section in ipairs(ns.sections) do
        if section.dirty then
            gather(section)
        end
    end
end

-- The logout pass (M20-17). On the owner's run on 71037 (2026-11-20) a
-- logout about 30 s after login wrote `actionbars` with 0 of 180 slots
-- filled and nothing dropped, where the session before had written 27 and
-- nothing had been removed from the bars. A change event after the first
-- pass (ACTIONBAR_SLOT_CHANGED; the client is believed to fire such events
-- as the character leaves the world [verify]) left the section dirty, the
-- 30 s timer had not run, and the pass at PLAYER_LOGOUT read every slot when
-- GetActionInfo already answered nothing; that read replaced the good
-- record. So at logout only a dirty section with no record from this
-- session is gathered, as a last resort (`at_logout` in `gathered`). A
-- section whose record lacks its `again_without` field (identity's realm,
-- M20-16) is read again; the session's record and its time are always put
-- back, and only its `again_fields` are taken from the new read, when it has
-- the field (`again_at_logout` in `gathered`). Every
-- other section is written as this session's passes gathered it, with its
-- own time in `gathered`; a change in the last GATHER_INTERVAL seconds
-- before logout is not recorded (`/wowlab save` before logging out takes it).

-- True when the section's record from this session lacks its
-- `again_without` field.
local function lacksAgain(section)
    local held = ns.state[section.key]
    return section.again_without ~= nil and type(held) == "table" and held[section.again_without] == nil
end

-- Copies the section's `again_fields` (identity: the realm and the keys that
-- belong to it) from the logout read `fresh` into the session's record
-- `held`; a field the logout read has not written is cleared, so the
-- session's `realm_absent` goes. Nothing else of the logout read is kept: a
-- read as the world is torn down may be emptier in every other field.
local function takeAgain(section, held, fresh)
    for _, field in ipairs(section.again_fields or {}) do
        if type(field) == "string" then
            -- Text or true/false (realm_normalized); anything else clears it.
            local value = fresh[field]
            if type(value) == "boolean" then
                held[field] = ns.Bool(value)
            else
                held[field] = ns.String(value)
            end
        end
    end
end

local function gatherAtLogout()
    for _, section in ipairs(ns.sections) do
        if section.dirty and not section.gathered then
            gather(section)
            if section.gathered then
                gatheredAtLogout[section.key] = true
            end
        elseif section.dirty and lacksAgain(section) then
            local held, heldAt = ns.state[section.key], gatheredAt[section.key]
            gather(section)
            local fresh = ns.state[section.key]
            ns.state[section.key] = held
            gatheredAt[section.key] = heldAt
            if type(fresh) == "table" and fresh[section.again_without] ~= nil then
                takeAgain(section, held, fresh)
                againAtLogout[section.key] = true
            end
        end
    end
end

local function schedule()
    if not timerAfter then
        if inCombat then
            waitingForCombat = true
        else
            gatherDirty()
        end
        return
    end
    if pending then
        return
    end
    pending = true
    timerAfter(GATHER_INTERVAL, function()
        if firstPassDone and inCombat then
            waitingForCombat = true
            return
        end
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
    -- Once per schema (each new lab-addon format), or after a session whose
    -- own write did not run, or when the probe was lost.
    if ns.announced ~= ns.SCHEMA or ns.probe.lost then
        say(
            "recording in "
                .. FIRST_PASS_DELAY
                .. " s. To switch a section off first: /wowlab skip <section>  (/wowlab skip lists them)"
        )
    end
    timerAfter(FIRST_PASS_DELAY, function()
        firstPassDone = true
        -- Out of combat, like every later pass.
        if inCombat then
            firstPassWaiting = true
        else
            gatherDirty()
        end
    end)
end

function ns.Section(section)
    ns.sections[#ns.sections + 1] = section
end

-- Registers the section's change events (at ADDON_LOADED, never for a
-- switched-off section). One handler serves all its events, so switching the
-- section off later removes exactly that handler.
--
-- A section with a `count_only` list (M11-29) registers those events too, on
-- the same handler, and counts every event it registered as the event reaches
-- the handler: section.events_received maps each registered event, change
-- events included, to that count (0 from registration). A count_only event is
-- counted and nothing else: it never gathers and never marks the section
-- dirty. One the client refuses has no entry in events_received and is not
-- added to events_unregistered, which stays the list of refused change events
-- and (M13-05) refused retry events. A retry event registers on the same
-- handler and only schedules a pass when the section is still dirty.
local function listen(section)
    local changes = {}
    for _, event in ipairs(section.events or {}) do
        changes[event] = true
    end
    -- Count-only events and their counts are debug only (schema 5).
    local counts = nil
    if section.count_only and ns.debug then
        counts = {}
    end
    local retries = {}
    for _, event in ipairs(section.retry or {}) do
        retries[event] = true
    end
    local function onEvent(fired)
        if section.off then
            return
        end
        if counts then
            counts[fired] = (ns.Number(counts[fired]) or 0) + 1
        end
        if retries[fired] then
            -- A retry event gathers the section only while it is still
            -- dirty (a gather was blocked); it never marks it dirty.
            if section.dirty then
                schedule()
            end
            return
        end
        if not changes[fired] then
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
    section.events_received = counts
    for _, event in ipairs(section.events or {}) do
        local registered = ns.On(event, onEvent)
        if not registered then
            section.events_unregistered = section.events_unregistered or {}
            local missing = section.events_unregistered
            missing[#missing + 1] = event
        elseif counts then
            counts[event] = 0
        end
    end
    -- Retry events (M13-05): a refused one is listed in events_unregistered,
    -- as a refused change event is.
    for _, event in ipairs(section.retry or {}) do
        if not ns.On(event, onEvent) then
            if not section.events_unregistered then
                section.events_unregistered = {}
            end
            local refused = section.events_unregistered
            refused[#refused + 1] = ns.String(event)
        elseif counts then
            counts[event] = 0
        end
    end
    if counts then
        for _, event in ipairs(section.count_only or {}) do
            if ns.On(event, onEvent) then
                counts[event] = 0
            end
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
        for _, event in ipairs(section.retry or {}) do
            off(event, section.listener)
        end
        for _, event in ipairs(section.events or {}) do
            off(event, section.listener)
        end
        -- Outside a debug session the count-only events were never
        -- registered; `off` is then a no-op for them.
        for _, event in ipairs(section.count_only or {}) do
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

-- Writes the debug switch for the next load into `db`, or removes it when off.
local function putDebug(db)
    if debugNext then
        db.debug = true
    else
        db.debug = nil
    end
end

-- At ADDON_LOADED: the debug switch and the announced schema the loaded file
-- holds. Anything but `true` is off; anything but a number is none.
local function loadQuiet(saved)
    if type(saved) ~= "table" then
        return
    end
    ns.debug = saved.debug == true
    debugNext = ns.debug
    if type(saved.announced) == "number" then
        ns.announced = ns.Number(saved.announced)
    end
end

-- Outside a debug session (schema 5) a record keeps no `probes`; in their
-- place `dropped`, one whole number: the calls that raised plus the values
-- dropped as secret or untestable, over every probe but issecretvalue's own
-- (its errors are values already counted on the API that gave them).
local function quiet(record)
    if ns.debug or type(record) ~= "table" or type(record.probes) ~= "table" then
        return
    end
    local dropped = 0
    for _, entry in ipairs(record.probes) do
        if type(entry) == "table" and entry.api ~= "issecretvalue" then
            dropped = dropped + (ns.Number(entry.errors) or 0) + (ns.Number(entry.secret) or 0)
        end
    end
    record.probes = nil
    record.dropped = dropped
end

-- `gathered`: time() of each section's last gather this session, in section
-- order; a section not gathered this session, or switched off, has none.
-- M20-17: `at_logout = true` on one the logout pass gathered.
local function gatheredList()
    local list = {}
    for _, section in ipairs(ns.sections) do
        local at = ns.Number(gatheredAt[section.key])
        if at and not section.off then
            local entry = { section = ns.String(section.key), at = at }
            if gatheredAtLogout[section.key] then
                entry.at_logout = true
            end
            -- Round 1: the field the logout read supplied (identity:
            -- "realm"); `at` stays the time of the session's record.
            if againAtLogout[section.key] then
                entry.again_at_logout = ns.String(section.again_without)
            end
            list[#list + 1] = entry
        end
    end
    return list
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

-- Builds WowLabCharDB from what was gathered this session. `by` is what
-- made the write: "logout" (PLAYER_LOGOUT) or "save" (`/wowlab save`).
function ns.Write(by)
    local db = { schema = ns.SCHEMA, probe = copyProbe(), client = clientInfo() }
    for _, section in ipairs(ns.sections) do
        -- A section with no gather (collections.appearances, M11-20) keeps
        -- its own reason even when switched off: it never gathers anyway.
        local record
        if section.off and section.gather then
            record = ns.Absent(SWITCHED_OFF)
        else
            record = ns.state[section.key] or ns.Absent(section.not_gathered or "not gathered this session")
            -- The events the client refused, or an empty list when it
            -- accepted all of them, on every record written for a section
            -- that registered its events this session (section.listener is
            -- set by listen and cleared by switchOff): gathered, carried
            -- (customization; carry drops the saved list, so this is the
            -- session's) or never gathered (its not_gathered reason). One
            -- login answers whether an event such as BARBER_SHOP_OPEN exists
            -- even when the section never runs (M11-22). An empty list is the
            -- answer; a record without the key predates M11-22. A section
            -- switched off (at load or this session) keeps the plain owner
            -- reason and nothing else.
            if section.listener and type(section.events) == "table" and #section.events > 0 then
                record.events_unregistered = section.events_unregistered or {}
            end
            quiet(record)
        end
        place(db, section.path, record)
        -- M11-29: the counts kept by `listen`, on whatever record is written
        -- for a section with a count_only list that registered its events
        -- this session (gathered, carried or never gathered). Set after
        -- place, on the table place just put into db. A switched-off section
        -- has no listener, so it keeps the plain owner reason.
        if section.listener and section.events_received then
            record.events_received = section.events_received
        end
    end
    putSkip(db)
    putDebug(db)
    db.gathered = gatheredList()
    db.written_at = ns.Number(time())
    db.written_by = ns.String(by)
    -- Put back only after a first pass this session (see ns.announced).
    if firstPassDone then
        db.announced = ns.SCHEMA
    else
        db.announced = ns.Number(ns.announced)
    end
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
    loadQuiet(WowLabCharDB)
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
    -- Until this session's own write puts it back (see ns.announced).
    WowLabCharDB.announced = nil
end)

-- Combat, from the client's events: a pass that waited for combat to end
-- runs as soon as it does.
ns.On("PLAYER_REGEN_DISABLED", function()
    inCombat = true
end)

ns.On("PLAYER_REGEN_ENABLED", function()
    inCombat = false
    local run = false
    if firstPassWaiting then
        firstPassWaiting = false
        run = true
    end
    if waitingForCombat then
        waitingForCombat = false
        pending = false
        run = true
    end
    if run then
        gatherDirty()
    end
end)

ns.On("PLAYER_ENTERING_WORLD", function()
    -- Combat as the client says it now: a /reload in combat sends no
    -- PLAYER_REGEN_DISABLED. When the client cannot say, the flag is left as
    -- the events set it.
    if type(InCombatLockdown) == "function" then
        local ok, locked = pcall(InCombatLockdown)
        if ok and locked == true then
            inCombat = true
        elseif ok then
            inCombat = false
        end
    end
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
    -- Nothing is gathered in combat: a logout or /reload in combat writes
    -- what the session's earlier passes gathered. Out of combat only what
    -- gatherAtLogout allows (M20-17).
    if not inCombat then
        gatherAtLogout()
    end
    ns.Write("logout")
end)

-- Slash command ---------------------------------------------------------------

-- `/wowlab save`: false, and nothing gathered or written, in combat.
function ns.Refresh()
    if inCombat then
        return false
    end
    for _, section in ipairs(ns.sections) do
        if section.on_world and not section.off then
            gather(section)
        end
    end
    ns.Write("save")
    return true
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

-- `/wowlab debug [on|off]` (schema 5). The typed word is only compared.
local function debugCommand(word)
    if word == "on" or word == "off" then
        debugNext = word == "on"
        if type(WowLabCharDB) == "table" then
            putDebug(WowLabCharDB)
        end
    elseif word ~= "" then
        say("/wowlab debug on  or  /wowlab debug off")
        return
    end
    say(
        "debug is "
            .. (ns.debug and "on" or "off")
            .. " this session, and "
            .. (debugNext and "on" or "off")
            .. " from the next /reload or login; /reload or log out to save a change."
    )
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
    elseif command == "debug" then
        if not ns.probe then
            say("not loaded yet.")
            return
        end
        debugCommand(key)
    elseif command == "save" then
        if not ns.probe then
            say("not loaded yet.")
            return
        end
        if ns.Refresh() then
            say("tables refreshed in memory; the file is written at the next /reload or logout.")
        else
            say("in combat: nothing is gathered in combat. Type it again after the fight.")
        end
    elseif command == "crew" then
        -- The crew book (Crew.lua), handed the whole typed text: a name and a
        -- note are not lowercased.
        ns.CrewCommand(message)
    else
        say("/wowlab save  refreshes the tables in memory (written at the next /reload or logout).")
        say("/wowlab skip <section>  switches a section off; /wowlab unskip <section>  back on; /wowlab skip  lists.")
        say("/wowlab debug on|off  records the research probes from the next /reload or login (off by default).")
        say("/wowlab crew  the crew book of players you group with: off until you turn it on.")
    end
end
