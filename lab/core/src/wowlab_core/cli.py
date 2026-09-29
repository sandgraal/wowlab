"""The `wowlab` command (docs/LAB_PLAN.md §6.11, M10-14).

A thin shell over the library. Every command reads through the modules the
spec names and prints text, or JSON with `--json`; the only commands that
change an install are `snap restore`, `profile apply` (§13.3, M11-08),
`addon install|remove lab` (§13.1, M11-02), `sv merge` (§13.4, M11-09) and `undo`, and all go
through `wowlab_core.guard` (L2, ADR-0021): they print
the plan and ask before writing unless `--yes` is given. Nothing here writes a file itself; the
snapshot store, the game-data cache and saved looks are written by `snapshot`,
`gamedata` and `lookstore`, under the user data directory (L1).

Exit codes: 0 ok, 1 error, 2 usage (including "more than one flavor, pick
one with --flavor"), 3 refused by the write gate (any `guard.GuardError`:
client running or unknown, a path outside the allowlist, the store busy, a
file changed since the pre-write snapshot, a snapshot of another install).
`sv merge` also exits 1 when conflicts are left unresolved (its report is
printed, nothing is written), 2 for a character-to-character merge of an
account-wide file, and 3 when its SavedVariables loader check refuses.

Where the install is: `--root`, else `$WOWLAB_WOW_ROOT`, else the platform
defaults (`install.discover`). Which flavor: `--flavor`, implied when the
install has exactly one. Nothing about a flavor is known here (L6): folder
names, products and versions come from discovery.

JSON: each data command's output validates against the Pydantic model named
in its help (the models are defined below, or are the library's own). Text
that came from file names or file bytes and is not valid UTF-8 is written in
JSON with the scheme `layout` and `snapshot` use (a lone surrogate becomes
NUL followed by four hex digits); snapshot manifests are written with
`snapshot.manifest_bytes`, the one canonical encoding. `sv dump --json` is
flat (each value one row naming its parent) and written compact, row by
row, so its size grows with the number of values and never with depth.
Text output escapes every control character but tab (`\\x1b` for ESC), so
nothing read from an install reaches the terminal as a control sequence.

A whole-snapshot `snap restore` leaves alone the files the client manages
(file-map Edit `no`) unless they are named with `--paths` (owner decision
2026-09-27, §6.11 amendment).

`log tail` (§6.11, M10-13) prints the last lines of the newest combat log
under the flavor's `Logs/`, tokenized by `combatlog`, and with `--follow`
keeps printing as the client appends, across truncation and a new log file.
Its `--json` is one `LogTailReport`, or with `--follow` one `LogTailLine` per
line of output (JSON Lines), since a stream that never ends is not one
document. Timestamps are printed as the log has them; they are local times
the client wrote, and in the committed fixtures shifted, never parsed here.

`looks` (§13.2, M11-06) reads the customization tables of one build through
`gamedata` and checks looks with `wowlab_core.looks`; saved looks are JSON
files `lookstore` writes under the user data directory, never in an install.
The build is `--build` or the flavor's version; only when no install is found
does it fall back to a saved look's build or the one fully cached build.
`looks page` (M11-07, ADR-0027) writes all of that into one self-contained
HTML file (`lookspage`), under the user data directory or at `--out`, never in
an install: every verdict on the page is computed here and only shown there.

`char show` (§13.1, M11-04) reads one character's `WowLab.lua`, written by
the lab-addon, through `wowlab_core.labaddon`, and the account's
`WowLab.lua` beside it; read only. Without `--character` it takes the
character whose `WowLab.lua` has the newest modification time.
"""

import base64
import contextlib
import csv
import functools
import hashlib
import io
import json
import os
import re
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Any, Literal, NoReturn

import typer
from pydantic import BaseModel, ConfigDict, Field, RootModel

from wowlab_core import (
    __version__,
    addoninstall,
    combatlog,
    gamedata,
    guard,
    install,
    labaddon,
    layout,
    looks,
    lookspage,
    lookstore,
    luadata,
    process,
    profiles,
    snapshot,
    svmerge,
    wtfconfig,
)
from wowlab_core.filemap import FileMapEntry

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_REFUSED = 3

app = typer.Typer(
    name="wowlab",
    help="Local-only toolchain over a World of Warcraft install.",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode=None,
)
install_app = typer.Typer(help="The install and its flavors.", no_args_is_help=True)
sv_app = typer.Typer(
    help="SavedVariables files: list and dump read them; merge writes one through the write gate.",
    no_args_is_help=True,
)
cvar_app = typer.Typer(help="CVars in Config.wtf and config-cache.wtf.", no_args_is_help=True)
binds_app = typer.Typer(help="Key bindings in bindings-cache.wtf.", no_args_is_help=True)
macros_app = typer.Typer(help="Macros in macros-cache.txt.", no_args_is_help=True)
addons_app = typer.Typer(help="Folders under Interface/AddOns/.", no_args_is_help=True)
addon_app = typer.Typer(
    help="Install or remove the lab-addon (Interface/AddOns/WowLab/) through the write gate. "
    "Its SavedVariables (WowLab.lua) are never touched.",
    no_args_is_help=True,
)
db2_app = typer.Typer(
    help="Game data tables from wago.tools, cached by build.", no_args_is_help=True
)
snap_app = typer.Typer(help="The snapshot store.", no_args_is_help=True)
log_app = typer.Typer(help="The combat log (read only).", no_args_is_help=True)
looks_app = typer.Typer(
    help="Character customization looks: races, options and choices from a build's tables, "
    "and looks saved as JSON under the user data directory. Data only: nothing here writes "
    "into an install or reaches the game.",
    no_args_is_help=True,
)
profile_app = typer.Typer(
    help="Named sets of the client's local UI files, saved into the snapshot store and "
    "applied through the write gate (`wowlab undo` reverses an apply). A profile covers "
    "every account and every character folder that existed when it was saved, not only "
    "the character you play; applying it deletes files created in those places since. "
    "The lab-addon is never in a profile. " + profiles.SERVER_SIDE_NOTE,
    no_args_is_help=True,
)
app.add_typer(install_app, name="install")
app.add_typer(sv_app, name="sv")
app.add_typer(cvar_app, name="cvar")
app.add_typer(binds_app, name="binds")
app.add_typer(macros_app, name="macros")
app.add_typer(addons_app, name="addons")
app.add_typer(addon_app, name="addon")
app.add_typer(db2_app, name="db2")
app.add_typer(snap_app, name="snap")
app.add_typer(log_app, name="log")
app.add_typer(profile_app, name="profile")
app.add_typer(looks_app, name="looks")
char_app = typer.Typer(
    help="Characters as the lab-addon recorded them in WowLab.lua (read only).",
    no_args_is_help=True,
)
app.add_typer(char_app, name="char")


# ─── options ─────────────────────────────────────────────────────────────────

RootOpt = Annotated[
    Path | None,
    typer.Option(
        "--root",
        help="The install folder, the one that holds .build.info. Default: "
        "$WOWLAB_WOW_ROOT, then the usual install locations.",
    ),
]
FlavorOpt = Annotated[
    str | None,
    typer.Option(
        "--flavor",
        help="The flavor folder to use, as `wowlab install show` lists it. "
        "Implied when the install has only one.",
    ),
]
JsonOpt = Annotated[bool, typer.Option("--json", help="Print JSON instead of text.")]
YesOpt = Annotated[bool, typer.Option("--yes", "-y", help="Do not ask before changing files.")]
AccountOpt = Annotated[
    str | None,
    typer.Option("--account", help="Account folder under WTF/Account/. Implied when there is one."),
]
CharacterOpt = Annotated[
    str | None,
    typer.Option(
        "--character",
        help="Character folder, as <Character> or <Realm folder>/<Character> "
        "when the name alone is ambiguous.",
    ),
]


# ─── output ──────────────────────────────────────────────────────────────────


class CliError(Exception):
    """An error the command reports as one line, with its exit code."""

    def __init__(self, message: str, code: int = EXIT_ERROR) -> None:
        super().__init__(message)
        self.code = code


_CONTROL = re.compile("[\x00-\x08\x0a-\x1f\x7f-\x9f]")


def _safe(text: str) -> str:
    """Printable text for a terminal.

    Bytes that are not UTF-8 (carried as lone surrogates) are shown as `\\xNN`
    escapes instead of failing the write, and so is every C0 and C1 control
    character but tab, line feed included: a file name or value from the
    install can carry ESC or OSC sequences, which must never reach the
    terminal as control codes."""
    try:
        text = text.encode("utf-8", "surrogateescape").decode("utf-8", "backslashreplace")
    except UnicodeEncodeError:
        text = text.encode("utf-8", "backslashreplace").decode("utf-8")
    return _CONTROL.sub(lambda m: f"\\x{ord(m.group()):02x}", text)


def _say(text: str = "") -> None:
    typer.echo(_safe(text))


def _note(text: str) -> None:
    """A message on stderr. Its own line breaks (a multi-line error from the
    library) are kept; every other control character is escaped."""
    typer.echo("\n".join(_safe(line) for line in text.split("\n")), err=True)


def _fail(message: str, code: int) -> NoReturn:
    _note(f"wowlab: {message}")
    raise typer.Exit(code)


_SURROGATE = re.compile("[\ud800-\udfff]")


def _json_text(value: str) -> str:
    """A lone surrogate as NUL and four hex digits, as `layout` and `snapshot` do."""
    return _SURROGATE.sub(lambda m: f"\x00{ord(m.group()):04X}", value)


def _map_strings(value: Any, fn: Callable[[str], str]) -> Any:
    if isinstance(value, str):
        return fn(value)
    if isinstance(value, list | tuple):
        return [_map_strings(v, fn) for v in value]
    if isinstance(value, dict):
        return {fn(k) if isinstance(k, str) else k: _map_strings(v, fn) for k, v in value.items()}
    return value


def _dumps(payload: Any) -> str:
    return json.dumps(_map_strings(payload, _json_text), indent=2, ensure_ascii=True)


def _emit(model: BaseModel) -> None:
    """The model as JSON on stdout; `type(model).model_validate_json` reads it back."""
    typer.echo(_dumps(model.model_dump(mode="json")))


def _emit_manifest(manifest: snapshot.Manifest) -> None:
    """A manifest in its one canonical encoding (`snapshot.manifest_bytes`)."""
    typer.echo(snapshot.manifest_bytes(manifest).decode("ascii"), nl=False)


def _bytes(n: int) -> str:
    return f"{n:,} byte" + ("" if n == 1 else "s")


def _confirm(question: str, yes: bool) -> None:
    if yes:
        return
    try:
        agreed = typer.confirm(question, default=False, err=True)
    except typer.Abort:  # end of input: no answer is not a yes
        agreed = False
        _note("")
    if not agreed:
        _note("Nothing was changed.")
        raise typer.Exit(EXIT_ERROR)


def _handled[**P, R](fn: Callable[P, R]) -> Callable[P, R]:
    """Turn the library's typed errors into one line on stderr and an exit code."""

    @functools.wraps(fn)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        try:
            return fn(*args, **kwargs)
        except guard.GuardError as exc:
            _fail(f"refused by the write gate: {exc}", EXIT_REFUSED)
        except CliError as exc:
            _fail(str(exc), exc.code)
        except luadata.LuaLimitError as exc:
            _fail(
                f"refused: this file is beyond what the SavedVariables parser will hold "
                f"({exc.message}); nothing was printed",
                EXIT_ERROR,
            )
        except luadata.LuaDataError as exc:
            _fail(f"not SavedVariables data the parser accepts: {exc}", EXIT_ERROR)
        except (
            install.InstallError,
            snapshot.SnapshotError,
            profiles.ProfileError,
            addoninstall.AddonError,
            labaddon.LabAddonError,
            gamedata.GameDataError,
            lookstore.LookStoreError,
            OSError,
        ) as exc:
            _fail(str(exc), EXIT_ERROR)

    return wrapper


