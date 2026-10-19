-- The character's identity (schema 4, M15-01; docs/LAB_PLAN.md §16.3,
-- amendment of 2026-10-19): race id, body type and class, so a reader that
-- needs them (the dressing-room link, M15-02) does not depend on a
-- barber-shop visit. Gathered on entering the world like the other on-world
-- sections, and again after an applied barber-shop change (a body type can
-- change there), so the record written at logout or /reload is the one the
-- client gave last.
--
-- Each value is exactly one of the value and `<field>_absent`, a reason
-- naming the API and what it did; nothing is guessed:
-- - race_id: the third return of UnitRace("player"), as Customization.lua
--   reads it. The localized name and file token are not kept.
-- - sex_raw: the first return of UnitSex("player") as the client gives it
--   (1 unknown, 2 male, 3 female, from memory: [verify]). The addon does not
--   map it; the Lab's reader derives the barber shop's Enum.UnitSex value
--   (0/1) from it, so the raw value is kept. C_BarberShop's `sex`, which
--   Customization.lua records, answers only in the barber shop.
-- - class_id and class_file: the third and second returns of
--   UnitClass("player"). The first (the localized class name) is not kept.
--
-- All [verify] until an owner capture shows them ran (lab/addon/README.md,
-- API status; docs/handoffs/M15-01.md).

local _, ns = ...

ns.Section({
    key = "identity",
    path = { "identity" },
    events = { "BARBER_SHOP_APPEARANCE_APPLIED" },
    on_world = true,
    gather = function()
        local record = {}
        if type(UnitRace) ~= "function" then
            record.race_id_absent = "the client has no UnitRace function"
        else
            local race = select(3, UnitRace("player"))
            if type(race) == "number" then
                record.race_id = race
            else
                record.race_id_absent = "the third return of UnitRace was not a number"
            end
        end
        if type(UnitSex) ~= "function" then
            record.sex_raw_absent = "the client has no UnitSex function"
        else
            local sex = UnitSex("player")
            if type(sex) == "number" then
                record.sex_raw = sex
            else
                record.sex_raw_absent = "the first return of UnitSex was not a number"
            end
        end
        if type(UnitClass) ~= "function" then
            record.class_id_absent = "the client has no UnitClass function"
            record.class_file_absent = "the client has no UnitClass function"
        else
            local classFile, classID = select(2, UnitClass("player"))
            if type(classID) == "number" then
                record.class_id = classID
            else
                record.class_id_absent = "the third return of UnitClass was not a number"
            end
            if type(classFile) == "string" and classFile ~= "" then
                record.class_file = classFile
            else
                record.class_file_absent = "the second return of UnitClass was not a non-empty string"
            end
        end
        return record
    end,
})
