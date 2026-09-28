-- Currencies (docs/LAB_PLAN.md §13.1): id and quantity, and the total cap,
-- weekly cap, weekly earned and account-wide flag raw where the client gives
-- them. Weekly values follow the region's reset (docs/GLOSSARY.md). The list
-- is the currency panel's, read through its collapsed headers and filter,
-- which the addon never changes: currencies under a collapsed header are
-- missing, and the record counts the collapsed headers it saw. Every API here
-- is [verify] for M11-03 (present on 69893 per forever-addon-kit's API
-- baseline). Currency names are not recorded.

local _, ns = ...

local function currencyID(info, index)
    if type(info.currencyID) == "number" then
        return info.currencyID
    end
    local link = ns.Fn(C_CurrencyInfo, "GetCurrencyListLink")
    local fromLink = ns.Fn(C_CurrencyInfo, "GetCurrencyIDFromLink")
    if link and fromLink then
        local text = ns.Call(link, index)
        if type(text) == "string" then
            return ns.Call(fromLink, text)
        end
    end
    return nil
end

local function describe(id, getInfo, accountWide)
    local info = ns.Call(getInfo, id)
    if type(info) ~= "table" then
        return { id = id, absent = "C_CurrencyInfo.GetCurrencyInfo returned nothing" }
    end
    local entry = {
        id = id,
        quantity = info.quantity,
        max_quantity = info.maxQuantity,
        max_weekly_quantity = info.maxWeeklyQuantity,
        earned_this_week = info.quantityEarnedThisWeek,
        can_earn_per_week = info.canEarnPerWeek,
        total_earned = info.totalEarned,
        use_total_earned_for_max = info.useTotalEarnedForMaxQty,
        account_wide = info.isAccountWide,
    }
    if entry.account_wide == nil and accountWide then
        entry.account_wide = ns.Call(accountWide, id)
    end
    return entry
end

ns.Section({
    key = "currencies",
    path = { "currencies" },
    events = { "CURRENCY_DISPLAY_UPDATE" },
    on_world = true,
    gather = function()
        local size = ns.Fn(C_CurrencyInfo, "GetCurrencyListSize")
        local listInfo = ns.Fn(C_CurrencyInfo, "GetCurrencyListInfo")
        local getInfo = ns.Fn(C_CurrencyInfo, "GetCurrencyInfo")
        if not (size and listInfo and getInfo) then
            return ns.Absent("C_CurrencyInfo list or info functions missing")
        end
        local accountWide = ns.Fn(C_CurrencyInfo, "IsAccountWideCurrency")
        local ids, seen, collapsed = {}, {}, 0
        local rows = ns.Call(size)
        for index = 1, type(rows) == "number" and rows or 0 do
            local info = ns.Call(listInfo, index)
            if type(info) == "table" then
                if info.isHeader then
                    if not info.isHeaderExpanded then
                        collapsed = collapsed + 1
                    end
                else
                    local id = currencyID(info, index)
                    if type(id) == "number" and not seen[id] then
                        seen[id] = true
                        ids[#ids + 1] = id
                    end
                end
            end
        end
        table.sort(ids)
        local list = {}
        for _, id in ipairs(ids) do
            list[#list + 1] = describe(id, getInfo, accountWide)
        end
        local record = { list = list, filtered = true, headers_collapsed = collapsed }
        local filter = ns.Fn(C_CurrencyInfo, "GetCurrencyFilter")
        if filter then
            record.filter = ns.Call(filter)
        end
        return record
    end,
})
