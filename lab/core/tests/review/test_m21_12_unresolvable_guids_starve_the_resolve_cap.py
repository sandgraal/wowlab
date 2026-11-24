# Probe from review of m21/12-full-names-region; reproduces 100 GUIDs the client cannot name using up every login's MAX_RESOLVES, so no player met afterwards is ever named
"""`resolveNames` (lab/addon/WowLab/Crew.lua, pinned token for token in
tests/addon/test_m21_12_full_names.py) counts every GUID it asks against
MAX_RESOLVES (100 per login or /reload), whether or not the client answers,
and runs at the end of every roster pass, the first one 15 s after login.
The ticket wants a GUID the client no longer knows "retried later", and the
README promises "a full book is named a little at a time".

Once a book holds 100 or more players with a `guid` the client cannot name
(players met in earlier sessions; the README itself says the client is
believed to answer only for players it still has cached), the first pass of
every login spends the whole cap re-asking them. Then:

1. a player met later in that session, whom the client does know, is never
   asked in that session (the cap is gone), whatever order `pairs` gives;
2. with a stable `pairs` order (the model's dict order), a known player
   placed after them is never asked in any later login either.

The book can hold MAX_PLAYERS = 20000 players, so this is the normal state of
a book after a few weeks of battlegrounds, not a corner.

Played through the branch's own line-for-line Python model of the pinned
function (`resolve_names`), as its stub-client tests do. Nothing runs Lua
(L3). CONSTRUCTED INPUT: every GUID and name is invented.

Positive control: with no unresolvable GUIDs in the book, the same newly met
player is named by the next pass of the same session.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "tests" / "addon"))

import test_m21_12_full_names as m  # noqa: E402

OLD = [f"Player-9998-{i:08X}" for i in range(m.MAX_RESOLVES)]  # the client cannot name these
NEW = "Player-9998-7FFFFFFF"  # met this session: the client knows them
NEW_INFO = (*m.TAY_INFO[:5], "Invented Newcomer", "", 20)


def _old_book() -> Any:
    return m.Book(players={f"Invented{i}-Old": {"met": 1, "guid": g} for i, g in enumerate(OLD)})


def _meet_new(book: Any, client: Any) -> None:
    """A battleground scoreboard row recorded later in the session."""
    book.players["Invented-Newcomer"] = {"met": 1, "guid": NEW}
    client.known[NEW] = NEW_INFO


def test_control_with_no_unresolvable_guids_a_new_player_is_named_the_same_session() -> None:
    book = m.Book(players={})
    client = m.Client()
    session = m.Session()
    m.resolve_names(book, client, session)  # the pass 15 s after login
    _meet_new(book, client)
    m.resolve_names(book, client, session)  # the pass after the match
    assert book.players["Invented-Newcomer"].get("resolved_name") == "Invented Newcomer"


def test_unresolvable_guids_use_up_the_cap_before_a_new_player_is_met() -> None:
    book = _old_book()
    client = m.Client()  # knows none of OLD
    session = m.Session()
    m.resolve_names(book, client, session)  # the pass 15 s after login
    assert session.asked == m.MAX_RESOLVES  # all spent on GUIDs that gave nothing
    _meet_new(book, client)
    m.resolve_names(book, client, session)  # the pass after the match
    assert NEW in client.asked, (
        "a player the client knows, met after login, was never asked: the cap was spent "
        "re-asking GUIDs the client cannot name"
    )


def test_a_known_player_behind_them_is_never_asked_in_any_later_login() -> None:
    book = _old_book()
    _meet_new(book, m.Client())
    client = m.Client(known={NEW: NEW_INFO})
    for _ in range(10):  # ten logins, the same order each time
        m.resolve_names(book, client, m.Session())
    assert NEW in client.asked, "ten logins and the known player was never asked"
