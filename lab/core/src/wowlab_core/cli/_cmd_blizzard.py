"""`wowlab blizzard` (M22-11, ADR-0040 (2)): public profiles from Blizzard's API.

`login` keeps the player's own API client (made at develop.battle.net) in
wowlab's settings folder (the user config directory); the secret is asked for, hidden, never taken as an
option, so it is in no shell history and no process list. `logout`
forgets it. `status` says whether one is kept and how to add one. `fetch`
asks Blizzard for one character's profile, gear, portrait address and
guild, through `bnet` (stdlib `urllib`, https only) and only when it is
run; `show` prints what the last fetches saved and asks nothing. With no
API client kept, `fetch` asks nothing and says how to add one.

The game (where Blizzard keeps its characters) is read from the client's
version (`bnet_games.toml`), the region from `--region` or the flavor's
`portal` setting. Nothing from the install is sent: the region and the
character's names are the whole of what a request names.
"""

from pathlib import Path
from typing import Annotated

import typer

from wowlab_core import bnet
from wowlab_core.cli._common import (
    EXIT_ERROR,
    CliError,
    FlavorOpt,
    JsonOpt,
    RootOpt,
    _discover,
    _emit,
    _handled,
    _Out,
    _say,
    _say_prose,
    _select_flavor,
    blizzard_app,
)
from wowlab_core.region import Region
from wowlab_core.region import read as read_region

# ─── blizzard ────────────────────────────────────────────────────────────────

BnetNameArg = Annotated[
    str,
    typer.Argument(
        metavar="NAME",
        help="The character's first and second name, as 'First-Second' or 'First Second'.",
    ),
]
BnetRegionOpt = Annotated[
    str | None,
    typer.Option(
        "--region",
        help="us, eu, kr, tw or cn. Default: the region your game's settings name.",
    ),
]


class BlizzardStatusReport(_Out):
    """`wowlab blizzard status --json`: whether an API client is kept
    (`configured`), the file that holds it (`credentials_path`), and, when
    none is, how to add one (`setup`). The client ID and secret are never
    in it."""

    configured: bool
    credentials_path: str
    setup: str | None = None


class BlizzardLoginReport(_Out):
    """`wowlab blizzard login --json`: the file the client was saved to.
    The client ID and secret are never in it."""

    saved: bool
    credentials_path: str


class BlizzardLogoutReport(_Out):
    """`wowlab blizzard logout --json`: whether a kept client was removed."""

    removed: bool
    credentials_path: str


class BlizzardProfileReport(_Out):
    """`wowlab blizzard fetch|show --json`: one character's public profile
    as Blizzard's API gave it (`profile`, None when nothing was ever
    fetched for this name), each part with its own state and fetch time.
    `fetched` is whether this run asked Blizzard; `saved_fetches` how many
    fetches of this character are kept. `configured` is whether an API
    client is kept; when it is not, `setup` says how to add one."""

    configured: bool
    setup: str | None = None
    game: str
    region: Region
    name: str
    fetched: bool
    saved_fetches: int
    profile: bnet.BnetProfile | None


def _bnet_configured(*, strict: bool) -> tuple[bnet.Credentials | None, str | None]:
    """The kept API client, or None and how to add one. A kept file that
    cannot be used raises with `strict`; without it, its words are the
    `setup` line."""
    try:
        creds = bnet.read_credentials()
    except bnet.BnetError as exc:
        if strict:
            raise
        return None, str(exc)
    return creds, None if creds is not None else bnet.SETUP_WORDS


def _bnet_where(
    root: Path | None, flavor: str | None, region_opt: str | None
) -> tuple[bnet.Game, Region]:
    """The game of the flavor's client version, and the region: `--region`,
    else the flavor's `portal` setting."""
    inst, _ = _discover(root)
    f = _select_flavor(inst, flavor)
    game = bnet.game_for_version(f.version)
    if game is None:
        raise CliError(
            f"wowlab does not know where Blizzard keeps the characters of {f.folder} "
            f"(game version {f.version or 'not known'}); nothing was asked"
        )
    if region_opt is not None:
        return game, bnet.check_region(region_opt)
    reading = read_region(f.path, root=inst.root)
    if reading.region is None:
        raise CliError(
            f"wowlab does not know your region ({reading.absent}); give it with --region"
        )
    return game, reading.region


def _bnet_time(value: str | None) -> str:
    if not value:
        return "time not known"
    return value.replace("T", " ").replace("+00:00", " UTC")


