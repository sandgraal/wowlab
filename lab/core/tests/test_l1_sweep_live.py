"""The L1 sweep: every read command and the UI's read endpoints, run against
the owner's install, change nothing in it (M16-38). Never run in CI
(ADR-0012); the owner runs it before each release:

    WOWLAB_WOW_ROOT="/path/to/World of Warcraft" uv run pytest -m live lab/core/tests/test_l1_sweep_live.py -s -p no:cacheprovider

Quit the game client and the Battle.net app first: both write into the
install (the client at logout and `/reload`, the Battle.net app on an update
or a repair), and either would show here as a change wowlab did not make.
The test fails at once, before reading anything else, when wowlab cannot
tell that the client is closed.

What it does, in order:

1. Fingerprints the install: every directory, file and link, with its mode,
   size and modification time (directories included), and the SHA-256 of
   every file's contents except under the install root's `Data/` folder and
   inside an executable (a path with a component guard treats as one:
   `.app`, `.exe`, `.dll`, ...). Those are tens of GB the Lab never opens
   (L7); their sizes, modes and times are still compared. It also takes the
   sizes, modes and times (no contents) of wowlab's own user data
   directory, where the snapshot store and the caches live.
2. Runs every read command, as a player would (`python -m wowlab_core`, no
   `--yes` anywhere, stdin closed so nothing can be asked), for every flavor
   folder, every account folder, and one character per flavor. `names fetch`
   runs only when every installed version already has every name table
   cached, since otherwise it downloads; nothing else here goes online.
   Nothing that writes runs: no `snap create`, no `--yes`.
3. Starts the `wowlab ui` server in this process and makes its read
   requests with the session key: the page shell, `/api/commands`,
   `/api/looks-page`, and `POST /api/run` for read commands only, never with
   a confirm token.
4. Fingerprints again and fails on any difference in the install, then on
   any difference in the user data directory, printing the first ones. Then
   it fails if a command crashed, exited with something other than 0 or 1
   (0 ok, 1 error; §6's exit codes: 2 is a usage mistake in this test, 3 a
   refusal by the write gate, which no read reaches), or printed something
   other than JSON under `--json`.

With `-s` it prints one block for the release record: the verdicts, every
command's exit code and every skip, with `account#N`, `character#N`,
`<root>`, `<home>` and `<login>` in place of the account and character
folders and the paths of this machine (ADR-0019 amendment of 2026-10-17).
That block is all it prints. A failure's message names the real paths, for
the owner's terminal only.

The constructed tests below it (not `live`, so CI runs them) check the
comparison on a constructed tree and run the same sweep on a copy of the
captured install `fixtures/macos-70888/` in `tmp_path`, with the user data
directory redirected there too. Nothing in this file writes into a real
install.
"""

from __future__ import annotations

import contextlib
import dataclasses
import getpass
import hashlib
import http.client
import importlib.metadata
import json
import os
import shutil
import stat
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import platformdirs
import pytest

from wowlab_core import gamedata, guard, install, process, snapshot, ui

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CAPTURE = FIXTURES / "macos-70888"  # a whole captured flavor, with its .build.info
LOG_CAPTURE = FIXTURES / "macos" / "forever" / "Logs"
FLAVOR = "_forever_"  # the provenance row's flavor folder; tests may name it (L6)
ACCOUNT = "LABACCOUNTA"
CHARACTER = "Sandgraal-Rizzen"
KEPT = "Keptchar-Rizzen"  # constructed: a copy of CHARACTER, kept only in a backup

# §6: 0 ok, 1 error. 2 (usage) would be a mistake in this test's argv; 3 is
# a refusal by the write gate, which a read never reaches.
ALLOWED_EXITS = frozenset({0, 1})
RUN_TIMEOUT_S = 600.0
SHOWN_DIFFERENCES = 40
_CHUNK = 1 << 20


# ─── the fingerprint ─────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Entry:
    """One path as `fingerprint` saw it. `sha256` is None for a directory, a
    link, and a file whose contents are not hashed; `target` is a link's."""

    kind: str  # "dir", "file", "link", "other", or "unreadable" (with `error`)
    mode: int = 0
    size: int = 0
    mtime_ns: int = 0
    sha256: str | None = None
    target: str | None = None
    error: str | None = None


def contents_skipped(parts: tuple[str, ...]) -> bool:
    """Whether a file's contents are left unhashed: under the install root's
    `Data/` (any case), or at or under a path component guard treats as an
    executable. Their size, mode and time are still compared."""
    if parts and parts[0].casefold() == "data":
        return True
    return any(guard._is_executable(part) for part in parts)


