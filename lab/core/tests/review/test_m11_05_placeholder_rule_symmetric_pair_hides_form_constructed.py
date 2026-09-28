# Probe from review of m11/05-looks-model (a91493f); reproduces: the placeholder rule is symmetric, so a form option with one druid choice and one all-but-druid "None" marks both as placeholders and is never listed, even for the druid it belongs to.
"""`Customizations._is_placeholder` calls a class-restricted choice a
placeholder when its classes are exactly the build classes the option's other
class-restricted choices exclude. For a pair of choices that is true of each
one: {druid} is the complement of {every class but druid} and the reverse. On
1.60.1.70009 Flight Form escapes this only because it has several druid-only
choices (for any one of them the others already cover druid). An option with
a single druid choice plus the 'None' placeholder loses both, and
`options_for` stops offering the form to the Night Elf druid its druid choice
admits.

Constructed (L8, boundary case): the real 70009 tables with Flight Form
(option 966) cut down to two of its real choices, 'None' 7243 (requirement
148, ClassMask 15359) and one Night-Elf druid choice (requirement 4223).
Positive control: with every real choice kept, the Night Elf druid is offered
Flight Form.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

import pytest

from wowlab_core.looks import REQUIRED_TABLES, Customizations

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "wago"
BUILD = "1.60.1.70009"
NIGHT_ELF, DRUID = 4, 11
FLIGHT_FORM, NONE_CHOICE, NE_DRUID_REQ = "966", "7243", "4223"


def _tables(two_choices: bool) -> dict[str, list[dict[str, str]]]:
    tables = {
        name: list(
            csv.DictReader(
                io.StringIO((FIXTURES / f"{name}.{BUILD}.csv").read_text("utf-8"), newline="")
            )
        )
        for name in REQUIRED_TABLES
    }
    if two_choices:
        choices = tables["ChrCustomizationChoice"]
        ne = next(
            r
            for r in choices
            if r["ChrCustomizationOptionID"] == FLIGHT_FORM
            and r["ChrCustomizationReqID"] == NE_DRUID_REQ
        )
        keep = {NONE_CHOICE, ne["ID"]}
        tables["ChrCustomizationChoice"] = [
            r for r in choices if r["ChrCustomizationOptionID"] != FLIGHT_FORM or r["ID"] in keep
        ]
    return tables


@pytest.mark.parametrize(
    "two_choices",
    [
        pytest.param(False, id="all_real_choices_positive_control"),
        pytest.param(True, id="one_druid_choice_plus_none_constructed"),
    ],
)
def test_druid_choice_keeps_its_form_listed_constructed(two_choices: bool) -> None:
    model = Customizations.from_tables(BUILD, _tables(two_choices))
    assert int(FLIGHT_FORM) in {o.id for o in model.options_for(NIGHT_ELF, 0, DRUID)}
