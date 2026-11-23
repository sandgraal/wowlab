"""nemesis: the record against each opponent, who is a nemesis and who is
a rival team (docs/LAB_PLAN.md §21.3, M22-03).

Pure: no database, no install, no clock. The input is a list of fights,
one for each source row that put a player against you; the output is each
player's record, added up from those fights and nothing else, so every
count can be traced back to the rows it came from (`Fight.source` and
`Fight.row`).

The record keeps two parts apart (owner ruling, 2026-11-23):

- **Personal**: killing blows between the two of you, yours on them
  (`kills`) and theirs on you (`deaths`). It decides the **nemesis**
  (`is_nemesis`, `NEMESIS_RULE`).
- **Team**: battleground and arena results, your side's wins and losses in
  the matches you played against them. It decides the **rival team**
  (`is_rival`, `RIVAL_RULE`), and never the nemesis.

Two sources feed it today; each is read by `crewdb` from wowlab's own crew
database:

- **A battleground or arena match** (`match`), from the game's scoreboard:
  one fight with every player on the other side, carrying only its team
  result (your side's win or loss; none when the client gave no winner). A
  solo shuffle has no sides, so it puts no one against you.
- **An open-world encounter** (`world`), from a combat log `crew scan-log`
  read: one fight with each player who fought you in it, carrying only
  killing blows: yours on them and theirs on you. A trade counts both.

A further source plugs in as one more list of fights: a reader that turns
its rows into `Fight`s with a new `Source` value, handed to `records` with
the others. A fight carries killing blows, a team result, or both, so the
battleground kill pairs a combat log will give (a later ticket) are
personal fights like the open world's. Nothing else here changes for them.

Both rules, and their words, live here and nowhere else, so changing a
rule is one edit.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# ─── the rules ───────────────────────────────────────────────────────────────

# How many times a player must have killed you to be a nemesis.
NEMESIS_DEATHS = 3
# How many matches their side must have won against yours to be a rival team.
RIVAL_LOSSES = 3

# Each rule in words, for every help text, report and page that names it.
NEMESIS_RULE = (
    f"A nemesis is a player who has killed you at least {NEMESIS_DEATHS} times, and more "
    "often than you have killed them."
)
RIVAL_RULE = (
    f"A rival team is a player whose side has beaten yours in at least {RIVAL_LOSSES} "
    "battleground or arena matches, and more often than your side has beaten theirs."
)


def is_nemesis(kills: int, deaths: int) -> bool:
    """The nemesis rule (`NEMESIS_RULE`): their killing blows on you
    (`deaths`) at least `NEMESIS_DEATHS`, and more than yours on them
    (`kills`)."""
    return deaths >= NEMESIS_DEATHS and deaths > kills


def is_rival(wins: int, losses: int) -> bool:
    """The rival-team rule (`RIVAL_RULE`): your side's losses to theirs at
    least `RIVAL_LOSSES`, and more than its wins."""
    return losses >= RIVAL_LOSSES and losses > wins


# What counts, in words, for the notes that explain a record.
HOW_COUNTED = (
    "A fight is a battleground or arena match with them on the other side, or an open-world "
    "encounter from a combat log in which they fought you. Killing blows between the two of "
    "you decide a nemesis; the matches your side won or lost against theirs decide a rival "
    "team, and never a nemesis."
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
    named it; the personal part, your killing blows on them (`kills`) and
    theirs on you (`deaths`); and the team part, your side's result against
    theirs (`team`: `win`, `loss`, or None when the row has no team result
    or the client gave no winner)."""

    key: str
    source: Source
    row: int
    at: int
    place: str | None = None
    kills: int = 0
    deaths: int = 0
    team: Result | None = None


def team_result(won: bool | None) -> Result | None:
    """A match's result against each player on the other side: your side's
    win or loss; None when the client gave no winner."""
    if won is None:
        return None
    return "win" if won else "loss"


# ─── the record ──────────────────────────────────────────────────────────────


class Personal(_Model):
    """Killing blows between the two of you: yours on them (`kills`),
    theirs on you (`deaths`), and whether they are your nemesis."""

    kills: int = 0
    deaths: int = 0
    nemesis: bool = False


class Team(_Model):
    """The matches you played against them that had a winner: your side's
    wins and losses against theirs, and whether they are a rival team."""

    wins: int = 0
    losses: int = 0
    rival: bool = False


class Record(_Model):
    """One player's record against you: the fights, the start of the first
    and of the latest (`first`, `last`; None with no fight), how many fights
    each source gave (`sources`, by source name: `match`, `world`), and the
    two parts, `personal` and `team`, kept apart."""

    fights: int = 0
    first: int | None = None
    last: int | None = None
    sources: dict[str, int] = Field(default_factory=dict)
    personal: Personal = Field(default_factory=Personal)
    team: Team = Field(default_factory=Team)


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
        wins += f.team == "win"
        losses += f.team == "loss"
        first = f.at if first is None else min(first, f.at)
        last = f.at if last is None else max(last, f.at)
        sources[f.source] = sources.get(f.source, 0) + 1
    return Record(
        fights=n,
        first=first,
        last=last,
        sources=dict(sorted(sources.items())),
        personal=Personal(kills=kills, deaths=deaths, nemesis=is_nemesis(kills, deaths)),
        team=Team(wins=wins, losses=losses, rival=is_rival(wins, losses)),
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
    """Most killed by them first, then the fewest of your killing blows on
    them, then by stored key."""
    return (-record.personal.deaths, record.personal.kills, key)


def rival_order(record: Record, key: str) -> tuple[int, int, str]:
    """Most team losses to them first, then the fewest team wins over them,
    then by stored key."""
    return (-record.team.losses, record.team.wins, key)