def _hash(path: str) -> str:
    """SHA-256 of a regular file, opened read-only and without following a
    link."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags)
    try:
        digest = hashlib.sha256()
        while chunk := os.read(fd, _CHUNK):
            digest.update(chunk)
        return digest.hexdigest()
    finally:
        os.close(fd)


def _entry(st: os.stat_result, kind: str) -> Entry:
    return Entry(kind=kind, mode=st.st_mode, size=st.st_size, mtime_ns=st.st_mtime_ns)


def fingerprint(
    root: Path, *, hash_file: Callable[[tuple[str, ...]], bool] = lambda p: True
) -> dict[str, Entry]:
    """Every path at and under `root`, keyed by its `/`-joined parts relative
    to `root` (`"."` is `root` itself). Lists, stats and reads only: links
    are recorded, never followed; a file is hashed when `hash_file(parts)`.
    A missing `root` is an empty fingerprint, so one created later shows as
    added. A folder that cannot be listed keeps its own entry with the error;
    a path that cannot be stat'ed is `unreadable`, and a file that cannot be
    read has no hash and the error: each compares equal to itself."""
    found: dict[str, Entry] = {}
    try:
        top = root.lstat()
    except FileNotFoundError:
        return found
    found["."] = _entry(top, "dir")
    stack: list[tuple[str, tuple[str, ...]]] = [(str(root), ())]
    while stack:
        folder, parts = stack.pop()
        try:
            with os.scandir(folder) as it:
                children = sorted(it, key=lambda e: e.name)
        except OSError as exc:
            key = "/".join(parts) or "."
            error = f"{type(exc).__name__}: {exc.strerror}"
            found[key] = dataclasses.replace(found[key], error=f"not listed: {error}")
            continue
        for child in children:
            sub = (*parts, child.name)
            key = "/".join(sub)
            try:
                st = child.stat(follow_symlinks=False)
            except OSError as exc:
                found[key] = Entry(kind="unreadable", error=f"{type(exc).__name__}: {exc.strerror}")
                continue
            if stat.S_ISLNK(st.st_mode):
                try:
                    target: str | None = str(Path(child.path).readlink())
                except OSError:
                    target = None
                found[key] = Entry(
                    kind="link",
                    mode=st.st_mode,
                    size=st.st_size,
                    mtime_ns=st.st_mtime_ns,
                    target=target,
                )
            elif stat.S_ISDIR(st.st_mode):
                found[key] = _entry(st, "dir")
                stack.append((child.path, sub))
            elif stat.S_ISREG(st.st_mode):
                digest: str | None = None
                error: str | None = None
                if hash_file(sub):
                    try:
                        digest = _hash(child.path)
                    except OSError as exc:
                        error = f"{type(exc).__name__}: {exc.strerror}"
                found[key] = Entry(
                    kind="file",
                    mode=st.st_mode,
                    size=st.st_size,
                    mtime_ns=st.st_mtime_ns,
                    sha256=digest,
                    error=error,
                )
            else:
                found[key] = _entry(st, "other")
    return found


def install_fingerprint(root: Path) -> dict[str, Entry]:
    """The install: contents hashed except under `Data/` and executables."""
    return fingerprint(root, hash_file=lambda parts: not contents_skipped(parts))


def store_fingerprint(path: Path) -> dict[str, Entry]:
    """wowlab's user data directory: sizes, modes and times, no contents."""
    return fingerprint(path, hash_file=lambda parts: False)


_FIELDS = ("kind", "mode", "size", "mtime_ns", "sha256", "target", "error")


def differences(
    before: dict[str, Entry], after: dict[str, Entry], limit: int = SHOWN_DIFFERENCES
) -> list[str]:
    """What changed from `before` to `after`, one line per path, sorted by
    path, at most `limit` lines and then one line counting the rest."""
    lines: list[str] = []
    for key in sorted(before.keys() | after.keys()):
        old, new = before.get(key), after.get(key)
        if old == new:
            continue
        if old is None:
            lines.append(f"added    {key} ({new.kind if new else '?'})")
        elif new is None:
            lines.append(f"removed  {key} ({old.kind})")
        else:
            changed = []
            for name in _FIELDS:
                a, b = getattr(old, name), getattr(new, name)
                if a != b:
                    shown = (oct(a), oct(b)) if name == "mode" else (a, b)
                    changed.append(f"{name} {shown[0]!r} -> {shown[1]!r}")
            lines.append(f"changed  {key}: " + "; ".join(changed))
    if len(lines) > limit:
        rest = len(lines) - limit
        lines = [*lines[:limit], f"... and {rest} more"]
    return lines


# ─── the read commands ───────────────────────────────────────────────────────


@dataclass
class Ran:
    """One command's run, or one UI request."""

    what: str
    exit_code: int
    json_out: bool = True
    stdout: str = ""
    stderr: str = ""
    problem: str | None = None

    def check(self) -> None:
        """Set `problem` when the run is not what a read may end with."""
        if self.exit_code not in ALLOWED_EXITS:
            self.problem = f"exit {self.exit_code}"
        elif "Traceback (most recent call last)" in self.stderr:
            self.problem = "a traceback on stderr"
        elif self.json_out and self.stdout.strip():
            try:
                json.loads(self.stdout)
            except ValueError:
                self.problem = "stdout is not JSON"
        elif self.json_out and self.exit_code == 0:
            self.problem = "no JSON on stdout"


