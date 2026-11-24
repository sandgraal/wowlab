"""The reader side of M21-11: the lab-addon's fixes from the launch-build API
list (lab/addon/README.md, "The [verify] notes against the launch-build list
and the field guide") and the owner's in-game check of 2026-11-23.

What the M21-11 addon writes that an earlier one did not, all in schema 5
with no bump: `macros.account_slots_from = "MacroConsts"`;
`bindings.binding_sets` (the client's Enum.BindingSet) in place of
`account_bindings` and `character_bindings`; `combatlog.state_from` and
`advanced_from`; and `identity.second_name` with `second_name_from`, the
second return of UnitFullName("player") when no realm call gave a realm,
never the realm. A file an earlier addon wrote reads as before.

CONSTRUCTED INPUT: each record is the M20-16 test's constructed schema-5
record (the real 70450 schema-4 record with the schema-5 keys added the way
`lab/addon/WowLab/` writes them). Every name, realm and GUID is invented.
"""

from __future__ import annotations

import types
from typing import Any

import pytest
import test_m20_16_schema_5_first_run as m16

from wowlab_core import cli, labaddon

# The members of Enum.BindingSet in the API list at 70938.
BINDING_SETS = {"Default": 0, "Account": 1, "Character": 2, "Current": 3}
REALM_ABSENT = (
    "the return of GetRealmName was not a non-empty string; the return of "
    "GetNormalizedRealmName was not a non-empty string; the second return of UnitFullName "
    "is the second name"
)


def _bindings_line(raw: dict[str, Any]) -> str:
    lines = [line for line in labaddon.describe(m16._char(raw)) if line.startswith("Key bindings")]
    assert len(lines) == 1, lines
    return lines[0]


# 1. Macro slots from Constants.MacroConsts -------------------------------------


def test_constructed_macro_slots_from_the_client_table_are_read() -> None:
    char = m16._char(m16._raw(account_slots_from="MacroConsts"))
    assert isinstance(char.macros, labaddon.Macros)
    assert char.macros.account_slots_from == "MacroConsts"
    assert labaddon.unknown_keys(char) == []


def test_constructed_no_macro_line_names_the_window() -> None:
    """The false reason is gone for every source (the macro window was
    never what made the count missing)."""
    for source in ("MacroConsts", "MAX_ACCOUNT_MACROS", "fallback"):
        text = "\n".join(labaddon.describe(m16._char(m16._raw(account_slots_from=source))))
        assert "macro window" not in text, source
        assert "loaded only once" not in text, source


# 3. Binding sets from Enum.BindingSet ------------------------------------------


@pytest.mark.parametrize(
    ("which", "words"),
    [
        (2, "binding set 2 (the character's own bindings)"),
        (1, "binding set 1 (the account's bindings)"),
        (3, "binding set 3 (the set in use)"),
        (7, "binding set 7 (the client's raw value; not one of the game's named sets)"),
    ],
    ids=["constructed-character", "constructed-account", "constructed-current", "constructed-7"],
)
def test_constructed_binding_set_named_by_the_client_enum(which: int, words: str) -> None:
    raw = m16._raw()
    raw["bindings"]["binding_set"] = which
    raw["bindings"]["binding_sets"] = dict(BINDING_SETS)
    char = m16._char(raw)
    assert isinstance(char.bindings, labaddon.BindingsV5)
    assert char.bindings.binding_sets == BINDING_SETS
    assert labaddon.unknown_keys(char) == []
    assert words in _bindings_line(raw)


def test_constructed_bindings_without_the_enum_keep_the_old_words() -> None:
    """A file an earlier addon wrote (no `binding_sets`, and no number for
    either global, as on 70425) reads as before."""
    raw = m16._raw()
    raw["bindings"]["binding_set"] = 2
    raw["bindings"].pop("binding_sets", None)
    raw["bindings"].pop("account_bindings", None)
    raw["bindings"].pop("character_bindings", None)
    assert (
        "binding set 2 (the client's raw value; which number is the account's and which the "
        "character's was not recorded)"
    ) in _bindings_line(raw)


def test_constructed_an_empty_binding_sets_claims_nothing() -> None:
    """Fix round 1: a client table with no member the addon kept names no
    set and does not call the number the client's raw value."""
    raw = m16._raw()
    raw["bindings"]["binding_set"] = 1
    raw["bindings"]["binding_sets"] = {}
    line = _bindings_line(raw)
    assert "binding set 1 (no binding set recorded)" in line
    assert "raw value" not in line


def test_constructed_a_bad_binding_sets_member_is_refused() -> None:
    raw = m16._raw()
    raw["bindings"]["binding_sets"] = {"Character": "two"}
    with pytest.raises(labaddon.LabAddonError, match="binding_sets"):
        labaddon.load_char(raw)


# 4. The combat log's state from C_ChatInfo.IsLoggingCombat ---------------------


