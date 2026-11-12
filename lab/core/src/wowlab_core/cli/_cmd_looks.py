"""`wowlab looks`: races, options, save, show, compare, import-char and page
(M16-111)."""

import os
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Literal

import typer

from wowlab_core import (
    gamedata,
    install,
    labaddon,
    looks,
    lookspage,
    lookstore,
)
from wowlab_core.cli._cmd_char import (
    _picked_words,
)
from wowlab_core.cli._cmd_db2 import (
    _build_for,
    _check_table_key,
)
from wowlab_core.cli._cmd_doctor import (
    _open_gamedata,
)
from wowlab_core.cli._common import (
    EXIT_ERROR,
    EXIT_USAGE,
    AccountOpt,
    CharacterOpt,
    CliError,
    FlavorOpt,
    JsonOpt,
    RootOpt,
    _bytes,
    _emit,
    _handled,
    _install_words,
    _is_lab_file,
    _keep_notes,
    _lab_char_file,
    _listed,
    _local_time,
    _note,
    _open,
    _Out,
    _player_copy,
    _say,
    _say_note,
    _select_account,
    looks_app,
)

# ─── looks ───────────────────────────────────────────────────────────────────

_PLAYABLE_NOTE = (
    "Flagged playable by the build's ChrRaces rows (a PlayableRaceBit, not NPC-only); "
    "not a claim about what a server lets anyone create. Body types are numbered as the "
    "tables number them (ChrRaceXChrModel.Sex 0 and 1); the game's own screens may number "
    "them differently [verify]."
)
_EXPORTED_ONLY = (
    "Checked against build {version}'s exported tables only: not what a server allows, not "
    "hotfixes the server sends (Cache/ADB), and not what this account has unlocked."
)
_HOTFIX_HINT = (
    "is not in build {version}'s tables: check the id with `wowlab looks options`; an id "
    "read from the game may come from a hotfix the exported tables lack"
)
_IMPORTED_REMARK = (
    "Look {name} was imported from a character's lab-addon customization record (wowlab "
    "looks import-char): the choices the addon had last recorded in the barber shop before "
    "the import. It may miss a change applied during that visit [verify], and any change since."
)
_IMPORTED_BUILD_REMARK = (
    "Look {name} comes from a record the lab-addon made on client build {client}."
)
_IMPORTED_UNKNOWN = (
    " (recorded by client {client}, not the build of these tables: the id may exist only in "
    "that build, or come from a hotfix)"
)
_MODEL_HOTFIX = " (possibly a hotfix)"
_ID = re.compile(r"[0-9]{1,9}")
_ALLIANCE_WORDS = {0: "Alliance", 1: "Horde", 2: "neither faction"}
_SEX_WORDS = {"male": 0, "female": 1}


class LooksRace(_Out):
    id: int
    name: str
    client_file_string: str
    alliance: int  # ChrRaces.Alliance: 0 Alliance, 1 Horde, 2 neither
    flagged_playable: bool
    body_types: list[looks.BodyType]  # body_type as ChrRaceXChrModel.Sex


class LooksRacesReport(_Out):
    """`wowlab looks races --json`."""

    build: str
    races: list[LooksRace]
    notes: list[str]


class LooksChoice(_Out):
    id: int
    name: str
    # The model's check of a look holding only this choice, less the findings
    # every choice of the option shares (those are the option's `notes`).
    refusals: list[looks.Finding]
    notes: list[looks.Finding]


class LooksOption(_Out):
    id: int
    name: str
    chr_model_id: int
    category: str | None
    form_or_pet: bool  # on a model no race uses (druid form, demon, pet) [verify]
    notes: list[looks.Finding]  # findings every choice shares (two or more choices)
    choices: list[LooksChoice]


class LooksBodyType(_Out):
    body_type: int
    chr_model_id: int
    options: list[LooksOption]


class LooksOptionsReport(_Out):
    """`wowlab looks options --json`."""

    build: str
    race: LooksRace
    class_id: int | None
    class_name: str | None
    body_types: list[LooksBodyType]
    notes: list[str]


class LooksChoiceRef(_Out):
    option_id: int
    option_name: str | None  # None: the option is unknown to the build
    choice_id: int
    # None: unknown to the build, or not a choice of this option (then
    # `choice_of_option` says whose it is); "" when the table names none.
    choice_name: str | None
    # The option the build files this choice under, when that is not
    # `option_id`; None when the choice is this option's or unknown to the build.
    choice_of_option: int | None = None


class LookReport(_Out):
    """`wowlab looks save --json` and `wowlab looks show NAME --json`.

    `path` is None when `save` refused the look (nothing was written)."""

    name: str
    path: str | None
    saved_build: str | None  # the build whose tables checked it when it was saved
    # typed (`save`) or imported (`import-char`); decides the unknown-id wording
    origin: Literal["typed", "imported"] = "typed"
    build: str  # the build whose tables checked it now
    race_id: int
    race_name: str | None
    body_type: int
    class_id: int | None
    class_name: str | None
    choices: list[LooksChoiceRef]
    refused: bool
    refusals: list[looks.Finding]
    notes: list[looks.Finding]
    remarks: list[str]


class LookSummary(_Out):
    name: str
    saved_build: str
    race_id: int
    race_name: str | None
    body_type: int
    class_id: int | None
    choices: int
    refused: bool
    refusals: int
    notes: int


class LooksListReport(_Out):
    """`wowlab looks show --json` (no name). The command exits 1 when `damaged`
    is not empty. `build` is None when there is no look to check."""

    directory: str
    build: str | None
    looks: list[LookSummary]
    damaged: list[lookstore.DamagedLook]
    remarks: list[str]


class LooksDifference(_Out):
    option_id: int
    option_name: str | None
    a: LooksChoiceRef | None  # None: the look does not set this option
    b: LooksChoiceRef | None


class LooksCompareReport(_Out):
    """`wowlab looks compare --json`: both looks checked against one build."""

    build: str
    a: LookReport
    b: LookReport
    same_race: bool
    same_body_type: bool
    same_class: bool
    same: list[LooksChoiceRef]  # options both looks set to the same choice
    different: list[LooksDifference]  # set to different choices, or by one look only
    remarks: list[str]


LooksBuildOpt = Annotated[
    str | None,
    typer.Option(
        "--build",
        help="Full version string whose tables to use. Default: the flavor's version; "
        "with no install found, the build a saved look was checked against, or the one "
        "build whose customization tables are cached.",
    ),
]
SexOpt = Annotated[
    str | None,
    typer.Option(
        "--sex",
        help="Body type as the tables number it: 0 or 1; 'male' and 'female' are read as 0 and 1.",
    ),
]
ClassOpt = Annotated[
    str | None,
    typer.Option("--class", help="Class id or name, as the build's ChrClasses has it."),
]
LookNameArg = Annotated[str, typer.Argument(metavar="NAME", help="The look's name.")]


def _cached_look_builds(data: gamedata.GameData) -> list[str]:
    """Builds whose every customization table is in the cache (a listing only)."""
    tables = data.cache_dir / "tables"
    if not tables.is_dir():
        return []
    found: list[str] = []
    for entry in sorted(tables.iterdir()):
        try:
            if all(data.table_path(name, entry.name).is_file() for name in looks.REQUIRED_TABLES):
                found.append(entry.name)
        except ValueError:  # not a build folder
            continue
    return found