def _cli(*args: str) -> Ran:
    """`wowlab <args>` as a player runs it: this interpreter, the working
    directory off the import path, stdin closed, never `--yes`."""
    assert "--yes" not in args and "-y" not in args, args
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    done = subprocess.run(
        [sys.executable, "-P", "-m", "wowlab_core", *args],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        env=env,
        timeout=RUN_TIMEOUT_S,
        check=False,
    )
    ran = Ran(
        what="wowlab " + " ".join(args),
        exit_code=done.returncode,
        json_out="--json" in args,
        stdout=done.stdout.decode("utf-8", "replace"),
        stderr=done.stderr.decode("utf-8", "replace"),
    )
    ran.check()
    return ran


def _report(ran: Ran) -> dict[str, Any] | None:
    try:
        value = json.loads(ran.stdout)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _accounts(flavor_dir: Path) -> list[str]:
    folder = flavor_dir / "WTF" / "Account"
    try:
        with os.scandir(folder) as it:
            return sorted(
                e.name
                for e in it
                if e.is_dir(follow_symlinks=False) and e.name.casefold() != "savedvariables"
            )
    except OSError:
        return []


def _has_combat_log(flavor_dir: Path) -> bool:
    try:
        with os.scandir(flavor_dir / "Logs") as it:
            return any(e.name.startswith("WoWCombatLog") for e in it)
    except OSError:
        return False


def _names_cached(inst: install.Install) -> bool:
    """Whether `names fetch` would download nothing: every flavor with a
    version has every name table cached for it (a flavor with no version is
    passed over without a request)."""
    for f in inst.flavors:
        if f.version is None:
            continue
        try:
            if gamedata.missing_tables(gamedata.NAME_TABLES, f.version):
                return False
        except (ValueError, OSError):
            return False
    return True


def _character(report: dict[str, Any] | None) -> dict[str, Any] | None:
    """One character row of `char list --json`: one with a readable
    lab-addon record when there is one, else the first folder."""
    rows = [r for r in (report or {}).get("characters", []) if isinstance(r, dict)]
    with_record = [r for r in rows if r.get("place") == "file" and not r.get("error")]
    pick = with_record or rows
    return pick[0] if pick else None


@dataclass
class Sweep:
    ran: list[Ran] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    # Account and character folders seen, each with its placeholder, so the
    # summary for the release record names none of them (ADR-0019 amendment
    # of 2026-10-17: the account folder stays scrubbed).
    hidden: dict[str, str] = field(default_factory=dict)
    summary: list[str] = field(default_factory=list)

    def cli(self, *args: str) -> Ran:
        ran = _cli(*args)
        self.ran.append(ran)
        return ran

    def hide(self, kind: str, value: object) -> None:
        """Give `value` (an account or character folder) a placeholder."""
        text = str(value or "")
        if text and text not in self.hidden:
            n = sum(1 for v in self.hidden.values() if v.startswith(kind + "#")) + 1
            self.hidden[text] = f"{kind}#{n}"

    def hide_rows(self, report: dict[str, Any] | None, key: str, label: str) -> None:
        """Every account and character folder a report's rows name."""
        for r in (report or {}).get(key, []):
            if not isinstance(r, dict):
                continue
            self.hide("account", r.get("account"))
            name = r.get(label)
            if name:
                self.hide("character", name)
                if r.get("realm_folder"):  # the same placeholder, with its folder
                    self.hidden.setdefault(f"{r['realm_folder']}/{name}", self.hidden[str(name)])


def _version() -> str:
    try:
        return importlib.metadata.version("wowlab")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _login() -> str | None:
    try:
        return getpass.getuser() or None
    except (OSError, KeyError, ImportError):
        return None


def record_summary(sweep: Sweep, root: Path, verdicts: list[str]) -> list[str]:
    """The lines for the release record: the verdicts, each run's exit code
    and command, and each skip, with the install root as `<root>`, the home
    folder as `<home>`, the login as `<login>`, and every account and
    character folder as `account#N` / `character#N`. It is all the sweep
    prints; only a failure's message names real paths, so it can be read
    on the terminal and is never pasted into the record."""
    swaps: list[tuple[str, str]] = [(str(root), "<root>")]
    with contextlib.suppress(RuntimeError, KeyError, OSError):
        swaps.append((str(Path.home()), "<home>"))
    swaps += sorted(sweep.hidden.items(), key=lambda kv: -len(kv[0]))
    login = _login()
    if login and len(login) >= 3:
        swaps.append((login, "<login>"))

    def scrub(text: str) -> str:
        for old, new in swaps:
            text = text.replace(old, new)
        return text

    lines = ["--- L1 sweep, for the release record ---"]
    lines += [scrub(v) for v in verdicts]
    for ran in sweep.ran:
        flag = f"  !! {ran.problem}" if ran.problem else ""
        lines.append(scrub(f"exit {ran.exit_code:>3}  {ran.what}{flag}"))
    lines += [scrub(f"skipped  {note}") for note in sweep.skipped]
    lines.append("--- end ---")
    return lines


