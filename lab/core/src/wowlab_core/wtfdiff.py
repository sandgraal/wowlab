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
    """One key, macro or CVar that differs. ``before``/``after`` are ``None``
    when the file has no such key, macro or CVar on that side. For a key they
    are the bound command, for a CVar its value, for a macro its text (body
    lines joined by LF). ``parts`` names what changed in a macro present on
    both sides (``text``, ``icon``); it is empty for keys and CVars."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    what: Literal["key", "macro", "cvar"]
    name: str
    before: str | None
    after: str | None
    parts: tuple[str, ...] = ()


class WtfDiff(BaseModel):
    """What differs by name, and whether lines the parser does not type
    (``Unknown``) differ too. With no change and no such line, the two
    files differ only in order, line endings or record details the names
    do not carry (a macro's id or version)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Kind
    changes: tuple[NamedChange, ...]
    other_lines_differ: bool


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


_Record = tuple[str, str]  # (text, icon)


def _macros(data: bytes | None) -> tuple[dict[tuple[str, int], _Record], Counter[bytes]]:
    """(name, n) -> (text, icon), where n counts macros of the same name in
    file order from 1, so two macros named alike are compared in turn."""
    if data is None:
        return {}, Counter()
    doc = wtfconfig.parse_macros(data)
    seen: Counter[str] = Counter()
    out: dict[tuple[str, int], _Record] = {}
    for m in doc.macros:
        seen[m.name] += 1
        out[(m.name, seen[m.name])] = ("\n".join(m.body_lines), m.icon)
    return out, _unknown(doc.unknown_lines())


def _macro_changes(
    before: dict[tuple[str, int], _Record], after: dict[tuple[str, int], _Record]
) -> list[NamedChange]:
    def label(key: tuple[str, int]) -> str:
        name, n = key
        return name if n == 1 else f"{name} ({n})"

    out: list[NamedChange] = []
    for key, (text, icon) in after.items():
        old = before.get(key)
        if old is None:
            out.append(NamedChange(what="macro", name=label(key), before=None, after=text))
            continue
        parts = tuple(
            part
            for part, differs in (("text", old[0] != text), ("icon", old[1] != icon))
            if differs
        )
        if parts:
            out.append(
                NamedChange(what="macro", name=label(key), before=old[0], after=text, parts=parts)
            )
    out.extend(
        NamedChange(what="macro", name=label(key), before=text, after=None)
        for key, (text, _icon) in before.items()
        if key not in after
    )
    return out


def compare(kind: Kind, before: bytes | None, after: bytes | None) -> WtfDiff:
    """What differs by name from ``before`` to ``after`` (``None``: the file
    is absent on that side). Keys and CVars are listed in the order of the
    ``after`` file, then those only ``before`` has; CVar names compare
    without ASCII case, as the client reads them, and are shown as the
    ``after`` side spells them."""
    changes: list[NamedChange]
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
        b_macros, b_other = _macros(before)
        a_macros, a_other = _macros(after)
        changes = _macro_changes(b_macros, a_macros)
    return WtfDiff(kind=kind, changes=tuple(changes), other_lines_differ=b_other != a_other)
