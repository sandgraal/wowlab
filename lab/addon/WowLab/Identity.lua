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
-- - realm: GetRealmName(), the realm's display name; since M20-16, when it
--   gives nothing usable, GetNormalizedRealmName() and then the second
--   return of UnitFullName("player"), with `realm_from` naming the source
--   and `realm_normalized` set for the last two. A record with no realm
--   keeps the section dirty (`again_without`), so a later pass, at logout at
--   the latest, asks again; at logout its record replaces this one only when
--   it has a realm (Core.lua, M20-17). Since M20-17 `realm_tried` holds what
--   each source asked before the answering one gave (nil, an empty string,
--   another type, or a secret value), and `realm_absent` the same for all
--   three;
-- - guild: the first return of GetGuildInfo("player"); nil means no guild,
--   or a guild the client has not loaded yet [verify]. PLAYER_GUILD_UPDATE
--   gathers the section again.
-- Each value is tested with issecretvalue (`secret`) before it is compared
-- or stored; one it calls secret, or cannot say about, is not kept. All six
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

-- The text the client gave, or nil and the reason it is not kept. M20-17:
-- the reason names the kind of nothing (a secret value, an empty string, or
-- the type it was: nil, a number, ...); `type` reads only the value's kind.
local function ownText(value, api)
    if secret(value) then
        return nil, api .. " was a value the client calls secret"
    end
    if type(value) == "string" and value ~= "" then
        return value, nil
    end
    if type(value) == "string" then
        return nil, api .. " was an empty string"
    end
    return nil, api .. " was " .. type(value) .. ", not a non-empty string"
end

ns.Section({
    key = "identity",
    path = { "identity" },
    -- PLAYER_GUILD_UPDATE (schema 5, [verify]): the guild is nil until the
    -- client has its guild data, which may come after the first pass, and
    -- joining or leaving a guild changes it.
    events = { "BARBER_SHOP_APPEARANCE_APPLIED", "PLAYER_GUILD_UPDATE" },
    on_world = true,
    -- M20-16: a record with no realm (none of the three sources gave one at
    -- this gather) keeps the section dirty, so the next pass, at logout at
    -- the latest, asks again; a realm found then replaces the absent one.
    again_without = "realm",
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
        -- The realm (M20-16): the first of three sources that gives a
        -- non-empty text the client does not call secret, and which one it
        -- was in `realm_from`: "realm_name" (GetRealmName, the display
        -- name), "normalized_realm_name" (GetNormalizedRealmName) or
        -- "full_name" (the second return of UnitFullName("player")). The last
        -- two give the normalized form, with no spaces ([verify]), and
        -- `realm_normalized` says so. None: `realm_absent` holds each
        -- source's reason. On the owner's run on 71037 (2026-11-20),
        -- GetRealmName gave nothing usable at the first pass.
        local realmWhy = ""
        if type(GetRealmName) ~= "function" then
            realmWhy = realmWhy .. "; the client has no GetRealmName function"
        else
            local realm = GetRealmName()
            local keptRealm, whyRealm = ownText(realm, "the return of GetRealmName")
            if keptRealm then
                record.realm = ns.String(keptRealm)
                record.realm_from = "realm_name"
            else
                realmWhy = realmWhy .. "; " .. ns.String(whyRealm)
            end
        end
        if not record.realm then
            if type(GetNormalizedRealmName) ~= "function" then
                realmWhy = realmWhy .. "; the client has no GetNormalizedRealmName function"
            else
                local normalized = GetNormalizedRealmName()
                local keptNormalized, whyNormalized =
                    ownText(normalized, "the return of GetNormalizedRealmName")
                if keptNormalized then
                    record.realm = ns.String(keptNormalized)
                    record.realm_from = "normalized_realm_name"
                    record.realm_normalized = true
                else
                    realmWhy = realmWhy .. "; " .. ns.String(whyNormalized)
                end
            end
        end
        if not record.realm then
            if type(UnitFullName) ~= "function" then
                realmWhy = realmWhy .. "; the client has no UnitFullName function"
            else
                local fullRealm = select(2, UnitFullName("player"))
                local keptFull, whyFull = ownText(fullRealm, "the second return of UnitFullName")
                if keptFull then
                    record.realm = ns.String(keptFull)
                    record.realm_from = "full_name"
                    record.realm_normalized = true
                else
                    realmWhy = realmWhy .. "; " .. ns.String(whyFull)
                end
            end
        end
        if not record.realm then
            record.realm_absent = ns.String(string.sub(realmWhy, 3))
        elseif realmWhy ~= "" then
            -- M20-17: what each source asked before `realm_from` gave.
            record.realm_tried = ns.String(string.sub(realmWhy, 3))
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
