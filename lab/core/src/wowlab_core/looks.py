"""looks: character customization tables for one build, and looks checked against them.

Spec: docs/LAB_PLAN.md §13.2. Data: ADR-0022 (tables are keyed by the full
version string; the build comes from the caller or from discovery, never from
this module, L6).

What the tables say, as read here (community convention for these DB2
tables, TrinityCore's ``Player::MeetsChrCustomizationReq`` and
``WorldSession::ValidateAppearance``; each is **[verify]** against the
client):

- ``ChrRaceXChrModel`` gives each race one ``ChrModel`` per body type (its
  ``Sex`` column). ``ChrCustomizationOption`` rows belong to one model; each
  ``ChrCustomizationChoice`` belongs to one option.
- Some options sit on models no ``ChrRaceXChrModel`` row names. Those whose
  ``ChrModel.Sex`` is 3 are druid forms, warlock demons, a pet and the
  dragonriding bodies (on 1.60.1.70009: models 148, 176, 180-184, 189-194,
  197, 198, 212, 216, 217): race-independent, gated only by their
  requirements' class and race masks. The others (257-278 on 70009, Sex 0 or
  1) look like the original pre-HD character models: all of them share
  texture layout 203, which no linked model uses (the linked Sex 0/1 models
  mostly have one layout each, 103-202, e.g. 103 and 104 for the Human
  pair, and 1 or 2 on some NPC races), and display ids 49-60,
  1478/1479 and 1563/1564, which match the original eight races' models
  **[memory, verify]**; 277/278 are unexplained. What links them to a race
  is not in the recorded tables; ``ChrModelAltVariant`` is a candidate
  **[memory, verify]**. Neither kind is refused on its model: its
  requirements are applied and a note says what it is, so a Human look
  holding an option of model 259 is noted, not refused.
- Options a race takes from ``ChrRaces.UnalteredVisualCustomizationRaceID``
  (the Worgen human form, the Dracthyr visage) are not modelled; no race
  flagged playable in 70009 has one.
- Options and choices each point at a ``ChrCustomizationReq``. Only bit
  ``0x1`` of ``ReqType`` is read: without it the requirement gates nothing.
  Bits 0x2, 0x4 and 0x8 occur but their meaning is not known **[verify]**.
  On 1.60.1.70009, every row without 0x1 restricts nothing, so no verdict
  depends on them. With it: a non-zero ``ClassMask`` other than all-ones
  admits class *c* only if bit ``c - 1`` is set; a non-zero race mask other
  than all-ones (``RaceMasks_0`` low word, ``RaceMasks_1`` high word) admits
  a race only if the bit at its ``ChrRaces.PlayableRaceBit`` is set;
  ``ReqAchievementID``, ``ReqQuestID`` and ``ReqItemModifiedAppearanceID``
  are unlocks. Class bit *c - 1* and the ``PlayableRaceBit`` race indexing
  agree with the 70009 rows (class masks 32, 2048, 1024 and 256 on death
  knight, demon hunter, druid and warlock choices; ``RaceMasks_1`` = 3 on
  Skyborne-only rows).
- ``ChrCustomizationReqChoice`` rows name choices for a requirement; grouped
  by the option each choice belongs to. On a *choice's* requirement, the look
  must hold one of the named choices for each such option. On an *option's*
  requirement they only decide whether the barber shop shows the option
  (``ValidateAppearance`` checks them on choice requirements only), so they
  are a condition note, never a refusal **[verify]**.
- A race is *flagged playable* when its ``PlayableRaceBit`` is not negative
  and its ``Flags`` lack ``0x1`` (NPC only). That is what the tables say,
  not a claim about what a server lets anyone create. Flags ``0x200`` (not
  selectable) and ``0x1000000`` (internal only) are from memory
  **[verify]**; on 70009 they occur only together with 0x1.
- ``ChrClasses`` says which class ids the build has (Forever:
  1, 2, 3, 4, 5, 7, 8, 9 and 11).

What a check refuses, and nothing else (§13.2): an option or choice for
another race or body type (including a race mask that excludes the race), a
choice mapped to an option it does not belong to, a class mask that excludes
the look's class, and a choice it depends on that the look sets to something
else. A dependency on an option the look leaves unset is undecided: the
client always holds some choice there. An unlock is a "needs <unlock>" note.
An option or choice id the build lacks is "unknown to build <version>
(possibly a hotfix)", never refused: server hotfixes are not in these
exports (ADR-0022).

``ChrCustomizationElement`` (how a choice is drawn) and
``ChrCustomizationConversion`` (legacy appearance bytes to choices) are
recorded with the others but no rule here reads them.

Pure data: nothing here touches an install (L1) or the network; tables come
from ``gamedata`` or from rows the caller hands over.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping
from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from wowlab_core.gamedata import GameData

__all__ = [
    "REQUIRED_TABLES",
    "BodyType",
    "Category",
    "CharacterModel",
    "Choice",
    "Customizations",
    "Finding",
    "FindingKind",
    "Look",
    "LookCheck",
    "LooksDataError",
    "Option",
    "PlayerClass",
    "Race",
    "Requirement",
]

# The tables the model is built from. Element and Conversion are recorded
# fixtures too (§13.2) but nothing here reads them.
REQUIRED_TABLES: tuple[str, ...] = (
    "ChrRaces",
    "ChrClasses",
    "ChrModel",
    "ChrRaceXChrModel",
    "ChrCustomizationCategory",
    "ChrCustomizationOption",
    "ChrCustomizationChoice",
    "ChrCustomizationReq",
    "ChrCustomizationReqChoice",
)

_RACE_NPC_ONLY = 0x1  # ChrRaces.Flags [verify]
_REQ_HAS_REQUIREMENTS = 0x1  # ChrCustomizationReq.ReqType [verify]
# 3: not specific to one body type (shared models, forms, pets, mounts) [verify]
_MODEL_SEX_SHARED = 3
_ALL_32 = 0xFFFFFFFF
_ALL_64 = 0xFFFFFFFFFFFFFFFF


class LooksDataError(ValueError):
    """The tables handed over cannot be read as customization data: a table
    or column is missing, or a cell that should be an integer is not."""


# ─── table models ────────────────────────────────────────────────────────────


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class BodyType(_Frozen):
    """One of a race's body types and the model whose options apply.

    ``body_type`` is 0 or 1 as in ``ChrRaceXChrModel.Sex``, not
    ``ChrModel.Sex``, which disagrees where one model serves both (it is 3
    there). The addon API's ``UnitSex`` uses 2 and 3 **[memory, verify]**;
    M11-04 and M11-06 must convert."""

    body_type: int
    chr_model_id: int


class Race(_Frozen):
    """One ``ChrRaces`` row.

    ``alliance``: 0 Alliance, 1 Horde, 2 neither. One race as the player sees
    it can be several rows, one per faction, sharing models: on 70009 races 95
    and 96 are the Skyborne, sharing models 218 and 219."""

    id: int
    name: str
    client_file_string: str
    flags: int
    playable_race_bit: int
    faction_id: int
    alliance: int
    body_types: tuple[BodyType, ...]

    @property
    def flagged_playable(self) -> bool:
        """The tables' own flags, not a claim about any server."""
        return self.playable_race_bit >= 0 and not self.flags & _RACE_NPC_ONLY

    def model_for(self, body_type: int) -> int | None:
        for entry in self.body_types:
            if entry.body_type == body_type:
                return entry.chr_model_id
        return None