def _looks_build(
    data: gamedata.GameData,
    build: str | None,
    root: Path | None,
    flavor: str | None,
    saved_builds: Sequence[str] = (),
) -> tuple[str, list[str]]:
    """The build to check against, and a remark when it did not come from
    `--build` or the install (L6: nothing here names a build)."""
    if build is not None:
        return build, []
    try:
        return _build_for(None, root, flavor), []
    except install.InstallNotFoundError as exc:
        saved = sorted(set(saved_builds))
        if len(saved) == 1:
            return saved[0], [
                f"No install found; checked against build {saved[0]}, the build the look "
                "was saved against (--build chooses another)."
            ]
        cached = _cached_look_builds(data)
        if len(cached) == 1:
            return cached[0], [
                f"No install found; using build {cached[0]}, the one build whose "
                "customization tables are cached (--build chooses another)."
            ]
        have = f" (cached: {', '.join(cached)})" if cached else ""
        raise _keep_notes(
            CliError(
                f"{_install_words(exc)}. Without an install, pass --build with a full "
                f"version string{have}"
            ),
            exc,
        ) from exc


def _load_model(data: gamedata.GameData, build: str) -> looks.Customizations:
    _check_table_key(data, looks.REQUIRED_TABLES[0], build)
    try:
        return looks.Customizations.from_gamedata(data, build)
    except looks.LooksDataError as exc:
        raise CliError(f"the customization tables of build {build} cannot be read: {exc}") from exc


def _race_out(race: looks.Race) -> LooksRace:
    return LooksRace(
        id=race.id,
        name=race.name,
        client_file_string=race.client_file_string,
        alliance=race.alliance,
        flagged_playable=race.flagged_playable,
        body_types=list(race.body_types),
    )


def _race_label(race: looks.Race) -> str:
    return f"{race.name} ({race.id})"


def _as_id(text: str) -> int | None:
    """An id typed on the command line: ASCII digits, nine at most; else None."""
    stripped = text.strip()
    return int(stripped) if _ID.fullmatch(stripped) else None


def _faction(alliance: int) -> str:
    return _ALLIANCE_WORDS.get(alliance, f"Alliance column {alliance}")


def _resolve_race(model: looks.Customizations, text: str) -> tuple[looks.Race, list[str]]:
    """The race `text` names, and a remark when the playable flag chose between rows."""
    playable = ", ".join(_race_label(r) for r in model.playable_races())
    typed = text.strip()
    race_id = _as_id(typed)
    if race_id is not None:
        race = model.races.get(race_id)
        if race is None:
            raise CliError(
                f"no race {typed} in build {model.build}'s ChrRaces (flagged playable: {playable})",
                EXIT_USAGE,
            )
        return race, []
    folded = typed.casefold()
    matches = [r for _, r in sorted(model.races.items()) if r.name.casefold() == folded] or [
        r for _, r in sorted(model.races.items()) if r.client_file_string.casefold() == folded
    ]
    remarks: list[str] = []
    if len(matches) > 1:
        flagged = [r for r in matches if r.flagged_playable]
        if len(flagged) == 1:
            others = [r for r in matches if not r.flagged_playable]
            remarks.append(
                f"{typed!r} also names {', '.join(_race_label(r) for r in others)}, not flagged "
                f"playable; using {_race_label(flagged[0])}. Give "
                f"{' or '.join(str(r.id) for r in others)} for that row."
            )
        matches = flagged or matches
    if len(matches) == 1:
        return matches[0], remarks
    if not matches:
        raise CliError(
            f"no race {typed!r} in build {model.build}'s ChrRaces (flagged playable: {playable})",
            EXIT_USAGE,
        )
    listed = ", ".join(f"{r.name} ({r.id}, {_faction(r.alliance)})" for r in matches)
    shared_models = len({r.body_types for r in matches}) == 1
    factions = len({r.alliance for r in matches}) == len(matches)
    if shared_models and factions:
        count = "two" if len(matches) == 2 else str(len(matches))
        raise CliError(
            f"race {typed!r} matches {listed}: one race's {count} faction rows, sharing "
            "models; give the id (race-masked choices are checked against it)",
            EXIT_USAGE,
        )
    raise CliError(f"race {typed!r} matches {listed}; give the id", EXIT_USAGE)


def _resolve_class(model: looks.Customizations, text: str | None) -> int | None:
    if text is None:
        return None
    typed = _as_id(text)
    if typed is not None:
        return typed
    folded = text.strip().casefold()
    for class_id, player_class in sorted(model.classes.items()):
        if player_class.name.casefold() == folded:
            return class_id
    known = ", ".join(f"{c.name} ({i})" for i, c in sorted(model.classes.items()))
    raise CliError(f"no class {text!r} in build {model.build}'s ChrClasses ({known})", EXIT_USAGE)


def _parse_sex(text: str | None) -> int | None:
    if text is None:
        return None
    word = text.strip().casefold()
    if word in _SEX_WORDS:
        return _SEX_WORDS[word]
    number = _as_id(word)
    if number is not None:
        return number
    raise CliError(f"--sex takes 0, 1, male or female, not {text!r}", EXIT_USAGE)


def _parse_choices(pairs: Sequence[str]) -> dict[int, int]:
    out: dict[int, int] = {}
    for pair in pairs:
        option, sep, choice = pair.partition("=")
        option_id, choice_id = _as_id(option), _as_id(choice)
        if not sep or option_id is None or choice_id is None:
            raise CliError(
                f"--choice takes OPTION=CHOICE with two ids (digits 0-9, nine at most), as "
                f"`wowlab looks options` lists them, not {pair!r}",
                EXIT_USAGE,
            )
        if option_id in out:
            raise CliError(f"option {option_id} is given twice", EXIT_USAGE)
        out[option_id] = choice_id
    return out


def _class_name(model: looks.Customizations, class_id: int | None) -> str | None:
    if class_id is None:
        return None
    found = model.classes.get(class_id)
    return found.name if found else None


def _choice_ref(model: looks.Customizations, option_id: int, choice_id: int) -> LooksChoiceRef:
    option = model.options.get(option_id)
    choice = model.choices.get(choice_id)
    return LooksChoiceRef(
        option_id=option_id,
        option_name=option.name if option else None,
        choice_id=choice_id,
        # A choice of another option is not this option's choice: no name here,
        # and whose it is (the check reports the mismatch as a refusal).
        choice_name=choice.name if choice is not None and choice.option_id == option_id else None,
        choice_of_option=(
            choice.option_id if choice is not None and choice.option_id != option_id else None
        ),
    )


def _ref_choice_text(ref: LooksChoiceRef) -> str:
    """The choice half of a reference, as `save`, `show` and `compare` print it."""
    if ref.choice_of_option is not None:
        return f"{ref.choice_id} (a choice of option {ref.choice_of_option})"
    if ref.choice_name is None:
        return f"{ref.choice_id} (unknown to this build)"
    return f"{ref.choice_id} {ref.choice_name!r}" if ref.choice_name else str(ref.choice_id)


