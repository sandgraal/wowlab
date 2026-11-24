"""`wowlab char link set|list|forget` (M21-03).

Addresses the player pastes for one of their own characters, kept in a
small file in wowlab's own data folder (`linkstore`), never in the game
folder: a ForeverTwinking character page or build, and a Warcraft Logs
report. Their addresses cannot be built (the sites make their own numbers
and codes), so the player pastes each once; `char gear --json` and the
Character tab show them as links. Each address is checked against the
shapes in `link_sites.toml` (`links.check_pasted`) before it is kept.

`set` and `forget` show the plan and ask first unless `--yes` is given;
`--dry-run` shows the plan and changes nothing. Nothing in the game folder
changes, so there is no write gate, no snapshot and no `undo`: the file is
replaced whole, and `forget` (or `set` again) changes it back.
"""

from __future__ import annotations

from typing import Annotated

import typer

from wowlab_core import links, linkstore
from wowlab_core.cli._cmd_char import CharLinkRow, _link_row
from wowlab_core.cli._common import (
    EXIT_USAGE,
    AccountOpt,
    CharacterOpt,
    CliError,
    FlavorOpt,
    JsonOpt,
    RootOpt,
    YesOpt,
    _confirm,
    _emit,
    _handled,
    _home_words,
    _open,
    _open_char,
    _Out,
    _say,
    _say_prose,
    _select_account,
    _select_character,
    char_app,
)

link_app = typer.Typer(
    help="Addresses you paste for your own characters (a ForeverTwinking page or build, a "
    "Warcraft Logs report), kept in wowlab's own data folder and shown as links.",
    no_args_is_help=True,
)
char_app.add_typer(
    link_app,
    name="link",
    short_help="Addresses you paste for a character, shown as links.",
)


class CharLinkRefused(_Out):
    """A saved entry wowlab no longer shows (the file was edited, or the
    site's address shapes changed): whose, which, and why."""

    flavor_folder: str
    account: str
    character: str
    site: str
    kind: str
    why: str


class CharLinkListReport(_Out):
    """`wowlab char link list --json`: the saved addresses (of one
    character, with `--character`), the file they are kept in, and the
    entries left out."""

    file: str
    links: list[CharLinkRow]
    refused: list[CharLinkRefused]


class CharLinkChangeReport(_Out):
    """`wowlab char link set|forget --json`: whether this was a dry run,
    whether the file changes (`changed`), the plan in words (`plan_lines`),
    the character, the file, the address saved (`saved`, `set` only), the
    ones dropped (`dropped`: the one `set` replaces, or those `forget`
    drops), and the character's addresses after the change."""

    dry_run: bool
    changed: bool
    plan_lines: list[str]
    flavor_folder: str
    account: str
    character: str
    file: str
    saved: CharLinkRow | None
    dropped: list[CharLinkRow]
    links: list[CharLinkRow]


def _link_key(
    root: RootOpt, flavor: FlavorOpt, account: AccountOpt, character: str, *, reading: bool
) -> linkstore.CharacterKey:
    """The character `--character` names, as the `char` commands name it.
    A change never picks a flavor (`_open`); a read may (`_open_char`)."""
    _, chosen, lay = _open_char(root, flavor, account) if reading else _open(root, flavor)
    acct = _select_account(lay, account)
    char = _select_character(acct, character)
    return linkstore.CharacterKey(
        flavor=chosen.folder, account=acct.folder, character=f"{char.realm_folder}/{char.folder}"
    )


def _link_read() -> linkstore.Stored:
    try:
        return linkstore.read()
    except (linkstore.LinkStoreError, links.LinkSitesError) as exc:
        raise CliError(str(exc)) from None


def _link_write(file: linkstore.StoreFile) -> None:
    try:
        linkstore.write(file)
    except linkstore.LinkStoreError as exc:
        raise CliError(str(exc)) from None


def _link_who(key: linkstore.CharacterKey) -> str:
    return f"{key.character} in account {key.account}"


LinkDryRunOpt = Annotated[bool, typer.Option("--dry-run", help="Show the plan and change nothing.")]
LinkCharacterOpt = Annotated[
    str,
    typer.Option(
        "--character",
        help="Character folder, as <Character> or <Realm folder>/<Character> "
        "when the name alone is ambiguous.",
    ),
]