def run_commands(inst: install.Install, sweep: Sweep) -> None:
    """Every read command, for every flavor and account, and one character
    per flavor."""
    root = inst.root
    sweep.cli("doctor", "--json")
    sweep.cli("install", "show", "--json")
    sweep.cli("filemap", "gaps", "--json")
    sweep.cli("tree", "--install", "--json")
    sweep.hide_rows(_report(sweep.cli("char", "list", "--json")), "characters", "label")
    sweep.cli("char", "list", "--summary", "--json")
    kept = _report(sweep.cli("char", "kept", "--json"))
    sweep.hide_rows(kept, "kept", "label")
    rows = [r for r in (kept or {}).get("kept", []) if isinstance(r, dict)]
    if rows:
        k = rows[0]
        kf = ("--flavor", str(k["flavor"])) if k.get("flavor") else ()
        who = ("--account", str(k["account"]), "--character", f"{k['realm_folder']}/{k['label']}")
        sweep.cli("char", "kept", *kf, *who, "--json")
    else:
        sweep.skipped.append("char kept --character: no character is kept only in a backup")
    sweep.cli("profile", "list", "--json")
    snaps = _report(sweep.cli("snap", "list", "--json"))
    if snaps and snaps.get("snapshots"):
        sweep.cli("snap", "show", "latest", "--json")
    else:
        sweep.skipped.append("snap show: the store holds no snapshot")
    if _names_cached(inst):
        sweep.cli("names", "fetch", "--json")
    else:
        sweep.skipped.append("names fetch: a name table is not cached, so it would go online")
    for explained in (root / ".build.info", root / "Data"):
        sweep.cli("explain", "--json", "--", str(explained))

    for f in inst.flavors:
        fl = ("--flavor", f.folder)
        sweep.cli("tree", *fl, "--json")
        sweep.cli("tree", "--explain", *fl, "--json")
        for rel in ("WTF/Config.wtf", "WTF/Account", "Interface/AddOns", "Logs"):
            sweep.cli("explain", *fl, "--verbose", "--json", "--", str(f.path / rel))
        sweep.cli("cvar", "list", *fl, "--json")
        sweep.cli("addons", "list", *fl, "--json")
        sweep.cli("addons", "check", *fl, "--json")
        sweep.cli("char", "kept", *fl, "--json")
        if _has_combat_log(f.path):
            sweep.cli("log", "tail", "-n", "20", *fl, "--json")
        else:
            sweep.skipped.append(f"log tail: {f.folder} has no combat log")
        accounts = _accounts(f.path)
        for account in accounts:
            sweep.hide("account", account)
        if not accounts:
            sweep.skipped.append(f"account commands: {f.folder} has no account folder")
        for account in accounts:
            ac = (*fl, "--account", account)
            sweep.cli("cvar", "list", "--scope", "account", *ac, "--json")
            sweep.cli("binds", "list", *ac, "--json")
            sweep.cli("macros", "list", *ac, "--json")
            sweep.cli("macros", "check", *ac, "--json")
            listing = _report(sweep.cli("sv", "list", *ac, "--json"))
            sweep.hide_rows(listing, "files", "character_folder")
            sweep.cli("sv", "list", "--orphans", *ac, "--json")
            sweep.cli("sv", "list", "--unreadable", *ac, "--json")
            files = [
                x
                for x in (listing or {}).get("files", [])
                if isinstance(x, dict) and not x.get("backup")
            ]
            if files:
                smallest = min(files, key=lambda x: int(x.get("size", 0)))
                path = f.path / str(smallest["path"])
                sweep.cli("sv", "dump", "--install-only", *ac, "--json", "--", str(path))
            else:
                sweep.skipped.append(f"sv dump: {f.folder} {account} has no SavedVariables file")
        chars = _report(sweep.cli("char", "list", *fl, "--brief", "--json"))
        sweep.hide_rows(chars, "characters", "label")
        sweep.cli("char", "list", "--summary", *fl, "--json")
        row = _character(chars)
        if row is None:
            sweep.skipped.append(f"character commands: {f.folder} has no character folder")
            continue
        ch = (*fl, "--account", str(row["account"]), "--character")
        ch = (*ch, f"{row['realm_folder']}/{row['label']}")
        sweep.cli("cvar", "list", "--scope", "character", *ch, "--json")
        sweep.cli("binds", "list", *ch, "--json")
        sweep.cli("macros", "list", *ch, "--json")
        sweep.cli("macros", "check", *ch, "--json")
        sweep.cli("sv", "list", *ch, "--json")
        for sub in ("show", "gear", "binds", "macros", "history"):
            sweep.cli("char", sub, *ch, "--json")
        if _has_combat_log(f.path):
            sweep.cli("binds", "coach", *ch, "--json")


# ─── the UI's read endpoints ─────────────────────────────────────────────────


@dataclass
class _Ui:
    port: int
    token: str = field(repr=False)
    boot: str = field(repr=False)

    def request(
        self, method: str, path: str, body: dict[str, Any] | None = None, *, key: bool = True
    ) -> tuple[int, bytes]:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=RUN_TIMEOUT_S)
        try:
            headers = {"X-Wowlab-Session": self.token} if key else {}
            data = None
            if body is not None:
                data = json.dumps(body).encode("utf-8")
                headers["Content-Type"] = "application/json"
            conn.request(method, path, body=data, headers=headers)
            response = conn.getresponse()
            return response.status, response.read()
        finally:
            conn.close()


