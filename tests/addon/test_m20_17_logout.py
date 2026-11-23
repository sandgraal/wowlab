"""M20-17: the logout pass, the crew book's home realm, and the identity's
realm diagnosis (the owner's second schema-5 run, Forever 71037, 2026-11-20).

Source scans of the real addon; nothing here runs Lua (L3). Every
constructed edit below is a boundary test of one kept rule: an edit of the
real source that must fail for that rule's own reason (a word its report
must hold). No name in this file is the owner's.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import test_lab_addon as g

CORE = g.ADDON / "Core.lua"
CREW = g.ADDON / g.CREW_FILE
IDENTITY = g.ADDON / "Identity.lua"

# The PLAYER_LOGOUT handler: out of combat, the logout pass, then the write.
LOGOUT_HANDLER = (
    "function ( ) if not ns . probe then return end "
    'if not inCombat then gatherAtLogout ( ) end ns . Write ( "logout" ) end'
)
# The logout pass, pinned whole: a dirty section with no record from this
# session is gathered and marked; one whose record lacks its `again_without`
# field is read again, the session's record and time are always put back,
# and (round 1) only the section's `again_fields` are copied from the new
# read, when it has the field. No other section is read at logout.
LOGOUT_PASS = (
    "function gatherAtLogout ( ) for _ , section in ipairs ( ns . sections ) do "
    "if section . dirty and not section . gathered then gather ( section ) "
    "if section . gathered then gatheredAtLogout [ section . key ] = true end "
    "elseif section . dirty and lacksAgain ( section ) then "
    "local held , heldAt = ns . state [ section . key ] , gatheredAt [ section . key ] "
    "gather ( section ) local fresh = ns . state [ section . key ] "
    "ns . state [ section . key ] = held gatheredAt [ section . key ] = heldAt "
    'if type ( fresh ) == "table" and fresh [ section . again_without ] ~= nil then '
    "takeAgain ( section , held , fresh ) againAtLogout [ section . key ] = true "
    "end end end end"
)
TAKE_AGAIN = (
    "function takeAgain ( section , held , fresh ) "
    "for _ , field in ipairs ( section . again_fields or { } ) do "
    'if type ( field ) == "string" then local value = fresh [ field ] '
    'if type ( value ) == "boolean" then held [ field ] = ns . Bool ( value ) '
    "else held [ field ] = ns . String ( value ) end end end end"
)
# Identity's fields a logout read supplies: the realm and its own keys only.
AGAIN_FIELDS = (
    'again_fields = { "realm" , "realm_absent" , "realm_from" , '
    '"realm_normalized" , "realm_tried" }'
)
AGAIN_ENTRY = (
    "if againAtLogout [ section . key ] then "
    "entry . again_at_logout = ns . String ( section . again_without ) end"
)
LACKS_AGAIN = (
    "function lacksAgain ( section ) local held = ns . state [ section . key ] "
    'return section . again_without ~= nil and type ( held ) == "table" '
    "and held [ section . again_without ] == nil end"
)
AT_LOGOUT_ENTRY = "if gatheredAtLogout [ section . key ] then entry . at_logout = true end"


def logout_problems(path: Path) -> list[str]:
    """Why the logout pass in `path` (Core.lua, or an edited copy) may
    replace a section already gathered this session, or not say which
    sections it gathered."""
    flow = g._Flow(path, g._tokens(path))
    rendered = g._render(flow.tokens)
    problems: list[str] = []
    if g._body(flow, 'ns.On("PLAYER_LOGOUT")') != LOGOUT_HANDLER:
        problems.append("the logout handler does not run only the logout pass, out of combat")
    if g._body(flow, "gatherAtLogout") != LOGOUT_PASS:
        problems.append("the logout pass is not the pinned one: it may replace a gathered section")
    if g._body(flow, "lacksAgain") != LACKS_AGAIN:
        problems.append("lacksAgain is not the pinned one: the logout pass may replace a record")
    if rendered.count("gatherAtLogout ( )") != 2:  # its definition and its one call
        problems.append("the logout pass is called other than once, by the logout handler")
    if AT_LOGOUT_ENTRY not in g._body(flow, "gatheredList"):
        problems.append("`gathered` does not mark a section the logout pass gathered")
    if rendered.count("gatheredAtLogout [ section . key ] = true") != 1:
        problems.append("gatheredAtLogout is set other than by the logout pass")
    # Round 1: a re-read at logout supplies only the listed fields.
    if g._body(flow, "takeAgain") != TAKE_AGAIN or rendered.count("takeAgain (") != 2:
        problems.append("takeAgain is not the pinned one: a re-read may replace the whole record")
    if AGAIN_ENTRY not in g._body(flow, "gatheredList"):
        problems.append("`gathered` does not mark the fields a re-read at logout supplied")
    if rendered.count("againAtLogout [ section . key ] = true") != 1:
        problems.append("againAtLogout is set other than by the logout pass")
    return problems


def again_fields_problems(path: Path) -> list[str]:
    """Identity.lua (or an edited copy) lists only the realm's keys as what a
    re-read at logout supplies, once."""
    text = g._render(g._tokens(path))
    if text.count("again_fields =") != 1 or AGAIN_FIELDS not in text:
        return ["identity's again_fields are not the realm's keys: a re-read may replace more"]
    return []


def test_m20_17_a_re_read_at_logout_supplies_only_the_realm() -> None:
    assert again_fields_problems(IDENTITY) == []


def test_m20_17_the_logout_pass_never_replaces_a_gathered_section() -> None:
    assert logout_problems(CORE) == []


def _crew(path: Path) -> list[str]:
    return g._crew_problems(path, path.read_text(encoding="utf-8"))


_HOME_HEAD = (
    "local function homeRealm()\n    if not crewOn() then\n        return\n    end\n"
    "    if crewBlocked() then\n        return\n    end\n"
)

# (the file, its edits, the check, a word the check's report must hold).
EDITS: dict[str, tuple[Path, list[tuple[str, str]], Callable[[Path], list[str]], str]] = {
    # The new rule: a logout pass that overwrites a section already gathered.
    "logout: the handler gathers every dirty section": (
        CORE,
        [("        gatherAtLogout()\n    end\n", "        gatherDirty()\n    end\n")],
        logout_problems,
        "the logout handler",
    ),
    "logout: the pass gathers a section already gathered": (
        CORE,
        [
            (
                "        if section.dirty and not section.gathered then\n",
                "        if section.dirty then\n",
            )
        ],
        logout_problems,
        "may replace a gathered section",
    ),
    "logout: the session's record is not put back": (
        CORE,
        [("            ns.state[section.key] = held\n", "")],
        logout_problems,
        "may replace a gathered section",
    ),
    # Round 1: a re-read at logout replaces the whole record.
    "logout: the re-read replaces the whole record": (
        CORE,
        [
            (
                "                takeAgain(section, held, fresh)\n",
                "                ns.state[section.key] = fresh\n",
            )
        ],
        logout_problems,
        "may replace a gathered section",
    ),
    "logout: takeAgain copies every field of the re-read": (
        CORE,
        [
            (
                "    for _, field in ipairs(section.again_fields or {}) do\n",
                "    for field in pairs(fresh) do\n",
            )
        ],
        logout_problems,
        "takeAgain is not the pinned one",
    ),
    "logout: identity's re-read supplies the guild too": (
        IDENTITY,
        [('"realm_tried" },', '"realm_tried", "guild" },')],
        again_fields_problems,
        "again_fields are not the realm's keys",
    ),
    "logout: gathered no longer marks the re-read's fields": (
        CORE,
        [("                entry.again_at_logout = ns.String(section.again_without)\n", "")],
        logout_problems,
        "does not mark the fields",
    ),
    "logout: lacksAgain always says the record lacks the field": (
        CORE,
        [
            (
                '    return section.again_without ~= nil and type(held) == "table" and '
                "held[section.again_without] == nil\n",
                "    return true\n",
            )
        ],
        logout_problems,
        "lacksAgain",
    ),
    "logout: a second call of the pass": (
        CORE,
        [("        debugCommand(key)\n", "        gatherAtLogout()\n        debugCommand(key)\n")],
        logout_problems,
        "called other than once",
    ),
    "logout: gathered no longer marks the logout pass's sections": (
        CORE,
        [("                entry.at_logout = true\n", "")],
        logout_problems,
        "does not mark",
    ),
    # Nothing in combat: the logout pass, too, stands behind its guard.
    "nothing in combat: the logout pass called from the slash handler": (
        CORE,
        [("        debugCommand(key)\n", "        gatherAtLogout()\n        debugCommand(key)\n")],
        g._m20_13_core_combat,
        "gatherAtLogout() in WOWLAB with no combat guard pinned",
    ),
    "nothing in combat: the logout pass gathers in combat": (
        CORE,
        [
            (
                "    if not inCombat then\n        gatherAtLogout()\n    end\n",
                "    gatherAtLogout()\n",
            )
        ],
        g._m20_13_core_combat,
        "PLAYER_LOGOUT",
    ),
    # Crew.lua's gate and secret test, on the home realm (M20-17).
    "crew gate: homeRealm without the on test": (
        CREW,
        [
            (
                _HOME_HEAD,
                _HOME_HEAD.replace("    if not crewOn() then\n        return\n    end\n", ""),
            )
        ],
        _crew,
        "is not behind the on and combat tests",
    ),
    "crew gate: homeRealm without the combat test": (
        CREW,
        [
            (
                _HOME_HEAD,
                _HOME_HEAD.replace("    if crewBlocked() then\n        return\n    end\n", ""),
            )
        ],
        _crew,
        "is not behind the on and combat tests",
    ),
    # M21-11: the full name's second value left the crew book's home realm;
    # its two secret-test edits went with it.
    "crew secret: the normalized realm stored before the test": (
        CREW,
        [
            (
                "        local normalized = GetNormalizedRealmName()\n",
                "        local normalized = GetNormalizedRealmName()\n"
                "        homeFound = normalized\n",
            )
        ],
        _crew,
        "normalized (from GetNormalizedRealmName) is used before the secret test",
    ),
    # The own-value calls stay confined: UnitFullName outside Identity.lua
    # and Crew.lua.
    "own values: UnitFullName read in Gear.lua": (
        g.ADDON / "Gear.lua",
        [("local _, ns = ...\n", 'local _, ns = ...\nlocal _, home = UnitFullName("player")\n')],
        g._m20_13_own_value_problems,
        "UnitFullName outside Identity.lua",
    ),
    # Identity.lua's secret test before the new reasons.
    "secret test: ownText reads the type before the test": (
        IDENTITY,
        [
            (
                "    if secret(value) then\n",
                '    if type(value) == "nil" then\n        return nil, api\n    end\n'
                "    if secret(value) then\n",
            )
        ],
        g._m20_13_identity_problems,
        "ownText is not the pinned one",
    ),
}


def test_m20_17_each_rule_fails_its_own_edit() -> None:
    missed = []
    for tag, (path, edits, check, word) in sorted(EDITS.items()):
        problems = g._m20_13_edited(path, edits, check)
        if not any(word in problem for problem in problems):
            missed.append((tag, problems))
    assert not missed, missed


def test_m20_17_the_crew_home_realm_is_one_rule_for_every_key() -> None:
    """Every same-realm key (the roster pass, the tooltip, the target, the
    match) takes the realm from homeRealm, which strips spaces and hyphens
    from GetNormalizedRealmName's answer; nothing else in Crew.lua asks a
    realm call. M21-11: the second return of UnitFullName("player") is no
    longer a source (on Forever it is the owner's second name)."""
    text = g._render(g._tokens(CREW))
    assert text.count("GetNormalizedRealmName ( )") == 1
    assert "UnitFullName" not in text
    assert text.count('string . gsub ( normalized , "[%s%-]" , "" )') == 1
    assert text.count("homeRealm ( )") == 6  # the definition and five uses
    assert text.count("local home = homeRealm ( )") == 3
    assert "local home , hidden = homeRealm ( )" in text
    # Kept only once found: the cache is set only to a non-empty bare text.
    assert text.count("homeFound = bare") == 1
    assert text.count("homeFound =") == 2  # and its declaration, `= nil`
    assert "local homeFound = nil" in text
    assert text.count('if bare ~= "" then homeFound = bare return homeFound , false end') == 1
    # `/wowlab crew` says when no home realm could be found.
    assert "if homeRealm ( ) == nil then missing = missing .." in text


def test_m20_17_identity_keeps_what_the_other_realm_sources_gave() -> None:
    text = g._render(g._tokens(IDENTITY))
    assert (
        "if not record . realm then record . realm_absent = ns . String ( string . sub ( "
        'realmWhy , 3 ) ) elseif realmWhy ~= "" then '
        "record . realm_tried = ns . String ( string . sub ( realmWhy , 3 ) ) end"
    ) in text
