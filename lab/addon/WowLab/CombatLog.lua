-- The combat-log switch (docs/LAB_PLAN.md §21.3; ADR-0026, note of
-- 2026-11-22): the addon turns the client's combat log on in the open world
-- and off in an instance, in a raid group and after an idle time, by time and
-- place only. Nothing about other players or the PvP flag decides it.
--
-- The rule, at each check out of combat:
--   - in an instance, or in a raid group: off;
--   - in the open world: on, except after the addon turned it off for idling
--     there, when only the next fight turns it back on;
--   - the idle time (IDLE_MINUTES by default, a saved setting) with no combat
--     since the log went on or the last fight ended: off.
-- In combat it does one thing only: at the start of a fight, a log it turned
-- off for idling in the open world goes back on. A zone or group change in
-- combat is checked when the fight ends.
--
-- It turns off only a log it turned on itself. A log found on that it did not
-- turn on is the player's: left on, never turned off, and said once. A log
-- it turned on that the player turned off by hand stays off for the rest of
-- the session, said once. It never changes the Advanced Combat Logging
-- setting; it says once a session when that setting is off.
--
-- It is a section (`combatlog`), so `/wowlab skip combatlog` (or
-- `/wowlab combatlog off`) switches the whole feature off, and its record in
-- WowLabCharDB says what it last did: a count and the time and reason of the
-- last switch, never anything the log holds. LoggingCombat is the only thing
-- it changes; the client writes the log, not the addon.
--
-- Not yet confirmed on the owner's client [verify]: that LoggingCombat exists
-- for addons there and a call from an addon switches the log, in and out of
-- combat; that LoggingCombat() with no argument answers true or false; that
-- the log stays on across /reload; IsInInstance in the open world; the
-- advancedCombatLogging setting's name. Where LoggingCombat is missing, or
-- the log does not change when asked, the switch is dormant for the session
-- and says so once.

local _, ns = ...

local KEY = "combatlog"

-- The idle time with no combat before the addon turns its own log off, in
-- minutes: the default, and the bounds `/wowlab combatlog idle` accepts.
local IDLE_MINUTES = 10
local IDLE_LEAST = 1
local IDLE_MOST = 120

-- What each event the section listens to asks of the switch. The two
-- WOWLAB_ names are the addon's own timers (ns.Wake), never client events:
-- the check FIRST_DELAY seconds after a loading screen, and the idle check.
-- PLAYER_ENTERING_WORLD itself only starts that timer, so a
-- `/wowlab skip combatlog` typed in the first 15 s after login stops the
-- first call, as the first recording pass allows for every section.
local FIRST_DELAY = 15
local ENTER_EVENT = "WOWLAB_COMBATLOG_ENTER"
local IDLE_EVENT = "WOWLAB_COMBATLOG_IDLE"
local TRIGGERS = {
    PLAYER_ENTERING_WORLD = "arrive",
    WOWLAB_COMBATLOG_ENTER = "start",
    ZONE_CHANGED_NEW_AREA = "enter",
    GROUP_ROSTER_UPDATE = "enter",
    PLAYER_REGEN_DISABLED = "combat",
    PLAYER_REGEN_ENABLED = "calm",
    WOWLAB_COMBATLOG_IDLE = "idle",
}

-- The reasons a switch is made, as the record and the chat line give them.
local WHY_WORLD = "open world"
local WHY_COMBAT = "combat in the open world"
local WHY_INSTANCE = "entered an instance"
local WHY_RAID = "in a raid group"
local WHY_IDLE = "no combat for the idle time"
local WHY_KNOWN = {
    ["open world"] = true,
    ["combat in the open world"] = true,
    ["entered an instance"] = true,
    ["in a raid group"] = true,
    ["no combat for the idle time"] = true,
}