def _serving() -> Iterator[_Ui]:
    server = ui.make_server(timeout_s=RUN_TIMEOUT_S)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield _Ui(server.port, server.token, server.boot)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=30)


def _ui_get(sweep: Sweep, what: str, status: int, ok: bool) -> None:
    ran = Ran(f"{what} (HTTP {status})", 0, json_out=False)
    if not ok:
        ran.problem = f"HTTP {status}"
    sweep.ran.append(ran)


def _ui_run(client: _Ui, sweep: Sweep, command: list[str], params: dict[str, Any]) -> None:
    what = "ui POST /api/run " + " ".join(command) + (f" {params}" if params else "")
    status, body = client.request("POST", "/api/run", {"command": command, "params": params})
    if status != 200:
        sweep.ran.append(Ran(what, -1, json_out=False, problem=f"HTTP {status}: {body[:200]!r}"))
        return
    answer = json.loads(body)
    ran = Ran(
        what,
        int(answer["exit_code"]),
        stdout=str(answer.get("stdout", "")),
        stderr=str(answer.get("stderr", "")),
    )
    ran.check()
    if answer.get("timed_out") or answer.get("truncated"):
        ran.problem = ran.problem or "timed out or cut"
    sweep.ran.append(ran)


def run_ui(inst: install.Install, sweep: Sweep) -> None:
    """The UI server's read requests, all with the session key, none with a
    confirm token."""
    for client in _serving():
        status, page = client.request("GET", f"/?boot={client.boot}", key=False)
        _ui_get(sweep, "ui GET /?boot=", status, status == 200 and b"wowlab-session" in page)
        status, _ = client.request("GET", "/", key=False)
        _ui_get(sweep, "ui GET /", status, status == 200)
        status, body = client.request("GET", "/api/commands")
        _ui_get(sweep, "ui GET /api/commands", status, status == 200)
        listed = json.loads(body) if status == 200 else {}
        leaves = {tuple(c["path"]): c for c in listed.get("commands", [])}
        status, body = client.request("GET", "/api/looks-page")
        # 200 with the page `looks page` wrote; 404 `no_page` without one;
        # 403 `not_a_page` for a file there that is not one. Never `token`.
        _ui_get(
            sweep,
            "ui GET /api/looks-page",
            status,
            status == 200 or (status in (403, 404) and b"page" in body),
        )
        runs: list[tuple[list[str], dict[str, Any]]] = [
            (["doctor"], {}),
            (["install", "show"], {}),
            (["filemap", "gaps"], {}),
            (["tree"], {"whole_install": True}),
            (["snap", "list"], {}),
            (["char", "list"], {"brief": True}),
            (["char", "kept"], {}),
        ]
        for f in inst.flavors:
            fl = {"flavor": f.folder}
            runs += [
                (["tree"], fl),
                (["addons", "list"], fl),
                (["addons", "check"], fl),
                (["cvar", "list"], fl),
                (["sv", "list"], fl),
                (["char", "list"], {**fl, "summary": True}),
            ]
        for command, params in runs:
            leaf = leaves.get(tuple(command))
            if leaves and leaf is None:
                sweep.ran.append(Ran("ui " + " ".join(command), -1, problem="not listed"))
                continue
            _ui_run(client, sweep, command, params)


# ─── the sweep ───────────────────────────────────────────────────────────────


def sweep_and_compare(root: Path, data_dir: Path) -> Sweep:
    """Fingerprint, run every read, fingerprint again; fail on a change in
    the install, then in `data_dir`, then on a run that ended badly."""
    inst = install.discover(root)
    before = install_fingerprint(inst.root)
    data_before = store_fingerprint(data_dir)
    sweep = Sweep()
    try:
        run_commands(inst, sweep)
        run_ui(inst, sweep)
    finally:
        after = install_fingerprint(inst.root)
        data_after = store_fingerprint(data_dir)
    hashed = sum(1 for e in before.values() if e.sha256 is not None)
    changed = differences(before, after)
    data_changed = differences(data_before, data_after)
    flagged = [r for r in sweep.ran if r.problem]
    verdicts = [
        f"wowlab {_version()}; flavors: "
        + ", ".join(f"{f.folder} {f.version}" for f in inst.flavors),
        f"install: {len(before)} paths, {hashed} files hashed; "
        + ("unchanged" if not changed else f"CHANGED ({len(changed)} lines in the failure)"),
        "user data directory: " + ("unchanged" if not data_changed else "CHANGED"),
        f"runs: {len(sweep.ran)}, flagged: {len(flagged)}",
    ]
    sweep.summary = record_summary(sweep, inst.root, verdicts)
    print("\n" + "\n".join(sweep.summary))
    assert not changed, "the install changed (L1):\n" + "\n".join(changed)
    assert not data_changed, f"wowlab's user data directory {data_dir} changed:\n" + "\n".join(
        data_changed
    )
    bad = [f"{r.what}: {r.problem}\n{r.stderr[-2000:]}" for r in flagged]
    assert not bad, "runs that did not end as a read may:\n" + "\n".join(bad)
    return sweep


