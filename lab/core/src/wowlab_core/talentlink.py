"""talentlink: "Open my talents" in WoW Forever Builds' talent calculator (M21-07).

wowforeverbuilds.com's calculator opens a build from its address,
`/talents/<class>?b=<code>&race=<race>` (`link_sites.toml`, `[links.talents]`
and `[talents]`, where what was read from the site's script is recorded).
The code is, per tree in the order the game draws them (left to right), one
digit per talent, the talent's rank, with the talents sorted by row and then
column; trailing zeros of each tree are cut, empty trees at the end are
dropped, and the trees are joined with `-`. This module makes that code from
what the lab-addon recorded and the game's own tables, and `links` makes the
address. Nothing is fetched and nothing is sent: the tables are read from
wowlab's cache only, and the address is opened by the player's browser when
the player clicks it.

**Where the ranks are.** Forever's class talents are `C_Traits` nodes of one
trait tree per class, and the lab-addon records them under `talents.class`
(the class config of the spec active when the character last saved: each
node's id and rank), as `simexport` reads them for WoWSims. `talents.legacy`
holds the candidates for Forever's separate Legacy system (trait trees of
another system, 45 in the captures; that 45 is the Legacy system is
**[verify]**), which the calculator does not show.

**Where each talent sits.** The game's `Talent` and `TalentTab` tables are
still in the Forever client, but they are the classic layout: at
1.60.1.70888 they place Improved Life Tap at row 1, column 2 and lack the
new talents (Soul Harvest, Wrack, ...), where the game, and the site, draw
it at row 0, column 0 (docs/DATA_SOURCES.md, 2026-11-24). What the game
draws is `TraitNode`'s `PosX` and `PosY` for each node of the class's trait
tree. Splitting that tree into its three is wowlab's own reading of
`TraitNodeGroup` and `TraitNodeGroupXTraitNode`: of the groups of a tree,
the ones no other group contains are taken as its three trees (each class at
1.60.1.70888: three such groups, disjoint, holding every node). The talent
frame draws one header per group `C_Traits.GetGroupDisplayInfoByTreeID`
returns, and the tables do not say which groups those are, so that these
are the display groups is **[verify]**; the reading is backed by the site's
layout, below.
A row is the nodes whose `PosY` lie within `ROW_TOLERANCE` of the row's
first (rows are 600 apart there, and a node may sit 10 off its row), a
column the order of `PosX` within the row. Checked by hand on 2026-11-24
against the site's own layout of all nine classes: seven alike talent for
talent; for the hunter and the priest the tables hold three more nodes
(two and one), each drawn far outside the grid and absent from the site,
which sort after every talent of their tree and so, unranked, change no
code.

A tree the groups do not split cleanly, a ranked node the tables do not
hold, a rank over 9, or a class or race the site does not name, gives no
link and says why. The site does not refuse a code in its address that is
not a legal build for it: on load its script caps each rank at the talent's
maximum, drops talents whose prerequisites or row gate fail, trims to its
51 points, ignores digits past its talents, and rewrites the address bar
with the code of what it kept (its strict check runs only on a code pasted
into its import box). So the owner's check (M21-O2) is that after the page
loads the address still reads the code wowlab built.

Nothing here names a client folder, a product or a build (L6): the build
read is the record's, else the install's, and the room the address is made
for comes from `dressing_sites.toml`'s band table through `links`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from wowlab_core import gamedata, labaddon, links

__all__ = [
    "MAX_DIGIT",
    "ROW_TOLERANCE",
    "TABLES",
    "CachedLayout",
    "Layout",
    "TalentsLink",
    "cached_layout",
    "code_of",
    "layout_of",
    "ranks_of",
    "talents_link",
]

TABLES = gamedata.TALENT_TABLES
ROW_TOLERANCE = 150
"""How far, in the tables' `PosY` units, a node may sit from its row's
first node and still be in that row (1.60.1.70888: rows 600 apart, a node at
most 10 off). Layout, not a flavor or a build."""
MAX_DIGIT = 9  # the site's code holds one digit per talent
_INT = re.compile(r"-?[0-9]{1,9}")
_SHOWN_UNKNOWN = 3


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


# ─── the layout ──────────────────────────────────────────────────────────────


class Layout(_Model):
    """Each trait tree's trees as the talent frame draws them, left to right,
    each its node ids by row and then column (`trees`); why a tree could not
    be split (`unsplit`); and each node's trait tree (`tree_of`)."""

    trees: dict[int, list[list[int]]]
    unsplit: dict[int, str]
    tree_of: dict[int, int]


def _int(row: Mapping[str, str], column: str) -> int | None:
    value = row.get(column) or ""
    return int(value) if _INT.fullmatch(value) else None


def _rows(position: dict[int, tuple[int, int]], nodes: Iterable[int]) -> dict[int, int]:
    """Each node's row: nodes sorted by `PosY`, a new row whenever one lies
    more than `ROW_TOLERANCE` below the current row's first."""
    row_of: dict[int, int] = {}
    row, start = -1, None
    for node in sorted(nodes, key=lambda n: (position[n][1], n)):
        y = position[node][1]
        if start is None or y - start > ROW_TOLERANCE:
            row, start = row + 1, y
        row_of[node] = row
    return row_of


