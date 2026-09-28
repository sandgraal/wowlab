"""looks: character customization tables for one build, and looks checked against them.

Spec: docs/LAB_PLAN.md §13.2. Data: ADR-0022 (tables are keyed by the full
version string; the build comes from the caller or from discovery, never from
this module, L6).

What the tables say, as read here (community convention for these DB2
tables, TrinityCore's ``Player::MeetsChrCustomizationReq``; each is
**[verify]** against the client):

- ``ChrRaceXChrModel`` gives each race one ``ChrModel`` per body type (the
  ``Sex`` column, 0 and 1; the client calls them body types).
  ``ChrCustomizationOption`` rows belong to one model; each
  ``ChrCustomizationChoice`` belongs to one option.
- Options and choices each point at a ``ChrCustomizationReq``. A
  requirement gates nothing unless its ``ReqType`` has bit ``0x1``. When it
  does: a non-zero ``ClassMask`` other than all-ones admits class *c* only if
  bit ``c - 1`` is set; a non-zero race mask other than all-ones
  (``RaceMasks_0`` low word, ``RaceMasks_1`` high word) admits a race only if
  the bit at its ``ChrRaces.PlayableRaceBit`` is set; ``ReqAchievementID``,
  ``ReqQuestID`` and ``ReqItemModifiedAppearanceID`` are unlocks; and every
  ``ChrCustomizationReqChoice`` row for the requirement names a choice, and
  for each option those choices belong to, the look must hold one of them.
- A race is *flagged playable* when its ``PlayableRaceBit`` is not negative
  and its ``Flags`` lack ``0x1`` (NPC only). That is what the tables say,
  not a claim about what a server lets anyone create.

What a check refuses, and nothing else (§13.2): an option or choice for
another race or body type, a class mask that excludes the look's class, and
a missing choice that another depends on. An unlock is a "needs <unlock>"
note. An option or choice id the build lacks is "unknown to build <version>
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
    "Choice",
    "Customizations",
    "Finding",
    "FindingKind",
    "Look",
    "LookCheck",
    "LooksDataError",
    "Option",
    "Race",
    "Requirement",
]

# The tables the model is built from. Element and Conversion are recorded
# fixtures too (§13.2) but nothing here reads them.
REQUIRED_TABLES: tuple[str, ...] = (
    "ChrRaces",
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
_ALL_32 = 0xFFFFFFFF
_ALL_64 = 0xFFFFFFFFFFFFFFFF


class LooksDataError(ValueError):
    """The tables handed over cannot be read as customization data: a table
    or column is missing, or a cell that should be an integer is not."""


# ─── table models ────────────────────────────────────────────────────────────


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class BodyType(_Frozen):
    """One of a race's body types: the ``Sex`` value in ``ChrRaceXChrModel``
    and the model whose options apply."""

    body_type: int
    chr_model_id: int


class Race(_Frozen):
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


class Category(_Frozen):
    """UI grouping of options (``ChrCustomizationCategory``)."""

    id: int
    name: str
    order_index: int


class Requirement(_Frozen):
    """One ``ChrCustomizationReq`` row, with the choices it depends on
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
    # option id -> choice ids, one of which the look must hold
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

    def classes(self) -> tuple[int, ...]:
        """Class ids the mask admits (1-32)."""
        return tuple(c for c in range(1, 33) if self.class_mask >> (c - 1) & 1)

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
    CLASS_RESTRICTED = "class_restricted"
    CONDITION = "condition"
    UNDECIDED_DEPENDENCY = "undecided_dependency"


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
    """Races, body types, options, choices, requirements and categories of one
    build, keyed by id."""

    build: str
    races: dict[int, Race]
    categories: dict[int, Category]
    options: dict[int, Option]
    choices: dict[int, Choice]
    requirements: dict[int, Requirement]
    chr_model_ids: frozenset[int]

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

        models = frozenset(r.number("ID") for r in rows["ChrModel"])

        body_types: defaultdict[int, list[BodyType]] = defaultdict(list)
        for r in rows["ChrRaceXChrModel"]:
            body_types[r.number("ChrRacesID")].append(
                BodyType(body_type=r.number("Sex"), chr_model_id=r.number("ChrModelID"))
            )
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
            # it is grouped under option 0 and can never be satisfied.
            option_id = owner.option_id if owner is not None else 0
            needed[r.number("ChrCustomizationReqID")].setdefault(option_id, []).append(choice_id)

        requirements = {
            r.number("ID"): Requirement(
                id=r.number("ID"),
                req_type=r.number("ReqType"),
                class_mask=r.number("ClassMask") & _ALL_32,
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
            categories=categories,
            options=options,
            choices=choices,
            requirements=requirements,
            chr_model_ids=models,
        )

    # reading ----------------------------------------------------------------

    def playable_races(self) -> list[Race]:
        """Races the tables flag as playable, by id."""
        return [race for _, race in sorted(self.races.items()) if race.flagged_playable]

    def options_for(
        self, race_id: int, body_type: int, class_id: int | None = None
    ) -> list[Option]:
        """Options of the race's model for that body type, in the client's
        order, without those whose own requirement excludes the race, or the
        class when one is given."""
        race = self.races.get(race_id)
        if race is None:
            return []
        model = race.model_for(body_type)
        found: list[Option] = []
        for option in self.options.values():
            if option.chr_model_id != model:
                continue
            req = self.requirements.get(option.requirement_id)
            if req is not None:
                if not req.admits_race(race):
                    continue
                if class_id is not None and not req.admits_class(class_id):
                    continue
            found.append(option)
        return sorted(found, key=lambda o: (o.order_index, o.id))

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

        for option_id, choice_id in look.choices.items():
            option = self.options.get(option_id)
            if option is None:
                findings.append(self._unknown("option", option_id, option_id, choice_id))
                continue
            if option.chr_model_id != model:
                findings.append(
                    Finding(
                        kind=FindingKind.WRONG_RACE_OR_BODY_TYPE,
                        message=(
                            f"option {option.name!r} ({option.id}) belongs to model "
                            f"{option.chr_model_id}, not to {race.name} ({race.id}) "
                            f"body type {look.body_type} (model {model})"
                        ),
                        option_id=option_id,
                        choice_id=choice_id,
                    )
                )
                continue
            subject = f"option {option.name!r} ({option.id})"
            findings += self._requirement(
                option.requirement_id, subject, look, race, option_id, choice_id
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
                choice.requirement_id, f"{label} of {subject}", look, race, option_id, choice_id
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

    def _requirement(
        self,
        requirement_id: int,
        subject: str,
        look: Look,
        race: Race,
        option_id: int,
        choice_id: int,
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
                add(
                    FindingKind.CLASS_RESTRICTED,
                    f"only for classes {list(req.classes())} (no class given)",
                )
            elif not req.admits_class(look.class_id):
                add(
                    FindingKind.CLASS_EXCLUDED,
                    f"requirement {req.id} excludes class {look.class_id} "
                    f"(allows {list(req.classes())})",
                )
        for unlock in req.unlocks():
            add(FindingKind.NEEDS_UNLOCK, unlock)
        if req.region_group_mask:
            add(FindingKind.CONDITION, f"only in region group mask {req.region_group_mask}")
        if req.override_archive != -1:
            add(FindingKind.CONDITION, f"only when OverrideArchive is {req.override_archive}")

        for dep_option_id, allowed in req.required_choices:
            if any(c in allowed for c in look.choices.values()):
                continue
            held = look.choices.get(dep_option_id)
            if held is not None and held not in self.choices:
                # The look holds an id this build does not know for that option:
                # it may be a hotfixed choice that satisfies the dependency.
                add(
                    FindingKind.UNDECIDED_DEPENDENCY,
                    f"depends on option {dep_option_id} being one of {list(allowed)}; "
                    f"the look's choice {held} is unknown to build {self.build}",
                )
                continue
            dep = self.options.get(dep_option_id)
            dep_label = f"option {dep.name!r} ({dep_option_id})" if dep else "an option"
            add(
                FindingKind.MISSING_DEPENDENCY,
                f"depends on {dep_label} being one of choices {list(allowed)}",
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
