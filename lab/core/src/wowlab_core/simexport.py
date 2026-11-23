"""simexport: one character as the JSON wowsims/forever's individual sim imports (M21-08).

WoWSims (https://github.com/wowsims/forever, MIT) is a damage simulator
for WoW Forever that runs in the player's browser. Its sim page has an
"Addon Import" that reads the JSON its own in-game exporter addon writes:
the class (required), the race, the level, the talent string with the word
`flavor` naming the trees it was built from, and the gear as the sim's
`EquipmentSpec`, one `ItemSpec` (item id, enchant, random suffix) per sim
slot. `build` writes that JSON from what the lab-addon recorded, so the
player can paste it there. wowlab never sends it anywhere and fetches
nothing: this module reads the record, the shipped `wowsims_format.toml`
and, for the talents, a game table already in wowlab's cache.

The format was read by hand from wowsims/forever at commit
57df2a7a5a2bb085bfce15890a33d655bb7fd66f (2026-11-23, after v1.0.2):
`ui/features/import-export/importers/addon.ts` and
`finish_individual_import.ts` (what the import reads and applies),
`proto/common.proto` (`ItemSpec`, `EquipmentSpec`, `ItemSlot`, `Race`,
`Class`), `ui/sim/proto/names.ts` (the race and class names),
`ui/sim/proto/database.ts` (how an `ItemSpec` is matched),
`ui/sim/talents/trees/<class>.json` (the talent order),
`tools/database/dbc/random_suffix.go` (a random suffix is a bonus list),
`assets/database/db.json` (the sim's random suffix ids) and
`ui/sim/constants/mechanics.ts` (`CHARACTER_LEVEL`, the level it models).
Everything read from it is data in `wowsims_format.toml`, which names the
commit; a newer sim is read again by hand. Nothing here names a client
folder, product or build (L6).

What goes in, and what is left out and listed (`SimExport.left_out`):

- `class`: the sim's class name for the record's class id (or, without
  one, for its `class_file`). A record with neither cannot be exported
  (`SimExportError`): the import refuses a text without a class.
- `race`: the sim's race name for the record's race id; left out when the
  record has none or the sim has no such race.
- `level`: the record's (`talents.legacy.player_level`); left out when
  absent. The sim models one level (`[format] level`, 60): below it the
  import warns and the sim still simulates that level, which the note says.
- `talents`: the sim's talent string. The record holds, per talent node
  of its class config, the rank and the active trait entry id; the game's
  `TraitNodeEntry` table gives each entry's TraitDefinition id, and the
  sim's trees list those ids in order (digit i of tree t). Trailing zeros of
  each tree and trailing dashes are trimmed, as the sim writes it. Left out
  when the record has no class config or no table is cached; a talent
  whose entry or definition is not found, or whose rank is over 9, is
  listed and left out of the string. The table is the record's build's
  when cached, else the install's; the note says when it is not the
  record's.
- `gear.items`: one entry per sim slot, in the sim's slot order, `null` for
  an empty one, up to the last filled (as the exporter addon writes it; the
  import drops the nulls and places each item by its type). Each item is
  `{"id", "enchant", "random_suffix"}`, the last two only when set. The
  shirt and the tabard have no sim slot and are listed. Of each link, the
  item, enchant, suffix, gem and bonus fields are read, and what of them
  the sim cannot take is listed: the sim's `ItemSpec` has no gems (its gem
  fields are reserved at the pinned commit); a random suffix is a bonus
  list id in this sim, so the link's one bonus id that is among the sim's
  random suffixes (`[suffixes] ids`, from its `db.json`) is
  `random_suffix`, and every other bonus id is listed as not one of them;
  the link's own suffix field (empty on every committed Forever capture) is
  not a bonus list and is listed. The link's other fields (link level,
  specialization, item context, modifiers) are neither exported nor listed:
  the import reads nothing like them.

The export carries no `version`: it is not the WoWSims Exporter addon's.
The import then shows its "not up to date" warning and imports anyway
(`addon.ts`, the version check), and it refuses a text of another class
than the sim page's (`finish_individual_import.ts`, `wrong_class`); the
note says both. Whether WoWSims imports a real export is the owner's check
(M21-O2): the note says it is not yet confirmed, and the recorded expected
output in the tests is marked **[verify]** until then.
"""

