-- WowLab: the crew book (docs/LAB_PLAN.md §20.1, ADR-0036, ADR-0026).
--
-- Runs in the game client only; the Lab never loads or runs this file (L3).
-- It remembers the players the owner groups with, in the account-wide saved
-- variable WowLabCrewDB (its own schema, 3; the per-character schema does not
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
-- the next pass. Since M20-11 (owner decision, 2026-11-20, ADR-0036's second
-- note of that date) a member's GUID, race, faction and honor level are kept
-- too, each through the same test.
--
-- Schema 2 (§21.2): the larger list, the near-full line, the migration of a
-- schema 1 book that is on, the scoreboard probe (`probe.pvp`), and, from
-- M20-11, the matches: once a battleground or arena reports a winner and the
-- player is out of combat, the scoreboard is read once (recordMatch), each
-- row copied as plain values (scoreRows), and one `matches` entry written,
-- after the `matched` marker, so a repeated event or a /reload counts it once.
-- Every scoreboard call is made through pcall, after a test that it exists.
--
-- Schema 3 (M21-12): the book's region, once (`region`, the text
-- GetCurrentRegionName() gives), and each kept player's name as the game
-- gives it for their GUID (`resolved_name`, `resolved_realm`,
-- `resolved_eighth`, `resolved_at`, from GetPlayerInfoByGUID). On Forever the
-- name is "<First> <Second>"; the realm was empty for the player checked on
-- 2026-11-23 (retail leaves it empty for your own realm, [verify]); the
-- eighth value was 22 for a level-22 character, so it is probably the level
-- ([verify]). Both are read at the end of a roster pass and of the
-- scoreboard pass, out of combat only (resolveNames, which says the order and
-- the limits). A GUID the client does not name now gives nothing: nothing is
-- stored but the time of the ask (`resolve_tried_at`), which sends it to the
-- back of the queue.
--
-- API status: every client API used is listed in lab/addon/README.md; all of
-- them are [verify] for this file until the owner's in-game checks (M19-07,
-- and M20-06 for the scoreboard).

local _, ns = ...

-- The book's own version number (§20.1, §21.2; 3 since M21-12). A book of
-- schema 1 or 2 that is on is migrated (crewMigrate); a book of any other
-- schema (a newer WowLab, a restored snapshot) is left wholly alone: crewOn,
-- crewLoaded and the slash handler's first lines test it.
local CREW_SCHEMA = 3
-- The drop order (§20.1 "Limits", §21.2): at most MAX_PLAYERS players. When
-- the book is full, the unrated, un-noted player with the oldest `last` is
-- dropped first (makeRoom); a rated or noted player is never dropped, and
-- when every kept player is rated or noted, new members and new ratings are
-- refused and the owner is told once per session.
local MAX_PLAYERS = 20000
-- At most MAX_MATCHES matches (§21.2), the oldest dropped first (recordMatch).
local MAX_MATCHES = 500
-- At most MAX_ROWS scoreboard rows are read (40 a side is the largest
-- battleground).
local MAX_ROWS = 80
-- The near-full line (§21.1): when either list holds NEAR_FULL_PERCENT of
-- its limit, at most once in NEAR_FULL_SECONDS (seven days), by `warned`.
local NEAR_FULL_PERCENT = 90
local NEAR_FULL_SECONDS = 604800
-- The note rule (§20.1 "Limits"): control characters and | are removed byte
-- by byte (multi-byte text is left alone), spaces are trimmed, and what is
-- left may be at most NOTE_MAX_BYTES bytes. A longer note is refused, never
-- cut (cleanNote, noteFits). 2,000 since M20-14 (owner decision, 2026-11-20;
-- it was 200).
local NOTE_MAX_BYTES = 2000
-- The tooltip line (M20-14) shows at most TIP_NOTE_CHARS characters of a note,
-- cut before a character's first byte (noteStart), never inside one. Only the
-- line is cut; the stored note is not.
local TIP_NOTE_CHARS = 60
-- The debounce the sections use, and the first pass's wait (Core.lua).
local DEBOUNCE = 2
local FIRST_PASS_DELAY = 15
-- The unit tokens walked: raid1 to raid40 in a raid, party1 to party4 in a party.
local MAX_RAID = 40
local MAX_PARTY = 4
-- M21-12: at most MAX_RESOLVES GUIDs met before this session and never named
-- are asked in one pass, and at most MAX_RESOLVES that gave no name in an
-- earlier session in one login or /reload (resolveNames), so a full book is
-- asked a little at a time. Players met this session are not limited.
local MAX_RESOLVES = 100
-- M21-12 fix round 1: a GUID that gave no name is asked once more in the
-- same session, at a pass at least RETRY_SECONDS later. The client has no
-- event for a late answer (the 71037 event list), so asking again is the
-- only way to collect one.
local RETRY_SECONDS = 5

-- The sentence of ADR-0036 term 2 (§20.5), printed when the book is turned on.
-- Changed for M19-10's domain review: battleground and arena teams count
-- (owner decision, 2026-11-19), and the backup "probably" holds it ([verify]).
-- Changed for M20-11: both teams, and what the game shows of each player.
local TERMS = "The crew book keeps, for each player in your party or raid and on either team in a "
    .. "battleground or arena, what the game shows of them: name, realm, guild, class, race, faction, spec, "
    .. "level, role, honor level and the game's ID for the character, when you first and last grouped, "
    .. "how many times and in which instance, how many matches with and against you and how they ended, "
    .. "their scoreboard line from the latest match, the teams of each match, and your own rating and note. "
    .. "It keeps them in WowLab.lua (and probably the game's backup of it, WowLab.lua.bak) in this game account's "
    .. "SavedVariables folder, in wowlab's snapshots of it, and in wowlab's own crew database on this computer, "
    .. "which keeps them after the game's list drops them. Nothing is sent anywhere. "
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
local tipHooked = false -- the tooltip hook was tried (once per session)
local tipAbsent = nil -- why no tooltip line can be shown
-- The keys of the match being written (recordMatch): makeRoom never drops one.
local roomKeep = nil
-- The instance of the current pass (readWhere): open world true or false,
-- or nil when it could not be read; the name and id when in an instance.
local passOpenWorld = nil
local passWhere = nil
local passWhereId = nil
-- The scoreboard probe (§21.2): inside a battleground or arena since the last
-- pass outside one; the second snapshot taken; a score request made; and the
-- two candidate events counted since entry.
local pvpEntered = false
local pvpWinnerRead = false
local pvpRequested = false
local pvpReasked = false -- the data asked for once more after the winner (recordMatch)
local pvpScoreEvents = 0
local pvpCompleteEvents = 0
-- M21-12: the GUIDs asked for a name in this session (to the time() of the
-- first ask, 0 without a clock), those asked the second time, those met this
-- session (a group member or a scoreboard row), how many asks in all, and how
-- many of players that gave no name in an earlier session.
local resolveTried = {}
local resolveRetried = {}
local resolveSeen = {}
local resolveAsked = 0
local backlogAsked = 0

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