@pytest.mark.parametrize(
    ("state_from", "advanced_from"),
    [
        ("IsLoggingCombat", "IsLoggingCombat"),
        ("LoggingCombat", "setting"),
        ("IsLoggingCombat", None),
    ],
    ids=["constructed-documented", "constructed-fallbacks", "constructed-no-advanced"],
)
def test_constructed_combatlog_names_the_call_that_answered(
    state_from: str, advanced_from: str | None
) -> None:
    raw = m16._raw()
    raw["combatlog"] = {
        "idle_minutes": 10,
        "switches": 1,
        "log_on": True,
        "mine": True,
        "state_from": state_from,
        "events_unregistered": {},
    }
    if advanced_from is not None:
        raw["combatlog"]["advanced"] = True
        raw["combatlog"]["advanced_from"] = advanced_from
    char = m16._char(raw)
    assert isinstance(char.combatlog, labaddon.CombatLog)
    assert char.combatlog.state_from == state_from
    assert char.combatlog.advanced_from == advanced_from
    assert labaddon.unknown_keys(char) == []


@pytest.mark.parametrize("field", ["state_from", "advanced_from"])
def test_constructed_an_unknown_combatlog_source_is_refused(field: str) -> None:
    raw = m16._raw()
    raw["combatlog"] = {"idle_minutes": 10, field: "guessed", "events_unregistered": {}}
    with pytest.raises(labaddon.LabAddonError, match=field):
        labaddon.load_char(raw)


# 6. The second name, not a realm -----------------------------------------------


def _second_name_raw() -> dict[str, Any]:
    """As the M21-11 addon writes identity on a client whose realm calls give
    nothing: the full name's second value as `second_name`, no realm."""
    raw = m16._raw()
    identity = raw["identity"]
    del identity["realm"]
    del identity["realm_from"]
    identity["realm_absent"] = REALM_ABSENT
    identity["second_name"] = "Inventedsecond"
    identity["second_name_from"] = "full_name"
    return raw


def test_constructed_second_name_is_read_and_never_a_realm() -> None:
    char = m16._char(_second_name_raw())
    identity = char.identity
    assert isinstance(identity, labaddon.IdentityV5)
    assert identity.second_name == "Inventedsecond"
    assert identity.second_name_from == "full_name"
    assert identity.realm is None
    assert identity.realm_from is None
    assert labaddon.unknown_keys(char) == []


def test_constructed_second_name_is_shown_as_the_second_name() -> None:
    lines = labaddon.describe(m16._char(_second_name_raw()))
    character = next(line for line in lines if line.startswith("Character: "))
    assert character.startswith(
        "Character: name Inventedname, second name Inventedsecond (from the game's full name; "
        "no realm given) [verify], "
    ), character
    assert "realm absent" not in character
    assert "realm Inventedsecond" not in character


def test_constructed_second_name_shown_to_the_player_once_marked() -> None:
    """The CLI's player-copy rendering turns the marker into words."""
    rendered = labaddon.describe(
        m16._char(_second_name_raw()), render=lambda t: t.replace("[verify]", "(not yet confirmed)")
    )
    character = next(line for line in rendered if line.startswith("Character: "))
    assert "second name Inventedsecond (from the game's full name; no realm given) (not yet " in (
        character
    )


def test_constructed_an_unknown_second_name_source_is_refused() -> None:
    raw = _second_name_raw()
    raw["identity"]["second_name_from"] = "realm_name"
    with pytest.raises(labaddon.LabAddonError, match="second_name_from"):
        labaddon.load_char(raw)


def _full_name_raw() -> dict[str, Any]:
    """As the M20-16 to M20-19 addons wrote identity on Forever: the full
    name's second value as the realm (`realm_from = "full_name"`)."""
    raw = m16._raw()
    raw["identity"].update(
        {"realm": "Inventedsecond", "realm_from": "full_name", "realm_normalized": True}
    )
    return raw


def test_constructed_an_earlier_full_name_realm_still_reads() -> None:
    """Such a file is read as before; `char show` calls the value a realm or
    second name, probably the second name, and not a realm written without
    spaces."""
    lines = labaddon.describe(m16._char(_full_name_raw()))
    character = next(line for line in lines if line.startswith("Character: "))
    assert (
        "realm or second name Inventedsecond (from the character's full name; probably the "
        "second name [verify])"
    ) in character
    assert "written without spaces" not in character


def _owners(raw: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> dict[str, str | None]:
    files = [types.SimpleNamespace(record=m16._char(raw))]
    monkeypatch.setattr(labaddon, "read_all", lambda lay, account=None: files)
    owners, without = cli._owner_guids(None, "ACCOUNT")  # type: ignore[arg-type]
    assert without == 0
    return owners


def test_constructed_scan_log_takes_no_home_realm_from_the_full_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`crew scan-log` keys a log name without a realm on your stored realm;
    an older record's `realm_from = "full_name"` value is very likely your
    second name, so it is not taken as one."""
    assert _owners(_full_name_raw(), monkeypatch) == {m16.GUID: None}


def test_constructed_scan_log_keeps_a_realm_a_realm_call_gave(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Control: a realm from GetRealmName is still the home realm."""
    assert _owners(m16._raw(), monkeypatch) == {m16.GUID: "Invented Realm"}
