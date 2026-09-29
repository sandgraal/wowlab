-- Spec and talents (docs/LAB_PLAN.md §13.1).
--
-- Talents are two sections, each a C_Traits config dump (config, tree, node
-- and entry ids and ranks), each { absent = "<reason>" } when its API is
-- missing:
--   talents.class   the class talents active now, the last selected saved
--                   loadout's config id, and the loadout export string;
--   talents.legacy  Legacy-tree candidates: every non-Combat, non-Profession trait config (the panel ToggleLegacySystemUI opens, unlocked at level 25); empty below that level.
-- No loadout names: C_Traits.GetConfigInfo's `name` is text the owner typed
-- and is never read.
--
-- Reference: the trait walker in https://github.com/Thunderz96/forever-addon-kit
-- (MIT; addons/ForeverBeacon/FB_Spells.lua), read as a reference only; no
-- code is copied. APIs it exercises on the live Forever client are marked
-- "confirmed by forever-addon-kit on 69893, re-verify in M11-03" below.
-- Everything else is [verify] for the owner's capture (M11-03).
--
-- Spec: Forever has no Retail GetSpecialization global (confirmed by
-- forever-addon-kit on 69893, re-verify in M11-03), so the spec is found by
-- testing for the API; the field is optional in the schema-1 model.

local _, ns = ...

-- Spec -------------------------------------------------------------------------

-- Returns { index, id, api } or nil and a reason.
-- C_SpecializationInfo.GetSpecialization / .GetSpecializationInfo: the
-- namespace exists on 69893 per the kit's API baseline; what they return on
-- Forever is [verify]. The GetSpecialization / GetSpecializationInfo
-- globals are absent on Forever (confirmed by forever-addon-kit on 69893,
-- re-verify in M11-03) and only used if some client provides them.
function ns.Spec()
    local getSpec = ns.Fn(C_SpecializationInfo, "GetSpecialization")
    local getInfo = ns.Fn(C_SpecializationInfo, "GetSpecializationInfo")
    local api = "C_SpecializationInfo"
    if not getSpec then
        getSpec = type(GetSpecialization) == "function" and GetSpecialization or nil
        getInfo = type(GetSpecializationInfo) == "function" and GetSpecializationInfo or nil
        api = "GetSpecialization"
    end
    if not getSpec then
        return nil, "no GetSpecialization API (C_SpecializationInfo or global)"
    end
    local index = ns.Call(getSpec)
    local spec = { index = ns.Number(index), api = api }
    if type(index) == "number" and index > 0 and getInfo then
        -- First return only: the spec id. Name and description are not kept.
        local id = ns.Call(getInfo, index)
        if type(id) == "number" then
            spec.id = id
        end
    end
    return spec
end

ns.Section({
    key = "spec",
    path = { "spec" },
    events = { "ACTIVE_PLAYER_SPECIALIZATION_CHANGED", "PLAYER_SPECIALIZATION_CHANGED" },
    on_world = true,
    gather = function()
        local spec, reason = ns.Spec()
        return spec or ns.Absent(reason)
    end,
})

-- C_Traits walker ----------------------------------------------------------------

-- C_Traits.GetConfigInfo, .GetTreeNodes, .GetNodeInfo: confirmed by
-- forever-addon-kit on 69893, re-verify in M11-03.
local function traitsReason()
    if type(C_Traits) ~= "table" then
        return "C_Traits missing"
    end
    if not ns.Fn(C_Traits, "GetConfigInfo") then
        return "C_Traits.GetConfigInfo missing"
    end
    if not ns.Fn(C_Traits, "GetTreeNodes") then
        return "C_Traits.GetTreeNodes missing"
    end
    if not ns.Fn(C_Traits, "GetNodeInfo") then
        return "C_Traits.GetNodeInfo missing"
    end
    return nil
end