local function say(text)
    print("|cff33ff99WowLab|r: " .. text)
end

-- The migration (§21.2), pinned whole: a schema 1 book gains an empty
-- `matches` (schema 2's one change), and a schema 1 or 2 book the new number
-- (M21-12: schema 3 adds only fields that are left out until read), and
-- nothing else. Called only for a book that is on (crewLoaded) or is being
-- turned on (the slash handler's head). A book that is off keeps its schema;
-- any other schema is left alone.
local function crewMigrate()
    if type(WowLabCrewDB) ~= "table" then
        return
    end
    if WowLabCrewDB.schema ~= 1 and WowLabCrewDB.schema ~= 2 then
        return
    end
    if WowLabCrewDB.schema == 1 then
        WowLabCrewDB.matches = {}
    end
    WowLabCrewDB.schema = CREW_SCHEMA
end

-- The probe's helpers (§21.2), pinned whole. True only in an instance whose
-- type is `pvp` (a battleground) or `arena`.
local function pvpInstance()
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

-- True once the client reports a winner for the match.
local function pvpWon()
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

-- One probe run: whether each candidate call exists; the first row's field
-- names and their type names (the table form) or the type at each position
-- (the several-values form); the row count and whether a request was made;
-- the two event counts; the first row's side, the player's own and the
-- winner as whole numbers; and three yes-or-no answers about the first
-- row's name. No name, GUID or other value is kept: each value of the row is
-- handed to `type` only, and its GUID field is never indexed.
local function pvpSnapshot()
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
            -- M21-11: only a row the wrapper found. At 71037 the 12.0.1
            -- deprecation wrapper answers for a row C_PvP.GetScoreInfo does
            -- not have, with a nil name and side 0, so the sixth value is a
            -- side only when the first is not nil (type only, never compared).
            if not read and type(packed[1]) ~= "nil" then
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

-- A field as it is printed: the bidirectional embeddings and isolates
-- (U+202A to U+202E, U+2066 to U+2069, as UTF-8 bytes) and line breaks
-- removed, and | doubled, at the moment it is printed, whatever was done when
-- it was stored (a record can also come from a desk edit, a restored snapshot
-- or a merge). So a stored note cannot reorder or split the line.
local function shown(text)
    if type(text) ~= "string" then
        return ""
    end
    local embeds = string.gsub(text, "\226\128[\170-\174]", "")
    local isolates = string.gsub(embeds, "\226\129[\166-\169]", "")
    local flat = string.gsub(isolates, "[\r\n]", "")
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
    if not crewOn() then
        return
    end
    if crewBlocked() then
        return
    end
    WowLabCrewDB.skipped = (ns.Number(WowLabCrewDB.skipped) or 0) + 1
end

-- M21-11: a read of a member the game named with no second part (no second
-- name on Forever, no realm elsewhere) while no home realm was found is
-- skipped, and counted here, one per read (each roster pass), never reset
-- while the book lasts, so the owner's check can see whether it happens
-- [verify].
local function addNoSecond()
    if not crewOn() then
        return
    end
    if crewBlocked() then
        return
    end
    WowLabCrewDB.no_second = (ns.Number(WowLabCrewDB.no_second) or 0) + 1
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

-- The owner's home realm, the realm part of a same-realm player's key
-- (M20-17), or nil; and true when the source gave a value the client calls
-- secret. GetNormalizedRealmName() only, kept with every space and hyphen
-- removed, the way the client is believed to normalize a realm name
-- ([verify], not yet confirmed). M21-11: the second return of
-- UnitFullName("player") is no longer taken when that gives none. On a
-- client whose realm calls give no realm (Forever: the owner's runs on
-- 71037, 2026-11-20) that slot holds the character's second name, not a
-- realm (UnitName("target") gave "Tay", "Soulrend" on 2026-11-23), and it
-- would have keyed another player with the owner's second name; [verify]
-- with /dump UnitFullName("player") and /dump GetNormalizedRealmName().
-- Read when a key needs it; kept for the session once found, never while
-- the source gave none.
local homeFound = nil
local function homeRealm()
    if not crewOn() then
        return
    end
    if crewBlocked() then
        return
    end
    if homeFound ~= nil then
        return homeFound, false
    end
    local hidden = false
    if type(GetNormalizedRealmName) == "function" then
        local normalized = GetNormalizedRealmName()
        if secret(normalized) then
            hidden = true
        end
        if not secret(normalized) and type(normalized) == "string" then
            local bare = string.gsub(normalized, "[%s%-]", "")
            if bare ~= "" then
                homeFound = bare
                return homeFound, false
            end
        end
    end
    return nil, hidden
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
    -- M21-11: said only when a member was skipped that way (addNoSecond).
    local noSecond = ns.Number(WowLabCrewDB.no_second) or 0
    if noSecond > 0 then
        missing = missing
            .. " "
            .. noSecond
            .. " reads of a group member came back with no second name or realm and were skipped,"
            .. " since the book was started (not yet confirmed)."
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
    if type(GetPlayerInfoByGUID) ~= "function" then
        missing = missing .. " No names by character id: the client has no GetPlayerInfoByGUID function."
    end
    if type(GetCurrentRegionName) ~= "function" then
        missing = missing .. " No region: the client has no GetCurrentRegionName function."
    end
    if type(issecretvalue) ~= "function" then
        missing = missing .. " Values are not tested for secrets: the client has no issecretvalue function."
    end
    if tipAbsent ~= nil then
        missing = missing .. tipAbsent
    end
    if missing == "" then
        return nil
    end
    return missing
end

-- The drop order (§20.1 "Limits"). Returns true when a new player may be
-- added: the book holds fewer than MAX_PLAYERS, or the unrated, un-noted
-- player with the oldest `last` (or, for a player met only on a scoreboard,
-- `pvp_last`) was just dropped. A rated or noted player is never dropped, nor
-- one in the match being written (roomKeep); when there is no one to drop it
-- returns false and says so once per session. Each call walks the book once.
-- Proven only by reading this code: no in-game check can fill 20,000 entries.
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
        local held = type(roomKeep) == "table" and roomKeep[key] == true
        if type(entry) == "table" and entry.rating == nil and entry.note == nil and not held then
            local last = ns.Number(entry.last) or ns.Number(entry.pvp_last) or 0
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

-- The kept entry for `key`, added with no group counted when there is room;
-- nil when the book is full of rated and noted players.
local function admit(key)
    if not crewOn() then
        return
    end
    if crewBlocked() then
        return
    end
    local players = WowLabCrewDB.players
    if players[key] == nil and makeRoom() then
        players[key] = { met = 0 }
    end
    return players[key]
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
        text = text .. " (last seen in <" .. shown(entry.guild) .. ">)"
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

-- The start of a note for the tooltip line: at most TIP_NOTE_CHARS
-- characters, cut before the first byte of the next character (a byte below
-- 128 or from 192 up starts one), so no character is split. Only the line is
-- cut; the stored note is never shortened.
local function noteStart(note)
    if type(note) ~= "string" then
        return ""
    end
    local size = #note
    local cut = size
    local chars = 0
    for i = 1, size do
        local byte = string.byte(note, i)
        if byte < 128 or byte >= 192 then
            chars = chars + 1
            if chars > TIP_NOTE_CHARS then
                cut = i - 1
                break
            end
        end
    end
    if cut < size then
        return string.sub(note, 1, cut) .. "..."
    end
    return string.sub(note, 1, size)
end

-- The tooltip line for one kept player (M20-14): the rating, the matches with
-- and against you, and the start of the note, in the warning's words; nil
-- when the book holds none of the three. One lookup, no walk of the book.
local function tipText(key)
    if not crewOn() then
        return
    end
    if crewBlocked() then
        return
    end
    local entry = WowLabCrewDB.players[key]
    if type(entry) ~= "table" then
        return nil
    end
    local text = ""
    if entry.rating == "good" then
        text = text .. " You rated good."
    elseif entry.rating == "bad" then
        text = text .. " You rated bad."
    end
    local allied = ns.Number(entry.allied) or 0
    local faced = ns.Number(entry.faced) or 0
    if allied > 0 or faced > 0 then
        -- The owner's wording (2026-11-21).
        text = text .. " Battlegrounds: " .. allied .. " with you, " .. faced .. " against."
    end
    if type(entry.note) == "string" and entry.note ~= "" then
        text = text .. " Your note: " .. shown(noteStart(entry.note))
    end
    if text == "" then
        return nil
    end
    return "Crew book:" .. text
end

-- The key of the player under the mouse, built as the roster pass builds a
-- member's (name-realm, a missing realm being the owner's own); nil for
-- anything else, and when the name or realm is a secret value.
local function hoverKey()
    if not crewOn() then
        return
    end
    if crewBlocked() then
        return
    end
    if type(UnitIsPlayer) ~= "function" or type(UnitName) ~= "function" then
        return nil
    end
    local isPlayer = UnitIsPlayer("mouseover")
    local name, realm = UnitName("mouseover")
    if secret(isPlayer) or secret(name) or secret(realm) then
        return nil
    end
    if
        not secret(isPlayer)
        and isPlayer
        and not secret(name)
        and not secret(realm)
        and type(name) == "string"
        and name ~= ""
        and name ~= UNKNOWNOBJECT
    then
        if type(realm) == "string" and realm ~= "" then
            local key = name .. "-" .. realm
            return key
        else
            local home = homeRealm()
            if type(home) == "string" then
                local key = name .. "-" .. home
                return key
            end
        end
    end
    return nil
end

-- Adds the line to the game's tooltip when the book holds the player.
local function tipShow()
    if not crewOn() then
        return
    end
    if crewBlocked() then
        return
    end
    local key = hoverKey()
    if type(key) ~= "string" then
        return
    end
    local text = tipText(key)
    if type(text) ~= "string" then
        return
    end
    GameTooltip:AddLine(text, 1, 0.82, 0, true)
end

-- The tooltip hook (M20-14). Installed only while the book is on (hookTooltip);
-- once installed it stays for the session, so it starts with the on and combat
-- tests: with the book off or in combat it does nothing. Only the game's own
-- tooltip gets the line, and the work runs inside pcall, so an error cannot
-- break the tooltip. [verify] on the Forever client: the hook call, that the
-- tooltip's unit is "mouseover", that adding a line taints nothing, and
-- whether a name is a secret value in an instance.
local function onTooltip(tooltip)
    if not crewOn() then
        return
    end
    if crewBlocked() then
        return
    end
    if tooltip ~= GameTooltip then
        return
    end
    pcall(tipShow)
end

-- Installs the tooltip hook, once per session, only while the book is on and
-- out of combat, the modern way (TooltipDataProcessor), tested for first. A
-- client without it gets no tooltip line, and `/wowlab crew` says why.
local function hookTooltip()
    if not crewOn() then
        return
    end
    if crewBlocked() then
        return
    end
    if tipHooked then
        return
    end
    tipHooked = true
    if type(TooltipDataProcessor) ~= "table" or type(TooltipDataProcessor.AddTooltipPostCall) ~= "function" then
        tipAbsent = " No tooltip line: the client has no TooltipDataProcessor.AddTooltipPostCall function."
        return
    end
    if type(Enum) ~= "table" or type(Enum.TooltipDataType) ~= "table" then
        tipAbsent = " No tooltip line: the client has no Enum.TooltipDataType table."
        return
    end
    local unitType = Enum.TooltipDataType.Unit
    if secret(unitType) then
        tipAbsent = " No tooltip line: the client did not say which tooltips show a unit."
        return
    end
    if not secret(unitType) and type(unitType) ~= "number" then
        tipAbsent = " No tooltip line: the client did not say which tooltips show a unit."
        return
    end
    if not pcall(TooltipDataProcessor.AddTooltipPostCall, unitType, onTooltip) then
        tipAbsent = " No tooltip line: the client refused the tooltip hook."
    end
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
            entry = admit(key)
            counted = current[key] == true
            if entry ~= nil then
                current[key] = true
            end
            label = key
        else
            -- A nil or empty realm is the owner's own realm (homeRealm).
            local home, hidden = homeRealm()
            if home == nil and hidden then
                addSkipped()
                return
            end
            -- M21-11: with no home realm the member is skipped, and counted.
            if home == nil then
                addNoSecond()
            end
            if type(home) == "string" then
                local key = name .. "-" .. home
                entry = admit(key)
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
    -- M20-11: the GUID, race (the client's localized name, as a scoreboard
    -- row gives it), faction (the English name) and honor level.
    if type(UnitGUID) == "function" then
        local guid = UnitGUID(unit)
        if secret(guid) then
            addSkipped()
        end
        if not secret(guid) and type(guid) == "string" and guid ~= "" then
            entry.guid = ns.String(guid)
            -- M21-12: met now, so the client knows them: asked first.
            resolveSeen[guid] = true
        end
    end
    if type(UnitRace) == "function" then
        local race = UnitRace(unit)
        if secret(race) then
            addSkipped()
        end
        if not secret(race) and type(race) == "string" and race ~= "" then
            entry.race = ns.String(race)
        end
    end
    if type(UnitFactionGroup) == "function" then
        local faction = UnitFactionGroup(unit)
        if secret(faction) then
            addSkipped()
        end
        if not secret(faction) and type(faction) == "string" and faction ~= "" then
            entry.faction = ns.String(faction)
        end
    end
    if type(UnitHonorLevel) == "function" then
        local honor = UnitHonorLevel(unit)
        if secret(honor) then
            addSkipped()
        end
        if not secret(honor) and type(honor) == "number" and honor > 0 then
            entry.honor_level = ns.Number(honor)
        end
    end
end

-- M21-12: the book's region, once per book: the text GetCurrentRegionName()
-- gives ("US" on the owner's launch client, 2026-11-23), kept as given, with
-- the time() it was recorded (`region_at`). A secret or empty answer stores
-- nothing and is asked again at the next pass.
local function readRegion()
    if not crewOn() then
        return
    end
    if crewBlocked() then
        return
    end
    if WowLabCrewDB.region ~= nil then
        return
    end
    if type(GetCurrentRegionName) ~= "function" then
        return
    end
    local region = GetCurrentRegionName()
    if secret(region) then
        addSkipped()
    end
    if not secret(region) and type(region) == "string" and region ~= "" then
        WowLabCrewDB.region = ns.String(region)
        -- Fix round 1: when it was recorded, so the desk can date it.
        if type(time) == "function" then
            WowLabCrewDB.region_at = ns.Number(time())
        end
    end
end

-- M21-12: the names the game gives for the GUIDs of kept players that have
-- none yet, out of combat (the gate). GetPlayerInfoByGUID's sixth value is
-- the name ("<First> <Second>" on Forever), the seventh the realm (empty for
-- the player checked on 2026-11-23; retail leaves it empty for your own
-- realm, [verify]), the eighth probably the level ([verify]; 22 for a
-- level-22 character). The client is believed to name only a player it has
-- met lately, so the order is (fix round 1): players met this session
-- (resolveSeen: a group member, a scoreboard row), not limited; then players
-- never asked; then one more ask, at least RETRY_SECONDS after the first, of
-- a GUID that gave nothing this session; then players that gave nothing in an
-- earlier session, the oldest ask first. All but the first kind share
-- MAX_RESOLVES per pass, and the last kind also MAX_RESOLVES per login or
-- /reload. A GUID that gives no name keeps the time of the ask
-- (`resolve_tried_at`), so it goes to the back of the queue; a name clears it.
local function resolveNames()
    if not crewOn() then
        return
    end
    if crewBlocked() then
        return
    end
    if type(GetPlayerInfoByGUID) ~= "function" then
        return
    end
    local now = nil
    if type(time) == "function" then
        now = ns.Number(time())
    end
    local seen, fresh, again, older = {}, {}, {}, {}
    local olderRoom = MAX_RESOLVES - backlogAsked
    for _, entry in pairs(WowLabCrewDB.players) do
        local guid = nil
        if type(entry) == "table" and entry.resolved_name == nil then
            guid = ns.String(entry.guid)
        end
        if guid ~= nil and string.find(guid, "^Player%-") ~= nil then
            local asked = resolveTried[guid]
            local due = asked == nil
            if asked ~= nil and resolveRetried[guid] == nil and now ~= nil and now - asked >= RETRY_SECONDS then
                due = true
            end
            local tried = ns.Number(entry.resolve_tried_at)
            if due and resolveSeen[guid] == true then
                seen[#seen + 1] = entry
            elseif due and asked ~= nil then
                again[#again + 1] = entry
            elseif due and tried == nil then
                fresh[#fresh + 1] = entry
            elseif due and olderRoom > 0 then
                -- Kept oldest first, at most olderRoom long.
                local last = older[#older]
                if #older < olderRoom or tried < (ns.Number(last.resolve_tried_at) or 0) then
                    if #older >= olderRoom then
                        older[#older] = nil
                    end
                    local at = #older + 1
                    while at > 1 and (ns.Number(older[at - 1].resolve_tried_at) or 0) > tried do
                        older[at] = older[at - 1]
                        at = at - 1
                    end
                    older[at] = entry
                end
            end
        end
    end
    -- The queue in that order; kinds: 1 met this session, 2 never asked or
    -- asked again, 3 from an earlier session.
    local queue, kinds = {}, {}
    for i = 1, #seen do
        queue[#queue + 1] = seen[i]
        kinds[#queue] = 1
    end
    for i = 1, #fresh do
        queue[#queue + 1] = fresh[i]
        kinds[#queue] = 2
    end
    for i = 1, #again do
        queue[#queue + 1] = again[i]
        kinds[#queue] = 2
    end
    for i = 1, #older do
        queue[#queue + 1] = older[i]
        kinds[#queue] = 3
    end
    local passRoom = MAX_RESOLVES
    for i = 1, #queue do
        local entry = queue[i]
        local kind = kinds[i]
        local guid = ns.String(entry.guid)
        if guid ~= nil and (kind == 1 or passRoom > 0) then
            if kind ~= 1 then
                passRoom = passRoom - 1
            end
            if kind == 3 then
                backlogAsked = backlogAsked + 1
            end
            if resolveTried[guid] == nil then
                resolveTried[guid] = now or 0
            else
                resolveRetried[guid] = true
            end
            resolveAsked = resolveAsked + 1
            local _, _, _, _, _, name, realm, eighth = GetPlayerInfoByGUID(guid)
            if secret(name) or secret(realm) or secret(eighth) then
                addSkipped()
            end
            entry.resolve_tried_at = now
            if not secret(name) and type(name) == "string" and name ~= "" and name ~= UNKNOWNOBJECT then
                entry.resolved_name = ns.String(name)
                entry.resolved_at = now
                entry.resolve_tried_at = nil
                if not secret(realm) and type(realm) == "string" then
                    entry.resolved_realm = ns.String(realm)
                end
                if not secret(eighth) and type(eighth) == "number" then
                    entry.resolved_eighth = ns.Number(eighth)
                end
            end
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
    if not firstDone then
        return
    end
    -- The login line is said even when nothing can be recorded, with why.
    local missing = featureMissing()
    local intact = pcall(bookIntact)
    if missing ~= nil or not intact then
        if not greeted then
            greeted = true
            if not intact then
                say("Crew book on, but it cannot be read, so nothing is recorded. /wowlab crew off to stop.")
            else
                say("Crew book on, but nothing is recorded: " .. missing .. ". /wowlab crew off to stop.")
            end
        end
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
        say("Crew book on: " .. plural(ns.Number(countPlayers()) or 0, "player", "players") .. ". /wowlab crew off to stop.")
    end
    -- M21-12: the region once, then the names the game gives by GUID.
    pcall(readRegion)
    pcall(resolveNames)
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

-- The scoreboard as the client gives it now (§21.2, as built by M20-11): one
-- plain copy per row, at most MAX_ROWS, of each field whose name is a string
-- and whose value is a number, a string or true/false and not a secret value.
-- A table in a row (the client's per-battleground `stats` is believed to be
-- one) is not copied. Empty when the newer score call is missing or there are
-- no rows yet. [verify] every field name, in M20-06.
local function scoreRows()
    if not crewOn() then
        return
    end
    if crewBlocked() then
        return
    end
    local rows = {}
    if type(C_PvP) ~= "table" or type(C_PvP.GetScoreInfo) ~= "function" then
        return rows
    end
    if type(GetNumBattlefieldScores) ~= "function" then
        return rows
    end
    local okCount, counted = pcall(GetNumBattlefieldScores)
    local count = 0
    if okCount and not secret(counted) and type(counted) == "number" then
        count = ns.Number(counted) or 0
    end
    if count > MAX_ROWS then
        count = MAX_ROWS
    end
    for i = 1, count do
        local okRow, row = pcall(C_PvP.GetScoreInfo, i)
        if okRow and not secret(row) and type(row) == "table" then
            local copy = {}
            for field, value in pairs(row) do
                if not secret(field) and not secret(value) and type(field) == "string" then
                    if type(value) == "number" then
                        copy[field] = ns.Number(value)
                    elseif type(value) == "string" then
                        copy[field] = ns.String(value)
                    elseif type(value) == "boolean" then
                        copy[field] = ns.Bool(value)
                    end
                end
            end
            rows[#rows + 1] = copy
        end
    end
    return rows
end

-- One finished match (§21.2, as built by M20-11): read once a winner is
-- reported and the player is out of combat. Everything that can fail is read
-- and built first; then, with nothing fallible between, the `matched` marker
-- (the map's id and the time) is written, then the players and counters, then
-- the entry. So a repeated score event or a /reload in the same instance
-- counts the match once, and an error cannot leave it half counted. A pass
-- outside a battleground or arena empties the marker (pvpPass). Nothing is
-- written while the scoreboard has no rows (the data is asked for once more,
-- and read at the next score event), or when the player's own side cannot be
-- found (the own row by GUID, else GetBattlefieldArenaFaction). The player's
-- own row is left out by GUID and by key. A row with no side is in neither
-- list. Each row's player is added as a group member is (the drop order
-- applies, and no one in this match is dropped), with the row's plain values
-- as `score`, and the match lands in `matches`, the oldest dropped at
-- MAX_MATCHES. A solo shuffle (one arena, six rounds, sides changing each
-- round) is kept as `kind = "shuffle"`, everyone in `team`, no `won`, and no
-- counter raised. [verify] in M20-06: that a row's `name` holds "Name-Realm"
-- for another realm and the bare name for the player's own; that `faction` is
-- the team number (0 or 1) and GetBattlefieldWinner's value the winning
-- team's; that `guid`, `raceName`, `classToken`, `talentSpec` and
-- `honorLevel` are what their names say; that C_PvP.IsSoloShuffle exists.
local function recordMatch()
    if not crewOn() then
        return
    end
    if crewBlocked() then
        return
    end
    if type(GetInstanceInfo) ~= "function" or type(time) ~= "function" or type(UnitGUID) ~= "function" then
        return
    end
    local where, kind, _, _, _, _, _, whereId = GetInstanceInfo()
    if secret(where) or secret(kind) or secret(whereId) then
        addSkipped()
        return
    end
    local mapId = ns.Number(whereId)
    local marker = WowLabCrewDB.matched
    if type(marker) == "table" and marker.map_id == mapId then
        return
    end
    local mine = nil
    local own = UnitGUID("player")
    if not secret(own) and type(own) == "string" and own ~= "" then
        mine = own
    end
    local home = homeRealm()
    local ownKey = nil
    if type(UnitName) == "function" then
        local myName = UnitName("player")
        if not secret(myName) and type(myName) == "string" and myName ~= "" and home ~= nil then
            ownKey = myName .. "-" .. home
        end
    end
    local rows = scoreRows()
    if type(rows) ~= "table" or #rows == 0 then
        -- Asked for once more; the next score event reads again.
        if not pvpReasked and type(RequestBattlefieldScoreData) == "function" then
            pvpReasked = true
            pcall(RequestBattlefieldScoreData)
        end
        return
    end
    local shuffle = false
    if type(C_PvP) == "table" and type(C_PvP.IsSoloShuffle) == "function" then
        local okShuffle, isShuffle = pcall(C_PvP.IsSoloShuffle)
        if okShuffle and not secret(isShuffle) and isShuffle == true then
            shuffle = true
        end
    end
    -- Each row's key, built as a group member's is; the own row left out.
    local keys = {}
    local ownAt = nil
    -- M21-11: rows whose name has no second part while no home realm was
    -- found get no key; counted in the match record as `rows_unkeyed`, the
    -- player's own row (known by its GUID) left out of the count.
    local unkeyed = 0
    for i = 1, #rows do
        local score = rows[i]
        local name = score.name
        local key = nil
        local ownGuid = mine ~= nil and score.guid == mine
        if type(name) == "string" and name ~= "" and name ~= UNKNOWNOBJECT then
            if string.find(name, "-", 1, true) ~= nil then
                key = name
            elseif home ~= nil then
                key = name .. "-" .. home
            elseif not ownGuid then
                unkeyed = unkeyed + 1
            end
        end
        local isOwn = (ownKey ~= nil and key == ownKey) or ownGuid
        if isOwn then
            key = nil
            ownAt = i
        end
        keys[i] = ns.String(key)
        -- M21-12: every row's player is met now, so asked first.
        if type(score.guid) == "string" then
            resolveSeen[score.guid] = true
        end
    end
    local ownSide = nil
    if ownAt ~= nil then
        ownSide = ns.Number(rows[ownAt].faction)
    end
    if ownSide == nil and type(GetBattlefieldArenaFaction) == "function" then
        local okSide, side = pcall(GetBattlefieldArenaFaction)
        if okSide and not secret(side) and type(side) == "number" then
            ownSide = ns.Number(side)
        end
    end
    if ownSide == nil and not shuffle then
        return
    end
    local winner = nil
    if type(GetBattlefieldWinner) == "function" then
        local okWinner, won = pcall(GetBattlefieldWinner)
        if okWinner and not secret(won) and type(won) == "number" then
            winner = ns.Number(won)
        end
    end
    local now = ns.Number(time())
    local mapName = nil
    if type(where) == "string" then
        mapName = string.gsub(where, "|", "")
    end
    local matchKind = "bg"
    if shuffle then
        matchKind = "shuffle"
    elseif kind == "arena" then
        matchKind = ns.String(kind)
    end
    local result = nil
    if not shuffle and (winner == 0 or winner == 1) then
        result = winner == ownSide
    end
    -- Who is with you, against you, or (a shuffle) in it; a row with no
    -- side is in neither list.
    local team = {}
    local foes = {}
    local sides = {}
    local inMatch = {}
    for i = 1, #rows do
        local key = keys[i]
        local side = ns.Number(rows[i].faction)
        if key ~= nil and (shuffle or side ~= nil) then
            inMatch[key] = true
            if shuffle or side == ownSide then
                team[#team + 1] = ns.String(key)
                sides[i] = "with"
            else
                foes[#foes + 1] = ns.String(key)
                sides[i] = "against"
            end
        end
    end
    local free = MAX_PLAYERS - (ns.Number(countPlayers()) or 0)
    -- From here nothing reads the client: the marker first, then the
    -- players and their counters, then the entry.
    WowLabCrewDB.matched = { map_id = mapId, at = now }
    local players = WowLabCrewDB.players
    roomKeep = inMatch
    for i = 1, #rows do
        local key = keys[i]
        local score = rows[i]
        if sides[i] ~= nil and players[key] == nil and (free > 0 or makeRoom()) then
            players[key] = { met = 0 }
            free = free - 1
        end
        local entry = nil
        if sides[i] ~= nil then
            entry = players[key]
        end
        if type(entry) == "table" then
            if not shuffle and sides[i] == "with" then
                entry.allied = (ns.Number(entry.allied) or 0) + 1
                if result == true then
                    entry.allied_won = (ns.Number(entry.allied_won) or 0) + 1
                end
                entry.pvp_side = "with"
            elseif not shuffle then
                entry.faced = (ns.Number(entry.faced) or 0) + 1
                if result == true then
                    entry.faced_won = (ns.Number(entry.faced_won) or 0) + 1
                end
                entry.pvp_side = "against"
            end
            entry.pvp_last = now
            entry.pvp_map = ns.String(mapName)
            entry.first = ns.Number(entry.first) or now
            entry.guid = ns.String(score.guid) or ns.String(entry.guid)
            entry.race = ns.String(score.raceName) or ns.String(entry.race)
            entry.class = ns.String(score.classToken) or ns.String(entry.class)
            entry.spec = ns.String(score.talentSpec) or ns.String(entry.spec)
            entry.honor_level = ns.Number(score.honorLevel) or ns.Number(entry.honor_level)
            score.name = nil
            score.guid = nil
            score.at = now
            entry.score = score
        end
    end
    roomKeep = nil
    local record = { at = now, map = ns.String(mapName), map_id = mapId, kind = matchKind, team = team, foes = foes }
    if result ~= nil then
        record.won = result
    end
    if unkeyed > 0 then
        record.rows_unkeyed = unkeyed
    end
    if type(WowLabCrewDB.matches) ~= "table" then
        WowLabCrewDB.matches = {}
    end
    local list = WowLabCrewDB.matches
    while #list >= MAX_MATCHES do
        table.remove(list, 1)
    end
    list[#list + 1] = record
end

-- The scoreboard probe's schedule (§21.2): in a battleground or arena, one
-- snapshot after entry and a second once a winner is reported; again in the
-- next match (a pass outside the instance resets it). Each stores into
-- `probe.pvp` and nowhere else.
local function pvpPass()
    if not crewOn() then
        return
    end
    if crewBlocked() then
        return
    end
    if not pvpInstance() then
        pvpEntered = false
        pvpWinnerRead = false
        -- Outside a battleground or arena: the next match is a new one.
        if pvpInstance() == false then
            WowLabCrewDB.matched = nil
        end
        return
    end
    if type(WowLabCrewDB.probe) ~= "table" then
        return
    end
    if not pvpEntered then
        pvpWinnerRead = false
        pvpRequested = false
        pvpReasked = false
        pvpScoreEvents = 0
        pvpCompleteEvents = 0
        WowLabCrewDB.probe.pvp = { pvpSnapshot() }
        -- Set only once the store is made, so a snapshot that failed never
        -- lets this match's second one land beside another match's first.
        pvpEntered = true
        return
    end
    if not pvpWinnerRead and pvpWon() then
        pvpWinnerRead = true
        WowLabCrewDB.probe.pvp[2] = pvpSnapshot()
    end
    -- The match, once a winner is reported: tried at each pass until the
    -- scoreboard has rows, then held off by the `matched` marker.
    if pvpWon() then
        pcall(recordMatch)
        -- M21-12: the scoreboard's players, while the client knows them.
        pcall(resolveNames)
    end
end

local function onScore()
    if not crewOn() then
        return
    end
    if crewBlocked() then
        return
    end
    pvpScoreEvents = pvpScoreEvents + 1
    pcall(pvpPass)
end

-- Out of combat again: a pass, and the probe (a winner may have been
-- reported while the player was in combat).
local function onRegen()
    if not crewOn() then
        return
    end
    if crewBlocked() then
        return
    end
    pcall(pvpPass)
    hookTooltip()
    kick()
end

local function onComplete()
    if not crewOn() then
        return
    end
    if crewBlocked() then
        return
    end
    pvpCompleteEvents = pvpCompleteEvents + 1
    pcall(pvpPass)
end

-- The near-full line (§21.1): when either list holds 90% of its limit, once
-- in seven days at most, by `warned` (`time()` of the last such line). A
-- list at its limit stays there, so without the seven days it would print
-- at every login.
local function nearFull()
    if not crewOn() then
        return
    end
    if crewBlocked() then
        return
    end
    if type(time) ~= "function" then
        return
    end
    local now = ns.Number(time())
    local last = ns.Number(WowLabCrewDB.warned) or 0
    local held = 0
    for _ in pairs(WowLabCrewDB.players) do
        held = held + 1
    end
    local kept = 0
    if type(WowLabCrewDB.matches) == "table" then
        kept = #WowLabCrewDB.matches
    end
    if
        now ~= nil
        and now - last >= NEAR_FULL_SECONDS
        and (held * 100 >= MAX_PLAYERS * NEAR_FULL_PERCENT or kept * 100 >= MAX_MATCHES * NEAR_FULL_PERCENT)
    then
        WowLabCrewDB.warned = now
        say(
            "the crew book is nearly full (it keeps at most 20,000 players and 500 matches): "
                .. "open wowlab so nothing is lost."
        )
    end
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
        ns.On("UPDATE_BATTLEFIELD_SCORE", onScore)
        ns.On("PVP_MATCH_COMPLETE", onComplete)
    end
    pcall(nearFull)
    pcall(pvpPass)
    hookTooltip()
    kick()
end

-- At this addon's load: a book that is on is migrated from schema 1, then
-- `probe.loads` is raised in place (so `probe.pvp` survives a load) for a
-- book of this schema only. A book that is off is not touched beyond that.
local function crewLoaded(_, name)
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

-- Rating the target: when it is another player, its key, after adding it
-- to the book (with no group counted) if `create` and there is room; or false,
-- after saying why. Nothing (nil) while the book is off or in combat.
local function targetKey(create)
    if not crewOn() then
        return
    end
    if crewBlocked() then
        return
    end
    if type(UnitIsPlayer) ~= "function" or type(UnitIsUnit) ~= "function" or type(UnitName) ~= "function" then
        say("this client cannot say who your target is.")
        return false
    end
    local unit = "target"
    local isPlayer = UnitIsPlayer(unit)
    local me = UnitIsUnit(unit, "player")
    local name, realm = UnitName(unit)
    if secret(isPlayer) or secret(me) or secret(name) or secret(realm) then
        addSkipped()
        say("the client did not say who your target is.")
        return false
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
        if type(realm) == "string" and realm ~= "" then
            local key = name .. "-" .. realm
            if create then
                admit(key)
            end
            return key
        else
            local home = homeRealm()
            if type(home) == "string" then
                local key = name .. "-" .. home
                if create then
                    admit(key)
                end
                return key
            end
        end
    end
    say("target another player, or type their name-realm.")
    return false
end

-- `/wowlab crew who`: the warning line for every kept member of the group.
local function whoLines()
    if not crewOn() then
        return
    end
    if crewBlocked() then
        return
    end
    if not pcall(bookIntact) then
        say("the crew book cannot be read; nothing was shown.")
        return true
    end
    firstDone = true
    pcall(pass)
    if type(IsInGroup) == "function" then
        local inGroup = IsInGroup()
        if not secret(inGroup) and not inGroup then
            say("you are not in a group.")
            return true
        end
    end
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
-- compared, measured and, for a note, stored after cleanNote. A book of
-- another schema is left wholly alone (the pinned second test).
function ns.CrewCommand(word)
    if type(word) ~= "string" then
        return
    end
    if string.match(string.lower(word), "^%s*%S+%s+on%s*$") ~= nil then
        crewMigrate()
    end
    if type(WowLabCrewDB) == "table" and WowLabCrewDB.schema ~= CREW_SCHEMA then
        print("|cff33ff99WowLab|r: this crew book was written by another version of WowLab, so it has been left alone.")
        return
    end
    local rest = string.match(word, "^%s*%S+%s*(.-)%s*$") or ""
    local verb, tail = string.match(rest, "^(%S*)%s*(.-)$")
    verb = string.lower(verb or "")
    tail = tail or ""
    if verb == "on" then
        if type(WowLabCrewDB) ~= "table" then
            -- Without the client's time function the book starts with no date.
            local startedAt = nil
            if type(time) == "function" then
                startedAt = ns.Number(time())
            end
            WowLabCrewDB = {
                schema = CREW_SCHEMA,
                on = true,
                started = startedAt,
                probe = { loads = 1 },
                skipped = 0,
                players = {},
                current = {},
                matches = {},
            }
            say("a new, empty crew book was started.")
        elseif not pcall(bookIntact) then
            if WowLabCrewDB.on == true then
                say("the crew book cannot be read; nothing is recorded.")
            else
                -- An off schema 1 book was given the new version number by the
                -- first lines of this handler before this test; nothing else.
                say(
                    "this crew book cannot be read, so it stays off and nothing is recorded. "
                        .. "At most its version number was updated; the players in it were not touched."
                )
            end
            return
        elseif WowLabCrewDB.on ~= true then
            -- Nothing was counted while it was off: the group the player is
            -- in now is counted afresh.
            local current = WowLabCrewDB.current
            for key in pairs(WowLabCrewDB.current) do
                current[key] = nil
            end
            WowLabCrewDB.on = true
        end
        -- This notice stands for the login line in this session.
        greeted = true
        say("Crew book on. " .. TERMS)
        say(
            "Recording starts out of combat. The book keeps up to 20,000 players and says when it is nearly full. "
                .. "/wowlab crew off stops it and keeps what is stored. "
                .. KEPT
        )
    end
    if type(WowLabCrewDB) ~= "table" then
        say("the crew book is off and holds no one. /wowlab crew on starts it.")
        return
    end
    -- The roster events are registered once per session, only while the
    -- book is on: here, or at a loading screen out of combat (onWorld).
    local waiting = WowLabCrewDB.on == true and not armed and verb ~= "on"
    if WowLabCrewDB.on == true and not armed then
        armed = true
        if not ns.On("GROUP_ROSTER_UPDATE", onRoster) then
            rosterAbsent = "the client does not know the GROUP_ROSTER_UPDATE event, so no one is recorded"
        end
        ns.On("UNIT_NAME_UPDATE", onRoster)
        ns.On("PLAYER_REGEN_ENABLED", onRegen)
        ns.On("UPDATE_BATTLEFIELD_SCORE", onScore)
        ns.On("PVP_MATCH_COMPLETE", onComplete)
        onWorld()
    end
    if verb == "on" then
        return
    end
    if verb == "off" then
        WowLabCrewDB.on = false
        local ok, kept = pcall(countPlayers)
        if ok then
            -- The group counted so far is forgotten: a later group is counted
            -- afresh once the book is on again.
            local current = WowLabCrewDB.current
            for key in pairs(WowLabCrewDB.current) do
                current[key] = nil
            end
            say(
                "Crew book off: nothing more is recorded; the "
                    .. plural(ns.Number(kept) or 0, "player", "players")
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
            count = plural(ns.Number(kept) or 0, "player", "players")
        end
        local since = ", start date unknown"
        local started = ns.Number(WowLabCrewDB.started)
        if type(date) == "function" and printable(started) then
            since = ", started " .. date("%Y-%m-%d", started)
        end
        if WowLabCrewDB.on == true then
            say("Crew book on: " .. count .. since .. ".")
        else
            say("Crew book off: " .. count .. " kept" .. since .. ".")
        end
        if waiting then
            say(
                "it was waiting for the next loading screen or a /wowlab crew command: you were in combat "
                    .. "when the world loaded. It records from now on, out of combat."
            )
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
    -- good, bad and clear take nothing else: a word without "-" is refused,
    -- so a mistyped name never lands on the target.
    local first, after = string.match(tail, "^(%S*)%s*(.-)$")
    local typed = type(first) == "string" and string.find(first, "-", 1, true) ~= nil
    if not typed and verb ~= "note" and tail ~= "" then
        say(
            "type the player's name-realm (with the -), or target the player and type the command "
                .. "with nothing after it. Nothing was changed."
        )
        return
    end
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
                .. " bytes (about 666 characters of Chinese, Japanese or Korean). Nothing was changed."
        )
        return
    end
    if not pcall(bookIntact) then
        say("the crew book cannot be read. Nothing was changed.")
        return
    end
    local wanted = nil
    if typed then
        wanted = first
        -- A member of the group not yet in the book is added first.
        firstDone = true
        pcall(pass)
    else
        local create = verb == "good" or verb == "bad" or (verb == "note" and tidy ~= "")
        local ok, key = pcall(targetKey, create)
        if not ok then
            say("your target could not be read. Nothing was changed.")
            return
        end
        if key == false then
            return
        end
        if key == nil then
            say("nothing is read while you are in combat. Nothing was changed.")
            return
        end
        wanted = key
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
        elseif verb == "note" then
            say(
                shown(wanted)
                    .. " is not in the crew book or your group (or the book is full; a first word with a - "
                    .. "is read as a name). Nothing was changed."
            )
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
    local whom = shown(found)
    if not typed then
        whom = "your target, " .. shown(found)
    end
    if verb == "good" then
        entry.rating = "good"
        say(whom .. ": rated good. " .. KEPT)
    elseif verb == "bad" then
        entry.rating = "bad"
        say(whom .. ": rated bad. " .. KEPT)
    elseif verb == "clear" then
        entry.rating = nil
        say(whom .. ": rating removed. " .. KEPT)
    elseif tidy == "" then
        entry.note = nil
        say(whom .. ": note removed. " .. KEPT)
    else
        entry.note = ns.String(tidy)
        say(whom .. ": note kept. " .. KEPT)
    end
end
