"""The unit names of the launch client's combat log (M21-02; LAB_FORMATS §8,
amendment of 2026-11-23).

Real inputs (L8): `fixtures/macos-71037/` is the first 120 lines of the
owner's launch-client log (build 1.60.1.71037), captured with
`scripts/lab_capture.py --pseudonymise-other-players`. Every other player is
named in four parts, `"<First>-<Second>-<Realm>-<Region>"`; the tool
replaced each first name (`Labothera`, ...) and kept the second name, the
realm (with its space) and the region as written. `fixtures/macos/` is the
beta's log (build 1.60.1.70009), which named the owner in three parts,
`"<Name>-<Realm>-"`, with no second name.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from wowlab_core import combatlog, worldpvp

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
LAUNCH_LOG = FIXTURES / "macos-71037" / "forever" / "Logs" / "WoWCombatLog-010926_073444.txt"
BETA_LOG = FIXTURES / "macos" / "forever" / "Logs" / "WoWCombatLog-040126_021630.txt"


def _player_names(path: Path) -> dict[str, str]:
    """Each player GUID's unit name, as the log's GUID+name pairs give it."""
    names: dict[str, str] = {}
    for e in combatlog.read_log(path):
        if not isinstance(e, combatlog.Record):
            continue
        fields = e.fields
        for i in range(len(fields) - 1):
            guid, name = fields[i], fields[i + 1]
            if (
                isinstance(guid, str)
                and guid.startswith("Player-")
                and isinstance(name, combatlog.Quoted)
            ):
                names[guid] = name.text
    return names


@pytest.mark.parser
def test_every_player_of_the_launch_log_is_named_in_four_parts() -> None:
    names = _player_names(LAUNCH_LOG)
    assert len(names) == 41  # the capture's "other-players-pseudonymised: 41"
    for text in names.values():
        parts = worldpvp.unit_parts(text, second_names=True)
        assert parts.second, text
        assert parts.realm == "Roleplay PvP" and parts.region == "US", text


@pytest.mark.parser
def test_a_four_part_name_gives_first_second_realm_region_and_its_key() -> None:
    names = _player_names(LAUNCH_LOG)
    text = names["Player-9998-00000001"]
    assert text == "Labothera-Crazywater-Roleplay PvP-US"
    assert worldpvp.unit_parts(text, second_names=True) == worldpvp.UnitParts(
        "Labothera", "Crazywater", "Roleplay PvP", "US"
    )
    # Keyed `<First>-<Second>`, as the lab-addon keys a Forever player.
    assert worldpvp.player_key(text, None, second_names=True) == (
        "Labothera-Crazywater",
        "Labothera",
        "Crazywater",
    )
    # On a flavor without second names the old reading stands: name, realm.
    assert worldpvp.player_key(text, None) == (
        "Labothera-CrazywaterRoleplayPvP",
        "Labothera",
        "CrazywaterRoleplayPvP",
    )
    for guid, name in names.items():
        parts = worldpvp.unit_parts(name, second_names=True)
        got = worldpvp.player_key(name, None, second_names=True)
        assert got is not None and got[0] == f"{parts.name}-{parts.second}", guid


@pytest.mark.parser
def test_the_beta_logs_three_part_name_has_no_second_name() -> None:
    (text,) = set(_player_names(BETA_LOG).values())
    assert text == "Labchard-LabrealmbPartbPartcPartd-"
    parts = worldpvp.unit_parts(text, second_names=True)
    assert parts == worldpvp.UnitParts("Labchard", None, "LabrealmbPartbPartcPartd", "")
    # Its middle part is the realm: the key is `<Name>-<Realm>`, as before.
    assert worldpvp.player_key(text, None) == (
        "Labchard-LabrealmbPartbPartcPartd",
        "Labchard",
        "LabrealmbPartbPartcPartd",
    )


@pytest.mark.parser
def test_the_launch_log_reads_whole_and_names_no_encounter() -> None:
    """No line of the slice is the logging player's (the capture holds only
    other players), so there is no fight of yours: every line is read."""
    scan = worldpvp.read_world(LAUNCH_LOG, {"Player-9999-00000001": None})
    assert scan.encounters == [] and scan.unparsed == 0
    assert scan.end == LAUNCH_LOG.stat().st_size
