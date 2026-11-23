"""nemesis: the record against each opponent, and who is a nemesis
(docs/LAB_PLAN.md §21.3, M22-03).

Pure: no database, no install, no clock. The input is a list of fights,
one for each source row that put a player against you; the output is each
player's record, added up from those fights and nothing else, so every
count can be traced back to the rows it came from (`Fight.source` and
`Fight.row`).

Two sources feed it today; each is read by `crewdb` from wowlab's own crew
database:

- **A battleground or arena match** (`match`), from the game's scoreboard:
  one fight with every player on the other side. Your side's win is a win
  against each of them and its loss a loss to each; a match the client gave
  no winner for decides nothing. A solo shuffle has no sides, so it puts no
  one against you. The match keeps no killing blows.
- **An open-world encounter** (`world`), from a combat log `crew scan-log`
  read: one fight with each player who fought you in it. Your killing blow
  on them is a kill, theirs on you a death. A death decides it: your
  killing blow with none of theirs on you is a win, theirs on you with none
  of yours a loss, and both (you traded) or neither decides nothing.

A third source plugs in as one more list of fights: a reader that turns
its rows into `Fight`s with a new `Source` value, handed to `records` with
the others. Nothing here changes for it.

The nemesis rule is `is_nemesis`, and `RULE` says it in words; both live
here and nowhere else, so changing the rule is one edit.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# ─── the rule ────────────────────────────────────────────────────────────────

# How many times a player must have beaten you to be a nemesis.
NEMESIS_LOSSES = 3

# The rule in words, for every help text, report and page that names it.
RULE = (
    f"A nemesis is a player who has beaten you at least {NEMESIS_LOSSES} times, and more "
    "often than you have beaten them."
)


def is_nemesis(wins: int, losses: int) -> bool:
    """The nemesis rule (`RULE`): at least `NEMESIS_LOSSES` losses to the
    player, and more losses than wins."""
    return losses >= NEMESIS_LOSSES and losses > wins


# How a match and an open-world encounter are decided, in words, for the
# notes that explain a record.
HOW_DECIDED = (
    "A fight is a battleground or arena match with them on the other side, or an open-world "
    "encounter from a combat log in which they fought you. A match is won or lost against "
    "everyone on the other side; in the open world your killing blow on them with none of "
    "theirs on you is a win, and theirs on you with none of yours is a loss."
)

# ─── the input ───────────────────────────────────────────────────────────────

# Where a fight came from. A new source adds a value here and a reader.
Source = Literal["match", "world"]
Result = Literal["win", "loss"]


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Fight(_Model):
    """One player against you in one source row: the player's stored key,
    the source (`match` or `world`), the row's id in that source (a match's
    or an open-world encounter's id in wowlab's crew database), when it
    started (`at`, seconds since the epoch), the map or zone as the client
    named it, your killing blows on them and theirs on you in it, and its
    result for you (`win`, `loss`, or None when nothing decided it)."""

    key: str
    source: Source
    row: int
    at: int
    place: str | None = None
    kills: int = 0
    deaths: int = 0
    result: Result | None = None


def match_result(won: bool | None) -> Result | None:
    """A match's result against each player on the other side: your side's
    win or loss; None when the client gave no winner."""
    if won is None:
        return None
    return "win" if won else "loss"


def world_result(killed_by_you: bool, killed_you: bool) -> Result | None:
    """An open-world encounter's result against one player: a death
    decides it. Your killing blow on them alone is a win, theirs on you
    alone a loss; a trade, or no killing blow, decides nothing."""
    if killed_by_you and not killed_you:
        return "win"
    if killed_you and not killed_by_you:
        return "loss"
    return None


# ─── the record ──────────────────────────────────────────────────────────────


class Record(_Model):
    """One player's record against you: the fights, your killing blows on
    them (`kills`) and theirs on you (`deaths`), the fights you won and lost
    (the others decided nothing), the start of the first and of the latest
    fight (`first`, `last`; None with no fight), whether they are your
    nemesis (`RULE`), and how many of the fights each source gave
    (`sources`, by source name: `match`, `world`)."""

    fights: int = 0
    kills: int = 0
    deaths: int = 0
    wins: int = 0
    losses: int = 0
    first: int | None = None
    last: int | None = None
    nemesis: bool = False
    sources: dict[str, int] = Field(default_factory=dict)


def record_of(fights: Iterable[Fight]) -> Record:
    """The record the fights given add up to (all of one player's)."""
    n = kills = deaths = wins = losses = 0
    first: int | None = None
    last: int | None = None
    sources: dict[str, int] = {}
    for f in fights:
        n += 1
        kills += f.kills
        deaths += f.deaths
        wins += f.result == "win"
        losses += f.result == "loss"
        first = f.at if first is None else min(first, f.at)
        last = f.at if last is None else max(last, f.at)
        sources[f.source] = sources.get(f.source, 0) + 1
    return Record(
        fights=n,
        kills=kills,
        deaths=deaths,
        wins=wins,
        losses=losses,
        first=first,
        last=last,
        nemesis=is_nemesis(wins, losses),
        sources=dict(sorted(sources.items())),
    )


def records(*sources: Iterable[Fight]) -> dict[str, Record]:
    """Each player's record, by stored key, from every source's fights
    together. A player with no fight has no entry."""
    by_key: dict[str, list[Fight]] = {}
    for fights in sources:
        for f in fights:
            by_key.setdefault(f.key, []).append(f)
    return {key: record_of(found) for key, found in by_key.items()}


def nemesis_order(record: Record, key: str) -> tuple[int, int, str]:
    """The order of the nemeses view: most losses to them first, then the
    fewest wins over them, then by stored key."""
    return (-record.losses, record.wins, key)
