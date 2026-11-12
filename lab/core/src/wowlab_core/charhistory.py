"""A character over time, from the lab-addon records the snapshots hold
(M17-02, `docs/LAB_PLAN.md` §19 CW-2).

Every snapshot of a flavor's `WTF/` holds each character's `WowLab.lua` as
it was when the snapshot was taken. `collect` reads that file from every
snapshot in the store, through the store's objects (never the install), plus
the live file when the caller gives it, and returns one row per distinct
record, oldest first: its save time, level, average item level (equipped),
spec, and what changed from the row before it (the gear slots, by the item
name in the link and its item level, and the currencies).

What makes two copies one record is the save time: the file's modification
time, which the manifest entry records (the addon writes no time of its
own). Copies with the same save time are read once and shown once, with
every snapshot that holds them named on the row. A copy that cannot be read
(its object missing or damaged, a link, too large, a record the reader does
not accept) is listed with the reason, and the others are still read. A
manifest that does not load is listed by name with its reason. A row whose
bytes are those of an earlier row (a restore puts old bytes back under a new
time) names that row's save time.

Two gear records are compared item instance by item instance, never link
text by link text: an item link carries the character's level (field 8) and
spec (field 9) at the save (`docs/LAB_FORMATS.md`, item links), so a level-up
or a spec change rewrites every link without changing any item. Those two
fields are left out of the comparison.

Reads only. The store is read through `SnapshotStore.list_lenient` and
`read_object`, which never write; the live file is read by the caller's
callable, never here. Nothing is fetched: names come from the callables the
caller passes, which may answer None.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, ConfigDict

from wowlab_core import dressing, gear, itemlink, labaddon, luadata, snapshot

__all__ = [
    "CharHistory",
    "CurrencyChange",
    "GearChange",
    "HistoryItem",
    "HistoryRow",
    "UnreadableCopy",
    "collect",
]

# The reader's words for a copy that is not there to compare.
NOT_COMPARED_FIRST = "the first record"

# The parts of an item instance compared between two saves, with their
# words. The link level (field 8) and the specialization (field 9) are the
# character's, not the item's, and are left out.
_INSTANCE_FIELDS: tuple[tuple[str, str], ...] = (
    ("enchant_id", "enchant"),
    ("gem_ids", "gems"),
    ("suffix_id", "suffix"),
    ("unique_id", "unique id"),
    ("modifiers_mask", "modifier mask"),
    ("context", "item context"),
    ("bonus_ids", "bonus ids"),
    ("modifiers", "modifiers"),
    ("relic_bonus_ids", "relic bonus ids"),
    ("crafter", "crafter field"),
    ("extra_enchant_id", "extra enchant"),
    ("extra_fields", "extra link fields"),
    ("colour", "quality colour"),
    ("name", "name"),
)
_LINK_LEVEL = 8
_LINK_SPEC = 9


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class HistoryItem(_Model):
    """One equipped item as a record holds it: the link's bracketed text,
    its item id, and the client's item level at the save."""

    name: str
    item_id: int | None
    item_level: int | None
    link: str


class GearChange(_Model):
    """One slot whose item differs from the row before. `before` or `after`
    is None for an empty slot. `differs` names what differs: `["item id"]`
    for another item, the instance's parts (`"bonus ids"`, `"item level"`,
    ...) for the same item id, and nothing when one side is empty."""

    slot: int
    slot_name: str | None
    before: HistoryItem | None
    after: HistoryItem | None
    differs: list[str]


class CurrencyChange(_Model):
    """One currency whose quantity differs from the row before. `before`
    or `after` is None when that record does not list the currency (or
    lists it without a quantity)."""

    id: int
    name: str | None
    before: int | None
    after: int | None


class HistoryRow(_Model):
    """One distinct record: a save of the character, as one or more copies
    hold it.

    `saved_at_ns` is the file's modification time. `snapshots` names every
    snapshot holding this save, oldest first; `live` is whether the file in
    the install is this save. `same_bytes_as_ns` is the save time of an
    earlier row whose bytes this row's are (a restored record, most
    likely), else None. `changed_from_ns` is the save time of the row the
    changes are counted from (the row before), None on the first row.
    `gear_not_compared` and `currencies_not_compared` say why a list of
    changes is empty because no comparison was possible (None when the two
    records were compared). `currency_panel_changed` says how the currency
    panel the addon read through differed from the row before (its filter
    or its collapsed headers), which can change what is listed."""

    saved_at_ns: int
    snapshots: list[str]
    live: bool
    same_bytes_as_ns: int | None
    record_schema: int  # the lab-addon's schema number for this record
    client_version: str | None
    level: int | None
    level_absent: str | None
    item_level: labaddon.Number | None  # GetAverageItemLevel's equipped figure
    item_level_absent: str | None
    spec: gear.GearSpec
    spec_name: str | None
    changed_from_ns: int | None
    gear_changes: list[GearChange]
    gear_not_compared: str | None
    currency_changes: list[CurrencyChange]
    currencies_not_compared: str | None
    currency_panel_changed: str | None