def _look_report(
    model: looks.Customizations,
    saved: lookstore.SavedLook,
    path: Path | None,
    remarks: Sequence[str] = (),
    *,
    exported_only: bool = True,
    imported_remark: bool = True,
) -> LookReport:
    """`exported_only=False` leaves the tables-only remark to the caller
    (`compare` states it once, at its top level). An id the build lacks is
    worded by the look's origin (M11-23): a typed id as a thing to check, an
    imported one as the model words it, "(possibly a hotfix)"."""
    look = saved.look
    verdict = model.check(look)
    race = model.races.get(look.race_id)
    notes = list(remarks)
    if saved.origin == "imported" and path is not None and imported_remark:
        notes.append(_IMPORTED_REMARK.format(name=look.name))
        if saved.recorded_client_build is not None:
            notes.append(
                _IMPORTED_BUILD_REMARK.format(name=look.name, client=saved.recorded_client_build)
            )
    if saved.saved_build != model.build:
        notes.append(
            f"Saved against build {saved.saved_build}; checked here against build {model.build}."
        )
    if exported_only:
        notes.append(_EXPORTED_ONLY.format(version=model.build))
    report = LookReport(
        name=look.name,
        path=str(path) if path is not None else None,
        saved_build=saved.saved_build if path is not None else None,
        origin=saved.origin,
        build=model.build,
        race_id=look.race_id,
        race_name=race.name if race else None,
        body_type=look.body_type,
        class_id=look.class_id,
        class_name=_class_name(model, look.class_id),
        choices=[_choice_ref(model, o, c) for o, c in look.choices.items()],
        refused=verdict.refused,
        refusals=list(verdict.refusals),
        notes=list(verdict.notes),
        remarks=notes,
    )
    if saved.origin == "typed":
        return _save_wording(model, report)
    return _imported_wording(model, report, saved.recorded_client_build)


def _imported_wording(
    model: looks.Customizations, report: LookReport, client: str | None
) -> LookReport:
    """An imported look keeps the model's "(possibly a hotfix)", unless the
    recording client's build is known and differs from the checking build:
    then an option or choice id from the record may exist only in that build
    (either direction; M11-23 review, P4 and D1). A requirement id the
    tables name is theirs, not the record's, and keeps the model's words."""
    if client is None or client == model.build:
        return report
    tail = _IMPORTED_UNKNOWN.format(client=client)

    def from_record(f: looks.Finding) -> bool:
        # Only the option and choice ids came from the record; a requirement
        # id an option or choice names comes from the tables themselves.
        return (
            f.option_id is not None and f.message.startswith(f"option {f.option_id} is unknown")
        ) or (f.choice_id is not None and f.message.startswith(f"choice {f.choice_id} is unknown"))

    def reworded(f: looks.Finding) -> looks.Finding:
        if (
            f.kind is not looks.FindingKind.UNKNOWN_TO_BUILD
            or not f.message.endswith(_MODEL_HOTFIX)
            or not from_record(f)
        ):
            return f
        return f.model_copy(update={"message": f.message[: -len(_MODEL_HOTFIX)] + tail})

    return report.model_copy(update={"notes": [reworded(f) for f in report.notes]})


def _save_wording(model: looks.Customizations, report: LookReport) -> LookReport:
    """A typed look (`save`, and `show`/`compare` of a look saved that way)
    words an id the build lacks as a thing to check; an imported look keeps
    the model's "(possibly a hotfix)" (M11-23)."""
    hint = _HOTFIX_HINT.format(version=model.build)

    def reworded(f: looks.Finding) -> looks.Finding:
        if f.kind is not looks.FindingKind.UNKNOWN_TO_BUILD:
            return f
        if f.option_id is not None and f.message.startswith(f"option {f.option_id} is unknown"):
            return f.model_copy(update={"message": f"option {f.option_id} {hint}"})
        option = model.options.get(f.option_id) if f.option_id is not None else None
        if (
            option is not None
            and f.choice_id is not None
            and f.message.startswith(f"choice {f.choice_id} is unknown")
        ):
            return f.model_copy(
                update={
                    "message": f"choice {f.choice_id} of option {option.name!r} ({option.id}) "
                    f"{hint}"
                }
            )
        return f

    return report.model_copy(update={"notes": [reworded(f) for f in report.notes]})


def _ref_text(ref: LooksChoiceRef) -> str:
    option = (
        f"{ref.option_name} ({ref.option_id})"
        if ref.option_name is not None
        else f"option {ref.option_id} (unknown to this build)"
    )
    return f"{option} = {_ref_choice_text(ref)}"


def _print_findings(
    refusals: Sequence[looks.Finding], notes: Sequence[looks.Finding], pad: str
) -> None:
    # A finding is the tables' words about the tables' ids and names.
    for f in refusals:
        _say(f"{pad}refused: {_player_copy(f.message)}")
    for f in notes:
        _say(f"{pad}note: {_player_copy(f.message)}")


def _print_look(report: LookReport, *, heading: str) -> None:
    _say(f"{heading} {report.name} (checked against build {report.build})")
    if report.path is not None:
        _say(f"  file:      {report.path}")
    race = f"{report.race_name} ({report.race_id})" if report.race_name else f"{report.race_id}"
    _say(f"  race:      {race}, body type {report.body_type}")
    if report.class_id is None:
        _say("  class:     not given (class-restricted choices are noted, not refused)")
    else:
        _say(f"  class:     {report.class_name or 'not in this build'} ({report.class_id})")
    _say(f"  choices:   {len(report.choices)}")
    for ref in report.choices:
        _say(f"    {_ref_text(ref)}")
    if report.refused:
        _say(
            f"  verdict:   refused ({len(report.refusals)} reason(s)), {len(report.notes)} note(s)"
        )
    else:
        _say(f"  verdict:   not refused, {len(report.notes)} note(s)")
    _print_findings(report.refusals, report.notes, "    ")
    for remark in report.remarks:  # a look's name holds no bracket or parenthesis
        _say_note(remark)


@looks_app.command("races", short_help="Playable races and their body types.")
@_handled
def looks_races(
    build: LooksBuildOpt = None,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
) -> None:
    """Races the build's tables flag as playable, with their body types.

    \f
    JSON: LooksRacesReport."""
    with _open_gamedata() as data:
        version, remarks = _looks_build(data, build, root, flavor)
        model = _load_model(data, version)
    report = LooksRacesReport(
        build=version,
        races=[_race_out(r) for r in model.playable_races()],
        notes=[_PLAYABLE_NOTE, *remarks],
    )
    if json_out:
        _emit(report)
        return
    _say(f"Races flagged playable in build {version}'s ChrRaces:")
    for r in report.races:
        side = _ALLIANCE_WORDS.get(r.alliance, f"Alliance column {r.alliance}")
        bodies = ", ".join(f"{b.body_type} (model {b.chr_model_id})" for b in r.body_types)
        _say(f"  {r.id:>4}  {r.name}  [{side}]  body types {bodies or 'none'}")
    for n in report.notes:
        _say_note(n)


def _choice_text(choice: looks.Choice, subject: str, f: looks.Finding) -> str | None:
    """The text after "<choice label> of <option subject>: " (the model's wording),
    or None when the finding is not about this choice."""
    label = f"choice {choice.name!r} ({choice.id})" if choice.name else f"choice {choice.id}"
    prefix = f"{label} of {subject}: "
    return f.message[len(prefix) :] if f.message.startswith(prefix) else None