class _Out(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


# ─── install, flavor, account, character ────────────────────────────────────


def _found_by(root: Path | None) -> str:
    if root is not None and str(root) != "":
        return "--root"
    if os.environ.get(install.ENV_ROOT, "").strip():
        return f"${install.ENV_ROOT}"
    return "a default location"


def _discover(root: Path | None) -> tuple[install.Install, str]:
    return install.discover(root), _found_by(root)


def _flavor_list(inst: install.Install) -> str:
    return ", ".join(f.folder for f in inst.flavors) or "none"


def _select_flavor(inst: install.Install, name: str | None) -> install.Flavor:
    flavors = inst.flavors
    if name is not None:
        exact = [f for f in flavors if f.folder == name]
        folded = [f for f in flavors if f.folder.casefold() == name.casefold()]
        match = exact or folded
        if len(match) == 1:
            return match[0]
        raise CliError(
            f"no flavor folder {name!r} in {inst.root} (flavors found: {_flavor_list(inst)})",
            EXIT_USAGE,
        )
    if len(flavors) == 1:
        return flavors[0]
    if not flavors:
        raise CliError(
            f"{inst.root} has no flavor folders (a folder named _..._ holding "
            f"{install.FLAVOR_INFO})"
        )
    raise CliError(
        f"{inst.root} has {len(flavors)} flavors ({_flavor_list(inst)}); choose one with --flavor",
        EXIT_USAGE,
    )


def _open(
    root: Path | None, flavor: str | None
) -> tuple[install.Install, install.Flavor, layout.Layout]:
    inst, _ = _discover(root)
    chosen = _select_flavor(inst, flavor)
    return inst, chosen, layout.Layout.for_flavor(chosen, inst.root)


def _select_account(lay: layout.Layout, name: str | None) -> layout.Account:
    accounts = lay.accounts()
    names = ", ".join(a.folder for a in accounts) or "none"
    if name is not None:
        match = [a for a in accounts if a.folder == name] or [
            a for a in accounts if a.folder.casefold() == name.casefold()
        ]
        if len(match) == 1:
            return match[0]
        raise CliError(f"no account folder {name!r} (accounts: {names})", EXIT_USAGE)
    if len(accounts) == 1:
        return accounts[0]
    if not accounts:
        raise CliError(f"no account folders under WTF/Account/ in {lay.flavor_path}")
    raise CliError(f"{len(accounts)} accounts ({names}); choose one with --account", EXIT_USAGE)


def _select_character(account: layout.Account, spec: str) -> layout.Character:
    characters = [c for r in account.realms for c in r.characters]
    if "/" in spec:
        realm, _, folder = spec.rpartition("/")
        match = [c for c in characters if c.realm_folder == realm and c.folder == folder]
    else:
        match = [c for c in characters if c.folder == spec] or [
            c for c in characters if c.folder.casefold() == spec.casefold()
        ]
    if len(match) == 1:
        return match[0]
    listing = ", ".join(f"{c.realm_folder}/{c.folder}" for c in characters) or "none"
    if not match:
        raise CliError(
            f"no character folder {spec!r} in account {account.folder} (characters: {listing})",
            EXIT_USAGE,
        )
    raise CliError(
        f"{spec!r} names {len(match)} character folders; use <Realm folder>/<Character> "
        f"(characters: {listing})",
        EXIT_USAGE,
    )


Scope = Literal["global", "account", "character"]


class _Target(BaseModel):
    """Which WTF file a cvar/binds/macros command reads."""

    scope: Scope
    account: str | None
    character: str | None  # "<realm folder>/<character folder>"


def _wtf_file(
    lay: layout.Layout,
    file_name: str,
    scope: Scope,
    account: str | None,
    character: str | None,
) -> tuple[_Target, layout.WtfFile | None]:
    """The WTF file of `file_name` for the scope, or None when it is absent."""
    files = lay.wtf_files()
    folded = file_name.casefold()
    if scope == "global":
        target = _Target(scope=scope, account=None, character=None)
        found = [f for f in files if f.scope == "machine" and f.name.casefold() == folded]
        return target, (found[0] if found else None)
    acct = _select_account(lay, account)
    if scope == "account":
        target = _Target(scope=scope, account=acct.folder, character=None)
        found = [
            f
            for f in files
            if f.scope == "account" and f.account == acct.folder and f.name.casefold() == folded
        ]
        return target, (found[0] if found else None)
    if character is None:
        raise CliError("--scope character needs --character", EXIT_USAGE)
    char = _select_character(acct, character)
    target = _Target(
        scope=scope, account=acct.folder, character=f"{char.realm_folder}/{char.folder}"
    )
    found = [
        f
        for f in files
        if f.scope == "character"
        and f.account == acct.folder
        and f.realm_folder == char.realm_folder
        and f.character_folder == char.folder
        and f.name.casefold() == folded
    ]
    return target, (found[0] if found else None)


def _client_names(flavor_dir: Path) -> list[str]:
    """Executable names in a flavor folder, for `process` (`extra_names`).

    Lists the folder (and a macOS bundle's `Contents/MacOS/`); opens nothing.
    The same rule as `guard`'s own (private) helper, which it applies before
    a write; a test pins the two together. A flavor folder that cannot be
    listed raises `OSError`, so a caller reports "could not check" rather
    than checking with no names.
    """
    names: list[str] = []
    for child in sorted(flavor_dir.iterdir()):
        folded = child.name.casefold()
        if folded.endswith(".exe"):
            names.append(child.name)
        elif folded.endswith(".app"):
            names.append(child.name[: -len(".app")])
            with contextlib.suppress(OSError):
                names.extend(p.name for p in (child / "Contents" / "MacOS").iterdir())
    return names


def _roots(inst: install.Install) -> list[Path]:
    roots = [Path(inst.root)]
    try:
        resolved = Path(inst.root).resolve()
    except (OSError, RuntimeError):
        return roots
    if resolved != roots[0]:
        roots.append(resolved)
    return roots


# ─── doctor ──────────────────────────────────────────────────────────────────


class UncheckedLocation(_Out):
    location: str
    reason: str


class Locations(_Out):
    """Where discovery looked when no install was named."""

    searched: list[str]
    could_not_check: list[UncheckedLocation]


class ClientReport(_Out):
    # `running` and `unknown` are both treated as running by the write gate.
    state: Literal["running", "unknown", "not_running", "could_not_check"]
    processes: list[process.ClientProcess]
    error: str | None


class GameDataReport(_Out):
    # `published`: the source lists this flavor's version; only `version` is
    # stated, since a match may come from another product's listing.
    status: Literal["published", "not_published", "no_version", "not_checked", "error"]
    version: str | None
    detail: str | None


class SyncCVar(_Out):
    file: str  # relative to the flavor folder
    account: str | None
    name: str
    value: str | None  # None: not set in this file (the client default applies)


class DoctorFlavor(_Out):
    folder: str
    product: str
    version: str | None
    build: int | None
    matching_rows: int
    game_data: GameDataReport
    sync_cvars: list[SyncCVar]


class LastTransaction(_Out):
    id: str
    created_at: str
    label: str
    state: str
    paths: int


class StoreReport(_Out):
    path: str
    exists: bool
    snapshots: int
    damaged: list[snapshot.InvalidManifest]
    transactions: int | None
    last_transaction: LastTransaction | None
    journal_error: str | None


class DoctorReport(_Out):
    """`wowlab doctor --json`."""

    install_root: str | None
    found_by: str | None
    install_error: str | None
    locations: Locations | None
    flavors: list[DoctorFlavor]
    other_dirs: list[str]
    client: ClientReport | None
    store: StoreReport
    problems: list[str]


# The CVars that decide whether the server may replace local binds, macros
# and settings at login (docs/LAB_FILE_MAP.md, timing rules).
_SYNC_CVARS = ("synchronizeBindings", "synchronizeMacros", "synchronizeConfig")


@contextmanager
def _open_gamedata() -> Iterator[gamedata.GameData]:
    """The game-data client (the only network client, ADR-0022). Tests replace
    this with one that replays recordings."""
    source = gamedata.WagoSource()
    try:
        yield gamedata.GameData(source)
    finally:
        source.close()


def _sync_cvars(lay: layout.Layout) -> list[SyncCVar]:
    out: list[SyncCVar] = []
    for f in lay.wtf_files():
        name = f.name.casefold()
        is_machine = f.scope == "machine" and name == "config.wtf"
        is_account = f.scope == "account" and name == "config-cache.wtf"
        if not (is_machine or is_account):
            continue
        doc = wtfconfig.read_config(lay.flavor_path / f.path)
        for cvar in _SYNC_CVARS:
            found = doc.get(cvar)
            out.append(
                SyncCVar(
                    file=f.path,
                    account=f.account,
                    name=found.name if found else cvar,
                    value=found.value if found else None,
                )
            )
    return out


def _game_data(flavors: Sequence[install.Flavor], offline: bool) -> dict[str, GameDataReport]:
    reports: dict[str, GameDataReport] = {}
    pending: list[install.Flavor] = []
    for f in flavors:
        if f.version is None:
            reports[f.folder] = GameDataReport(
                status="no_version",
                version=None,
                detail=f".build.info has no version for product {f.product!r}",
            )
        elif offline:
            reports[f.folder] = GameDataReport(
                status="not_checked", version=f.version, detail="--offline"
            )
        else:
            pending.append(f)
    if not pending:
        return reports
    try:
        with _open_gamedata() as data:
            for f in pending:
                try:
                    build = data.resolve_build(f)
                except gamedata.BuildNotPublished:
                    reports[f.folder] = GameDataReport(
                        status="not_published", version=f.version, detail=None
                    )
                else:
                    # Only `.version` is a fact about the install: a match may
                    # come from another product's listing (§6.6 amendment).
                    reports[f.folder] = GameDataReport(
                        status="published", version=build.version, detail=None
                    )
    except (gamedata.GameDataError, OSError) as exc:
        for f in pending:
            reports.setdefault(
                f.folder, GameDataReport(status="error", version=f.version, detail=str(exc))
            )
    return reports


def _client_report(inst: install.Install) -> ClientReport:
    try:
        names = sorted({n for f in inst.flavors for n in _client_names(f.path)})
        clients = process.running_clients(
            _roots(inst),
            flavor_folders=[f.folder for f in inst.flavors],
            extra_names=names,
        )
    except Exception as exc:  # the write gate treats any probe failure as running
        return ClientReport(
            state="could_not_check", processes=[], error=f"{type(exc).__name__}: {exc}"
        )
    state: Literal["running", "unknown", "not_running"] = "not_running"
    if any(c.state is process.ClientState.RUNNING for c in clients):
        state = "running"
    elif clients:
        state = "unknown"
    return ClientReport(state=state, processes=list(clients), error=None)


def _store_report() -> StoreReport:
    store = snapshot.SnapshotStore()
    listing = store.list_lenient()
    transactions: int | None = None
    last: LastTransaction | None = None
    journal_error: str | None = None
    try:
        records = guard.history()
    except guard.GuardError as exc:
        journal_error = str(exc)
    else:
        transactions = len(records)
        if records:
            r = records[-1]
            last = LastTransaction(
                id=r.id, created_at=r.created_at, label=r.label, state=r.state, paths=len(r.paths)
            )
    return StoreReport(
        path=str(store.path),
        exists=store.path.is_dir(),
        snapshots=len(listing.manifests),
        damaged=list(listing.invalid),
        transactions=transactions,
        last_transaction=last,
        journal_error=journal_error,
    )


def _doctor(root: Path | None, offline: bool) -> DoctorReport:
    problems: list[str] = []
    inst: install.Install | None = None
    found_by: str | None = None
    install_error: str | None = None
    locations: Locations | None = None
    try:
        inst, found_by = _discover(root)
    except install.InstallNotFoundError as exc:
        install_error = (
            "no install found at the default locations that could be checked"
            if exc.unchecked
            else "no install found at the default locations"
        )
        locations = Locations(
            searched=[str(p) for p in exc.searched],
            could_not_check=[
                UncheckedLocation(location=str(p), reason=why)
                for p, why in zip(exc.unchecked, exc.unchecked_reasons, strict=True)
            ],
        )
        problems.append(install_error)
    except install.InstallError as exc:
        install_error = str(exc)
        problems.append(install_error)

    flavors: list[DoctorFlavor] = []
    client: ClientReport | None = None
    if inst is not None:
        game = _game_data(inst.flavors, offline)
        for f in inst.flavors:
            lay = layout.Layout.for_flavor(f, inst.root)
            try:
                sync = _sync_cvars(lay)
            except OSError as exc:
                problems.append(f"{f.folder}: cannot read the config files: {exc}")
                sync = []
            flavors.append(
                DoctorFlavor(
                    folder=f.folder,
                    product=f.product,
                    version=f.version,
                    build=f.build,
                    matching_rows=f.matching_rows,
                    game_data=game[f.folder],
                    sync_cvars=sync,
                )
            )
        if not inst.flavors:
            problems.append(f"{inst.root} has no flavor folders")
        client = _client_report(inst)

    store = _store_report()
    if store.damaged:
        problems.append(f"{len(store.damaged)} damaged snapshot manifest(s)")
    if store.journal_error:
        problems.append("the journal of the write gate cannot be read")
    return DoctorReport(
        install_root=str(inst.root) if inst is not None else None,
        found_by=found_by,
        install_error=install_error,
        locations=locations,
        flavors=flavors,
        other_dirs=list(inst.other_dirs) if inst is not None else [],
        client=client,
        store=store,
        problems=problems,
    )


def _print_game_data(g: GameDataReport) -> str:
    if g.status == "published":
        return f"wago.tools lists version {g.version}"
    if g.status == "not_published":
        return (
            f"wago.tools does not list version {g.version}; wowlab db2 cannot fetch tables for it"
        )
    if g.status == "no_version":
        return f"no version to look up ({g.detail})"
    if g.status == "not_checked":
        return "not checked (--offline)"
    return f"could not check ({g.detail})"


def _print_doctor(report: DoctorReport) -> None:
    if report.install_root is not None:
        _say(f"Install: {report.install_root} (found by {report.found_by})")
    else:
        _say(f"Install: {report.install_error}")
        if report.locations is not None:
            for loc in report.locations.searched:
                _say(f"  searched, no install there: {loc}")
            for unchecked in report.locations.could_not_check:
                _say(f"  could not check: {unchecked.location} ({unchecked.reason})")
            _say(f"  Pass --root, or set {install.ENV_ROOT}, to name the install folder.")
    for f in report.flavors:
        version = f.version or "no version in .build.info"
        _say(f"Flavor {f.folder}: product {f.product or '(none)'}, version {version}")
        if f.matching_rows > 1:
            _say(
                f"  {f.matching_rows} rows of .build.info name this product; "
                "the first active one is shown"
            )
        _say(f"  Game data: {_print_game_data(f.game_data)}")
        if f.sync_cvars:
            _say(
                "  Server sync (when on, the server can replace local binds, macros or settings "
                "at login; which other *-cache files it replaces is not known):"
            )
            for sync in f.sync_cvars:
                value = (
                    f'"{sync.value}"'
                    if sync.value is not None
                    else "not set (client default; wowlab does not know whether that default is on)"
                )
                _say(f"    {sync.file}: {sync.name} {value}")
    if report.other_dirs:
        _say("Folders named _..._ that are not flavors: " + ", ".join(report.other_dirs))
    if report.client is not None:
        c = report.client
        if c.state == "not_running":
            _say("Client: not running")
        elif c.state == "could_not_check":
            _say(f"Client: could not check ({c.error}); the write gate treats this as running")
        else:
            label = "running" if c.state == "running" else "cannot tell (the write gate refuses)"
            _say(f"Client: {label}")
            for p in c.processes:
                what = p.name or (p.exe.name if p.exe else "?")
                where = f" in {p.flavor_folder}" if p.flavor_folder else ""
                _say(f"  pid {p.pid}: {p.state.value} {what}{where}")
    store = report.store
    _say(f"Snapshot store: {store.path}" + ("" if store.exists else " (not created yet)"))
    damaged = f"{len(store.damaged)} damaged" if store.damaged else "none damaged"
    _say(f"  {store.snapshots} snapshot(s) that load; {damaged}")
    for bad in store.damaged:
        _say(f"  damaged: {bad.name}: {bad.reason}")
    if store.journal_error:
        _say(f"  journal: {store.journal_error}")
    elif store.last_transaction is not None:
        t = store.last_transaction
        _say(
            f"  {store.transactions} transaction(s) in the journal; most recent: {t.id} "
            f'({t.state}, {t.paths} file(s)) "{t.label}"'
        )
    else:
        _say("  no transactions in the journal")
    _say("  (`wowlab snap verify` re-hashes every stored object)")


@app.command()
@_handled
def doctor(
    root: RootOpt = None,
    offline: Annotated[
        bool,
        typer.Option("--offline", help="Do not ask wago.tools whether each version is listed."),
    ] = False,
    json_out: JsonOpt = False,
) -> None:
    """Install, flavors, versions, whether the client runs, store health. JSON: DoctorReport."""
    report = _doctor(root, offline)
    if json_out:
        _emit(report)
    else:
        _print_doctor(report)
    if report.problems:
        raise typer.Exit(EXIT_ERROR)


# ─── install show ────────────────────────────────────────────────────────────


@install_app.command("show")
@_handled
def install_show(root: RootOpt = None, json_out: JsonOpt = False) -> None:
    """The install root, its flavors and its .build.info rows. JSON: install.Install."""
    try:
        inst, found_by = _discover(root)
    except install.InstallNotFoundError as exc:
        for p in exc.searched:
            _note(f"searched, no install there: {p}")
        for p, why in zip(exc.unchecked, exc.unchecked_reasons, strict=True):
            _note(f"could not check: {p} ({why})")
        raise
    if json_out:
        _emit(inst)
        return
    _say(f"Install: {inst.root} (found by {found_by})")
    if not inst.flavors:
        _say("No flavor folders (a folder named _..._ holding .flavor.info).")
    for f in inst.flavors:
        _say(f"Flavor {f.folder}")
        _say(f"  product: {f.product or '(none in .flavor.info)'}")
        _say(f"  version: {f.version or '(no .build.info row for this product)'}")
        if f.build is not None:
            _say(f"  build:   {f.build}")
        if f.matching_rows > 1:
            _say(f"  {f.matching_rows} .build.info rows name this product; the first active one")
    _say(".build.info rows (a row does not show that its flavor folder exists):")
    for row in inst.products:
        active = "active" if row.is_active else "inactive"
        _say(f"  {row.product or '(no product)'}  {row.version or '(no version)'}  {active}")
    if inst.other_dirs:
        _say("Folders named _..._ that are not flavors: " + ", ".join(inst.other_dirs))
    if inst.decode_errors:
        _say("Some bytes in .build.info or .flavor.info are not UTF-8; shown replaced.")


# ─── tree and explain ────────────────────────────────────────────────────────


class Explained(_Out):
    """A file-map entry, as `explain` and `tree --explain` show it."""

    entry_id: str
    what: str
    written_by: str
    edit: str
    tier: str
    module: str


class TreeItem(_Out):
    path: str  # relative to the flavor folder
    kind: Literal["file", "folder", "symlink"]
    size: int | None
    note: str | None
    explained: Explained | None


class TreeReport(_Out):
    """`wowlab tree --json`."""

    install_root: str
    flavor_folder: str
    items: list[TreeItem]
    truncated: bool
    errors: list[str]


def _explained(entry: FileMapEntry) -> Explained:
    return Explained(
        entry_id=entry.id,
        what=entry.what,
        written_by=entry.written_by,
        edit=entry.edit,
        tier=entry.tier,
        module=entry.module,
    )


def _tree_items(lay: layout.Layout, inv: layout.Inventory) -> dict[str, TreeItem]:
    items: dict[str, TreeItem] = {}

    def add(
        path: str, kind: Literal["file", "folder", "symlink"], size: int | None, note: str | None
    ) -> None:
        if path and path not in items:
            items[path] = TreeItem(path=path, kind=kind, size=size, note=note, explained=None)

    try:
        with os.scandir(lay.flavor_path) as it:
            top = sorted(it, key=lambda e: e.name)
    except OSError:
        top = []
    for e in top:
        if e.is_symlink():
            continue  # reported with its target below
        add(e.name, "folder" if e.is_dir(follow_symlinks=False) else "file", None, None)
    for acct in inv.accounts:
        add(acct.path, "folder", None, "account folder")
        for realm in acct.realms:
            note = (
                "numeric folder (not a realm name)" if realm.kind == "numeric" else "realm folder"
            )
            add(realm.path, "folder", None, note)
            for c in realm.characters:
                add(c.path, "folder", None, "character folder")
    for f in inv.wtf_files:
        add(f.path, "file", f.size, None)
    for sv in inv.saved_variables:
        who = "Blizzard UI" if sv.blizzard else f"addon {sv.addon}"
        what = "previous write of " if sv.backup else ""
        add(sv.path, "file", sv.size, f"{what}{sv.scope} SavedVariables of {who}")
    for addon in inv.addons:
        add(addon.path, "folder", None, f"addon; {_SELECTION_WORDS[addon.selection]}")
        for toc in addon.tocs:
            add(f"{addon.path}/{toc.file}", "file", toc.size, toc.error)
    for o in inv.other.overrides:
        add(o.path, "file", o.size, f"{o.area} override")
    for area in inv.other.areas:
        if area.present and area.path is not None and not area.symlink:
            add(
                area.path,
                "folder",
                area.bytes,
                f"{area.files} files in {area.folders} folders, not listed one by one",
            )
    for link in inv.symlinks:
        where = "inside" if link.inside_install else "outside"
        add(
            link.path,
            "symlink",
            None,
            f"link to {link.target} ({where} the install), not followed",
        )
    for meta in inv.os_metadata:
        add(meta, "file", None, "operating-system folder metadata")
    return items


_SELECTION_WORDS = {
    "single": "one TOC",
    "depends_on_game_type": "several TOCs; which one loads depends on the game type",
    "no_toc": "no TOC named after the folder",
}


def _in_scope(path: str, prefix: str) -> bool:
    return not prefix or path == prefix or path.startswith(prefix + "/")


@app.command()
@_handled
def tree(
    path: Annotated[
        str | None, typer.Argument(help="Only this path under the flavor folder.")
    ] = None,
    explain: Annotated[
        bool, typer.Option("--explain", help="Add each path's file-map entry.")
    ] = False,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
) -> None:
    """Inventory of a flavor folder: WTF files, SavedVariables, addons, overrides,
    areas (counted, not listed). JSON: TreeReport."""
    inst, chosen, lay = _open(root, flavor)
    inv = lay.inventory()
    prefix = ""
    if path:
        given = Path(path)
        if given.is_absolute():
            try:
                prefix = given.relative_to(lay.flavor_path).as_posix()
            except ValueError:
                raise CliError(f"{path} is not inside {lay.flavor_path}", EXIT_USAGE) from None
        else:
            prefix = given.as_posix().strip("/")
        if prefix == ".":
            prefix = ""
    items = [i for p, i in sorted(_tree_items(lay, inv).items()) if _in_scope(p, prefix)]
    if explain:
        explained = []
        for item in items:
            found = lay.classify(item.path, is_dir=item.kind == "folder" or None)
            if isinstance(found, layout.Classified):
                item = item.model_copy(update={"explained": _explained(found.entry)})
            explained.append(item)
        items = explained
    errors = [f"{e.path}: {e.error}" for e in inv.errors]
    report = TreeReport(
        install_root=str(inst.root),
        flavor_folder=chosen.folder,
        items=items,
        truncated=inv.truncated,
        errors=errors,
    )
    if json_out:
        _emit(report)
        return
    _say(f"{chosen.folder} in {inst.root}")
    for item in items:
        slash = "/" if item.kind == "folder" else ""
        size = f"  {_bytes(item.size)}" if item.size is not None and item.kind == "file" else ""
        note = f"  ({item.note})" if item.note else ""
        _say(f"  {item.path}{slash}{size}{note}")
        if explain:
            if item.explained is None:
                _say("      no file-map row")
            else:
                e = item.explained
                edit = _edit_words(e.edit, e.tier, restorable=_restorable("flavor", item.path))
                _say(f"      [{e.entry_id}] {_plain(e.what)}")
                _say(f"      written by: {_plain(e.written_by)}")
                _say(f"      edit: {edit}")
    if inv.truncated:
        _note("The walk hit a bound; the listing is incomplete.")
    for err in errors:
        _note(f"could not read {err}")


def _plain(markdown: str) -> str:
    """File-map cells are Markdown; drop the bold markers for a terminal."""
    return markdown.replace("**", "")


_GATE_WORDS = "yes, through wowlab's write gate only, with the client closed"
_SERVER_MAY_REPLACE = "(server may replace)"
_TIER_WORDS = {
    "A": "A: ordinary addon-user behaviour",
    "A (read)": "A (read): reading it is ordinary addon-user behaviour",
    "A (read, later wave)": "A (read, later wave): reading it is ordinary; wowlab does not read it yet",
    "B": "B: works, but Blizzard does not support it and a patch can reset it",
    "—": "—: nothing wowlab does with it",
}
# The subtrees the write gate may write, spelled as the client spells them.
_GATE_AREAS = frozenset({"wtf", "interface", "fonts"})


def _restorable(base: str | None, path: str) -> bool:
    """Whether a flavor path lies where the write gate could put it back when
    named (`WTF/`, `Interface/`, `Fonts/`)."""
    return base == "flavor" and path.split("/", 1)[0].casefold() in _GATE_AREAS


def _edit_words(edit: str, tier: str = "", *, restorable: bool = False) -> str:
    """The file map's Edit cell in words. `restorable`: the path is inside the
    subtrees the gate writes, so a `no` file is one a restore leaves alone
    unless it is named (owner decision 2026-09-27)."""
    if edit.startswith("gate"):
        rest = edit[len("gate") :].strip()
        if rest == _SERVER_MAY_REPLACE:
            words = (
                f"{_GATE_WORDS}; the server may replace it at login (sync settings: wowlab doctor)"
            )
        else:
            words = _GATE_WORDS + (f" {rest}" if rest else "")
    elif edit == "no":
        words = (
            "no; wowlab leaves it alone (a restore writes it only if you name it with --paths)"
            if restorable
            else "no; wowlab never writes it"
        )
    elif edit == "n/a":
        words = "n/a; not a file wowlab touches"
    elif edit == "—":
        words = "—; a folder, see the files in it"
    else:
        words = edit
    if tier == "B":
        words += "; unsupported by Blizzard, a patch can reset it"
    return words


def _tier_words(tier: str) -> str:
    return _TIER_WORDS.get(tier, tier)


class ExplainReport(_Out):
    """`wowlab explain --json`."""

    query: str
    result: Annotated[layout.Classified | layout.Unclassified, Field(discriminator="status")]


def _is_inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


@app.command()
@_handled
def explain(
    path: Annotated[
        str,
        typer.Argument(
            help="Absolute; or relative to the current folder when it exists there; "
            "else relative to the install folder; else to the flavor folder."
        ),
    ],
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
) -> None:
    """What a path in the install is, who writes it, whether wowlab may edit it.
    JSON: ExplainReport."""
    inst, _ = _discover(root)
    given = Path(path)
    result: layout.Classified | layout.Unclassified
    here = Path.cwd() / given
    if given.is_absolute():
        result = layout.classify(path, inst)
    elif os.path.lexists(here) and _is_inside(here.absolute(), Path(inst.root).absolute()):
        result = layout.classify(str(here) + ("/" if path.endswith("/") else ""), inst)
    else:
        result = layout.classify(path, inst)
        if not isinstance(result, layout.Classified) and (flavor or len(inst.flavors) == 1):
            chosen = _select_flavor(inst, flavor)
            in_flavor = layout.Layout.for_flavor(chosen, inst.root).classify(path)
            if isinstance(in_flavor, layout.Classified):
                result = in_flavor
    report = ExplainReport(query=path, result=result)
    if json_out:
        _emit(report)
        return
    if isinstance(result, layout.Unclassified):
        where = f" ({result.flavor_folder})" if result.flavor_folder else ""
        _say(f"{path}: no file-map row{where}: {result.reason}")
        if len(inst.flavors) > 1 and flavor is None and not given.is_absolute():
            _say("  (with several flavors, pass --flavor to read PATH inside one of them)")
        return
    e = result.entry
    base = f"in flavor folder {result.flavor_folder}" if result.base == "flavor" else "install root"
    edit = _edit_words(e.edit, e.tier, restorable=_restorable(result.base, result.path))
    _say(f"{result.path or '.'} ({base})")
    _say(f"  what:         {_plain(e.what)}")
    _say(f"  written by:   {_plain(e.written_by)}")
    _say(f"  edit:         {edit}")
    _say(f"  tier:         {_tier_words(e.tier)}")
    _say(f"  read by:      {e.module}")
    _say(f"  file-map row: {e.id} ({e.doc_path})")


# ─── sv ──────────────────────────────────────────────────────────────────────


_SV_TIMING = (
    "SavedVariables are written at logout, /reload or a clean exit: these files are from the "
    "last save, not the session in progress, and a running client overwrites them at its "
    "next save."
)


class SvListReport(_Out):
    """`wowlab sv list --json`."""

    flavor_folder: str
    files: list[layout.SavedVariablesFile]
    notes: list[str]  # the caveats the text output prints


@sv_app.command("list")
@_handled
def sv_list(
    account: AccountOpt = None,
    character: CharacterOpt = None,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
) -> None:
    """Every SavedVariables file, account-wide and per character. JSON: SvListReport."""
    _, chosen, lay = _open(root, flavor)
    files = list(lay.saved_variables())
    if account is not None or character is not None:
        acct = _select_account(lay, account)
        files = [f for f in files if f.account == acct.folder]
        if character is not None:
            char = _select_character(acct, character)
            files = [
                f
                for f in files
                if f.realm_folder == char.realm_folder and f.character_folder == char.folder
            ]
    report = SvListReport(flavor_folder=chosen.folder, files=files, notes=[_SV_TIMING])
    if json_out:
        _emit(report)
        return
    if not files:
        _say("No SavedVariables files.")
    for f in files:
        who = "Blizzard UI" if f.blizzard else (f.addon or "?")
        flags = " (previous write, .bak)" if f.backup else ""
        _say(f"{f.scope:<9}  {who:<32}  {_bytes(f.size):>14}  {f.path}{flags}")
    if files:
        _say(_SV_TIMING)


# The JSON shape of `sv dump --json`. Flat: every value is one row naming its
# parent row, so the JSON nests to a fixed depth however deep the Lua tables
# go (a nested shape stops validating near depth 60, below the parser's
# MAX_DEPTH of 200), and rows can be written one at a time.


class LuaStringNode(_Out):
    type: Literal["string"]
    text: str  # the decoded bytes as UTF-8; invalid bytes shown as \xNN
    utf8: bool  # whether the bytes are valid UTF-8
    base64: str | None  # the exact bytes, when they are not valid UTF-8


class LuaNumberNode(_Out):
    type: Literal["number"]
    raw: str  # the number exactly as written


class LuaBoolNode(_Out):
    type: Literal["boolean"]
    value: bool


class LuaNilNode(_Out):
    type: Literal["nil"]


class LuaTableNode(_Out):
    type: Literal["table"]
    entries: int  # how many entries it holds; they are the rows whose parent is this row


LuaNode = Annotated[
    LuaTableNode | LuaStringNode | LuaNumberNode | LuaBoolNode | LuaNilNode,
    Field(discriminator="type"),
]


class SvValue(_Out):
    """One value: a top-level variable, the `--path` value, or a table entry."""

    id: int  # the row's index in `values`
    parent: int | None  # the table row holding it; None for a variable or the --path value
    # `variable`: a top-level `name = value`; `path`: the value --path named;
    # otherwise how the entry's key is written.
    style: Literal["variable", "path", "positional", "string", "number", "name", "boolean"]
    name: str | None  # the variable name, a bare-name key, or the --path text
    position: int | None  # 1-based, positional entries only
    key: LuaStringNode | LuaNumberNode | LuaBoolNode | None  # a bracketed key
    value: LuaNode
    duplicate: bool  # a later entry with a key an earlier one already has
    comment: str | None  # the line comment after the entry, e.g. "-- [1]"


class SvDumpReport(_Out):
    """`wowlab sv dump --json`: every value, depth first in file order (a
    table's entries follow its row). Written compact, one row at a time."""

    file: str
    path: str | None
    values: list[SvValue]


def _lua_text(raw: bytes) -> str:
    return raw.decode("utf-8", "backslashreplace")


def _string_node(value: luadata.LuaString) -> dict[str, Any]:
    data = value.data
    try:
        return {"type": "string", "text": data.decode("utf-8"), "utf8": True, "base64": None}
    except UnicodeDecodeError:
        return {
            "type": "string",
            "text": _lua_text(data),
            "utf8": False,
            "base64": base64.b64encode(data).decode("ascii"),
        }


def _value_node(value: luadata.LuaValue) -> dict[str, Any]:
    if isinstance(value, luadata.LuaTable):
        return {"type": "table", "entries": len(value.entries)}
    if isinstance(value, luadata.LuaString):
        return _string_node(value)
    if isinstance(value, luadata.LuaNumber):
        return {"type": "number", "raw": value.raw}
    if isinstance(value, luadata.LuaBool):
        return {"type": "boolean", "value": value.value}
    return {"type": "nil"}


def _key_node(entry: luadata.Entry) -> dict[str, Any] | None:
    key = entry.key
    if isinstance(key, luadata.LuaString):
        return _string_node(key)
    if isinstance(key, luadata.LuaNumber):
        return {"type": "number", "raw": key.raw}
    if isinstance(key, luadata.LuaBool):
        return {"type": "boolean", "value": key.value}
    return None


def _rows(tops: Sequence[tuple[str, str, luadata.LuaValue]]) -> Iterator[dict[str, Any]]:
    """`SvValue` rows as plain dicts, one at a time, depth first; iterative,
    so the parser's depth bound is never a Python recursion problem."""
    next_id = 0
    for style, name, value in tops:
        top = next_id
        next_id += 1
        yield {
            "id": top,
            "parent": None,
            "style": style,
            "name": name,
            "position": None,
            "key": None,
            "value": _value_node(value),
            "duplicate": False,
            "comment": None,
        }
        if not isinstance(value, luadata.LuaTable):
            continue
        stack: list[tuple[int, Iterator[luadata.Entry], list[int]]] = [
            (top, iter(value.entries), [0])
        ]
        while stack:
            parent, entries, counter = stack[-1]
            entry = next(entries, None)
            if entry is None:
                stack.pop()
                continue
            position: int | None = None
            if entry.style is luadata.KeyStyle.POSITIONAL:
                counter[0] += 1
                position = counter[0]
            row = next_id
            next_id += 1
            yield {
                "id": row,
                "parent": parent,
                "style": entry.style.value,
                "name": entry.key if isinstance(entry.key, str) else None,
                "position": position,
                "key": _key_node(entry),
                "value": _value_node(entry.value),
                "duplicate": entry.duplicate,
                "comment": _lua_text(entry.comment) if entry.comment is not None else None,
            }
            if isinstance(entry.value, luadata.LuaTable):
                stack.append((row, iter(entry.value.entries), [0]))


def _stream_dump(file: str, path: str | None, rows: Iterator[dict[str, Any]]) -> None:
    """Write `SvDumpReport` JSON to stdout row by row: only one row is held
    at a time, and nothing is indented (a deep file would otherwise print a
    wall of spaces on every line)."""
    encoder = json.JSONEncoder(ensure_ascii=True, separators=(",", ":"))
    out = typer.get_text_stream("stdout")
    head = {"file": _json_text(file), "path": None if path is None else _json_text(path)}
    out.write(encoder.encode(head)[:-1] + ',"values":[')
    for n, row in enumerate(rows):
        if n:
            out.write(",")
        for chunk in encoder.iterencode(_map_strings(row, _json_text)):
            out.write(chunk)
    out.write("]}\n")
    out.flush()


def _scalar(value: luadata.LuaValue) -> str:
    if isinstance(value, luadata.LuaTable):
        return "{}"
    if isinstance(value, luadata.LuaString):
        return _lua_text(value.raw)
    if isinstance(value, luadata.LuaNumber):
        return value.raw
    if isinstance(value, luadata.LuaBool):
        return "true" if value.value else "false"
    return "nil"


def _flatten(path: str, value: luadata.LuaValue) -> Iterator[tuple[str, str]]:
    """(path, value text) for every leaf and empty table, in file order.

    Positional entries count from 1 as Lua numbers them. Iterative, so the
    parser's depth bound is never a Python recursion problem."""
    if not isinstance(value, luadata.LuaTable) or not value.entries:
        yield path, _scalar(value)
        return
    stack: list[tuple[str, Iterator[luadata.Entry], list[int]]] = [(path, iter(value.entries), [0])]
    while stack:
        prefix, entries, counter = stack[-1]
        entry = next(entries, None)
        if entry is None:
            stack.pop()
            continue
        position: int | None = None
        if entry.style is luadata.KeyStyle.POSITIONAL:
            counter[0] += 1
            position = counter[0]
        child = prefix + svmerge.path_step(entry, position)
        inner = entry.value
        if isinstance(inner, luadata.LuaTable) and inner.entries:
            stack.append((child, iter(inner.entries), [0]))
        else:
            yield child, _scalar(inner)


def _parse_path(expr: str) -> svmerge.KeyPath:
    """`--path`, read by the grammar `sv merge --key` reads (M11-25)."""
    try:
        return svmerge.parse_path(expr, "--path")
    except svmerge.MergeError as exc:
        raise CliError(str(exc), EXIT_USAGE) from exc


def _resolve_path(doc: luadata.LuaDocument, expr: str) -> luadata.LuaValue:
    path = _parse_path(expr)
    name = path.head
    found = [a for a in doc.assignments if a.name == name]
    if not found:
        raise CliError(f"no top-level variable {name!r} in this file")
    value: luadata.LuaValue = found[-1].value  # the client runs the file top to bottom
    where = name
    for step in path.steps:
        if not isinstance(value, luadata.LuaTable):
            raise CliError(f"{where} is not a table, so it has no {step.spelling}")
        matches = []
        position = 0
        for entry in value.entries:
            pos: int | None = None
            if entry.style is luadata.KeyStyle.POSITIONAL:
                position += 1
                pos = position
            if step.matches(entry, pos):
                matches.append(entry)
        if not matches:
            raise CliError(f"{where} has no key {step.spelling}")
        if len(matches) > 1:
            raise CliError(
                f"{where} has the key {step.spelling} {len(matches)} times (duplicate keys); "
                "see `wowlab sv dump` without --path"
            )
        where += step.spelling
        value = matches[0].value
    return value


def _sv_file(path: str, root: Path | None, flavor: str | None) -> Path:
    given = Path(path)
    if given.is_absolute() or os.path.lexists(given):
        return given
    _, _, lay = _open(root, flavor)
    return lay.flavor_path / given


@sv_app.command("dump")
@_handled
def sv_dump(
    file: Annotated[
        str,
        typer.Argument(
            help="A SavedVariables file: absolute, relative to the current folder, "
            "or relative to the flavor folder."
        ),
    ],
    path: Annotated[
        str | None,
        typer.Option(
            "--path",
            help='One value, e.g. Var.key[3].name or Var["some key"], spelled as the dump '
            'prints it; a quoted key takes Lua 5.1 escapes (\\n, \\\\, \\", \\ddd).',
        ),
    ] = None,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
) -> None:
    """A SavedVariables file as data: one `path = value` line per value, in file
    order. Parsed by `luadata`, never run as code. JSON: SvDumpReport."""
    target = _sv_file(file, root, flavor)
    doc = luadata.read(target)
    if path is not None:
        value = _resolve_path(doc, path)
        if json_out:
            _stream_dump(str(target), path, _rows([("path", path, value)]))
        else:
            for p, v in _flatten(path, value):
                _say(f"{p} = {v}")
    elif json_out:
        tops = [("variable", a.name, a.value) for a in doc.assignments]
        _stream_dump(str(target), None, _rows(tops))
    else:
        for a in doc.assignments:
            for p, v in _flatten(a.name, a.value):
                _say(f"{p} = {v}")
    _note(_SV_TIMING)


# ─── sv merge (§13.4, M11-09) ────────────────────────────────────────────────


class SvMergeReport(_Out):
    """`wowlab sv merge --json`: what the merge found and whether it wrote.
    Printed when unresolved conflicts stop the write, too."""

    file: str  # the target, relative to the flavor folder
    scope: Literal["account", "character"]  # the file's, from `layout`
    into: str  # the --into character, <realm folder>/<character folder>
    source: str  # "character <realm folder>/<folder>", "snapshot <id>" or "this file"
    base: str | None  # the --base snapshot, for a three-way merge
    mode: Literal["two-way", "three-way"]
    keys: list[str]  # --key as given
    take: Literal["ours", "theirs"] | None
    conflicts: list[svmerge.Conflict]  # resolved ones too (`resolved` says how)
    absent: list[svmerge.Absent]  # on one side only: never deleted from the target
    taken: list[svmerge.Taken]  # taken from the source into the target
    written: bool
    transaction: str | None  # the journal record of the write (`wowlab undo` reverses it)
    notes: list[str]  # the warnings and caveats the text output prints


_MERGE_PROVEN = (
    "The addon reads the merged file at its next login, may migrate it, and rewrites it at "
    "logout: the merge is proven only after one login and logout, and a subtree taken from "
    "an older version of the addon may be reset by it."
)
_ABSENT_NOTE = (
    "A key listed as absent is on one side only. Many addons leave out values equal to their "
    "defaults [verify], which wowlab cannot see, so absent is not deleted: it is never "
    "removed from the target."
)
_NO_LOGIN = "no login between the two snapshots; the loader was not re-checked"
_PROBE_KEPT = "probe kept from the target: it counts that character's logins (§13.4)"
_LOADER_BUG = (
    "the target addon would load its defaults at the next login and save them at logout, "
    "overwriting the merge (the Forever beta had such a SavedVariables loader bug, "
    "https://github.com/nobewayo/ForeverSVFix). Log in and out once with the lab-addon "
    "enabled and check again, or pass --force-loader-check to merge anyway"
)


def _into_character(account: layout.Account, spec: str, option: str) -> layout.Character:
    """A character folder for --into/--from; never a `<Realm>/<First>/` twin
    of `<digits>/<First>-<Second>/` folders (§13.4)."""
    char = _select_character(account, spec)
    if char.shape == "realm_name" and char.twins:
        raise CliError(
            f"{option} {spec!r} is the folder {char.realm_folder}/{char.folder}, a twin of "
            f"{', '.join(char.twins)}; SavedVariables are kept in the <digits>/<First>-<Second> "
            f"folder, so name that one with {option}",
            EXIT_USAGE,
        )
    return char


def _is_character(account: layout.Account, spec: str) -> bool:
    realm, _, folder = spec.rpartition("/")
    for r in account.realms:
        for c in r.characters:
            if realm and c.realm_folder == realm and c.folder == folder:
                return True
            if not realm and c.folder.casefold() == spec.casefold():
                return True
    return False


def _sv_name(f: layout.SavedVariablesFile) -> str:
    return f.path.rsplit("/", 1)[-1]


def _character_sv(
    files: Sequence[layout.SavedVariablesFile],
    account: layout.Account,
    char: layout.Character,
    name: str,
) -> layout.SavedVariablesFile | None:
    """The file `name` in `char`'s SavedVariables/ in `account` (another
    account may hold a character folder of the same name), or None. More
    than one match (names differing only in case) is an error, never read
    as "none"."""
    mine = [
        f
        for f in files
        if f.scope == "character"
        and not f.backup
        and f.account == account.folder
        and f.realm_folder == char.realm_folder
        and f.character_folder == char.folder
    ]
    exact = [f for f in mine if _sv_name(f) == name]
    match = exact or [f for f in mine if _sv_name(f).casefold() == name.casefold()]
    if len(match) > 1:
        raise CliError(
            f"{char.path}/SavedVariables/ holds {len(match)} files named like {name!r}: "
            + ", ".join(f.path for f in match)
        )
    return match[0] if match else None


def _merge_target(
    lay: layout.Layout,
    files: Sequence[layout.SavedVariablesFile],
    file: str,
    account: layout.Account,
    char: layout.Character,
) -> layout.SavedVariablesFile:
    """FILE: a bare name in the --into character's SavedVariables/, then the
    account's; or a path as `sv dump` takes it, whose scope `layout` gives."""
    usable = [f for f in files if not f.backup and f.account == account.folder]
    if "/" not in file and os.sep not in file:
        found = _character_sv(usable, account, char, file)
        if found is not None:
            return found
        acct = [f for f in usable if f.scope == "account"]
        match = [f for f in acct if _sv_name(f) == file] or [
            f for f in acct if _sv_name(f).casefold() == file.casefold()
        ]
        if len(match) == 1:
            return match[0]
        raise CliError(
            f"no SavedVariables file {file!r} in {char.path}/SavedVariables/ or in "
            f"WTF/Account/{account.folder}/SavedVariables/"
        )
    given = Path(file)
    if not given.is_absolute() and not os.path.lexists(given):
        given = lay.flavor_path / given
    absolute = Path(os.path.normpath(given.absolute()))
    if not _is_inside(absolute, lay.flavor_path):
        raise CliError(f"{file} is not inside the flavor folder {lay.flavor_path}", EXIT_USAGE)
    rel = absolute.relative_to(lay.flavor_path).as_posix()
    every = [f for f in files if f.path == rel] or [
        f for f in files if f.path.casefold() == rel.casefold()
    ]
    if len(every) != 1:
        raise CliError(f"{file} is not a SavedVariables file wowlab lists (see `wowlab sv list`)")
    target = every[0]
    if target.backup:
        raise CliError(
            f"{file} is the client's backup of the previous write; not merged", EXIT_USAGE
        )
    if target.account != account.folder:
        raise CliError(f"{file} is in account {target.account}, not {account.folder}", EXIT_USAGE)
    if target.scope == "character" and (
        target.realm_folder != char.realm_folder or target.character_folder != char.folder
    ):
        raise CliError(
            f"{file} belongs to {target.realm_folder}/{target.character_folder}, not to the "
            f"--into character {char.realm_folder}/{char.folder}",
            EXIT_USAGE,
        )
    return target


def _read_sv(path: Path) -> bytes:
    """A SavedVariables file's bytes, read without following a link and
    without blocking on anything but a regular file (L1)."""
    return snapshot.read_regular_file(path, limit=luadata.MAX_FILE_BYTES)


def _snapshot_sv(store: snapshot.SnapshotStore, snapshot_id: str, rel: str, folder: str) -> bytes:
    manifest = store.show(snapshot_id)
    held = f"{manifest.flavor_folder or folder}/{rel}"
    entry = manifest.entry(held)
    if entry is None or entry.kind != "file" or entry.sha256 is None:
        raise CliError(f"snapshot {manifest.id} does not hold {held}")
    return store.read_object(entry.sha256, size=entry.size)


def _same_root(a: str, b: Path) -> bool:
    if Path(a) == b:
        return True
    try:
        return Path(a).resolve() == b.resolve()
    except (OSError, RuntimeError):
        return False


_LAB_WROTE = "the Lab wrote WowLab.lua after that snapshot; the loader was not re-checked"

# A point in the order the loader check compares states in: a snapshot's
# (created_at, id), the key `holding` sorts by.
_When = tuple[str, str]


def _lab_writes(
    store: snapshot.SnapshotStore,
    flavor_path: Path,
    rel: str,
    manifests: Sequence[snapshot.Manifest],
) -> list[tuple[_When, str]]:
    """Committed guard writes that changed the file `rel` (the full path
    relative to the flavor folder, not its name) in this flavor, each as
    (the point it happened after, its journal record id). A write happened
    right after its pre-write snapshot; when that snapshot is gone from the
    store, after the record's own `created_at`. Records that rolled back,
    did not finish rolling back, or are still open (killed) are left out:
    only a committed write is the Lab's (§13.4, owner ruling for M11-24)."""
    when = {m.id: (m.created_at, m.id) for m in manifests}
    out: list[tuple[_When, str]] = []
    for record in guard.history(store=store.path):
        if record.state != "committed" or not _same_root(record.flavor_path, flavor_path):
            continue
        if any(c.path == rel and c.before != c.after for c in record.paths):
            out.append((when.get(record.snapshot_id, (record.created_at, "")), record.id))
    return out


def _spanned(writes: Sequence[tuple[_When, str]], older: _When, newer: _When | None) -> str | None:
    """The journal id of a Lab write between the states `older` (a snapshot)
    and `newer` (a later snapshot; None: the disk now), or None. A write
    after snapshot S lies after S and before the next snapshot, so the pair
    S and an older snapshot does not span it."""
    for point, record in writes:
        if older <= point and (newer is None or point < newer):
            return record
    return None


def _loader_check(
    store: snapshot.SnapshotStore,
    inst: install.Install,
    chosen: install.Flavor,
    lay: layout.Layout,
    files: Sequence[layout.SavedVariablesFile],
    account: layout.Account,
    char: layout.Character,
) -> tuple[str | None, list[str]]:
    """(the reason to refuse, or None; notes) from the --into character's
    `WowLab.lua` on disk, the newest snapshot of it, and the newest snapshot
    together with the newest older one whose bytes differ from it (§13.4 as
    ruled on 2026-09-29, and the owner ruling for M11-24). A comparison that
    spans a committed guard write to that file is skipped with a note: the
    Lab, not the client, changed the counter. Reads only."""
    name = svmerge.LAB_ADDON_FILE
    found = _character_sv(files, account, char, name)
    who = f"{char.realm_folder}/{char.folder}"
    if found is None:
        return None, [
            f"No {name} for {who}: the SavedVariables loader was not checked. With the "
            "lab-addon installed (`wowlab addon install lab`), one login and logout writes it."
        ]
    rel = found.path
    disk = _read_sv(lay.flavor_path / rel)
    try:
        probe = svmerge.read_probe(luadata.parse(disk))
    except luadata.LuaDataError as exc:
        return f"{rel} is not data the parser accepts ({exc}), so its probe cannot be read", []
    if probe.lost:
        return (
            f"{rel}: probe.lost is true. At its last load the lab-addon found WowLabCharDB "
            f"without its probe, which is what a SavedVariables loader failure leaves: "
            f"{_LOADER_BUG}.",
            [],
        )
    if probe.loads is None:
        return f"{rel} holds no probe.loads, so the loader cannot be checked; {_LOADER_BUG}.", []
    held = f"{chosen.folder}/{rel}"
    mine: list[snapshot.Manifest] = []
    holding: list[tuple[snapshot.Manifest, snapshot.Entry]] = []
    for m in store.list_lenient().manifests:
        if m.flavor_folder != chosen.folder or not _same_root(m.install_root, Path(inst.root)):
            continue
        mine.append(m)
        entry = m.entry(held)
        if entry is not None and entry.kind == "file" and entry.sha256 is not None:
            holding.append((m, entry))
    holding.sort(key=lambda pair: (pair[0].created_at, pair[0].id))
    writes = _lab_writes(store, lay.flavor_path, rel, mine)
    notes: list[str] = []

    def when(m: snapshot.Manifest) -> _When:
        return (m.created_at, m.id)

    def loads_in(m: snapshot.Manifest, entry: snapshot.Entry) -> int | str:
        """`probe.loads` in the snapshot, or the reason to refuse."""
        assert entry.sha256 is not None
        data = store.read_object(entry.sha256, size=entry.size)
        try:
            loads = svmerge.read_probe(luadata.parse(data)).loads
        except luadata.LuaDataError as exc:
            return f"{rel} in snapshot {m.id} is not data the parser accepts ({exc})"
        if loads is None:
            return f"{rel} in snapshot {m.id} holds no probe.loads, so loads cannot be compared"
        return loads

    if holding:
        # The disk against the newest snapshot (ruling of 2026-09-29): lower
        # `loads` means the session since did not load its SavedVariables.
        # Equal `loads` with other bytes is that session with the file edited
        # after the snapshot, and passes.
        newest, newest_entry = holding[-1]
        if hashlib.sha256(disk).hexdigest() != newest_entry.sha256:
            record = _spanned(writes, when(newest), None)
            if record is not None:
                notes.append(
                    f"{rel}: {_LAB_WROTE} (snapshot {newest.id}, then journal record {record})."
                )
            else:
                newest_loads = loads_in(newest, newest_entry)
                if isinstance(newest_loads, str):
                    return newest_loads, []
                if probe.loads < newest_loads:
                    return (
                        f"{rel}: loads on disk ({probe.loads}) went down from the newest snapshot "
                        f"({newest_loads}): the last session's SavedVariables did not load "
                        f"(snapshot {newest.id}); {_LOADER_BUG}.",
                        [],
                    )
    if len(holding) < 2:
        return None, [
            *notes,
            f"Fewer than two snapshots hold {rel}, so probe.loads was not compared across "
            "sessions (`wowlab snap create` before and after a login makes the check possible).",
        ]
    # The newest snapshot against the newest older one whose bytes differ
    # (owner ruling for M11-24): snapshots with the same bytes are one state,
    # and guard snapshots before every write, whatever the file.
    new, new_entry = holding[-1]
    differing = [pair for pair in holding[:-1] if pair[1].sha256 != new_entry.sha256]
    if not differing:
        old = holding[-2][0]
        return None, [
            *notes,
            f"{rel}: {_NO_LOGIN} (snapshots {old.id} and {new.id} hold the same bytes).",
        ]
    old, old_entry = differing[-1]
    record = _spanned(writes, when(old), when(new))
    if record is not None:
        notes.append(
            f"{rel}: {_LAB_WROTE} (snapshot {old.id}, then journal record {record}, then "
            f"snapshot {new.id})."
        )
        return None, notes
    before = loads_in(old, old_entry)
    if isinstance(before, str):
        return before, []
    after = loads_in(new, new_entry)
    if isinstance(after, str):
        return after, []
    if after > before:
        return None, notes
    went = "went down" if after < before else "did not go up"
    same = "" if old is holding[-2][0] else " (the snapshots after it hold the same bytes)"
    return (
        f"{rel}: probe.loads {went}, from {before} in snapshot {old.id}{same} to {after} in "
        f"snapshot {new.id}. It goes up at every load of the lab-addon, so the client did not "
        f"load this character's SavedVariables back in between: {_LOADER_BUG}.",
        [],
    )


def _lab_written(store: snapshot.SnapshotStore, flavor_path: Path) -> list[Path]:
    """Files the guard journal records as last written by wowlab in this
    flavor: `luadata.serialize` never takes its style from them."""
    last: dict[str, str | None] = {}
    for record in guard.history(store=store.path):
        if record.state != "committed" or Path(record.flavor_path) != flavor_path:
            continue
        for change in record.paths:
            last[change.path] = change.after
    return [flavor_path / p for p, after in last.items() if after is not None]


def _print_merge(show: Callable[[str], None], report: SvMergeReport) -> None:
    base = f", base snapshot {report.base}" if report.base else ""
    show(
        f"Merge {report.file} ({report.scope}, {report.mode}{base}) from {report.source} "
        f"into {report.into}"
    )
    if report.keys:
        show(f"  limited to --key {', '.join(report.keys)}")
    if report.conflicts:
        how = {None: "unresolved", "ours": "kept ours", "theirs": "took theirs"}[report.take]
        show(f"  {len(report.conflicts)} conflict(s), {how}:")
        for c in report.conflicts:
            base_text = f", base {c.base}" if c.base is not None else ""
            show(f"    {c.path}: ours {c.ours}, theirs {c.theirs}{base_text}")
    if report.absent:
        show(f"  {len(report.absent)} key(s) absent from one side (never deleted):")
        for a in report.absent:
            show(f"    {a.path}  (missing from {a.missing_from})")
    if report.taken:
        show(f"  {len(report.taken)} value(s) taken from the source:")
        for t in report.taken:
            show(f"    {t.path} = {t.value}")


@sv_app.command("merge")
@_handled
def sv_merge(
    file: Annotated[
        str,
        typer.Argument(
            help="A SavedVariables file: a name (WowLab.lua), looked up in the --into "
            "character's SavedVariables/ and then the account's; or a path as `sv dump` takes it."
        ),
    ],
    into: Annotated[
        str,
        typer.Option(
            "--into",
            help="The target character folder (<digits>/<First>-<Second> on Forever); its "
            "WowLab.lua is read for the loader check.",
        ),
    ],
    from_: Annotated[
        str | None,
        typer.Option(
            "--from",
            help="The source: a character folder (per-character files only) or a snapshot "
            "id. Left out, --key SRC=DST copies within the file.",
        ),
    ] = None,
    base: Annotated[
        str | None,
        typer.Option(
            "--base",
            help="A snapshot holding the common ancestor: with --from SNAPSHOT, a three-way merge.",
        ),
    ] = None,
    key: Annotated[
        list[str] | None,
        typer.Option(
            "--key",
            help='A subtree, e.g. Var.profile or Var["some key"], spelled as sv dump prints it; '
            'a quoted key takes Lua 5.1 escapes (\\n, \\\\, \\", \\ddd). SRC=DST copies SRC '
            "to DST. Repeatable.",
        ),
    ] = None,
    take: Annotated[
        Literal["ours", "theirs"] | None,
        typer.Option(
            "--take", help="Resolve every conflict with the target's or the source's value."
        ),
    ] = None,
    force_loader_check: Annotated[
        bool,
        typer.Option(
            "--force-loader-check", help="Merge even when the SavedVariables loader check refuses."
        ),
    ] = False,
    account: AccountOpt = None,
    yes: YesOpt = False,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
) -> None:
    """Merge one SavedVariables file into a character's copy, by key path, and
    write it through the write gate (client closed, snapshot first, `wowlab undo`
    reverses it). Parsed and written as data by `luadata`, never run.

    Two-way (--from a character or a snapshot): every key whose values differ is a
    conflict. Three-way (--from SNAPSHOT --base SNAPSHOT): a change on one side is
    taken, the same change on both once, different changes are conflicts. Conflicts
    are listed and nothing is written unless --take resolves them; exit 1. A key on
    one side only is listed as absent and never deleted. An account-wide file is
    shared by every character, so merging it from another character is refused
    (exit 2); use --from SNAPSHOT, or --key SRC=DST to copy between the per-character
    keys an addon keeps inside it. Before writing, the --into character's WowLab.lua
    is checked for a SavedVariables loader failure (exit 3). JSON: SvMergeReport."""
    keys = list(key or [])
    try:
        same_path = svmerge.check_keys(keys)
    except svmerge.MergeError as exc:
        raise CliError(str(exc), EXIT_USAGE) from exc
    if from_ is None:
        for given, same in zip(keys, same_path, strict=True):
            if same:
                raise CliError(
                    f"--key {given} names the same path on both sides, which needs --from: "
                    "within one file, copy with --key SRC=DST",
                    EXIT_USAGE,
                )
    if from_ is None and base is not None:
        raise CliError(
            "--base names the common ancestor of --from SNAPSHOT; give --from", EXIT_USAGE
        )
    if from_ is None and not keys:
        raise CliError(
            "give --from CHARACTER|SNAPSHOT, or --key SRC=DST to copy within the file", EXIT_USAGE
        )
    store = snapshot.SnapshotStore()
    inst, chosen, lay = _open(root, flavor)
    acct = _select_account(lay, account)
    char = _into_character(acct, into, "--into")
    files = lay.saved_variables()
    target = _merge_target(lay, files, file, acct, char)
    source_char: layout.Character | None = None
    source_snap: str | None = None
    if from_ is not None:
        if _is_character(acct, from_):
            source_char = _into_character(acct, from_, "--from")
        else:
            try:
                source_snap = store.resolve_id(from_)
            except snapshot.SnapshotNotFoundError as exc:
                raise CliError(
                    f"--from {from_!r} is neither a character folder in account {acct.folder} "
                    f"nor a snapshot id ({exc})",
                    EXIT_USAGE,
                ) from exc
    if source_char is not None:
        if target.scope == "account":
            raise CliError(
                f"{target.path} is account-wide: every character on account {acct.folder} "
                "shares this one file, so there is nothing to merge between two characters. "
                "Use --from SNAPSHOT (another machine or an earlier state), or --key SRC=DST "
                "to copy between per-character keys inside it",
                EXIT_USAGE,
            )
        if source_char.path == char.path:
            raise CliError("--from and --into name the same character", EXIT_USAGE)
        if base is not None:
            raise CliError("--base goes with --from SNAPSHOT, not a character", EXIT_USAGE)
    base_id = store.resolve_id(base) if base is not None else None
    target_path = lay.flavor_path / target.path
    ours_bytes = _read_sv(target_path)
    if source_char is not None:
        found = _character_sv(files, acct, source_char, _sv_name(target))
        if found is None:
            raise CliError(f"{source_char.path}/SavedVariables/ has no {_sv_name(target)}")
        theirs_bytes = _read_sv(lay.flavor_path / found.path)
        source = f"character {source_char.realm_folder}/{source_char.folder}"
    elif source_snap is not None:
        theirs_bytes = _snapshot_sv(store, source_snap, target.path, chosen.folder)
        source = f"snapshot {source_snap}"
    else:
        theirs_bytes = ours_bytes
        source = "this file"
    base_bytes = (
        _snapshot_sv(store, base_id, target.path, chosen.folder) if base_id is not None else None
    )

    refusal, notes = _loader_check(store, inst, chosen, lay, files, acct, char)
    if refusal is not None:
        if not force_loader_check:
            raise CliError(f"refused by the loader check: {refusal}", EXIT_REFUSED)
        notes.append(f"The loader check was overridden by --force-loader-check: {refusal}")

    ours = luadata.parse(ours_bytes)
    theirs = ours if source_char is None and source_snap is None else luadata.parse(theirs_bytes)
    base_doc = luadata.parse(base_bytes) if base_bytes is not None else None
    try:
        result = svmerge.merge(ours, theirs, base=base_doc, keys=keys, take=take)
    except svmerge.MergeError as exc:
        raise CliError(str(exc), EXIT_USAGE) from exc
    lab_file = _character_sv(files, acct, char, svmerge.LAB_ADDON_FILE)
    if lab_file is not None and lab_file.path == target.path:
        # The --into character's own WowLab.lua: its probe counts that
        # character's logins, and the next loader check reads it (§13.4,
        # owner ruling for M11-24).
        try:
            result, kept = svmerge.keep_probe(ours, result, theirs=theirs, base=base_doc)
        except svmerge.MergeError as exc:
            raise CliError(str(exc), EXIT_USAGE) from exc
        if kept:
            notes.append(_PROBE_KEPT)
    if result.absent:
        notes.append(_ABSENT_NOTE)
    show = _say if not json_out else _note

    def report(*, written: bool, transaction: str | None = None) -> SvMergeReport:
        return SvMergeReport(
            file=target.path,
            scope=target.scope,
            into=f"{char.realm_folder}/{char.folder}",
            source=source,
            base=base_id,
            mode=result.mode,
            keys=keys,
            take=take,
            conflicts=list(result.conflicts),
            absent=list(result.absent),
            taken=list(result.taken),
            written=written,
            transaction=transaction,
            notes=[*notes, _MERGE_PROVEN] if written else notes,
        )

    def finish(done: SvMergeReport, line: str) -> None:
        if json_out:
            _emit(done)
            return
        _print_merge(_say, done)
        for n in done.notes:
            _note(n)
        _say(line)

    if result.unresolved:
        finish(
            report(written=False),
            f"{len(result.conflicts)} conflict(s) left unresolved: nothing was written. "
            "Resolve them with --take ours|theirs, or limit the merge with --key.",
        )
        raise typer.Exit(EXIT_ERROR)
    data = luadata.serialize(
        result.document, target=target_path, lab_written=_lab_written(store, chosen.path)
    )
    if data == ours_bytes:
        finish(report(written=False), f"Nothing to change: {target.path} already holds the merge.")
        return
    if not yes:
        _print_merge(show, report(written=False))
        show(f"  replace  {target.path}  ({_bytes(len(data))})")
        show(_MERGE_PROVEN)
    try:
        _confirm("Write the merge?", yes)
    except typer.Exit:
        if json_out:
            _emit(report(written=False))
        raise
    expected = hashlib.sha256(ours_bytes).hexdigest()
    with guard.transaction(chosen, label=f"sv merge {target.path}", store=store.path) as tx:
        tx.write(target.path, data)
        if [item.before for item in tx.plan] != [expected]:
            raise CliError(
                f"{target.path} changed after it was read, so the merge was rolled back; "
                "run the command again"
            )
    record = guard.history(store=store.path)[-1].id
    finish(
        report(written=True, transaction=record),
        f"Merged into {target.path}: {len(result.taken)} value(s) taken. `wowlab undo` puts "
        "back what was there before.",
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
    return target, found.path, wtfconfig.read_config(lay.flavor_path / found.path)


def _no_file(what: str, target: _Target) -> str:
    who = {
        "global": "",
        "account": f" for account {target.account}",
        "character": f" for {target.character}",
    }[target.scope]
    return f"No {what}{who}."


@cvar_app.command("list")
@_handled
def cvar_list(
    scope: ScopeOpt = "global",
    account: AccountOpt = None,
    character: CharacterOpt = None,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
) -> None:
    """Every SET line of one config file. JSON: CVarListReport."""
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


@cvar_app.command("get")
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
    """One CVar's value in one config file. JSON: CVarGetReport."""
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


def _bindings_note(scope: Scope, found: bool) -> str:
    if scope == "character" and not found:
        return _NO_CHARACTER_BINDINGS
    if scope == "account":
        return _ACCOUNT_BINDINGS
    return "(the server can replace this file at login when binding sync is on; see wowlab doctor)"


class BindItem(_Out):
    key: str
    action: str
    line: int


class BindsReport(_Out):
    """`wowlab binds list --json`."""

    file: str | None
    scope: Scope
    account: str | None
    character: str | None
    bindings: list[BindItem]
    unknown_lines: int
    notes: list[str]  # the caveats the text output prints


@binds_app.command("list")
@_handled
def binds_list(
    account: AccountOpt = None,
    character: CharacterOpt = None,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
) -> None:
    """Key bindings of the account, or of one character with --character.
    JSON: BindsReport."""
    _, _, lay = _open(root, flavor)
    scope: Scope = "character" if character is not None else "account"
    target, found = _wtf_file(lay, "bindings-cache.wtf", scope, account, character)
    doc = wtfconfig.read_bindings(lay.flavor_path / found.path) if found else None
    report = BindsReport(
        file=found.path if found else None,
        scope=target.scope,
        account=target.account,
        character=target.character,
        bindings=[BindItem(key=b.key, action=b.action, line=b.index + 1) for b in doc.bindings]
        if doc
        else [],
        unknown_lines=len(doc.unknown_lines()) if doc else 0,
        notes=[_bindings_note(scope, doc is not None)],
    )
    if json_out:
        _emit(report)
        return
    if doc is None:
        _say(_no_file("bindings-cache.wtf", target))
        if scope == "character":
            _say(_NO_CHARACTER_BINDINGS)
        return
    _say(f"{report.file}:")
    for b in report.bindings:
        _say(f"  {b.key:<24} {b.action}")
    if report.unknown_lines:
        _say(f"  {report.unknown_lines} other line(s) are not bind lines and are not shown")
    _say(report.notes[0])


class MacroItem(_Out):
    id: str
    name: str
    icon: str
    body_lines: list[str]
    complete: bool


class MacrosReport(_Out):
    """`wowlab macros list --json`."""

    file: str | None
    scope: Scope
    account: str | None
    character: str | None
    macros: list[MacroItem]
    unknown_lines: int
    notes: list[str]  # the caveats the text output prints


@macros_app.command("list")
@_handled
def macros_list(
    account: AccountOpt = None,
    character: CharacterOpt = None,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
) -> None:
    """Account macros, or one character's with --character. JSON: MacrosReport."""
    _, _, lay = _open(root, flavor)
    scope: Scope = "character" if character is not None else "account"
    target, found = _wtf_file(lay, "macros-cache.txt", scope, account, character)
    doc = wtfconfig.read_macros(lay.flavor_path / found.path) if found else None
    report = MacrosReport(
        file=found.path if found else None,
        scope=target.scope,
        account=target.account,
        character=target.character,
        macros=[
            MacroItem(
                id=m.macro_id,
                name=m.name,
                icon=m.icon,
                body_lines=list(m.body_lines),
                complete=m.complete,
            )
            for m in doc.macros
        ]
        if doc
        else [],
        unknown_lines=len(doc.unknown_lines()) if doc else 0,
        notes=[_MACRO_SYNC],
    )
    if json_out:
        _emit(report)
        return
    if doc is None:
        _say(_no_file("macros-cache.txt", target))
        return
    _say(f"{report.file}:")
    if not report.macros:
        _say("  no macros")
    for m in report.macros:
        cut = "" if m.complete else "  (the file ends before this macro's END)"
        _say(f"  {m.name}  (id {m.id}, icon {m.icon}){cut}")
        for line in m.body_lines:
            _say(f"    {line}")
    if report.unknown_lines:
        _say(f"  {report.unknown_lines} line(s) outside any macro are not shown")
    _say(_MACRO_SYNC)


# ─── addons ──────────────────────────────────────────────────────────────────


_UI_ESCAPE = re.compile(r"\|c[0-9A-Fa-f]{8}|\|r")


def _ui_text(text: str) -> str:
    """A TOC title without the client's colour escapes (`|cAARRGGBB`, `|r`)."""
    return _UI_ESCAPE.sub("", text)


class AddonsReport(_Out):
    """`wowlab addons list --json`."""

    flavor_folder: str
    addons: list[layout.Addon]


@addons_app.command("list")
@_handled
def addons_list(root: RootOpt = None, flavor: FlavorOpt = None, json_out: JsonOpt = False) -> None:
    """Every folder under Interface/AddOns/ with its TOC files. JSON: AddonsReport."""
    _, chosen, lay = _open(root, flavor)
    addons = list(lay.addons())
    report = AddonsReport(flavor_folder=chosen.folder, addons=addons)
    if json_out:
        _emit(report)
        return
    if not addons:
        _say("No addon folders.")
    for a in addons:
        toc = next((t for t in a.tocs if t.file == a.selected_toc), None)
        doc = toc.document if toc else None
        title = _ui_text(doc.get("Title") or "") if doc else None
        declared = doc.interface if doc else None
        bits = [a.name]
        if title:
            bits.append(f'"{title}"')
        if a.selection == "depends_on_game_type":
            bits.append(f"{len(a.tocs)} TOC files; which one loads depends on the game type")
        elif a.selection == "no_toc":
            bits.append("no TOC file named after the folder; the client does not load it")
        if declared is not None:
            bits.append(f"TOC declares Interface {declared.text}")
        if a.blizzard:
            bits.append("named like a Blizzard addon")
        _say("  ".join(bits))


# ─── log ─────────────────────────────────────────────────────────────────────

_LOG_POLL = 0.5  # seconds between looks at the log while following
_follow_sleep: Callable[[float], None] = time.sleep  # a test replaces it
MAX_TAIL_LINES = 100_000
_NO_LOG = (
    "no combat log under Logs/ (the client creates one when combat logging is turned on, "
    "by /combatlog or by an addon)"
)
_BATCHES = (
    "the client adds to this file in batches, and only while combat logging is on: the "
    "latest events may not be on disk yet, and if logging is off, this file ends where "
    "logging was turned off or the client closed"
)
_STILL_WRITING = "the last line is still being written and is not shown"


class LogFollowing(_Out):
    """From here on, lines come from `file` (`log tail --follow`)."""

    kind: Literal["following"] = "following"
    file: str  # relative to the flavor folder
    reason: Literal["start", "rotated", "truncated", "replaced"]


type LogEntry = Annotated[
    combatlog.Record | combatlog.Unparsed | LogFollowing, Field(discriminator="kind")
]


class LogTailLine(RootModel[LogEntry]):
    """`wowlab log tail --follow --json`: one per line of output."""


class LogTailReport(_Out):
    """`wowlab log tail --json`."""

    file: str | None  # relative to the flavor folder; None when there is no combat log
    entries: list[combatlog.Entry]
    notes: list[
        str
    ]  # the caveats the text output also prints (the no-log message on stdout, the others on stderr)


def _logs_dir(lay: layout.Layout) -> Path | None:
    """The flavor's `Logs/` folder, however its case is spelled, or None."""
    for child in sorted(lay.flavor_path.iterdir()):
        if child.name.casefold() == "logs" and child.is_dir():
            return child
    return None


def _rel(lay: layout.Layout, path: Path) -> str:
    try:
        return path.relative_to(lay.flavor_path).as_posix()
    except ValueError:
        return str(path)


_FOLLOWING_WORDS = {
    "start": "",
    "rotated": " (a new log file)",
    "truncated": " (the file shrank or its earlier bytes changed; reading it from the start)",
    "replaced": " (a different file under this name; reading it from the start)",
}


def _print_log_entry(entry: combatlog.Entry | LogFollowing, json_out: bool) -> None:
    if json_out:
        line = LogTailLine(entry).model_dump(mode="json")
        typer.echo(json.dumps(_map_strings(line, _json_text), ensure_ascii=True))
    elif isinstance(entry, LogFollowing):
        _say(f"==> {entry.file}{_FOLLOWING_WORDS[entry.reason]} <==")
    elif isinstance(entry, combatlog.Unparsed):
        cut = ""
        if entry.truncated:
            kept = len(entry.raw.encode("utf-8", "surrogateescape"))  # bytes, not characters
            cut = f" (cut to its first {kept} of {entry.length} bytes)"
        _say(f"(not tokenized: {entry.reason}){cut} {entry.raw}")
    else:
        _say(entry.raw)


def _follow_log(
    lay: layout.Layout,
    newest: Path | None,
    end: int | None,
    entries: list[combatlog.Entry],
    json_out: bool,
) -> None:
    """`log tail --follow`: print as the client appends, until Ctrl-C."""
    logs = _logs_dir(lay)
    if newest is None:
        _note(f"{_NO_LOG}; waiting for one")
    while logs is None:
        _follow_sleep(_LOG_POLL)
        logs = _logs_dir(lay)
    # With no log when the command started, the first one found is read whole.
    stream = combatlog.follow(
        newest or logs,
        offset=end if newest is not None else 0,
        poll_interval=_LOG_POLL,
        sleep=_follow_sleep,
    )
    noted = False
    try:
        for item in stream:
            if isinstance(item, combatlog.Following):
                _print_log_entry(
                    LogFollowing(file=_rel(lay, item.path), reason=item.reason), json_out
                )
                if not noted:
                    _note(_BATCHES)
                    noted = True
                if item.reason == "start":
                    for entry in entries:
                        _print_log_entry(entry, json_out)
                    entries = []
            else:
                _print_log_entry(item, json_out)
    finally:
        stream.close()


@log_app.command("tail")
@_handled
def log_tail(
    follow: Annotated[
        bool,
        typer.Option(
            "--follow",
            "-f",
            help="Keep printing lines as the client appends them, including in a "
            "new log file, until interrupted. Waits for a log if there is none yet.",
        ),
    ] = False,
    lines: Annotated[
        int,
        typer.Option(
            "--lines",
            "-n",
            min=0,
            max=MAX_TAIL_LINES,
            help=f"How many of the last lines to print (at most {MAX_TAIL_LINES:,}).",
        ),
    ] = 10,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
) -> None:
    """The last lines of the newest combat log (Logs/WoWCombatLog*.txt), tokenized.
    JSON: LogTailReport; with --follow, one LogTailLine per line of output."""
    _, _, lay = _open(root, flavor)
    logs = _logs_dir(lay)
    newest = combatlog.newest_log(logs) if logs is not None else None
    entries: list[combatlog.Entry] = []
    end: int | None = None
    notes = [_NO_LOG]
    if newest is not None:
        size = newest.stat().st_size  # before the read: a line finished since is not "partial"
        entries, end = combatlog.tail(newest, lines)
        notes = [_BATCHES] + ([_STILL_WRITING] if end < size else [])
    if follow:
        try:
            _follow_log(lay, newest, end, entries, json_out)
        except KeyboardInterrupt:
            raise typer.Exit(EXIT_OK) from None
        return
    report = LogTailReport(file=_rel(lay, newest) if newest else None, entries=entries, notes=notes)
    if json_out:
        _emit(report)
        return
    if newest is None:
        _say(_NO_LOG)
        return
    _print_log_entry(LogFollowing(file=_rel(lay, newest), reason="start"), json_out)
    for entry in entries:
        _print_log_entry(entry, json_out)
    for note in notes:
        _note(note)


# ─── db2 ─────────────────────────────────────────────────────────────────────


class BuildsReport(_Out):
    """`wowlab db2 builds --json`: the source's listing, as it gives it."""

    products: dict[str, list[gamedata.Build]]


class FetchReport(_Out):
    """`wowlab db2 fetch --json`."""

    table: str
    build: str
    path: str
    sidecar: gamedata.Sidecar | None


class HeadReport(_Out):
    """`wowlab db2 head --json`."""

    table: str
    build: str
    rows: list[dict[str, str]]


BuildOpt = Annotated[
    str | None,
    typer.Option("--build", help="Full version string. Default: the flavor's version."),
]


def _build_for(build: str | None, root: Path | None, flavor: str | None) -> str:
    if build is not None:
        return build
    _, chosen, _ = _open(root, flavor)
    if chosen.version is None:
        raise CliError(f"{chosen.folder} has no version in .build.info; pass --build")
    return chosen.version


def _check_table_key(data: gamedata.GameData, table: str, build: str) -> None:
    """A malformed table name or build string is a usage error (exit 2), not a
    traceback: `GameData` refuses them before any I/O."""
    try:
        data.table_path(table, build)
    except ValueError as exc:
        raise CliError(str(exc), EXIT_USAGE) from exc


@db2_app.command("builds")
@_handled
def db2_builds(
    product: Annotated[
        str | None, typer.Option("--product", help="Only this product code.")
    ] = None,
    json_out: JsonOpt = False,
) -> None:
    """Products and their versions as wago.tools lists them. JSON: BuildsReport."""
    with _open_gamedata() as data:
        listing = data.builds()
    if product is not None:
        if product not in listing:
            raise CliError(f"wago.tools lists no product {product!r}")
        listing = {product: listing[product]}
    if json_out:
        _emit(BuildsReport(products=listing))
        return
    if product is None:
        for name, builds in listing.items():
            _say(f"{name}: {len(builds)} version(s)")
        _say("(--product P lists one product's versions)")
        return
    for b in listing[product]:
        when = f"  {b.created_at}" if b.created_at else ""
        _say(f"{b.version}{when}")


@db2_app.command("fetch")
@_handled
def db2_fetch(
    table: Annotated[str, typer.Argument(help="Table name, e.g. ChrClasses.")],
    build: BuildOpt = None,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
) -> None:
    """Download one table for one version into the cache (never replaced once
    there). JSON: FetchReport."""
    version = _build_for(build, root, flavor)
    with _open_gamedata() as data:
        _check_table_key(data, table, version)
        path = data.table(table, version)
        sidecar = data.sidecar(table, version)
    report = FetchReport(table=table, build=version, path=str(path), sidecar=sidecar)
    if json_out:
        _emit(report)
        return
    _say(f"{table} for {version}: {path}")
    if sidecar is not None:
        _say(
            f"  from {sidecar.url} at {sidecar.fetched_at:%Y-%m-%d %H:%M:%S} UTC, {_bytes(sidecar.size)}"
        )


@db2_app.command("head")
@_handled
def db2_head(
    table: Annotated[str, typer.Argument(help="Table name, e.g. ChrClasses.")],
    build: BuildOpt = None,
    rows: Annotated[int, typer.Option("-n", "--rows", min=0, help="How many rows.")] = 10,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
) -> None:
    """The first rows of a table (fetched first if needed), as CSV. JSON: HeadReport."""
    version = _build_for(build, root, flavor)
    got: list[dict[str, str]] = []
    with _open_gamedata() as data:
        _check_table_key(data, table, version)
        for row in data.rows(table, version):
            if len(got) >= rows:
                break
            got.append(row)
    if json_out:
        _emit(HeadReport(table=table, build=version, rows=got))
        return
    if not got:
        _say("(no rows)")
        return
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(got[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(got)
    typer.echo(buffer.getvalue(), nl=False)


# ─── snap ────────────────────────────────────────────────────────────────────


class SnapshotSummary(_Out):
    id: str
    created_at: str
    label: str
    install_root: str
    flavor_folder: str | None
    flavor_version: str | None
    client_running: bool | None
    files: int
    bytes: int


class SnapListReport(_Out):
    """`wowlab snap list --json`. The command exits 1 when `damaged` is not empty."""

    snapshots: list[SnapshotSummary]
    damaged: list[snapshot.InvalidManifest]


def _summary(m: snapshot.Manifest) -> SnapshotSummary:
    return SnapshotSummary(
        id=m.id,
        created_at=m.created_at,
        label=m.label,
        install_root=m.install_root,
        flavor_folder=m.flavor_folder,
        flavor_version=m.flavor_version,
        client_running=m.client_running,
        files=sum(1 for e in m.entries if e.kind == "file"),
        bytes=sum(e.size for e in m.entries if e.kind == "file"),
    )


def _client_running(inst: install.Install, chosen: install.Flavor) -> bool | None:
    """True running, False not, None when it cannot be told."""
    try:
        state = process.client_state(
            _roots(inst), flavor_folders=[chosen.folder], extra_names=_client_names(chosen.path)
        )
    except Exception:
        return None
    if state is process.ClientState.RUNNING:
        return True
    if state is process.ClientState.NOT_RUNNING:
        return False
    return None


# `snap gc` leaves objects younger than this, even when no manifest refers to
# them. Every `snap create` now holds the store lock, the first one included
# (`guard.store_lock(create=True)`, M10-17), so gc cannot run in the middle of
# one; the grace period stays as defence in depth for a create that holds no
# store lock: any library caller of `SnapshotStore.create`.
GC_GRACE_SECONDS = 3600.0


@snap_app.command("create")
@_handled
def snap_create(
    message: Annotated[str, typer.Option("-m", "--message", help="A label for the snapshot.")] = "",
    screenshots: Annotated[
        bool, typer.Option("--screenshots", help="Also capture Screenshots/.")
    ] = False,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
) -> None:
    """Snapshot a flavor's WTF/, Interface/ and Fonts/ into the store (reads the
    install only). Labels starting with `profile:` or `deleted-profile:` are
    `wowlab profile`'s and are refused. JSON: the manifest (snapshot.Manifest)."""
    if message.startswith(profiles.RESERVED_LABEL_PREFIXES):
        raise CliError(
            f"labels starting with {' or '.join(profiles.RESERVED_LABEL_PREFIXES)} belong to "
            "`wowlab profile`; use `wowlab profile save` or choose another label",
            EXIT_USAGE,
        )
    inst, chosen, lay = _open(root, flavor)
    subtrees = [f"{chosen.folder}/{s}" for s in lay.snapshot_subtrees(screenshots=screenshots)]
    running = _client_running(inst, chosen)
    store = snapshot.SnapshotStore()
    # A store that is an ancestor of the install is outside every install, so
    # the gate would create it and its `lock` before `create` refused the
    # overlap (exit 1). Refuse it first, with nothing created (M10-19).
    store.refuse_holding(Path(inst.root))
    # The first create makes the store; guard refuses one inside any install
    # before creating anything (L1), then holds its lock like every later one.
    with guard.store_lock(store.path, create=True):
        manifest = store.create(
            Path(inst.root),
            subtrees,
            label=message,
            flavor_folder=chosen.folder,
            flavor_version=chosen.version,
            client_running=running,
        )
    if running is not False:
        _note(
            "wowlab could not confirm the client was closed: SavedVariables on disk are as of "
            "its last logout or /reload, and the client overwrites them at its next logout, "
            "/reload or exit."
        )
    if json_out:
        _emit_manifest(manifest)
        return
    s = _summary(manifest)
    _say(f"Snapshot {manifest.id}")
    _say(f"  {chosen.folder} ({chosen.version or 'no version'}) in {inst.root}")
    if manifest.label:
        _say(f"  label: {manifest.label}")
    _say(f"  captured: {', '.join(manifest.subtrees)}")
    _say(f"  {s.files} file(s), {_bytes(s.bytes)}")


@snap_app.command("list")
@_handled
def snap_list(json_out: JsonOpt = False) -> None:
    """Every snapshot, oldest first; names each damaged manifest and then exits 1.
    JSON: SnapListReport."""
    listing = snapshot.SnapshotStore().list_lenient()
    report = SnapListReport(
        snapshots=[_summary(m) for m in listing.manifests], damaged=list(listing.invalid)
    )
    if json_out:
        _emit(report)
    else:
        if not report.snapshots:
            _say("No snapshots.")
        for s in report.snapshots:
            running = (
                "  (client was running)"
                if s.client_running
                else "  (client state not known)"
                if s.client_running is None
                else ""
            )
            label = f'  "{s.label}"' if s.label else ""
            version = s.flavor_version or "no version"
            _say(f"{s.id}  {s.flavor_folder}  {version}  {s.files} files{label}{running}")
    for bad in report.damaged:
        _note(f"damaged manifest {bad.name}: {bad.reason}")
    if report.damaged:
        raise typer.Exit(EXIT_ERROR)


@snap_app.command("show")
@_handled
def snap_show(
    snapshot_id: Annotated[str, typer.Argument(metavar="ID", help="Id or unique prefix.")],
    json_out: JsonOpt = False,
) -> None:
    """One snapshot and every path in it. JSON: the manifest (snapshot.Manifest)."""
    manifest = snapshot.SnapshotStore().show(snapshot_id)
    if json_out:
        _emit_manifest(manifest)
        return
    _say(f"Snapshot {manifest.id}")
    _say(f"  taken:    {manifest.created_at}")
    if manifest.label:
        _say(f"  label:    {manifest.label}")
    _say(f"  install:  {manifest.install_root}")
    _say(f"  flavor:   {manifest.flavor_folder} ({manifest.flavor_version or 'no version'})")
    _say(f"  captured: {', '.join(manifest.subtrees)}")
    if manifest.client_running:
        _say(
            "  The client was running when this was taken: its SavedVariables are as of its "
            "last logout or /reload, not the moment of the snapshot."
        )
    elif manifest.client_running is None:
        _say("  Whether the client was running was not known when this was taken.")
    for e in manifest.entries:
        if e.kind == "symlink":
            _say(f"  {e.path} -> {e.target} (link, recorded, not followed)")
        else:
            _say(f"  {e.path}  {_bytes(e.size)}  {(e.sha256 or '')[:12]}")


class LuaChange(_Out):
    path: str  # `Var.key[3]` style
    before: str | None  # None: added
    after: str | None  # None: removed


class ChangedFile(_Out):
    path: str
    before: snapshot.Entry
    after: snapshot.Entry
    lua: list[LuaChange] | None  # set for a SavedVariables file when both sides parse
    lua_note: str | None  # why there is no structural diff for a SavedVariables file


class DiffReport(_Out):
    """`wowlab snap diff --json`."""

    a: str
    b: str
    a_version: str | None  # the flavor version each snapshot was taken on
    b_version: str | None
    added: list[snapshot.Entry]
    removed: list[snapshot.Entry]
    changed: list[ChangedFile]
    mode_changed: list[snapshot.Change]


def _is_saved_variables(path: str) -> bool:
    parts = [p.casefold() for p in path.split("/")]
    if not parts[-1].endswith(".lua") or "wtf" not in parts[:-1]:
        return False
    return "savedvariables" in parts[:-1] or parts[-1] == "savedvariables.lua"


def _leaves(data: bytes) -> dict[str, str]:
    doc = luadata.parse(data)
    out: dict[str, str] = {}
    for a in doc.assignments:
        for p, v in _flatten(a.name, a.value):
            out[p] = v
    return out


def _lua_diff(
    store: snapshot.SnapshotStore, change: snapshot.Change
) -> tuple[list[LuaChange] | None, str | None]:
    if not _is_saved_variables(change.path):
        return None, None
    if change.before.sha256 is None or change.after.sha256 is None:
        return None, "one side is a link"
    try:
        # By content hash: the store checks the bytes against the name.
        before = _leaves(store.read_object(change.before.sha256, size=change.before.size))
        after = _leaves(store.read_object(change.after.sha256, size=change.after.size))
    except luadata.LuaDataError as exc:
        return None, f"not compared as data: one side does not parse ({exc})"
    except snapshot.SnapshotError as exc:
        # A side the store cannot supply has not parsed either: keep the
        # plain changed-path entry, and say why there is no more.
        return None, f"contents unreadable: {exc}"
    changes = [
        LuaChange(path=p, before=before.get(p), after=v)
        for p, v in after.items()
        if before.get(p) != v
    ]
    changes.extend(
        LuaChange(path=p, before=v, after=None) for p, v in before.items() if p not in after
    )
    return changes, None


@snap_app.command("diff")
@_handled
def snap_diff(
    a: Annotated[str, typer.Argument(metavar="A", help="Older snapshot id or prefix.")],
    b: Annotated[str, typer.Argument(metavar="B", help="Newer snapshot id or prefix.")],
    json_out: JsonOpt = False,
) -> None:
    """Files added, removed and changed from A to B; for SavedVariables that
    parse, which values changed. JSON: DiffReport."""
    store = snapshot.SnapshotStore()
    diff = store.diff(a, b)
    a_version = store.show(diff.a).flavor_version
    b_version = store.show(diff.b).flavor_version
    changed = []
    for c in diff.changed:
        lua, note = _lua_diff(store, c)
        changed.append(
            ChangedFile(path=c.path, before=c.before, after=c.after, lua=lua, lua_note=note)
        )
    report = DiffReport(
        a=diff.a,
        b=diff.b,
        a_version=a_version,
        b_version=b_version,
        added=list(diff.added),
        removed=list(diff.removed),
        changed=changed,
        mode_changed=list(diff.mode_changed),
    )
    if json_out:
        _emit(report)
        return
    _say(f"From {diff.a} to {diff.b}:")
    if a_version != b_version:
        _say(f"  ({diff.a} was taken on {_version(a_version)}; {diff.b} on {_version(b_version)})")
    if diff.is_empty:
        _say("  no differences")
    for e in report.added:
        _say(f"  added    {e.path}")
    for e in report.removed:
        _say(f"  removed  {e.path}")
    for cf in report.changed:
        _say(f"  changed  {cf.path}")
        if cf.lua_note:
            _say(f"      ({cf.lua_note})")
        for lc in cf.lua or []:
            if lc.before is None:
                _say(f"      + {lc.path} = {lc.after}")
            elif lc.after is None:
                _say(f"      - {lc.path} = {lc.before}")
            else:
                _say(f"      ~ {lc.path}: {lc.before} -> {lc.after}")
    for m in report.mode_changed:
        _say(f"  mode     {m.path} ({m.before.mode:o} -> {m.after.mode:o})")


@snap_app.command("verify")
@_handled
def snap_verify(json_out: JsonOpt = False) -> None:
    """Re-hash every stored object and check every manifest. Exits 1 on any
    problem. JSON: snapshot.VerifyReport."""
    report = snapshot.SnapshotStore().verify()
    if json_out:
        _emit(report)
    else:
        _say(
            f"{report.objects_checked} object(s) and {report.manifests_checked} manifest(s) checked"
        )
        for name in report.corrupt_objects:
            if name == snapshot.OBJECTS_DIR_ENTRY:
                _say(
                    f"  {name} is a link: nothing under it was checked. snap create "
                    "refuses to write through it, but reads (restore, undo, snap diff) "
                    "still follow it; move it aside and put the real directory back"
                )
            else:
                _say(f"  corrupt object: {name}")
        for miss in report.missing_objects:
            _say(f"  missing object {miss.sha256[:12]} for {miss.path} in {miss.snapshot_id}")
        for bad in report.invalid_manifests:
            _say(f"  damaged manifest {bad.name}: {bad.reason}")
        if report.ok:
            _say("  all sound")
    if not report.ok:
        raise typer.Exit(EXIT_ERROR)


@snap_app.command("gc")
@_handled
def snap_gc(
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Only list what would be removed.")
    ] = False,
    yes: YesOpt = False,
    json_out: JsonOpt = False,
) -> None:
    """Remove stored objects no snapshot refers to and older than an hour
    (GC_GRACE_SECONDS). Holds the store lock for the whole run, so no
    transaction or `snap create` runs meanwhile. JSON: snapshot.GcReport."""
    store = snapshot.SnapshotStore()
    if not store.path.is_dir():
        report = snapshot.GcReport(dry_run=True, unreferenced=(), unreferenced_bytes=0, removed=())
        if json_out:
            _emit(report)
        else:
            _say(f"No snapshot store at {store.path} yet; nothing to collect.")
        return
    with guard.store_lock(store.path):
        report = store.gc(dry_run=True, grace_seconds=GC_GRACE_SECONDS)
        if report.unreferenced and not dry_run:
            if not json_out:
                _say(
                    f"{len(report.unreferenced)} object(s) no snapshot refers to, "
                    f"{_bytes(report.unreferenced_bytes)} on disk."
                )
            _confirm("Remove them?", yes)
            report = store.gc(dry_run=False, grace_seconds=GC_GRACE_SECONDS)
    if json_out:
        _emit(report)
        return
    if report.skipped:
        _say(
            f"Left alone {len(report.skipped)} entr{'y' if len(report.skipped) == 1 else 'ies'} "
            "in the store that are links or not regular files, or changed before they "
            "could be removed (a link is never followed or deleted through): "
            + ", ".join(report.skipped)
        )
    if not report.unreferenced:
        _say(
            "Nothing to collect: every stored object is referred to by a snapshot, or is "
            "younger than an hour (kept in case a snapshot is being written)."
        )
    elif report.dry_run:
        _say(
            f"Would remove {len(report.unreferenced)} object(s), "
            f"{_bytes(report.unreferenced_bytes)} on disk (dry run)."
        )
    else:
        _say(f"Removed {len(report.removed)} object(s), {_bytes(report.removed_bytes)}.")


_RELOGIN = (
    "The client reads these files at its next login; with server sync on (see wowlab "
    "doctor), the server may replace binds, macros, settings and other *-cache files then."
)


def _version(version: str | None) -> str:
    return version if version is not None else "no recorded version"


def _relogin_notes(paths: Sequence[str]) -> list[str]:
    """The login caveat, when a change touches Config.wtf or a *-cache file."""
    for path in paths:
        name = path.rsplit("/", 1)[-1].casefold()
        if name == "config.wtf" or "-cache" in name:
            return [_RELOGIN]
    return []


def _plan_line(item: guard.PlanItem) -> str:
    if item.before is None:
        return f"  create   {item.path}  ({_bytes(item.size or 0)})"
    if item.after is None:
        return f"  delete   {item.path}"
    return f"  replace  {item.path}  ({_bytes(item.size or 0)})"


class SkippedPath(_Out):
    """A file a whole-snapshot restore left alone (owner decision 2026-09-27)."""

    path: str  # relative to the flavor folder
    entry_id: str  # its file-map row, whose Edit cell is `no`
    what: str


class RestoreReport(_Out):
    """`wowlab snap restore --json`: the plan, and with `--yes` what was done."""

    snapshot_id: str
    flavor_path: str
    snapshot_version: str | None  # the flavor version the snapshot was taken on
    flavor_version: str | None  # the flavor's version now
    paths: list[str] | None  # --paths as given; None restores the whole snapshot
    plan: list[guard.PlanItem]  # path, before/after SHA-256 (None: absent), bytes written
    skipped: list[SkippedPath]  # client-managed files a whole restore does not write
    dry_run: bool
    applied: bool
    transaction: str | None  # the journal record of the applied restore
    notes: list[str]


class UndoReport(_Out):
    """`wowlab undo --json`: the transaction to undo, and whether it was."""

    undone: str  # the journal record undone (or to be)
    label: str
    state: str
    created_at: str
    flavor_path: str
    # As that transaction journaled them: `before` is what the undo puts back
    # (None: the file is deleted), `after` is what is there now.
    plan: list[guard.PathChange]
    created_dirs: list[str]
    applied: bool
    transaction: str | None  # the journal record of the undo itself
    notes: list[str]


def _client_managed(
    lay: layout.Layout, plan: Sequence[guard.PlanItem]
) -> tuple[list[guard.PlanItem], list[SkippedPath]]:
    """Split a whole-snapshot plan: files whose file-map row says `no` are
    ones wowlab leaves alone (client-written backups such as `.lua.bak` and
    `.old`, `Blizzard_*` folders, most likely copied in by the owner, and
    file-browser metadata); they are skipped unless named with --paths."""
    kept: list[guard.PlanItem] = []
    skipped: list[SkippedPath] = []
    for item in plan:
        found = lay.classify(item.path, is_dir=False)
        if isinstance(found, layout.Classified) and found.entry.edit == "no":
            skipped.append(
                SkippedPath(path=item.path, entry_id=found.entry.id, what=_plain(found.entry.what))
            )
        else:
            kept.append(item)
    return kept, skipped


@snap_app.command("restore")
@_handled
def snap_restore(
    snapshot_id: Annotated[str, typer.Argument(metavar="ID", help="Id or unique prefix.")],
    paths: Annotated[
        list[str] | None,
        typer.Option(
            "--paths",
            help="Only this path, relative to the flavor folder; repeat for more. "
            "A path the snapshot covers but does not hold is deleted. A named path is "
            "restored even when its file-map row says wowlab leaves it alone.",
        ),
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Print the plan and change nothing.")
    ] = False,
    yes: YesOpt = False,
    root: RootOpt = None,
    json_out: JsonOpt = False,
) -> None:
    """Put back files from a snapshot, through the write gate: the client must be
    closed, a pre-write snapshot is taken first, and `wowlab undo` reverses it.

    Without --paths, files whose file-map Edit is `no` (client-written
    backups such as `.lua.bak` and `.old`, `Interface/AddOns/Blizzard_*`,
    file-browser metadata) are left alone and counted in the plan; name one
    with --paths to restore it. JSON: RestoreReport."""
    store = snapshot.SnapshotStore()
    manifest = store.show(snapshot_id)
    inst, _ = _discover(root)
    if manifest.flavor_folder is None:
        raise CliError(f"snapshot {manifest.id} names no flavor folder; it cannot be restored")
    match = [f for f in inst.flavors if f.folder == manifest.flavor_folder]
    if not match:
        raise CliError(
            f"snapshot {manifest.id} is of flavor folder {manifest.flavor_folder!r}, which "
            f"{inst.root} does not have (flavors: {_flavor_list(inst)}). Flavor folders are "
            "renamed between beta and launch; wowlab restores only into the folder a snapshot "
            "was taken from."
        )
    chosen = match[0]
    say = _note if json_out else _say
    # With --json the plan goes to stdout as JSON at the end; when a prompt
    # will ask first, the text plan goes to stderr so the question is not blind.
    prompting = json_out and not yes and not dry_run
    show = _say if not json_out else _note
    label = f"restore {manifest.id}"
    if paths:
        label += " (" + ", ".join(paths) + ")"
    named = list(paths) if paths else None  # no --paths: everything the snapshot holds
    with guard.transaction(chosen, label=label, dry_run=True) as tx:
        tx.restore(manifest.id, named)
        whole_plan = tx.plan
    skipped: list[SkippedPath] = []
    plan = list(whole_plan)
    if named is None:
        plan, skipped = _client_managed(layout.Layout.for_flavor(chosen, inst.root), whole_plan)
    # With something skipped, the rest is restored by name: the gate then
    # writes exactly the kept paths.
    wanted = named if named is not None or not skipped else [i.path for i in plan]

    def report(*, applied: bool, transaction: str | None = None) -> RestoreReport:
        notes = _relogin_notes([i.path for i in plan]) if applied else []
        return RestoreReport(
            snapshot_id=manifest.id,
            flavor_path=str(chosen.path),
            snapshot_version=manifest.flavor_version,
            flavor_version=chosen.version,
            paths=named,
            plan=plan,
            skipped=skipped,
            dry_run=dry_run,
            applied=applied,
            transaction=transaction,
            notes=notes,
        )

    if (not json_out or (prompting and plan)) and (plan or skipped):
        show(f"Restore from snapshot {manifest.id} into {chosen.path}: {len(plan)} change(s)")
        if manifest.flavor_version != chosen.version:
            show(
                f"  Taken on version {_version(manifest.flavor_version)}; {chosen.folder} is "
                f"now on {_version(chosen.version)}."
            )
        for item in plan:
            show(_plan_line(item))
        if skipped:
            show(
                f'  Skipped {len(skipped)} file(s) wowlab leaves alone (file-map Edit "no": '
                "client-written backups, Blizzard_* folders, file-browser metadata; name one "
                "with --paths to restore it):"
            )
            for sk in skipped:
                show(f"    {sk.path}  [{sk.entry_id}]")
    if not plan:
        if json_out:
            _emit(report(applied=False))
        elif skipped:
            _say("Nothing to restore but the skipped files; nothing was changed.")
        else:
            _say(f"Nothing to restore: {chosen.folder} already matches snapshot {manifest.id}.")
        return
    if dry_run:
        if json_out:
            _emit(report(applied=False))
        else:
            _say("Dry run: nothing was changed.")
        return
    try:
        _confirm("Apply these changes?", yes)
    except typer.Exit:
        if json_out:
            _emit(report(applied=False))
        raise
    with guard.transaction(chosen, label=label) as tx:
        tx.restore(manifest.id, wanted)
        if tx.plan != tuple(plan):
            # Raising inside the transaction makes the gate roll it back.
            raise CliError(
                "the files changed after the plan was shown, so the restore was rolled "
                "back; run the command again to see the new plan"
            )
    record = guard.history()[-1].id
    done = report(applied=True, transaction=record)
    if json_out:
        _emit(done)
        return
    say(f"Restored {len(plan)} file(s). `wowlab undo` puts back what was there before.")
    for n in done.notes:
        say(n)


@app.command()
@_handled
def undo(yes: YesOpt = False, json_out: JsonOpt = False) -> None:
    """Undo the most recent change made through the write gate (a restore, or an
    earlier undo), from the snapshot taken before it. JSON: UndoReport.

    The journal is read again after you answer, and the undo is refused if
    its most recent record changed meanwhile. The gate compares again under
    its store lock (`guard.undo(expected_id=...)`), so a change journaled
    after that re-read is refused too: only the record shown is undone."""
    records = guard.history()
    if not records:
        raise CliError("nothing to undo: the write gate's journal is empty")
    last = records[-1]

    def report(*, applied: bool, transaction: str | None = None) -> UndoReport:
        touched = [p.path for p in last.paths]
        return UndoReport(
            undone=last.id,
            label=last.label,
            state=last.state,
            created_at=last.created_at,
            flavor_path=last.flavor_path,
            plan=list(last.paths),
            created_dirs=list(last.created_dirs),
            applied=applied,
            transaction=transaction,
            notes=_relogin_notes(touched) if applied else [],
        )

    if not last.paths and not last.created_dirs:
        if json_out:
            _emit(report(applied=False))
        else:
            _say(f'The most recent transaction, {last.id} ("{last.label}"), changed no files.')
        return
    # With --json, the text plan goes to stderr when a prompt will ask first.
    show = _say if not json_out else _note
    if not json_out or not yes:
        show(f'Undo {last.id} ("{last.label}", {last.state}, {last.created_at})')
        show(f"  in {last.flavor_path}:")
        for p in last.paths:
            if p.before is None:
                show(f"  delete     {p.path}  (created by that transaction)")
            elif p.after is None:
                show(f"  recreate   {p.path}  (deleted by that transaction)")
            else:
                show(f"  put back   {p.path}  (as it was before that transaction)")
        for d in last.created_dirs:
            show(f"  remove folder {d}  (created by that transaction, if empty)")
    try:
        _confirm("Undo it?", yes)
    except typer.Exit:
        if json_out:
            _emit(report(applied=False))
        raise
    now = guard.history()
    if not now or now[-1].id != last.id:
        raise CliError(
            "the journal changed after the plan was shown; nothing was undone, run the "
            "command again to see the new plan"
        )
    guard.undo(expected_id=last.id)
    done = report(applied=True, transaction=guard.history()[-1].id)
    if json_out:
        _emit(done)
        return
    _say("Undone. This undo is journaled too: `wowlab undo` again reverses it.")
    for n in done.notes:
        _say(n)


# ─── profile ─────────────────────────────────────────────────────────────────


class ProfileSummary(_Out):
    name: str
    snapshot_id: str
    created_at: str
    presets: list[str]  # as given at save; informational
    install_root: str
    flavor_folder: str | None
    flavor_version: str | None
    client_running: bool | None
    subtrees: int
    files: int
    bytes: int


class ProfileListReport(_Out):
    """`wowlab profile list --json`. The command exits 1 when `damaged` is not empty."""

    profiles: list[ProfileSummary]
    damaged: list[snapshot.InvalidManifest]


class ProfileReport(_Out):
    """`wowlab profile save --json` and `wowlab profile show --json`."""

    profile: ProfileSummary
    subtrees: list[str]  # relative to the install root, as the snapshot records them
    excluded: list[str]
    entries: list[snapshot.Entry]
    notes: list[str]


class ProfileDeleteReport(_Out):
    """`wowlab profile delete --json`: the snapshot stays, relabelled."""

    name: str
    relabelled: list[SnapshotSummary]  # usually one; every snapshot labelled as this profile


class ProfileApplyReport(_Out):
    """`wowlab profile apply --json`: the plan, and with `--yes` what was done."""

    profile: str
    snapshot_id: str
    flavor_path: str
    snapshot_version: str | None  # the flavor version the profile was saved on
    flavor_version: str | None  # the flavor's version now
    plan: list[guard.PlanItem]  # writes, then deletes (after None) of files added since
    added: list[str]  # the deletes: files added under the profile's subtrees since the save
    skipped: list[profiles.SkippedPath]  # file-map Edit `no`: left alone
    left: list[profiles.LeftPath]  # added since, but not deletable: left alone
    cache_files: list[profiles.CacheFile]  # every *-cache* file in the plan, with the note
    dry_run: bool
    applied: bool
    transaction: str | None  # the journal record of the applied profile
    notes: list[str]


def _profile_summary(p: profiles.Profile) -> ProfileSummary:
    m = p.manifest
    return ProfileSummary(
        name=p.name,
        snapshot_id=m.id,
        created_at=m.created_at,
        presets=list(p.presets),
        install_root=m.install_root,
        flavor_folder=m.flavor_folder,
        flavor_version=m.flavor_version,
        client_running=m.client_running,
        subtrees=len(m.subtrees),
        files=sum(1 for e in m.entries if e.kind == "file"),
        bytes=sum(e.size for e in m.entries if e.kind == "file"),
    )


def _profile_report(p: profiles.Profile) -> ProfileReport:
    m = p.manifest
    scope = profiles.PRESET_SCOPE_NOTE if p.presets else profiles.SUBTREE_SCOPE_NOTE
    notes = [scope, profiles.SERVER_SIDE_NOTE, *profiles.linked_root_notes(m)]
    if m.client_running is not False:
        notes.append(
            "wowlab could not confirm the client was closed when this was saved: "
            "SavedVariables in it are as of the client's last logout or /reload."
        )
    return ProfileReport(
        profile=_profile_summary(p),
        subtrees=list(m.subtrees),
        excluded=list(m.excluded),
        entries=list(m.entries),
        notes=notes,
    )


def _print_profile(report: ProfileReport, *, saved: bool) -> None:
    s = report.profile
    _say(f"{'Saved profile' if saved else 'Profile'} {s.name} (snapshot {s.snapshot_id})")
    _say(f"  taken:    {s.created_at}")
    _say(f"  install:  {s.install_root}")
    _say(f"  flavor:   {s.flavor_folder} ({s.flavor_version or 'no version'})")
    if s.presets:
        _say(f"  presets:  {', '.join(s.presets)}")
    _say(f"  {s.subtrees} subtree(s), {s.files} file(s), {_bytes(s.bytes)}")
    for e in report.entries:
        if e.kind == "symlink":
            _say(f"  {e.path} -> {e.target} (link, recorded, not followed)")
        else:
            _say(f"  {e.path}  {_bytes(e.size)}")
    for n in report.notes:
        _say(n)


PresetOpt = Annotated[
    list[str] | None,
    typer.Option(
        "--preset",
        help="A preset: "
        + "; ".join(f"{name}: {p.summary}" for name, p in profiles.presets().items())
        + ". Repeat for more.",
    ),
]
SubtreeOpt = Annotated[
    list[str] | None,
    typer.Option(
        "--subtree",
        help="A file or folder relative to the flavor folder, under WTF/, Interface/ or "
        "Fonts/ and narrower than WTF/Account/ (for a whole area use snap create and snap "
        "restore); files added anywhere under it since the save, including in character "
        "folders created since, are deleted by an apply. "
        "Repeat for more.",
    ),
]
NameArg = Annotated[str, typer.Argument(metavar="NAME", help="The profile's name.")]


@profile_app.command("save")
@_handled
def profile_save(
    name: NameArg,
    preset: PresetOpt = None,
    subtree: SubtreeOpt = None,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
) -> None:
    """Save a profile: a snapshot of the preset's (or the subtrees') files, labelled
    with NAME (reads the install only).

    A preset covers every account and every character folder that exists now,
    not only the character you play, and applying the profile later deletes
    files created in those places since the save. An explicit --subtree is a
    whole file or folder: files added anywhere under it since are deleted by
    an apply. The lab-addon (Interface/AddOns/WowLab/ and WowLab.lua) is never
    in a profile. A saved subtree that is itself a link (a symlinked
    Interface/AddOns, say) is held as the link, not what is behind it, and the
    output says so. Action-bar contents and talents are not in these files (the
    server keeps them; not yet verified on this client), so no profile saves or
    restores them. JSON: ProfileReport."""
    if bool(preset) == bool(subtree):
        raise CliError("give --preset or --subtree (not both)", EXIT_USAGE)
    try:
        profiles.check_name(name)
    except profiles.ProfileError as exc:
        raise CliError(str(exc), EXIT_USAGE) from exc
    inst, chosen, lay = _open(root, flavor)
    try:
        selection = profiles.select(lay, preset_names=preset or (), subtrees=subtree or ())
    except profiles.ProfileError as exc:
        raise CliError(str(exc), EXIT_USAGE) from exc
    running = _client_running(inst, chosen)
    saved = profiles.save(
        snapshot.SnapshotStore(),
        Path(inst.root),
        chosen,
        name,
        selection,
        preset_names=preset or (),
        client_running=running,
    )
    if running is not False:
        _note(
            "wowlab could not confirm the client was closed: SavedVariables on disk are as of "
            "its last logout or /reload, and the client overwrites them at its next logout, "
            "/reload or exit."
        )
    report = _profile_report(saved)
    if json_out:
        _emit(report)
    else:
        _print_profile(report, saved=True)


@profile_app.command("list")
@_handled
def profile_list(json_out: JsonOpt = False) -> None:
    """Every profile, oldest first; names each damaged manifest and then exits 1.
    JSON: ProfileListReport."""
    found = profiles.listing(snapshot.SnapshotStore())
    report = ProfileListReport(
        profiles=[_profile_summary(p) for p in found.profiles], damaged=list(found.invalid)
    )
    if json_out:
        _emit(report)
    else:
        if not report.profiles:
            _say("No profiles.")
        for s in report.profiles:
            presets = f"  presets: {', '.join(s.presets)}" if s.presets else ""
            version = s.flavor_version or "no version"
            _say(
                f"{s.name}  {s.snapshot_id}  {s.flavor_folder}  {version}  {s.files} files{presets}"
            )
    for bad in report.damaged:
        _note(f"damaged manifest {bad.name}: {bad.reason}")
    if report.damaged:
        raise typer.Exit(EXIT_ERROR)


@profile_app.command("show")
@_handled
def profile_show(name: NameArg, json_out: JsonOpt = False) -> None:
    """One profile and every file in it, with the notes `save` gave (a saved
    subtree that was a link is held as the link). JSON: ProfileReport."""
    report = _profile_report(profiles.find(snapshot.SnapshotStore(), name))
    if json_out:
        _emit(report)
    else:
        _print_profile(report, saved=False)


@profile_app.command("delete")
@_handled
def profile_delete(name: NameArg, json_out: JsonOpt = False) -> None:
    """Delete a profile. Its snapshot stays in the store (snapshots are immutable),
    relabelled `deleted-profile:NAME`. Changes nothing in the install.
    JSON: ProfileDeleteReport."""
    relabelled = profiles.delete(snapshot.SnapshotStore(), name)
    report = ProfileDeleteReport(name=name, relabelled=[_summary(m) for m in relabelled])
    if json_out:
        _emit(report)
        return
    for m in relabelled:
        _say(f"Deleted profile {name}; snapshot {m.id} stays in the store, labelled {m.label!r}.")


def _print_apply_plan(show: Callable[[str], None], plan: profiles.ApplyPlan) -> None:
    for item in plan.plan:
        line = _plan_line(item)
        if item.after is None:
            line += "  (added since the profile was saved)"
        show(line)
    if plan.skipped:
        show(
            f'  Skipped {len(plan.skipped)} file(s) wowlab leaves alone (file-map Edit "no": '
            "client-written backups, Blizzard_* folders, file-browser metadata):"
        )
        for sk in plan.skipped:
            show(f"    {sk.path}  [{sk.entry_id}]")
    if plan.left:
        show(f"  Left alone ({len(plan.left)} path(s)):")
        for lp in plan.left:
            show(f"    {lp.path}  ({lp.reason})")
    if plan.cache_files:
        show("  *-cache files in this change:")
        for c in plan.cache_files:
            show(f"    {c.path}: {c.note}")
    for n in plan.notes[1:]:  # the scope note (first) is printed under the header
        show(n)


@profile_app.command("apply")
@_handled
def profile_apply(
    name: NameArg,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Print the plan and change nothing.")
    ] = False,
    yes: YesOpt = False,
    root: RootOpt = None,
    json_out: JsonOpt = False,
) -> None:
    """Return the profile's files to their saved bytes, through the write gate: the
    client must be closed, a pre-write snapshot is taken first, and `wowlab undo`
    reverses it.

    A profile covers every account and every character folder that existed when
    it was saved, not only the character you play. Its files are written back,
    and files created in those places since the save are deleted: for example
    macros or character-specific key bindings made since on any of those
    characters, and with the addons preset, addons installed since (code and
    settings). Addon updates made since are undone. `--dry-run` shows the plan
    without asking. Files whose file-map Edit is `no` (client-written backups,
    Blizzard_* folders, file-browser metadata) are left alone. Every *-cache*
    file is listed: the server may replace it at your next login, so the result
    is proven only by logging in.

    The lab-addon (Interface/AddOns/WowLab/ and WowLab.lua) is always left
    alone, and so is anything the write gate will not write or delete (an
    executable); both are listed. Action-bar contents and talents are not in
    these files (the server keeps them; not yet verified on this client), so no
    profile saves or restores them. JSON: ProfileApplyReport."""
    store = snapshot.SnapshotStore()
    profile = profiles.find(store, name)
    manifest = profile.manifest
    inst, _ = _discover(root)
    match = [f for f in inst.flavors if f.folder == manifest.flavor_folder]
    if manifest.flavor_folder is None or not match:
        raise CliError(
            f"profile {name} is of flavor folder {manifest.flavor_folder!r}, which "
            f"{inst.root} does not have (flavors: {_flavor_list(inst)}). Flavor folders are "
            "renamed between beta and launch; wowlab applies a profile only to the folder it "
            "was saved from."
        )
    chosen = match[0]
    plan = profiles.plan_apply(profile, chosen, Path(inst.root), store=store)
    prompting = json_out and not yes and not dry_run
    show = _say if not json_out else _note

    def report(*, applied: bool, transaction: str | None = None) -> ProfileApplyReport:
        notes = [profiles.SERVER_SIDE_NOTE, *plan.notes]
        if plan.plan:
            notes.append(profiles.LOGIN_NOTE)
        return ProfileApplyReport(
            profile=name,
            snapshot_id=manifest.id,
            flavor_path=str(chosen.path),
            snapshot_version=manifest.flavor_version,
            flavor_version=chosen.version,
            plan=list(plan.plan),
            added=list(plan.added),
            skipped=list(plan.skipped),
            left=list(plan.left),
            cache_files=list(plan.cache_files),
            dry_run=dry_run,
            applied=applied,
            transaction=transaction,
            notes=notes,
        )

    if (not json_out or (prompting and plan.plan)) and (plan.plan or plan.skipped or plan.left):
        show(
            f"Apply profile {name} (snapshot {manifest.id}) to {chosen.path}: "
            f"{len(plan.plan)} change(s)"
        )
        show(f"  {plan.notes[0]}")
        if manifest.flavor_version != chosen.version:
            show(
                f"  Saved on version {_version(manifest.flavor_version)}; {chosen.folder} is "
                f"now on {_version(chosen.version)}."
            )
        _print_apply_plan(show, plan)
        if plan.plan:
            show(profiles.LOGIN_NOTE)
    if not plan.plan:
        if json_out:
            _emit(report(applied=False))
        elif plan.skipped or plan.left:
            _say("Nothing to apply but the files left alone; nothing was changed.")
        else:
            _say(f"Nothing to apply: the files of profile {name} already match it.")
        return
    if dry_run:
        if json_out:
            _emit(report(applied=False))
        else:
            _say("Dry run: nothing was changed.")
        return
    try:
        _confirm("Apply these changes?", yes)
    except typer.Exit:
        if json_out:
            _emit(report(applied=False))
        raise
    record = profiles.apply(plan, chosen, store=store)
    done = report(applied=True, transaction=record)
    if json_out:
        _emit(done)
        return
    _say(
        f"Applied profile {name}: {len(plan.plan)} change(s). `wowlab undo` puts back what "
        "was there before."
    )
    if profiles.MACROS_NOTE in plan.notes:
        _say(profiles.MACROS_NOTE)
    _say(profiles.LOGIN_NOTE)


# ─── addon ───────────────────────────────────────────────────────────────────


class AddonInstallReport(_Out):
    """`wowlab addon install lab --json`: the plan, and with `--yes` what was done."""

    addon: str  # the addon folder's name
    flavor_path: str
    flavor_version: str  # the discovered version the interface was derived from
    interface: int  # what the installed TOC's ## Interface: says
    source: str  # the source folder read
    files: list[str]  # every file of the addon, relative to the flavor folder
    plan: list[guard.PlanItem]  # new or changed files, then deletes of files not in the sources
    unchanged: list[str]  # files that already hold the bytes install writes
    left: list[addoninstall.LeftPath]  # entries under the folder left alone
    dry_run: bool
    applied: bool
    transaction: str | None  # the journal record of the install
    notes: list[str]  # LOAD_NOTE only when applied


class AddonRemoveReport(_Out):
    """`wowlab addon remove lab --json`: the plan, and with `--yes` what was done."""

    addon: str
    flavor_path: str
    plan: list[guard.PlanItem]  # deletes only
    left: list[addoninstall.LeftPath]
    dry_run: bool
    applied: bool
    transaction: str | None  # the journal record of the removal
    notes: list[str]


AddonArg = Annotated[
    str,
    typer.Argument(metavar="NAME", help="The addon: `lab` (the lab-addon) is the only one."),
]
DryRunOpt = Annotated[bool, typer.Option("--dry-run", help="Print the plan and change nothing.")]


def _lab_addon(name: str) -> None:
    if name != addoninstall.LAB_ADDON:
        raise CliError(
            f"no addon {name!r}: `{addoninstall.LAB_ADDON}` (the lab-addon) is the only one "
            "wowlab installs",
            EXIT_USAGE,
        )


def _print_left(show: Callable[[str], None], left: Sequence[addoninstall.LeftPath]) -> None:
    if left:
        show(f"  Left alone ({len(left)} path(s)):")
        for lp in left:
            show(f"    {lp.path}  ({lp.reason})")


@addon_app.command("install")
@_handled
def addon_install(
    name: AddonArg,
    dry_run: DryRunOpt = False,
    yes: YesOpt = False,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
) -> None:
    """Copy the lab-addon from this checkout's lab/addon/WowLab/ into the flavor's
    Interface/AddOns/WowLab/, through the write gate: the client must be closed, a
    pre-write snapshot is taken first, and `wowlab undo` reverses it.

    The TOC's ## Interface: is filled from the flavor's discovered version by the
    patch-number rule; after a client update that changes the first three parts of
    the version, run this again. Only WowLab.toc and the .lua files it lists are
    copied. Over an existing copy only changed files are written, and files not in
    the sources are deleted. JSON: AddonInstallReport."""
    _lab_addon(name)
    inst, _ = _discover(root)
    chosen = _select_flavor(inst, flavor)
    plan = addoninstall.plan_install(chosen)
    prompting = json_out and not yes and not dry_run
    show = _say if not json_out else _note
    target = addoninstall.ADDON_FOLDER

    def report(*, applied: bool, transaction: str | None = None) -> AddonInstallReport:
        return AddonInstallReport(
            addon=addoninstall.ADDON_NAME,
            flavor_path=str(chosen.path),
            flavor_version=plan.flavor_version,
            interface=plan.interface,
            source=plan.source,
            files=list(plan.files),
            plan=list(plan.plan),
            unchanged=list(plan.unchanged),
            left=list(plan.left),
            dry_run=dry_run,
            applied=applied,
            transaction=transaction,
            # The load note is for after an install that happened, as in the text.
            notes=[n for n in plan.notes if applied or n != addoninstall.LOAD_NOTE],
        )

    if not json_out or (prompting and plan.plan):
        show(
            f"Install the lab-addon into {chosen.path / target}: {len(plan.plan)} change(s), "
            f"## Interface: {plan.interface}"
        )
        show(f"  from {plan.source}")
        for item in plan.plan:
            line = _plan_line(item)
            if item.after is None:
                line += "  (not part of the lab-addon's sources)"
            show(line)
        if plan.unchanged and plan.plan:
            show(f"  {len(plan.unchanged)} file(s) already up to date.")
        _print_left(show, plan.left)
        # The load note is for after an install that happened.
        for n in plan.notes:
            if n != addoninstall.LOAD_NOTE:
                show(n)
    if not plan.plan:
        if json_out:
            _emit(report(applied=False))
        else:
            _say(
                f"Nothing to install: {target}/ already holds this lab-addon with "
                f"## Interface: {plan.interface}."
            )
        return
    if dry_run:
        if json_out:
            _emit(report(applied=False))
        else:
            _say("Dry run: nothing was changed.")
        return
    try:
        _confirm("Install it?", yes)
    except typer.Exit:
        if json_out:
            _emit(report(applied=False))
        raise
    record = addoninstall.install(plan, chosen)
    done = report(applied=True, transaction=record)
    if json_out:
        _emit(done)
        return
    _say(
        f"Installed the lab-addon: {len(plan.plan)} change(s). `wowlab undo` puts back what "
        "was there before."
    )
    if addoninstall.LOAD_NOTE in plan.notes:
        _say(addoninstall.LOAD_NOTE)


@addon_app.command("remove")
@_handled
def addon_remove(
    name: AddonArg,
    dry_run: DryRunOpt = False,
    yes: YesOpt = False,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
) -> None:
    """Delete the lab-addon's files from the flavor's Interface/AddOns/WowLab/, through
    the write gate: the client must be closed, a pre-write snapshot is taken first,
    and `wowlab undo` puts them back. A file the gate will not delete refuses the
    whole removal (exit 3).

    Its SavedVariables (WowLab.lua and WowLab.lua.bak under WTF/, holding WowLabDB
    and each character's WowLabCharDB) are never touched: they are the captures.
    The folder stays; the gate deletes files, not folders. JSON: AddonRemoveReport."""
    _lab_addon(name)
    inst, _ = _discover(root)
    chosen = _select_flavor(inst, flavor)
    plan = addoninstall.plan_remove(chosen)
    prompting = json_out and not yes and not dry_run
    show = _say if not json_out else _note
    target = addoninstall.ADDON_FOLDER

    def report(*, applied: bool, transaction: str | None = None) -> AddonRemoveReport:
        return AddonRemoveReport(
            addon=addoninstall.ADDON_NAME,
            flavor_path=str(chosen.path),
            plan=list(plan.plan),
            left=list(plan.left),
            dry_run=dry_run,
            applied=applied,
            transaction=transaction,
            notes=list(plan.notes),
        )

    if not json_out or (prompting and plan.plan):
        if plan.plan or plan.left:
            show(f"Remove the lab-addon from {chosen.path / target}: {len(plan.plan)} file(s)")
        for item in plan.plan:
            show(_plan_line(item))
        _print_left(show, plan.left)
        for n in plan.notes:
            show(n)
    if not plan.plan:
        if json_out:
            _emit(report(applied=False))
        else:
            _say(f"Nothing to remove: no lab-addon files in {target}/.")
        return
    if dry_run:
        if json_out:
            _emit(report(applied=False))
        else:
            _say("Dry run: nothing was changed.")
        return
    try:
        _confirm("Remove it?", yes)
    except typer.Exit:
        if json_out:
            _emit(report(applied=False))
        raise
    record = addoninstall.remove(plan, chosen)
    done = report(applied=True, transaction=record)
    if json_out:
        _emit(done)
        return
    _say(f"Removed the lab-addon: {len(plan.plan)} file(s) deleted. `wowlab undo` puts them back.")


# ─── char ────────────────────────────────────────────────────────────────────


_CHAR_NOTES = [
    "WowLab.lua is written at logout, /reload or a clean exit (a crash writes nothing): "
    "this is the character as of that save, not the session in progress.",
    "The addon records no time; the time shown is the file's modification time.",
]


class CharShowReport(_Out):
    """`wowlab char show --json`: the character's `WowLabCharDB` as the
    schema-1 model reads it (unknown keys kept), and the account `WowLabDB`."""

    flavor_folder: str
    account: str
    character: str  # "<realm folder>/<character folder>"
    chosen_by: Literal["--character", "latest"]
    file: str  # relative to the flavor folder
    mtime_ns: int
    account_file: str | None
    account_record: labaddon.AccountDBV1 | None
    record: labaddon.CharDBV1
    skip_known: list[str]  # section keys in `skip` this reader knows
    skip_ignored: list[str]  # the rest of `skip`, kept in `record` and ignored
    customization_loads_ago: int | None
    unknown_keys: list[str]
    notes: list[str]


def _is_lab_file(f: layout.SavedVariablesFile) -> bool:
    return (
        not f.backup
        and f.addon is not None
        and f.addon.casefold() == labaddon.ADDON_NAME.casefold()
    )


def _local_time(mtime_ns: int) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S %z", time.localtime(mtime_ns // 1_000_000_000))


@char_app.command("show")
@_handled
def char_show(
    character: CharacterOpt = None,
    account: AccountOpt = None,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
) -> None:
    """What the lab-addon recorded for one character, from its WowLab.lua.
    Without --character, the character whose WowLab.lua was written last.
    JSON: CharShowReport."""
    _, chosen, lay = _open(root, flavor)
    acct = _select_account(lay, account)
    files = [f for f in lay.saved_variables() if f.account == acct.folder and _is_lab_file(f)]
    target, how, tie = _lab_char_file(acct, files, character)
    record = labaddon.read_char(lay.flavor_path / target.path)
    account_files = [f for f in files if f.scope == "account"]
    account_file = account_files[0] if account_files else None
    account_record = (
        labaddon.read_account(lay.flavor_path / account_file.path) if account_file else None
    )
    known, ignored = labaddon.skip_known(record)
    report = CharShowReport(
        flavor_folder=chosen.folder,
        account=acct.folder,
        character=f"{target.realm_folder}/{target.character_folder}",
        chosen_by=how,
        file=target.path,
        mtime_ns=target.mtime_ns,
        account_file=account_file.path if account_file else None,
        account_record=account_record,
        record=record,
        skip_known=known,
        skip_ignored=ignored,
        customization_loads_ago=labaddon.customization_loads_ago(record),
        unknown_keys=labaddon.unknown_keys(record),
        notes=_CHAR_NOTES,
    )
    if json_out:
        _emit(report)
        return
    _say(f"Character: {report.character} in account {acct.folder}{_picked_words(how, tie)}")
    _say(f"File: {target.path}")
    _say(f"Written: {_local_time(target.mtime_ns)} (the file's modification time)")
    for line in labaddon.describe(record):
        _say(line)
    _say()
    if account_record is None:
        _say(f"Account {labaddon.ACCOUNT_VARIABLE}: no account {labaddon.ADDON_NAME}.lua")
    else:
        for line in labaddon.describe_account(account_record):
            _say(line)
    for note in _CHAR_NOTES:
        _say(note)


def _picked_words(how: Literal["--character", "latest"], tie: str) -> str:
    """How `char show` and `looks import-char` say which file they took."""
    if how != "latest":
        return ""
    return (
        " (the WowLab.lua with the newest modification time; a wowlab restore also sets it"
        f"{tie}; choose another with --character)"
    )


def _lab_char_file(
    acct: layout.Account,
    files: Sequence[layout.SavedVariablesFile],
    character: str | None,
) -> tuple[layout.SavedVariablesFile, Literal["--character", "latest"], str]:
    """The character `WowLab.lua` to read: the one `--character` names, else
    the newest by modification time (ties by path, said in the third value)."""
    char_files = [f for f in files if f.scope == "character"]
    how: Literal["--character", "latest"]
    tie = ""
    if character is not None:
        char = _select_character(acct, character)
        mine = [
            f
            for f in char_files
            if f.realm_folder == char.realm_folder and f.character_folder == char.folder
        ]
        if not mine:
            having = (
                ", ".join(f"{f.realm_folder}/{f.character_folder}" for f in char_files) or "none"
            )
            raise CliError(
                f"no {labaddon.ADDON_NAME}.lua in {char.realm_folder}/{char.folder}. Character "
                f"folders in account {acct.folder} that have one: {having}. If none is listed: "
                "install the lab-addon (wowlab addon install lab), log in on the character, "
                "then log out or /reload."
            )
        target, how = mine[0], "--character"
    else:
        if not char_files:
            raise CliError(
                f"no character in account {acct.folder} has a {labaddon.ADDON_NAME}.lua "
                "(install the lab-addon with `wowlab addon install lab`, log in, then log out "
                "or /reload)"
            )
        ranked = sorted(char_files, key=lambda f: (f.mtime_ns, f.path), reverse=True)
        target = ranked[0]
        if len(ranked) > 1 and ranked[1].mtime_ns == target.mtime_ns:
            tie = (
                f"; tied with {ranked[1].realm_folder}/{ranked[1].character_folder} on that "
                "time, taken by path order"
            )
        how = "latest"
    return target, how, tie


# ─── looks ───────────────────────────────────────────────────────────────────

_PLAYABLE_NOTE = (
    "Flagged playable by the build's ChrRaces rows (a PlayableRaceBit, not NPC-only); "
    "not a claim about what a server lets anyone create. Body types are numbered as the "
    "tables number them (ChrRaceXChrModel.Sex 0 and 1); the game's own screens may number "
    "them differently [verify]."
)
_EXPORTED_ONLY = (
    "Checked against build {version}'s exported tables only: not what a server allows, not "
    "hotfixes the server sends (Cache/ADB), and not what this account has unlocked."
)
_HOTFIX_HINT = (
    "is not in build {version}'s tables: check the id with `wowlab looks options`; an id "
    "read from the game may come from a hotfix the exported tables lack"
)
_IMPORTED_REMARK = (
    "Look {name} was imported from a character's lab-addon customization record (wowlab "
    "looks import-char): the choices the addon had last recorded in the barber shop before "
    "the import. It may miss a change applied during that visit [verify], and any change since."
)
_IMPORTED_BUILD_REMARK = (
    "Look {name} comes from a record the lab-addon made on client build {client}."
)
_IMPORTED_UNKNOWN = (
    " (recorded by client {client}, not the build of these tables: the id may exist only in "
    "that build, or come from a hotfix)"
)
_MODEL_HOTFIX = " (possibly a hotfix)"
_ID = re.compile(r"[0-9]{1,9}")
_ALLIANCE_WORDS = {0: "Alliance", 1: "Horde", 2: "neither faction"}
_SEX_WORDS = {"male": 0, "female": 1}


class LooksRace(_Out):
    id: int
    name: str
    client_file_string: str
    alliance: int  # ChrRaces.Alliance: 0 Alliance, 1 Horde, 2 neither
    flagged_playable: bool
    body_types: list[looks.BodyType]  # body_type as ChrRaceXChrModel.Sex


class LooksRacesReport(_Out):
    """`wowlab looks races --json`."""

    build: str
    races: list[LooksRace]
    notes: list[str]


class LooksChoice(_Out):
    id: int
    name: str
    # The model's check of a look holding only this choice, less the findings
    # every choice of the option shares (those are the option's `notes`).
    refusals: list[looks.Finding]
    notes: list[looks.Finding]


class LooksOption(_Out):
    id: int
    name: str
    chr_model_id: int
    category: str | None
    form_or_pet: bool  # on a model no race uses (druid form, demon, pet) [verify]
    notes: list[looks.Finding]  # findings every choice shares (two or more choices)
    choices: list[LooksChoice]


class LooksBodyType(_Out):
    body_type: int
    chr_model_id: int
    options: list[LooksOption]


class LooksOptionsReport(_Out):
    """`wowlab looks options --json`."""

    build: str
    race: LooksRace
    class_id: int | None
    class_name: str | None
    body_types: list[LooksBodyType]
    notes: list[str]


class LooksChoiceRef(_Out):
    option_id: int
    option_name: str | None  # None: the option is unknown to the build
    choice_id: int
    # None: unknown to the build, or not a choice of this option (then
    # `choice_of_option` says whose it is); "" when the table names none.
    choice_name: str | None
    # The option the build files this choice under, when that is not
    # `option_id`; None when the choice is this option's or unknown to the build.
    choice_of_option: int | None = None


class LookReport(_Out):
    """`wowlab looks save --json` and `wowlab looks show NAME --json`.

    `path` is None when `save` refused the look (nothing was written)."""

    name: str
    path: str | None
    saved_build: str | None  # the build whose tables checked it when it was saved
    # typed (`save`) or imported (`import-char`); decides the unknown-id wording
    origin: Literal["typed", "imported"] = "typed"
    build: str  # the build whose tables checked it now
    race_id: int
    race_name: str | None
    body_type: int
    class_id: int | None
    class_name: str | None
    choices: list[LooksChoiceRef]
    refused: bool
    refusals: list[looks.Finding]
    notes: list[looks.Finding]
    remarks: list[str]


class LookSummary(_Out):
    name: str
    saved_build: str
    race_id: int
    race_name: str | None
    body_type: int
    class_id: int | None
    choices: int
    refused: bool
    refusals: int
    notes: int


class LooksListReport(_Out):
    """`wowlab looks show --json` (no name). The command exits 1 when `damaged`
    is not empty. `build` is None when there is no look to check."""

    directory: str
    build: str | None
    looks: list[LookSummary]
    damaged: list[lookstore.DamagedLook]
    remarks: list[str]


class LooksDifference(_Out):
    option_id: int
    option_name: str | None
    a: LooksChoiceRef | None  # None: the look does not set this option
    b: LooksChoiceRef | None


class LooksCompareReport(_Out):
    """`wowlab looks compare --json`: both looks checked against one build."""

    build: str
    a: LookReport
    b: LookReport
    same_race: bool
    same_body_type: bool
    same_class: bool
    same: list[LooksChoiceRef]  # options both looks set to the same choice
    different: list[LooksDifference]  # set to different choices, or by one look only
    remarks: list[str]


LooksBuildOpt = Annotated[
    str | None,
    typer.Option(
        "--build",
        help="Full version string whose tables to use. Default: the flavor's version; "
        "with no install found, the build a saved look was checked against, or the one "
        "build whose customization tables are cached.",
    ),
]
SexOpt = Annotated[
    str | None,
    typer.Option(
        "--sex",
        help="Body type as the tables number it (ChrRaceXChrModel.Sex): 0 or 1; "
        "'male' and 'female' are read as 0 and 1.",
    ),
]
ClassOpt = Annotated[
    str | None,
    typer.Option("--class", help="Class id or name, as the build's ChrClasses has it."),
]
LookNameArg = Annotated[str, typer.Argument(metavar="NAME", help="The look's name.")]


def _cached_look_builds(data: gamedata.GameData) -> list[str]:
    """Builds whose every customization table is in the cache (a listing only)."""
    tables = data.cache_dir / "tables"
    if not tables.is_dir():
        return []
    found: list[str] = []
    for entry in sorted(tables.iterdir()):
        try:
            if all(data.table_path(name, entry.name).is_file() for name in looks.REQUIRED_TABLES):
                found.append(entry.name)
        except ValueError:  # not a build folder
            continue
    return found


def _looks_build(
    data: gamedata.GameData,
    build: str | None,
    root: Path | None,
    flavor: str | None,
    saved_builds: Sequence[str] = (),
) -> tuple[str, list[str]]:
    """The build to check against, and a remark when it did not come from
    `--build` or the install (L6: nothing here names a build)."""
    if build is not None:
        return build, []
    try:
        return _build_for(None, root, flavor), []
    except install.InstallNotFoundError as exc:
        saved = sorted(set(saved_builds))
        if len(saved) == 1:
            return saved[0], [
                f"No install found; checked against build {saved[0]}, the build the look "
                "was saved against (--build chooses another)."
            ]
        cached = _cached_look_builds(data)
        if len(cached) == 1:
            return cached[0], [
                f"No install found; using build {cached[0]}, the one build whose "
                "customization tables are cached (--build chooses another)."
            ]
        have = f" (cached: {', '.join(cached)})" if cached else ""
        raise CliError(f"{exc}; pass --build with a full version string{have}") from exc


def _load_model(data: gamedata.GameData, build: str) -> looks.Customizations:
    _check_table_key(data, looks.REQUIRED_TABLES[0], build)
    try:
        return looks.Customizations.from_gamedata(data, build)
    except looks.LooksDataError as exc:
        raise CliError(f"the customization tables of build {build} cannot be read: {exc}") from exc


def _race_out(race: looks.Race) -> LooksRace:
    return LooksRace(
        id=race.id,
        name=race.name,
        client_file_string=race.client_file_string,
        alliance=race.alliance,
        flagged_playable=race.flagged_playable,
        body_types=list(race.body_types),
    )


def _race_label(race: looks.Race) -> str:
    return f"{race.name} ({race.id})"


def _as_id(text: str) -> int | None:
    """An id typed on the command line: ASCII digits, nine at most; else None."""
    stripped = text.strip()
    return int(stripped) if _ID.fullmatch(stripped) else None


def _faction(alliance: int) -> str:
    return _ALLIANCE_WORDS.get(alliance, f"Alliance column {alliance}")


def _resolve_race(model: looks.Customizations, text: str) -> tuple[looks.Race, list[str]]:
    """The race `text` names, and a remark when the playable flag chose between rows."""
    playable = ", ".join(_race_label(r) for r in model.playable_races())
    typed = text.strip()
    race_id = _as_id(typed)
    if race_id is not None:
        race = model.races.get(race_id)
        if race is None:
            raise CliError(
                f"no race {typed} in build {model.build}'s ChrRaces (flagged playable: {playable})",
                EXIT_USAGE,
            )
        return race, []
    folded = typed.casefold()
    matches = [r for _, r in sorted(model.races.items()) if r.name.casefold() == folded] or [
        r for _, r in sorted(model.races.items()) if r.client_file_string.casefold() == folded
    ]
    remarks: list[str] = []
    if len(matches) > 1:
        flagged = [r for r in matches if r.flagged_playable]
        if len(flagged) == 1:
            others = [r for r in matches if not r.flagged_playable]
            remarks.append(
                f"{typed!r} also names {', '.join(_race_label(r) for r in others)}, not flagged "
                f"playable; using {_race_label(flagged[0])}. Give "
                f"{' or '.join(str(r.id) for r in others)} for that row."
            )
        matches = flagged or matches
    if len(matches) == 1:
        return matches[0], remarks
    if not matches:
        raise CliError(
            f"no race {typed!r} in build {model.build}'s ChrRaces (flagged playable: {playable})",
            EXIT_USAGE,
        )
    listed = ", ".join(f"{r.name} ({r.id}, {_faction(r.alliance)})" for r in matches)
    shared_models = len({r.body_types for r in matches}) == 1
    factions = len({r.alliance for r in matches}) == len(matches)
    if shared_models and factions:
        count = "two" if len(matches) == 2 else str(len(matches))
        raise CliError(
            f"race {typed!r} matches {listed}: one race's {count} faction rows, sharing "
            "models; give the id (race-masked choices are checked against it)",
            EXIT_USAGE,
        )
    raise CliError(f"race {typed!r} matches {listed}; give the id", EXIT_USAGE)


def _resolve_class(model: looks.Customizations, text: str | None) -> int | None:
    if text is None:
        return None
    typed = _as_id(text)
    if typed is not None:
        return typed
    folded = text.strip().casefold()
    for class_id, player_class in sorted(model.classes.items()):
        if player_class.name.casefold() == folded:
            return class_id
    known = ", ".join(f"{c.name} ({i})" for i, c in sorted(model.classes.items()))
    raise CliError(f"no class {text!r} in build {model.build}'s ChrClasses ({known})", EXIT_USAGE)


def _parse_sex(text: str | None) -> int | None:
    if text is None:
        return None
    word = text.strip().casefold()
    if word in _SEX_WORDS:
        return _SEX_WORDS[word]
    number = _as_id(word)
    if number is not None:
        return number
    raise CliError(f"--sex takes 0, 1, male or female, not {text!r}", EXIT_USAGE)


def _parse_choices(pairs: Sequence[str]) -> dict[int, int]:
    out: dict[int, int] = {}
    for pair in pairs:
        option, sep, choice = pair.partition("=")
        option_id, choice_id = _as_id(option), _as_id(choice)
        if not sep or option_id is None or choice_id is None:
            raise CliError(
                f"--choice takes OPTION=CHOICE with two ids (digits 0-9, nine at most), as "
                f"`wowlab looks options` lists them, not {pair!r}",
                EXIT_USAGE,
            )
        if option_id in out:
            raise CliError(f"option {option_id} is given twice", EXIT_USAGE)
        out[option_id] = choice_id
    return out


def _class_name(model: looks.Customizations, class_id: int | None) -> str | None:
    if class_id is None:
        return None
    found = model.classes.get(class_id)
    return found.name if found else None


def _choice_ref(model: looks.Customizations, option_id: int, choice_id: int) -> LooksChoiceRef:
    option = model.options.get(option_id)
    choice = model.choices.get(choice_id)
    return LooksChoiceRef(
        option_id=option_id,
        option_name=option.name if option else None,
        choice_id=choice_id,
        # A choice of another option is not this option's choice: no name here,
        # and whose it is (the check reports the mismatch as a refusal).
        choice_name=choice.name if choice is not None and choice.option_id == option_id else None,
        choice_of_option=(
            choice.option_id if choice is not None and choice.option_id != option_id else None
        ),
    )


def _ref_choice_text(ref: LooksChoiceRef) -> str:
    """The choice half of a reference, as `save`, `show` and `compare` print it."""
    if ref.choice_of_option is not None:
        return f"{ref.choice_id} (a choice of option {ref.choice_of_option})"
    if ref.choice_name is None:
        return f"{ref.choice_id} (unknown to this build)"
    return f"{ref.choice_id} {ref.choice_name!r}" if ref.choice_name else str(ref.choice_id)


def _look_report(
    model: looks.Customizations,
    saved: lookstore.SavedLook,
    path: Path | None,
    remarks: Sequence[str] = (),
    *,
    exported_only: bool = True,
    imported_remark: bool = True,
) -> LookReport:
    """`exported_only=False` leaves the tables-only remark to the caller
    (`compare` states it once, at its top level). An id the build lacks is
    worded by the look's origin (M11-23): a typed id as a thing to check, an
    imported one as the model words it, "(possibly a hotfix)"."""
    look = saved.look
    verdict = model.check(look)
    race = model.races.get(look.race_id)
    notes = list(remarks)
    if saved.origin == "imported" and path is not None and imported_remark:
        notes.append(_IMPORTED_REMARK.format(name=look.name))
        if saved.recorded_client_build is not None:
            notes.append(
                _IMPORTED_BUILD_REMARK.format(name=look.name, client=saved.recorded_client_build)
            )
    if saved.saved_build != model.build:
        notes.append(
            f"Saved against build {saved.saved_build}; checked here against build {model.build}."
        )
    if exported_only:
        notes.append(_EXPORTED_ONLY.format(version=model.build))
    report = LookReport(
        name=look.name,
        path=str(path) if path is not None else None,
        saved_build=saved.saved_build if path is not None else None,
        origin=saved.origin,
        build=model.build,
        race_id=look.race_id,
        race_name=race.name if race else None,
        body_type=look.body_type,
        class_id=look.class_id,
        class_name=_class_name(model, look.class_id),
        choices=[_choice_ref(model, o, c) for o, c in look.choices.items()],
        refused=verdict.refused,
        refusals=list(verdict.refusals),
        notes=list(verdict.notes),
        remarks=notes,
    )
    if saved.origin == "typed":
        return _save_wording(model, report)
    return _imported_wording(model, report, saved.recorded_client_build)


def _imported_wording(
    model: looks.Customizations, report: LookReport, client: str | None
) -> LookReport:
    """An imported look keeps the model's "(possibly a hotfix)", unless the
    recording client's build is known and differs from the checking build:
    then an option or choice id from the record may exist only in that build
    (either direction; M11-23 review, P4 and D1). A requirement id the
    tables name is theirs, not the record's, and keeps the model's words."""
    if client is None or client == model.build:
        return report
    tail = _IMPORTED_UNKNOWN.format(client=client)

    def from_record(f: looks.Finding) -> bool:
        # Only the option and choice ids came from the record; a requirement
        # id an option or choice names comes from the tables themselves.
        return (
            f.option_id is not None and f.message.startswith(f"option {f.option_id} is unknown")
        ) or (f.choice_id is not None and f.message.startswith(f"choice {f.choice_id} is unknown"))

    def reworded(f: looks.Finding) -> looks.Finding:
        if (
            f.kind is not looks.FindingKind.UNKNOWN_TO_BUILD
            or not f.message.endswith(_MODEL_HOTFIX)
            or not from_record(f)
        ):
            return f
        return f.model_copy(update={"message": f.message[: -len(_MODEL_HOTFIX)] + tail})

    return report.model_copy(update={"notes": [reworded(f) for f in report.notes]})


def _save_wording(model: looks.Customizations, report: LookReport) -> LookReport:
    """A typed look (`save`, and `show`/`compare` of a look saved that way)
    words an id the build lacks as a thing to check; an imported look keeps
    the model's "(possibly a hotfix)" (M11-23)."""
    hint = _HOTFIX_HINT.format(version=model.build)

    def reworded(f: looks.Finding) -> looks.Finding:
        if f.kind is not looks.FindingKind.UNKNOWN_TO_BUILD:
            return f
        if f.option_id is not None and f.message.startswith(f"option {f.option_id} is unknown"):
            return f.model_copy(update={"message": f"option {f.option_id} {hint}"})
        option = model.options.get(f.option_id) if f.option_id is not None else None
        if (
            option is not None
            and f.choice_id is not None
            and f.message.startswith(f"choice {f.choice_id} is unknown")
        ):
            return f.model_copy(
                update={
                    "message": f"choice {f.choice_id} of option {option.name!r} ({option.id}) "
                    f"{hint}"
                }
            )
        return f

    return report.model_copy(update={"notes": [reworded(f) for f in report.notes]})


def _ref_text(ref: LooksChoiceRef) -> str:
    option = (
        f"{ref.option_name} ({ref.option_id})"
        if ref.option_name is not None
        else f"option {ref.option_id} (unknown to this build)"
    )
    return f"{option} = {_ref_choice_text(ref)}"


def _print_findings(
    refusals: Sequence[looks.Finding], notes: Sequence[looks.Finding], pad: str
) -> None:
    for f in refusals:
        _say(f"{pad}refused: {f.message}")
    for f in notes:
        _say(f"{pad}note: {f.message}")


def _print_look(report: LookReport, *, heading: str) -> None:
    _say(f"{heading} {report.name} (checked against build {report.build})")
    if report.path is not None:
        _say(f"  file:      {report.path}")
    race = f"{report.race_name} ({report.race_id})" if report.race_name else f"{report.race_id}"
    _say(f"  race:      {race}, body type {report.body_type}")
    if report.class_id is None:
        _say("  class:     not given (class-restricted choices are noted, not refused)")
    else:
        _say(f"  class:     {report.class_name or 'not in this build'} ({report.class_id})")
    _say(f"  choices:   {len(report.choices)}")
    for ref in report.choices:
        _say(f"    {_ref_text(ref)}")
    if report.refused:
        _say(
            f"  verdict:   refused ({len(report.refusals)} reason(s)), {len(report.notes)} note(s)"
        )
    else:
        _say(f"  verdict:   not refused, {len(report.notes)} note(s)")
    _print_findings(report.refusals, report.notes, "    ")
    for remark in report.remarks:
        _say(remark)


@looks_app.command("races")
@_handled
def looks_races(
    build: LooksBuildOpt = None,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
) -> None:
    """Races the build's tables flag as playable, with their body types.
    JSON: LooksRacesReport."""
    with _open_gamedata() as data:
        version, remarks = _looks_build(data, build, root, flavor)
        model = _load_model(data, version)
    report = LooksRacesReport(
        build=version,
        races=[_race_out(r) for r in model.playable_races()],
        notes=[_PLAYABLE_NOTE, *remarks],
    )
    if json_out:
        _emit(report)
        return
    _say(f"Races flagged playable in build {version}'s ChrRaces:")
    for r in report.races:
        side = _ALLIANCE_WORDS.get(r.alliance, f"Alliance column {r.alliance}")
        bodies = ", ".join(f"{b.body_type} (model {b.chr_model_id})" for b in r.body_types)
        _say(f"  {r.id:>4}  {r.name}  [{side}]  body types {bodies or 'none'}")
    for n in report.notes:
        _say(n)


def _choice_text(choice: looks.Choice, subject: str, f: looks.Finding) -> str | None:
    """The text after "<choice label> of <option subject>: " (the model's wording),
    or None when the finding is not about this choice."""
    label = f"choice {choice.name!r} ({choice.id})" if choice.name else f"choice {choice.id}"
    prefix = f"{label} of {subject}: "
    return f.message[len(prefix) :] if f.message.startswith(prefix) else None


def _option_out(
    model: looks.Customizations,
    race: looks.Race,
    body: looks.BodyType,
    class_id: int | None,
    option: looks.Option,
) -> LooksOption:
    """One option, each choice with the check of a look holding only that choice.

    A finding on the option itself (its requirement, its model), which starts
    with the option's subject, is the same for every choice and is shown once
    on the option, whatever the number of choices. A finding every choice has,
    compared on its kind and the text after the choice's label, is shown once
    on the option as "every choice: <text>"."""
    checked: list[tuple[looks.Choice, looks.LookCheck]] = [
        (
            choice,
            model.check(
                looks.Look(
                    name="-",
                    race_id=race.id,
                    body_type=body.body_type,
                    class_id=class_id,
                    choices={option.id: choice.id},
                )
            ),
        )
        for choice in option.choices
    ]
    subject = f"option {option.name!r} ({option.id})"
    option_notes: list[looks.Finding] = []
    on_option: set[tuple[str, str]] = set()
    for _, verdict in checked:
        for f in (*verdict.refusals, *verdict.notes):
            key = (f.kind.value, f.message)
            if f.message.startswith(subject) and key not in on_option:
                on_option.add(key)
                option_notes.append(f.model_copy(update={"choice_id": None}))
    shared: set[tuple[str, str]] = set()
    if len(checked) > 1:
        shared = set.intersection(
            *(
                {
                    (f.kind.value, text)
                    for f in (*verdict.refusals, *verdict.notes)
                    if (text := _choice_text(choice, subject, f)) is not None
                }
                for choice, verdict in checked
            )
        )
        first_choice, first = checked[0]
        for f in (*first.refusals, *first.notes):
            text = _choice_text(first_choice, subject, f)
            if text is not None and (f.kind.value, text) in shared:
                option_notes.append(
                    f.model_copy(update={"choice_id": None, "message": f"every choice: {text}"})
                )

    def keep(choice: looks.Choice, f: looks.Finding) -> bool:
        if (f.kind.value, f.message) in on_option:
            return False
        text = _choice_text(choice, subject, f)
        return text is None or (f.kind.value, text) not in shared

    category = model.categories.get(option.category_id)
    return LooksOption(
        id=option.id,
        name=option.name,
        chr_model_id=option.chr_model_id,
        category=category.name if category else None,
        form_or_pet=model.is_form_or_pet(option),
        notes=option_notes,
        choices=[
            LooksChoice(
                id=choice.id,
                name=choice.name,
                refusals=[f for f in verdict.refusals if keep(choice, f)],
                notes=[f for f in verdict.notes if keep(choice, f)],
            )
            for choice, verdict in checked
        ],
    )


def _options_for_body(
    model: looks.Customizations, race: looks.Race, body: looks.BodyType, class_id: int | None
) -> LooksBodyType:
    return LooksBodyType(
        body_type=body.body_type,
        chr_model_id=body.chr_model_id,
        options=[
            _option_out(model, race, body, class_id, option)
            for option in model.options_for(race.id, body.body_type, class_id)
        ],
    )


@looks_app.command("options")
@_handled
def looks_options(
    race: Annotated[str, typer.Argument(help="Race id or name, as `looks races` lists it.")],
    sex: SexOpt = None,
    class_: ClassOpt = None,
    build: LooksBuildOpt = None,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
) -> None:
    """A race's options and choices per body type (every body type unless --sex),
    for a class when --class is given, with what the tables say about each
    choice: refusals, "needs <unlock>" and other notes. JSON: LooksOptionsReport."""
    body_type = _parse_sex(sex)
    with _open_gamedata() as data:
        version, remarks = _looks_build(data, build, root, flavor)
        model = _load_model(data, version)
    chosen, race_remarks = _resolve_race(model, race)
    class_id = _resolve_class(model, class_)
    bodies = [b for b in chosen.body_types if body_type is None or b.body_type == body_type]
    if not bodies:
        have = [b.body_type for b in chosen.body_types]
        raise CliError(
            f"race {_race_label(chosen)} has no body type {body_type} in build {version} "
            f"(it has {have})",
            EXIT_USAGE,
        )
    notes = [*race_remarks, *remarks]
    if not chosen.flagged_playable:
        notes.append(f"{_race_label(chosen)} is not flagged playable in build {version}.")
    if class_id is not None and class_id not in model.classes:
        notes.append(f"Class {class_id} is not in build {version}'s ChrClasses.")
    if class_id is None:
        notes.append("No --class: class-restricted choices are listed with a note.")
    report = LooksOptionsReport(
        build=version,
        race=_race_out(chosen),
        class_id=class_id,
        class_name=_class_name(model, class_id),
        body_types=[_options_for_body(model, chosen, b, class_id) for b in bodies],
        notes=notes,
    )
    if json_out:
        _emit(report)
        return
    who = _race_label(chosen)
    if class_id is not None:
        who += f", {report.class_name or 'class'} ({class_id})"
    for body in report.body_types:
        _say(f"{who}, body type {body.body_type} (model {body.chr_model_id}), build {version}:")
        for option in body.options:
            where = f" [{option.category}]" if option.category else ""
            form = " (form or pet option)" if option.form_or_pet else ""
            _say(
                f"  option {option.id}  {option.name}{where}{form}: {len(option.choices)} choice(s)"
            )
            _print_findings(
                [f for f in option.notes if f.refuses],
                [f for f in option.notes if not f.refuses],
                "      ",
            )
            for choice in option.choices:
                _say(f"    {choice.id:>7}  {choice.name}".rstrip())
                _print_findings(choice.refusals, choice.notes, "             ")
    for n in notes:
        _say(n)


@looks_app.command("save")
@_handled
def looks_save(
    name: LookNameArg,
    race: Annotated[str, typer.Option("--race", help="Race id or name.")],
    sex: Annotated[
        str,
        typer.Option(
            "--sex",
            help="Body type (ChrRaceXChrModel.Sex): 0 or 1; 'male' and 'female' read as 0 and 1.",
        ),
    ],
    choice: Annotated[
        list[str] | None,
        typer.Option(
            "--choice",
            help="OPTION=CHOICE, two ids as `wowlab looks options` lists them. Repeat for more.",
        ),
    ] = None,
    class_: ClassOpt = None,
    replace: Annotated[
        bool, typer.Option("--replace", help="Overwrite a saved look with this name.")
    ] = False,
    build: LooksBuildOpt = None,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
) -> None:
    """Check a look against the build's tables and save it under the user data
    directory (never in an install). A look the tables refuse is not saved
    (exit 1); notes ("needs <unlock>", "unknown to build ...") are shown and do
    not stop the save. JSON: LookReport."""
    try:
        lookstore.check_name(name)
    except lookstore.LookStoreError as exc:
        raise CliError(str(exc), EXIT_USAGE) from exc
    body_type = _parse_sex(sex)
    assert body_type is not None
    choices = _parse_choices(choice or ())
    with _open_gamedata() as data:
        version, remarks = _looks_build(data, build, root, flavor)
        model = _load_model(data, version)
    chosen, race_remarks = _resolve_race(model, race)
    remarks = [*race_remarks, *remarks]
    look = looks.Look(
        name=name,
        race_id=chosen.id,
        body_type=body_type,
        class_id=_resolve_class(model, class_),
        choices=choices,
    )
    saved = lookstore.SavedLook(saved_build=version, look=look)
    report = _look_report(model, saved, None, remarks)
    if report.refused:
        if json_out:
            _emit(report)
        else:
            _print_look(report, heading="Not saved: the tables refuse look")
        raise CliError(f"look {name} is refused by build {version}'s tables; nothing was saved")
    path = lookstore.LookStore().save(saved, replace=replace)
    report = _look_report(model, saved, path, remarks)
    if json_out:
        _emit(report)
    else:
        _print_look(report, heading="Saved look")


@looks_app.command("show")
@_handled
def looks_show(
    name: Annotated[
        str | None,
        typer.Argument(metavar="[NAME]", help="A saved look. Default: every saved look."),
    ] = None,
    build: LooksBuildOpt = None,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
) -> None:
    """One saved look checked against the build's tables, or every saved look
    with its verdict; names each damaged file and then exits 1.
    JSON: LookReport with NAME, else LooksListReport."""
    store = lookstore.LookStore()
    try:
        if name is not None:
            lookstore.check_name(name)
    except lookstore.LookStoreError as exc:
        raise CliError(str(exc), EXIT_USAGE) from exc
    if name is not None:
        path = store.locate(name)
        one = store.read(path)
        with _open_gamedata() as data:
            version, remarks = _looks_build(data, build, root, flavor, [one.saved_build])
            model = _load_model(data, version)
        report = _look_report(model, one, path, remarks)
        if json_out:
            _emit(report)
        else:
            _print_look(report, heading="Look")
        return
    found, damaged = store.listing()
    summaries: list[LookSummary] = []
    version_used: str | None = None
    list_remarks: list[str] = []
    if found:
        with _open_gamedata() as data:
            version_used, list_remarks = _looks_build(
                data, build, root, flavor, [s.saved_build for s in found]
            )
            model = _load_model(data, version_used)
        list_remarks = [*list_remarks, _EXPORTED_ONLY.format(version=version_used)]
        for s in found:
            verdict = model.check(s.look)
            race = model.races.get(s.look.race_id)
            summaries.append(
                LookSummary(
                    name=s.look.name,
                    saved_build=s.saved_build,
                    race_id=s.look.race_id,
                    race_name=race.name if race else None,
                    body_type=s.look.body_type,
                    class_id=s.look.class_id,
                    choices=len(s.look.choices),
                    refused=verdict.refused,
                    refusals=len(verdict.refusals),
                    notes=len(verdict.notes),
                )
            )
    listing = LooksListReport(
        directory=str(store.root),
        build=version_used,
        looks=summaries,
        damaged=damaged,
        remarks=list_remarks,
    )
    if json_out:
        _emit(listing)
    else:
        if not summaries and not damaged:
            _say(f"No saved looks (in {store.root}).")
        else:
            _say(f"Saved looks in {store.root}, checked against build {version_used}:")
        for item in summaries:
            who = f"{item.race_name} ({item.race_id})" if item.race_name else str(item.race_id)
            verdict_text = (
                f"refused ({item.refusals})" if item.refused else "not refused"
            ) + f", {item.notes} note(s)"
            _say(
                f"  {item.name}  {who}, body type {item.body_type}, "
                f"{item.choices} choice(s): {verdict_text}"
            )
        for remark in list_remarks:
            _say(remark)
    for bad in damaged:
        _note(f"wowlab: damaged look file {bad.file}: {bad.error}")
    if damaged:
        raise typer.Exit(EXIT_ERROR)


@looks_app.command("compare")
@_handled
def looks_compare(
    a: Annotated[str, typer.Argument(metavar="A", help="A saved look.")],
    b: Annotated[str, typer.Argument(metavar="B", help="Another saved look.")],
    build: LooksBuildOpt = None,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
) -> None:
    """Two saved looks side by side, both checked against one build's tables.
    JSON: LooksCompareReport."""
    store = lookstore.LookStore()
    try:
        lookstore.check_name(a)
        lookstore.check_name(b)
    except lookstore.LookStoreError as exc:
        raise CliError(str(exc), EXIT_USAGE) from exc
    path_a, path_b = store.locate(a), store.locate(b)
    first, second = store.read(path_a), store.read(path_b)
    with _open_gamedata() as data:
        version, remarks = _looks_build(
            data, build, root, flavor, [first.saved_build, second.saved_build]
        )
        model = _load_model(data, version)
    # What holds for both looks (how the build was chosen, the tables-only
    # remark) is stated once, in the top-level remarks; each side keeps only
    # what is its own (saved against another build).
    ra = _look_report(model, first, path_a, exported_only=False)
    rb = _look_report(model, second, path_b, exported_only=False)
    la, lb = first.look, second.look
    same: list[LooksChoiceRef] = []
    different: list[LooksDifference] = []
    for option_id in list(dict.fromkeys([*la.choices, *lb.choices])):
        ca, cb = la.choices.get(option_id), lb.choices.get(option_id)
        if ca is not None and ca == cb:
            same.append(_choice_ref(model, option_id, ca))
            continue
        option = model.options.get(option_id)
        different.append(
            LooksDifference(
                option_id=option_id,
                option_name=option.name if option else None,
                a=_choice_ref(model, option_id, ca) if ca is not None else None,
                b=_choice_ref(model, option_id, cb) if cb is not None else None,
            )
        )
    report = LooksCompareReport(
        build=version,
        a=ra,
        b=rb,
        same_race=la.race_id == lb.race_id,
        same_body_type=la.body_type == lb.body_type,
        same_class=la.class_id == lb.class_id,
        same=same,
        different=different,
        remarks=[*remarks, _EXPORTED_ONLY.format(version=model.build)],
    )
    if json_out:
        _emit(report)
        return

    def race(r: LookReport) -> str:
        return f"{r.race_name} ({r.race_id})" if r.race_name else str(r.race_id)

    def cls(r: LookReport) -> str:
        if r.class_id is None:
            return "not given"
        return f"{r.class_name or 'not in this build'} ({r.class_id})"

    def side(ref: LooksChoiceRef | None) -> str:
        if ref is None:
            return "(not set in this look)"
        return _ref_choice_text(ref)

    _say(f"Looks {ra.name} | {rb.name}, checked against build {version}")
    _say(f"  race:      {race(ra)} | {race(rb)}")
    _say(f"  body type: {ra.body_type} | {rb.body_type}")
    _say(f"  class:     {cls(ra)} | {cls(rb)}")
    _say(f"  same choice on {len(same)} option(s)")
    if different:
        _say(f"  different on {len(different)} option(s):")
    for d in different:
        label = f"{d.option_name} ({d.option_id})" if d.option_name else f"option {d.option_id}"
        _say(f"    {label}: {side(d.a)} | {side(d.b)}")
    for r in (ra, rb):
        verdict = f"refused ({len(r.refusals)} reason(s))" if r.refused else "not refused"
        _say(f"  {r.name}: {verdict}, {len(r.notes)} note(s)")
        _print_findings(r.refusals, r.notes, "    ")
    for remark in dict.fromkeys([*remarks, *ra.remarks, *rb.remarks, *report.remarks]):
        _say(remark)


# ─── looks import-char ───────────────────────────────────────────────────────

_AS_OF_WORDS: dict[str | None, str] = {
    "open": "as of the last barber-shop open",
    "applied": "as of the last applied barber-shop change",
    None: "as of the last barber-shop visit",
}
_OPEN_REMARK = (
    "Recorded when the barber shop opened: a change applied during that visit may not be in "
    'it (a capture has shown a record left at "open" after a change was applied) [verify].'
)
_MODEL_ONLY_REMARK = (
    "The record holds only the options the barber shop listed, for the model it was showing; "
    "it can leave out options the tables give that model."
)
_NOT_LISTED_REMARK = (
    "Not listed by the barber shop, so not in this look: {options} (options of model {model} "
    "in build {build}'s tables). The record cannot say what the character has there; a choice "
    "that depends on one of them is shown as undecided."
)
_NO_CLASS_REMARK = (
    "The record holds no class: class-restricted choices are noted, not refused; --class "
    "checks them."
)


class LooksImportReport(_Out):
    """`wowlab looks import-char --json`. `saved` is False when the tables
    refuse the look (nothing written; `look.path` is None)."""

    flavor_folder: str
    account: str
    character: str  # "<realm folder>/<character folder>"
    chosen_by: Literal["--character", "latest"]
    file: str  # relative to the flavor folder
    mtime_ns: int
    record: labaddon.CustomizationImport
    as_of_words: str  # how the record's moment is said: never the character's appearance now
    saved: bool
    look: LookReport


def _loads_ago_text(ago: int | None) -> str:
    if ago is None:
        return "an unknown number of logins or reloads before this file was saved"
    if ago == 0:
        return "in the session that saved this file"
    return f"{ago} login(s) or reload(s) before the session that saved this file"


def _import_remarks(
    model: looks.Customizations, got: labaddon.CustomizationImport, class_id: int | None
) -> list[str]:
    at = got.recorded_at if got.recorded_at is not None else "not recorded"
    remarks = [
        f"Customization {_AS_OF_WORDS[got.recorded_at]} (recorded_at: {at}; the addon's as_of: "
        f'"{got.as_of}"), {_loads_ago_text(got.loads_ago)}. The character may look different '
        "since.",
    ]
    if got.recorded_at == "open":
        remarks.append(_OPEN_REMARK)
    remarks.append(labaddon.PAID_CHANGE_NOTE)
    remarks.append(_MODEL_ONLY_REMARK)
    if got.without_choice:
        listed = ", ".join(str(o) for o in got.without_choice)
        remarks.append(f"Recorded with no choice id, so left out of the look: option(s) {listed}.")
    race = model.races.get(got.race_id)
    expected = race.model_for(got.body_type) if race is not None else None
    shown_model = got.chr_model_id if got.chr_model_id is not None else expected
    if shown_model is not None:
        listed_ids = {*got.choices, *got.without_choice}
        unlisted = sorted(
            (o for o in model.options.values() if o.chr_model_id == shown_model),
            key=lambda o: o.id,
        )
        unlisted = [o for o in unlisted if o.id not in listed_ids]
        if unlisted:
            remarks.append(
                _NOT_LISTED_REMARK.format(
                    options=", ".join(f"{o.name} ({o.id})" for o in unlisted),
                    model=shown_model,
                    build=model.build,
                )
            )
    if got.chr_model_id is None:
        remarks.append(
            "The record names no model (chr_model_id): the body type is its sex value "
            f"{got.body_type}, read as the tables' body type (ChrRaceXChrModel.Sex) [verify]."
        )
    elif expected is not None and expected != got.chr_model_id:
        remarks.append(
            f"The barber shop was showing model {got.chr_model_id}; build {model.build}'s "
            f"tables give race {got.race_id} body type {got.body_type} model {expected}."
        )
    if class_id is None:
        remarks.append(_NO_CLASS_REMARK)
    if got.client_build is not None and got.client_build != model.build:
        remarks.append(
            f"Recorded by client build {got.client_build}; checked against build "
            f"{model.build}'s tables: an id they lack may exist only in the recording build, or "
            "come from a hotfix."
        )
    return remarks


@looks_app.command("import-char")
@_handled
def looks_import_char(
    name: LookNameArg,
    character: CharacterOpt = None,
    account: AccountOpt = None,
    class_: ClassOpt = None,
    replace: Annotated[
        bool, typer.Option("--replace", help="Overwrite a saved look with this name.")
    ] = False,
    build: LooksBuildOpt = None,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
) -> None:
    """Save a character's customization choices, as the lab-addon recorded
    them at its last barber-shop visit, as a look under the user data
    directory (never in an install). Reads the character's WowLab.lua (read
    only); without --character, the one written last. A character with no
    recorded visit gets the addon's reason (exit 1). The look is checked like
    `looks save`: one the tables refuse is not saved (exit 1); an id the
    tables lack is noted "(possibly a hotfix)". JSON: LooksImportReport."""
    try:
        lookstore.check_name(name)
    except lookstore.LookStoreError as exc:
        raise CliError(str(exc), EXIT_USAGE) from exc
    _, chosen, lay = _open(root, flavor)
    acct = _select_account(lay, account)
    files = [f for f in lay.saved_variables() if f.account == acct.folder and _is_lab_file(f)]
    target, how, tie = _lab_char_file(acct, files, character)
    who = f"{target.realm_folder}/{target.character_folder}"
    record = labaddon.read_char(lay.flavor_path / target.path)
    try:
        got = labaddon.customization_import(record)
    except labaddon.NoCustomizationError as exc:
        raise CliError(
            f"{who}{_picked_words(how, tie)}: nothing to import: {exc}; nothing was saved"
        ) from exc
    with _open_gamedata() as data:
        version, build_remarks = _looks_build(data, build, root, flavor)
        model = _load_model(data, version)
    class_id = _resolve_class(model, class_)
    look = looks.Look(
        name=name,
        race_id=got.race_id,
        body_type=got.body_type,
        class_id=class_id,
        choices=got.choices,
    )
    saved = lookstore.SavedLook(
        saved_build=version,
        origin="imported",
        recorded_client_build=got.client_build,
        look=look,
    )
    remarks = [*_import_remarks(model, got, class_id), *build_remarks]
    report = _look_report(model, saved, None, remarks, imported_remark=False)
    path: Path | None = None
    if not report.refused:
        path = lookstore.LookStore().save(saved, replace=replace)
        report = _look_report(model, saved, path, remarks, imported_remark=False)
    out = LooksImportReport(
        flavor_folder=chosen.folder,
        account=acct.folder,
        character=who,
        chosen_by=how,
        file=target.path,
        mtime_ns=target.mtime_ns,
        record=got,
        as_of_words=_AS_OF_WORDS[got.recorded_at],
        saved=path is not None,
        look=report,
    )
    if json_out:
        _emit(out)
    else:
        _say(f"Character: {who} in account {acct.folder}{_picked_words(how, tie)}")
        _say(f"File: {target.path}")
        _say(f"Written: {_local_time(target.mtime_ns)} (the file's modification time)")
        heading = "Imported look" if path is not None else "Not saved: the tables refuse look"
        _print_look(report, heading=heading)
    if path is None:
        raise CliError(f"look {name} is refused by build {version}'s tables; nothing was saved")


# ─── looks page ──────────────────────────────────────────────────────────────

LOOKS_PAGE_FORMAT: Literal[1] = 1
_PAGE_LEGEND = (
    "Each choice is checked as a look that holds only that choice, as `wowlab looks "
    "options` does, so a dependency on another option shows as a note, never a refusal.",
    "refused: the tables decide against it, and `wowlab looks save` would not save a look "
    "holding it (an option or choice for another race or body type, a choice outside its "
    "option, a class the ClassMask excludes, or a choice it depends on set to something "
    "else).",
    'note: shown, never a refusal: "needs <unlock>" (an achievement, quest or item '
    'appearance to earn), an id the build lacks (in a typed look: "is not in build '
    "<version>'s tables: check the id with `wowlab looks options`\"; in an imported look: "
    '"unknown to build <version> (possibly a hotfix)", or, when the recording client was '
    'another build, "(recorded by client <build>, not the build of these tables: the id may '
    'exist only in that build, or come from a hotfix)"), a dependency on '
    "an option the look leaves unset, a class-restricted choice when no class is chosen, "
    "conditions, and options on a model no race uses (a form, pet or mount), checked by "
    "their requirements only [verify].",
)
_PAGE_CLASSES_NOTE = (
    "Classes are the rows of build {version}'s ChrClasses ({classes}); class masks are "
    "checked against these ids only. Every class is offered with every race: which races "
    "can be which class is not read from the tables [verify], so a pairing here (a Human "
    "Druid, say) may be one the game does not offer."
)
_PAGE_PAIR_NOTE = (
    "Race and class are not checked as a pair: this view lists what the customization "
    "tables give this race and class, not proof that the game lets anyone create it."
)
_PAGE_NO_CLASS_NOTE = "No class chosen: class-restricted choices are listed with a note."
_PAGE_NO_CLASS_LABEL = "not given (class-restricted choices are noted, not refused)"


class LooksPageClass(_Out):
    id: int
    name: str


class LooksPageRace(_Out):
    race: LooksRace
    label: str  # faction, id and body types, as `looks races` words them


class LooksPageOption(_Out):
    """`LooksOption` with its option-level findings split as the CLI prints them."""

    id: int
    name: str
    chr_model_id: int
    category: str | None
    form_or_pet: bool
    refusals: list[looks.Finding]
    notes: list[looks.Finding]
    choices: list[LooksChoice]


class LooksPageView(_Out):
    """`looks options <race> --sex <body_type> [--class <class_id>]`, precomputed.
    `options` are indices into `LooksPageData.options`, in the command's order."""

    race_id: int
    body_type: int
    class_id: int | None
    chr_model_id: int
    heading: str
    notes: list[str]
    options: list[int]


class LooksPageLook(_Out):
    """`looks show NAME`, precomputed: the report and the lines the CLI prints."""

    report: LookReport
    verdict: str
    facts: list[tuple[str, str]]
    choice_lines: list[str]


class LooksPageData(_Out):
    """The JSON block embedded in the looks page (ADR-0027). The page's script
    only displays it; everything in it is computed here."""

    format: Literal[1] = LOOKS_PAGE_FORMAT
    build: str
    remarks: list[str]  # how the build was chosen, when not by --build or the install
    notes: list[str]  # the caveats `looks races`, `looks options` and `looks show` print
    legend: list[str]
    races_heading: str
    races: list[LooksPageRace]
    classes: list[LooksPageClass]
    options: list[LooksPageOption]
    views: list[LooksPageView]
    looks_directory: str
    looks: list[LooksPageLook]
    damaged: list[lookstore.DamagedLook]
    damaged_heading: str
    no_class_label: str
    no_findings_label: str
    no_race_label: str
    no_options_label: str
    no_looks_label: str


class LooksPageReport(_Out):
    """`wowlab looks page --json`: what was written. The command exits 1 after
    writing when `damaged` is not empty, as `looks show` does."""

    path: str
    build: str
    bytes: int
    races: int
    views: int
    looks: int
    damaged: list[lookstore.DamagedLook]
    remarks: list[str]


def _home_short(text: str) -> str:
    """``text`` with the home directory written as ``~``, so a page passed on
    does not carry the account's user name in its paths."""
    try:
        home = str(Path.home())
    except (RuntimeError, OSError):
        return text
    if home in ("", os.sep):
        return text
    if text == home:
        return "~"
    return text.replace(home + os.sep, "~" + os.sep)


def _page_report(report: LookReport) -> LookReport:
    """A `looks show NAME` report as the page embeds it: its path with `~`."""
    if report.path is None:
        return report
    return report.model_copy(update={"path": _home_short(report.path)})


def _page_option(option: LooksOption) -> LooksPageOption:
    return LooksPageOption(
        id=option.id,
        name=option.name,
        chr_model_id=option.chr_model_id,
        category=option.category,
        form_or_pet=option.form_or_pet,
        refusals=[f for f in option.notes if f.refuses],
        notes=[f for f in option.notes if not f.refuses],
        choices=option.choices,
    )


def _page_look(report: LookReport) -> LooksPageLook:
    race = f"{report.race_name} ({report.race_id})" if report.race_name else f"{report.race_id}"
    if report.class_id is None:
        player_class = _PAGE_NO_CLASS_LABEL
    else:
        player_class = f"{report.class_name or 'not in this build'} ({report.class_id})"
    facts = [
        ("file", report.path or ""),
        ("saved against build", report.saved_build or ""),
        ("checked against build", report.build),
        ("race", f"{race}, body type {report.body_type}"),
        ("class", player_class),
        ("choices", str(len(report.choices))),
    ]
    if report.refused:
        verdict = f"refused ({len(report.refusals)} reason(s)), {len(report.notes)} note(s)"
    else:
        verdict = f"not refused, {len(report.notes)} note(s)"
    return LooksPageLook(
        report=report,
        verdict=verdict,
        facts=facts,
        choice_lines=[_ref_text(ref) for ref in report.choices],
    )


def _looks_page_data(
    model: looks.Customizations,
    remarks: Sequence[str],
    store: lookstore.LookStore,
    found: Sequence[tuple[Path, lookstore.SavedLook]],
    damaged: Sequence[lookstore.DamagedLook],
) -> LooksPageData:
    """Everything the page shows, from the same functions `looks races`,
    `looks options` and `looks show` use: every playable race, each body
    type, without a class and with each class of the build's ChrClasses."""
    version = model.build
    classes = [LooksPageClass(id=i, name=c.name) for i, c in sorted(model.classes.items())]
    class_ids: list[int | None] = [None, *(c.id for c in classes)]
    table: list[LooksPageOption] = []
    index: dict[str, int] = {}
    races: list[LooksPageRace] = []
    views: list[LooksPageView] = []
    for race in model.playable_races():
        bodies = ", ".join(f"{b.body_type} (model {b.chr_model_id})" for b in race.body_types)
        races.append(
            LooksPageRace(
                race=_race_out(race),
                label=f"{race.id} · {_faction(race.alliance)} · body types {bodies or 'none'}",
            )
        )
        for body in race.body_types:
            for class_id in class_ids:
                refs: list[int] = []
                for option in _options_for_body(model, race, body, class_id).options:
                    entry = _page_option(option)
                    key = entry.model_dump_json()
                    if key not in index:
                        index[key] = len(table)
                        table.append(entry)
                    refs.append(index[key])
                who = _race_label(race)
                if class_id is not None:
                    who += f", {_class_name(model, class_id) or 'class'} ({class_id})"
                views.append(
                    LooksPageView(
                        race_id=race.id,
                        body_type=body.body_type,
                        class_id=class_id,
                        chr_model_id=body.chr_model_id,
                        heading=(
                            f"{who}, body type {body.body_type} (model {body.chr_model_id}), "
                            f"build {version}"
                        ),
                        notes=[_PAGE_NO_CLASS_NOTE] if class_id is None else [_PAGE_PAIR_NOTE],
                        options=refs,
                    )
                )
    class_list = ", ".join(f"{c.name} ({c.id})" for c in classes) or "none"
    return LooksPageData(
        build=version,
        remarks=list(remarks),
        notes=[
            _PLAYABLE_NOTE,
            _EXPORTED_ONLY.format(version=version),
            _PAGE_CLASSES_NOTE.format(version=version, classes=class_list),
        ],
        legend=list(_PAGE_LEGEND),
        races_heading=f"Races flagged playable in build {version}'s ChrRaces",
        races=races,
        classes=classes,
        options=table,
        views=views,
        looks_directory=_home_short(str(store.root)),
        looks=[_page_look(_page_report(_look_report(model, saved, path))) for path, saved in found],
        damaged=[d.model_copy(update={"error": _home_short(d.error)}) for d in damaged],
        damaged_heading="Damaged look files (not saved looks; `wowlab looks show` names them too)",
        no_class_label=_PAGE_NO_CLASS_LABEL,
        no_findings_label="nothing noted",
        no_race_label="Pick a race.",
        no_options_label="The tables give this race, body type and class no options.",
        no_looks_label=f"No saved looks (in {_home_short(str(store.root))}).",
    )


@looks_app.command("page")
@_handled
def looks_page(
    out: Annotated[
        Path | None,
        typer.Option(
            "--out",
            help="The HTML file to write. Default: pages/looks.html under the user data "
            "directory. Refused when it is in an install.",
        ),
    ] = None,
    build: LooksBuildOpt = None,
    root: RootOpt = None,
    flavor: FlavorOpt = None,
    json_out: JsonOpt = False,
) -> None:
    """Write one self-contained HTML page (no network requests) to browse the
    races, body types, classes, options and choices of the build's tables, with
    what the tables say about each choice, and to view the saved looks checked
    against that build. Never written into an install; names each damaged look
    file and then exits 1, as `looks show` does. JSON: LooksPageReport."""
    target = out if out is not None else lookspage.default_page_path()
    try:
        # Before any work: a refused --out costs nothing and writes nothing.
        lookspage.check_target(target)
    except (lookspage.PageError, lookstore.LookLocationError) as exc:
        raise CliError(f"refused: {exc}; nothing was written") from exc
    store = lookstore.LookStore()
    found, damaged = store.entries()
    with _open_gamedata() as data:
        version, remarks = _looks_build(
            data, build, root, flavor, [saved.saved_build for _, saved in found]
        )
        model = _load_model(data, version)
    payload = _looks_page_data(model, remarks, store, found, damaged)
    page = lookspage.render(payload.model_dump_json())
    try:
        written = lookspage.write_page(page, target)
    except (lookspage.PageError, lookstore.LookLocationError) as exc:
        raise CliError(f"refused: {exc}; nothing was written") from exc
    report = LooksPageReport(
        path=str(written),
        build=version,
        bytes=len(page.encode("utf-8")),
        races=len(payload.races),
        views=len(payload.views),
        looks=len(payload.looks),
        damaged=list(damaged),
        remarks=list(remarks),
    )
    if json_out:
        _emit(report)
    else:
        _say(f"Wrote the looks page for build {version}: {written}")
        _say(
            f"  {report.races} race(s) flagged playable, {report.views} race/body type/class "
            f"view(s), {report.looks} saved look(s), {_bytes(report.bytes)}"
        )
        _say(
            "Open it in a browser. It is one self-contained file and makes no network "
            "requests (its Content-Security-Policy forbids them)."
        )
        for remark in remarks:
            _say(remark)
    for bad in damaged:
        _note(f"wowlab: damaged look file {bad.file}: {bad.error}")
    if damaged:
        raise typer.Exit(EXIT_ERROR)


# ─── version ─────────────────────────────────────────────────────────────────


def _print_version(value: bool) -> None:
    if value:
        typer.echo(f"wowlab {__version__}")
        raise typer.Exit


@app.callback()
def main(
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_print_version,
            is_eager=True,
            help="Print the version and exit.",
        ),
    ] = False,
) -> None:
    """Local-only toolchain over a World of Warcraft install."""