class UnreadableCopy(_Model):
    """A copy of the character's file that could not be read: in a
    snapshot (`snapshot_id`, `created_at`), or the file in the install
    (`where` is "install", both None). `saved_at_ns` is the copy's
    modification time when it is known."""

    where: Literal["snapshot", "install"]
    snapshot_id: str | None
    created_at: str | None
    saved_at_ns: int | None
    reason: str


class CharHistory(_Model):
    """What `collect` found. `rows` oldest first; `snapshots_holding` is how
    many snapshots hold the file (read or not), `snapshots_searched` how
    many manifests loaded."""

    rows: list[HistoryRow]
    unreadable: list[UnreadableCopy]
    damaged_snapshots: list[snapshot.InvalidManifest]
    snapshots_searched: int
    snapshots_holding: int


def _no_name(_: int) -> str | None:
    return None


def _no_spec_name(_: int, __: int | None) -> str | None:
    return None


def parse_words(data: bytes) -> labaddon.CharDB | str:
    """The record in `data`, or why it cannot be read (the words the
    `char list` survey uses)."""
    try:
        return labaddon.parse_char(data)
    except labaddon.LabAddonError as exc:
        return str(exc)
    except luadata.LuaLimitError as exc:
        return f"beyond what the SavedVariables parser will hold ({exc.message})"
    except luadata.LuaDataError as exc:
        return f"not SavedVariables data the parser accepts: {exc}"


class _Slot:
    """One filled slot: the row `gear.rows` gives and its link decoded."""

    def __init__(self, row: gear.GearSlot) -> None:
        self.row = row
        self.link = itemlink.parse(row.raw_link)


class _Read:
    """One record read, with what a row and the next comparison need."""

    def __init__(
        self,
        record: labaddon.CharDB,
        sha256: str,
        table: gear.SlotTable,
        spec_name: Callable[[int, int | None], str | None],
    ) -> None:
        self.record = record
        self.sha256 = sha256
        section = record.gear
        self.items: dict[int, _Slot] | None = None
        self.no_gear: str | None = None
        self.average: gear.GearAverage | None = None
        if isinstance(section, labaddon.Gear):
            self.average = gear.average_of(section)
            try:
                filled, _ = gear.rows(section, table)
                self.items = {s.slot: _Slot(s) for s in filled}
            except itemlink.ItemLinkError as exc:  # the record reader admits none
                self.no_gear = f"an item link wowlab cannot read: {exc}"
        elif section is None:
            self.no_gear = f"gear is {labaddon.NOT_IN_FILE}"
        else:
            self.no_gear = f"gear was recorded as absent ({section.absent})"
        self.currencies: dict[int, int | None] | None = None
        self.panel: tuple[int | None, int] | None = None
        self.no_currencies: str | None = None
        cur = record.currencies
        if isinstance(cur, labaddon.Currencies):
            self.currencies = {
                c.id: c.quantity for c in cur.list_ if isinstance(c, labaddon.Currency)
            }
            self.panel = (cur.filter, cur.headers_collapsed)
        elif cur is None:
            self.no_currencies = f"currencies are {labaddon.NOT_IN_FILE}"
        else:
            self.no_currencies = f"currencies were recorded as absent ({cur.absent})"
        self.spec = gear.spec_of(record)
        sex = labaddon.identity_of(record).sex
        self.spec_name = spec_name(self.spec.id, sex) if self.spec.id is not None else None


def _item(s: gear.GearSlot) -> HistoryItem:
    return HistoryItem(name=s.name, item_id=s.item_id, item_level=s.item_level, link=s.raw_link)


def _without_character(link: itemlink.ItemLink) -> list[str]:
    """The link's raw fields with the link level and spec left blank."""
    fields = list(link.fields)
    for i in (_LINK_LEVEL, _LINK_SPEC):
        if i < len(fields):
            fields[i] = ""
    return fields


def instance_differs(a: _Slot, b: _Slot) -> list[str]:
    """What differs between two items, in words: `["item id"]` for another
    item, else the instance's parts that differ, never the link level or
    spec (the character's, not the item's). Empty: the same instance."""
    la, lb = a.link, b.link
    if la.item_id != lb.item_id:
        return ["item id"]
    if not (la.complete and lb.complete):
        # A link whose counts did not fit: its decoded fields may be off, so
        # its raw fields are compared instead, still without fields 8 and 9.
        out = ["link fields"] if _without_character(la) != _without_character(lb) else []
    else:
        out = [words for name, words in _INSTANCE_FIELDS if getattr(la, name) != getattr(lb, name)]
    if a.row.item_level != b.row.item_level:
        out.append("item level")
    return out