# ─── live ────────────────────────────────────────────────────────────────────


def live_root() -> Path:
    """The install `WOWLAB_WOW_ROOT` names; skips when it is unset or is not
    an install wowlab can read."""
    value = os.environ.get(install.ENV_ROOT, "")
    if not value.strip():
        pytest.skip(f"set {install.ENV_ROOT} to the install to run the L1 sweep")
    try:
        inst = install.discover(Path(value))
    except install.InstallError as exc:
        pytest.skip(f"{install.ENV_ROOT} is not an install wowlab can read: {exc}")
    if not inst.flavors:
        pytest.skip(f"{install.ENV_ROOT} names an install with no flavor folder")
    return inst.root


def _client_closed(inst: install.Install) -> None:
    names: list[str] = []
    for f in inst.flavors:
        names += guard._client_names(f.path)
    state = process.client_state(
        [inst.root], flavor_folders=[f.folder for f in inst.flavors], extra_names=names
    )
    if state is not process.ClientState.NOT_RUNNING:
        pytest.fail(f"the game client is {state.value}: quit it (and the Battle.net app) first")


@pytest.mark.live
@pytest.mark.timeout(3600)
def test_live_l1_sweep_changes_nothing_in_the_install() -> None:
    root = live_root()
    _client_closed(install.discover(root))
    sweep_and_compare(root, snapshot.user_data_dir())


# ─── constructed ─────────────────────────────────────────────────────────────


def test_live_root_skips_without_the_variable_constructed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(install.ENV_ROOT, raising=False)
    with pytest.raises(pytest.skip.Exception, match="set WOWLAB_WOW_ROOT"):
        live_root()
    monkeypatch.setenv(install.ENV_ROOT, "   ")
    with pytest.raises(pytest.skip.Exception, match="set WOWLAB_WOW_ROOT"):
        live_root()


def test_live_root_skips_on_a_folder_that_is_not_an_install_constructed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(install.ENV_ROOT, str(tmp_path))
    with pytest.raises(pytest.skip.Exception, match="not an install"):
        live_root()
    monkeypatch.setenv(install.ENV_ROOT, str(tmp_path / "missing"))
    with pytest.raises(pytest.skip.Exception, match="not an install"):
        live_root()


def _tree(tmp_path: Path) -> Path:
    """A constructed install-shaped tree: a flavor with WTF, an executable
    bundle and a Data/ folder."""
    root = tmp_path / "World of Warcraft"
    (root / FLAVOR / "WTF").mkdir(parents=True)
    (root / FLAVOR / "WTF" / "Config.wtf").write_bytes(b'SET a "1"\n')
    app = root / FLAVOR / "World of Warcraft.app" / "Contents" / "MacOS"
    app.mkdir(parents=True)
    (app / "World of Warcraft").write_bytes(b"constructed")
    (root / "Data" / "data").mkdir(parents=True)
    (root / "Data" / "data" / "data.000").write_bytes(b"0123456789")
    (root / ".build.info").write_bytes(b"constructed\n")
    return root


def _same_times(path: Path, st: os.stat_result) -> None:
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))


def test_fingerprint_is_stable_and_hashes_only_what_it_should_constructed(
    tmp_path: Path,
) -> None:
    root = _tree(tmp_path)
    first = install_fingerprint(root)
    assert differences(first, install_fingerprint(root)) == []
    config = first[f"{FLAVOR}/WTF/Config.wtf"]
    assert config.sha256 == hashlib.sha256(b'SET a "1"\n').hexdigest()
    assert first["Data/data/data.000"].sha256 is None
    assert first["Data/data/data.000"].size == 10
    assert first[f"{FLAVOR}/World of Warcraft.app/Contents/MacOS/World of Warcraft"].sha256 is None
    assert first[".build.info"].sha256 is not None
    assert first["."].kind == "dir" and first[f"{FLAVOR}/WTF"].kind == "dir"


