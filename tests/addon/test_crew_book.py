"""Static checks on the crew book's source, `lab/addon/WowLab/Crew.lua` (M19-10).

Source scans, labelled as such: nothing here runs Lua (L3, ADR-0026), so each
test grades the text of `Crew.lua`, not its behaviour. They add to the rules
M19-01 built in `test_lab_addon.py` (which this file reuses, tokenizer and
all) the three things `docs/LAB_PLAN.md` §20.1 says a scan must pin:

- nothing is registered, called or created while the book's `on` is absent
  or false (ADR-0036 term 2);
- the note rule (200 bytes after removing control characters and `|` and
  trimming spaces; a longer note refused, never cut), held in a named
  constant and two short functions;
- the drop order (5,000 players; the unrated, un-noted player with the
  oldest `last` dropped first; rated and noted players never), held in a
  named constant and one function.

The book's saved variable is named through `test_lab_addon.BOOK`, so this
file never spells it (tests/repo/test_crew_book_out_of_repo.py).
"""

from __future__ import annotations

import re

import test_lab_addon as g

CREW = g.ADDON / g.CREW_FILE
BOOK = g.BOOK


def _flow() -> g._Flow:
    return g._Flow(CREW, g._tokens(CREW))


def _function(flow: g._Flow, label: str) -> tuple[int, int]:
    """The token range (head, end) of the one function labelled `label`."""
    heads = [h for h, lab in flow.labels.items() if lab == label]
    assert len(heads) == 1, f"{label}: {len(heads)} definitions"
    return heads[0], flow.block_end(heads[0])


def _text(flow: g._Flow, label: str) -> str:
    head, end = _function(flow, label)
    return g._render(flow.tokens[head : end + 1])


def _command_on_block(flow: g._Flow) -> tuple[int, int]:
    """The `if verb == "on" then ... end` block of ns.CrewCommand."""
    head, end = _function(flow, g.CREW_COMMAND)
    t = flow.tokens
    starts = [k for k in range(head, end) if g._render(t[k : k + 5]) == 'if verb == "on" then']
    assert len(starts) == 2, starts  # the branch, then `if verb == "on" then return end`
    return starts[0], flow.block_end(starts[0])


def _block_starting(flow: g._Flow, k: int, condition: str) -> bool:
    """Is token k inside an `if` block whose header renders as `condition`?"""
    t = flow.tokens
    n = len(condition.split(" "))
    for b in flow.blocks:
        if (
            b.kind == "if"
            and b.start <= k < b.stop
            and g._render(t[b.head : b.head + n]) == condition
        ):
            return True
    return False


def test_crew_lua_exists_and_is_listed_in_the_toc() -> None:
    toc = g.parse_toc(g.TOC.read_bytes())
    files = [line.strip() for line in g.TOC.read_text(encoding="utf-8").splitlines()]
    assert g.CREW_FILE in files
    assert files.index(g.CREW_FILE) > files.index("Core.lua")
    assert toc is not None


# --- Off until turned on (§20.1, ADR-0036 term 2) -----------------------------


def test_crew_book_source_scan_nothing_registers_while_off() -> None:
    """Labelled source scan: at load only the two file-level registrations
    exist, for events Core.lua already registers (ADDON_LOADED for the
    pinned probe handler, PLAYER_ENTERING_WORLD for a gated handler); every
    other `ns.On` sits in a function behind the on and combat tests, or in
    the slash handler inside `if <book>.on == true and not armed then`."""
    flow = _flow()
    t = flow.tokens
    arm = f"if {BOOK} . on == true and not armed then"
    file_level = []
    for k in range(len(t) - 4):
        if g._render(t[k : k + 4]) != "ns . On (" or g._is_field(t, k):
            continue
        fn = flow.function_at(k)
        if fn is None:
            file_level.append((t[k + 4].text, t[k + 6].text))
        elif flow.labels.get(fn.head) == g.CREW_COMMAND:
            assert _block_starting(flow, k, arm), (
                f"line {t[k].line}: ns.On while the book may be off"
            )
        else:
            assert g._crew_gated(flow, fn.head), f"line {t[k].line}: ns.On in an ungated function"
    assert sorted(file_level) == [
        ("ADDON_LOADED", "crewLoaded"),
        ("PLAYER_ENTERING_WORLD", "onWorld"),
    ]
    assert g._crew_gated(flow, flow.resolve("onWorld", len(t) - 1).function)


