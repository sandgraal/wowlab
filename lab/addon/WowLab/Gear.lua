-- Equipped gear (docs/LAB_PLAN.md §13.1): item links as strings, so bonus IDs
-- and enchants survive; each slot's current item level and the equipped
-- average as the client reports them. Slots are every slot number from the
-- client's own INVSLOT_FIRST_EQUIPPED to INVSLOT_LAST_EQUIPPED, so the range
-- covers whatever the client defines, not a fixed list (whether Forever has a
-- ranged slot in it is [verify]).
--
-- A crafted item's link can carry the crafter's player GUID
-- ("Player-<realm id>-<hex>"). Since schema 5 (ADR-0026, note of
-- 2026-11-20) the link is stored as the client gives it, the GUID included,
-- and `crafter_removed` is no longer written. Whether Forever fills the
-- field is [verify]. The capture tool replaces a GUID in a committed capture.

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
        local record = { first_slot = ns.Number(first), last_slot = ns.Number(last), slots = {} }
        for slot = first, last do
            local raw = GetInventoryItemLink("player", slot)
            if type(raw) == "string" then
                local level, source = itemLevel(slot, raw)
                record.slots[#record.slots + 1] = {
                    slot = slot,
                    link = raw,
                    item_level = level,
                    item_level_api = source,
                }
            end
        end
        if type(GetAverageItemLevel) == "function" then
            -- overall: best items owned, bags included; equipped: the
            -- equipped average as the client reports it. On Retail that is
            -- the character-sheet figure; Forever's character sheet shows
            -- no item level (M11-03), so nothing in its UI backs it.
            local overall, equipped, pvp = GetAverageItemLevel()
            record.average = { overall = ns.Number(overall), equipped = ns.Number(equipped), pvp = ns.Number(pvp) }
        else
            record.average = ns.Absent("GetAverageItemLevel missing")
        end
        return record
    end,
})
