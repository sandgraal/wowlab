-- Customization choices (docs/LAB_PLAN.md §13.1, amended 2026-09-28): option
-- id to choice id, mapped from the barber shop's choice index to the choice's
-- `id`, for the model the barber shop was showing (`chr_model_id`). Recorded
-- only when the barber shop opens and after an applied change, never at close
-- after a cancelled preview.
--
-- Appearance changes only in the barber shop or through a paid service at
-- character select, so the last record is kept from session to session: at
-- ADDON_LOADED a saved record is carried into this session with
-- `carried = true`, and it describes the character until the next visit. Each
-- new record carries `recorded_load`, the probe count of the session that
-- recorded it. At PLAYER_ENTERING_WORLD a record whose `race_id` no longer
-- matches the character's race is dropped with a reason (race only; a paid
-- change that keeps the race is invisible here, and the reader says so).
--
-- All [verify] for M11-03: C_BarberShop.GetAvailableCustomizations and its
-- category/option/choice shape, whether currentChoiceIndex is 1-based, the
-- BARBER_SHOP_APPEARANCE_APPLIED event, C_BarberShop.GetCurrentCharacterData's
-- `sex` field (Enum.UnitSex 0/1, not UnitSex()'s 2/3), and
-- C_BarberShop.GetViewingChrModel. The choices cover only the model being
-- viewed. The name fields that GetCurrentCharacterData returns are never read.

local _, ns = ...

local KEY = "customization"

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

-- The character's race id: the third return of UnitRace("player"); the first
-- two (localized race name and file token) are not kept.
local function raceID()
    if type(UnitRace) ~= "function" then
        return nil
    end
    return select(3, UnitRace("player"))
end

ns.Section({
    key = KEY,
    path = { "customization" },
    events = { "BARBER_SHOP_OPEN", "BARBER_SHOP_APPEARANCE_APPLIED" },
    immediate = true,
    not_gathered = "no barber-shop visit recorded with the addon enabled",
    -- Keeps the last saved record, unless it was itself an absent marker.
    -- The record is rebuilt field by field into a new table: only numbers,
    -- the two known `recorded_at` values and the addon's own constants are
    -- copied, so nothing else in the saved file (not the saved table itself,
    -- nor its old events_unregistered) is carried forward.
    -- tests/addon/test_lab_addon.py checks this shape.
    carry = function(saved)
        local r = type(saved) == "table" and saved.customization or nil
        if type(r) ~= "table" or r.absent ~= nil or type(r.choices) ~= "table" then
            return nil
        end
        local out = { as_of = "last barber-shop visit with the addon enabled", carried = true, choices = {} }
        if r.recorded_at == "open" or r.recorded_at == "applied" then
            out.recorded_at = r.recorded_at
        end
        for _, k in ipairs({ "recorded_load", "race_id", "sex", "chr_model_id" }) do
            if type(r[k]) == "number" then
                out[k] = r[k]
            end
        end
        for _, c in ipairs(r.choices) do
            if type(c) == "table" and type(c.option) == "number" then
                out.choices[#out.choices + 1] = {
                    option = c.option,
                    choice_index = type(c.choice_index) == "number" and c.choice_index or nil,
                    choice = type(c.choice) == "number" and c.choice or nil,
                }
            end
        end
        return out
    end,
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
            recorded_load = ns.probe and ns.probe.loads or nil,
            choices = choices(categories),
            race_id = raceID(),
        }
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

-- A carried record for another race no longer describes the character.
ns.On("PLAYER_ENTERING_WORLD", function()
    local record = ns.state[KEY]
    if type(record) ~= "table" or not record.carried or type(record.race_id) ~= "number" then
        return
    end
    local race = raceID()
    if type(race) == "number" and race ~= record.race_id then
        ns.state[KEY] = ns.Absent("dropped: the recorded race no longer matches the character")
    end
end)