class PlayerClass(_Frozen):
    """One ``ChrClasses`` row: the class ids the build has."""

    id: int
    name: str


class CharacterModel(_Frozen):
    """One ``ChrModel`` row, as far as the checks need it."""

    id: int
    sex: int  # 0, 1, or 3 for forms, pets and shared models [verify]


class Category(_Frozen):
    """UI grouping of options (``ChrCustomizationCategory``).

    ``spell_shapeshift_form_id`` is non-zero on some druid-form categories;
    form options are found by their model (``is_form_or_pet``), not by
    category (Bear Form option 901 is in "Face")."""

    id: int
    name: str
    order_index: int
    spell_shapeshift_form_id: int


class Requirement(_Frozen):
    """One ``ChrCustomizationReq`` row, with the choices named for it
    (``ChrCustomizationReqChoice``) grouped by the option they belong to."""

    id: int
    req_type: int
    class_mask: int  # unsigned 32-bit
    race_mask: int  # unsigned 64-bit
    region_group_mask: int
    achievement_id: int
    quest_id: int
    item_modified_appearance_id: int
    override_archive: int
    source_text: str
    # option id -> choice ids; option 0 groups choices the build lacks
    required_choices: tuple[tuple[int, tuple[int, ...]], ...] = ()

    @property
    def active(self) -> bool:
        return bool(self.req_type & _REQ_HAS_REQUIREMENTS)

    @property
    def class_restricted(self) -> bool:
        return self.class_mask not in (0, _ALL_32)

    @property
    def race_restricted(self) -> bool:
        return self.race_mask not in (0, _ALL_64)

    def admits_class(self, class_id: int) -> bool:
        if not self.active or not self.class_restricted:
            return True
        return 1 <= class_id <= 32 and bool(self.class_mask >> (class_id - 1) & 1)

    def admits_race(self, race: Race) -> bool:
        if not self.active or not self.race_restricted:
            return True
        bit = race.playable_race_bit
        return 0 <= bit < 64 and bool(self.race_mask >> bit & 1)

    def classes(self, known: Iterable[int]) -> tuple[int, ...]:
        """The ``known`` class ids (the build's ``ChrClasses``) the mask admits."""
        return tuple(c for c in sorted(known) if self.admits_class(c))

    def unlocks(self) -> tuple[str, ...]:
        """What must be earned: ``needs <unlock>`` texts, never a refusal."""
        if not self.active:
            return ()
        found: list[str] = []
        if self.achievement_id:
            found.append(f"achievement {self.achievement_id}")
        if self.quest_id:
            found.append(f"quest {self.quest_id}")
        if self.item_modified_appearance_id:
            found.append(f"item appearance {self.item_modified_appearance_id}")
        if found and self.source_text:
            found = [f"{u} ({self.source_text})" for u in found]
        return tuple(f"needs {u}" for u in found)