-- C_Traits.GetTreeCurrencyInfo: confirmed by forever-addon-kit on 69893,
-- re-verify in M11-03. Whether it carries the Legacy points spent and the
-- seasonal cap is [verify]. The third argument, excludeStagedChanges, is
-- true: points staged in the talent UI but not applied are left out.
local function treeCurrencies(configID, treeID)
    local fn = ns.Fn(C_Traits, "GetTreeCurrencyInfo")
    if not fn then
        return ns.Absent("C_Traits.GetTreeCurrencyInfo missing")
    end
    local out = {}
    local list = ns.Call(fn, configID, treeID, true)
    if type(list) == "table" then
        for _, currency in ipairs(list) do
            if type(currency) == "table" then
                out[#out + 1] = {
                    id = ns.Number(currency.traitCurrencyID),
                    quantity = ns.Number(currency.quantity),
                    max_quantity = ns.Number(currency.maxQuantity),
                    spent = ns.Number(currency.spent),
                }
            end
        end
    end
    return out
end

-- active_rank is the committed rank; ranks_purchased and current_rank may
-- include changes staged in the talent UI but not applied [verify].
local function dumpNode(configID, nodeID)
    local node = ns.Call(C_Traits.GetNodeInfo, configID, nodeID)
    -- A node the config cannot reach comes back with ID 0.
    if type(node) ~= "table" or not node.ID or node.ID == 0 then
        return nil
    end
    local record = {
        id = ns.Number(nodeID),
        ranks_purchased = ns.Number(node.ranksPurchased),
        active_rank = ns.Number(node.activeRank),
        current_rank = ns.Number(node.currentRank),
        max_ranks = ns.Number(node.maxRanks),
        is_visible = ns.Bool(node.isVisible),
        entries = ns.Numbers(node.entryIDs),
        sub_tree = ns.Number(node.subTreeID),
    }
    if type(node.activeEntry) == "table" then
        record.active_entry = ns.Number(node.activeEntry.entryID)
        record.active_entry_rank = ns.Number(node.activeEntry.rank)
    end
    return record
end

