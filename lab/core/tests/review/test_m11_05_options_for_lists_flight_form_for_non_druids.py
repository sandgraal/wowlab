# Probe from review of m11/05-looks-model (6f69fcb); reproduces: options_for lists the druid Flight Form for a Human warrior or mage, because its "None" choice 7243 carries ClassMask 15359 (every class but druid) and the form rule counts any restricted-and-admitting choice as evidence the option applies.
"""`Customizations.options_for` documents its form rule as "a warlock's demons
for a warlock of any race, a druid form for a druid of a race that has
choices for it". The rule it implements is: list a form or pet option when
some choice's requirement is class- or race-restricted and admits the look.

Flight Form (option 966, model 193) has a choice 7243 "None" on requirement
148, ClassMask 0x3bff = 15359: every class id except 11, druid. That mask
restricts (it is neither 0 nor all-ones) and admits a warrior, so a Human
warrior (a race and class with no Flight Form choice of its own) is offered
Flight Form. The mask is the placeholder that *excludes* druids, not
evidence that the form applies.

Real rows only (the 1.60.1.70009 recordings). Positive control: a Night Elf
druid, which does have Flight Form choices, is offered it.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

import pytest

from wowlab_core.looks import REQUIRED_TABLES, Customizations

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "wago"
BUILD = "1.60.1.70009"
HUMAN, NIGHT_ELF = 1, 4
WARRIOR, MAGE, DRUID = 1, 8, 11
FLIGHT_FORM = 966


@pytest.fixture(scope="module")
def model() -> Customizations:
    tables = {
        name: list(
            csv.DictReader(
                io.StringIO((FIXTURES / f"{name}.{BUILD}.csv").read_text("utf-8"), newline="")
            )
        )
        for name in REQUIRED_TABLES
    }
    return Customizations.from_tables(BUILD, tables)


@pytest.mark.parametrize("class_id", [WARRIOR, MAGE])
def test_flight_form_is_not_offered_to_a_non_druid(model: Customizations, class_id: int) -> None:
    assert model.options[FLIGHT_FORM].name == "Flight Form"
    listed = {o.id for o in model.options_for(HUMAN, 0, class_id)}
    assert FLIGHT_FORM not in listed


def test_flight_form_is_offered_to_a_night_elf_druid_positive_control(
    model: Customizations,
) -> None:
    assert FLIGHT_FORM in {o.id for o in model.options_for(NIGHT_ELF, 0, DRUID)}