class Choice(_Frozen):
    id: int
    option_id: int
    name: str
    requirement_id: int
    order_index: int
    ui_order_index: int
    flags: int


class Option(_Frozen):
    id: int
    name: str
    chr_model_id: int
    category_id: int
    order_index: int
    option_type: int
    flags: int
    requirement_id: int
    choices: tuple[Choice, ...]


# ─── looks and what a check says about them ─────────────────────────────────


class Look(_Frozen):
    """A named mapping of option to choice, for one race and body type.

    ``class_id`` is optional: without it a class-restricted choice is noted,
    not refused, because nothing decides it."""

    name: str
    race_id: int
    body_type: int
    class_id: int | None = None
    choices: dict[int, int] = Field(default_factory=dict)  # option id -> choice id


class FindingKind(StrEnum):
    # refusals: the data decides
    UNKNOWN_RACE = "unknown_race"
    WRONG_BODY_TYPE = "wrong_body_type"
    WRONG_RACE_OR_BODY_TYPE = "wrong_race_or_body_type"
    CHOICE_NOT_IN_OPTION = "choice_not_in_option"
    CLASS_EXCLUDED = "class_excluded"
    MISSING_DEPENDENCY = "missing_dependency"
    # notes: shown, never refused
    NEEDS_UNLOCK = "needs_unlock"
    UNKNOWN_TO_BUILD = "unknown_to_build"
    CLASS_NOT_IN_BUILD = "class_not_in_build"
    CLASS_RESTRICTED = "class_restricted"
    CONDITION = "condition"
    UNDECIDED_DEPENDENCY = "undecided_dependency"
    FORM_OR_PET_OPTION = "form_or_pet_option"
    UNLINKED_MODEL_OPTION = "unlinked_model_option"


