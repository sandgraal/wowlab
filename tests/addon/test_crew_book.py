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
    assert len(starts) == 1, starts
    return starts[0], flow.block_end(starts[0])


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
    the `on` branch of the slash handler, after the book is on."""
    flow = _flow()
    t = flow.tokens
    on_start, on_end = _command_on_block(flow)
    file_level = []
    for k in range(len(t) - 4):
        if g._render(t[k : k + 4]) != "ns . On (" or g._is_field(t, k):
            continue
        fn = flow.function_at(k)
        if fn is None:
            file_level.append((t[k + 4].text, t[k + 6].text))
        elif flow.labels.get(fn.head) == g.CREW_COMMAND:
            sets_on = [
                j
                for j in range(on_start, k)
                if g._render(t[j : j + 5]) in {f"{BOOK} . on = true", f"{BOOK} = {{ schema"}
            ]
            assert on_start < k < on_end and sets_on, (
                f"line {t[k].line}: ns.On outside the on branch"
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
    guard = g._render(t[created[0] - 8 : created[0]])
    assert guard == f'if type ( {BOOK} ) ~= "table" then', guard
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
    """Labelled source scan: `shown` removes line breaks and doubles `|`, and
    every name, guild, note or typed text the file concatenates into a chat
    line is wrapped in it at that moment (a record can come from a desk edit,
    a snapshot or a merge)."""
    flow = _flow()
    assert _text(flow, "shown") == (
        'function shown ( text ) if type ( text ) ~= "string" then return "" end '
        'local flat = string . gsub ( text , "[\r\n]" , "" ) '
        'local safe = string . gsub ( flat , "|" , "||" ) return safe end'
    )
    t = flow.tokens
    bad = []
    for k, x in enumerate(t):
        field = x.text in {"guild", "note"} and g._is_field(t, k) and t[k - 2].text == "entry"
        local = x.text in {"label", "wanted", "found"} and not g._is_field(t, k)
        if not (field or local):
            continue
        start = k - 2 if field else k
        concatenated = t[start - 1].text == ".." or t[k + 1].text == ".."
        printed = t[start - 1].text == "(" and t[start - 2].text in {"say", "print"}
        if (concatenated or printed) and not (
            t[start - 1].text == "(" and t[start - 2].text == "shown"
        ):
            bad.append(f"line {x.line}: {x.text}")
    assert not bad, bad