def _option_out(
    model: looks.Customizations,
    race: looks.Race,
    body: looks.BodyType,
    class_id: int | None,
    option: looks.Option,
) -> LooksOption:
    """One option, each choice with the check of a look holding only that choice.

    A finding on the option itself (its requirement, its model), which starts
    with the option's subject, is the same for every choice and is shown once
    on the option, whatever the number of choices. A finding every choice has,
    compared on its kind and the text after the choice's label, is shown once
    on the option as "every choice: <text>"."""
    checked: list[tuple[looks.Choice, looks.LookCheck]] = [
        (
            choice,
            model.check(
                looks.Look(
                    name="-",
                    race_id=race.id,
                    body_type=body.body_type,
                    class_id=class_id,
                    choices={option.id: choice.id},
                )
            ),
        )
        for choice in option.choices
    ]
    subject = f"option {option.name!r} ({option.id})"
    option_notes: list[looks.Finding] = []
    on_option: set[tuple[str, str]] = set()
    for _, verdict in checked:
        for f in (*verdict.refusals, *verdict.notes):
            key = (f.kind.value, f.message)
            if f.message.startswith(subject) and key not in on_option:
                on_option.add(key)
                option_notes.append(f.model_copy(update={"choice_id": None}))
    shared: set[tuple[str, str]] = set()
    if len(checked) > 1:
        shared = set.intersection(
            *(
                {
                    (f.kind.value, text)
                    for f in (*verdict.refusals, *verdict.notes)
                    if (text := _choice_text(choice, subject, f)) is not None
                }
                for choice, verdict in checked
            )
        )
        first_choice, first = checked[0]
        for f in (*first.refusals, *first.notes):
            text = _choice_text(first_choice, subject, f)
            if text is not None and (f.kind.value, text) in shared:
                option_notes.append(
                    f.model_copy(update={"choice_id": None, "message": f"every choice: {text}"})
                )

    def keep(choice: looks.Choice, f: looks.Finding) -> bool:
        if (f.kind.value, f.message) in on_option:
            return False
        text = _choice_text(choice, subject, f)
        return text is None or (f.kind.value, text) not in shared

    category = model.categories.get(option.category_id)
    return LooksOption(
        id=option.id,
        name=option.name,
        chr_model_id=option.chr_model_id,
        category=category.name if category else None,
        form_or_pet=model.is_form_or_pet(option),
        notes=option_notes,
        choices=[
            LooksChoice(
                id=choice.id,
                name=choice.name,
                refusals=[f for f in verdict.refusals if keep(choice, f)],
                notes=[f for f in verdict.notes if keep(choice, f)],
            )
            for choice, verdict in checked
        ],
    )


def _options_for_body(
    model: looks.Customizations, race: looks.Race, body: looks.BodyType, class_id: int | None
) -> LooksBodyType:
    return LooksBodyType(
        body_type=body.body_type,
        chr_model_id=body.chr_model_id,
        options=[
            _option_out(model, race, body, class_id, option)
            for option in model.options_for(race.id, body.body_type, class_id)
        ],
    )


@looks_app.command("options", short_help="A race's customization options and choices.")
@_handled
def looks_options(
    race: Annotated[str, typer.Argument(help="Race id or name, as `looks races` lists it.")],
    sex: SexOpt = None,
    class_: ClassOpt = None,
    build: LooksBuildOpt = None,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
) -> None:
    """A race's options and choices per body type (every body type unless --sex),
    for a class when --class is given, with what the tables say about each
    choice: refusals, "needs <unlock>" and other notes.

    \f
    JSON: LooksOptionsReport."""
    body_type = _parse_sex(sex)
    with _open_gamedata() as data:
        version, remarks = _looks_build(data, build, root, flavor)
        model = _load_model(data, version)
    chosen, race_remarks = _resolve_race(model, race)
    class_id = _resolve_class(model, class_)
    bodies = [b for b in chosen.body_types if body_type is None or b.body_type == body_type]
    if not bodies:
        have = [b.body_type for b in chosen.body_types]
        raise CliError(
            f"race {_race_label(chosen)} has no body type {body_type} in build {version} "
            f"(it has {have})",
            EXIT_USAGE,
        )
    notes = [*race_remarks, *remarks]
    if not chosen.flagged_playable:
        notes.append(f"{_race_label(chosen)} is not flagged playable in build {version}.")
    if class_id is not None and class_id not in model.classes:
        notes.append(f"Class {class_id} is not in build {version}'s ChrClasses.")
    if class_id is None:
        notes.append("No --class: class-restricted choices are listed with a note.")
    report = LooksOptionsReport(
        build=version,
        race=_race_out(chosen),
        class_id=class_id,
        class_name=_class_name(model, class_id),
        body_types=[_options_for_body(model, chosen, b, class_id) for b in bodies],
        notes=notes,
    )
    if json_out:
        _emit(report)
        return
    who = _race_label(chosen)
    if class_id is not None:
        who += f", {report.class_name or 'class'} ({class_id})"
    for body in report.body_types:
        _say(f"{who}, body type {body.body_type} (model {body.chr_model_id}), build {version}:")
        for option in body.options:
            where = f" [{option.category}]" if option.category else ""
            form = " (form or pet option)" if option.form_or_pet else ""
            _say(
                f"  option {option.id}  {option.name}{where}{form}: {len(option.choices)} choice(s)"
            )
            _print_findings(
                [f for f in option.notes if f.refuses],
                [f for f in option.notes if not f.refuses],
                "      ",
            )
            for choice in option.choices:
                _say(f"    {choice.id:>7}  {choice.name}".rstrip())
                _print_findings(choice.refusals, choice.notes, "             ")
    for n in notes:
        _say_note(n)


@looks_app.command("save", short_help="Check a look against the tables and save it.")
@_handled
def looks_save(
    name: LookNameArg,
    race: Annotated[str, typer.Option("--race", help="Race id or name.")],
    sex: Annotated[
        str,
        typer.Option(
            "--sex",
            help="Body type: 0 or 1; 'male' and 'female' read as 0 and 1.",
        ),
    ],
    choice: Annotated[
        list[str] | None,
        typer.Option(
            "--choice",
            help="OPTION=CHOICE, two ids as `wowlab looks options` lists them. Repeat for more.",
        ),
    ] = None,
    class_: ClassOpt = None,
    replace: Annotated[
        bool, typer.Option("--replace", help="Overwrite a saved look with this name.")
    ] = False,
    build: LooksBuildOpt = None,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
) -> None:
    """Check a look against the build's tables and save it under the user data
    directory (never in an install). A look the tables refuse is not saved
    (exit 1); notes ("needs <unlock>", "unknown to build ...") are shown and do
    not stop the save.

    \f
    JSON: LookReport."""
    try:
        lookstore.check_name(name)
    except lookstore.LookStoreError as exc:
        raise CliError(str(exc), EXIT_USAGE) from exc
    body_type = _parse_sex(sex)
    assert body_type is not None
    choices = _parse_choices(choice or ())
    with _open_gamedata() as data:
        version, remarks = _looks_build(data, build, root, flavor)
        model = _load_model(data, version)
    chosen, race_remarks = _resolve_race(model, race)
    remarks = [*race_remarks, *remarks]
    look = looks.Look(
        name=name,
        race_id=chosen.id,
        body_type=body_type,
        class_id=_resolve_class(model, class_),
        choices=choices,
    )
    saved = lookstore.SavedLook(saved_build=version, look=look)
    report = _look_report(model, saved, None, remarks)
    if report.refused:
        if json_out:
            _emit(report)
        else:
            _print_look(report, heading="Not saved: the tables refuse look")
        raise CliError(f"look {name} is refused by build {version}'s tables; nothing was saved")
    path = lookstore.LookStore().save(saved, replace=replace)
    report = _look_report(model, saved, path, remarks)
    if json_out:
        _emit(report)
    else:
        _print_look(report, heading="Saved look")