from __future__ import annotations

import functools
import json
import re
import tomllib
import uuid
from collections.abc import Iterable, Mapping, Sequence
from importlib import resources
from pathlib import Path
from typing import Any

import platformdirs
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from wowlab_core import gear, labaddon
from wowlab_core.errors import WowlabError
from wowlab_core.lookstore import LookLocationError, refuse_install
from wowlab_core.snapshot import make_private_dirs

__all__ = [
    "EXPORTS_DIR",
    "FORMAT_FILE",
    "TALENT_TABLE",
    "FormatError",
    "LeftOut",
    "OutError",
    "SimExport",
    "SimExportError",
    "SimFormat",
    "Talents",
    "build",
    "class_name",
    "definitions",
    "exports_dir",
    "load_format",
    "out_path",
    "parse_format",
    "purchased",
    "talents_of",
    "write_export",
]

FORMAT_FILE = "wowsims_format.toml"
TALENT_TABLE = "TraitNodeEntry"
"""The game table that gives each trait entry id its TraitDefinition id."""
EXPORTS_DIR = "sim-exports"
"""The folder under wowlab's user data directory `--out` writes into."""
_SIZE_LIMIT = 64 * 1024  # the shipped file is about 9 KiB
_COMMIT = re.compile(r"[0-9a-f]{40}")
_WORD = re.compile(r"[a-z]{1,32}")
_NAME = re.compile(r"[A-Za-z][A-Za-z ]{0,63}")
_ID = re.compile(r"[1-9][0-9]{0,8}")
_MAX_RANK_DIGIT = 9


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class SimExportError(WowlabError, ValueError):
    """The record cannot be exported (it names no class)."""


class FormatError(ValueError):
    """`wowsims_format.toml` cannot be used (a packaging fault)."""


class OutError(WowlabError, ValueError):
    """`--out` names a file the export will not be written to."""


# ─── the shipped format ──────────────────────────────────────────────────────


class Source(_Model):
    repository: str
    commit: str = Field(pattern=f"^{_COMMIT.pattern}$")
    read: list[str]


class Format(_Model):
    flavor: str = Field(pattern=f"^{_WORD.pattern}$")
    level: int = Field(ge=1, le=1000)
    slots: list[int] = Field(min_length=1, max_length=32)


class Suffixes(_Model):
    ids: list[int] = Field(min_length=1, max_length=10_000)


class ClassTalents(_Model):
    trees: list[list[int]] = Field(min_length=1, max_length=3)


class SimFormat(_Model):
    """The shipped file: where it was read, the export's `flavor`, the level
    the sim models, the sim's slot order, its random suffix ids, race and
    class names by game id, and each class's talent trees as
    TraitDefinition ids in the sim's order."""

    source: Source
    format: Format
    suffixes: Suffixes
    races: dict[int, str]
    classes: dict[int, str]
    talents: dict[str, ClassTalents]

    @model_validator(mode="after")
    def _consistent(self) -> SimFormat:
        if len(set(self.format.slots)) != len(self.format.slots):
            raise ValueError("a slot is listed twice")
        if any(i <= 0 for i in self.suffixes.ids):
            raise ValueError("a random suffix id is not a positive id")
        if len(set(self.suffixes.ids)) != len(self.suffixes.ids):
            raise ValueError("a random suffix id is listed twice")
        for name in self.races.values():
            if not _NAME.fullmatch(name):
                raise ValueError(f"race name {name!r} is not a plain name")
        for name in self.classes.values():
            if not _WORD.fullmatch(name):
                raise ValueError(f"class name {name!r} is not a lower-case word")
            if name not in self.talents:
                raise ValueError(f"class {name!r} has no talent trees")
        for name, own in self.talents.items():
            if name not in self.classes.values():
                raise ValueError(f"talent trees for {name!r}, which is no class")
            ids = [i for tree in own.trees for i in tree]
            if len(set(ids)) != len(ids):
                raise ValueError(f"a talent of {name!r} is listed twice")
        return self


