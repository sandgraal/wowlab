"""`wowlab_core.spy` on constructed files: refusals, versions and the shapes
the capture does not hold (M22-01).

CONSTRUCTED INPUT. Every file here is built in the test, in the layout of
the real capture (`test_spy_fixtures.py`: CRLF, a leading empty line, one
entry per line). The names are invented. They cover what the owner's
files do not hold yet: kill-on-sight, ignore and fight entries, Spy's win
and loss counts and reasons, notes and tags, a field this reader does not
know, a value of the wrong type, an unknown or missing version, and files
that are not data.
"""

from __future__ import annotations

import pytest

from wowlab_core import luadata, spy

pytestmark = pytest.mark.parser

NL = "\r\n"
KEY = "Inventa-Fakerealm"
OTHER = "Inventb-Fakerealm"
SOURCE = spy.SpySource(file="x/Spy.lua", account="ACCT", character="1/Mine-Fakerealm")


def _char(body: str, version: str | None = '"1.1"') -> bytes:
    lines = ["", "SpyPerCharDB = {"]
    if version is not None:
        lines.append(f'["version"] = {version},')
    lines.append(body)
    lines.append("}")
    return (NL.join(lines) + NL).encode("utf-8")


def _row(**fields: str) -> str:
    inner = NL.join(f'["{k}"] = {v},' for k, v in fields.items())
    return "{" + NL + inner + NL + "}"


def test_constructed_kos_ignore_wins_reasons_and_an_unknown_field() -> None:
    row = _row(
        name=f'"{KEY}"',
        level="29",
        isGuess="true",
        wins="3",
        loses="1",
        rank="5",
        newField='"kept"',
        reason="{"
        + NL
        + '["Ganked me"] = true,'
        + NL
        + '["Other"] = "camped the flight master",'
        + NL
        + "}",
    )
    data = _char(
        f'["PlayerData"] = {{{NL}["{KEY}"] = {row},{NL}}},{NL}'
        f'["KOSData"] = {{{NL}["{KEY}"] = 1795400000,{NL}["{OTHER}"] = 1795400001,{NL}}},{NL}'
        f'["IgnoreData"] = {{{NL}["{OTHER}"] = true,{NL}}},'
    )
    got = spy.parse_character(data, SOURCE)
    assert got.warning is None
    (p,) = got.players
    assert (p.level, p.is_guess, p.wins, p.loses, p.rank) == (29, True, 3, 1, 5)
    assert p.reasons == {"Ganked me": True, "Other": "camped the flight master"}
    assert p.kos and p.kos_added == 1795400000 and not p.ignored
    assert p.other == {"newField": "kept"}
    assert [(k.key, k.added) for k in got.kos] == [(KEY, 1795400000), (OTHER, 1795400001)]
    assert [i.key for i in got.ignored] == [OTHER]
    assert all(r.source == SOURCE for r in (*got.kos, *got.ignored, p))
    assert luadata.serialize(luadata.parse(data)) == data


def test_constructed_a_known_field_of_another_type_is_kept_in_other() -> None:
    row = _row(level='"high"', isEnemy="1", mapX='"left"', time="-5", wins="2.5")
    got = spy.parse_character(_char(f'["PlayerData"] = {{{NL}["{KEY}"] = {row},{NL}}},'), SOURCE)
    (p,) = got.players
    assert p.level is None and p.is_enemy is None and p.map_x is None and p.time is None
    assert p.wins is None
    assert p.other == {"level": "high", "isEnemy": 1, "mapX": "left", "time": -5, "wins": 2.5}


def test_constructed_a_row_that_is_not_a_table_is_unread_and_the_others_read() -> None:
    data = _char(
        f'["PlayerData"] = {{{NL}["{KEY}"] = true,{NL}["{OTHER}"] = {_row(level="3")},{NL}}},'
    )
    got = spy.parse_character(data, SOURCE)
    assert [p.key for p in got.players] == [OTHER]
    assert [(u.table, u.key, u.reason) for u in got.unread] == [("PlayerData", KEY, "not a table")]


def test_constructed_encounters_are_read_in_order_with_their_results() -> None:
    def fight(t: int, r: str, k: str) -> str:
        return _row(
            t=str(t),
            n=f'"{KEY}"',
            r=f'"{r}"',
            k=f'"{k}"',
            c='"MAGE"',
            l="29",
            m="1413",
            x="0.5",
            y="0.25",
        )

    body = f'["Encounters"] = {{{NL}{fight(10, "W", "kill")},{NL}{fight(20, "L", "recap")},{NL}{fight(30, "P", "probable")},{NL}{fight(40, "Z", "new")},{NL}7,{NL}}},'
    got = spy.parse_character(_char(body), SOURCE)
    assert [(e.position, e.time, e.result, e.result_code, e.how) for e in got.encounters] == [
        (1, 10, "win", "W", "kill"),
        (2, 20, "loss", "L", "recap"),
        (3, 30, "probable_win", "P", "probable"),
        (4, 40, None, "Z", "new"),
    ]
    first = got.encounters[0]
    assert (first.name, first.class_token, first.level, first.map_id, first.x, first.y) == (
        KEY,
        "MAGE",
        29,
        1413,
        0.5,
        0.25,
    )
    assert [(u.table, u.key) for u in got.unread] == [("Encounters", "[5]")]
    assert spy.encounters_with(got.encounters, KEY.lower()) == got.encounters


@pytest.mark.parametrize("version", ['"2.0"', "2", None])
def test_constructed_an_unknown_or_missing_version_is_read_with_a_warning(
    version: str | None,
) -> None:
    got = spy.parse_character(
        _char(f'["PlayerData"] = {{{NL}["{KEY}"] = {_row(level="3")},{NL}}},', version), SOURCE
    )
    assert got.warning is not None and "1.1" in got.warning
    assert [p.level for p in got.players] == [3]
    if version == '"2.0"':
        assert got.version == "2.0" and "2.0" in got.warning
    else:
        assert got.version is None


