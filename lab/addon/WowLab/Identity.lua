-- The character's identity (schema 4, M15-01; docs/LAB_PLAN.md §16.3,
-- amendment of 2026-10-19): race id, body type and class, so a reader that
-- needs them (the dressing-room link, M15-02) does not depend on a
-- barber-shop visit. Gathered on entering the world like the other on-world
-- sections, and again on BARBER_SHOP_APPEARANCE_APPLIED (a body type can
-- change there). That the event fires is confirmed (70425, M11-29 capture);
-- that UnitSex already gives the new body type at the debounced gather 2 s
-- after it is [verify], and not tested by the M15-01 capture: a gather that
-- is too early is not repeated at logout (the section is no longer dirty).
--
-- Each value is exactly one of the value and `<field>_absent`, a reason
-- naming the API and what it did; nothing is guessed:
-- - race_id: the third return of UnitRace("player"), as Customization.lua
--   reads it. The localized name and file token are not kept.
-- - sex_raw: the first return of UnitSex("player") as the client gives it
--   (1 unknown, 2 male, 3 female, from memory: [verify]). The addon does not
--   map it; the Lab's reader derives the barber shop's Enum.UnitSex value
--   (0/1) from it, so the raw value is kept. C_BarberShop's `sex`, which
--   Customization.lua records, is called only in the barber shop (how it
--   answers outside is untested).
-- - class_id and class_file: the third and second returns of
--   UnitClass("player"). The first (the localized class name) is not kept.
--
-- All three ran on 70450 (M15-01 capture; lab/addon/README.md, API status;
-- docs/LAB_FORMATS.md, amendment of 2026-10-19). UnitSex 3 and 1 are not
-- yet seen: [verify].
--
-- Schema 5 (M20-13; ADR-0026, note of 2026-11-20): the character's own
-- GUID, name, realm and guild, as the client gives them, each exactly one of
-- the value and `<field>_absent`:
-- - guid: UnitGUID("player");
-- - name: the first return of UnitName("player");
-- - realm: GetRealmName(), the realm's display name;
-- - guild: the first return of GetGuildInfo("player"); nil means no guild,
--   or a guild the client has not loaded yet [verify].
-- Each value is tested with issecretvalue (`secret`) before it is compared
-- or stored; one it calls secret, or cannot say about, is not kept. All four
-- calls are [verify] on Forever until the owner's schema-5 capture.

local _, ns = ...

-- True when the client calls a value secret or cannot say; false when it has
-- no issecretvalue (the values are then not tested, not known not secret).
local function secret(value)
    if type(issecretvalue) ~= "function" then
        return false
    end
    local ok, hidden = pcall(issecretvalue, value)
    return not ok or hidden == true
end

-- The text the client gave, or nil and the reason it is not kept.
local function ownText(value, api)
    if secret(value) then
        return nil, api .. " was a value the client calls secret"
    end
    if type(value) == "string" and value ~= "" then
        return value, nil
    end
    return nil, api .. " was not a non-empty string"
end

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
        -- Schema 5: the character's own GUID, name, realm and guild.
        if type(UnitGUID) ~= "function" then
            record.guid_absent = "the client has no UnitGUID function"
        else
            local guid = UnitGUID("player")
            local keptGuid, whyGuid = ownText(guid, "the return of UnitGUID")
            record.guid = ns.String(keptGuid)
            record.guid_absent = ns.String(whyGuid)
        end
        if type(UnitName) ~= "function" then
            record.name_absent = "the client has no UnitName function"
        else
            local name = UnitName("player")
            local keptName, whyName = ownText(name, "the first return of UnitName")
            record.name = ns.String(keptName)
            record.name_absent = ns.String(whyName)
        end
        if type(GetRealmName) ~= "function" then
            record.realm_absent = "the client has no GetRealmName function"
        else
            local realm = GetRealmName()
            local keptRealm, whyRealm = ownText(realm, "the return of GetRealmName")
            record.realm = ns.String(keptRealm)
            record.realm_absent = ns.String(whyRealm)
        end
        if type(GetGuildInfo) ~= "function" then
            record.guild_absent = "the client has no GetGuildInfo function"
        else
            local guild = GetGuildInfo("player")
            local keptGuild, whyGuild = ownText(guild, "the first return of GetGuildInfo")
            record.guild = ns.String(keptGuild)
            record.guild_absent = ns.String(whyGuild)
        end
        return record
    end,
})
