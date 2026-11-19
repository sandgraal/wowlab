"""`wowlab char model` (M15-11, docs/LAB_PLAN.md §17.6).

Runs the whole chain for one character: its lab-addon record → the render
plan (`renderplan`, through the game tables `gamedata` caches per build;
the first run for a build downloads about twenty tables, said on stderr
before it starts) → the install's own files (`casc`, read-only) → the
body texture (`compositor`) → one `.glb` and its PNGs (`charmodel`,
`gltf`). It reads the install and writes only wowlab's own data
(`cascfiles`): by default `casc/<product>-<version>/characters/<account>/
<realm>-<character>.glb` and `<realm>-<character>.<part>.png` beside it,
which a re-run replaces (§17.1's exemption for `characters/`); with
`--out`, a path inside the same build folder outside `files/` and
`characters/`, never over another file. No write gate and no `--yes`:
nothing in the install changes. `wowlab ui` refuses `--out`, so from the UI
the model goes only to its default place.
"""

import hashlib
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

import typer

from wowlab_core import cascfiles, charmodel, gamedata, gltf, labaddon, renderplan
from wowlab_core.casc import CascError
from wowlab_core.cli._cmd_casc import _casc_open
from wowlab_core.cli._cmd_char import _CHAR_NOTES, _picked_words
from wowlab_core.cli._cmd_doctor import _open_gamedata
from wowlab_core.cli._common import (
    EXIT_USAGE,
    AccountOpt,
    CharacterOpt,
    CliError,
    FlavorOpt,
    JsonOpt,
    NotesOpt,
    RootOpt,
    _account_words,
    _bytes,
    _emit,
    _handled,
    _home_words,
    _is_lab_file,
    _lab_char_file,
    _listed,
    _local_time,
    _note,
    _open_char,
    _Out,
    _plural,
    _say,
    _say_notes,
    _say_prose,
    _select_account,
    char_app,
)

# ─── char model ──────────────────────────────────────────────────────────────


class CharModelPlan(_Out):
    """What the model was drawn from, in brief: the body (race, body type,
    the game's model id, the body model file and the texture layout), the
    customization choices drawn, the body-texture layers and item pieces
    the plan names, and the items drawn."""

    race_id: int
    body_type: int
    chr_model_id: int
    body_file_data_id: int
    layout_id: int
    layout_size: tuple[int, int] | None
    choices: int
    layers: int
    item_pieces: int
    items: int
    item_models: int


class CharModelPng(_Out):
    """A PNG written beside the `.glb`: `name` is the part of its file name
    after the character's (`body`, `hair`, `eyes`, `texture<type>`);
    `file_data_id` the game file it was decoded from (None for the body
    texture, which wowlab composes); `not_drawn` the files of the same
    texture the model does not combine with it."""

    name: str
    path: str
    texture_type: int
    file_data_id: int | None
    width: int
    height: int
    bytes: int
    not_drawn: list[int]


class CharModelReport(_Out):
    """`wowlab char model --json`: the model written (`path`, its `outcome`:
    `written`, `replaced` (a re-run over the default place), or
    `already_there` (an `--out` file with the same bytes, nothing written)),
    the PNGs beside it and any older PNG of this character removed, the
    `.glb`'s facts, the plan in brief, every model and whether it was
    placed, the body texture's painted and not-painted layers, every file
    the plan names with its state, the files left out and why, the items
    not drawn, the two dates (`gear_as_of`: the record's last save, at
    logout or /reload; `look_as_of`: the barber-shop record, whose
    `saved_at` is the latest it can be, older when `carried`),
    `stand_in_look` (no barber-shop record: each option's first choice, not
    the character's look), `rest_pose` and `likeness` (the sentences the
    text output says), the tables downloaded, timings in seconds, and the
    notes."""

    flavor_folder: str
    product: str
    version: str
    account: str
    character: str  # "<realm folder>/<character folder>"
    chosen_by: Literal["--character", "latest"]
    file: str  # the record, relative to the flavor folder
    mtime_ns: int
    path: str
    outcome: Literal["written", "replaced", "already_there"]
    glb: gltf.GlbFacts
    pngs: list[CharModelPng]
    removed: list[str]
    plan: CharModelPlan
    gear_as_of: renderplan.GearAsOf
    look_as_of: renderplan.LookAsOf
    stand_in_look: bool
    rest_pose: str
    likeness: str
    models: list[charmodel.ModelDrawn]
    body_texture: charmodel.BodyTextureFacts
    files: list[renderplan.PlanFile]
    left_out: list[charmodel.FileLeftOut]
    items_not_drawn: list[renderplan.GearNotDrawn]
    tables_downloaded: list[str]
    seconds: dict[str, float]
    notes: list[str]


