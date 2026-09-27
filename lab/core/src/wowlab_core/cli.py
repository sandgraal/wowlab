"""The `wowlab` command (docs/LAB_PLAN.md §6.11, M10-14).

A thin shell over the library. Every command reads through the modules the
spec names and prints text, or JSON with `--json`; the only commands that
change an install are `snap restore` and `undo`, and both go through
`wowlab_core.guard` (L2, ADR-0021): they print the plan and ask before
writing unless `--yes` is given. Nothing here writes a file itself; the
snapshot store and the game-data cache are written by `snapshot` and
`gamedata`, under the user data directory (L1).

Exit codes: 0 ok, 1 error, 2 usage (including "more than one flavor, pick
one with --flavor"), 3 refused by the write gate (any `guard.GuardError`:
client running or unknown, a path outside the allowlist, the store busy, a
file changed since the pre-write snapshot, a snapshot of another install).

Where the install is: `--root`, else `$WOWLAB_WOW_ROOT`, else the platform
defaults (`install.discover`). Which flavor: `--flavor`, implied when the
install has exactly one. Nothing about a flavor is known here (L6): folder
names, products and versions come from discovery.

JSON: each data command's output validates against the Pydantic model named
in its help (the models are defined below, or are the library's own). Text
that came from file names or file bytes and is not valid UTF-8 is written in
JSON with the scheme `layout` and `snapshot` use (a lone surrogate becomes
NUL followed by four hex digits); snapshot manifests are written with
`snapshot.manifest_bytes`, the one canonical encoding.

`log tail` (§6.11) is not here: it needs `combatlog` (M10-13), which is not
part of this ticket's dependencies.
"""

import base64
import contextlib
import csv
import functools
import io
import json
import os
import re
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Any, Literal, NoReturn

import typer
from pydantic import BaseModel, ConfigDict, Field

