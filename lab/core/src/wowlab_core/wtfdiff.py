"""Changes by name between two versions of a WTF text file (M16-10,
docs/LAB_PLAN.md §18.1 item 3).

``snap diff`` and the plans of ``snap restore``, ``profile apply`` and
``undo`` say what a change to ``bindings-cache.wtf``, ``macros-cache.txt``,
``config-cache.wtf`` or ``Config.wtf`` does in the game's terms: which key
is bound to what before and after, which macro is added, removed or
changed (by name), and which CVar goes from which value to which.

Pure functions on bytes: the caller reads both sides (read-only, L1) and
this module parses them with the ``wtfconfig`` parsers (no Lua, L3). It
never decides what the client does with the result: a CVar that is not in
the file takes the client's default, a key with no line has its default
binding, and the server may replace these files at login when sync is on.
"""

from __future__ import annotations

from collections import Counter
from typing import Literal

from pydantic import BaseModel, ConfigDict

from wowlab_core import wtfconfig

__all__ = ["Kind", "NamedChange", "WtfDiff", "compare", "kind_of"]

Kind = Literal["bindings", "macros", "cvars"]

_KINDS: dict[str, Kind] = {
    "bindings-cache.wtf": "bindings",
    "macros-cache.txt": "macros",
    "config-cache.wtf": "cvars",
    "config.wtf": "cvars",
}