def _ids(table: object, where: str) -> dict[int, object]:
    if not isinstance(table, dict):
        raise FormatError(f"[{where}] must be a table")
    out: dict[int, object] = {}
    for key, value in table.items():
        if not isinstance(key, str) or not _ID.fullmatch(key):
            raise FormatError(f"[{where}] key {key!r} is not an id")
        out[int(key)] = value
    return out


def parse_format(text: str) -> SimFormat:
    """The format file from TOML text. Raises `FormatError`."""
    if len(text) > _SIZE_LIMIT:
        raise FormatError(f"larger than {_SIZE_LIMIT} characters")
    try:
        data: dict[str, Any] = tomllib.loads(text)
        data["races"] = _ids(data.get("races"), "races")
        data["classes"] = _ids(data.get("classes"), "classes")
        return SimFormat.model_validate(data)
    except FormatError:
        raise
    except (tomllib.TOMLDecodeError, ValidationError, ValueError, TypeError, RecursionError) as exc:
        raise FormatError(f"{type(exc).__name__}: {exc}"[:500]) from exc


@functools.cache
def load_format() -> SimFormat:
    """The shipped `wowsims_format.toml`. Raises `FormatError` naming it."""
    where = resources.files("wowlab_core").joinpath(FORMAT_FILE)
    try:
        data = where.read_bytes()
        if len(data) > _SIZE_LIMIT:
            raise FormatError(f"larger than {_SIZE_LIMIT} bytes")
        return parse_format(data.decode("utf-8"))
    except (OSError, UnicodeDecodeError, FormatError) as exc:
        raise FormatError(f"{where}: {exc}") from exc


# ─── the result ──────────────────────────────────────────────────────────────


class LeftOut(_Model):
    """One thing the record holds that the export does not carry, and why."""

    what: str
    why: str


class Talents(_Model):
    """The talent string for the export, or None with why not; and the
    talents left out of it."""

    string: str | None
    absent: str | None
    left_out: list[LeftOut]
    table_build: str | None  # the build whose TraitNodeEntry table was read
    record_build: str | None  # the build the record was saved by, when it names one
    # Said when the table read is not the record's build's: the ids may differ.
    table_note: str | None


class SimExport(_Model):
    """The export: `text` is the JSON to paste (one line), `export` the same
    object; what the record holds that the export leaves out, and where the
    format was read."""

    text: str
    export: dict[str, Any]
    left_out: list[LeftOut]
    talents_table_build: str | None
    record_build: str | None
    source_repository: str
    source_commit: str
    note: str


# The note, in player words (no marker: the CLI, `--json` and the page say
# exactly this). Each sentence about WoWSims is read from its source at the
# pinned commit; whether it imports a real export is the owner's check.
_NOTE_PASTE = (
    "Paste this into WoWSims' Addon Import, on the sim page for this character's class: the "
    "import refuses another class. WoWSims then warns that the addon is not up to date, since "
    "this text is wowlab's and carries no addon version, and imports it anyway."
)
_NOTE_LEVEL = (
    "WoWSims also warns that the character is below level {sim}: it simulates a level {sim} "
    "character in this gear."
)
_NOTE_END = (
    "Whether WoWSims imports this export is not yet confirmed. The format was read from "
    "wowsims/forever at commit {commit}; wowlab fetched nothing and sends nothing."
)


# ─── the class ───────────────────────────────────────────────────────────────