def test_crew_book_source_scan_no_client_call_while_off() -> None:
    """Labelled source scan: every client name `Crew.lua` uses (the std
    file's globals) is, outside a function behind the on and combat tests,
    only `type()`-tested, or inside the pinned combat and secret helpers, or
    `time`/`date` in the slash handler (the book's creation and the status
    line's date). So with `on` absent or false the client is asked nothing
    about anyone."""
    flow = _flow()
    t = flow.tokens
    client = {name.split(".")[0] for name in g._std_globals()} - g.OWN_GLOBALS
    bad = []
    for k, x in enumerate(t):
        if x.kind != "name" or x.text not in client or g._is_field(t, k):
            continue
        if flow.resolve(x.text, k) is not None:
            continue  # a local of the same name
        fn = flow.function_at(k)
        label = flow.labels.get(fn.head) if fn else None
        if fn is not None and g._crew_gated(flow, fn.head):
            continue
        if label in {"crewBlocked", "secret"}:
            continue
        if t[k - 1].text == "(" and t[k - 2].text == "type" and t[k + 1].text == ")":
            continue
        if label == g.CREW_COMMAND and x.text in {"time", "date"}:
            continue
        bad.append(f"line {x.line}: {x.text} in {label or 'file level'}")
    assert not bad, bad


def test_crew_book_source_scan_the_book_is_created_and_turned_on_only_by_crew_on() -> None:
    """Labelled source scan: the whole book is assigned once, and `on = true`
    set once, both inside the slash handler's `on` branch; the book is
    created only when it is not a table; the on test is the pinned one, which
    needs `on == true`; the load-time probe handler is the pinned one, which
    creates nothing."""
    flow = _flow()
    t = flow.tokens
    on_start, on_end = _command_on_block(flow)
    created = [
        k
        for k in range(len(t) - 2)
        if t[k].text == BOOK and t[k + 1].text == "=" and not g._is_field(t, k)
    ]
    assert len(created) == 1 and on_start < created[0] < on_end
    assert _block_starting(flow, created[0], f'if type ( {BOOK} ) ~= "table" then')
    turned_on = [k for k in range(len(t) - 4) if g._render(t[k : k + 5]) == f"{BOOK} . on = true"]
    assert len(turned_on) == 1 and on_start < turned_on[0] < on_end
    assert "local " + _text(flow, "crewOn") == g.CREW_ON
    head, end = _function(flow, "crewLoaded")
    assert g._render(t[head - 1 : end + 1]) == g.CREW_PROBE


# --- The note rule (§20.1 "Limits") -------------------------------------------


def test_crew_book_source_scan_the_note_rule_is_named_and_refuses_not_cuts() -> None:
    """Labelled source scan: NOTE_MAX_BYTES is 200; cleanNote removes control
    characters and `|` byte by byte (`%c` is the bytes 0 to 31 and 127, so a
    multi-byte character is left alone) and then trims spaces; noteFits
    measures the cleaned bytes; the slash handler refuses a note that does not
    fit (and returns) before anything is read or stored, and stores only the
    cleaned text. Nothing in the file shortens a note (no `string.sub`)."""
    flow = _flow()
    text = CREW.read_text(encoding="utf-8")
    assert re.search(r"^local NOTE_MAX_BYTES = 200$", text, re.MULTILINE)
    assert _text(flow, "cleanNote") == (
        'function cleanNote ( text ) if type ( text ) ~= "string" then return "" end '
        'local removed = string . gsub ( text , "[%c|]" , "" ) '
        'local trimmed = string . match ( removed , "^%s*(.-)%s*$" ) '
        'return trimmed or "" end'
    )
    assert (
        _text(flow, "noteFits") == "function noteFits ( note ) return # note <= NOTE_MAX_BYTES end"
    )
    assert not re.search(r"\bsub\s*\(", text)
    command = _text(flow, g.CREW_COMMAND)
    refusal = 'if verb == "note" and not noteFits ( tidy ) then'
    assert command.count(refusal) == 1
    after = command[command.index(refusal) :]
    assert (
        after.index("Nothing was changed.") < after.index("return end") < after.index("targetKey")
    )
    assert command.index(refusal) < command.index("pcall ( pass )")
    stores = re.findall(r"entry \. note = (nil|ns \. \w+ \( \w+ \))", command)
    assert stores == ["nil", "ns . String ( tidy )"], stores


