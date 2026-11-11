-- Spellbook, action bars and key bindings (docs/LAB_PLAN.md §13.1 and §15.2;
-- schema 3, M13-05).
--
--   spellbook   every item of the player's spellbook, by skill line: slot,
--               item type, action and spell id, the name and subtext (a rank
--               where the client gives one) as the client returns them, and
--               the passive and off-spec flags. Future spells (not learned
--               yet) are items too; `item_types` is the client's own
--               Enum.SpellBookItemType, name to number, so a reader can name
--               each item's `type`;
--   actionbars  every action slot that holds something: its type, its id
--               when the id is a number, and its subtype. A macro slot keeps
--               an id only, never the macro's name or body (they are text
--               the owner typed, ADR-0026): with no subtype the id is the
--               macro's index [verify]; with one it is the spell or item the
--               macro shows now [verify]. An equipment-set slot, whose id is
--               the set's name, keeps no id at all;
--   bindings    every binding command GetBinding lists that is a plain
--               upper-case name, with the keys GetBindingKey returns for it
--               now, to cross-check bindings-cache.wtf (§15.2). Commands
--               that are not a plain upper-case name (text after the name,
--               as in `MACRO x`, or other characters) are counted as
--               `commands_left_out` and never asked about or stored: a
--               macro's name is text the owner typed. GetBinding lists
--               named commands; whether a key bound straight to a spell,
--               macro, item or click (a spell dragged onto a key, an
--               action-bar addon's button) is among them at all is
--               [verify], so `commands_left_out` counts only those it listed.
--               Since schema 5 (M16-45; ADR-0026, amendment of 2026-10-22)
--               one more shape is kept: `CLICK <frame>:<button>`, only when
--               the frame is an action button (clickAction below) and the
--               button part is a mouse-button name or on CLICK_BAR_BUTTONS
--               (clickButton below; amendment of 2026-11-11), with
--               `action`: the button's `action` attribute when the bindings
--               were gathered (for a paging bar, the slot of the page shown
--               then). Every other CLICK command
--               is counted in `commands_left_out` and `clicks_left_out`, and
--               nothing more is read from its frame. Whether GetBinding lists
--               CLICK commands at all is [verify].
--
-- Out of combat only (ADR-0026, docs/GLOSSARY.md "secret value"): each
-- gather asks InCombatLockdown first and reads nothing in combat, or when it
-- cannot tell. It then returns ns.Blocked(reason): Core.lua keeps whatever
-- the section gathered earlier this session, writes the reason only when
-- nothing was, and leaves the section dirty, so PLAYER_REGEN_ENABLED (combat
-- ended) gathers it again; a section that is not dirty is not re-read after
-- a fight. Values are read only when InCombatLockdown says not in combat;
-- whether these values can be secret outside combat in restricted content
-- (an instance between pulls) is [verify]. Where the client has
-- `issecretvalue` [verify], each value is tested with it before it is used
-- or stored, and one it calls secret (or cannot say about) is dropped and
-- counted on its API's probe as `secret`. No `issecretvalue` means values
-- are not tested, not that none are secret; a secret value then makes the
-- gather raise and the section is written as an error, unless it was
-- gathered earlier in the session: Core.lua keeps that record, as for a
-- block, since each of these sections has a retry event. `C_Secrets` and
-- `canaccessvalue` [verify] are looked up, never called: their probes say
-- only whether the client has them.
--
-- Every client call in this file is a `pcall` whose values are each passed
-- through `clean` (or cleared when the call raised) before anything else
-- reads them; no client function is called directly (tests/addon pins it).
--
-- Every API here is [verify]. forever-addon-kit's API list for 69893 has the
-- names (not issecretvalue), which proves only that they exist, so each
-- record carries `probes`: per API, whether the function was there, how many
-- calls were made, how many raised an error, how many answered, and how many
-- values were dropped as secret (the way `spec.api` names the API that
-- answered). The README's "M13-05" notes say what each count means. Since
-- schema 5 (M16-45) `probes` is written only in a debug session
-- (`/wowlab debug on`); the calls and checks are the same either way.

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
-- number, a table, a string or a filled slot, as the README lists), and how
-- many of its values were dropped as secret.
local function probe(api, fn)
    return { api = ns.String(api), present = fn ~= nil, calls = 0, errors = 0, answered = 0, secret = 0 }