from wowlab_core import (
    __version__,
    gamedata,
    guard,
    install,
    layout,
    luadata,
    process,
    snapshot,
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
sv_app = typer.Typer(help="SavedVariables files (read only).", no_args_is_help=True)
cvar_app = typer.Typer(help="CVars in Config.wtf and config-cache.wtf.", no_args_is_help=True)
binds_app = typer.Typer(help="Key bindings in bindings-cache.wtf.", no_args_is_help=True)
macros_app = typer.Typer(help="Macros in macros-cache.txt.", no_args_is_help=True)
addons_app = typer.Typer(help="Folders under Interface/AddOns/.", no_args_is_help=True)
db2_app = typer.Typer(
    help="Game data tables from wago.tools, cached by build.", no_args_is_help=True
)
snap_app = typer.Typer(help="The snapshot store.", no_args_is_help=True)
app.add_typer(install_app, name="install")
app.add_typer(sv_app, name="sv")
app.add_typer(cvar_app, name="cvar")
app.add_typer(binds_app, name="binds")
app.add_typer(macros_app, name="macros")
app.add_typer(addons_app, name="addons")
app.add_typer(db2_app, name="db2")
app.add_typer(snap_app, name="snap")


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


def _safe(text: str) -> str:
    """Printable text: bytes that are not UTF-8 (carried as lone surrogates)
    are shown as `\\xNN` escapes instead of failing the write to the terminal."""
    try:
        return text.encode("utf-8", "surrogateescape").decode("utf-8", "backslashreplace")
    except UnicodeEncodeError:
        return text.encode("utf-8", "backslashreplace").decode("utf-8")


def _say(text: str = "") -> None:
    typer.echo(_safe(text))


def _note(text: str) -> None:
    typer.echo(_safe(text), err=True)


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
            gamedata.GameDataError,
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
    The same rule `guard` applies before a write.
    """
    names: list[str] = []
    try:
        children = sorted(flavor_dir.iterdir())
    except OSError:
        return names
    for child in children:
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
    names = sorted({n for f in inst.flavors for n in _client_names(f.path)})
    try:
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
        install_error = "no install found at the default locations"
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
        return f"wago.tools does not list version {g.version} yet; no game data for it"
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
            _say("  Server sync (when on, the server can replace local binds, macros or")
            _say("  settings at login):")
            for sync in f.sync_cvars:
                value = f'"{sync.value}"' if sync.value is not None else "not set (client default)"
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
        add(addon.path, "folder", None, f"addon; TOC choice: {addon.selection}")
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
                _say(f"      [{e.entry_id}] {_plain(e.what)}")
                _say(f"      edit: {_edit_words(e.edit)}")
    if inv.truncated:
        _note("The walk hit a bound; the listing is incomplete.")
    for err in errors:
        _note(f"could not read {err}")


def _plain(markdown: str) -> str:
    """File-map cells are Markdown; drop the bold markers for a terminal."""
    return markdown.replace("**", "")


def _edit_words(edit: str) -> str:
    if edit.startswith("gate"):
        rest = edit[len("gate") :].strip()
        extra = f" {rest}" if rest else ""
        return f"yes, through wowlab's write gate only, with the client closed{extra}"
    if edit == "no":
        return "no; wowlab never writes it"
    if edit == "n/a":
        return "n/a; not a file wowlab touches"
    return edit


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
    _say(f"{result.path or '.'} ({base})")
    _say(f"  file-map row: {e.id} ({e.doc_path})")
    _say(f"  what:         {_plain(e.what)}")
    _say(f"  written by:   {_plain(e.written_by)}")
    _say(f"  edit:         {_edit_words(e.edit)}")
    _say(f"  tier:         {e.tier}")
    _say(f"  read by:      {e.module}")


# ─── sv ──────────────────────────────────────────────────────────────────────


class SvListReport(_Out):
    """`wowlab sv list --json`."""

    flavor_folder: str
    files: list[layout.SavedVariablesFile]


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
    report = SvListReport(flavor_folder=chosen.folder, files=files)
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
        _say("SavedVariables are written at logout or /reload: they hold the last session.")


# The JSON shape of a Lua value (`sv dump --json`).


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


class LuaEntryNode(_Out):
    style: Literal["positional", "string", "number", "name", "boolean"]
    position: int | None  # 1-based, positional entries only
    name: str | None  # a bare-name key
    key: LuaStringNode | LuaNumberNode | LuaBoolNode | None  # a bracketed key
    value: "LuaNode"
    duplicate: bool  # a later entry with a key an earlier one already has
    comment: str | None  # the line comment after the entry, e.g. "-- [1]"


class LuaTableNode(_Out):
    type: Literal["table"]
    entries: list[LuaEntryNode]


LuaNode = Annotated[
    LuaTableNode | LuaStringNode | LuaNumberNode | LuaBoolNode | LuaNilNode,
    Field(discriminator="type"),
]
LuaEntryNode.model_rebuild()


class SvAssignment(_Out):
    name: str
    value: LuaNode


class SvDumpReport(_Out):
    """`wowlab sv dump --json`: every top-level assignment in file order, or
    with `--path` the one value it names."""

    file: str
    path: str | None
    assignments: list[SvAssignment] | None
    value: LuaNode | None


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


def _lua_node(value: luadata.LuaValue) -> dict[str, Any]:
    """Plain JSON data in the `LuaNode` shape (built as dicts: a large file
    holds millions of entries)."""
    if isinstance(value, luadata.LuaTable):
        entries: list[dict[str, Any]] = []
        position = 0
        for e in value.entries:
            key: dict[str, Any] | None = None
            name: str | None = None
            pos: int | None = None
            if e.style is luadata.KeyStyle.POSITIONAL:
                position += 1
                pos = position
            elif isinstance(e.key, str):
                name = e.key
            elif isinstance(e.key, luadata.LuaString):
                key = _string_node(e.key)
            elif isinstance(e.key, luadata.LuaNumber):
                key = {"type": "number", "raw": e.key.raw}
            elif isinstance(e.key, luadata.LuaBool):
                key = {"type": "boolean", "value": e.key.value}
            entries.append(
                {
                    "style": e.style.value,
                    "position": pos,
                    "name": name,
                    "key": key,
                    "value": _lua_node(e.value),
                    "duplicate": e.duplicate,
                    "comment": _lua_text(e.comment) if e.comment is not None else None,
                }
            )
        return {"type": "table", "entries": entries}
    if isinstance(value, luadata.LuaString):
        return _string_node(value)
    if isinstance(value, luadata.LuaNumber):
        return {"type": "number", "raw": value.raw}
    if isinstance(value, luadata.LuaBool):
        return {"type": "boolean", "value": value.value}
    return {"type": "nil"}


def _key_segment(entry: luadata.Entry) -> str:
    key = entry.key
    if isinstance(key, str):
        return f".{key}"
    if isinstance(key, luadata.LuaString):
        return f"[{_lua_text(key.raw)}]"
    if isinstance(key, luadata.LuaNumber):
        return f"[{key.raw}]"
    if isinstance(key, luadata.LuaBool):
        return "[true]" if key.value else "[false]"
    raise AssertionError(f"entry without a key: {entry.style}")


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
        if entry.style is luadata.KeyStyle.POSITIONAL:
            counter[0] += 1
            child = f"{prefix}[{counter[0]}]"
        else:
            child = prefix + _key_segment(entry)
        inner = entry.value
        if isinstance(inner, luadata.LuaTable) and inner.entries:
            stack.append((child, iter(inner.entries), [0]))
        else:
            yield child, _scalar(inner)


_PATH_HEAD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_PATH_STEP = re.compile(
    r"""\.(?P<name>[A-Za-z_][A-Za-z0-9_]*)"""
    r"""|\[\s*(?:"(?P<dq>(?:[^"\\]|\\.)*)"|'(?P<sq>(?:[^'\\]|\\.)*)'"""
    r"""|(?P<bool>true|false)|(?P<num>-?(?:0[xX][0-9A-Fa-f]+|[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?)))\s*\]"""
)
_PATH_ESCAPE = re.compile(r"\\(.)")