_CHAR_MODEL_STDOUT = "-"
_GLB = ".glb"
_PNG_PARTS = r"(?:body|hair|eyes|texture[0-9]{1,4})"
_AS_OF_LOOK: dict[str | None, str] = {
    "open": "the last time the barber shop was opened",
    "applied": "the last change applied in the barber shop",
    None: "the last barber-shop visit",
}
_CHAR_MODEL_NOTES = [
    "Only the game's files on this computer are read; wowlab never downloads a game file, so "
    "a file that is not on this computer leaves its part out and is listed.",
    "Open the .glb file in any glTF viewer; the PNG files beside it are the same textures.",
]


def _png_path(glb: Path, name: str) -> Path:
    return glb.with_name(f"{glb.stem}.{name}.png")


def _check_out(out: str | None) -> None:
    if out is None:
        return
    if out == _CHAR_MODEL_STDOUT:
        raise CliError("--out - is not offered: char model writes a file and its PNGs", EXIT_USAGE)
    if not out.strip():
        raise CliError("--out is empty; name a .glb file", EXIT_USAGE)
    if not out.lower().endswith(_GLB):
        raise CliError(f"--out names {out!r}; name a file ending in .glb", EXIT_USAGE)


def _download_note(data: gamedata.GameData, version: str) -> list[str]:
    """The tables this run will download, said on stderr before it starts."""
    try:
        missing = gamedata.missing_tables(
            renderplan.REQUIRED_TABLES, version, cache_dir=data.cache_dir
        )
    except ValueError as exc:
        raise CliError(str(exc), EXIT_USAGE) from exc
    if missing:
        _note(
            "wowlab: downloading {} game tables for {} from wago.tools (once for this game "
            "version; later runs use the copies kept in wowlab's data folder)",
            len(missing),
            version,
        )
    return missing


def _write_default(
    built: charmodel.CharModel, glb: Path, folder: Path
) -> tuple[Literal["written", "replaced"], list[tuple[charmodel.PngOut, Path]], list[str]]:
    pngs = [(p, _png_path(glb, p.name)) for p in built.pngs]
    for path in (glb, *(path for _, path in pngs)):
        cascfiles.check_character(path, folder)
    existed = os.path.lexists(glb)
    for p, path in pngs:
        cascfiles.replace_derived(p.png, path, folder)
    removed: list[str] = []
    written = {path.name for _, path in pngs}
    stale = re.compile(rf"{re.escape(glb.stem)}\.{_PNG_PARTS}\.png")
    try:
        names = sorted(e.name for e in os.scandir(glb.parent))
    except OSError:
        names = []
    for name in names:
        if stale.fullmatch(name) and name not in written:
            path = glb.parent / name
            if cascfiles.remove_derived(path, folder):
                removed.append(str(path))
    cascfiles.replace_derived(built.glb, glb, folder)
    return ("replaced" if existed else "written"), pngs, removed


def _write_out(
    built: charmodel.CharModel, glb: Path, folder: Path
) -> tuple[Literal["written", "already_there"], list[tuple[charmodel.PngOut, Path]]]:
    pngs = [(p, cascfiles.check_out(_png_path(glb, p.name), folder)) for p in built.pngs]
    every = [(built.glb, glb), *((p.png, path) for p, path in pngs)]
    keyed = [
        (data, path, hashlib.md5(data, usedforsecurity=False).hexdigest()) for data, path in every
    ]
    fresh = [k for k in keyed if not cascfiles.already_there(k[1], len(k[0]), k[2])]
    for data, path, key in fresh:
        cascfiles.write_new(data, path, folder, key)
    return ("written" if fresh else "already_there"), pngs


def _plan_brief(plan: renderplan.RenderPlan) -> CharModelPlan:
    body = plan.body
    size = (
        (body.layout_width, body.layout_height)
        if body.layout_width is not None and body.layout_height is not None
        else None
    )
    return CharModelPlan(
        race_id=body.race_id,
        body_type=body.sex,
        chr_model_id=body.chr_model_id,
        body_file_data_id=body.file_data_id,
        layout_id=body.layout_id,
        layout_size=size,
        choices=len(plan.choices),
        layers=len(plan.layers),
        item_pieces=len(plan.item_pieces),
        items=len(plan.items),
        item_models=len(plan.item_models),
    )