end

-- The probes looked up and never called (M13-05 round 3): whether the client
-- has the C_Secrets table and canaccessvalue [verify], next to issecretvalue.
-- Appended to `list`, last.
local function lookedUp(list)
    list[#list + 1] = probe("C_Secrets", type(C_Secrets) == "table" and C_Secrets or nil)
    list[#list + 1] = probe("canaccessvalue", type(canaccessvalue) == "function" and canaccessvalue or nil)
end

-- How many values the gather dropped (M16-45 round 1): over every probe,
-- the calls that raised an error and the values dropped as secret or
-- untestable. Written on every gathered record as `dropped`, debug or not;
-- the per-API detail stays in `probes`, behind `/wowlab debug`. Round 2
-- leaves out two probes so nothing is counted twice: `issecretvalue`'s own
-- errors (the value it could not test is already counted as secret on its
-- API's probe), and a CLICK frame's `GetAttribute` (a CLICK command it
-- fails is counted in clicks_left_out, not dropped).
local NOT_DROPPED = { ["issecretvalue"] = true, ["GetAttribute"] = true }

local function droppedCount(list)
    local total = 0
    for i = 1, #list do
        local entry = list[i]
        if not NOT_DROPPED[entry.api] then
            total = total + (ns.Number(entry.errors) or 0) + (ns.Number(entry.secret) or 0)
        end
    end
    return total
end

local function tally(entry, ok, answered)
    entry.calls = (ns.Number(entry.calls) or 0) + 1
    if not ok then
        entry.errors = (ns.Number(entry.errors) or 0) + 1
    elseif answered then
        entry.answered = (ns.Number(entry.answered) or 0) + 1
    end
end

-- `value` when it may be used, or nil and true when it was dropped. With
-- `isSecret` (the client's issecretvalue, [verify]) every value that is not
-- nil is tested first; it is kept only when the test answered false, so a
-- value it calls secret, or one it raised an error or gave no boolean for, is
-- dropped and counted on `entry` (the probe of the API that gave it) as
-- `secret`. The test's own calls are counted on `check`. Without
-- issecretvalue every value is kept untested, and its probe says
-- `present = false`: that means values are not tested, not that none are
-- secret (a secret value then makes the gather raise, and the section is
-- written as an error unless an earlier record is kept).
-- Nothing here compares `value` itself (a secret value may refuse that);
-- `type` reads only its kind.
local function clean(isSecret, check, entry, value)
    if type(value) == "nil" or not isSecret then
        return value, false
    end
    local ok, secret = pcall(isSecret, value)
    tally(check, ok, type(secret) == "boolean")
    if ok and secret == false then
        return value, false
    end
    entry.secret = (ns.Number(entry.secret) or 0) + 1
    return nil, true
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

-- An enum member's name as the client spells it (FutureSpell): a letter,
-- then letters, digits and "_", at most 64.
local function enumName(value)
    if type(value) == "string" and #value <= 64 and string.find(value, "^%a[%w_]*$") then
        return value
    end
    return nil
end

-- Bounds on what one gather walks, so a client returning a bogus count
-- cannot make a gather run away.
local MAX_SKILL_LINES = 64
local MAX_SPELLBOOK_ITEMS = 4096
local MAX_BINDINGS = 4096
local MAX_ENUM_MEMBERS = 64

-- spellbook ---------------------------------------------------------------------

-- Enum.SpellBookItemType as the client has it: member name to number, or
-- nil when the client has no such table. Read, never assumed.
local function spellbookItemTypes()
    local itemTypes = type(Enum) == "table" and Enum.SpellBookItemType or nil
    if type(itemTypes) ~= "table" then
        return nil
    end
    local out, members = {}, 0
    for name, value in pairs(itemTypes) do
        -- tostring: a key that is not a string never passes enumName.
        local key = type(name) == "string" and enumName(tostring(name)) or nil
        if key and type(value) == "number" and members < MAX_ENUM_MEMBERS then
            out[key] = ns.Number(value)
            members = members + 1
        end
    end
    return out
end

local function spellbookItem(probes, slot, skillLine, bank, itemInfo, itemName, isSecret)
    local ok, info = pcall(itemInfo, slot, bank)
    if ok then
        info = clean(isSecret, probes.test, probes.item, info)
    else
        info = nil
    end
    tally(probes.item, ok, type(info) == "table")
    local entry = { slot = ns.Number(slot), skill_line = ns.Number(skillLine) }
    if ok and type(info) == "table" then
        entry.type = ns.Number(clean(isSecret, probes.test, probes.item, info.itemType))
        entry.action_id = ns.Number(clean(isSecret, probes.test, probes.item, info.actionID))
        entry.spell_id = ns.Number(clean(isSecret, probes.test, probes.item, info.spellID))
        entry.passive = ns.Bool(clean(isSecret, probes.test, probes.item, info.isPassive))
        entry.off_spec = ns.Bool(clean(isSecret, probes.test, probes.item, info.isOffSpec))
    end
    if itemName then
        local okText, spellName, subtext = pcall(itemName, slot, bank)
        if okText then
            spellName = clean(isSecret, probes.test, probes.text, spellName)
            subtext = clean(isSecret, probes.test, probes.text, subtext)
        else
            spellName, subtext = nil, nil
        end
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
    events = { "SPELLS_CHANGED" },
    retry = { "PLAYER_REGEN_ENABLED" },
    on_world = true,
    gather = function()
        local blocked = combatReason()
        if blocked then
            return ns.Blocked(blocked)
        end
        local numLines = ns.Fn(C_SpellBook, "GetNumSpellBookSkillLines")
        local lineInfo = ns.Fn(C_SpellBook, "GetSpellBookSkillLineInfo")
        local itemInfo = ns.Fn(C_SpellBook, "GetSpellBookItemInfo")
        local itemName = ns.Fn(C_SpellBook, "GetSpellBookItemName")
        local isSecret = type(issecretvalue) == "function" and issecretvalue or nil
        local probes = {
            lines = probe("C_SpellBook.GetNumSpellBookSkillLines", numLines),
            line = probe("C_SpellBook.GetSpellBookSkillLineInfo", lineInfo),
            item = probe("C_SpellBook.GetSpellBookItemInfo", itemInfo),
            text = probe("C_SpellBook.GetSpellBookItemName", itemName),
            test = probe("issecretvalue", isSecret),
        }
        local asked = { probes.lines, probes.line, probes.item, probes.text, probes.test }
        lookedUp(asked)
        -- Written only in a debug session (M16-45).
        local list = nil
        if ns.debug then
            list = asked
        end
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
        if okCount then
            count = clean(isSecret, probes.test, probes.lines, count)
        else
            count = nil
        end
        tally(probes.lines, okCount, type(count) == "number")
        if not okCount or type(count) ~= "number" then
            return { absent = "C_SpellBook.GetNumSpellBookSkillLines gave no number", probes = list }
        end
        local record = { item_types = spellbookItemTypes(), skill_lines = {}, list = {}, probes = list }
        for line = 1, math.min(count, MAX_SKILL_LINES) do
            local okLine, info = pcall(lineInfo, line)
            if okLine then
                info = clean(isSecret, probes.test, probes.line, info)
            else
                info = nil
            end
            tally(probes.line, okLine, type(info) == "table")
            if okLine and type(info) == "table" then
                -- The skill line's name is never read.
                local offset = clean(isSecret, probes.test, probes.line, info.itemIndexOffset)
                local items = clean(isSecret, probes.test, probes.line, info.numSpellBookItems)
                record.skill_lines[#record.skill_lines + 1] = {
                    index = line,
                    offset = ns.Number(offset),
                    count = ns.Number(items),
                    guild = ns.Bool(clean(isSecret, probes.test, probes.line, info.isGuild)),
                    hidden = ns.Bool(clean(isSecret, probes.test, probes.line, info.shouldHide)),
                    spec_id = ns.Number(clean(isSecret, probes.test, probes.line, info.specID)),
                    off_spec_id = ns.Number(clean(isSecret, probes.test, probes.line, info.offSpecID)),
                }
                if type(offset) == "number" and type(items) == "number" then
                    for slot = offset + 1, offset + items do
                        if #record.list >= MAX_SPELLBOOK_ITEMS then
                            break
                        end
                        record.list[#record.list + 1] =
                            spellbookItem(probes, slot, line, bank, itemInfo, itemName, isSecret)
                    end
                end
            end
        end
        record.dropped = ns.Number(droppedCount(asked))
        return record
    end,
})

-- actionbars ------------------------------------------------------------------

-- The action slots walked: 1 to 180 is fifteen runs of twelve slots: the
-- main bar's pages, the stance and bonus bars, and the multi-action bars
-- [verify]. This is the addon's own bound, not a client constant (none is
-- known); a slot the client does not have is expected to answer nothing
-- [verify], and the record says which range was walked. In a vehicle or
-- while skyriding the client fills some slots with temporary actions
-- [verify] (whether skyriding exists on Forever is not known), so a capture
-- is taken outside both.
local FIRST_ACTION_SLOT = 1
local LAST_ACTION_SLOT = 180

ns.Section({
    key = "actionbars",
    path = { "actionbars" },
    events = { "ACTIONBAR_SLOT_CHANGED" },
    retry = { "PLAYER_REGEN_ENABLED" },
    on_world = true,
    gather = function()
        local blocked = combatReason()
        if blocked then
            return ns.Blocked(blocked)
        end
        local getAction = type(GetActionInfo) == "function" and GetActionInfo or nil
        local getMacro = type(GetMacroInfo) == "function" and GetMacroInfo or nil
        local isSecret = type(issecretvalue) == "function" and issecretvalue or nil
        local action = probe("GetActionInfo", getAction)
        local macro = probe("GetMacroInfo", getMacro)
        local secret = probe("issecretvalue", isSecret)
        local asked = { action, macro, secret }
        lookedUp(asked)
        -- Written only in a debug session (M16-45).
        local list = nil
        if ns.debug then
            list = asked
        end
        if not getAction then
            return { absent = "GetActionInfo missing", probes = list }
        end
        local record = { first_slot = FIRST_ACTION_SLOT, last_slot = LAST_ACTION_SLOT, slots = {}, probes = list }
        for slot = FIRST_ACTION_SLOT, LAST_ACTION_SLOT do
            local hiddenSubType = false
            local ok, kind, id, subType = pcall(getAction, slot)
            if ok then
                kind = clean(isSecret, secret, action, kind)
                id = clean(isSecret, secret, action, id)
                subType, hiddenSubType = clean(isSecret, secret, action, subType)
            else
                kind, id, subType = nil, nil, nil
            end
            tally(action, ok, ok and kind ~= nil)
            if ok and type(kind) == "string" then
                record.slots[#record.slots + 1] = {
                    slot = slot,
                    type = ns.String(word(kind)),
                    id = ns.Number(id),
                    sub_type = ns.String(word(subType)),
                }
                -- A macro slot's id is the macro's index only when the
                -- client gives no subtype (nil or ""); with one, it is the
                -- spell or item the macro shows now [verify], and handing
                -- that to GetMacroInfo would be an out-of-range index into a
                -- C API (the crash class in docs/DATA_SOURCES.md,
                -- 2026-09-28). So GetMacroInfo is asked only then, only
                -- whether the index names a macro. Its first return (the
                -- name) is bound to `_`, which nothing in this file reads,
                -- and its third (the body) to nothing; neither is stored.
                -- `icon` is cleaned, then only compared with nil: only the
                -- probe counts are kept.
                local macroIndex = not hiddenSubType and (subType == nil or subType == "")
                if kind == "macro" and macroIndex and type(id) == "number" and getMacro then
                    local okMacro, _, icon = pcall(getMacro, id)
                    if okMacro then
                        icon = clean(isSecret, secret, macro, icon)
                    else
                        icon = nil
                    end
                    tally(macro, okMacro, icon ~= nil)
                end
            end
        end
        record.dropped = ns.Number(droppedCount(asked))
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

-- The bar-addon button names a kept CLICK command may end in, besides the
-- mouse-button names (ADR-0026, amendment of 2026-11-11). Empty: a name is
-- added here, and to lab/addon/README.md ("Click bindings") and the reader's
-- CLICK_BAR_BUTTONS, only by a ticket, once a capture has shown it.
local CLICK_BAR_BUTTONS = {}

-- Whether a CLICK command's button part may be kept (ADR-0026, amendment of
-- 2026-11-11): a mouse-button name (LeftButton, RightButton, MiddleButton,
-- or Button followed by digits) or a name on CLICK_BAR_BUTTONS.
-- SetBindingClick takes any string as the button, so any other button part
-- may be a word the player typed, and the command is left out.
local function clickButton(button)
    if button == "LeftButton" or button == "RightButton" or button == "MiddleButton" then
        return true
    end
    if string.find(button, "^Button%d+$") then
        return true
    end
    for i = 1, #CLICK_BAR_BUTTONS do
        if button == CLICK_BAR_BUTTONS[i] then
            return true
        end
    end
    return false
end

-- A `CLICK <frame>:<button>` binding command (M16-45; ADR-0026, amendments of
-- 2026-10-22 and 2026-11-11): its frame part, when the command is exactly
-- that shape, with a frame part of at most 64 bytes and a button part of at
-- most 32 that clickButton keeps; nil otherwise. The button is checked here,
-- before the frame is looked up, so a command it refuses reads nothing from
-- its frame.
local function clickParts(value)
    if type(value) ~= "string" then
        return nil
    end
    local _, _, frameName, button = string.find(value, "^CLICK ([%a_][%w_]*):(%a%w*)$")
    if frameName and #frameName <= 64 and #button <= 32 and clickButton(button) then
        return frameName
    end
    return nil
end

-- A binding command that starts with "CLICK " (M16-45): kept here only to be
-- counted in clicks_left_out, or to pass clickParts and clickAction.
local function clickCommand(value)
    if type(value) == "string" and string.find(value, "^CLICK ") then
        return value
    end
    return nil
end

-- The largest action slot number kept: the largest 32-bit signed integer.
local MAX_ACTION_SLOT = 2147483647

-- One attribute of a CLICK command's frame (M16-45). Called only through
-- `pcall`, so the method lookup itself is protected too.
local function attribute(f, name)
    return f:GetAttribute(name)
end

-- The action slot a CLICK command presses, or nil (M16-45; ADR-0026,
-- amendment of 2026-10-22). The global the frame part names must be a table
-- that is a frame (its index 0 holds the client's userdata, read with rawget
-- before any other index); its `type` attribute must be exactly "action" (a
-- button that presses one
-- of the client's numbered action slots: bar addons name these buttons
-- themselves, while GSE, BindPad and `/run` targets are not of that type);
-- and its `action` attribute must be a whole number from 1 to
-- MAX_ACTION_SLOT. Each attribute is read through `pcall` and passed through
-- `clean` (issecretvalue where the client has it). Nothing else is read from
-- the frame, and the frame's name is never stored unless all of this holds.
local function clickAction(isSecret, secret, attributeProbe, frameName)
    -- The one dynamic global lookup in the addon, by the owner's ruling of
    -- 2026-10-22 (ADR-0026): `frameName` passed clickParts, and only the
    -- type of what it names is read here.
    -- selene: allow(global_usage)
    local target = _G[frameName]
    if type(target) ~= "table" then
        return nil
    end
    -- A frame's index 0 holds the client's userdata. rawget reads it without
    -- any metatable, so a plain table posing as a frame stops here (the
    -- second dynamic lookup the ruling allows, M16-45 round 1).
    if type(rawget(target, 0)) ~= "userdata" then
        return nil
    end
    local okType, kind = pcall(attribute, target, "type")
    if okType then
        kind = clean(isSecret, secret, attributeProbe, kind)
    else
        kind = nil
    end
    tally(attributeProbe, okType, type(kind) == "string")
    if not okType or kind ~= "action" then
        return nil
    end
    local okSlot, slot = pcall(attribute, target, "action")
    if okSlot then
        slot = clean(isSecret, secret, attributeProbe, slot)
    else
        slot = nil
    end
    tally(attributeProbe, okSlot, type(slot) == "number")
    if okSlot and type(slot) == "number" and slot >= 1 and slot <= MAX_ACTION_SLOT and slot % 1 == 0 then
        return ns.Number(slot)
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
    events = { "UPDATE_BINDINGS" },
    retry = { "PLAYER_REGEN_ENABLED" },
    on_world = true,
    gather = function()
        local blocked = combatReason()
        if blocked then
            return ns.Blocked(blocked)
        end
        local getSet = type(GetCurrentBindingSet) == "function" and GetCurrentBindingSet or nil
        local getCount = type(GetNumBindings) == "function" and GetNumBindings or nil
        local getBinding = type(GetBinding) == "function" and GetBinding or nil
        local getKey = type(GetBindingKey) == "function" and GetBindingKey or nil
        local isSecret = type(issecretvalue) == "function" and issecretvalue or nil
        local set = probe("GetCurrentBindingSet", getSet)
        local count = probe("GetNumBindings", getCount)
        local binding = probe("GetBinding", getBinding)
        local key = probe("GetBindingKey", getKey)
        local secret = probe("issecretvalue", isSecret)
        -- A frame's GetAttribute (CLICK commands, M16-45): looked up on each
        -- frame, so it is counted as present.
        local attributeProbe = probe("GetAttribute", true)
        local asked = { set, count, binding, key, secret, attributeProbe }
        lookedUp(asked)
        -- Written only in a debug session (M16-45).
        local list = nil
        if ns.debug then
            list = asked
        end
        local missing = (not getCount and "GetNumBindings")
            or (not getBinding and "GetBinding")
            or (not getKey and "GetBindingKey")
        if missing then
            return { absent = ns.String(missing .. " missing"), probes = list }
        end
        local record = {
            list = {},
            commands = 0,
            commands_left_out = 0,
            clicks_left_out = 0,
            keys_left_out = 0,
            probes = list,
        }
        -- The client's names for its two binding sets [verify], so a reader
        -- can name `binding_set` without assuming the numbers.
        record.account_bindings = ns.Number(ACCOUNT_BINDINGS)
        record.character_bindings = ns.Number(CHARACTER_BINDINGS)
        if getSet then
            -- Which binding set is active: the client's raw value [verify].
            local okSet, which = pcall(getSet)
            if okSet then
                which = clean(isSecret, secret, set, which)
            else
                which = nil
            end
            tally(set, okSet, type(which) == "number")
            if okSet then
                record.binding_set = ns.Number(which)
            end
        end
        local okCount, total = pcall(getCount)
        if okCount then
            total = clean(isSecret, secret, count, total)
        else
            total = nil
        end
        tally(count, okCount, type(total) == "number")
        if not okCount or type(total) ~= "number" then
            return { absent = "GetNumBindings gave no number", probes = list }
        end
        local commands, leftOut, clicksLeftOut, keysLeftOut = 0, 0, 0, 0
        for index = 1, math.min(total, MAX_BINDINGS) do
            -- First return only: the command. The header, category and keys
            -- GetBinding also returns are not bound.
            local okBinding, found = pcall(getBinding, index)
            if okBinding then
                found = clean(isSecret, secret, binding, found)
            else
                found = nil
            end
            tally(binding, okBinding, type(found) == "string")
            if okBinding and type(found) == "string" then
                commands = commands + 1
                local name = command(found)
                local action = nil
                local click = nil
                if not name then
                    click = clickCommand(found)
                end
                if click then
                    -- A CLICK command is kept only under the rule of
                    -- clickParts and clickAction (M16-45); every other one
                    -- is counted in clicks_left_out too.
                    local frameName = clickParts(click)
                    if frameName then
                        action = clickAction(isSecret, secret, attributeProbe, frameName)
                    end
                    if action then
                        name = ns.String(click)
                    else
                        clicksLeftOut = clicksLeftOut + 1
                    end
                end
                if not name then
                    leftOut = leftOut + 1
                else
                    local keys = ns.Pack(pcall(getKey, name))
                    local kept, any = {}, false
                    for i = 2, keys[1] and keys.n or 1 do
                        local given = clean(isSecret, secret, key, keys[i])
                        local value = keyName(given)
                        if given ~= nil then
                            any = true
                        end
                        if value then
                            kept[#kept + 1] = ns.String(value)
                        elseif given ~= nil then
                            keysLeftOut = keysLeftOut + 1
                        end
                    end
                    tally(key, keys[1], any)
                    if #kept > 0 then
                        record.list[#record.list + 1] =
                            { command = ns.String(name), action = ns.Number(action), keys = kept }
                    end
                end
            end
        end
        record.commands = commands
        record.commands_left_out = leftOut
        record.clicks_left_out = clicksLeftOut
        record.keys_left_out = keysLeftOut
        record.dropped = ns.Number(droppedCount(asked))
        return record
    end,
})