_Step = tuple[Literal["text"], str] | tuple[Literal["number"], float] | tuple[Literal["bool"], bool]


def _parse_path(expr: str) -> tuple[str, list[_Step]]:
    head = _PATH_HEAD.match(expr)
    if head is None:
        raise CliError(f"--path must start with a variable name: {expr!r}", EXIT_USAGE)
    steps: list[_Step] = []
    pos = head.end()
    while pos < len(expr):
        m = _PATH_STEP.match(expr, pos)
        if m is None:
            raise CliError(
                f"cannot read --path {expr!r} at column {pos + 1}; use .name, [n], "
                '["text"] or [true]',
                EXIT_USAGE,
            )
        if m.group("name") is not None:
            steps.append(("text", m.group("name")))
        elif m.group("dq") is not None or m.group("sq") is not None:
            quoted = m.group("dq") if m.group("dq") is not None else m.group("sq")
            steps.append(("text", _PATH_ESCAPE.sub(r"\1", quoted)))
        elif m.group("bool") is not None:
            steps.append(("bool", m.group("bool") == "true"))
        else:
            steps.append(("number", luadata.LuaNumber(b"", m.group("num")).as_float()))
        pos = m.end()
    return head.group(), steps


def _step_text(step: _Step) -> str:
    kind, value = step
    if kind == "text":
        return f"[{json.dumps(value)}]"
    if kind == "bool":
        return "[true]" if value else "[false]"
    return f"[{value:g}]"


def _entry_matches(entry: luadata.Entry, position: int | None, step: _Step) -> bool:
    kind, wanted = step
    key = entry.key
    if kind == "number":
        if position is not None:
            return float(position) == wanted
        return isinstance(key, luadata.LuaNumber) and key.as_float() == wanted
    if position is not None:
        return False
    if kind == "text":
        if isinstance(key, str):
            return key == wanted
        return (
            isinstance(key, luadata.LuaString)
            and isinstance(wanted, str)
            and (key.data == wanted.encode("utf-8"))
        )
    return isinstance(key, luadata.LuaBool) and key.value == wanted