def class_name(identity: labaddon.CharIdentity, fmt: SimFormat) -> str:
    """The sim's class name for the record's class id, else for its
    `class_file`. Raises `SimExportError` when neither names a sim class."""
    if identity.class_id is not None:
        name = fmt.classes.get(identity.class_id)
        if name is not None:
            return name
        raise SimExportError(f"class id {identity.class_id} is not one of the sim's classes")
    file = (identity.class_file or "").lower()
    if file in fmt.classes.values():
        return file
    reason = identity.class_id_absent or "the record holds no class"
    raise SimExportError(f"the sim needs the class, and the record has none: {reason}")


# ─── the talents ─────────────────────────────────────────────────────────────


def purchased(record: labaddon.CharDB) -> list[tuple[int, int]] | str:
    """The (trait entry id, rank) of every node of the record's class
    config that holds a rank (its current rank, else its purchased ranks)
    and an active entry, in the record's order; or why there are none to
    read. A node with a rank and no active entry is skipped: its talent is
    not known."""
    talents = record.talents
    section = talents.class_ if talents is not None else None
    if section is None:
        return f"the class talent record is {labaddon.NOT_IN_FILE}"
    if isinstance(section, labaddon.AbsentSection):
        return f"the class talent record is absent ({section.absent})"
    config = section.config
    if isinstance(config, labaddon.AbsentConfig):
        return f"the class talent config is absent ({config.absent})"
    out: list[tuple[int, int]] = []
    for tree in config.trees:
        for node in tree.nodes:
            rank = node.current_rank if node.current_rank is not None else node.ranks_purchased
            if rank is None or rank <= 0 or node.active_entry is None:
                continue
            out.append((node.active_entry, rank))
    return out


def definitions(rows: Iterable[Mapping[str, str]]) -> dict[int, int]:
    """Trait entry id to TraitDefinition id, from `TraitNodeEntry` rows (its
    `ID` and `TraitDefinitionID` columns). A row without both as whole
    numbers is skipped."""
    out: dict[int, int] = {}
    for row in rows:
        entry, definition = row.get("ID") or "", row.get("TraitDefinitionID") or ""
        if _ID.fullmatch(entry) and _ID.fullmatch(definition):
            out[int(entry)] = int(definition)
    return out


def talents_of(
    ranks: list[tuple[int, int]] | str,
    klass: str,
    table: Mapping[int, int] | None,
    *,
    table_build: str | None,
    table_absent: str | None = None,
    record_build: str | None = None,
    fmt: SimFormat | None = None,
) -> Talents:
    """The sim's talent string for class `klass` from `purchased`'s ranks and
    the `definitions` of the cached table of `table_build` (None when none is
    cached: `table_absent` says why), or why there is none. `record_build` is
    the build the record was saved by; when the table is another build's,
    `table_note` says so."""
    fmt = fmt if fmt is not None else load_format()
    if isinstance(ranks, str):
        return Talents(
            string=None, absent=ranks, left_out=[], table_build=None,
            record_build=record_build, table_note=None,
        )  # fmt: skip
    if table is None:
        why = table_absent or f"the game table {TALENT_TABLE} is not cached"
        return Talents(
            string=None, absent=why, left_out=[], table_build=None,
            record_build=record_build, table_note=None,
        )  # fmt: skip
    note = None
    if table_build != record_build:
        saved = f"was saved by {record_build}" if record_build else "names no game version"
        note = (
            f"The record {saved}; its talents were mapped with the {TALENT_TABLE} table of "
            f"{table_build}, whose ids may differ."
        )
    trees = fmt.talents[klass].trees
    where = {d: (t, i) for t, tree in enumerate(trees) for i, d in enumerate(tree)}
    points = [[0] * len(tree) for tree in trees]
    left_out: list[LeftOut] = []
    for entry, rank in ranks:
        definition = table.get(entry)
        what = f"talent entry {entry} (rank {rank})"
        if definition is None:
            why = f"not in the {TALENT_TABLE} table of {table_build}"
            left_out.append(LeftOut(what=what, why=why))
            continue
        place = where.get(definition)
        if place is None:
            why = f"its definition {definition} is not in the sim's {klass} trees"
            left_out.append(LeftOut(what=what, why=why))
            continue
        if rank > _MAX_RANK_DIGIT:
            left_out.append(LeftOut(what=what, why="the sim's talent string holds one digit"))
            continue
        tree_at, index = place
        points[tree_at][index] = rank
    parts = ["".join(str(p) for p in tree).rstrip("0") for tree in points]
    string = "-".join(parts).rstrip("-")
    return Talents(
        string=string, absent=None, left_out=left_out, table_build=table_build,
        record_build=record_build, table_note=note,
    )  # fmt: skip