def _look_words(look: renderplan.LookAsOf, saved: str) -> str:
    if look.source != "record":
        return charmodel.STAND_IN
    when = _AS_OF_LOOK.get(look.recorded_at, _AS_OF_LOOK[None])
    carried = (
        "; the lab-addon carried it from an earlier session, so it is older than that"
        if look.carried
        else ""
    )
    return (
        f"Look as of {when} with the lab-addon on, recorded no later than {saved} (the file's "
        f"last save){carried}."
    )


def _model_words(m: charmodel.ModelDrawn) -> str:
    where = (
        f"at attachment point {m.attachment_id}"
        if m.kind == "attached"
        else "on the body"
        if m.kind == "collection"
        else "not placed"
    )
    return f"slot {m.slot}: item {m.item_id}, {where}"


@char_app.command(
    "model", short_help="Draw one character as a 3D model file from the game's own files."
)
@_handled
def char_model(
    character: CharacterOpt = None,
    account: AccountOpt = None,
    out: Annotated[
        str | None,
        typer.Option(
            "--out",
            metavar="<path>",
            help="Where to write the .glb instead, inside the same casc/<product>-<version>/ "
            "folder (a relative path is taken inside it) but outside its files/ and "
            "characters/ folders; its PNGs go beside it. Never over another file. Default: "
            "characters/<account>/<realm>-<character>.glb in that folder, under wowlab's data "
            "folder, replaced by each run.",
        ),
    ] = None,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
    notes_out: NotesOpt = False,
) -> None:
    """Draw one character as a 3D model (a .glb file, glTF) from what the
    lab-addon recorded and the game's own files on this computer: its body,
    its look from the barber shop, and its gear. Without --character, the
    character whose WowLab.lua was written last. The model and its textures
    (PNG) go into wowlab's data folder, never into the game folder; a re-run
    replaces them. The first run for a game version downloads the game
    tables it needs from wago.tools; wowlab never downloads a game file. A
    character whose body model is not on this computer exits 1.

    \f
    JSON: CharModelReport."""
    started = time.perf_counter()
    _check_out(out)
    inst, chosen, lay = _open_char(root, flavor, account)
    acct = _select_account(lay, account)
    sv = [f for f in lay.saved_variables() if f.account == acct.folder and _is_lab_file(f)]
    target, how, tie = _lab_char_file(acct, sv, character)
    realm = target.realm_folder or ""
    who = target.character_folder or ""
    if chosen.product and chosen.version:
        # Before any work: an unsafe name or a refused --out costs nothing.
        folder = cascfiles.build_dir(chosen.product, chosen.version)
        if out is None:
            cascfiles.check_character(
                cascfiles.character_path(chosen.product, chosen.version, acct.folder, realm, who),
                folder,
            )
        else:
            cascfiles.check_out(out, folder)
    record = _listed(labaddon.read_char, lay, target.path)
    saved_at = datetime.fromtimestamp(target.mtime_ns / 1_000_000_000, tz=UTC)
    source = _casc_open(inst, chosen)
    index = source.report.index
    folder = cascfiles.build_dir(index.product, index.version)
    if out is None:
        glb = cascfiles.character_path(index.product, index.version, acct.folder, realm, who)
        cascfiles.check_character(glb, folder)
    else:
        glb = cascfiles.check_out(out, folder)
    seconds: dict[str, float] = {}
    clock = time.perf_counter()
    with _open_gamedata() as data:
        downloaded = _download_note(data, index.version)
        tables = renderplan.RenderTables.from_gamedata(data, index.version)
    seconds["tables"] = time.perf_counter() - clock
    clock = time.perf_counter()
    try:
        plan = renderplan.plan_for_record(tables, record, statter=source, saved_at=saved_at)
    except CascError as exc:
        raise CliError(f"cannot say where the character's files stand: {exc}") from exc
    seconds["plan"] = time.perf_counter() - clock
    try:
        built = charmodel.build(plan, source)
    except charmodel.BodyNotDrawnError as exc:
        raise CliError(f"cannot draw {realm}/{who}: {exc}; nothing was written") from exc
    seconds.update(built.seconds)
    clock = time.perf_counter()
    removed: list[str] = []
    outcome: Literal["written", "replaced", "already_there"]
    if out is None:
        outcome, pngs, removed = _write_default(built, glb, folder)
    else:
        outcome, pngs = _write_out(built, glb, folder)
    seconds["write"] = time.perf_counter() - clock
    seconds["total"] = time.perf_counter() - started
    report = CharModelReport(
        flavor_folder=chosen.folder,
        product=index.product,
        version=index.version,
        account=acct.folder,
        character=f"{realm}/{who}",
        chosen_by=how,
        file=target.path,
        mtime_ns=target.mtime_ns,
        path=str(glb),
        outcome=outcome,
        glb=built.facts,
        pngs=[
            CharModelPng(
                name=p.name,
                path=str(path),
                texture_type=p.texture_type,
                file_data_id=p.file_data_id,
                width=p.width,
                height=p.height,
                bytes=len(p.png),
                not_drawn=list(p.not_drawn),
            )
            for p, path in pngs
        ],
        removed=removed,
        plan=_plan_brief(plan),
        gear_as_of=plan.gear,
        look_as_of=plan.look,
        stand_in_look=plan.look.source != "record",
        rest_pose=charmodel.REST_POSE,
        likeness=charmodel.LIKENESS,
        models=list(built.models),
        body_texture=built.body_texture,
        files=list(plan.files),
        left_out=list(built.left_out),
        items_not_drawn=list(plan.items_not_drawn),
        tables_downloaded=downloaded,
        seconds={k: round(v, 3) for k, v in seconds.items()},
        notes=[*plan.notes, *built.notes],
    )
    if json_out:
        _emit(report)
        return
    _say_char_model(report, acct_words=_account_words(acct), how_words=_picked_words(how, tie))
    _say_notes([*report.notes, *_CHAR_MODEL_NOTES, *_CHAR_NOTES], notes_out)


