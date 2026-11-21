"""The reader side of M20-17 (the owner's second schema-5 run; 71037,
2026-11-20): each section's gather time and the ones the pass at logout
gathered, what the other realm sources gave (`realm_tried`), the addon's
new words for a nil guild, and the no-time note only for a record without a
time of its own.

CONSTRUCTED INPUT: each record is the M20-16 test's constructed schema-5
record (the real 70450 schema-4 record with the schema-5 keys added the way
`lab/addon/WowLab/` writes them). Every name, realm and GUID is invented.
"""

from __future__ import annotations

from typing import Any

import pytest
import test_m20_16_schema_5_first_run as m16

from wowlab_core import labaddon
from wowlab_core.cli import _cmd_char

# 2026-11-20 12:00:00 UTC, and the times after it.
NOON = 1795176000


def _raw() -> dict[str, Any]:
    raw = m16._raw()
    raw["written_at"] = NOON + 60
    raw["written_by"] = "logout"
    raw["gathered"] = [
        {"section": "gear", "at": NOON},
        {"section": "actionbars", "at": NOON + 60, "at_logout": True},
    ]
    return raw


def test_constructed_gather_times_and_the_logout_pass_are_shown() -> None:
    lines = labaddon.describe(m16._char(_raw()))
    assert "Gathered (UTC): gear 12:00:00, actionbars 12:01:00" in lines
    late = [line for line in lines if "Gathered at logout" in line]
    assert late == [
        "  Gathered at logout (nothing earlier that session; may be emptier than the game "
        "was): actionbars"
    ]


def test_constructed_gathered_without_the_logout_flag_reads_as_before() -> None:
    raw = _raw()
    del raw["gathered"][1]["at_logout"]
    char = m16._char(raw)
    assert char.gathered is not None and char.gathered[1].at_logout is None
    assert not any("Gathered at logout" in line for line in labaddon.describe(char))


@pytest.mark.parametrize("value", [1, "yes"])
def test_constructed_logout_flag_other_than_true_is_refused(value: object) -> None:
    raw = _raw()
    raw["gathered"][1]["at_logout"] = value
    with pytest.raises(labaddon.LabAddonError):
        m16._char(raw)


def test_constructed_realm_tried_is_kept_and_shown() -> None:
    raw = _raw()
    tried = (
        "the return of GetRealmName was nil, not a non-empty string; "
        "the return of GetNormalizedRealmName was an empty string"
    )
    raw["identity"].update(
        {
            "realm": "InventedRealm",
            "realm_from": "full_name",
            "realm_normalized": True,
            "realm_tried": tried,
        }
    )
    char = m16._char(raw)
    assert isinstance(char.identity, labaddon.IdentityV5)
    assert char.identity.realm_tried == tried
    line = next(x for x in labaddon.describe(char) if x.startswith("Character:"))
    assert "realm InventedRealm (written without spaces: taken from the character's" in line
    assert tried in line


def test_constructed_new_nil_guild_reason_reads_as_no_guild() -> None:
    raw = _raw()
    raw["identity"]["guild_absent"] = (
        "the first return of GetGuildInfo was nil, not a non-empty string"
    )
    line = next(x for x in labaddon.describe(m16._char(raw)) if x.startswith("Character:"))
    assert "no guild, or the guild not loaded yet" in line


def test_constructed_no_time_note_only_without_a_time_of_its_own() -> None:
    raw = _raw()
    assert _cmd_char._char_notes(m16._char(raw)) == [_cmd_char._CHAR_NOTES[0]]
    del raw["written_at"]
    assert _cmd_char._char_notes(m16._char(raw)) == list(_cmd_char._CHAR_NOTES)
    v4 = labaddon.load_char(m16._v4())
    assert _cmd_char._char_notes(v4) == list(_cmd_char._CHAR_NOTES)


# Fix round 1 -----------------------------------------------------------------


def test_constructed_realm_read_again_at_logout_says_so() -> None:
    """Identity kept from the session's gather, its realm from a read at
    logout: the desk says that, never "nothing earlier that session"."""
    raw = _raw()
    raw["gathered"].append({"section": "identity", "at": NOON, "again_at_logout": "realm"})
    lines = labaddon.describe(m16._char(raw))
    assert "  identity: the realm was read at logout; the rest as gathered at 12:00:00" in lines
    late = next(line for line in lines if "Gathered at logout" in line)
    assert "identity" not in late


@pytest.mark.parametrize("value", ["Realm", "", True, "x" * 33])
def test_constructed_re_read_field_that_cannot_be_is_refused(value: object) -> None:
    raw = _raw()
    raw["gathered"].append({"section": "identity", "at": NOON, "again_at_logout": value})
    with pytest.raises(labaddon.LabAddonError):
        m16._char(raw)


class _Row:
    def __init__(self, schema: int) -> None:
        self.record_schema = schema


def test_the_no_time_note_is_one_decision() -> None:
    """char show, gear, history, kept and model all go through _time_notes."""
    both = list(_cmd_char._CHAR_NOTES)
    assert _cmd_char._time_notes([True, True]) == [both[0]]
    assert _cmd_char._time_notes([True, False]) == both
    rest = _cmd_char._HISTORY_NOTES[len(both) :]
    assert _cmd_char._history_notes([_Row(5), _Row(5)]) == [both[0], *rest]  # type: ignore[list-item]
    assert _cmd_char._history_notes([_Row(5), _Row(4)]) == [*both, *rest]  # type: ignore[list-item]
