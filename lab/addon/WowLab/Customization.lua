-- Customization choices (docs/LAB_PLAN.md §13.1): option id to choice id,
-- mapped from the barber shop's choice index to the choice's `id`. Recorded
-- only when the barber shop opens and after an applied change, never at close
-- after a cancelled preview. The record is "as of the last barber-shop visit
-- with the addon enabled" in this session: nothing but the probe count is
-- carried from one session to the next.
--
-- All [verify] for M11-03: C_BarberShop.GetAvailableCustomizations and its
-- category/option/choice shape, whether currentChoiceIndex is 1-based, the
-- BARBER_SHOP_APPEARANCE_APPLIED event, C_BarberShop.GetCurrentCharacterData's
-- `sex` field, and C_BarberShop.GetViewingChrModel. The name fields that
-- GetCurrentCharacterData returns are never read.

local _, ns = ...

local function choices(categories)
    local out, seen = {}, {}
    for _, category in ipairs(categories) do
        local options = type(category) == "table" and category.options or nil
        if type(options) == "table" then
            for _, option in ipairs(options) do
                if type(option) == "table" and type(option.id) == "number" and not seen[option.id] then
                    seen[option.id] = true
                    local index = option.currentChoiceIndex
                    local choice = type(index) == "number" and type(option.choices) == "table"
                        and option.choices[index] or nil
                    out[#out + 1] = {
                        option = option.id,
                        choice_index = index,
                        choice = type(choice) == "table" and choice.id or nil,
                    }
                end
            end
        end
    end
    return out
end

ns.Section({
    key = "customization",
    path = { "customization" },
    events = { "BARBER_SHOP_OPEN", "BARBER_SHOP_APPEARANCE_APPLIED" },
    immediate = true,
    not_gathered = "no barber-shop visit with the addon enabled this session",
    gather = function(event)
        local available = ns.Fn(C_BarberShop, "GetAvailableCustomizations")
        if not available then
            return ns.Absent("C_BarberShop.GetAvailableCustomizations missing")
        end
        local categories = ns.Call(available)
        if type(categories) ~= "table" then
            return ns.Absent("C_BarberShop.GetAvailableCustomizations returned nothing")
        end
        local record = {
            as_of = "last barber-shop visit with the addon enabled",
            recorded_at = event == "BARBER_SHOP_OPEN" and "open" or "applied",
            choices = choices(categories),
        }
        -- Race id: the third return of UnitRace; the first two (localized
        -- race name and file token) are not kept.
        if type(UnitRace) == "function" then
            record.race_id = select(3, UnitRace("player"))
        end
        local current = ns.Fn(C_BarberShop, "GetCurrentCharacterData")
        local data = current and ns.Call(current)
        if type(data) == "table" and type(data.sex) == "number" then
            record.sex = data.sex
        end
        local model = ns.Fn(C_BarberShop, "GetViewingChrModel")
        if model then
            record.chr_model_id = ns.Call(model)
        end
        return record
    end,
})
