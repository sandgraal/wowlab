-- Equipped gear (docs/LAB_PLAN.md §13.1): item links as strings, so bonus IDs
-- and enchants survive; each slot's current item level and the equipped
-- average as the client reports them. Slots come from the client's own
-- first/last equipped-slot constants, not a fixed list (whether Forever has a
-- ranged slot between them is [verify]).

local _, ns = ...

-- Returns the slot's item level and the API that gave it.
local function itemLevel(slot, link)
    local current = ns.Fn(C_Item, "GetCurrentItemLevel")
    local fromSlot = type(ItemLocation) == "table" and ns.Fn(ItemLocation, "CreateFromEquipmentSlot")
    if current and fromSlot then
        local location = ns.Call(fromSlot, ItemLocation, slot)
        local level = location and ns.Call(current, location)
        if type(level) == "number" then
            return level, "C_Item.GetCurrentItemLevel"
        end
    end
    local detailed = ns.Fn(C_Item, "GetDetailedItemLevelInfo")
    if detailed then
        local level = ns.Call(detailed, link)
        if type(level) == "number" then
            return level, "C_Item.GetDetailedItemLevelInfo"
        end
    end
    return nil, nil
end

ns.Section({
    key = "gear",
    path = { "gear" },
    events = { "PLAYER_EQUIPMENT_CHANGED", "PLAYER_AVG_ITEM_LEVEL_UPDATE" },
    on_world = true,
    gather = function()
        local first, last = INVSLOT_FIRST_EQUIPPED, INVSLOT_LAST_EQUIPPED
        if type(first) ~= "number" or type(last) ~= "number" then
            return ns.Absent("INVSLOT_FIRST_EQUIPPED or INVSLOT_LAST_EQUIPPED missing")
        end
        if type(GetInventoryItemLink) ~= "function" then
            return ns.Absent("GetInventoryItemLink missing")
        end
        local record = { first_slot = first, last_slot = last, slots = {} }
        for slot = first, last do
            local link = GetInventoryItemLink("player", slot)
            if type(link) == "string" then
                local level, source = itemLevel(slot, link)
                record.slots[#record.slots + 1] = {
                    slot = slot,
                    link = link,
                    item_level = level,
                    item_level_api = source,
                }
            end
        end
        if type(GetAverageItemLevel) == "function" then
            local overall, equipped, pvp = GetAverageItemLevel()
            record.average = { overall = overall, equipped = equipped, pvp = pvp }
        else
            record.average = ns.Absent("GetAverageItemLevel missing")
        end
        return record
    end,
})
