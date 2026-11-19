"""The crew-book reader on the owner's own install (M19-02, docs/LAB_PLAN.md
§20.5, "L8 and the reader"; the pattern of ADR-0033). Never run in CI
(ADR-0012); the owner runs it after the in-game check (M19-07):

    WOWLAB_WOW_ROOT="/path/to/World of Warcraft" uv run pytest -m live lab/core/tests/test_crewbook_live.py -s -p no:cacheprovider

It reads every account `WowLab.lua` of every flavor with `crewbook`
(read-only) and prints counts and key shapes only: never a key, a name, a
guild, a note, an instance name, an account folder, a flavor folder or a
path. A read that fails prints the error's class, never its message, which
could quote a token of the file. Nothing is asserted about the counts; the
test fails only when a book cannot be read.

The constructed tests below it (not `live`, so CI runs them) check what a
key's shape says and that the summary holds nothing else.
"""

from __future__ import annotations

import os
from collections import Counter
from pathlib import Path

import pytest

from wowlab_core import crewbook, install, labaddon, layout


def key_shape(key: str) -> str:
    """What a key looks like, never what it says: `<Name>-<Realm>`, with the
    realm part marked when it holds a space, an apostrophe or a character
    outside ASCII, and the hyphens counted when there is not exactly one."""
    hyphens = key.count("-")
    if hyphens != 1:
        return f"<{hyphens} hyphens>"
    name, realm = key.split("-")
    marks = []
    if not name:
        return "<empty name>-<Realm>"
    if not realm:
        return "<Name>-<empty realm>"
    if " " in realm:
        marks.append("spaces")
    if "'" in realm or chr(0x2019) in realm:
        marks.append("an apostrophe")
    if not realm.isascii():
        marks.append("non-ASCII")
    name_mark = "" if name.isascii() else " non-ASCII"
    realm_words = f"<Realm with {', '.join(marks)}>" if marks else "<Realm>"
    return f"<Name{name_mark}>-{realm_words}"


def summary(label: str, book: crewbook.CrewBook) -> list[str]:
    """Counts and key shapes for one book, under `label` (`flavor#1
    account#1`)."""
    players = book.players
    shapes = Counter(key_shape(p.key) for p in players)
    shapes.update(key_shape(u.key) for u in book.unread)
    lines = [
        f"{label}: state {book.state}, schema {book.schema_number}, "
        f"players {len(players)}, unread {len(book.unread)}, "
        f"rated good {sum(p.rating == 'good' for p in players)}, "
        f"rated bad {sum(p.rating == 'bad' for p in players)}, "
        f"noted {sum(bool(p.note) for p in players)}, "
        f"with a role {sum(p.role is not None for p in players)}, "
        f"with a guild {sum(p.guild is not None for p in players)}, "
        f"in an instance {sum(p.where is not None for p in players)}, "
        f"in the current group {book.current_count}, "
        f"skipped {book.skipped}, loads {book.probe_loads}, "
        f"started {'yes' if book.started is not None else 'no'}"
    ]
    lines += [f"{label}:   {n} x {shape}" for shape, n in sorted(shapes.items())]
    lines += [f"{label}:   unread because {u.reason}" for u in book.unread if u.reason]
    return lines


def _live_root() -> Path:
    value = os.environ.get(install.ENV_ROOT, "")
    if not value.strip():
        pytest.skip(f"set {install.ENV_ROOT} to the install to read its crew books")
    return Path(value)


@pytest.mark.live
def test_live_crew_books_read_and_print_only_counts_and_shapes() -> None:
    inst = install.discover(_live_root())
    lines = ["--- crew books, counts and key shapes only ---"]
    failed: list[str] = []
    for i, f in enumerate(inst.flavors, 1):
        lay = layout.Layout.for_flavor(f, inst.root)
        accounts = sorted({sv.account for sv in lay.saved_variables("account")})
        for j, account in enumerate(accounts, 1):
            label = f"flavor#{i} account#{j}"
            files = [
                sv
                for sv in lay.saved_variables("account")
                if sv.account == account
                and not sv.backup
                and labaddon.is_lab_file_name(sv.path.rsplit("/", 1)[-1])
            ]
            if not files:
                lines.append(f"{label}: no account WowLab.lua")
                continue
            try:
                book = crewbook.read_book(lay.flavor_path / files[0].path, root=inst.root)
            except Exception as exc:  # the class only: a message may quote the file
                failed.append(f"{label}: {type(exc).__name__}")
                lines.append(f"{label}: not read ({type(exc).__name__})")
                continue
            lines += summary(label, book)
    lines.append("--- end ---")
    print("\n" + "\n".join(lines))
    assert not failed, "books that could not be read: " + ", ".join(failed)


# ─── constructed ─────────────────────────────────────────────────────────────


def test_key_shape_says_the_shape_only_constructed() -> None:
    name, realm = ["Inventeda", "Fakerealm"]
    assert key_shape(f"{name}-{realm}") == "<Name>-<Realm>"
    assert key_shape(f"{name}-Fake {realm}") == "<Name>-<Realm with spaces>"
    assert key_shape(f"{name}-Fake'{realm}") == "<Name>-<Realm with an apostrophe>"
    assert key_shape(f"{name}-F{chr(0xE9)}{realm}") == "<Name>-<Realm with non-ASCII>"
    assert key_shape(f"{name}{chr(0xE9)}-{realm}") == "<Name non-ASCII>-<Realm>"
    assert key_shape(f"{name}-") == "<Name>-<empty realm>"
    assert key_shape(name) == "<0 hyphens>"


def test_the_summary_holds_no_key_or_text_constructed() -> None:
    text = "invented text"
    name, realm = ["Inventedb", "Fakerealm"]
    key = f"{name}-{realm}"
    book = crewbook.CrewBook(
        state="on",
        schema_number=1,
        players=[
            crewbook.CrewPlayer(key=key, guild=text, note=text, where=text, rating="bad"),
        ],
        unread=[crewbook.UnreadEntry(key=key + "x", reason="note is not text")],
        current_count=1,
    )
    lines = summary("flavor#1 account#1", book)
    joined = "\n".join(lines)
    assert key not in joined and name not in joined and realm not in joined
    assert text not in joined
    assert "players 1, unread 1, rated good 0, rated bad 1, noted 1" in joined
    assert "2 x <Name>-<Realm>" in joined
