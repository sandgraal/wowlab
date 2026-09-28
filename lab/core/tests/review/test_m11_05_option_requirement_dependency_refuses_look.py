# Probe from review of m11/05-looks-model (ca236cc); reproduces: a ChrCustomizationReqChoice list on an *option's* requirement is enforced as a refusal, so a real Undead look (Eye Glow "None", Eyesight "Both") is refused, although the convention the module cites checks dependent choices on choice requirements only.
"""`looks.py` says it reads the tables by TrinityCore's
`MeetsChrCustomizationReq`. TrinityCore's `WorldSession::ValidateAppearance`
calls it with `checkRequiredDependentChoices=false` for the option's
requirement and `true` only for the choice's requirement: an option-level
`ChrCustomizationReqChoice` list decides whether the client *shows* the
option, not whether a character holding a choice for it is valid.
`Customizations._requirement` applies the dependency list for both, so the
option-level list on Undead "Eyesight" (requirement 4103, which names only
Eye Glow "Glow") refuses every Undead look whose Eye Glow is anything else.
The spec (`docs/LAB_PLAN.md` §13.2) allows a refusal only for what the data
decides; here the data decides nothing about the look.

Real rows only (the 1.60.1.70009 recordings). The positive control is the
choice-level dependency the implementer's own test grades (Human death-knight
skin 13 depends on Face 20, 22 or 31): that one must stay a refusal.
"""

from __future__ import annotations

import csv
import io
from pathlib import Path

import pytest

from wowlab_core.looks import REQUIRED_TABLES, Customizations, FindingKind, Look

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "wago"
BUILD = "1.60.1.70009"
UNDEAD, HUMAN, DEATH_KNIGHT = 5, 1, 6


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


@pytest.mark.parametrize("body", [0, 1])
def test_option_level_dependency_does_not_refuse_undead_look(
    model: Customizations, body: int
) -> None:
    options = {o.name: o for o in model.options_for(UNDEAD, body)}
    glow, sight = options["Eye Glow"], options["Eyesight"]
    no_glow = next(c.id for c in glow.choices if c.name == "None")
    both = next(c.id for c in sight.choices if c.name == "Both")
    # the dependency is on the option's requirement, not on the choice's
    assert model.requirements[sight.requirement_id].required_choices
    assert not model.requirements[model.choices[both].requirement_id].required_choices

    check = model.check(
        Look(
            name="undead",
            race_id=UNDEAD,
            body_type=body,
            choices={glow.id: no_glow, sight.id: both},
        )
    )
    assert not check.refused, [f.message for f in check.refusals]


def test_choice_level_dependency_still_refuses_positive_control(model: Customizations) -> None:
    check = model.check(
        Look(name="dk", race_id=HUMAN, body_type=0, class_id=DEATH_KNIGHT, choices={9: 13, 10: 21})
    )
    assert [f.kind for f in check.refusals] == [FindingKind.MISSING_DEPENDENCY]