_REFUSALS = frozenset(
    {
        FindingKind.UNKNOWN_RACE,
        FindingKind.WRONG_BODY_TYPE,
        FindingKind.WRONG_RACE_OR_BODY_TYPE,
        FindingKind.CHOICE_NOT_IN_OPTION,
        FindingKind.CLASS_EXCLUDED,
        FindingKind.MISSING_DEPENDENCY,
    }
)


class Finding(_Frozen):
    kind: FindingKind
    message: str
    option_id: int | None = None
    choice_id: int | None = None

    @property
    def refuses(self) -> bool:
        return self.kind in _REFUSALS


class LookCheck(_Frozen):
    """The verdict on one look against one build."""

    look: str
    build: str
    refusals: tuple[Finding, ...]
    notes: tuple[Finding, ...]

    @property
    def refused(self) -> bool:
        return bool(self.refusals)


# ─── the model ───────────────────────────────────────────────────────────────


class Customizations(_Frozen):
    """Races, body types, classes, options, choices, requirements and
    categories of one build, keyed by id."""

    build: str
    races: dict[int, Race]
    classes: dict[int, PlayerClass]
    models: dict[int, CharacterModel]
    categories: dict[int, Category]
    options: dict[int, Option]
    choices: dict[int, Choice]
    requirements: dict[int, Requirement]
    linked_model_ids: frozenset[int]  # models some ChrRaceXChrModel row names

    # building ---------------------------------------------------------------

    @classmethod
    def from_gamedata(cls, data: GameData, build: str) -> Customizations:
        """Load through ``gamedata``'s never-overwrite cache (ADR-0022).
        ``build`` is the full version string, from discovery or the caller."""
        return cls.from_tables(build, {name: data.rows(name, build) for name in REQUIRED_TABLES})

    @classmethod
    def from_tables(
        cls, build: str, tables: Mapping[str, Iterable[Mapping[str, str]]]
    ) -> Customizations:
        """Build from rows as ``gamedata.rows`` yields them (``dict[str, str]``)."""
        missing = [name for name in REQUIRED_TABLES if name not in tables]
        if missing:
            raise LooksDataError(f"build {build}: tables missing: {missing}")
        rows = {name: _Rows(name, tables[name]) for name in REQUIRED_TABLES}

        models = {
            r.number("ID"): CharacterModel(id=r.number("ID"), sex=r.number("Sex"))
            for r in rows["ChrModel"]
        }
        classes = {
            r.number("ID"): PlayerClass(id=r.number("ID"), name=r.text("Name_lang"))
            for r in rows["ChrClasses"]
        }

        body_types: defaultdict[int, list[BodyType]] = defaultdict(list)
        for r in rows["ChrRaceXChrModel"]:
            body_types[r.number("ChrRacesID")].append(
                BodyType(body_type=r.number("Sex"), chr_model_id=r.number("ChrModelID"))
            )
        linked = frozenset(b.chr_model_id for entries in body_types.values() for b in entries)
        races = {
            r.number("ID"): Race(
                id=r.number("ID"),
                name=r.text("Name_lang"),
                client_file_string=r.text("ClientFileString"),
                flags=r.number("Flags"),
                playable_race_bit=r.number("PlayableRaceBit"),
                faction_id=r.number("FactionID"),
                alliance=r.number("Alliance"),
                body_types=tuple(sorted(body_types[r.number("ID")], key=lambda b: b.body_type)),
            )
            for r in rows["ChrRaces"]
        }

        categories = {
            r.number("ID"): Category(
                id=r.number("ID"),
                name=r.text("CategoryName_lang"),
                order_index=r.number("OrderIndex"),
                spell_shapeshift_form_id=r.number("SpellShapeshiftFormID"),
            )
            for r in rows["ChrCustomizationCategory"]
        }

        choice_list = [
            Choice(
                id=r.number("ID"),
                option_id=r.number("ChrCustomizationOptionID"),
                name=r.text("Name_lang"),
                requirement_id=r.number("ChrCustomizationReqID"),
                order_index=r.number("OrderIndex"),
                ui_order_index=r.number("UiOrderIndex"),
                flags=r.number("Flags"),
            )
            for r in rows["ChrCustomizationChoice"]
        ]
        choices = {c.id: c for c in choice_list}
        by_option: defaultdict[int, list[Choice]] = defaultdict(list)
        for c in choice_list:
            by_option[c.option_id].append(c)

        options = {
            r.number("ID"): Option(
                id=r.number("ID"),
                name=r.text("Name_lang"),
                chr_model_id=r.number("ChrModelID"),
                category_id=r.number("ChrCustomizationCategoryID"),
                order_index=r.number("OrderIndex"),
                option_type=r.number("OptionType"),
                flags=r.number("Flags"),
                requirement_id=r.number("Requirement"),
                choices=tuple(
                    sorted(by_option[r.number("ID")], key=lambda c: (c.ui_order_index, c.id))
                ),
            )
            for r in rows["ChrCustomizationOption"]
        }

        # requirement id -> option id -> choice ids, in table order
        needed: defaultdict[int, dict[int, list[int]]] = defaultdict(dict)
        for r in rows["ChrCustomizationReqChoice"]:
            choice_id = r.number("ChrCustomizationChoiceID")
            owner = choices.get(choice_id)
            # A dependency on a choice the build lacks cannot name its option;
            # it is grouped under option 0.
            option_id = owner.option_id if owner is not None else 0
            needed[r.number("ChrCustomizationReqID")].setdefault(option_id, []).append(choice_id)

        requirements = {
            r.number("ID"): Requirement(
                id=r.number("ID"),
                req_type=r.number("ReqType"),
                class_mask=r.number("ClassMask") & _ALL_32,
                # Read literally. Some rows sign-extend a low-word mask into the
                # high word (RaceMasks_0 = bit 31, Kul Tiran, with
                # RaceMasks_1 = -1), which then admits every race from bit 32
                # up: on 70009 that admits the Skyborne on one Flight Form
                # choice. The data says so; nothing here second-guesses it.
                race_mask=(r.number("RaceMasks_0") & _ALL_32)
                | ((r.number("RaceMasks_1") & _ALL_32) << 32),
                region_group_mask=r.number("RegionGroupMask"),
                achievement_id=r.number("ReqAchievementID"),
                quest_id=r.number("ReqQuestID"),
                item_modified_appearance_id=r.number("ReqItemModifiedAppearanceID"),
                override_archive=r.number("OverrideArchive"),
                source_text=r.text("ReqSource_lang"),
                required_choices=tuple(
                    (option_id, tuple(ids)) for option_id, ids in needed[r.number("ID")].items()
                ),
            )
            for r in rows["ChrCustomizationReq"]
        }

        return cls(
            build=build,
            races=races,
            classes=classes,
            models=models,
            categories=categories,
            options=options,
            choices=choices,
            requirements=requirements,
            linked_model_ids=linked,
        )

    # reading ----------------------------------------------------------------

    def playable_races(self) -> list[Race]:
        """Races the tables flag as playable, by id."""
        return [race for _, race in sorted(self.races.items()) if race.flagged_playable]

    def is_form_or_pet(self, option: Option) -> bool:
        """On a model no race and body type uses, and whose ``ChrModel.Sex``
        is 3: a druid form, a demon, a pet or a mount body [verify]."""
        model = self.models.get(option.chr_model_id)
        return (
            option.chr_model_id not in self.linked_model_ids
            and model is not None
            and model.sex == _MODEL_SEX_SHARED
        )

    def options_for(
        self, race_id: int, body_type: int, class_id: int | None = None
    ) -> list[Option]:
        """Options for a race and body type, in the client's order.

        The race's own model's options, without those whose requirement
        excludes the race (or the class, when given). Then form and pet
        options (``is_form_or_pet``) whose own requirement admits the race
        and class and that have a choice whose requirement restricts class or
        race and admits both: a warlock's demons for a warlock of any race, a
        druid form for a druid of a race that has choices for it. Without a
        class, class-restricted choices count as admitted.

        A class-restricted choice whose classes are exactly those the
        option's other restricted choices exclude, and strictly more of them
        than those choices admit, is the option's placeholder for classes it
        does not apply to (Flight Form's 'None': every class but druid); it
        does not list the option. When the two sides admit equally many
        classes, neither is the placeholder **[verify]**.

        Masks are read literally. On 70009 a Human druid is offered the
        Moonkin "Decoration Color" and "Effects Color" options but not the
        Moonkin body ("Full Transformation"): the colour choices carry
        ClassMask 0xffffe400 (-7168; within the build, druid only) and no
        race mask, while every druid choice of the body is also race-masked
        (Night Elf, Tauren, the Skyborne) and its other choices point at a
        requirement without bit 0x1, which restricts nothing **[verify]**.
        """
        race = self.races.get(race_id)
        if race is None:
            return []
        model = race.model_for(body_type)
        if model is None:
            return []

        def admits(req_id: int) -> bool:
            req = self.requirements.get(req_id)
            if req is None:
                return True
            if not req.admits_race(race):
                return False
            return class_id is None or req.admits_class(class_id)

        def restricted_and_admits(req_id: int) -> bool:
            req = self.requirements.get(req_id)
            if req is None or not req.active:
                return False
            return (req.class_restricted or req.race_restricted) and admits(req_id)

        def lists_option(option: Option) -> bool:
            return any(
                restricted_and_admits(c.requirement_id) and not self._is_placeholder(option, c)
                for c in option.choices
            )

        found: list[Option] = []
        forms: list[Option] = []
        for option in self.options.values():
            if not admits(option.requirement_id):
                continue
            if option.chr_model_id == model:
                found.append(option)
            elif self.is_form_or_pet(option) and lists_option(option):
                forms.append(option)
        found.sort(key=lambda o: (o.order_index, o.id))
        forms.sort(key=lambda o: (o.chr_model_id, o.order_index, o.id))
        return found + forms

    def _is_placeholder(self, option: Option, choice: Choice) -> bool:
        """``choice`` admits exactly the build classes the option's other
        active class-restricted choices exclude, and more of them than those
        choices admit (see ``options_for``). The size test breaks the
        symmetry of a two-choice pair, where each side is the other's
        complement: only the wider side is the placeholder, and on a tie
        neither is."""
        req = self.requirements.get(choice.requirement_id)
        if req is None or not req.active or not req.class_restricted:
            return False
        known = set(self.classes)
        own = set(req.classes(known))
        others: set[int] = set()
        for other in option.choices:
            if other.id == choice.id:
                continue
            other_req = self.requirements.get(other.requirement_id)
            if other_req is not None and other_req.active and other_req.class_restricted:
                others |= set(other_req.classes(known))
        return bool(others) and own == known - others and len(own) > len(others)

    # checking ---------------------------------------------------------------

    def check(self, look: Look) -> LookCheck:
        """Refuse only what the data decides (§13.2); note everything else."""
        findings: list[Finding] = []
        race = self.races.get(look.race_id)
        if race is None:
            findings.append(
                Finding(
                    kind=FindingKind.UNKNOWN_RACE,
                    message=f"race {look.race_id} is not in the tables of build {self.build}",
                )
            )
            return self._verdict(look, findings)
        model = race.model_for(look.body_type)
        if model is None:
            have = [b.body_type for b in race.body_types]
            findings.append(
                Finding(
                    kind=FindingKind.WRONG_BODY_TYPE,
                    message=(
                        f"race {race.name} ({race.id}) has no body type {look.body_type} "
                        f"in build {self.build} (it has {have})"
                    ),
                )
            )
            return self._verdict(look, findings)
        if look.class_id is not None and look.class_id not in self.classes:
            findings.append(
                Finding(
                    kind=FindingKind.CLASS_NOT_IN_BUILD,
                    message=f"class {look.class_id} is not in build {self.build}'s ChrClasses",
                )
            )

        for option_id, choice_id in look.choices.items():
            option = self.options.get(option_id)
            if option is None:
                findings.append(self._unknown("option", option_id, option_id, choice_id))
                continue
            subject = f"option {option.name!r} ({option.id})"
            if option.chr_model_id != model:
                if option.chr_model_id in self.linked_model_ids:
                    findings.append(
                        Finding(
                            kind=FindingKind.WRONG_RACE_OR_BODY_TYPE,
                            message=(
                                f"{subject} belongs to model {option.chr_model_id}, not to "
                                f"{race.name} ({race.id}) body type {look.body_type} "
                                f"(model {model})"
                            ),
                            option_id=option_id,
                            choice_id=choice_id,
                        )
                    )
                    continue
                findings.append(self._unlinked(option, subject, choice_id))
            findings += self._requirement(
                option.requirement_id, subject, look, race, option_id, choice_id, on_choice=False
            )
            choice = self.choices.get(choice_id)
            if choice is None:
                findings.append(self._unknown("choice", choice_id, option_id, choice_id))
                continue
            if choice.option_id != option_id:
                findings.append(
                    Finding(
                        kind=FindingKind.CHOICE_NOT_IN_OPTION,
                        message=(
                            f"choice {choice_id} belongs to option {choice.option_id}, "
                            f"not to {subject}"
                        ),
                        option_id=option_id,
                        choice_id=choice_id,
                    )
                )
                continue
            label = (
                f"choice {choice.name!r} ({choice.id})" if choice.name else f"choice {choice.id}"
            )
            findings += self._requirement(
                choice.requirement_id,
                f"{label} of {subject}",
                look,
                race,
                option_id,
                choice_id,
                on_choice=True,
            )
        return self._verdict(look, findings)

    def _verdict(self, look: Look, findings: list[Finding]) -> LookCheck:
        return LookCheck(
            look=look.name,
            build=self.build,
            refusals=tuple(f for f in findings if f.refuses),
            notes=tuple(f for f in findings if not f.refuses),
        )

    def _unknown(self, what: str, the_id: int, option_id: int, choice_id: int) -> Finding:
        return Finding(
            kind=FindingKind.UNKNOWN_TO_BUILD,
            message=f"{what} {the_id} is unknown to build {self.build} (possibly a hotfix)",
            option_id=option_id,
            choice_id=choice_id,
        )

    def _unlinked(self, option: Option, subject: str, choice_id: int) -> Finding:
        if self.is_form_or_pet(option):
            category = self.categories.get(option.category_id)
            where = f" ({category.name})" if category else ""
            return Finding(
                kind=FindingKind.FORM_OR_PET_OPTION,
                message=(
                    f"{subject}{where} is on model {option.chr_model_id}, which no race and "
                    "body type uses: a form, pet or mount option, checked by its requirements "
                    "only [verify]"
                ),
                option_id=option.id,
                choice_id=choice_id,
            )
        return Finding(
            kind=FindingKind.UNLINKED_MODEL_OPTION,
            message=(
                f"{subject} is on model {option.chr_model_id}, which no race and body type "
                "uses in these tables (possibly an alternate model set), checked by its "
                "requirements only [verify]"
            ),
            option_id=option.id,
            choice_id=choice_id,
        )

    def _class_list(self, req: Requirement) -> str:
        admitted = req.classes(self.classes)
        if not admitted:
            return f"no class in build {self.build} (ClassMask {req.class_mask:#x})"
        return ", ".join(f"{self.classes[c].name} ({c})" for c in admitted)

    def _option_label(self, option_id: int) -> str:
        option = self.options.get(option_id)
        return f"option {option.name!r} ({option_id})" if option else f"option {option_id}"

    def _requirement(
        self,
        requirement_id: int,
        subject: str,
        look: Look,
        race: Race,
        option_id: int,
        choice_id: int,
        *,
        on_choice: bool,
    ) -> list[Finding]:
        if requirement_id == 0:
            return []
        req = self.requirements.get(requirement_id)
        if req is None:
            return [
                Finding(
                    kind=FindingKind.UNKNOWN_TO_BUILD,
                    message=(
                        f"{subject}: requirement {requirement_id} is unknown to build "
                        f"{self.build} (possibly a hotfix)"
                    ),
                    option_id=option_id,
                    choice_id=choice_id,
                )
            ]
        if not req.active:
            return []

        out: list[Finding] = []

        def add(kind: FindingKind, message: str) -> None:
            out.append(
                Finding(
                    kind=kind,
                    message=f"{subject}: {message}",
                    option_id=option_id,
                    choice_id=choice_id,
                )
            )

        if not req.admits_race(race):
            add(
                FindingKind.WRONG_RACE_OR_BODY_TYPE,
                f"requirement {req.id} excludes race {race.name} ({race.id})",
            )
        if req.class_restricted:
            if look.class_id is None:
                if set(req.classes(self.classes)) != set(self.classes):
                    add(
                        FindingKind.CLASS_RESTRICTED,
                        f"only for {self._class_list(req)} (no class given)",
                    )
            elif not req.admits_class(look.class_id):
                add(
                    FindingKind.CLASS_EXCLUDED,
                    f"requirement {req.id} excludes class {look.class_id} "
                    f"(allows {self._class_list(req)})",
                )
        for unlock in req.unlocks():
            add(FindingKind.NEEDS_UNLOCK, unlock)
        if req.region_group_mask:
            add(FindingKind.CONDITION, f"only in region group mask {req.region_group_mask}")
        if req.override_archive == 0:
            add(
                FindingKind.CONDITION,
                "not in the regional override content set (OverrideArchive 0) [verify]",
            )
        elif req.override_archive == 1:
            add(
                FindingKind.CONDITION,
                "only in the regional override content set (OverrideArchive 1) [verify]",
            )
        elif req.override_archive != -1:
            add(FindingKind.CONDITION, f"OverrideArchive {req.override_archive} [verify]")

        for dep_option_id, allowed in req.required_choices:
            if not on_choice:
                # On an option's requirement the list decides whether the
                # barber shop shows the option, not whether a look is valid.
                add(
                    FindingKind.CONDITION,
                    f"shown only when {self._option_label(dep_option_id)} is one of "
                    f"{list(allowed)} [verify]",
                )
                continue
            if dep_option_id == 0:
                add(
                    FindingKind.UNDECIDED_DEPENDENCY,
                    f"depends on choices {list(allowed)} that build {self.build} lacks",
                )
                continue
            held = look.choices.get(dep_option_id)
            if held in allowed:
                continue
            label = self._option_label(dep_option_id)
            if held is None:
                add(
                    FindingKind.UNDECIDED_DEPENDENCY,
                    f"depends on {label} being one of {list(allowed)}; "
                    f"the look does not set option {dep_option_id}",
                )
            elif held not in self.choices:
                # An id this build does not know: it may be a hotfixed choice
                # that satisfies the dependency.
                add(
                    FindingKind.UNDECIDED_DEPENDENCY,
                    f"depends on {label} being one of {list(allowed)}; "
                    f"the look's choice {held} is unknown to build {self.build}",
                )
            else:
                add(
                    FindingKind.MISSING_DEPENDENCY,
                    f"depends on {label} being one of choices {list(allowed)}; "
                    f"the look sets {held}",
                )
        return out


class _Row:
    """One CSV row with typed, located accessors."""

    __slots__ = ("_line", "_row", "_table")

    def __init__(self, table: str, line: int, row: Mapping[str, str]) -> None:
        self._table = table
        self._line = line
        self._row = row

    def text(self, column: str) -> str:
        try:
            return self._row[column]
        except KeyError:
            raise LooksDataError(f"{self._table}: no column {column!r}") from None

    def number(self, column: str) -> int:
        text = self.text(column)
        try:
            return int(text)
        except ValueError:
            raise LooksDataError(
                f"{self._table} row {self._line}: {column}={text!r} is not an integer"
            ) from None


class _Rows:
    """A table's rows, materialised once so they can be read more than once."""

    def __init__(self, table: str, rows: Iterable[Mapping[str, str]]) -> None:
        self._rows = [_Row(table, number, row) for number, row in enumerate(rows, start=1)]

    def __iter__(self) -> Iterator[_Row]:
        return iter(self._rows)
