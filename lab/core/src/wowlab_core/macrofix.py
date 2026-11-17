"""macrofix: the corrected bytes `wowlab macros fix` writes (M13-07).

Pure functions over one parsed `macros-cache.txt` (`wtfconfig.parse_macros`,
lossless, L4). Nothing here opens or writes a file: the command hands the
bytes to `guard` (L2, ADR-0021).

What is fixed is exactly what `macrocheck` marks `fixable`: a
`showtooltip-not-first` finding in a macro with one `#showtooltip` or `#show`
line, not the first, that ends with its `END`. The fix moves that line to
the top of the body; the lines above it move down one. Every other rule
needs the player's choice (which spell, which bracket, which duplicate) and
is left alone.

Bytes: each line's ending stays at its position (a body line's text moves,
its slot's ending does not), so the file keeps its length, every byte outside
the fixed macros' bodies is unchanged, every record stays where it was (the
client numbers macros by their position in the file, `docs/LAB_FORMATS.md`
§7, 2026-11-12), and a file with no fixable finding comes back byte for byte.
Whether the client keeps such an edit after the next login is assumed, not
confirmed (owner decision, 2026-11-17).
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict

from wowlab_core import macrocheck, wtfconfig

__all__ = ["FixPlan", "MacroFix", "plan"]


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class MacroFix(_Model):
    """One macro the fix changes, with its body before and after."""

    rule: macrocheck.Rule
    macro: macrocheck.MacroRef
    name: str
    line: int  # 1-based line in the file of the line that moves to the top
    before: list[str]  # the body lines as read
    after: list[str]  # the body lines as written


class FixPlan(_Model):
    fixes: list[MacroFix]
    # Findings of `macros check`'s file rules this file has that the fix does
    # not change (the spellbook and action-bar rules are not run here).
    left: list[macrocheck.Finding]
    data: bytes  # the file's bytes after the fixes; the input's when there are none


def _text(raw: bytes) -> str:
    return raw.decode("utf-8", "surrogateescape")  # as `wtfconfig` exposes text


def _moved(body: Sequence[wtfconfig.MacroBodyLine], k: int) -> list[wtfconfig.MacroBodyLine]:
    """`body` with line `k`'s text first: texts move, endings keep their place."""
    texts = [line.text for line in body]
    texts.insert(0, texts.pop(k))
    return [
        wtfconfig.MacroBodyLine(text=text, ending=line.ending)
        for text, line in zip(texts, body, strict=True)
    ]


def plan(doc: wtfconfig.MacrosDocument, *, scope: macrocheck.MacroScope, file: str) -> FixPlan:
    """What `macros fix` would write for `doc`, the file `file` (relative to
    the flavor folder) of `scope`."""
    sources = [macrocheck.SourceMacro(scope=scope, file=file, macro=m) for m in doc.macros]
    checked = macrocheck.check(sources, None)
    fixable = {f.macro.line: f for f in checked.findings if f.fixable}
    left = [f for f in checked.findings if not f.fixable]
    lines = list(doc.lines)
    fixes: list[MacroFix] = []
    for m in doc.macros:
        finding = fixable.get(m.header_index + 1)
        if finding is None:
            continue
        (k,) = macrocheck.first_line_directives(m)
        start = m.header_index + 1
        body = [
            line
            for line in lines[start : start + len(m.body_lines)]
            if isinstance(line, wtfconfig.MacroBodyLine)
        ]
        if len(body) != len(m.body_lines):
            raise AssertionError(f"macro {m.macro_id}: its body is not where it was read")
        moved = _moved(body, k)
        lines[start : start + len(moved)] = moved
        fixes.append(
            MacroFix(
                rule=finding.rule,
                macro=finding.macro,
                name=m.name,
                line=finding.line,
                before=list(m.body_lines),
                after=[_text(line.text) for line in moved],
            )
        )
    if not fixes:
        return FixPlan(fixes=[], left=left, data=doc.to_bytes())
    fixed = wtfconfig.MacrosDocument(lines=tuple(lines), bom=doc.bom)
    data = fixed.to_bytes()
    _check_fixed(doc, fixed, data, fixes)
    return FixPlan(fixes=fixes, left=left, data=data)


def _check_fixed(
    doc: wtfconfig.MacrosDocument,
    fixed: wtfconfig.MacrosDocument,
    data: bytes,
    fixes: Sequence[MacroFix],
) -> None:
    """What the fix promises, checked on the bytes it would write: the same
    records in the same places, the same length, only fixed bodies changed,
    and nothing left for the fix to do."""
    again = wtfconfig.parse_macros(data)
    if again.to_bytes() != data or len(data) != len(doc.to_bytes()):
        raise AssertionError("the fixed file does not read back as written")
    changed = {f.macro.line for f in fixes}
    for old, new in zip(doc.macros, again.macros, strict=True):
        same_place = (old.header_index, old.macro_id, old.name, old.icon) == (
            new.header_index,
            new.macro_id,
            new.name,
            new.icon,
        )
        if not same_place or sorted(old.body_lines) != sorted(new.body_lines):
            raise AssertionError(f"the fix moved or changed macro {old.macro_id}")
        if old.header_index + 1 not in changed and old.body_lines != new.body_lines:
            raise AssertionError(f"the fix changed macro {old.macro_id}, which it does not fix")
    for i, (a, b) in enumerate(zip(doc.lines, fixed.lines, strict=True)):
        if a.ending != b.ending or (
            a.text != b.text and not isinstance(a, wtfconfig.MacroBodyLine)
        ):
            raise AssertionError(f"the fix changed line {i + 1} outside a macro body")
    for m in again.macros:
        if m.header_index + 1 in changed and macrocheck.first_line_directives(m) != [0]:
            raise AssertionError(f"macro {m.macro_id} still has its first-line rule broken")