@looks_app.command("show", short_help="One saved look, or every saved look, with its verdict.")
@_handled
def looks_show(
    name: Annotated[
        str | None,
        typer.Argument(metavar="[NAME]", help="A saved look. Default: every saved look."),
    ] = None,
    build: LooksBuildOpt = None,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
) -> None:
    """One saved look checked against the build's tables, or every saved look
    with its verdict; names each damaged file and then exits 1.

    \f
    JSON: LookReport with NAME, else LooksListReport."""
    store = lookstore.LookStore()
    try:
        if name is not None:
            lookstore.check_name(name)
    except lookstore.LookStoreError as exc:
        raise CliError(str(exc), EXIT_USAGE) from exc
    if name is not None:
        path = store.locate(name)
        one = store.read(path)
        with _open_gamedata() as data:
            version, remarks = _looks_build(data, build, root, flavor, [one.saved_build])
            model = _load_model(data, version)
        report = _look_report(model, one, path, remarks)
        if json_out:
            _emit(report)
        else:
            _print_look(report, heading="Look")
        return
    found, damaged = store.listing()
    summaries: list[LookSummary] = []
    version_used: str | None = None
    list_remarks: list[str] = []
    if found:
        with _open_gamedata() as data:
            version_used, list_remarks = _looks_build(
                data, build, root, flavor, [s.saved_build for s in found]
            )
            model = _load_model(data, version_used)
        list_remarks = [*list_remarks, _EXPORTED_ONLY.format(version=version_used)]
        for s in found:
            verdict = model.check(s.look)
            race = model.races.get(s.look.race_id)
            summaries.append(
                LookSummary(
                    name=s.look.name,
                    saved_build=s.saved_build,
                    race_id=s.look.race_id,
                    race_name=race.name if race else None,
                    body_type=s.look.body_type,
                    class_id=s.look.class_id,
                    choices=len(s.look.choices),
                    refused=verdict.refused,
                    refusals=len(verdict.refusals),
                    notes=len(verdict.notes),
                )
            )
    listing = LooksListReport(
        directory=str(store.root),
        build=version_used,
        looks=summaries,
        damaged=damaged,
        remarks=list_remarks,
    )
    if json_out:
        _emit(listing)
    else:
        if not summaries and not damaged:
            _say(f"No saved looks (in {store.root}).")
        else:
            _say(f"Saved looks in {store.root}, checked against build {version_used}:")
        for item in summaries:
            who = f"{item.race_name} ({item.race_id})" if item.race_name else str(item.race_id)
            verdict_text = (
                f"refused ({item.refusals})" if item.refused else "not refused"
            ) + f", {item.notes} note(s)"
            _say(
                f"  {item.name}  {who}, body type {item.body_type}, "
                f"{item.choices} choice(s): {verdict_text}"
            )
        for remark in list_remarks:
            _say(remark)
    for bad in damaged:
        _note("wowlab: damaged look file {}: {}", bad.file, bad.error)
    if damaged:
        raise typer.Exit(EXIT_ERROR)


@looks_app.command("compare", short_help="Two saved looks side by side.")
@_handled
def looks_compare(
    a: Annotated[str, typer.Argument(metavar="A", help="A saved look.")],
    b: Annotated[str, typer.Argument(metavar="B", help="Another saved look.")],
    build: LooksBuildOpt = None,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
) -> None:
    """Two saved looks side by side, both checked against one build's tables.

    \f
    JSON: LooksCompareReport."""
    store = lookstore.LookStore()
    try:
        lookstore.check_name(a)
        lookstore.check_name(b)
    except lookstore.LookStoreError as exc:
        raise CliError(str(exc), EXIT_USAGE) from exc
    path_a, path_b = store.locate(a), store.locate(b)
    first, second = store.read(path_a), store.read(path_b)
    with _open_gamedata() as data:
        version, remarks = _looks_build(
            data, build, root, flavor, [first.saved_build, second.saved_build]
        )
        model = _load_model(data, version)
    # What holds for both looks (how the build was chosen, the tables-only
    # remark) is stated once, in the top-level remarks; each side keeps only
    # what is its own (saved against another build).
    ra = _look_report(model, first, path_a, exported_only=False)
    rb = _look_report(model, second, path_b, exported_only=False)
    la, lb = first.look, second.look
    same: list[LooksChoiceRef] = []
    different: list[LooksDifference] = []
    for option_id in list(dict.fromkeys([*la.choices, *lb.choices])):
        ca, cb = la.choices.get(option_id), lb.choices.get(option_id)
        if ca is not None and ca == cb:
            same.append(_choice_ref(model, option_id, ca))
            continue
        option = model.options.get(option_id)
        different.append(
            LooksDifference(
                option_id=option_id,
                option_name=option.name if option else None,
                a=_choice_ref(model, option_id, ca) if ca is not None else None,
                b=_choice_ref(model, option_id, cb) if cb is not None else None,
            )
        )
    report = LooksCompareReport(
        build=version,
        a=ra,
        b=rb,
        same_race=la.race_id == lb.race_id,
        same_body_type=la.body_type == lb.body_type,
        same_class=la.class_id == lb.class_id,
        same=same,
        different=different,
        remarks=[*remarks, _EXPORTED_ONLY.format(version=model.build)],
    )
    if json_out:
        _emit(report)
        return

    def race(r: LookReport) -> str:
        return f"{r.race_name} ({r.race_id})" if r.race_name else str(r.race_id)

    def cls(r: LookReport) -> str:
        if r.class_id is None:
            return "not given"
        return f"{r.class_name or 'not in this build'} ({r.class_id})"

    def side(ref: LooksChoiceRef | None) -> str:
        if ref is None:
            return "(not set in this look)"
        return _ref_choice_text(ref)

    _say(f"Looks {ra.name} | {rb.name}, checked against build {version}")
    _say(f"  race:      {race(ra)} | {race(rb)}")
    _say(f"  body type: {ra.body_type} | {rb.body_type}")
    _say(f"  class:     {cls(ra)} | {cls(rb)}")
    _say(f"  same choice on {len(same)} option(s)")
    if different:
        _say(f"  different on {len(different)} option(s):")
    for d in different:
        label = f"{d.option_name} ({d.option_id})" if d.option_name else f"option {d.option_id}"
        _say(f"    {label}: {side(d.a)} | {side(d.b)}")
    for r in (ra, rb):
        verdict = f"refused ({len(r.refusals)} reason(s))" if r.refused else "not refused"
        _say(f"  {r.name}: {verdict}, {len(r.notes)} note(s)")
        _print_findings(r.refusals, r.notes, "    ")
    for remark in dict.fromkeys([*remarks, *ra.remarks, *rb.remarks, *report.remarks]):
        _say(remark)


# ─── looks import-char ───────────────────────────────────────────────────────

