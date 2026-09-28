-- Collections (docs/LAB_PLAN.md §13.1): mounts, toys and pets (by species id
-- and count; no battle-pet ids, they are GUIDs), and appearances as collected
-- source (item-modified-appearance) ids. A list read through a filtered
-- journal says so (`filtered = true`); the addon never changes the owner's
-- filters, search text or collapsed headers. Every API here is [verify] for
-- M11-03 (all are present on 69893 per forever-addon-kit's API baseline, but
-- nothing exercised them there). Collections stay per-character until M11-03
-- shows which are account-wide on Forever.

local _, ns = ...

-- Mounts: C_MountJournal.GetMountIDs lists every mount, unfiltered;
-- GetMountInfoByID's 11th return is isCollected [verify].
local function mounts()
    local ids = ns.Fn(C_MountJournal, "GetMountIDs")
    local info = ns.Fn(C_MountJournal, "GetMountInfoByID")
    if not (ids and info) then
        return ns.Absent("C_MountJournal.GetMountIDs or .GetMountInfoByID missing")
    end
    local collected = {}
    for _, mountID in ipairs(ns.Numbers(ns.Call(ids))) do
        if select(11, ns.Call(info, mountID)) == true then
            collected[#collected + 1] = mountID
        end
    end
    table.sort(collected)
    return { collected = collected, filtered = false }
end

-- Toys: the toy box has no unfiltered list, so this reads through its current
-- filter and search text, and records the filter switches it can read.
local function toys()
    local count = ns.Fn(C_ToyBox, "GetNumFilteredToys")
    local fromIndex = ns.Fn(C_ToyBox, "GetToyFromIndex")
    if not (count and fromIndex and type(PlayerHasToy) == "function") then
        return ns.Absent("C_ToyBox.GetNumFilteredToys, .GetToyFromIndex or PlayerHasToy missing")
    end
    local collected, seen = {}, {}
    local shown = ns.Call(count)
    for index = 1, type(shown) == "number" and shown or 0 do
        local itemID = ns.Call(fromIndex, index)
        if type(itemID) == "number" and itemID > 0 and not seen[itemID] then
            seen[itemID] = true
            if ns.Call(PlayerHasToy, itemID) then
                collected[#collected + 1] = itemID
            end
        end
    end
    table.sort(collected)
    local filter = {}
    local collectedShown = ns.Fn(C_ToyBox, "GetCollectedShown")
    if collectedShown then
        filter.collected_shown = ns.Call(collectedShown)
    end
    local uncollectedShown = ns.Fn(C_ToyBox, "GetUncollectedShown")
    if uncollectedShown then
        filter.uncollected_shown = ns.Call(uncollectedShown)
    end
    local unusableShown = ns.Fn(C_ToyBox, "GetUnusableShown")
    if unusableShown then
        filter.unusable_shown = ns.Call(unusableShown)
    end
    return { collected = collected, filtered = true, filter = filter }
end

-- Pets: the pet journal lists by index through its filters. Only the species
-- id and whether it is owned are taken from each row (returns 2 and 3); the
-- first return, the battle-pet GUID, is never bound to a variable.
local function pets()
    local num = ns.Fn(C_PetJournal, "GetNumPets")
    local byIndex = ns.Fn(C_PetJournal, "GetPetInfoByIndex")
    local collectedInfo = ns.Fn(C_PetJournal, "GetNumCollectedInfo")
    if not (num and byIndex and collectedInfo) then
        return ns.Absent("C_PetJournal.GetNumPets, .GetPetInfoByIndex or .GetNumCollectedInfo missing")
    end
    local species, seen = {}, {}
    local rows = ns.Call(num)
    for index = 1, type(rows) == "number" and rows or 0 do
        local speciesID, owned = select(2, ns.Call(byIndex, index))
        if owned and type(speciesID) == "number" and not seen[speciesID] then
            seen[speciesID] = true
            species[#species + 1] = speciesID
        end
    end
    table.sort(species)
    local out = {}
    for _, speciesID in ipairs(species) do
        out[#out + 1] = { species = speciesID, count = ns.Call(collectedInfo, speciesID) }
    end
    local record = { species = out, filtered = true }
    local default = ns.Fn(C_PetJournal, "IsUsingDefaultFilters")
    if default then
        record.default_filters = ns.Call(default)
    end
    return record
end

-- Appearances: every Enum.TransmogCollectionType category, its collected
-- visuals, and each visual's sources the player has. Whether
-- GetCategoryAppearances honours the wardrobe's filters is [verify], so the
-- list is marked filtered.
local function appearances()
    local categories = type(Enum) == "table" and Enum.TransmogCollectionType or nil
    local byCategory = ns.Fn(C_TransmogCollection, "GetCategoryAppearances")
    local sourcesOf = ns.Fn(C_TransmogCollection, "GetAllAppearanceSources")
    local has = ns.Fn(C_TransmogCollection, "PlayerHasTransmogItemModifiedAppearance")
    if not (type(categories) == "table" and byCategory and sourcesOf and has) then
        return ns.Absent("Enum.TransmogCollectionType or a C_TransmogCollection function missing")
    end
    local collected, seen = {}, {}
    for _, category in pairs(categories) do
        local visuals = type(category) == "number" and ns.Call(byCategory, category) or nil
        if type(visuals) == "table" then
            for _, visual in ipairs(visuals) do
                if type(visual) == "table" and visual.isCollected and type(visual.visualID) == "number" then
                    for _, sourceID in ipairs(ns.Numbers(ns.Call(sourcesOf, visual.visualID))) do
                        if not seen[sourceID] then
                            seen[sourceID] = true
                            if ns.Call(has, sourceID) then
                                collected[#collected + 1] = sourceID
                            end
                        end
                    end
                end
            end
        end
    end
    table.sort(collected)
    local record = { sources = collected, filtered = true }
    local default = ns.Fn(C_TransmogCollection, "IsUsingDefaultFilters")
    if default then
        record.default_filters = ns.Call(default)
    end
    return record
end

ns.Section({
    key = "collections.mounts",
    path = { "collections", "mounts" },
    events = { "NEW_MOUNT_ADDED", "COMPANION_LEARNED" },
    on_world = true,
    heavy = true,
    gather = mounts,
})

ns.Section({
    key = "collections.toys",
    path = { "collections", "toys" },
    events = { "NEW_TOY_ADDED", "TOYS_UPDATED" },
    on_world = true,
    heavy = true,
    gather = toys,
})

ns.Section({
    key = "collections.pets",
    path = { "collections", "pets" },
    events = { "NEW_PET_ADDED", "PET_JOURNAL_LIST_UPDATE" },
    on_world = true,
    heavy = true,
    gather = pets,
})

ns.Section({
    key = "collections.appearances",
    path = { "collections", "appearances" },
    events = { "TRANSMOG_COLLECTION_UPDATED", "TRANSMOG_COLLECTION_SOURCE_ADDED" },
    on_world = true,
    heavy = true,
    gather = appearances,
})
