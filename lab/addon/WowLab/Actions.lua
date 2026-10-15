-- Spellbook, action bars and key bindings (docs/LAB_PLAN.md §13.1 and §15.2;
-- schema 3, M13-05).
--
--   spellbook   every item of the player's spellbook, by skill line: slot,
--               item type, action and spell id, the name and subtext (a rank
--               where the client gives one) as the client returns them, and
--               the passive and off-spec flags;
--   actionbars  every action slot that holds something: its type, its id
--               when the id is a number, and its subtype. A macro slot keeps
--               the macro id only, never the macro's name or body (they are
--               text the owner typed, ADR-0026); an equipment-set slot,
--               whose id is the set's name, keeps no id at all;
--   bindings    every binding command that is a plain upper-case name, with
--               the keys GetBindingKey returns for it now, to cross-check
--               bindings-cache.wtf (§15.2). Commands that carry text after
--               the name (`SPELL x`, `MACRO x`, `ITEM x`, `CLICK x`) are
--               counted and never asked about or stored: a macro's name is
--               text the owner typed.
--
-- Out of combat only (ADR-0026, docs/GLOSSARY.md "secret value"): each
-- gather asks InCombatLockdown first and reads nothing in combat, or when it
-- cannot tell; the record then says so, and PLAYER_REGEN_ENABLED (combat
-- ended) gathers again. No value that becomes secret in combat is touched.
--
-- Every API here is [verify]. forever-addon-kit's API list for 69893 has the
-- names, which proves only that they exist, so each record carries `probes`:
-- per API, whether the function was there, how many calls were made, how
-- many raised an error, and how many answered (the way `spec.api` names the
-- API that answered). The README's "M13-05" notes say what each count means.

local _, ns = ...

local IN_COMBAT = "in combat: this section reads only out of combat, and gathers again when combat ends"

-- The reason nothing may be read now, or nil when the player is out of
-- combat. Unknown counts as in combat: nothing is read unless the client
-- said "not in combat".
local function combatReason()
    if type(InCombatLockdown) ~= "function" then
        return "InCombatLockdown missing, so whether the player is in combat is not known; nothing was read"
    end
    local ok, locked = pcall(InCombatLockdown)
    if not ok then
        return "InCombatLockdown raised an error, so whether the player is in combat is not known; nothing was read"
    end
    if locked then
        return IN_COMBAT
    end
    return nil
end

-- One API's probe: whether the function is there, and per call whether it
-- raised an error or answered (what "answered" means is the caller's: a
-- number, a table, a string or a filled slot, as the README lists).
local function probe(api, fn)
    return { api = ns.String(api), present = fn ~= nil, calls = 0, errors = 0, answered = 0 }
end

local function tally(entry, ok, answered)
    entry.calls = (ns.Number(entry.calls) or 0) + 1
    if not ok then
        entry.errors = (ns.Number(entry.errors) or 0) + 1
    elseif answered then
        entry.answered = (ns.Number(entry.answered) or 0) + 1
    end
end