def layout_of(
    nodes: Iterable[Mapping[str, str]],
    groups: Iterable[Mapping[str, str]],
    members: Iterable[Mapping[str, str]],
) -> Layout:
    """The layout from the rows of `TraitNode` (`ID`, `TraitTreeID`, `PosX`,
    `PosY`), `TraitNodeGroup` (`ID`, `TraitTreeID`) and
    `TraitNodeGroupXTraitNode` (`TraitNodeGroupID`, `TraitNodeID`). A row
    without those columns as whole numbers is skipped."""
    position: dict[int, tuple[int, int]] = {}
    tree_of: dict[int, int] = {}
    for row in nodes:
        node, tree = _int(row, "ID"), _int(row, "TraitTreeID")
        x, y = _int(row, "PosX"), _int(row, "PosY")
        if node is None or tree is None or x is None or y is None:
            continue
        position[node] = (x, y)
        tree_of[node] = tree
    group_tree: dict[int, int] = {}
    for row in groups:
        group, tree = _int(row, "ID"), _int(row, "TraitTreeID")
        if group is not None and tree is not None:
            group_tree[group] = tree
    held: dict[int, set[int]] = {}
    for row in members:
        group, node = _int(row, "TraitNodeGroupID"), _int(row, "TraitNodeID")
        if group is None or node is None or group not in group_tree:
            continue
        if tree_of.get(node) == group_tree[group]:
            held.setdefault(group, set()).add(node)
    by_tree: dict[int, list[frozenset[int]]] = {}
    for group, members_of in held.items():
        by_tree.setdefault(group_tree[group], []).append(frozenset(members_of))
    of_tree: dict[int, list[int]] = {}
    for node, tree in tree_of.items():
        of_tree.setdefault(tree, []).append(node)
    trees: dict[int, list[list[int]]] = {}
    unsplit: dict[int, str] = {}
    for tree, all_nodes in of_tree.items():
        sets = set(by_tree.get(tree, []))
        tops = [s for s in sets if not any(s < other for other in sets)]
        covered = frozenset().union(*tops) if tops else frozenset()
        if sum(len(s) for s in tops) != len(covered) or covered != set(all_nodes):
            unsplit[tree] = (
                "the game's tables do not split this class's talents into trees wowlab can read"
            )
            continue
        row_of = _rows(position, all_nodes)
        ordered = sorted(tops, key=lambda s: min((position[n][0], n) for n in s))
        trees[tree] = [sorted(s, key=lambda n: (row_of[n], position[n][0], n)) for s in ordered]
    return Layout(trees=trees, unsplit=unsplit, tree_of=tree_of)


# ─── the record's ranks ──────────────────────────────────────────────────────


def ranks_of(record: labaddon.CharDB) -> dict[int, int] | str:
    """Each node of the record's class talent config that holds a rank (its
    current rank, else its purchased ranks), by node id; or why there are
    none to read."""
    talents = record.talents
    section = talents.class_ if talents is not None else None
    if section is None:
        return f"the class talent record is {labaddon.NOT_IN_FILE}"
    if isinstance(section, labaddon.AbsentSection):
        return f"the class talent record is absent ({section.absent})"
    config = section.config
    if isinstance(config, labaddon.AbsentConfig):
        return f"the class talent config is absent ({config.absent})"
    out: dict[int, int] = {}
    for tree in config.trees:
        for node in tree.nodes:
            rank = node.current_rank if node.current_rank is not None else node.ranks_purchased
            if rank is not None and rank > 0:
                out[node.id] = rank
    return out


def code_of(ranks: Mapping[int, int], layout: Layout, *, build: str) -> str:
    """The site's code for `ranks` (node id to rank) placed by `layout`, the
    tables of `build`. Raises `ValueError` with why there is none, in the
    player's words."""
    if not ranks:
        raise ValueError("the record holds no talent points spent")
    unknown = sorted(n for n in ranks if n not in layout.tree_of)
    if unknown:
        shown = ", ".join(str(n) for n in unknown[:_SHOWN_UNKNOWN])
        more = f" and {len(unknown) - _SHOWN_UNKNOWN} more" if len(unknown) > _SHOWN_UNKNOWN else ""
        word, verb = ("talent", "is") if len(unknown) == 1 else ("talents", "are")
        raise ValueError(
            f"{word} {shown}{more} of the record {verb} not in the game tables of {build}"
        )
    trees = {layout.tree_of[n] for n in ranks}
    if len(trees) != 1:
        raise ValueError("the record's talents are in more than one of the game's talent trees")
    [tree] = trees
    if tree in layout.unsplit:
        raise ValueError(layout.unsplit[tree])
    over = sorted(n for n, r in ranks.items() if r > MAX_DIGIT)
    if over:
        raise ValueError(
            f"talent {over[0]} has rank {ranks[over[0]]}; the site's code holds one digit a talent"
        )
    parts = ["".join(str(ranks.get(n, 0)) for n in tab).rstrip("0") for tab in layout.trees[tree]]
    while parts and not parts[-1]:
        parts.pop()
    return "-".join(parts)