def _resolve_path(doc: luadata.LuaDocument, expr: str) -> luadata.LuaValue:
    name, steps = _parse_path(expr)
    found = [a for a in doc.assignments if a.name == name]
    if not found:
        raise CliError(f"no top-level variable {name!r} in this file")
    value: luadata.LuaValue = found[-1].value  # the client runs the file top to bottom
    where = name
    for step in steps:
        if not isinstance(value, luadata.LuaTable):
            raise CliError(f"{where} is not a table, so it has no {_step_text(step)}")
        matches = []
        position = 0
        for entry in value.entries:
            pos: int | None = None
            if entry.style is luadata.KeyStyle.POSITIONAL:
                position += 1
                pos = position
            if _entry_matches(entry, pos, step):
                matches.append(entry)
        if not matches:
            raise CliError(f"{where} has no key {_step_text(step)}")
        if len(matches) > 1:
            raise CliError(
                f"{where} has the key {_step_text(step)} {len(matches)} times (duplicate keys); "
                "see `wowlab sv dump` without --path"
            )
        where += _step_text(step)
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
        typer.Option("--path", help='One value, e.g. Var.key[3].name or Var["some key"].'),
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
            typer.echo(
                _dumps(
                    {
                        "file": str(target),
                        "path": path,
                        "assignments": None,
                        "value": _lua_node(value),
                    }
                )
            )
            return
        for p, v in _flatten(path, value):
            _say(f"{p} = {v}")
        return
    if json_out:
        assignments = [{"name": a.name, "value": _lua_node(a.value)} for a in doc.assignments]
        typer.echo(
            _dumps({"file": str(target), "path": None, "assignments": assignments, "value": None})
        )
        return
    for a in doc.assignments:
        for p, v in _flatten(a.name, a.value):
            _say(f"{p} = {v}")


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
    "(what this file says; the client may use a value from another scope, its default "
    "or the server, and it rewrites the file when it exits)"
)


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
        _say(f"{report.unknown_lines} line(s) of the file are not SET lines and were not read")
    _say(_WHAT_THE_FILE_SAYS)


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
    )
    if json_out:
        _emit(report)
        return
    if doc is None:
        _say(_no_file("bindings-cache.wtf", target))
        if scope == "character":
            _say("(the client writes one only when character-specific key bindings are on)")
        return
    _say(f"{report.file}:")
    for b in report.bindings:
        _say(f"  {b.key:<24} {b.action}")
    if report.unknown_lines:
        _say(f"  {report.unknown_lines} other line(s) are not bind lines and are not shown")
    _say("(the server can replace this file at login when binding sync is on)")


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
    _say("(the server can replace this file at login when macro sync is on)")


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