@pytest.mark.parametrize(
    "body",
    [
        "SpyPerCharDB = function() return {} end",
        "SpyPerCharDB = loadstring('return 1')()",
        'SpyPerCharDB = { ["PlayerData"] = setmetatable({}, {}) }',
        'SpyPerCharDB = { ["version"] = 1 + 1 }',
        "SpyPerCharDB = os",
    ],
)
def test_constructed_a_file_that_is_not_data_is_refused_with_a_position(body: str) -> None:
    with pytest.raises(luadata.LuaDataError) as caught:
        spy.parse_character((NL + body + NL).encode(), SOURCE)
    assert caught.value.line >= 1


@pytest.mark.parametrize("value", ["5", '"text"', "true"])
def test_constructed_a_top_level_that_is_not_a_table_is_refused(value: str) -> None:
    data = f"{NL}SpyPerCharDB = {value}{NL}".encode()
    with pytest.raises(spy.SpyError):
        spy.parse_character(data, SOURCE)
    with pytest.raises(spy.SpyError):
        spy.parse_account(data.replace(b"SpyPerCharDB", b"SpyDB"), SOURCE)


def test_constructed_the_account_lists_notes_and_tags() -> None:
    text = NL.join(
        [
            "",
            "SpyDB = {",
            '["kosData"] = {',
            '["Region RP-PvP"] = {',
            '["Horde"] = {',
            '["Mine Fakerealm"] = {',
            f'["{KEY}"] = {{',
            '["added"] = 1795400000,',
            '["level"] = 29,',
            "},",
            "},",
            "},",
            "},",
            "},",
            '["playerNotes"] = {',
            '["Region RP-PvP"] = {',
            f'["{KEY}"] = "camps the flight master",',
            f'["{OTHER}"] = 12,',
            "},",
            "},",
            '["playerTags"] = {',
            '["Region RP-PvP"] = {',
            f'["{KEY}"] = "rival",',
            f'["{OTHER}"] = "friend",',
            "},",
            "},",
            '["profiles"] = {},',
            "}",
            "",
        ]
    ).encode()
    got = spy.parse_account(text, SOURCE)
    (k,) = got.shared_kos
    assert (k.key, k.kept_under, k.faction, k.character, k.added) == (
        KEY,
        "Region RP-PvP",
        "Horde",
        "Mine Fakerealm",
        1795400000,
    )
    assert [(n.key, n.text) for n in got.notes] == [(KEY, "camps the flight master")]
    assert [(t.key, t.tag, t.other_tag) for t in got.tags] == [
        (KEY, "rival", None),
        (OTHER, None, "friend"),
    ]
    assert [(u.table, u.reason) for u in got.unread] == [("playerNotes", "not text")]
    assert got.other_keys == ["profiles"]
    # Spy's account lists write the two names with a space; a typed name
    # finds them either way.
    assert spy.named(got.shared_kos, "inventa fakerealm") == got.shared_kos
    assert luadata.serialize(luadata.parse(text)) == text


def test_constructed_a_table_of_the_wrong_kind_is_unread_not_refused() -> None:
    got = spy.parse_character(_char('["PlayerData"] = 5,' + NL + '["KOSData"] = "x",'), SOURCE)
    assert got.players == [] and got.kos == []
    assert sorted((u.table, u.reason) for u in got.unread) == [
        ("KOSData", "not a table"),
        ("PlayerData", "not a table"),
    ]


def test_constructed_named_prefers_the_exact_key() -> None:
    rows = [
        spy.SpyKos(key=KEY, source=SOURCE),
        spy.SpyKos(key=KEY.lower(), source=SOURCE),
    ]
    assert spy.named(rows, KEY) == rows[:1]
    assert spy.named(rows, KEY.upper()) == rows


def test_constructed_a_list_kos_entry_never_links_to_a_string_key_spelled_alike() -> None:
    """A `KOSData` written as a list has the key 1 (text `[1]`); a player
    row under the string key "[1]" is a different key and stays unlinked."""
    row = _row(level="3")
    data = _char(
        f'["PlayerData"] = {{{NL}["[1]"] = {row},{NL}}},{NL}["KOSData"] = {{{NL}1795400000,{NL}}},'
    )
    got = spy.parse_character(data, SOURCE)
    (p,) = got.players
    assert p.key == '["[1]"]' and not p.kos
    assert [k.key for k in got.kos] == ["[1]"]


def test_constructed_a_version_that_is_not_text_is_said_so() -> None:
    got = spy.parse_character(_char("", version="2"), SOURCE)
    assert got.warning is not None
    assert got.warning.startswith("Spy saved this character's data with a version that is not text")


def test_constructed_the_characters_that_take_shared_rows_are_named() -> None:
    text = NL.join(
        [
            "",
            "SpyDB = {",
            '["profileKeys"] = {',
            '["Mine Fakerealm"] = "Mine Fakerealm",',
            '["Other Fakerealm"] = "Default",',
            "},",
            '["profiles"] = {',
            '["Mine Fakerealm"] = {',
            '["UseData"] = true,',
            "},",
            '["Default"] = {',
            '["UseData"] = false,',
            "},",
            "},",
            "}",
            "",
        ]
    ).encode()
    got = spy.parse_account(text, SOURCE.model_copy(update={"character": None}))
    assert got.uses_shared_data == ["Mine Fakerealm"]
    assert spy.uses_shared_data([got], SOURCE)
    other = SOURCE.model_copy(update={"character": "1/Other-Fakerealm"})
    assert not spy.uses_shared_data([got], other)