# ─── the cache ───────────────────────────────────────────────────────────────


class CachedLayout(_Model):
    """The layout read from the cache and its build; or, with none, the
    tables to download (`download`, for `download_build`) and why."""

    layout: Layout | None
    build: str | None
    download: list[str]
    download_build: str | None
    why: str | None


def cached_layout(builds: Sequence[str | None], *, cache_dir: Path | None = None) -> CachedLayout:
    """The layout from the first of `builds` (the record's, then the
    install's) whose three tables are all cached and read. Never fetches and
    never writes. A build that is not a full game version, or whose cached
    table cannot be read, is passed over for the next."""
    wanting: tuple[str, list[str]] | None = None
    problems: list[str] = []
    for build in dict.fromkeys(b for b in builds if b):
        try:
            missing = gamedata.missing_tables(TABLES, build, cache_dir=cache_dir)
        except ValueError as exc:  # not a full game version
            problems.append(f"{build[:64]!r} cannot be used ({exc})")
            continue
        if missing:
            wanting = wanting or (build, missing)
            continue
        try:
            read = []
            for table in TABLES:
                rows = gamedata.cached_rows(table, build, cache_dir=cache_dir)
                if rows is None:  # removed since missing_tables looked
                    raise OSError(f"{table} is no longer cached")
                read.append(list(rows))
        except (OSError, ValueError, gamedata.GameDataError) as exc:
            problems.append(f"the cached talent tables of {build} cannot be read ({exc})")
            continue
        layout = layout_of(read[0], read[1], read[2])
        return CachedLayout(layout=layout, build=build, download=[], download_build=None, why=None)
    words: list[str] = []
    if wanting is not None:
        words.append(f"the game's talent tables for {wanting[0]} are not downloaded")
    words.extend(problems)
    why = "; ".join(words) or "neither the record nor the install names a game version"
    return CachedLayout(
        layout=None,
        build=None,
        download=list(wanting[1]) if wanting is not None else [],
        download_build=wanting[0] if wanting is not None else None,
        why=why,
    )


# ─── the link ────────────────────────────────────────────────────────────────


class TalentsLink(_Model):
    """`char gear --json`'s `talents` (M21-07): the calculator's address, or
    no link and why (`link`); the talent code (`ranks`); the build whose
    tables placed the talents and the record's; a note when the two differ;
    and, when the tables are not cached, the ones to download for a link
    (`download`, for `download_build`; `wowlab db2 fetch` does it)."""

    link: links.Link | links.NoLink
    ranks: str | None
    tables_build: str | None
    record_build: str | None
    table_note: str | None
    download: list[str]
    download_build: str | None


def _none(made: links.Link | links.NoLink, why: str) -> links.NoLink:
    return links.NoLink(
        link=made.link, site=made.site, site_name=made.site_name, label=made.label, why=why
    )


def talents_link(
    record: labaddon.CharDB, *, version: str | None, cache_dir: Path | None = None
) -> TalentsLink:
    """The "Open my talents" link for `record`, read by a client of
    `version` (it picks the site's room; the install's version is also the
    second build whose tables are tried). Never fetches; never raises for a
    record the reader accepted."""
    record_build = labaddon.client_build(record)
    identity = labaddon.identity_of(record)

    def result(made: links.Link | links.NoLink, **more: object) -> TalentsLink:
        values: dict[str, object] = {
            "link": made,
            "ranks": None,
            "tables_build": None,
            "record_build": record_build,
            "table_note": None,
            "download": [],
            "download_build": None,
        }
        values.update(more)
        return TalentsLink.model_validate(values)

    # First what needs no table: a client, class or race the site has no
    # page for asks for no download.
    before = links.talents(identity.class_id, identity.race_id, "", version=version)
    if isinstance(before, links.NoLink):
        return result(before)
    ranks = ranks_of(record)
    if isinstance(ranks, str):
        return result(_none(before, ranks))
    if not ranks:
        return result(_none(before, "the record holds no talent points spent"))
    cached = cached_layout([record_build, version], cache_dir=cache_dir)
    if cached.layout is None or cached.build is None:
        return result(
            _none(before, cached.why or "the game's talent tables are not downloaded"),
            download=cached.download,
            download_build=cached.download_build,
        )
    note = None
    if cached.build != record_build:
        saved = f"was saved by {record_build}" if record_build else "names no game version"
        note = (
            f"The record {saved}; its talents were placed with the game tables of "
            f"{cached.build}, whose layout may differ."
        )
    try:
        code = code_of(ranks, cached.layout, build=cached.build)
    except ValueError as exc:
        return result(_none(before, str(exc)), tables_build=cached.build, table_note=note)
    made = links.talents(identity.class_id, identity.race_id, code, version=version)
    return result(made, ranks=code, tables_build=cached.build, table_note=note)
