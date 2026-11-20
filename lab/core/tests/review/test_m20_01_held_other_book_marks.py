# Probe from review of m20/01-crewdb-sync; reproduces a held sync of another book, confirmed by hand, replacing a rating the database changed since it last agreed with the saved file.
"""CONSTRUCTED books and databases (ADR-0036 term 3), built at run time.

§21.3 "What a sync will not take": from a held book, a sync by hand
"replaces a rating or note only where the database's own row has not
changed since it last agreed with the saved file". `_merged` applies that
only in the `restored` mode; a book held as another identity (`new_book`)
overwrites the database's own change. The positive control is the same
change under a put-back hold, which is kept.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "parser"))

from _crew_build import KEY_A, T1, account_file, book, player
from test_crewdb import _three, make  # noqa: F401


def _db_rates_good(env: Any) -> None:
    conn = sqlite3.connect(env.db())
    conn.execute(
        "UPDATE players SET rating = 'good', marks_changed = marks_agreed + 1 WHERE player_key = ?",
        (KEY_A,),
    )
    conn.commit()
    conn.close()


def test_control_put_back_book_keeps_the_databases_rating_constructed(make: Any) -> None:  # noqa: F811
    env = make(_three())
    env.sync()
    _db_rates_good(env)
    env.gate(
        "restore 20261119-000000-abcd (WTF)",
        account_file(book({KEY_A: player(rating="bad", met=1)})),
    )
    assert env.sync().held == ["put_back"]
    assert env.run("crew", "sync", input="y\n").exit_code == 0
    assert env.rows()[KEY_A].rating == "good"


def test_another_book_keeps_the_databases_rating_constructed(make: Any) -> None:  # noqa: F811
    env = make(_three())
    env.sync()
    _db_rates_good(env)
    env.save(book({KEY_A: player(rating="bad", met=1)}, started=T1))  # a new book
    assert env.sync().held == ["other_book"]
    assert env.run("crew", "sync", input="y\n").exit_code == 0
    assert env.rows()[KEY_A].rating == "good"  # fails: 'bad'