# ─── the gear ────────────────────────────────────────────────────────────────


def _slot_words(s: gear.GearSlot) -> str:
    return f"slot {s.slot} {s.slot_name}" if s.slot_name else f"slot {s.slot}"


def _items(
    slots: Sequence[gear.GearSlot], fmt: SimFormat
) -> tuple[list[dict[str, int] | None], list[LeftOut]]:
    order = {game: at for at, game in enumerate(fmt.format.slots)}
    suffixes = frozenset(fmt.suffixes.ids)
    items: list[dict[str, int] | None] = [None] * len(fmt.format.slots)
    left_out: list[LeftOut] = []
    for s in sorted(slots, key=lambda s: s.slot):
        what = f"{_slot_words(s)}: {s.name}"
        at = order.get(s.slot)
        if at is None:
            left_out.append(LeftOut(what=what, why="the sim has no such slot"))
            continue
        if s.item_id is None or s.item_id <= 0:
            left_out.append(LeftOut(what=what, why="its link holds no item id"))
            continue
        item: dict[str, int] = {"id": s.item_id}
        if s.enchant_id:
            item["enchant"] = s.enchant_id
        known = [b for b in s.bonus_ids if b in suffixes]
        others = [b for b in s.bonus_ids if b not in suffixes]
        if len(known) == 1:
            item["random_suffix"] = known[0]
        elif known:
            ids = ", ".join(str(b) for b in known)
            why = f"its bonus ids {ids}: each is one of the sim's random suffixes, and it takes one"
            left_out.append(LeftOut(what=what, why=why))
        for bonus in others:
            why = f"its bonus id {bonus}: not one of the sim's random suffixes"
            left_out.append(LeftOut(what=what, why=why))
        if s.suffix_id:
            why = (
                f"its link's suffix field ({s.suffix_id}): the sim's random suffixes are bonus "
                "lists, not this field"
            )
            left_out.append(LeftOut(what=what, why=why))
        if s.gem_ids:
            gems = ", ".join(str(g) for g in s.gem_ids)
            why = f"its gems ({gems}): the sim's items carry no gems"
            left_out.append(LeftOut(what=what, why=why))
        items[at] = item
    while items and items[-1] is None:
        items.pop()
    return items, left_out


# ─── the export ──────────────────────────────────────────────────────────────