# --- The drop order (§20.1 "Limits") ------------------------------------------


def test_crew_book_source_scan_the_drop_order_is_named() -> None:
    """Labelled source scan: MAX_PLAYERS is 5,000; makeRoom walks the book,
    takes as the one to drop only an unrated, un-noted entry with the oldest
    `last`, says yes while the book holds fewer than MAX_PLAYERS, refuses
    (false, said once per session) when no one may be dropped, and otherwise
    drops that one entry from both tables; every new entry is added only
    after makeRoom said yes."""
    flow = _flow()
    text = CREW.read_text(encoding="utf-8")
    assert re.search(r"^local MAX_PLAYERS = 5000$", text, re.MULTILINE)
    room = _text(flow, "makeRoom")
    pinned = [
        f"for key , entry in pairs ( {BOOK} . players ) do kept = kept + 1 "
        'if type ( entry ) == "table" and entry . rating == nil and entry . note == nil then '
        "local last = ns . Number ( entry . last ) or 0 "
        "if oldest == nil or last < oldestLast then oldest = key oldestLast = last end end end",
        "if kept < MAX_PLAYERS then return true end",
        "if oldest == nil then if not fullSaid then fullSaid = true",
        "return false end",
        "players [ oldest ] = nil current [ oldest ] = nil return true end",
    ]
    at = 0
    for part in pinned:
        found = room.find(part, at)
        assert found >= 0, part
        at = found + len(part)
    t = flow.tokens
    for k in range(len(t) - 6):
        if g._render(t[k : k + 7]) == "[ key ] = { met = 0 }":
            line = g._render([x for x in t if x.line == t[k].line - 1])
            assert line.endswith("== nil and makeRoom ( ) then"), line


# --- What is printed (§20.1 "The warning") ------------------------------------