def _gear_changes(
    before: _Read, after: _Read, table: gear.SlotTable
) -> tuple[list[GearChange], str | None]:
    if after.items is None:
        return [], after.no_gear
    if before.items is None:
        return [], f"the record before: {before.no_gear}"
    out: list[GearChange] = []
    for slot in sorted(set(before.items) | set(after.items)):
        a, b = before.items.get(slot), after.items.get(slot)
        differs: list[str] = []
        if a is not None and b is not None:
            differs = instance_differs(a, b)
            if not differs:
                continue
        shown = b if b is not None else a
        name = shown.row.slot_name if shown is not None else table.slots.get(slot)
        out.append(
            GearChange(
                slot=slot,
                slot_name=name,
                before=_item(a.row) if a is not None else None,
                after=_item(b.row) if b is not None else None,
                differs=differs,
            )
        )
    return out, None


def _currency_changes(
    before: _Read, after: _Read, currency_name: Callable[[int], str | None]
) -> tuple[list[CurrencyChange], str | None]:
    if after.currencies is None:
        return [], after.no_currencies
    if before.currencies is None:
        return [], f"the record before: {before.no_currencies}"
    out: list[CurrencyChange] = []
    for cid in sorted(set(before.currencies) | set(after.currencies)):
        a, b = before.currencies.get(cid), after.currencies.get(cid)
        if a == b:
            continue
        out.append(CurrencyChange(id=cid, name=currency_name(cid), before=a, after=b))
    return out, None


def _value(n: int | None) -> str:
    return "not returned" if n is None else str(n)


def _panel_change(before: _Read, after: _Read) -> str | None:
    """How the currency panel differed between the two reads, or None."""
    if before.panel is None or after.panel is None or before.panel == after.panel:
        return None
    (f0, h0), (f1, h1) = before.panel, after.panel
    parts = []
    if h0 != h1:
        parts.append(f"{h1} collapsed headers, {h0} the save before")
    if f0 != f1:
        parts.append(f"filter value {_value(f1)}, {_value(f0)} the save before")
    return "the currency panel was read with " + "; ".join(parts)


class _Copy:
    """One copy of the file, before it is read."""

    def __init__(
        self,
        saved_at_ns: int,
        read: Callable[[], bytes],
        *,
        snapshot_id: str | None,
        created_at: str | None,
        sha256: str | None,
    ) -> None:
        self.saved_at_ns = saved_at_ns
        self.read = read
        self.snapshot_id = snapshot_id
        self.created_at = created_at
        self.sha256 = sha256


def _snapshot_copy(
    store: snapshot.SnapshotStore, manifest: snapshot.Manifest, entry: snapshot.Entry
) -> _Copy | UnreadableCopy:
    def unreadable(reason: str) -> UnreadableCopy:
        return UnreadableCopy(
            where="snapshot",
            snapshot_id=manifest.id,
            created_at=manifest.created_at,
            saved_at_ns=entry.mtime_ns,
            reason=reason,
        )

    if entry.kind != "file" or entry.sha256 is None:
        return unreadable("the snapshot holds a link at this path, not the file")
    if entry.size > luadata.MAX_FILE_BYTES:
        return unreadable(
            f"the copy holds {entry.size} bytes, more than {luadata.MAX_FILE_BYTES}, "
            "so it was not read"
        )
    sha, size = entry.sha256, entry.size

    def read() -> bytes:
        return store.read_object(sha, size=size)

    return _Copy(
        entry.mtime_ns, read, snapshot_id=manifest.id, created_at=manifest.created_at, sha256=sha
    )


def _average_words(now: _Read) -> tuple[labaddon.Number | None, str | None]:
    average = now.average
    if average is None:
        return None, now.no_gear
    if average.absent is not None:
        return None, f"the average was recorded as absent ({average.absent})"
    if average.equipped is None:
        return None, "the client returned no equipped average"
    return average.equipped, None


