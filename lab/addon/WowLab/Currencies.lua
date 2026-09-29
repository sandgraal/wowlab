-- Currencies (docs/LAB_PLAN.md §13.1): id and quantity, and the total cap,
-- weekly cap, weekly earned and account-wide flag raw where the client gives
-- them. Weekly values follow the region's reset (docs/GLOSSARY.md). The list
-- is the currency panel's, read through its collapsed headers and filter,
-- which the addon never changes: currencies under a collapsed header are
-- missing, and the record counts the header rows and collapsed headers it
-- saw and the rows the panel gave (`rows`; that it counts header rows and
-- leaves out rows under a collapsed header is [verify]). Every API here
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
        return { id = ns.Number(id), absent = "C_CurrencyInfo.GetCurrencyInfo returned nothing" }
    end
    local entry = {
        id = ns.Number(id),
        quantity = ns.Number(info.quantity),
        max_quantity = ns.Number(info.maxQuantity),
        max_weekly_quantity = ns.Number(info.maxWeeklyQuantity),
        earned_this_week = ns.Number(info.quantityEarnedThisWeek),
        can_earn_per_week = ns.Bool(info.canEarnPerWeek),
        total_earned = ns.Number(info.totalEarned),
        use_total_earned_for_max = ns.Bool(info.useTotalEarnedForMaxQty),
        account_wide = ns.Bool(info.isAccountWide),
    }
    if entry.account_wide == nil and accountWide then
        entry.account_wide = ns.Bool(ns.Call(accountWide, id))
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
        local ids, seen, headers, collapsed = {}, {}, 0, 0
        local rows = ns.Call(size)
        for index = 1, type(rows) == "number" and rows or 0 do
            local info = ns.Call(listInfo, index)
            if type(info) == "table" then
                if info.isHeader then
                    headers = headers + 1
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
        -- rows: the count GetCurrencyListSize gave (header rows included,
        -- rows under a collapsed header left out: [verify]); headers: every
        -- header row seen. rows - headers - #list is the rows whose id could
        -- not be read. rows is missing when that call raised an error or
        -- returned no number (M11-22).
        local record = {
            list = list,
            filtered = true,
            headers = headers,
            headers_collapsed = collapsed,
            rows = ns.Number(rows),
        }
        local filter = ns.Fn(C_CurrencyInfo, "GetCurrencyFilter")
        if filter then
            record.filter = ns.Number(ns.Call(filter))
        end
        return record
    end,
})