class NamedChange(BaseModel):
    """One key, macro or CVar that differs. For a key ``before``/``after`` are
    the bound command, for a CVar its value, for a macro its text (body lines
    joined by LF).

    ``None`` means the file has no line for it on that side. That is not
    "unbound" or "empty": a key with no line keeps the client's default
    binding, and a CVar with no line takes its value from another scope's
    file (machine, account or character) or the client's default. A macro
    with no record is absent from this file.

    ``parts`` names what changed in a macro present on both sides (``text``,
    ``icon``); it is empty for keys and CVars. For a macro, ``occurrence``
    counts the macros of the same name in file order from 1, and
    ``position`` is the record's place among the file's macros, from 1 (on
    the ``after`` side, or the ``before`` side when it was removed); both
    are 0 for keys and CVars."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    what: Literal["key", "macro", "cvar"]
    name: str
    before: str | None
    after: str | None
    parts: tuple[str, ...] = ()
    occurrence: int = 0
    position: int = 0


class WtfDiff(BaseModel):
    """What differs by name, and whether lines the parser does not type
    (``Unknown``) differ too. With no change and no such line, the two
    files differ only in order, line endings or record details the names
    do not carry (a macro's id or version).

    ``repeated_names`` is true when either side of a macros file holds two
    macros of one name: those are compared in file order, so an inserted or
    removed one shifts the rest."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Kind
    changes: tuple[NamedChange, ...]
    other_lines_differ: bool
    repeated_names: bool = False


def kind_of(path: str) -> Kind | None:
    """The kind of WTF text file ``path`` names, by its file name (any
    folder: the account's, a character's, or ``WTF/`` itself), else None."""
    return _KINDS.get(path.replace("\\", "/").rsplit("/", 1)[-1].casefold())


def _unknown(lines: tuple[tuple[int, bytes], ...]) -> Counter[bytes]:
    return Counter(text for _index, text in lines)


def _keyed(
    what: Literal["key", "cvar"], before: dict[str, str], after: dict[str, str]
) -> list[NamedChange]:
    out = [
        NamedChange(what=what, name=name, before=before.get(name), after=value)
        for name, value in after.items()
        if before.get(name) != value
    ]
    out.extend(
        NamedChange(what=what, name=name, before=value, after=None)
        for name, value in before.items()
        if name not in after
    )
    return out


def _bindings(data: bytes | None) -> tuple[dict[str, str], Counter[bytes]]:
    if data is None:
        return {}, Counter()
    doc = wtfconfig.parse_bindings(data)
    # A key bound on two lines: the last line is the one kept, as for CVars.
    # That the client applies the last line is believed, not observed
    # **[verify]** (as `wtfconfig.DuplicateCVar` says for CVars).
    return {b.key: b.action for b in doc.bindings}, _unknown(doc.unknown_lines())


def _cvars(data: bytes | None) -> tuple[dict[str, tuple[str, str]], Counter[bytes]]:
    """Folded name -> (name as spelled, value) of the last line setting it."""
    if data is None:
        return {}, Counter()
    doc = wtfconfig.parse_config(data)
    return (
        {folded: (c.name, c.value) for folded, c in doc.effective().items()},
        _unknown(doc.unknown_lines()),
    )


_Record = tuple[str, str, int]  # (text, icon, position in the file from 1)


def _macros(
    data: bytes | None,
) -> tuple[dict[tuple[str, int], _Record], Counter[bytes], bool]:
    """(name, n) -> (text, icon, position), where n counts macros of the same
    name in file order from 1, so two macros named alike are compared in
    turn; and whether a name repeats."""
    if data is None:
        return {}, Counter(), False
    doc = wtfconfig.parse_macros(data)
    seen: Counter[str] = Counter()
    out: dict[tuple[str, int], _Record] = {}
    for position, m in enumerate(doc.macros, start=1):
        seen[m.name] += 1
        out[(m.name, seen[m.name])] = ("\n".join(m.body_lines), m.icon, position)
    repeated = any(n > 1 for n in seen.values())
    return out, _unknown(doc.unknown_lines()), repeated


def _macro_changes(
    before: dict[tuple[str, int], _Record], after: dict[tuple[str, int], _Record]
) -> list[NamedChange]:
    def change(key: tuple[str, int], **fields: object) -> NamedChange:
        name, n = key
        return NamedChange.model_validate(
            {"what": "macro", "name": name, "occurrence": n, **fields}
        )

    out: list[NamedChange] = []
    for key, (text, icon, position) in after.items():
        old = before.get(key)
        if old is None:
            out.append(change(key, before=None, after=text, position=position))
            continue
        parts = tuple(
            part
            for part, differs in (("text", old[0] != text), ("icon", old[1] != icon))
            if differs
        )
        if parts:
            out.append(change(key, before=old[0], after=text, parts=parts, position=position))
    out.extend(
        change(key, before=text, after=None, position=position)
        for key, (text, _icon, position) in before.items()
        if key not in after
    )
    return out


def compare(kind: Kind, before: bytes | None, after: bytes | None) -> WtfDiff:
    """What differs by name from ``before`` to ``after`` (``None``: the file
    is absent on that side). Keys and CVars are listed in the order of the
    ``after`` file, then those only ``before`` has. A key or CVar set on
    two lines is compared by its last line (that the client applies the
    last line is believed, not observed **[verify]**). CVar names compare
    without ASCII case, as the client reads them, and are shown as the
    ``after`` side spells them."""
    changes: list[NamedChange]
    repeated = False
    if kind == "bindings":
        b_keys, b_other = _bindings(before)
        a_keys, a_other = _bindings(after)
        changes = _keyed("key", b_keys, a_keys)
    elif kind == "cvars":
        b_vars, b_other = _cvars(before)
        a_vars, a_other = _cvars(after)
        names = {f: n for f, (n, _v) in b_vars.items()} | {f: n for f, (n, _v) in a_vars.items()}
        changes = [
            c.model_copy(update={"name": names[c.name]})
            for c in _keyed(
                "cvar",
                {f: v for f, (_n, v) in b_vars.items()},
                {f: v for f, (_n, v) in a_vars.items()},
            )
        ]
    else:
        b_macros, b_other, b_repeated = _macros(before)
        a_macros, a_other, a_repeated = _macros(after)
        changes = _macro_changes(b_macros, a_macros)
        repeated = b_repeated or a_repeated
    return WtfDiff(
        kind=kind,
        changes=tuple(changes),
        other_lines_differ=b_other != a_other,
        repeated_names=repeated,
    )