-- Kept in the record, so they survive a /reload or a logout: read back from
-- the carried record at the first gather of the session.
local switches = 0
local lastAt = nil
local lastTo = nil
local lastWhy = nil
local idleMinutes = IDLE_MINUTES
-- The log is on because this addon turned it on (the log stays on across a
-- /reload [verify], and stays the addon's to turn off).
local mine = false

-- This session only.
local loaded = false -- the carried record has been read
local started = false -- the first check after the loading screen has run
local ownSeen = false -- the addon's own log was seen on this session
local handsOff = false -- the player turned off a log the addon turned on
local dormant = nil -- why the switch does nothing this session
local idled = false -- turned off for idling in the open world, since the last loading screen
local waiting = false -- a zone or group change came in combat
local active = nil -- time() of the last switch on, fight start or fight end
local arrivedAt = nil -- time() of the last loading screen
local fighting = false
local logOn = nil -- the log's state at the last check
local advanced = nil -- the advancedCombatLogging setting at the last switch on
local told = {}

local function sayOnce(name, text)
    if told[name] then
        return
    end
    told[name] = true
    ns.Say(text)
end

-- Reads what the carried record kept, once; and the idle time, at every
-- gather (`/wowlab combatlog idle` changes it in the record).
local function readBack()
    local held = ns.state[KEY]
    if type(held) ~= "table" or type(held.absent) ~= "nil" then
        return
    end
    if type(held.idle_minutes) == "number" then
        idleMinutes = held.idle_minutes
    end
    if loaded then
        return
    end
    loaded = true
    if type(held.switches) == "number" then
        switches = held.switches
    end
    if type(held.last_at) == "number" then
        lastAt = held.last_at
    end
    if type(held.last_to) == "string" then
        lastTo = held.last_to
    end
    if type(held.last_why) == "string" then
        lastWhy = held.last_why
    end
    mine = held.mine == true
end

-- The log's state as the client says it: true, false, or nil when it says
-- neither.
local function logState()
    local ok, on = pcall(LoggingCombat)
    if ok and type(on) == "boolean" then
        return on
    end
    return nil
end

-- "instance", "raid group" or "world"; nil when the client cannot say.
local function place()
    if type(IsInInstance) ~= "function" or type(IsInRaid) ~= "function" then
        return nil
    end
    local okInstance, inside = pcall(IsInInstance)
    if not okInstance or type(inside) ~= "boolean" then
        return nil
    end
    if inside then
        return "instance"
    end
    local okRaid, raid = pcall(IsInRaid)
    if not okRaid then
        return nil
    end
    if raid == true then
        return "raid group"
    end
    return "world"
end

-- Whether the client is in combat now (a /reload in combat sends no
-- PLAYER_REGEN_DISABLED); false when it cannot say.
local function inCombatNow()
    if type(InCombatLockdown) ~= "function" then
        return false
    end
    local ok, locked = pcall(InCombatLockdown)
    return ok and locked == true
end

-- The advancedCombatLogging setting: true, false, or nil when not known.
local function advancedOn()
    local get = ns.Fn(C_CVar, "GetCVar")
    if not get then
        return nil
    end
    local value = ns.Call(get, "advancedCombatLogging")
    if value == "1" then
        return true
    elseif value == "0" then
        return false
    end
    return nil
end

local function noted(to, why)
    switches = switches + 1
    lastAt = ns.Number(time())
    lastTo = to
    lastWhy = why
    ns.Say("combat log " .. to .. " (" .. why .. ").")
end

-- Turns the log on or off, and checks that it changed. Returns false, and
-- leaves the switch dormant for the session, when it did not.
local function turn(on)
    pcall(LoggingCombat, on)
    if logState() ~= on then
        dormant = "the game did not switch the combat log when asked"
        sayOnce("refused", "the game did not switch the combat log when asked, so WowLab leaves it to you this session.")
        return false
    end
    logOn = on
    return true
end

local function turnOn(why)
    if not turn(true) then
        return
    end
    mine = true
    ownSeen = true
    idled = false
    active = ns.Number(time())
    noted("on", why)
    ns.Wake(KEY, idleMinutes * 60, IDLE_EVENT)
    -- The setting is read out of combat only.
    if fighting then
        return
    end
    advanced = advancedOn()
    if advanced == false then
        sayOnce(
            "advanced",
            "Advanced Combat Logging is off (game options, Network); wowlab reads more from the log with it on."
        )
    end
end

-- What the log should be after a check out of combat: "on", "off", or nil
-- to leave it as it is.
local function want(trigger, where)
    if where ~= "world" or trigger == "idle" then
        return "off"
    end
    if idled then
        return nil
    end
    return "on"
end

-- The check out of combat: on entering (a loading screen, a zone or group
-- change) and when the idle time is over.
local function check(trigger)
    local on = logState()
    if on == nil then
        dormant = "LoggingCombat gave no state"
        sayOnce("no state", "the game did not say whether the combat log is on, so WowLab leaves it to you this session.")
        return
    end
    logOn = on
    -- A log the addon turned on and finds off, after it was seen on this
    -- session: the player turned it off. Found off at the first check of a
    -- session, a client restart ended it; that is no one's hand.
    if mine and on then
        ownSeen = true
    elseif mine and not on then
        mine = false
        if ownSeen then
            handsOff = true
            sayOnce("hands off", "you turned the combat log off; WowLab leaves it off until the next /reload or login.")
        end
    end
    local where = place()
    if where == nil then
        return
    end
    if where ~= "world" then
        idled = false
    end
    if handsOff then
        return
    end
    local wanted = want(trigger, where)
    if wanted == "on" and not on then
        turnOn(WHY_WORLD)
    elseif wanted == "on" and on and not mine then
        sayOnce("already on", "the combat log was already on; WowLab leaves it as you set it and will not turn it off.")
    elseif wanted == "off" and on and mine then
        if turn(false) then
            mine = false
            idled = trigger == "idle"
            if where == "instance" then
                noted("off", WHY_INSTANCE)
            elseif where == "raid group" then
                noted("off", WHY_RAID)
            else
                noted("off", WHY_IDLE)
            end
        end
    end
end

-- At the start of a fight: only a log the addon turned off for idling in the
-- open world goes back on.
local function resume()
    if not idled or handsOff then
        return
    end
    local on = logState()
    if on == false then
        turnOn(WHY_COMBAT)
    elseif on == true then
        -- Turned on by the player meanwhile: theirs.
        idled = false
        logOn = true
    end
end

-- What one event or timer asks of the switch.
local function run(trigger)
    if trigger == "arrive" then
        idled = false
        arrivedAt = ns.Number(time())
        -- A check is owed until one runs: Core drops the timer below when it
        -- ends in combat, and the end of the fight then runs it.
        waiting = true
        -- The check runs FIRST_DELAY s after the loading screen; without a
        -- timer, at once.
        if ns.Wake(KEY, FIRST_DELAY, ENTER_EVENT) then
            return
        end
        trigger = "start"
    end
    -- Only the check after a loading screen starts the switch: a zone or
    -- group change in the first 15 s after login waits for it.
    if trigger == "start" then
        started = true
        trigger = "enter"
    end
    -- Core drops a timer that ends in combat: a fight that outlasts the first
    -- check's delay leaves that check to the end of the fight.
    if trigger == "calm" and not started and arrivedAt then
        local now = ns.Number(time())
        if now and now - arrivedAt >= FIRST_DELAY then
            started = true
        end
    end
    if not started then
        return
    end
    if trigger == "combat" then
        fighting = true
        active = ns.Number(time())
        resume()
    elseif trigger == "calm" then
        fighting = false
        active = ns.Number(time())
        if waiting then
            waiting = false
            check("enter")
        end
        if mine then
            ns.Wake(KEY, idleMinutes * 60, IDLE_EVENT)
        end
    elseif trigger == "enter" then
        if fighting or inCombatNow() then
            waiting = true
        else
            waiting = false
            check("enter")
        end
    elseif trigger == "idle" then
        local now = ns.Number(time())
        if not fighting and now and active and now - active >= idleMinutes * 60 then
            check("idle")
        end
    end
end

-- The section's record: what the switch did, never what the log holds.
local function record()
    if dormant then
        return ns.Absent(dormant)
    end
    return {
        idle_minutes = ns.Number(idleMinutes),
        switches = ns.Number(switches),
        last_at = ns.Number(lastAt),
        last_to = ns.String(lastTo),
        last_why = ns.String(lastWhy),
        mine = ns.Bool(mine),
        log_on = ns.Bool(logOn),
        hands_off = ns.Bool(handsOff),
        advanced = ns.Bool(advanced),
    }
end

ns.Section({
    key = KEY,
    path = { KEY },
    events = {
        "PLAYER_ENTERING_WORLD",
        "ZONE_CHANGED_NEW_AREA",
        "GROUP_ROSTER_UPDATE",
        "PLAYER_REGEN_DISABLED",
        "PLAYER_REGEN_ENABLED",
    },
    immediate = true,
    -- What the last session kept, rebuilt from checked values; the first
    -- gather reads it back (readBack).
    carry = function(saved)
        local r = type(saved) == "table" and saved.combatlog or nil
        if type(r) ~= "table" or type(r.absent) ~= "nil" then
            return nil
        end
        local out = {}
        if type(r.switches) == "number" and r.switches >= 0 then
            out.switches = r.switches
        end
        if type(r.last_at) == "number" then
            out.last_at = r.last_at
        end
        if r.last_to == "on" then
            out.last_to = "on"
        elseif r.last_to == "off" then
            out.last_to = "off"
        end
        if type(r.last_why) == "string" and WHY_KNOWN[r.last_why] then
            out.last_why = r.last_why
        end
        if type(r.idle_minutes) == "number" and r.idle_minutes >= IDLE_LEAST and r.idle_minutes <= IDLE_MOST then
            out.idle_minutes = r.idle_minutes
        end
        if r.mine == true then
            out.mine = true
        end
        return out
    end,
    gather = function(event)
        if dormant then
            return record()
        end
        readBack()
        if type(LoggingCombat) ~= "function" then
            dormant = "LoggingCombat missing: the combat log is not switched"
            sayOnce("missing", "this game gives addons no combat-log switch, so WowLab does not turn the combat log on or off.")
            return record()
        end
        local trigger = TRIGGERS[event]
        if trigger then
            run(trigger)
        end
        return record()
    end,
})

-- `/wowlab combatlog` with no word: the switch's state, in one line, from
-- its record. `off`: the section is switched off.
function ns.CombatLogStatus(off)
    if off then
        ns.Say("the combat-log switch is off; /wowlab combatlog on  turns it on from the next /reload or login.")
        return
    end
    local held = ns.state[KEY]
    if type(held) ~= "table" then
        ns.Say("the combat-log switch is on; it has not run yet this session.")
        return
    end
    if type(held.absent) == "string" then
        ns.Say("the combat-log switch is on but does nothing this session: " .. held.absent)
        return
    end
    local minutes = type(held.idle_minutes) == "number" and held.idle_minutes or IDLE_MINUTES
    local count = type(held.switches) == "number" and held.switches or 0
    local text = "the combat-log switch is on: on in the open world, off in instances, in raid groups and after "
        .. minutes
        .. " min with no combat; "
        .. count
        .. " switches"
    if type(held.last_to) == "string" and type(held.last_why) == "string" then
        text = text .. ", the last " .. held.last_to .. " (" .. held.last_why .. ")"
    end
    ns.Say(text .. ".")
end

-- `/wowlab combatlog idle <minutes>`: the idle time, IDLE_LEAST to IDLE_MOST
-- whole minutes, kept in the record (saved at the next /reload or logout).
-- The typed text is read as a number only.
function ns.CombatLogIdle(text)
    local minutes = tonumber(text)
    if type(minutes) ~= "number" or minutes ~= math.floor(minutes) or minutes < IDLE_LEAST or minutes > IDLE_MOST then
        ns.Say("/wowlab combatlog idle <minutes>, " .. IDLE_LEAST .. " to " .. IDLE_MOST .. ".")
        return
    end
    local held = ns.state[KEY]
    if type(held) ~= "table" or type(held.absent) ~= "nil" then
        ns.Say("the combat-log switch is off or not running this session, so the idle time cannot be set now.")
        return
    end
    held.idle_minutes = ns.Number(minutes)
    ns.Say("idle time " .. minutes .. " min; /reload or log out to save that.")
end