local function dumpTree(configID, treeID)
    local tree = { id = ns.Number(treeID), nodes = {} }
    -- C_Traits.GetSystemIDByTreeID: confirmed by forever-addon-kit on 69893,
    -- re-verify in M11-03.
    local systemOf = ns.Fn(C_Traits, "GetSystemIDByTreeID")
    if systemOf then
        tree.system_id = ns.Number(ns.Call(systemOf, treeID))
    end
    tree.currencies = treeCurrencies(configID, treeID)
    local nodeIDs = ns.Call(C_Traits.GetTreeNodes, treeID)
    for _, nodeID in ipairs(ns.Numbers(nodeIDs)) do
        local node = dumpNode(configID, nodeID)
        if node then
            tree.nodes[#tree.nodes + 1] = node
        end
    end
    return tree
end

local function dumpConfig(configID)
    local info = ns.Call(C_Traits.GetConfigInfo, configID)
    if type(info) ~= "table" then
        return { id = ns.Number(configID), absent = "C_Traits.GetConfigInfo returned nothing" }
    end
    -- info.name (the loadout name) is never read.
    local config = { id = ns.Number(configID), type = ns.Number(info.type), trees = {} }
    for _, treeID in ipairs(ns.Numbers(info.treeIDs)) do
        config.trees[#config.trees + 1] = dumpTree(configID, treeID)
    end
    return config
end

-- C_ClassTalents.GetActiveConfigID: confirmed by forever-addon-kit on 69893,
-- re-verify in M11-03.
local function activeClassConfig()
    local fn = ns.Fn(C_ClassTalents, "GetActiveConfigID")
    if not fn then
        return nil
    end
    return ns.Call(fn)
end

-- talents.class ---------------------------------------------------------------

-- Events TRAIT_CONFIG_UPDATED, TRAIT_CONFIG_LIST_UPDATED,
-- TRAIT_TREE_CURRENCY_INFO_UPDATED, PLAYER_TALENT_UPDATE and
-- TRAIT_SYSTEM_INTERACTION_STARTED are registered by forever-addon-kit's
-- walker on 69893, but its registration is wrapped in pcall and it also
-- registers LEARNED_SPELL_IN_TAB, which its README says does not exist; so
-- they are [verify] like every other event, as are
-- ACTIVE_COMBAT_CONFIG_CHANGED and PLAYER_LEVEL_UP. Unknown events are
-- skipped (Core.lua).

ns.Section({
    key = "talents.class",
    path = { "talents", "class" },
    events = {
        "TRAIT_CONFIG_UPDATED",
        "TRAIT_CONFIG_LIST_UPDATED",
        "PLAYER_TALENT_UPDATE",
        "ACTIVE_COMBAT_CONFIG_CHANGED",
        "PLAYER_LEVEL_UP",
    },
    on_world = true,
    gather = function()
        local reason = traitsReason()
        if reason then
            return ns.Absent(reason)
        end
        if not ns.Fn(C_ClassTalents, "GetActiveConfigID") then
            return ns.Absent("C_ClassTalents.GetActiveConfigID missing")
        end
        local configID = activeClassConfig()
        if type(configID) ~= "number" then
            return ns.Absent("C_ClassTalents.GetActiveConfigID returned no config")
        end
        local record = { config = dumpConfig(configID) }

        -- C_Traits.GenerateImportString: present on 69893 per the kit's API
        -- baseline; its output on Forever is [verify]. Exactly one of
        -- `export` and `export_absent` is written (M11-22): the reason says
        -- whether the function is missing, raised an error, returned an
        -- empty string or returned no string. It is called through pcall, not ns.Call, so an error can be
        -- told from an empty return.
        local export = ns.Fn(C_Traits, "GenerateImportString")
        if not export then
            record.export_absent = "C_Traits.GenerateImportString missing"
        else
            local ok, text = pcall(export, configID)
            if not ok then
                record.export_absent = "C_Traits.GenerateImportString raised an error"
            elseif type(text) == "string" and text ~= "" then
                record.export = text
            elseif text == "" then
                record.export_absent = "C_Traits.GenerateImportString returned an empty string"
            else
                record.export_absent = "C_Traits.GenerateImportString returned no string"
            end
        end

        -- C_ClassTalents.GetLastSelectedSavedConfigID(specID): [verify]. It
        -- needs a spec id, which Forever may not expose the Retail way. The
        -- value is kept raw: it may be a negative sentinel (e.g. a starter
        -- build) rather than a config id. Exactly one of
        -- `last_selected_config` and `last_selected_config_absent` is written
        -- (M11-22); nil from the client is taken, from Retail behaviour, to
        -- mean no saved loadout is selected for this spec [verify], told apart
        -- from an error by calling through pcall.
        local lastSaved = ns.Fn(C_ClassTalents, "GetLastSelectedSavedConfigID")
        local spec = ns.Spec()
        if not lastSaved then
            record.last_selected_config_absent = "C_ClassTalents.GetLastSelectedSavedConfigID missing"
        elseif not (spec and spec.id) then
            record.last_selected_config_absent = "no spec id to ask with"
        else
            local ok, value = pcall(lastSaved, spec.id)
            if not ok then
                record.last_selected_config_absent = "C_ClassTalents.GetLastSelectedSavedConfigID raised an error"
            elseif type(value) == "number" then
                record.last_selected_config = value
            elseif value == nil then
                record.last_selected_config_absent = "the client returned no last-selected loadout for this spec"
            else
                record.last_selected_config_absent = "C_ClassTalents.GetLastSelectedSavedConfigID returned no number"
            end
        end
        return record
    end,
})

-- talents.legacy --------------------------------------------------------------

-- Which trait config holds the Legacy trees is [verify]. Two discoveries,
-- neither keyed on a constant of ours:
--   * every Enum.TraitConfigType value except Invalid, Combat (class
--     loadouts) and Profession, through C_Traits.GetConfigsByType;
--   * every numeric `*SYSTEM_ID*` field in the client's Constants tables,
--     through C_Traits.GetConfigIDBySystemID (the Constants scan is [verify]).
-- C_Traits.GetConfigsByType, C_Traits.GetConfigIDBySystemID and
-- Enum.TraitConfigType: called by forever-addon-kit's walker on 69893; which
-- call found the Legacy config is not recorded: [verify] in M11-03.
-- talents.legacy holds every trait config the client lists that is neither
-- the active class config nor of type Combat or Profession, each with
-- `found_by`; which of them is the Legacy system is decided from the M11-03
-- capture.
local function legacyConfigs(classConfig)
    local foundBy, order = {}, {}
    local function add(configID, how)
        if type(configID) ~= "number" or configID == classConfig then
            return
        end
        if not foundBy[configID] then
            foundBy[configID] = {}
            order[#order + 1] = ns.Number(configID)
        end
        local list = foundBy[configID]
        list[#list + 1] = ns.String(how)
    end

    local skipped = {}
    local byType = ns.Fn(C_Traits, "GetConfigsByType")
    local types = type(Enum) == "table" and Enum.TraitConfigType or nil
    local haveTypes = byType and type(types) == "table"
    if haveTypes then
        local skip = {}
        for _, name in ipairs({ "Invalid", "Combat", "Profession" }) do
            if types[name] ~= nil then
                skip[types[name]] = true
                skipped[#skipped + 1] = name
            end
        end
        for name, value in pairs(types) do
            if not skip[value] then
                local ids = ns.Call(byType, value)
                for _, configID in ipairs(ns.Numbers(ids)) do
                    add(configID, "type:" .. tostring(name))
                end
            end
        end
    end

    local bySystem = ns.Fn(C_Traits, "GetConfigIDBySystemID")
    local haveSystems = bySystem and type(Constants) == "table"
    if haveSystems then
        for group, fields in pairs(Constants) do
            if type(fields) == "table" then
                for key, value in pairs(fields) do
                    if type(key) == "string" and type(value) == "number" and string.find(key, "SYSTEM_ID", 1, true) then
                        add(ns.Call(bySystem, value), "system:" .. tostring(group) .. "." .. key)
                    end
                end
            end
        end
    end

    if not haveTypes and not haveSystems then
        return nil, "neither C_Traits.GetConfigsByType with Enum.TraitConfigType"
            .. " nor C_Traits.GetConfigIDBySystemID with Constants"
    end
    table.sort(order)
    for _, configID in ipairs(order) do
        table.sort(foundBy[configID])
    end
    return order, foundBy, skipped
end

ns.Section({
    key = "talents.legacy",
    path = { "talents", "legacy" },
    events = {
        "TRAIT_CONFIG_UPDATED",
        "TRAIT_CONFIG_LIST_UPDATED",
        "TRAIT_TREE_CURRENCY_INFO_UPDATED",
        "TRAIT_SYSTEM_INTERACTION_STARTED",
        "PLAYER_LEVEL_UP",
    },
    on_world = true,
    gather = function()
        local reason = traitsReason()
        if reason then
            return ns.Absent(reason)
        end
        local order, foundBy, skipped = legacyConfigs(activeClassConfig())
        if not order then
            -- On failure the second return is the reason.
            return ns.Absent(foundBy)
        end
        local record = {
            -- The panel's opener; confirmed by forever-addon-kit on 69893,
            -- re-verify in M11-03. Recorded as a marker only; never called.
            legacy_ui = type(ToggleLegacySystemUI) == "function",
            -- The unlock level is the client's rule; the level explains an
            -- empty list without a constant of ours. UnitLevel("player"):
            -- confirmed by forever-addon-kit on 69893, re-verify in M11-03.
            player_level = type(UnitLevel) == "function" and ns.Number(UnitLevel("player")) or nil,
            skipped_types = skipped,
            configs = {},
        }
        for _, configID in ipairs(order) do
            local config = dumpConfig(configID)
            config.found_by = foundBy[configID]
            record.configs[#record.configs + 1] = config
        end
        return record
    end,
})
