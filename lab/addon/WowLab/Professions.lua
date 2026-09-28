-- Professions (docs/LAB_PLAN.md §13.1): skill-line id, rank, maximum and
-- modifier as the client gives them. Forever's profession model is [verify];
-- GetProfessions and GetProfessionInfo are present on 69893 per
-- forever-addon-kit's API baseline, and their returns there are [verify].
-- `position` is the place in GetProfessions' returns (Retail: primary,
-- secondary, archaeology, fishing, cooking). Profession names are not kept.

local _, ns = ...

ns.Section({
    key = "professions",
    path = { "professions" },
    events = { "SKILL_LINES_CHANGED" },
    on_world = true,
    gather = function()
        if type(GetProfessions) ~= "function" or type(GetProfessionInfo) ~= "function" then
            return ns.Absent("GetProfessions or GetProfessionInfo missing")
        end
        local slots = ns.Pack(GetProfessions())
        local list = {}
        for position = 1, slots.n do
            local index = slots[position]
            if type(index) == "number" then
                -- Returns 3, 4, 7 and 8: rank, maximum, skill line, modifier.
                local rank, maximum, _, _, skillLine, modifier = select(3, GetProfessionInfo(index))
                list[#list + 1] = {
                    position = position,
                    skill_line = skillLine,
                    rank = rank,
                    max_rank = maximum,
                    modifier = modifier,
                }
            end
        end
        return { list = list }
    end,
})