def build(
    identity: labaddon.CharIdentity,
    slots: Sequence[gear.GearSlot],
    *,
    level: int | None,
    talents: Talents,
    gear_absent: str | None = None,
    fmt: SimFormat | None = None,
) -> SimExport:
    """The export for a character: `identity`, `slots` and `level` as
    `CharGearReport` gives them, `talents` from `talents_of`, and
    `gear_absent` the reason when the record has no gear section (`slots`
    then empty). Raises `SimExportError` when the record names no class the
    sim has; anything else missing is left out and listed."""
    fmt = fmt if fmt is not None else load_format()
    klass = class_name(identity, fmt)
    left_out: list[LeftOut] = []
    export: dict[str, Any] = {"class": klass, "flavor": fmt.format.flavor}
    if identity.race_id is None:
        left_out.append(LeftOut(what="race", why=identity.race_id_absent or "not in the record"))
    elif identity.race_id not in fmt.races:
        why = f"race id {identity.race_id} is not one of the sim's races"
        left_out.append(LeftOut(what="race", why=why))
    else:
        export["race"] = fmt.races[identity.race_id]
    if level is not None and level > 0:
        export["level"] = level
    else:
        left_out.append(LeftOut(what="level", why="the record holds no level"))
    if talents.string is not None:
        export["talents"] = talents.string
    else:
        left_out.append(LeftOut(what="talents", why=talents.absent or "not in the record"))
    left_out.extend(talents.left_out)
    items, gear_left_out = _items(slots, fmt)
    if gear_absent is not None:
        left_out.append(LeftOut(what="gear", why=gear_absent))
    else:
        export["gear"] = {"items": items}
    left_out.extend(gear_left_out)
    text = json.dumps(export, ensure_ascii=True, separators=(",", ":"))
    sentences = [_NOTE_PASTE]
    if level is not None and 0 < level < fmt.format.level:
        sentences.append(_NOTE_LEVEL.format(sim=fmt.format.level))
    if talents.table_note is not None:
        sentences.append(talents.table_note)
    sentences.append(_NOTE_END.format(commit=fmt.source.commit))
    return SimExport(
        text=text,
        export=export,
        left_out=left_out,
        talents_table_build=talents.table_build,
        record_build=talents.record_build,
        source_repository=fmt.source.repository,
        source_commit=fmt.source.commit,
        note=" ".join(sentences),
    )


# ─── the file ────────────────────────────────────────────────────────────────

_FILE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
# Names Windows keeps for devices, with any extension (`NUL.json` is NUL).
_DEVICE = re.compile(r"(?i)(?:CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])(?:\..*)?")
_WHAT = "sim exports"


def exports_dir() -> Path:
    """`<user data dir>/wowlab/sim-exports`, read at call time."""
    return platformdirs.user_data_path("wowlab") / EXPORTS_DIR


def out_path(out: str) -> Path:
    """Where `--out <out>` writes: `out` is a plain file name (letters,
    digits, `.`, `_` and `-`, at most 100, not starting with a dot or
    ending in one, and not a name Windows keeps for a device), taken inside
    `exports_dir()`. Raises `OutError`; reads only."""
    bad = not _FILE_NAME.fullmatch(out) or out.endswith(".") or ".." in out
    if bad or _DEVICE.fullmatch(out):
        raise OutError(
            f"--out takes a plain file name (letters, digits, '.', '_' and '-'), which goes "
            f"into wowlab's {EXPORTS_DIR} folder; {out[:120]!r} is not one"
        )
    return exports_dir() / out


def write_export(text: str, out: str) -> Path:
    """Write `text` (and a final newline) to `out_path(out)`, replacing an
    earlier export of that name whole, and return the path. Refused with
    nothing written (`OutError`) when the folder or the file is a link, the
    file is not a regular file, or either is inside a game install."""
    target = out_path(out)
    folder = target.parent
    try:
        refuse_install(folder, _WHAT)
        if folder.is_symlink():
            raise OutError(f"{folder} is a link; an export is written only into a real folder")
        make_private_dirs(folder)  # 0700 under the user data directory (M16-78)
        refuse_install(folder, _WHAT)
        if folder.is_symlink() or not folder.is_dir():
            raise OutError(f"{folder} is not a folder wowlab made")
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise OutError(f"{target} is not a plain file; nothing was written")
    except LookLocationError as exc:
        raise OutError(f"refused: {exc}; nothing was written") from exc
    tmp = folder / f".{target.name}.{uuid.uuid4().hex}.tmp"
    try:
        with tmp.open("xb") as handle:
            handle.write((text + "\n").encode("ascii"))
        tmp.replace(target)
    finally:
        tmp.unlink(missing_ok=True)
    return target