def test_same_size_same_time_content_change_is_found_constructed(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    target = root / FLAVOR / "WTF" / "Config.wtf"
    folder_st = target.parent.stat()
    st = target.stat()
    before = install_fingerprint(root)
    target.write_bytes(b'SET a "2"\n')  # same size
    _same_times(target, st)
    _same_times(target.parent, folder_st)
    lines = differences(before, install_fingerprint(root))
    assert len(lines) == 1
    assert lines[0].startswith(f"changed  {FLAVOR}/WTF/Config.wtf: sha256 ")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes")
def test_mode_change_is_found_constructed(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    target = root / FLAVOR / "WTF" / "Config.wtf"
    before = install_fingerprint(root)
    target.chmod(0o600 if (target.stat().st_mode & 0o777) != 0o600 else 0o644)
    lines = differences(before, install_fingerprint(root))
    assert len(lines) == 1 and "mode " in lines[0] and "sha256" not in lines[0]


def test_directory_time_change_is_found_constructed(tmp_path: Path) -> None:
    """A temp file made and removed again leaves only the folder's mtime."""
    root = _tree(tmp_path)
    folder = root / FLAVOR / "WTF"
    before = install_fingerprint(root)
    st = folder.stat()
    os.utime(folder, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    lines = differences(before, install_fingerprint(root))
    assert lines == [
        f"changed  {FLAVOR}/WTF: mtime_ns {st.st_mtime_ns!r} -> {st.st_mtime_ns + 1_000_000_000!r}"
    ]


def test_added_and_removed_paths_are_found_constructed(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    folder = root / FLAVOR / "WTF"
    folder_st = folder.stat()
    before = install_fingerprint(root)
    (folder / "Config.wtf").unlink()
    (folder / "Config.wtf.lock").write_bytes(b"")
    _same_times(folder, folder_st)
    lines = differences(before, install_fingerprint(root))
    assert lines == [
        f"removed  {FLAVOR}/WTF/Config.wtf (file)",
        f"added    {FLAVOR}/WTF/Config.wtf.lock (file)",
    ]


def test_data_and_executables_compare_by_size_and_time_constructed(tmp_path: Path) -> None:
    """Contents under Data/ and in an executable are not hashed (documented):
    a same-size, same-time rewrite there is not seen, a size change is."""
    root = _tree(tmp_path)
    blob = root / "Data" / "data" / "data.000"
    st = blob.stat()
    before = install_fingerprint(root)
    blob.write_bytes(b"9876543210")
    _same_times(blob, st)
    assert differences(before, install_fingerprint(root)) == []
    blob.write_bytes(b"01234567890")
    _same_times(blob, st)
    lines = differences(before, install_fingerprint(root))
    assert lines == ["changed  Data/data/data.000: size 10 -> 11"]


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_links_are_recorded_not_followed_constructed(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "big").write_bytes(b"x")
    (root / FLAVOR / "Interface").symlink_to(outside, target_is_directory=True)
    first = install_fingerprint(root)
    link = first[f"{FLAVOR}/Interface"]
    assert link.kind == "link" and link.target == str(outside)
    assert not any(k.startswith(f"{FLAVOR}/Interface/") for k in first)


@pytest.mark.skipif(
    sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="POSIX permissions, and not as root",
)
def test_a_folder_that_cannot_be_listed_keeps_its_entry_constructed(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    locked = root / FLAVOR / "WTF"
    locked.chmod(0)
    try:
        first = install_fingerprint(root)
        assert differences(first, install_fingerprint(root)) == []
    finally:
        locked.chmod(0o755)
    entry = first[f"{FLAVOR}/WTF"]
    assert entry.kind == "dir" and entry.error is not None and "not listed" in entry.error
    assert f"{FLAVOR}/WTF/Config.wtf" not in first


def test_missing_root_is_empty_and_its_creation_is_found_constructed(tmp_path: Path) -> None:
    data = tmp_path / "data"
    before = store_fingerprint(data)
    assert before == {}
    (data / "store").mkdir(parents=True)
    (data / "store" / "x").write_bytes(b"1")
    lines = differences(before, store_fingerprint(data))
    assert lines == ["added    . (dir)", "added    store (dir)", "added    store/x (file)"]
    assert store_fingerprint(data)["store/x"].sha256 is None


def test_differences_are_capped_constructed() -> None:
    before = {f"f{i:03}": Entry(kind="file", size=i) for i in range(50)}
    lines = differences(before, {}, limit=5)
    assert lines[:5] == [f"removed  f{i:03} (file)" for i in range(5)]
    assert lines[5] == "... and 45 more"


def test_a_run_that_ends_badly_is_flagged_constructed() -> None:
    cases = [
        (Ran("a", 0, stdout='{"x": 1}'), None),
        (Ran("b", 1, stdout='{"error": "x"}'), None),
        (Ran("c", 2, stdout=""), "exit 2"),
        (Ran("d", 3, stdout="{}"), "exit 3"),
        (Ran("e", 0, stdout="not json"), "stdout is not JSON"),
        (Ran("f", 0, stdout=""), "no JSON on stdout"),
        (Ran("g", 1, stderr="Traceback (most recent call last):\n"), "a traceback on stderr"),
        (Ran("h", 0, json_out=False, stdout="text"), None),
    ]
    for ran, problem in cases:
        ran.check()
        assert ran.problem == problem, ran.what


@pytest.fixture
def captured_install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """The captured flavor `macos-70888` copied into `tmp_path` as an install,
    with a captured combat log beside it and a constructed Data/ and
    executable bundle; HOME and every user data variable point into
    `tmp_path`, so the children's user data directory is there too."""
    home = tmp_path / "home"
    home.mkdir()
    for name in (
        "HOME",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "XDG_DATA_HOME",
        # On Windows platformdirs asks the shell for the folder (not the
        # variables above) unless one of these overrides is set.
        "WIN_PD_OVERRIDE_LOCAL_APPDATA",
        "WIN_PD_OVERRIDE_APPDATA",
    ):
        monkeypatch.setenv(name, str(home))
    # The children's user data directory is under tmp_path, or nothing runs:
    # `snap create` below would otherwise write into the real store.
    probe = subprocess.run(
        [
            sys.executable,
            "-P",
            "-c",
            "import platformdirs; print(platformdirs.user_data_path('wowlab'))",
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=True,
        timeout=60,
        text=True,
    )
    child_data = Path(probe.stdout.strip())
    if tmp_path.resolve() not in child_data.resolve().parents:
        pytest.skip(f"the user data directory does not follow the redirect here: {child_data}")
    monkeypatch.delenv(install.ENV_FLAVOR, raising=False)
    cache = tmp_path / "in-process-data"
    monkeypatch.setattr(platformdirs, "user_data_path", lambda *a, **k: cache)
    root = tmp_path / "World of Warcraft"
    root.mkdir()
    shutil.copy2(CAPTURE / ".build.info", root / ".build.info")
    shutil.copytree(CAPTURE / "forever", root / FLAVOR)
    shutil.copytree(LOG_CAPTURE, root / FLAVOR / "Logs")
    app = root / FLAVOR / "World of Warcraft.app" / "Contents" / "MacOS"
    app.mkdir(parents=True)
    (app / "World of Warcraft").write_bytes(b"constructed")
    (root / "Data" / "data").mkdir(parents=True)
    (root / "Data" / "data" / "data.000").write_bytes(b"constructed")
    monkeypatch.setenv(install.ENV_ROOT, str(root))
    monkeypatch.chdir(tmp_path)
    # A character kept only in a backup (constructed): a copy of the captured
    # character folder, snapshotted into the redirected store, then removed,
    # so `snap show`, `char history` and `char kept --character` have data.
    characters = root / FLAVOR / "WTF" / "Account" / ACCOUNT / "1"
    shutil.copytree(characters / CHARACTER, characters / KEPT)
    took = _cli("snap", "create", "--flavor", FLAVOR, "--json")
    assert took.exit_code == 0, took.stderr
    shutil.rmtree(characters / KEPT)
    return root, home


def test_sweep_on_a_captured_install_changes_nothing_constructed(
    captured_install: tuple[Path, Path],
) -> None:
    """The live test's own sweep, on a copy of a captured install: every
    command's argv is accepted (no exit 2), nothing crashes, the copy and
    the redirected user data directory are unchanged."""
    root, home = captured_install
    sweep = sweep_and_compare(root, home)
    ran = {r.what for r in sweep.ran}
    # The character commands ran: the capture has a lab-addon record.
    assert any(w.startswith("wowlab char gear ") for w in ran)
    assert any(w.startswith("wowlab sv dump ") for w in ran)
    assert any(w.startswith("wowlab log tail ") for w in ran)
    assert any(w.startswith("ui POST /api/run char list") for w in ran)
    assert any(w.startswith("wowlab snap show latest") for w in ran)
    assert f"--character 1/{KEPT}" in " ".join(w for w in ran if w.startswith("wowlab char kept"))
    assert any(s.startswith("names fetch:") for s in sweep.skipped)
    # The summary for the release record names no account or character
    # folder and no path of this machine, and still lists every run.
    summary = "\n".join(sweep.summary)
    for secret in (ACCOUNT, CHARACTER, KEPT, str(root), str(home), str(root.parent)):
        assert secret not in summary, secret
    assert "account#1" in summary and "character#1" in summary and "<root>" in summary
    assert len([x for x in sweep.summary if x.startswith("exit ")]) == len(sweep.ran)
    assert "install: " in summary and "; unchanged" in summary


def test_record_summary_holds_no_folder_or_path_constructed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Constructed names: an account folder, a character folder with its
    realm folder, a home folder holding the install, and a login."""
    home = Path("/Users/labloginz") if os.sep == "/" else Path("C:\\Users\\labloginz")
    root = home / "Games" / "World of Warcraft"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(getpass, "getuser", lambda: "labloginz")
    account, character, realm = "90000001#6", "Labchard-Labrealmg", "1"
    sweep = Sweep()
    sweep.hide("account", account)
    sweep.hide_rows(
        {"characters": [{"account": account, "realm_folder": realm, "label": character}]},
        "characters",
        "label",
    )
    sv = (
        root / FLAVOR / "WTF" / "Account" / account / realm / character / "SavedVariables" / "X.lua"
    )
    sweep.ran += [
        Ran(f"wowlab binds list --account {account} --character {realm}/{character} --json", 0),
        Ran(f"wowlab sv dump --install-only --account {account} --json -- {sv}", 1),
        Ran(f"wowlab explain --json -- {root / 'Data'}", 0),
        Ran("wowlab char list --json", 2, problem="exit 2"),
    ]
    sweep.skipped.append(f"sv dump: {FLAVOR} {account} has no SavedVariables file")
    lines = record_summary(sweep, root, [f"install {root} by labloginz"])
    text = "\n".join(lines)
    for secret in (account, character, str(root), str(home), "labloginz"):
        assert secret not in text, secret
    assert "--account account#1 --character character#1 --json" in text
    assert "<root>" in text and "<login>" in text
    assert "exit   2  wowlab char list --json  !! exit 2" in text
    assert f"skipped  sv dump: {FLAVOR} account#1 has no SavedVariables file" in text