def collect(
    store: snapshot.SnapshotStore,
    path: str,
    *,
    live: tuple[int, Callable[[], bytes]] | None = None,
    table: gear.SlotTable | None = None,
    spec_name: Callable[[int, int | None], str | None] = _no_spec_name,
    currency_name: Callable[[int], str | None] = _no_name,
) -> CharHistory:
    """The history of the file at `path` (relative to the install root, as a
    manifest names it: `<flavor folder>/WTF/Account/...`) in every
    snapshot of `store`, and in `live`, the file in the install as its
    modification time and a callable that reads its bytes (called only when
    no snapshot holds a copy with that save time). `table` names the gear
    slots (the shipped default when None). `spec_name` is given the spec id
    and the record's body type (`Enum.UnitSex`, None when not recorded).
    Never raises for a copy it cannot read; it lists it."""
    slot_table = table if table is not None else gear.shipped_slot_table()
    listing = store.list_lenient()
    copies: list[_Copy] = []
    unreadable: list[UnreadableCopy] = []
    holding = 0
    for manifest in listing.manifests:
        entry = manifest.entry(path)
        if entry is None:
            continue
        holding += 1
        found = _snapshot_copy(store, manifest, entry)
        if isinstance(found, UnreadableCopy):
            unreadable.append(found)
        else:
            copies.append(found)
    if live is not None:
        copies.append(_Copy(live[0], live[1], snapshot_id=None, created_at=None, sha256=None))

    # One read per save time (the first copy that reads), and one parse per
    # object: a restored file has a new time but the bytes it had.
    reads: dict[int, _Read] = {}
    holders: dict[int, list[str]] = {}
    is_live: dict[int, bool] = {}
    parsed: dict[str, _Read | str] = {}
    for copy in copies:
        when = copy.saved_at_ns
        if when not in reads:
            got = _read_copy(copy, parsed, slot_table, spec_name)
            if isinstance(got, str):
                unreadable.append(
                    UnreadableCopy(
                        where="install" if copy.snapshot_id is None else "snapshot",
                        snapshot_id=copy.snapshot_id,
                        created_at=copy.created_at,
                        saved_at_ns=when,
                        reason=got,
                    )
                )
                continue
            reads[when] = got
        if copy.snapshot_id is None:
            is_live[when] = True
        else:
            holders.setdefault(when, []).append(copy.snapshot_id)

    rows: list[HistoryRow] = []
    first_with: dict[str, int] = {}
    before: _Read | None = None
    before_ns: int | None = None
    for when in sorted(reads):
        now = reads[when]
        same_as = first_with.get(now.sha256)
        first_with.setdefault(now.sha256, when)
        panel: str | None = None
        if before is None:
            gear_changes: list[GearChange] = []
            gear_why: str | None = NOT_COMPARED_FIRST
            cur_changes: list[CurrencyChange] = []
            cur_why: str | None = NOT_COMPARED_FIRST
        else:
            gear_changes, gear_why = _gear_changes(before, now, slot_table)
            cur_changes, cur_why = _currency_changes(before, now, currency_name)
            panel = _panel_change(before, now)
        record = now.record
        level = dressing.level_of(record)
        ilvl, ilvl_absent = _average_words(now)
        client = record.client
        rows.append(
            HistoryRow(
                saved_at_ns=when,
                snapshots=holders.get(when, []),
                live=is_live.get(when, False),
                same_bytes_as_ns=same_as,
                record_schema=record.schema_,
                client_version=(client.version if isinstance(client, labaddon.Client) else None),
                level=level,
                level_absent=labaddon.level_absent_of(record) if level is None else None,
                item_level=ilvl,
                item_level_absent=ilvl_absent,
                spec=now.spec,
                spec_name=now.spec_name,
                changed_from_ns=before_ns,
                gear_changes=gear_changes,
                gear_not_compared=gear_why,
                currency_changes=cur_changes,
                currencies_not_compared=cur_why,
                currency_panel_changed=panel,
            )
        )
        before, before_ns = now, when
    return CharHistory(
        rows=rows,
        unreadable=unreadable,
        damaged_snapshots=list(listing.invalid),
        snapshots_searched=len(listing.manifests),
        snapshots_holding=holding,
    )


def _read_copy(
    copy: _Copy,
    parsed: dict[str, _Read | str],
    table: gear.SlotTable,
    spec_name: Callable[[int, int | None], str | None],
) -> _Read | str:
    if copy.sha256 is not None and copy.sha256 in parsed:
        return parsed[copy.sha256]
    try:
        data = copy.read()
    except snapshot.ObjectCorruptError as exc:
        return f"its stored copy cannot be used: {exc}"
    except snapshot.LinkedPathError as exc:
        return str(exc)
    except OSError as exc:
        return f"it cannot be read: {exc.strerror or exc}"
    # The live file's hash is known only now; a snapshot's is its object's
    # name, which `read_object` checked.
    sha = copy.sha256 if copy.sha256 is not None else hashlib.sha256(data).hexdigest()
    if sha in parsed:
        return parsed[sha]
    record = parse_words(data)
    got: _Read | str = record if isinstance(record, str) else _Read(record, sha, table, spec_name)
    parsed[sha] = got
    return got