@contextmanager
def _store_lock_if_present(store: snapshot.SnapshotStore) -> Iterator[None]:
    """The write gate's store lock, so no transaction or `snap gc` runs on the
    store meanwhile. A store that does not exist yet has nothing to collect
    or undo; the first `create` makes it, unlocked."""
    if not store.path.is_dir():
        yield
        return
    with guard.store_lock(store.path):
        yield


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
    install only). JSON: the manifest (snapshot.Manifest)."""
    inst, chosen, lay = _open(root, flavor)
    subtrees = [f"{chosen.folder}/{s}" for s in lay.snapshot_subtrees(screenshots=screenshots)]
    running = _client_running(inst, chosen)
    store = snapshot.SnapshotStore()
    with _store_lock_if_present(store):
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
            "The client is running, or it could not be told: SavedVariables on disk hold the "
            "previous session, and the client rewrites them when it exits."
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
            running = "  (client was running)" if s.client_running else ""
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
            "  The client was running when this was taken: its SavedVariables are from the "
            "session before, not the one that was running."
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
        before = _leaves(store.read_object(change.before.sha256))
        after = _leaves(store.read_object(change.after.sha256))
    except luadata.LuaDataError as exc:
        return None, f"not compared as data: one side does not parse ({exc})"
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
    changed = []
    for c in diff.changed:
        lua, note = _lua_diff(store, c)
        changed.append(
            ChangedFile(path=c.path, before=c.before, after=c.after, lua=lua, lua_note=note)
        )
    report = DiffReport(
        a=diff.a,
        b=diff.b,
        added=list(diff.added),
        removed=list(diff.removed),
        changed=changed,
        mode_changed=list(diff.mode_changed),
    )
    if json_out:
        _emit(report)
        return
    _say(f"From {diff.a} to {diff.b}:")
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
    """Remove stored objects no snapshot refers to. Holds the store lock for the
    whole run, so no transaction starts meanwhile. JSON: snapshot.GcReport."""
    store = snapshot.SnapshotStore()
    if not store.path.is_dir():
        report = snapshot.GcReport(dry_run=True, unreferenced=(), unreferenced_bytes=0, removed=())
        if json_out:
            _emit(report)
        else:
            _say(f"No snapshot store at {store.path} yet; nothing to collect.")
        return
    with guard.store_lock(store.path):
        report = store.gc(dry_run=True)
        if report.unreferenced and not dry_run:
            if not json_out:
                _say(
                    f"{len(report.unreferenced)} object(s) no snapshot refers to, "
                    f"{_bytes(report.unreferenced_bytes)} on disk."
                )
            _confirm("Remove them?", yes)
            report = store.gc(dry_run=False)
    if json_out:
        _emit(report)
        return
    if not report.unreferenced:
        _say("Nothing to collect: every stored object is referred to by a snapshot.")
    elif report.dry_run:
        _say(
            f"Would remove {len(report.unreferenced)} object(s), "
            f"{_bytes(report.unreferenced_bytes)} on disk (dry run)."
        )
    else:
        _say(f"Removed {len(report.removed)} object(s), {_bytes(report.unreferenced_bytes)}.")


def _plan_line(item: guard.PlanItem) -> str:
    if item.before is None:
        return f"  create   {item.path}  ({_bytes(item.size or 0)})"
    if item.after is None:
        return f"  delete   {item.path}"
    return f"  replace  {item.path}  ({_bytes(item.size or 0)})"


@snap_app.command("restore")
@_handled
def snap_restore(
    snapshot_id: Annotated[str, typer.Argument(metavar="ID", help="Id or unique prefix.")],
    paths: Annotated[
        list[str] | None,
        typer.Option(
            "--paths",
            help="Only this path, relative to the flavor folder; repeat for more. "
            "A path the snapshot covers but does not hold is deleted.",
        ),
    ] = None,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Print the plan and change nothing.")
    ] = False,
    yes: YesOpt = False,
    root: RootOpt = None,
) -> None:
    """Put back files from a snapshot, through the write gate: the client must be
    closed, a pre-write snapshot is taken first, and `wowlab undo` reverses it."""
    store = snapshot.SnapshotStore()
    manifest = store.show(snapshot_id)
    inst, _ = _discover(root)
    if manifest.flavor_folder is None:
        raise CliError(f"snapshot {manifest.id} names no flavor folder; it cannot be restored")
    match = [f for f in inst.flavors if f.folder == manifest.flavor_folder]
    if not match:
        raise CliError(
            f"snapshot {manifest.id} is of {manifest.flavor_folder!r} in {manifest.install_root}; "
            f"{inst.root} has no such flavor (flavors: {_flavor_list(inst)})"
        )
    chosen = match[0]
    label = f"restore {manifest.id}"
    if paths:
        label += " (" + ", ".join(paths) + ")"
    wanted = list(paths) if paths else None  # no --paths: everything the snapshot holds
    with guard.transaction(chosen, label=label, dry_run=True) as tx:
        tx.restore(manifest.id, wanted)
        plan = tx.plan
    if not plan:
        _say(f"Nothing to restore: {chosen.folder} already matches snapshot {manifest.id}.")
        return
    _say(f"Restore from snapshot {manifest.id} into {chosen.path}: {len(plan)} change(s)")
    for item in plan:
        _say(_plan_line(item))
    if dry_run:
        _say("Dry run: nothing was changed.")
        return
    _confirm("Apply these changes?", yes)
    with guard.transaction(chosen, label=label) as tx:
        tx.restore(manifest.id, wanted)
        done = tx.plan
        if done != plan:
            # Raising inside the transaction makes the gate roll it back.
            raise CliError(
                "the files changed after the plan was shown, so the restore was rolled "
                "back; run the command again to see the new plan"
            )
    _say(f"Restored {len(done)} file(s). `wowlab undo` puts back what was there before.")


@app.command()
@_handled
def undo(yes: YesOpt = False) -> None:
    """Undo the most recent change made through the write gate (a restore, or an
    earlier undo), from the snapshot taken before it."""
    records = guard.history()
    if not records:
        raise CliError("nothing to undo: the write gate's journal is empty")
    last = records[-1]
    if not last.paths and not last.created_dirs:
        _say(f'The most recent transaction, {last.id} ("{last.label}"), changed no files.')
        return
    _say(f'Undo {last.id} ("{last.label}", {last.state}, {last.created_at})')
    _say(f"  in {last.flavor_path}:")
    for p in last.paths:
        if p.before is None:
            _say(f"  delete     {p.path}  (created by that transaction)")
        elif p.after is None:
            _say(f"  recreate   {p.path}  (deleted by that transaction)")
        else:
            _say(f"  put back   {p.path}  (as it was before that transaction)")
    for d in last.created_dirs:
        _say(f"  remove folder {d}  (created by that transaction, if empty)")
    _confirm("Undo it?", yes)
    now = guard.history()
    if not now or now[-1].id != last.id:
        raise CliError(
            "the journal changed after the plan was shown; nothing was undone, run the "
            "command again to see the new plan"
        )
    guard.undo()
    _say("Undone. This undo is journaled too: `wowlab undo` again reverses it.")


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