_AS_OF_WORDS: dict[str | None, str] = {
    "open": "as of the last barber-shop open",
    "applied": "as of the last applied barber-shop change",
    None: "as of the last barber-shop visit",
}
_OPEN_REMARK = (
    "Recorded when the barber shop opened: a change applied during that visit may not be in "
    'it (a capture has shown a record left at "open" after a change was applied) [verify].'
)
_MODEL_ONLY_REMARK = (
    "The record holds only the options the barber shop listed, for the model it was showing; "
    "it can leave out options the tables give that model."
)
_NOT_LISTED_REMARK = (
    "Not listed by the barber shop, so not in this look: {options} (options of model {model} "
    "in build {build}'s tables). The record cannot say what the character has there; a choice "
    "that depends on one of them is shown as undecided."
)
_NO_CLASS_REMARK = (
    "The record holds no class: class-restricted choices are noted, not refused; --class "
    "checks them."
)
_RECORDED_NO_CLASS_REMARK = (
    "The record holds no class: {reason}. Class-restricted choices are noted, not refused; "
    "--class checks them."
)
_RECORDED_CLASS_REMARK = "The class is the one the lab-addon recorded for this character: {cls}."
_CLASS_DISAGREES_REMARK = (
    "--class gives {given}, but the lab-addon recorded {cls} for this character; --class is used."
)
_RACES_DIFFER_REMARK = (
    "The record's identity (race {identity}) and its customization (race {customization}) were "
    "recorded in different sessions; the class is the identity's, which may not be the class "
    "of the character that made this look."
)


class LooksImportReport(_Out):
    """`wowlab looks import-char --json`. `saved` is False when the tables
    refuse the look (nothing written; `look.path` is None)."""

    flavor_folder: str
    account: str
    character: str  # "<realm folder>/<character folder>"
    chosen_by: Literal["--character", "latest"]
    file: str  # relative to the flavor folder
    mtime_ns: int
    record: labaddon.CustomizationImport
    as_of_words: str  # how the record's moment is said: never the character's appearance now
    saved: bool
    look: LookReport
    # Where `look.class_id` came from: the --class option, the record's
    # `identity` (schema 4), or nowhere (None: no class in the look).
    class_from: Literal["--class", "record"] | None = None
    # The class the record holds (schema 4's `identity`), each beside the
    # reason it holds no class id; exactly one of the id and the reason is
    # set (the class file is beside the id when the record has both).
    recorded_class_id: int | None = None
    recorded_class_file: str | None = None
    recorded_class_absent: str | None = None
    # The race the record's `identity` holds (None when it holds none). It
    # can differ from `record.race_id`: the identity is gathered at each
    # login, the customization record is carried from an earlier session.
    recorded_race_id: int | None = None


def _loads_ago_text(ago: int | None) -> str:
    if ago is None:
        return "an unknown number of logins or reloads before this file was saved"
    if ago == 0:
        return "in the session that saved this file"
    return f"{ago} login(s) or reload(s) before the session that saved this file"


def _import_remarks(
    model: looks.Customizations, got: labaddon.CustomizationImport, class_remarks: list[str]
) -> list[str]:
    at = got.recorded_at if got.recorded_at is not None else "not recorded"
    remarks = [
        f"Customization {_AS_OF_WORDS[got.recorded_at]} (recorded_at: {at}; the addon's as_of: "
        f'"{got.as_of}"), {_loads_ago_text(got.loads_ago)}. The character may look different '
        "since.",
    ]
    if got.recorded_at == "open":
        remarks.append(_OPEN_REMARK)
    remarks.append(labaddon.PAID_CHANGE_NOTE)
    remarks.append(_MODEL_ONLY_REMARK)
    if got.without_choice:
        listed = ", ".join(str(o) for o in got.without_choice)
        remarks.append(f"Recorded with no choice id, so left out of the look: option(s) {listed}.")
    race = model.races.get(got.race_id)
    expected = race.model_for(got.body_type) if race is not None else None
    shown_model = got.chr_model_id if got.chr_model_id is not None else expected
    if shown_model is not None:
        listed_ids = {*got.choices, *got.without_choice}
        unlisted = sorted(
            (o for o in model.options.values() if o.chr_model_id == shown_model),
            key=lambda o: o.id,
        )
        unlisted = [o for o in unlisted if o.id not in listed_ids]
        if unlisted:
            remarks.append(
                _NOT_LISTED_REMARK.format(
                    options=", ".join(f"{o.name} ({o.id})" for o in unlisted),
                    model=shown_model,
                    build=model.build,
                )
            )
    if got.chr_model_id is None:
        remarks.append(
            "The record names no model (chr_model_id): the body type is its sex value "
            f"{got.body_type}, read as the tables' body type (ChrRaceXChrModel.Sex) [verify]."
        )
    elif expected is not None and expected != got.chr_model_id:
        remarks.append(
            f"The barber shop was showing model {got.chr_model_id}; build {model.build}'s "
            f"tables give race {got.race_id} body type {got.body_type} model {expected}."
        )
    remarks.extend(class_remarks)
    if got.client_build is not None and got.client_build != model.build:
        remarks.append(
            f"Recorded by client build {got.client_build}; checked against build "
            f"{model.build}'s tables: an id they lack may exist only in the recording build, or "
            "come from a hotfix."
        )
    return remarks


def _import_class(
    model: looks.Customizations,
    record: labaddon.CharDB,
    got: labaddon.CustomizationImport,
    given: str | None,
) -> tuple[int | None, Literal["--class", "record"] | None, labaddon.CharIdentity, list[str]]:
    """The look's class, where it came from, the record's identity and the
    remarks that say so. --class wins over the record (said in one line when
    they differ); a record of schema 1 to 3 holds no class and keeps the
    remark it always had. The record's class id is checked by the look, as a
    typed --class id is. A class taken from an identity whose race is not the
    customization record's is said to come from another session."""
    identity = labaddon.identity_of(record)
    recorded = identity.class_id
    class_id = _resolve_class(model, given)
    if class_id is not None:
        remarks: list[str] = []
        if recorded is not None and recorded != class_id:
            remarks.append(
                _CLASS_DISAGREES_REMARK.format(
                    given=_class_words(model, class_id), cls=_class_words(model, recorded)
                )
            )
        return class_id, "--class", identity, remarks
    if recorded is not None:
        remarks = [_RECORDED_CLASS_REMARK.format(cls=_class_words(model, recorded))]
        if identity.race_id is not None and identity.race_id != got.race_id:
            remarks.append(
                _RACES_DIFFER_REMARK.format(identity=identity.race_id, customization=got.race_id)
            )
        return recorded, "record", identity, remarks
    if not isinstance(record, labaddon.CharDBV4):
        return None, None, identity, [_NO_CLASS_REMARK]
    return (
        None,
        None,
        identity,
        [_RECORDED_NO_CLASS_REMARK.format(reason=(identity.class_id_absent or "").rstrip("."))],
    )


def _class_words(model: looks.Customizations, class_id: int) -> str:
    known = model.classes.get(class_id)
    return f"{known.name} ({class_id})" if known is not None else f"{class_id}"