@link_app.command("set", short_help="Keep an address you paste for one of your characters.")
@_handled
def char_link_set(
    address: Annotated[
        str,
        typer.Argument(
            metavar="ADDRESS",
            help="The page's address as your browser shows it: a ForeverTwinking character "
            "page or build, or a Warcraft Logs report (its address, or its code alone).",
        ),
    ],
    character: LinkCharacterOpt,
    site: Annotated[
        str | None,
        typer.Option(
            "--site",
            help="forevertwinking or warcraftlogs. Default: the site the address is on.",
        ),
    ] = None,
    account: AccountOpt = None,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    dry_run: LinkDryRunOpt = False,
    yes: YesOpt = False,
    json_out: JsonOpt = False,
) -> None:
    """Keep the address of a page about one of your characters, to show as a
    link: a ForeverTwinking character page or build, or a Warcraft Logs
    report. These sites make their own numbers and codes, so wowlab cannot
    build their addresses; you paste each once. A character keeps one of
    each kind, and a new one replaces the old.

    The address is kept in a small file in wowlab's own data folder; nothing
    in the game folder changes. It opens in your browser only when you click
    it; wowlab itself never opens it. Shows the plan and asks first.

    \f
    JSON: CharLinkChangeReport."""
    try:
        pasted = links.check_pasted(address, site)
    except links.LinkError as exc:
        raise CliError(f"not kept: {exc}", EXIT_USAGE) from None
    except links.LinkSitesError as exc:
        raise CliError(str(exc)) from None
    key = _link_key(root, flavor, account, character, reading=False)
    stored = _link_read()
    try:
        after, new, old = linkstore.save(stored.file, key, pasted)
    except linkstore.LinkStoreError as exc:
        raise CliError(str(exc)) from None
    same = old is not None and old.url == new.url
    if same:
        plan = [f"{pasted.label} for {_link_who(key)} is already {new.url}; nothing would change."]
    elif old is not None:
        plan = [f"{pasted.label} for {_link_who(key)}: {old.url} -> {new.url}"]
    else:
        plan = [f"{pasted.label} for {_link_who(key)}: {new.url}"]
    dropped = [] if old is None or same else [_link_row(key, old)]

    def report(done: bool) -> CharLinkChangeReport:
        current = linkstore.links_for(after if done else stored.file, key)
        return CharLinkChangeReport(
            dry_run=dry_run,
            changed=not same,
            plan_lines=plan,
            flavor_folder=key.flavor,
            account=key.account,
            character=key.character,
            file=stored.path,
            saved=_link_row(key, new),
            dropped=dropped,
            links=[_link_row(key, s) for s in current],
        )

    if dry_run or same:
        if json_out:
            _emit(report(False))
            return
        for line in plan:
            _say_prose(line, keep=[key.character])
        if dry_run and not same:
            _say("Nothing was changed (a dry run).")
        return
    if not json_out:
        for line in plan:
            _say_prose(line, keep=[key.character])
    _confirm("Keep this address?", yes)
    _link_write(after)
    if json_out:
        _emit(report(True))
        return
    _say_prose(f"Kept in {_home_words(stored.path)}.", keep=[_home_words(stored.path)])


@link_app.command("list", short_help="The addresses you pasted for your characters.")
@_handled
def char_link_list(
    character: CharacterOpt = None,
    account: AccountOpt = None,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
) -> None:
    """The addresses you pasted for your characters, from wowlab's own data
    folder; with --character, one character's. A saved address that no
    longer checks out is listed apart and never shown as a link. Reads
    only; without --character the game folder is not read.

    \f
    JSON: CharLinkListReport."""
    key = (
        _link_key(root, flavor, account, character, reading=True) if character is not None else None
    )
    stored = _link_read()
    rows = [
        _link_row(entry.key, s)
        for entry in stored.file.characters
        if key is None or entry.key == key
        for s in entry.links
    ]
    refused = [
        CharLinkRefused(
            flavor_folder=r.key.flavor,
            account=r.key.account,
            character=r.key.character,
            site=r.site,
            kind=r.kind,
            why=r.why,
        )
        for r in stored.refused
        if key is None or r.key == key
    ]
    if json_out:
        _emit(CharLinkListReport(file=stored.path, links=rows, refused=refused))
        return
    if not rows:
        _say("No saved addresses." if key is None else f"No saved addresses for {_link_who(key)}.")
    for row in rows:
        _say_prose(
            f"{row.character} ({row.account}, {row.flavor_folder}): {row.label}: {row.url}",
            keep=[row.character],
        )
    for r in refused:
        _say_prose(f"Not shown: {r.character}, {r.site} {r.kind}: {r.why}", keep=[r.character])


@link_app.command("forget", short_help="Drop an address you pasted for a character.")
@_handled
def char_link_forget(
    character: LinkCharacterOpt,
    site: Annotated[
        str,
        typer.Option("--site", help="forevertwinking or warcraftlogs."),
    ],
    kind: Annotated[
        str | None,
        typer.Option(
            "--kind",
            help="profile, build or report. Default: every address of that site.",
        ),
    ] = None,
    account: AccountOpt = None,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    dry_run: LinkDryRunOpt = False,
    yes: YesOpt = False,
    json_out: JsonOpt = False,
) -> None:
    """Drop the address of a site you pasted for one of your characters (of
    one kind, with --kind). Only wowlab's own data folder changes. Shows the
    plan and asks first.

    \f
    JSON: CharLinkChangeReport."""
    key = _link_key(root, flavor, account, character, reading=False)
    stored = _link_read()
    after, dropped = linkstore.forget(stored.file, key, site, kind)
    what = f"{site} {kind}" if kind is not None else site
    plan = (
        [f"Drop {_link_row(key, s).label} for {_link_who(key)}: {s.url}" for s in dropped]
        if dropped
        else [f"No {what} address is saved for {_link_who(key)}; nothing would change."]
    )

    def report(done: bool) -> CharLinkChangeReport:
        current = linkstore.links_for(after if done else stored.file, key)
        return CharLinkChangeReport(
            dry_run=dry_run,
            changed=bool(dropped),
            plan_lines=plan,
            flavor_folder=key.flavor,
            account=key.account,
            character=key.character,
            file=stored.path,
            saved=None,
            dropped=[_link_row(key, s) for s in dropped],
            links=[_link_row(key, s) for s in current],
        )

    if dry_run or not dropped:
        if json_out:
            _emit(report(False))
            return
        for line in plan:
            _say_prose(line, keep=[key.character])
        if dry_run and dropped:
            _say("Nothing was changed (a dry run).")
        return
    if not json_out:
        for line in plan:
            _say_prose(line, keep=[key.character])
    _confirm("Drop it?" if len(dropped) == 1 else "Drop them?", yes)
    _link_write(after)
    if json_out:
        _emit(report(True))
        return
    _say("Dropped.")