def _bnet_state_words(state: bnet.PartState) -> str:
    when = f"fetched {_bnet_time(state.fetched_at)}"
    if state.from_earlier:
        when += " (an earlier fetch; the last one did not get it)"
    if state.status == "ok":
        return when
    return f"{state.detail or 'not fetched'}" + (
        f" ({_bnet_time(state.fetched_at)})" if state.fetched_at else ""
    )


def _bnet_profile_lines(report: BlizzardProfileReport) -> None:
    p = report.profile
    head = f"{report.name} ({report.region.upper()})"
    if p is None:
        if report.configured:
            _say_prose(f"{head}: nothing fetched from Blizzard yet; `wowlab blizzard fetch` asks.")
        else:
            _say_prose(f"{head}: nothing fetched from Blizzard yet.")
            _say_prose(report.setup or bnet.SETUP_WORDS)
        return
    _say(f"{head}, from Blizzard's armory, {_bnet_time(p.fetched_at)}")
    c = p.character
    if c is None:
        _say_prose(f"Profile: {_bnet_state_words(p.character_state)}", indent="  ")
    else:
        who = " ".join(x for x in (c.race, c.character_class) if x)
        spec = f" ({c.active_spec})" if c.active_spec else ""
        level = f"Level {c.level} " if c.level is not None else ""
        side = f", {c.faction}" if c.faction else ""
        rules = f"; ruleset: {c.ruleset}" if c.ruleset else ""
        _say(f"  {level}{who}{spec}{side}{rules}".rstrip())
        if c.average_item_level is not None or c.equipped_item_level is not None:
            avg = c.average_item_level if c.average_item_level is not None else "?"
            eq = c.equipped_item_level if c.equipped_item_level is not None else "?"
            _say(f"  Item level: {avg} average, {eq} equipped")
        _say(f"  Profile: {_bnet_state_words(p.character_state)}")
    g = p.guild
    if g is not None and g.name:
        members = f" ({g.member_count} members)" if g.member_count is not None else ""
        _say(f"  Guild: {g.name}{members}; {_bnet_state_words(p.guild_state)}")
    elif c is not None and c.guild:
        _say(f"  Guild: {c.guild}; {_bnet_state_words(p.guild_state)}")
    else:
        _say(f"  Guild: {_bnet_state_words(p.guild_state)}")
    if p.portrait:
        _say(f"  Portrait: {p.portrait[0].url}; {_bnet_state_words(p.portrait_state)}")
    else:
        _say(f"  Portrait: {_bnet_state_words(p.portrait_state)}")
    if p.equipment:
        _say(f"  Gear, {_bnet_state_words(p.equipment_state)}:")
        for item in p.equipment:
            facts = [f"item level {item.item_level}" if item.item_level is not None else ""]
            facts.append(item.quality or "")
            said = ", ".join(x for x in facts if x)
            _say(
                f"    {item.slot_name or item.slot or '?'}: {item.name or item.item_id or '?'}"
                + (f" ({said})" if said else "")
            )
    else:
        _say(f"  Gear: {_bnet_state_words(p.equipment_state)}")


@blizzard_app.command("status", short_help="Whether wowlab has a Blizzard API client.")
@_handled
def blizzard_status(json_out: JsonOpt = False) -> None:
    """Whether wowlab keeps a Blizzard API client, and where; when it does
    not, how to add one. Asks Blizzard nothing.

    \f
    JSON: BlizzardStatusReport."""
    creds, setup = _bnet_configured(strict=False)
    report = BlizzardStatusReport(
        configured=creds is not None,
        credentials_path=str(bnet.credentials_path()),
        setup=setup,
    )
    if json_out:
        _emit(report)
        return
    if creds is not None:
        _say_prose(
            f"A Blizzard API client is kept in {report.credentials_path}.",
            keep=[report.credentials_path],
        )
    else:
        _say_prose(bnet.SETUP_WORDS)


def _bnet_ask(prompt: str, *, hide: bool) -> str:
    try:
        value = typer.prompt(prompt, hide_input=hide, err=True)
    except typer.Abort:
        raise CliError(f"no {prompt.lower()} was given; nothing was saved") from None
    return str(value).strip()