@looks_app.command("import-char", short_help="Save a character's recorded customization as a look.")
@_handled
def looks_import_char(
    name: LookNameArg,
    character: CharacterOpt = None,
    account: AccountOpt = None,
    class_: ClassOpt = None,
    replace: Annotated[
        bool, typer.Option("--replace", help="Overwrite a saved look with this name.")
    ] = False,
    build: LooksBuildOpt = None,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
) -> None:
    """Save a character's customization choices, as the lab-addon recorded
    them at its last barber-shop visit, as a look under the user data
    directory (never in an install). Reads the character's WowLab.lua (read
    only); without --character, the one written last. A character with no
    recorded visit gets the addon's reason (exit 1). The look is checked like
    `looks save`: one the tables refuse is not saved (exit 1); an id the
    tables lack is noted "(possibly a hotfix)". Without --class, the class is
    the one the lab-addon recorded for the character, when its record holds
    one; --class wins over it.

    \f
    JSON: LooksImportReport."""
    try:
        lookstore.check_name(name)
    except lookstore.LookStoreError as exc:
        raise CliError(str(exc), EXIT_USAGE) from exc
    _, chosen, lay = _open(root, flavor)
    acct = _select_account(lay, account)
    files = [f for f in lay.saved_variables() if f.account == acct.folder and _is_lab_file(f)]
    target, how, tie = _lab_char_file(acct, files, character)
    who = f"{target.realm_folder}/{target.character_folder}"
    record = _listed(labaddon.read_char, lay, target.path)
    try:
        got = labaddon.customization_import(record)
    except labaddon.NoCustomizationError as exc:
        raise CliError(
            f"{who}{_picked_words(how, tie)}: nothing to import: {exc}; nothing was saved"
        ) from exc
    with _open_gamedata() as data:
        version, build_remarks = _looks_build(data, build, root, flavor)
        model = _load_model(data, version)
    class_id, class_from, identity, class_remarks = _import_class(model, record, got, class_)
    look = looks.Look(
        name=name,
        race_id=got.race_id,
        body_type=got.body_type,
        class_id=class_id,
        choices=got.choices,
    )
    saved = lookstore.SavedLook(
        saved_build=version,
        origin="imported",
        recorded_client_build=got.client_build,
        look=look,
    )
    remarks = [*_import_remarks(model, got, class_remarks), *build_remarks]
    report = _look_report(model, saved, None, remarks, imported_remark=False)
    path: Path | None = None
    if not report.refused:
        path = lookstore.LookStore().save(saved, replace=replace)
        report = _look_report(model, saved, path, remarks, imported_remark=False)
    out = LooksImportReport(
        flavor_folder=chosen.folder,
        account=acct.folder,
        character=who,
        chosen_by=how,
        file=target.path,
        mtime_ns=target.mtime_ns,
        record=got,
        as_of_words=_AS_OF_WORDS[got.recorded_at],
        saved=path is not None,
        look=report,
        class_from=class_from,
        recorded_class_id=identity.class_id,
        recorded_class_file=identity.class_file,
        recorded_class_absent=identity.class_id_absent,
        recorded_race_id=identity.race_id,
    )
    if json_out:
        _emit(out)
    else:
        _say(f"Character: {who} in account {acct.folder}{_picked_words(how, tie)}")
        _say(f"File: {target.path}")
        _say(f"Written: {_local_time(target.mtime_ns)} (the file's modification time)")
        heading = "Imported look" if path is not None else "Not saved: the tables refuse look"
        _print_look(report, heading=heading)
    if path is None:
        raise CliError(f"look {name} is refused by build {version}'s tables; nothing was saved")


# ─── looks page ──────────────────────────────────────────────────────────────

LOOKS_PAGE_FORMAT: Literal[1] = 1
_PAGE_LEGEND = (
    "Each choice is checked as a look that holds only that choice, as `wowlab looks "
    "options` does, so a dependency on another option shows as a note, never a refusal.",
    "refused: the tables decide against it, and `wowlab looks save` would not save a look "
    "holding it (an option or choice for another race or body type, a choice outside its "
    "option, a class the ClassMask excludes, or a choice it depends on set to something "
    "else).",
    'note: shown, never a refusal: "needs <unlock>" (an achievement, quest or item '
    'appearance to earn), an id the build lacks (in a typed look: "is not in build '
    "<version>'s tables: check the id with `wowlab looks options`\"; in an imported look: "
    '"unknown to build <version> (possibly a hotfix)", or, when the recording client was '
    'another build, "(recorded by client <build>, not the build of these tables: the id may '
    'exist only in that build, or come from a hotfix)"), a dependency on '
    "an option the look leaves unset, a class-restricted choice when no class is chosen, "
    "conditions, and options on a model no race uses (a form, pet or mount), checked by "
    "their requirements only [verify].",
)
_PAGE_CLASSES_NOTE = (
    "Classes are the rows of build {version}'s ChrClasses ({classes}); class masks are "
    "checked against these ids only. Every class is offered with every race: which races "
    "can be which class is not read from the tables [verify], so a pairing here (a Human "
    "Druid, say) may be one the game does not offer."
)
_PAGE_PAIR_NOTE = (
    "Race and class are not checked as a pair: this view lists what the customization "
    "tables give this race and class, not proof that the game lets anyone create it."
)
_PAGE_NO_CLASS_NOTE = "No class chosen: class-restricted choices are listed with a note."
_PAGE_NO_CLASS_LABEL = "not given (class-restricted choices are noted, not refused)"


class LooksPageClass(_Out):
    id: int
    name: str


class LooksPageRace(_Out):
    race: LooksRace
    label: str  # faction, id and body types, as `looks races` words them


class LooksPageOption(_Out):
    """`LooksOption` with its option-level findings split as the CLI prints them."""

    id: int
    name: str
    chr_model_id: int
    category: str | None
    form_or_pet: bool
    refusals: list[looks.Finding]
    notes: list[looks.Finding]
    choices: list[LooksChoice]


class LooksPageView(_Out):
    """`looks options <race> --sex <body_type> [--class <class_id>]`, precomputed.
    `options` are indices into `LooksPageData.options`, in the command's order."""

    race_id: int
    body_type: int
    class_id: int | None
    chr_model_id: int
    heading: str
    notes: list[str]
    options: list[int]


class LooksPageLook(_Out):
    """`looks show NAME`, precomputed: the report and the lines the CLI prints."""

    report: LookReport
    verdict: str
    facts: list[tuple[str, str]]
    choice_lines: list[str]


class LooksPageData(_Out):
    """The JSON block embedded in the looks page (ADR-0027). The page's script
    only displays it; everything in it is computed here."""

    format: Literal[1] = LOOKS_PAGE_FORMAT
    build: str
    remarks: list[str]  # how the build was chosen, when not by --build or the install
    notes: list[str]  # the caveats `looks races`, `looks options` and `looks show` print
    legend: list[str]
    races_heading: str
    races: list[LooksPageRace]
    classes: list[LooksPageClass]
    options: list[LooksPageOption]
    views: list[LooksPageView]
    looks_directory: str
    looks: list[LooksPageLook]
    damaged: list[lookstore.DamagedLook]
    damaged_heading: str
    no_class_label: str
    no_findings_label: str
    no_race_label: str
    no_options_label: str
    no_looks_label: str


class LooksPageReport(_Out):
    """`wowlab looks page --json`: what was written. The command exits 1 after
    writing when `damaged` is not empty, as `looks show` does."""

    path: str
    build: str
    bytes: int
    races: int
    views: int
    looks: int
    damaged: list[lookstore.DamagedLook]
    remarks: list[str]


def _home_short(text: str) -> str:
    """``text`` with the home directory written as ``~``, so a page passed on
    does not carry the account's user name in its paths."""
    try:
        home = str(Path.home())
    except (RuntimeError, OSError):
        return text
    if home in ("", os.sep):
        return text
    if text == home:
        return "~"
    return text.replace(home + os.sep, "~" + os.sep)