def _say_char_model(report: CharModelReport, *, acct_words: list[str], how_words: str) -> None:
    shown = _home_words(report.path)
    keep = [*acct_words, shown]
    _say_prose(f"Character: {report.character} in account {report.account}{how_words}", keep=keep)
    placed = [m for m in report.models if m.what == "item" and m.placed]
    verb = {"written": "Wrote", "replaced": "Replaced", "already_there": "Already there:"}
    _say_prose(
        f"{verb[report.outcome]} {shown} ({_bytes(report.glb.bytes)}): the body and "
        f"{_plural(len(placed), 'item model')}, {report.glb.triangles:,} triangles"
        + ("; nothing was written" if report.outcome == "already_there" else ""),
        keep=keep,
    )
    if report.pngs:
        _say(f"Textures beside it: {', '.join(p.name for p in report.pngs)} (PNG)")
    saved = _local_time(report.mtime_ns)
    if report.gear_as_of.source == "record":
        _say(f"Gear as of your last logout or /reload: {saved}.")
    else:
        _say_prose(f"No gear drawn: {report.gear_as_of.why}.")
    _say_prose(_look_words(report.look_as_of, saved))
    _say_prose(report.rest_pose)
    _say_prose(report.likeness)
    body = report.body_texture
    if body.composed:
        custom = sum(1 for p in body.painted if p.job.kind == "customization")
        items = len(body.painted) - custom
        separate = [p.name for p in report.pngs if p.name != "body"]
        tail = f"; {' and '.join(separate)} are separate textures" if separate else ""
        _say_prose(
            f"Body texture: painted {_plural(custom, 'layer')} of the character's look and "
            f"{_plural(items, 'piece')} of gear{tail}."
        )
    else:
        _say_prose(f"Body texture: not made, so the body is drawn grey ({body.why}).")
    if len(report.models) > 1 or report.items_not_drawn:
        _say("Gear:")
    for m in report.models:
        if m.what == "item":
            line = _model_words(m)
            _say_prose(line if m.placed else f"{line}: {m.reason}", indent="  ")
    for item in report.items_not_drawn:
        what = f"item {item.item_id}" if item.item_id is not None else "no item id"
        _say_prose(f"slot {item.slot}: {what}, not drawn: {item.reason}", indent="  ")
    if report.left_out:
        _say(f"Left out ({_plural(len(report.left_out), 'file')}):")
        for f in report.left_out:
            _say_prose(f"{f.what}: {f.reason}", indent="  ")
    if report.removed:
        _say(f"Removed {_plural(len(report.removed), 'older texture')} of this character.")