@blizzard_app.command(
    "login", short_help="Keep your Blizzard API client's ID and secret (asked for, hidden)."
)
@_handled
def blizzard_login(
    client_id: Annotated[
        str | None,
        typer.Option(
            "--client-id",
            help="The client ID from develop.battle.net. Default: asked for.",
        ),
    ] = None,
    json_out: JsonOpt = False,
) -> None:
    """Keep the ID and secret of the API client you created at
    develop.battle.net, in wowlab's own settings folder, readable by you only,
    never in the game folder. The secret is asked for and not shown as you
    type it; it is never taken as an option, so it stays out of your shell
    history. It is never printed, logged or sent anywhere but to
    Blizzard's sign-in address when you fetch a profile. Asks Blizzard
    nothing.

    \f
    JSON: BlizzardLoginReport."""
    given = client_id if client_id is not None else _bnet_ask("Client ID", hide=False)
    secret = _bnet_ask("Client secret", hide=True)
    path = bnet.save_credentials(bnet.Credentials(given.strip(), secret))
    report = BlizzardLoginReport(saved=True, credentials_path=str(path))
    if json_out:
        _emit(report)
        return
    _say_prose(
        f"Kept your Blizzard API client in {path}. `wowlab blizzard fetch NAME` asks for a "
        "character's profile.",
        keep=[str(path)],
    )


@blizzard_app.command("logout", short_help="Forget the kept Blizzard API client.")
@_handled
def blizzard_logout(json_out: JsonOpt = False) -> None:
    """Remove the kept API client's ID and secret from wowlab's settings folder.
    Saved profiles stay. Asks Blizzard nothing.

    \f
    JSON: BlizzardLogoutReport."""
    removed = bnet.clear_credentials()
    report = BlizzardLogoutReport(removed=removed, credentials_path=str(bnet.credentials_path()))
    if json_out:
        _emit(report)
        return
    _say("Removed the kept Blizzard API client." if removed else "No Blizzard API client is kept.")


@blizzard_app.command(
    "fetch", short_help="Ask Blizzard for one character's profile, gear, guild and portrait."
)
@_handled
def blizzard_fetch(
    name: BnetNameArg,
    region: BnetRegionOpt = None,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
) -> None:
    """Ask Blizzard's API, with your own API client, for one character's
    public profile (level, race, class, item level), gear, guild and
    portrait address, save the answers in wowlab's data folder and print
    them, each part with the time it was fetched. A request names your
    region, the game and the character's names; the client ID and secret go
    only to Blizzard's sign-in address; nothing else from your game folder
    is sent. A character below level 10 has no profile, and a
    portrait often appears hours after the character does: an earlier
    fetch's is shown then. Without an API client, nothing is asked and
    wowlab says how to add one.

    \f
    JSON: BlizzardProfileReport."""
    game, where = _bnet_where(root, flavor, region)
    who = bnet.parse_name(name)
    bnet.character_path(game, who)  # a name the game's address cannot take is refused first
    creds, setup = _bnet_configured(strict=True)
    if creds is None:
        raise CliError(bnet.SETUP_WORDS)
    got, raw = bnet.fetch_profile(creds, game, where, who)
    bnet.save_profile(got, raw)
    profile, kept = bnet.load_profile(game.label, where, who)
    report = BlizzardProfileReport(
        configured=True,
        setup=setup,
        game=game.label,
        region=where,
        name=who.game_name,
        fetched=True,
        saved_fetches=kept,
        profile=profile or got,
    )
    if json_out:
        _emit(report)
    else:
        _bnet_profile_lines(report)
    if got.character_state.status != "ok":
        raise typer.Exit(EXIT_ERROR)


@blizzard_app.command(
    "show", short_help="What the last fetches from Blizzard saved for one character."
)
@_handled
def blizzard_show(
    name: BnetNameArg,
    region: BnetRegionOpt = None,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
) -> None:
    """What the last fetches from Blizzard saved for one character, each
    part with the time it was fetched, read from wowlab's data folder.
    Asks Blizzard nothing; `wowlab blizzard fetch` does.

    \f
    JSON: BlizzardProfileReport."""
    game, where = _bnet_where(root, flavor, region)
    who = bnet.parse_name(name)
    creds, setup = _bnet_configured(strict=False)
    profile, kept = bnet.load_profile(game.label, where, who)
    report = BlizzardProfileReport(
        configured=creds is not None,
        setup=setup,
        game=game.label,
        region=where,
        name=who.game_name,
        fetched=False,
        saved_fetches=kept,
        profile=profile,
    )
    if json_out:
        _emit(report)
    else:
        _bnet_profile_lines(report)