def test_crew_book_source_scan_every_printed_field_is_flattened() -> None:
    """Labelled source scan: `shown` removes the bidirectional embeddings and
    isolates (U+202A to U+202E, U+2066 to U+2069), removes line breaks and
    doubles `|`; and every chat line is built only from literals, numbers and
    `shown(...)` (a record can come from a desk edit, a snapshot or a merge,
    so nothing the book or the player holds is printed any other way)."""
    flow = _flow()
    text = CREW.read_text(encoding="utf-8")
    body = text[text.index("local function shown(text)") :]
    body = body[: body.index("\nend\n")]
    assert body.split("\n")[4:] == [
        '    local embeds = string.gsub(text, "\\226\\128[\\170-\\174]", "")',
        '    local isolates = string.gsub(embeds, "\\226\\129[\\166-\\169]", "")',
        '    local flat = string.gsub(isolates, "[\\r\\n]", "")',
        '    local safe = string.gsub(flat, "|", "||")',
        "    return safe",
    ], body
    t = flow.tokens
    prints = [k for k, x in enumerate(t) if x.text == "print" and not g._is_field(t, k)]
    # `say`'s, and the pinned one at the head of the slash handler (one literal).
    assert [flow.label_at(k) for k in prints] == ["say", g.CREW_COMMAND]
    k = prints[1]
    assert t[k + 2].kind == "string" and t[k + 3].text == ")"
    bad = []
    for k, x in enumerate(t):
        if (
            x.text != "say"
            or g._is_field(t, k)
            or t[k + 1].text != "("
            or t[k - 1].text == "function"
        ):
            continue
        if not _printable(flow, k + 2, flow.pair[k + 1], set()):
            bad.append(f"line {x.line}: say(...) with a part not built from literals and shown()")
    for label in _PRINTED_HELPERS:
        head, _ = _function(flow, label)
        for s_, e_ in flow.returns(head):
            if not _printable(flow, s_, e_, set()):
                bad.append(
                    f"line {t[s_].line}: {label} returns a part not built from literals and shown()"
                )
    for k, x in enumerate(t):
        if x.text == "plural" and t[k + 1].text == "(" and t[k - 1].text != "function":
            close = flow.pair[k + 1]
            args = t[k + 2 : close]
            if not (args[-1].kind == "string" and args[-3].kind == "string"):
                bad.append(f"line {x.line}: plural's words are not literals")
            # Fix round 2 (code review): plural prints its count, so the
            # count can only be a number: `ns.Number(...) or <n>`, or a local
            # every value of which is that.
            if not _a_number(flow, k + 2, close - 4):
                bad.append(f"line {x.line}: plural's count is not surely a number")
    assert not bad, bad


# Functions whose result may be printed: each of their returns is held to the
# same rule (plural's words are literals at every call; date prints a date).
_PRINTED_HELPERS = ("lineFor", "featureMissing", "fieldsMissing")
_PRINTED_CALLS = frozenset({"shown", "plural", "date", *_PRINTED_HELPERS})
_PRINTED_CONSTANTS = frozenset({"KEPT", "TERMS", "NOTE_MAX_BYTES", "MAX_PLAYERS", "nil"})


def _printable(flow: g._Flow, s: int, e: int, seen: set[int]) -> bool:
    """Is t[s:e] built only from literals, numbers (`#x`), the listed calls
    and constants, and locals every assignment of which is built the same way?"""
    t = flow.tokens
    k = s
    while k < e:
        x = t[k]
        if x.kind in {"string", "number"} or x.text in {"..", "(", ")"}:
            k += 1
            continue
        if x.text == "#" and t[k + 1].kind == "name":
            k += 2
            continue
        if x.kind == "name" and g._is_field(t, k):
            return False
        if x.kind == "name" and k + 1 < e and t[k + 1].text == "(":
            if x.text not in _PRINTED_CALLS:
                return False
            k = flow.pair[k + 1] + 1
            continue
        if x.kind == "name" and x.text in _PRINTED_CONSTANTS:
            k += 1
            continue
        if x.kind != "name" or (k + 1 < e and t[k + 1].text in {".", "[", ":"}):
            return False
        decl = flow.resolve(x.text, k)
        if decl is None or not _local_printable(flow, x.text, decl, seen):
            return False
        k += 1
    return True


_NUMBER_FORM = re.compile(r"ns \. Number \( .* \) or \d+")


def _a_number(flow: g._Flow, s: int, e: int) -> bool:
    """Is t[s:e] `ns.Number(...) or <number>`, or one local every value of
    which (its declaration and each assignment) is that?"""
    t = flow.tokens
    if _NUMBER_FORM.fullmatch(g._render(t[s:e])) and t[s + 3].text == "(":
        return flow.pair[s + 3] == e - 3
    if e != s + 1 or t[s].kind != "name":
        return False
    decl = flow.resolve(t[s].text, s)
    if decl is None:
        return False
    name = t[s].text
    values = []
    for p in range(len(t) - 3):
        if t[p].text == "local" and t[p + 1].text == name:
            end = _line_end(t, p)
            if end < len(t) and flow.resolve(name, end) is decl:
                if t[p + 2].text != "=":
                    return False
                values.append((p + 3, end))
    for root, ts in flow.rebound:
        if root == name and flow.resolve(name, ts) is decl:
            values.append((ts + 2, _line_end(t, ts)))
    return bool(values) and all(
        _NUMBER_FORM.fullmatch(g._render(t[a:b])) is not None for a, b in values
    )