-- Client text kept as text: 1 to 255 bytes with no control character and no
-- "|" (the client's escape character, which opens colour and link codes).
local function plainText(value)
    if type(value) == "string" and #value > 0 and #value <= 255 and not string.find(value, "[%c|]") then
        return value
    end
    return nil
end

-- A client word such as an action type ("spell", "macro"): letters only, at
-- most 32.
local function word(value)
    if type(value) == "string" and #value <= 32 and string.find(value, "^%a+$") then
        return value
    end
    return nil
end

-- Bounds on what one gather walks, so a client returning a bogus count
-- cannot make a gather run away.
local MAX_SKILL_LINES = 64
local MAX_SPELLBOOK_ITEMS = 4096
local MAX_BINDINGS = 4096

-- spellbook ---------------------------------------------------------------------

local function spellbookItem(probes, slot, line, bank, itemInfo, itemName)
    local ok, info = pcall(itemInfo, slot, bank)
    tally(probes.item, ok, type(info) == "table")
    local entry = { slot = ns.Number(slot), skill_line = ns.Number(line) }
    if ok and type(info) == "table" then
        entry.type = ns.Number(info.itemType)
        entry.action_id = ns.Number(info.actionID)
        entry.spell_id = ns.Number(info.spellID)
        entry.passive = ns.Bool(info.isPassive)
        entry.off_spec = ns.Bool(info.isOffSpec)
    end
    if itemName then
        local okText, spellName, subtext = pcall(itemName, slot, bank)
        tally(probes.text, okText, type(spellName) == "string")
        if okText then
            entry.spell_name = ns.String(plainText(spellName))
            entry.subtext = ns.String(plainText(subtext))
        end
    end
    return entry
end

ns.Section({
    key = "spellbook",
    path = { "spellbook" },
    events = { "SPELLS_CHANGED", "PLAYER_REGEN_ENABLED" },
    on_world = true,
    gather = function()
        local blocked = combatReason()
        if blocked then
            return ns.Absent(blocked)
        end
        local numLines = ns.Fn(C_SpellBook, "GetNumSpellBookSkillLines")
        local lineInfo = ns.Fn(C_SpellBook, "GetSpellBookSkillLineInfo")
        local itemInfo = ns.Fn(C_SpellBook, "GetSpellBookItemInfo")
        local itemName = ns.Fn(C_SpellBook, "GetSpellBookItemName")
        local probes = {
            lines = probe("C_SpellBook.GetNumSpellBookSkillLines", numLines),
            line = probe("C_SpellBook.GetSpellBookSkillLineInfo", lineInfo),
            item = probe("C_SpellBook.GetSpellBookItemInfo", itemInfo),
            text = probe("C_SpellBook.GetSpellBookItemName", itemName),
        }
        local list = { probes.lines, probes.line, probes.item, probes.text }
        local missing = (not numLines and "C_SpellBook.GetNumSpellBookSkillLines")
            or (not lineInfo and "C_SpellBook.GetSpellBookSkillLineInfo")
            or (not itemInfo and "C_SpellBook.GetSpellBookItemInfo")
        if missing then
            return { absent = ns.String(missing .. " missing"), probes = list }
        end
        -- The player's own spellbook, never a pet's: Enum.SpellBookSpellBank
        -- names the banks, and its Player value is read, not assumed.
        local banks = type(Enum) == "table" and Enum.SpellBookSpellBank or nil
        local bank = type(banks) == "table" and ns.Number(banks.Player) or nil
        if not bank then
            return { absent = "Enum.SpellBookSpellBank.Player missing", probes = list }
        end
        local okCount, count = pcall(numLines)
        tally(probes.lines, okCount, type(count) == "number")
        if not okCount or type(count) ~= "number" then
            return { absent = "C_SpellBook.GetNumSpellBookSkillLines gave no number", probes = list }
        end
        local record = { skill_lines = {}, list = {}, probes = list }
        for line = 1, math.min(count, MAX_SKILL_LINES) do
            local okLine, info = pcall(lineInfo, line)
            tally(probes.line, okLine, type(info) == "table")
            if okLine and type(info) == "table" then
                -- The skill line's name is never read.
                local offset, items = info.itemIndexOffset, info.numSpellBookItems
                record.skill_lines[#record.skill_lines + 1] = {
                    index = line,
                    offset = ns.Number(offset),
                    count = ns.Number(items),
                    guild = ns.Bool(info.isGuild),
                    hidden = ns.Bool(info.shouldHide),
                    spec_id = ns.Number(info.specID),
                    off_spec_id = ns.Number(info.offSpecID),
                }
                if type(offset) == "number" and type(items) == "number" then
                    for slot = offset + 1, offset + items do
                        if #record.list >= MAX_SPELLBOOK_ITEMS then
                            break
                        end
                        record.list[#record.list + 1] = spellbookItem(probes, slot, line, bank, itemInfo, itemName)
                    end
                end
            end
        end
        return record
    end,
})

-- actionbars ------------------------------------------------------------------

-- The action slots walked: 1 to 180 is twelve slots on each of the fifteen
-- action bars a Retail client numbers. This is the addon's own bound, not a
-- client constant (none is known); a slot the client does not have is
-- expected to answer nothing [verify], and the record says which range was
-- walked.
local FIRST_ACTION_SLOT = 1
local LAST_ACTION_SLOT = 180

ns.Section({
    key = "actionbars",
    path = { "actionbars" },
    events = { "ACTIONBAR_SLOT_CHANGED", "PLAYER_REGEN_ENABLED" },
    on_world = true,
    gather = function()
        local blocked = combatReason()
        if blocked then
            return ns.Absent(blocked)
        end
        local getAction = type(GetActionInfo) == "function" and GetActionInfo or nil
        local getMacro = type(GetMacroInfo) == "function" and GetMacroInfo or nil
        local action = probe("GetActionInfo", getAction)
        local macro = probe("GetMacroInfo", getMacro)
        local list = { action, macro }
        if not getAction then
            return { absent = "GetActionInfo missing", probes = list }
        end
        local record = { first_slot = FIRST_ACTION_SLOT, last_slot = LAST_ACTION_SLOT, slots = {}, probes = list }
        for slot = FIRST_ACTION_SLOT, LAST_ACTION_SLOT do
            local ok, kind, id, subType = pcall(getAction, slot)
            tally(action, ok, ok and kind ~= nil)
            if ok and type(kind) == "string" then
                record.slots[#record.slots + 1] = {
                    slot = slot,
                    type = ns.String(word(kind)),
                    id = ns.Number(id),
                    sub_type = ns.String(word(subType)),
                }
                -- GetMacroInfo is asked only whether the id names a macro.
                -- Its first return (the name) is bound to `_` and its third
                -- (the body) to nothing; neither is stored. Only the probe
                -- counts are kept.
                if kind == "macro" and type(id) == "number" and getMacro then
                    local okMacro, _, icon = pcall(getMacro, id)
                    tally(macro, okMacro, icon ~= nil)
                end
            end
        end
        return record
    end,
})

-- bindings --------------------------------------------------------------------

-- A binding command kept by name: upper-case letters, digits and "_" only,
-- at most 128 (ACTIONBUTTON1, MULTIACTIONBAR1BUTTON3, MOVEFORWARD).
local function command(value)
    if type(value) == "string" and #value <= 128 and string.find(value, "^[%u%d_]+$") then
        return value
    end
    return nil
end

-- A key as the client names it (1, SHIFT-F, CTRL-MOUSEWHEELUP): printable
-- ASCII with no space, at most 64.
local function keyName(value)
    if type(value) == "string" and #value <= 64 and string.find(value, "^[%w%p]+$") then
        return value
    end
    return nil
end

ns.Section({
    key = "bindings",
    path = { "bindings" },
    events = { "UPDATE_BINDINGS", "PLAYER_REGEN_ENABLED" },
    on_world = true,
    gather = function()
        local blocked = combatReason()
        if blocked then
            return ns.Absent(blocked)
        end
        local getSet = type(GetCurrentBindingSet) == "function" and GetCurrentBindingSet or nil
        local getCount = type(GetNumBindings) == "function" and GetNumBindings or nil
        local getBinding = type(GetBinding) == "function" and GetBinding or nil
        local getKey = type(GetBindingKey) == "function" and GetBindingKey or nil
        local set = probe("GetCurrentBindingSet", getSet)
        local count = probe("GetNumBindings", getCount)
        local binding = probe("GetBinding", getBinding)
        local key = probe("GetBindingKey", getKey)
        local list = { set, count, binding, key }
        local missing = (not getCount and "GetNumBindings")
            or (not getBinding and "GetBinding")
            or (not getKey and "GetBindingKey")
        if missing then
            return { absent = ns.String(missing .. " missing"), probes = list }
        end
        local record = { list = {}, commands = 0, commands_left_out = 0, keys_left_out = 0, probes = list }
        if getSet then
            -- Which binding set is active: the client's raw value (on Retail
            -- 1 is the account's set, 2 the character's) [verify].
            local okSet, which = pcall(getSet)
            tally(set, okSet, type(which) == "number")
            if okSet then
                record.binding_set = ns.Number(which)
            end
        end
        local okCount, total = pcall(getCount)
        tally(count, okCount, type(total) == "number")
        if not okCount or type(total) ~= "number" then
            return { absent = "GetNumBindings gave no number", probes = list }
        end
        local commands, leftOut, keysLeftOut = 0, 0, 0
        for index = 1, math.min(total, MAX_BINDINGS) do
            -- First return only: the command. The header, category and keys
            -- GetBinding also returns are not bound.
            local okBinding, found = pcall(getBinding, index)
            tally(binding, okBinding, type(found) == "string")
            if okBinding and type(found) == "string" then
                commands = commands + 1
                local name = command(found)
                if not name then
                    leftOut = leftOut + 1
                else
                    local keys = ns.Pack(pcall(getKey, name))
                    local kept = {}
                    for i = 2, keys[1] and keys.n or 1 do
                        local value = keyName(keys[i])
                        if value then
                            kept[#kept + 1] = ns.String(value)
                        elseif keys[i] ~= nil then
                            keysLeftOut = keysLeftOut + 1
                        end
                    end
                    tally(key, keys[1], keys.n > 1 and keys[2] ~= nil)
                    if #kept > 0 then
                        record.list[#record.list + 1] = { command = ns.String(name), keys = kept }
                    end
                end
            end
        end
        record.commands = commands
        record.commands_left_out = leftOut
        record.keys_left_out = keysLeftOut
        return record
    end,
})