def _page_report(report: LookReport) -> LookReport:
    """A `looks show NAME` report as the page embeds it: its path with `~`."""
    if report.path is None:
        return report
    return report.model_copy(update={"path": _home_short(report.path)})


def _page_option(option: LooksOption) -> LooksPageOption:
    return LooksPageOption(
        id=option.id,
        name=option.name,
        chr_model_id=option.chr_model_id,
        category=option.category,
        form_or_pet=option.form_or_pet,
        refusals=[f for f in option.notes if f.refuses],
        notes=[f for f in option.notes if not f.refuses],
        choices=option.choices,
    )


def _page_look(report: LookReport) -> LooksPageLook:
    race = f"{report.race_name} ({report.race_id})" if report.race_name else f"{report.race_id}"
    if report.class_id is None:
        player_class = _PAGE_NO_CLASS_LABEL
    else:
        player_class = f"{report.class_name or 'not in this build'} ({report.class_id})"
    facts = [
        ("file", report.path or ""),
        ("saved against build", report.saved_build or ""),
        ("checked against build", report.build),
        ("race", f"{race}, body type {report.body_type}"),
        ("class", player_class),
        ("choices", str(len(report.choices))),
    ]
    if report.refused:
        verdict = f"refused ({len(report.refusals)} reason(s)), {len(report.notes)} note(s)"
    else:
        verdict = f"not refused, {len(report.notes)} note(s)"
    return LooksPageLook(
        report=report,
        verdict=verdict,
        facts=facts,
        choice_lines=[_ref_text(ref) for ref in report.choices],
    )


def _looks_page_data(
    model: looks.Customizations,
    remarks: Sequence[str],
    store: lookstore.LookStore,
    found: Sequence[tuple[Path, lookstore.SavedLook]],
    damaged: Sequence[lookstore.DamagedLook],
) -> LooksPageData:
    """Everything the page shows, from the same functions `looks races`,
    `looks options` and `looks show` use: every playable race, each body
    type, without a class and with each class of the build's ChrClasses."""
    version = model.build
    classes = [LooksPageClass(id=i, name=c.name) for i, c in sorted(model.classes.items())]
    class_ids: list[int | None] = [None, *(c.id for c in classes)]
    table: list[LooksPageOption] = []
    index: dict[str, int] = {}
    races: list[LooksPageRace] = []
    views: list[LooksPageView] = []
    for race in model.playable_races():
        bodies = ", ".join(f"{b.body_type} (model {b.chr_model_id})" for b in race.body_types)
        races.append(
            LooksPageRace(
                race=_race_out(race),
                label=f"{race.id} · {_faction(race.alliance)} · body types {bodies or 'none'}",
            )
        )
        for body in race.body_types:
            for class_id in class_ids:
                refs: list[int] = []
                for option in _options_for_body(model, race, body, class_id).options:
                    entry = _page_option(option)
                    key = entry.model_dump_json()
                    if key not in index:
                        index[key] = len(table)
                        table.append(entry)
                    refs.append(index[key])
                who = _race_label(race)
                if class_id is not None:
                    who += f", {_class_name(model, class_id) or 'class'} ({class_id})"
                views.append(
                    LooksPageView(
                        race_id=race.id,
                        body_type=body.body_type,
                        class_id=class_id,
                        chr_model_id=body.chr_model_id,
                        heading=(
                            f"{who}, body type {body.body_type} (model {body.chr_model_id}), "
                            f"build {version}"
                        ),
                        notes=[_PAGE_NO_CLASS_NOTE] if class_id is None else [_PAGE_PAIR_NOTE],
                        options=refs,
                    )
                )
    class_list = ", ".join(f"{c.name} ({c.id})" for c in classes) or "none"
    return LooksPageData(
        build=version,
        remarks=list(remarks),
        notes=[
            _PLAYABLE_NOTE,
            _EXPORTED_ONLY.format(version=version),
            _PAGE_CLASSES_NOTE.format(version=version, classes=class_list),
        ],
        legend=list(_PAGE_LEGEND),
        races_heading=f"Races flagged playable in build {version}'s ChrRaces",
        races=races,
        classes=classes,
        options=table,
        views=views,
        looks_directory=_home_short(str(store.root)),
        looks=[_page_look(_page_report(_look_report(model, saved, path))) for path, saved in found],
        damaged=[d.model_copy(update={"error": _home_short(d.error)}) for d in damaged],
        damaged_heading="Damaged look files (not saved looks; `wowlab looks show` names them too)",
        no_class_label=_PAGE_NO_CLASS_LABEL,
        no_findings_label="nothing noted",
        no_race_label="Pick a race.",
        no_options_label="The tables give this race, body type and class no options.",
        no_looks_label=f"No saved looks (in {_home_short(str(store.root))}).",
    )


@looks_app.command("page", short_help="Write an HTML page to browse the tables and saved looks.")
@_handled
def looks_page(
    out: Annotated[
        Path | None,
        typer.Option(
            "--out",
            help="The HTML file to write. Default: pages/looks.html under the user data "
            "directory. Refused when it is in an install.",
        ),
    ] = None,
    build: LooksBuildOpt = None,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
) -> None:
    """Write one self-contained HTML page to browse the races, body types,
    classes, options and choices of the build's tables, with what the tables
    say about each choice, and to view the saved looks checked against that
    build. The build's public game tables are downloaded from wago.tools when
    wowlab does not have them yet; the page itself makes no network request.
    Never written into an install; names each damaged look file and then
    exits 1, as `looks show` does.

    \f
    JSON: LooksPageReport."""
    target = out if out is not None else lookspage.default_page_path()
    try:
        # Before any work: a refused --out costs nothing and writes nothing.
        lookspage.check_target(target)
    except (lookspage.PageError, lookstore.LookLocationError) as exc:
        raise CliError(f"refused: {exc}; nothing was written") from exc
    store = lookstore.LookStore()
    found, damaged = store.entries()
    with _open_gamedata() as data:
        version, remarks = _looks_build(
            data, build, root, flavor, [saved.saved_build for _, saved in found]
        )
        model = _load_model(data, version)
    payload = _looks_page_data(model, remarks, store, found, damaged)
    page = lookspage.render(payload.model_dump_json())
    try:
        written = lookspage.write_page(page, target)
    except (lookspage.PageError, lookstore.LookLocationError) as exc:
        raise CliError(f"refused: {exc}; nothing was written") from exc
    report = LooksPageReport(
        path=str(written),
        build=version,
        bytes=len(page.encode("utf-8")),
        races=len(payload.races),
        views=len(payload.views),
        looks=len(payload.looks),
        damaged=list(damaged),
        remarks=list(remarks),
    )
    if json_out:
        _emit(report)
    else:
        _say(f"Wrote the looks page for build {version}: {written}")
        _say(
            f"  {report.races} race(s) flagged playable, {report.views} race/body type/class "
            f"view(s), {report.looks} saved look(s), {_bytes(report.bytes)}"
        )
        _say(
            "Open it in a browser. It is one self-contained file and makes no network "
            "requests (its Content-Security-Policy forbids them)."
        )
        for remark in remarks:
            _say(remark)
    for bad in damaged:
        _note("wowlab: damaged look file {}: {}", bad.file, bad.error)
    if damaged:
        raise typer.Exit(EXIT_ERROR)