def _line_end(t: list[g.Token], k: int) -> int:
    line = t[k].line
    while k < len(t) and t[k].line == line:
        k += 1
    return k


def _local_printable(flow: g._Flow, name: str, decl: g._Decl, seen: set[int]) -> bool:
    if id(decl) in seen:
        return True
    seen.add(id(decl))
    t = flow.tokens
    values = []
    declared = False
    for p in range(len(t) - 3):
        if t[p].text == "local" and t[p + 1].text == name:
            end = _line_end(t, p)
            if end >= len(t) or flow.resolve(name, end) is not decl:
                continue
            declared = True
            if t[p + 2].text == ",":
                return False  # one of several values
            if t[p + 2].text == "=":
                values.append(p + 3)
    if not declared:
        return False  # a parameter or a loop variable: not built here
    for root, ts in flow.rebound:
        if root == name and flow.resolve(name, ts) is decl:
            if t[ts + 1].text != "=" or t[ts - 1].text == ",":
                return False
            values.append(ts + 2)
    return all(_printable(flow, v, _line_end(t, v), seen) for v in values)


# --- What is read (§20.1 "Secret values", "Where") ------------------------------


def test_crew_book_source_scan_the_instance_is_secret_tested_first() -> None:
    """Labelled source scan: the statement right after the one
    GetInstanceInfo call skips (and counts) when its name, type or id is
    secret, so nothing from it is compared or kept before the test."""
    flow = _flow()
    t = flow.tokens
    calls = [k for k, x in enumerate(t) if x.text == "GetInstanceInfo" and t[k + 1].text == "("]
    assert len(calls) == 1
    k = calls[0]
    assert g._render(t[k - 17 : k + 3]) == (
        "local where , kind , _ , _ , _ , _ , _ , whereId = GetInstanceInfo ( )"
    )
    test = "if secret ( where ) or secret ( kind ) or secret ( whereId ) then addSkipped ( ) return end"
    assert g._render(t[k + 3 : k + 3 + len(test.split(" "))]) == test


# --- Ratings land on the player named (§20.1 "Rating and note in game") --------


def test_crew_book_source_scan_a_word_without_a_dash_is_refused_not_the_target() -> None:
    """Labelled source scan: `good`, `bad` and `clear` given a first word
    with no `-` are refused before the target is read (so `/wowlab crew bad
    Bob` never rates whoever is targeted); a note given to the target names
    the target in its reply."""
    flow = _flow()
    command = _text(flow, g.CREW_COMMAND)
    refusal = 'if not typed and verb ~= "note" and tail ~= "" then'
    assert command.count(refusal) == 1
    after = command[command.index(refusal) :]
    assert (
        after.index("Nothing was changed.") < after.index("return end") < after.index("targetKey")
    )
    assert 'whom = "your target, " .. shown ( found )' in command
    assert 'say ( whom .. ": note kept. " .. KEPT )' in command


# --- `met` counts each group once (§20.1 "met") --------------------------------


def test_crew_book_source_scan_off_and_on_forget_the_group_counted_so_far() -> None:
    """Labelled source scan (fix round 2, code review): turning the book off,
    and turning an off book on, empties `current`, so a group joined while the
    book was off is counted (and warned about) once it is on."""
    flow = _flow()
    command = _text(flow, g.CREW_COMMAND)
    empty = f"local current = {BOOK} . current for key in pairs ( {BOOK} . current ) do current [ key ] = nil end"
    off = command[command.index('if verb == "off" then') :]
    assert empty in off[: off.index("return end")]
    on = command[command.index(f"elseif {BOOK} . on ~= true then") :]
    assert on.index(empty) < on.index(f"{BOOK} . on = true")
