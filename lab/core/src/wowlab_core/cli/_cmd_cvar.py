"""`wowlab cvar list` and `cvar get`, and the scope option and notes that
`binds` and `macros` share (M16-111)."""

from typing import Annotated

import typer

from wowlab_core import (
    layout,
    wtfconfig,
)
from wowlab_core.cli._common import (
    AccountOpt,
    CharacterOpt,
    FlavorOpt,
    JsonOpt,
    RootOpt,
    Scope,
    _emit,
    _handled,
    _listed,
    _open,
    _Out,
    _say,
    _Target,
    _wtf_file,
    cvar_app,
)

# ─── cvar, binds, macros ─────────────────────────────────────────────────────


ScopeOpt = Annotated[
    Scope,
    typer.Option(
        "--scope",
        help="global: WTF/Config.wtf (machine-wide); account or character: that "
        "folder's config-cache.wtf.",
    ),
]

_WHAT_THE_FILE_SAYS = (
    "(what this file said at the client's last write; the value in effect may come from "
    "another scope, the client's default, or the server at login; a running client rewrites "
    "this file, so an edit made while it runs is lost)"
)
_ACCOUNT_BINDINGS = (
    "(account bindings; a character with character-specific key bindings on uses its own "
    "file instead [verify]. The server can replace this file at login when binding sync is "
    "on; see wowlab doctor)"
)
_NO_CHARACTER_BINDINGS = (
    "(a character has its own bindings-cache.wtf only when character-specific key bindings "
    "are on [verify]; without one it uses the account bindings)"
)
_MACRO_SYNC = "(the server can replace this file at login when macro sync is on; see wowlab doctor)"


class CVarItem(_Out):
    name: str
    value: str
    line: int  # 1-based


class CVarListReport(_Out):
    """`wowlab cvar list --json`."""

    file: str | None  # relative to the flavor folder; None when there is no such file
    scope: Scope
    account: str | None
    character: str | None
    cvars: list[CVarItem]  # every SET line, in file order, duplicates included
    duplicates: list[str]  # names set more than once (the last line is the file's value)
    unknown_lines: list[int]  # lines that are not SET lines, not shown
    notes: list[str]  # the caveats the text output prints


class CVarGetReport(_Out):
    """`wowlab cvar get --json`."""

    file: str | None
    scope: Scope
    account: str | None
    character: str | None
    name: str
    value: str | None  # None: not set in this file
    line: int | None
    times_set: int
    unknown_lines: int
    notes: list[str]  # the caveats the text output prints


def _read_config(
    lay: layout.Layout, scope: Scope, account: str | None, character: str | None
) -> tuple[_Target, str | None, wtfconfig.ConfigDocument | None]:
    name = "Config.wtf" if scope == "global" else "config-cache.wtf"
    target, found = _wtf_file(lay, name, scope, account, character)
    if found is None:
        return target, None, None
    return (
        target,
        found.path,
        _listed(wtfconfig.read_config, lay, found.path),
    )


def _no_file(what: str, target: _Target) -> str:
    who = {
        "global": "",
        "account": f" for account {target.account}",
        "character": f" for {target.character}",
    }[target.scope]
    return f"No {what}{who}."


@cvar_app.command("list", short_help="Every CVar set in one config file.")
@_handled
def cvar_list(
    scope: ScopeOpt = "global",
    account: AccountOpt = None,
    character: CharacterOpt = None,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
) -> None:
    """Every SET line of one config file.

    \f
    JSON: CVarListReport."""
    _, _, lay = _open(root, flavor)
    target, rel, doc = _read_config(lay, scope, account, character)
    cvars = (
        [CVarItem(name=c.name, value=c.value, line=c.index + 1) for c in doc.cvars] if doc else []
    )
    report = CVarListReport(
        file=rel,
        scope=target.scope,
        account=target.account,
        character=target.character,
        cvars=cvars,
        duplicates=[d.entries[0].name for d in doc.duplicates()] if doc else [],
        unknown_lines=[i + 1 for i, _ in doc.unknown_lines()] if doc else [],
        notes=[_WHAT_THE_FILE_SAYS],
    )
    if json_out:
        _emit(report)
        return
    if doc is None:
        _say(_no_file("config file", target))
        return
    _say(f"{rel}:")
    for c in cvars:
        _say(f'  {c.name} = "{c.value}"')
    for d in report.duplicates:
        _say(f"  {d} is set more than once; the last line is this file's value")
    if report.unknown_lines:
        _say(f"  {len(report.unknown_lines)} other line(s) are not SET lines and are not shown")
    _say(_WHAT_THE_FILE_SAYS)


@cvar_app.command("get", short_help="One CVar's value in one config file.")
@_handled
def cvar_get(
    name: Annotated[str, typer.Argument(help="CVar name; compared without case.")],
    scope: ScopeOpt = "global",
    account: AccountOpt = None,
    character: CharacterOpt = None,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
) -> None:
    """One CVar's value in one config file.

    \f
    JSON: CVarGetReport."""
    _, _, lay = _open(root, flavor)
    target, rel, doc = _read_config(lay, scope, account, character)
    found = doc.get(name) if doc else None
    times = (
        sum(1 for c in doc.cvars if wtfconfig.fold_name(c.name) == wtfconfig.fold_name(name))
        if doc
        else 0
    )
    report = CVarGetReport(
        file=rel,
        scope=target.scope,
        account=target.account,
        character=target.character,
        name=found.name if found else name,
        value=found.value if found else None,
        line=found.index + 1 if found else None,
        times_set=times,
        unknown_lines=len(doc.unknown_lines()) if doc else 0,
        notes=[_WHAT_THE_FILE_SAYS],
    )
    if json_out:
        _emit(report)
        return
    if doc is None:
        _say(_no_file("config file", target))
        return
    if found is None:
        _say(
            f"{name} is not set in {rel} (the client default applies, unless another scope sets it)"
        )
    else:
        more = f"; set {times} times, this is the last" if times > 1 else ""
        _say(f'{found.name} = "{found.value}"  ({rel}, line {found.index + 1}{more})')
    if report.unknown_lines:
        _say(f"{report.unknown_lines} line(s) of the file are not SET lines; kept, not interpreted")
    _say(_WHAT_THE_FILE_SAYS)
