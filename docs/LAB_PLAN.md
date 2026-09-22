# Lab — Plan

**Status:** Wave 1 specified (2026-09-20), revised 2026-09-21 when the Lab
became the whole repository (ADR-0025). Later waves are chosen by the owner
after each wave ships (ADR-0024); nothing beyond Wave 1 is specified here on
purpose.

The Lab is the application this repository exists for, not a track beside
another product. It is a local toolchain for one player's own machine: it
reads a World of Warcraft install directly, explains what is in it, snapshots
it, and lets the owner change things and change them back (ADR-0019).

Read `AGENTS.md`, then this, then `docs/LAB_FORMATS.md` (file grammars),
`docs/LAB_FILE_MAP.md` (what every path in an install is), and ADR-0019
through ADR-0025.

## 1. Purpose

Three owner goals drive the work:

1. Know what every file in the install does, and be able to read all of it
   from code.
2. Modify the client's configurable state at will and return to any earlier
   state, without thinking about it.
3. Have a base that character-customization tools, offline character tools
   and addon experiments can be built on in later waves without each one
   re-solving install discovery, parsing and safe writes.

Wave 1 delivers the base: a Python library and a CLI. No web UI, no 3D, no
addon. `docs/LAB_IDEAS.md` is the menu for later waves.

## 2. Target clients

The library is flavor-agnostic (ADR-0020). It must work against any modern
(CASC-era) WoW product installed by Battle.net, and is developed against two:

| Product | Flavor folder | Why it matters |
|---|---|---|
| Retail (Midnight, 12.x) | `_retail_` | The owner's main characters |
| World of Warcraft: Forever (beta from 2026-09-17, launch announced for 2026-11-04) | reported as `_classic_beta_` during beta | The owner's new focus; character customization and offline tools |

Reported facts about Forever that the code must **not** depend on until
M10-03 verifies them on the owner's machine: beta build `1.60.1.x`, interface
version `16001`, Battle.net product code `wow_classic_beta`, and an addon API
that matches retail 12.1.5 (including secret values in combat). The beta
wipes before launch and the flavor folder will probably change at launch.
That is why flavor, product and build always come from discovery and never
from a constant.

## 3. Trust boundary

- **Local-only.** The Lab runs on the owner's machine, as the owner, against
  the owner's install. It is never distributed as a binary and never deployed
  as a service (ADR-0019).
- **Never uploads anything.** Nothing read from an install leaves the
  machine. The only network traffic is `gamedata` downloading public game
  tables (§6.6), which sends nothing about the install beyond a build string.
- **The repository is public, so fixtures are scrubbed.** Anything captured
  from a real install passes through the scrub tool before it is committed
  (§8).

## 4. Hard invariants

These are the hard invariants in `AGENTS.md`, restated here because the
module specifications below cite them by number. Violating any of these is a
bug even if tests pass.

**L1 — Reads never write.** Every module except `guard` opens the install
read-only. No temp files, caches or lock files inside the install. Caches
and the snapshot store live under the user data directory (§6.9).

**L2 — One write gate.** Every byte written into an install goes through
`wowlab_core.guard` (ADR-0021): client not running, path inside an
allowlisted subtree, snapshot taken first, operation journaled, atomic
replace. There is no second code path, no `force` that skips the snapshot.

**L3 — No Lua execution.** SavedVariables and every other Lua-syntax file
are parsed as data by a constrained literal parser. No interpreter, no
`load`, no third-party library that evaluates. Functions, metatables, calls,
operators and bare identifiers as values are rejected with a position.

**L4 — Lossless.** Parsers keep what they do not understand. `luadata` keeps
key order, key style, and the original text of every number. The WTF text
parsers keep every line, including ones they cannot classify. For an
unmodified document, `serialize(parse(x)) == x` byte for byte on every real
fixture.

**L5 — Game data is keyed by build and never overwritten.** A cached table
for build A is never replaced by build B (ADR-0022).

**L6 — Nothing is hard-coded about a flavor.** No `_retail_`, no
`_classic_beta_`, no product code, no interface number, no build number in
library code. Tests may name them; the library discovers them (ADR-0020).

**L7 — Out of scope, permanently, in this repository** (ADR-0023): process
memory reads or writes, DLL/dylib injection, packet capture or modification,
input automation, editing anything under `Data/` or any executable, and
bypassing the client's integrity checks. A ticket that seems to need one of
these is mis-specified; stop and report.

**L8 — Real fixtures.** Any parser of an external format is graded against
real captures with a provenance row (ADR-0012). Constructed inputs are
allowed only for hostile-input and boundary tests, and are labelled as such.

## 5. Architecture

```
                     ┌────────────────────────────────────────────┐
                     │ wowlab CLI (typer)                         │
                     └───────────────┬────────────────────────────┘
                                     │
┌──────────┐  ┌────────┐  ┌──────────┴─┐  ┌──────────┐  ┌──────────┐
│ install  │─▶│ layout │─▶│ parsers    │  │ gamedata │  │ snapshot │
│ discovery│  │ walker │  │ luadata    │  │ wago.tools│  │ store    │
└──────────┘  └────────┘  │ wtfconfig  │  │ + cache  │  └────┬─────┘
      ▲                   │ toc        │  └──────────┘       │
      │                   │ combatlog  │                     ▼
┌─────┴────┐              └────────────┘              ┌──────────────┐
│ process  │─────────────────────────────────────────▶│ guard        │
│ detection│                                          │ (write gate) │
└──────────┘                                          └──────┬───────┘
                                                             ▼
                                              install (WTF/, Interface/, Fonts/)
```

Everything left of `guard` is read-only. `guard` is the only arrow pointing
back into the install.

## 6. Module specifications

Package `wowlab_core`, source at `lab/core/src/wowlab_core/`, tests at
`lab/core/tests/`. Python 3.12, `mypy --strict`, Pydantic v2 models for
anything that crosses a module boundary, `pathlib` everywhere. Runtime
dependencies are limited to: `pydantic`, `typer`, `httpx`, `platformdirs`,
`psutil`. Adding one needs a line in the PR body saying why the standard
library does not do.

### 6.1 `install` — discovery (M10-05)

Input: nothing, or an explicit root. Output: `Install` with its `Flavor`s.

Resolution order for the root: explicit argument → `WOWLAB_WOW_ROOT` →
platform defaults (`/Applications/World of Warcraft`,
`C:\Program Files (x86)\World of Warcraft`, `C:\Program Files\World of Warcraft`,
and the same under each fixed drive root on Windows). No registry reads, no
Battle.net database reads in Wave 1. Linux (Wine, Lutris, Proton) is
supported through the explicit root only.

A directory is an install if it contains `.build.info`. Parse it per
`docs/LAB_FORMATS.md` §1: one row per product, with `Product`, `Version`,
`Build Key`, `Branch`, `Active`, `Tags`. A flavor is a child directory whose
name matches `_*_` and which contains `.flavor.info`; its product code comes
from that file (`docs/LAB_FORMATS.md` §2) and is joined to the `.build.info`
row with the same `Product`.

```python
class Flavor(BaseModel, frozen=True):
    folder: str  # "_retail_", as found on disk
    product: str  # "wow", as found in .flavor.info
    version: str | None  # "12.1.5.65432" from .build.info; None if no row matches
    build: int | None  # 65432
    build_key: str | None
    path: Path


class Install(BaseModel, frozen=True):
    root: Path
    flavors: tuple[Flavor, ...]  # sorted by folder
    raw_build_info: str  # preserved (L4)
```

Unknown `.build.info` columns are kept in a `extra: dict[str, str]` per row.
A flavor folder with no matching row is returned with `version=None`, not
dropped. Discovery never raises on a partial install; it raises only when the
root has no `.build.info`.

*Amended 2026-09-22 (M10-05 reviews):*
(1) **Windows defaults.** For each drive `os.listdrives()` returns, the
search tries `<drive>\World of Warcraft`, then
`<drive>\Program Files (x86)\World of Warcraft`, then
`<drive>\Program Files\World of Warcraft`. The system drive
(`%SystemDrive%`, else `C:`) comes first. Every listed drive is probed,
because telling fixed drives from removable or network ones needs a Win32
call, which is out of scope. (2) **Explicit root and `WOWLAB_WOW_ROOT` are
final.** If either names a directory that is not an install, discovery raises
`NotAnInstallError` and does not fall through to a default. A default that
holds a `.build.info` that is not a regular file, or cannot be read, is
reported, not skipped. (3) **Frozen and hashable.** Each `.build.info` row is
a `BuildInfoRow` whose `extra` is an immutable tuple of `(name, value)` pairs
in header order (read-only `extra_map` accessor), not a `dict`, so `Install`
is hashable and truly frozen. `Install.products` holds the rows in file
order. (4) **Undecodable bytes.** `Install.raw_build_info` is `bytes`
(lossless, L4), carried in JSON as base64 and validated back, so
`model_validate_json(model_dump_json())` is the identity. Parsed text fields
decode with `errors="replace"`. `Install.decode_errors` is true when
`.build.info` or any `.flavor.info` had bytes that are not UTF-8.
(5) **`Flavor.matching_rows`** counts the rows that name the flavor's
product. When there are several, the first active one wins, else the first.
This is a deterministic tie-break, not client behaviour **[verify]**.
(6) **`Install.other_dirs`** lists children named `_*_` that are not reported
as flavors, because they have no regular `.flavor.info` or are symlinks.
Symlinked `_*_` folders are not followed, consistent with M10-06.
(7) **Typed errors.** `NotAnInstallError` carries a `reason` (`missing`,
`not_regular`, `unreadable`); a permission refusal is never reported as
absent. The one error is a root without a readable, regular `.build.info`.
A root that looks like a flavor folder gets a pointer to its parent. When no
default holds an install, discovery raises `InstallNotFoundError`, which
lists the locations it searched. Executable names are not part of
discovery; `guard` finds them itself (M10-11 amendment).

### 6.2 `layout` — typed walk of a flavor (M10-06)

Pure read. Produces the inventory every later tool starts from:

- `accounts()` → account folders under `WTF/Account/`, each with its realms
  and characters (`WTF/Account/<ACCOUNT>/<Realm>/<Character>/`).
- `saved_variables(scope=...)` → every SavedVariables file with its scope
  (`account` or `character`), owning addon name, size and mtime. Includes the
  Blizzard-owned `SavedVariables.lua` files and `*.lua.bak` siblings, flagged.
- `addons()` → every directory under `Interface/AddOns/` with its parsed TOC
  files (one addon can carry several: `Foo.toc`, `Foo_Mainline.toc`,
  `Foo_Vanilla.toc`, …), which TOC the running flavor would pick if that can
  be decided from the file names alone, and whether the directory is a
  Blizzard addon.
- `wtf_files()` → `Config.wtf`, per-account and per-character
  `config-cache.wtf`, `bindings-cache.wtf`, `macros-cache.txt`,
  `layout-local.txt`, `chat-cache.txt`, `AddOns.txt`.
- `other()` → `Cache/`, `Logs/`, `Screenshots/`, `Errors/`, `Fonts/`, and
  loose override files under `Interface/` outside `AddOns/`.
- `classify(path)` → the `docs/LAB_FILE_MAP.md` entry for any path inside
  the install: what it is, who writes it, when, whether it is safe to edit,
  which module parses it. The file map's table is the data source; keep them
  in one place (a TOML or JSON data file the doc is generated from, or the
  doc parsed at build time; implementer's choice, stated in the PR).

Symlinks are reported and not followed outside the install. Walk depth and
entry counts are bounded; an `Interface/AddOns` with 400 addons must inventory
in under two seconds on an SSD.

### 6.3 `toc` — addon manifest parser (M10-06)

Per `docs/LAB_FORMATS.md` §3. Output keeps directive order, unknown
directives, the file list with per-line conditions (`[AllowLoadGameType …]`
and friends) preserved as text, and comments. `## Interface:` parses to a
tuple of ints; a value it cannot parse is kept as text and reported, not
raised. Localized directives (`## Title-deDE:`) are keyed as found.

### 6.4 `luadata` — SavedVariables parser and serializer (M10-04, M10-12) — load-bearing

Grammar and the client's exact output format: `docs/LAB_FORMATS.md` §4.

Parse result is a document: an ordered list of top-level assignments
`name = value`, plus any leading/trailing comment lines. Values are a closed
union: `LuaTable`, `LuaString`, `LuaNumber`, `LuaBool`, `LuaNil`.

- `LuaTable` is an ordered sequence of entries; each entry records its key
  (`positional`, `["string"]`, `[number]`, or bare `name`), the value, and
  the trailing comment the client writes (`-- [1]`). Duplicate keys are kept
  in order and flagged; nothing is merged or dropped.
- `LuaNumber` stores the source text and exposes `as_int()` / `as_float()`.
  Non-finite spellings found in real fixtures are recorded in
  `docs/LAB_FORMATS.md` as they are met; an unknown spelling raises with
  line and column.
- `LuaString` stores the decoded value and the raw source; all escapes in
  §4 of the format reference are decoded; an unknown escape raises.
- Convenience: `document.to_python()` returns plain `dict`/`list`/scalars
  for consumers that do not need fidelity; it is one-way and documented as
  lossy (array-like tables become lists only when keys are exactly `1..n`).

Bounds (constructed hostile inputs, L8): nesting depth 200, file size 256 MB
streamed without loading twice, single string 64 MB. Over a bound raises a
typed error; it does not truncate. The parser is iterative or
depth-guarded; a 10 000-deep table must raise, not crash the interpreter.

Rejected with a positioned error, never evaluated: `function`, any call
`f(...)`, `setmetatable`, `..`, arithmetic, comparison, `and`/`or`/`not`,
bare identifiers as values other than `true`/`false`/`nil`, long-bracket
strings unless a real fixture shows the client writing one, and statements
other than a top-level assignment.

Serializer (M10-12): emits the client's own format exactly (indentation,
key style, trailing commas, `-- [n]` comments, line endings as found in the
source document). Property graded on every real fixture:
`serialize(parse(x)) == x`. For a modified document, untouched subtrees
serialize to their original bytes; new entries use the format rules of the
reference. Output is byte-deterministic: same document, same bytes, on every
platform and locale.

Performance: a 50 MB SavedVariables file (auction or collection addons get
there) parses in under 10 s and under 1.5 GB RSS on the owner's laptop.
Measure it in the PR; if pure Python misses the target, report the numbers
and stop. Do not reach for a C extension or a third-party parser without an
ADR.

### 6.5 `wtfconfig` — Config.wtf, bindings, macros (M10-07)

Per `docs/LAB_FORMATS.md` §5–§7. Read-only in Wave 1, lossless (L4):

- `Config.wtf`, `config-cache.wtf`: ordered `SET name "value"` entries;
  lookups are case-insensitive on name, original case preserved; duplicate
  names kept in order with the last one reported as effective.
- `bindings-cache.wtf`: ordered `bind KEY ACTION` lines plus anything else
  verbatim.
- `macros-cache.txt`: macro records with id, name, icon, body; body keeps
  its own line endings.

Each parser exposes `lines` (everything, in order, typed or `Unknown`) and a
convenience view. No writer in Wave 1; the document model must be sufficient
for one (that is the reason `Unknown` lines are kept).

### 6.6 `gamedata` — DB2 tables by build (M10-08)

Source and rationale: ADR-0022, `docs/DATA_SOURCES.md` (wago.tools).

- `builds()` → products and their builds from the wago.tools builds
  endpoint, cached with a short TTL.
- `table(name, build)` → path to a cached CSV for that table at that build.
  Cache key is `(table, full build string)`; an existing file is never
  overwritten (L5); downloads go to a temp name in the cache directory and
  are renamed into place; a sidecar JSON records URL, fetch time, byte
  count and SHA-256.
- `rows(name, build)` → iterator of `dict[str, str]`. No type coercion in
  Wave 1; column typing arrives with whichever wave needs it (it requires
  WoWDBDefs).
- `resolve_build(flavor)` → the wago.tools build matching an installed
  `Flavor.version`, or a typed `BuildNotPublished` error.
- Polite client: one connection, a descriptive `User-Agent` with the repo
  URL, retry with backoff on 429/5xx, no parallel fetches. The exact query
  parameter for the build (`build=` vs `version=`) is decided by the
  recorded fixture, not by this document.

Tests replay recorded responses (small tables only). A `@pytest.mark.live`
test exists for manual verification and never runs in CI.

Local CASC reading (the install's own `Data/`) is deferred to the wave that
needs models or textures. `gamedata` exposes a `Source` protocol so a CASC
source can be added beside the HTTP one without changing callers.

### 6.7 `process` — is the client running (M10-09)

`running_clients()` → processes whose executable lives under an install root
or whose name matches the known client names (`Wow.exe`, `WowClassic.exe`,
`WowT.exe`, `WowB.exe`, `World of Warcraft`, `World of Warcraft Classic`, and
whatever M10-03 records for Forever), with pid, exe path and the flavor
folder if derivable. `psutil` only; no process memory access, no handles
opened on the client (L7). Access-denied on a process is reported as
"unknown", and `guard` treats unknown as running.

> **Amendment 2026-09-21 (M10-09 security review; wording by the
> security-reviewer, verified against the merged code at 35f2efc).**
> Two sentences above cannot be met as written, and one allowed call turned
> out to cross ADR-0023 on one platform. They now read as follows.
>
> - **Handles.** The module opens no handle itself and makes no call that
>   requests `PROCESS_VM_*`, `PROCESS_QUERY_INFORMATION`,
>   `PROCESS_DUP_HANDLE` or `PROCESS_CREATE_THREAD` on any process. On
>   Windows, `psutil.process_iter()` opens a
>   `PROCESS_QUERY_LIMITED_INFORMATION` handle on every process while
>   enumerating (the right Task Manager uses); "no handles opened on the
>   client" is unachievable with `psutil` there and is withdrawn.
> - **`cmdline` is never called on Windows.** There `Process.cmdline()` opens
>   the target with `PROCESS_QUERY_INFORMATION | PROCESS_VM_READ` and reads
>   its PEB with `ReadProcessMemory` (psutil 7.2.2,
>   `arch/windows/proc_info.c`), which is a process-memory read (L7). On
>   other platforms it is a kernel listing call (`sysctl`, `/proc`) and stays
>   a fallback for a denied or empty `exe()`. A path taken from `argv[0]` may
>   add a match; it never clears a process.
> - **Unknown.** A process is reported as unknown when neither a name nor an
>   OS-reported executable path could be read, when its only name may be a
>   truncated client name, or when inspecting it raised an error `psutil` did
>   not classify. A process with a readable, non-matching name and a denied
>   `exe()` is not reported: on the owner's Mac one of ~810 processes always
>   denies `exe()`, so the literal rule would make `guard` refuse forever.
>   Consequence: a client with an unlisted executable name *and* a denied
>   `exe()` reads as not running, so callers pass the install root they are
>   writing to and the executable names they find in its flavor folders
>   (`extra_names`); discovery does not report executable names (reworded
>   2026-09-22 after the M10-05 review).
> - **Three routes to `cmdline()` on Windows, all closed.** The module does
>   not call it there; psutil's `name()` on Windows does not use it; and
>   psutil's own `Process.exe()` wrapper, which falls back to `cmdline()` when
>   the platform query is denied or empty, is handled by shadowing `cmdline`
>   on the `psutil.Process` instance for the duration of each `exe()` call
>   and restoring the instance exactly afterwards, so the fallback sees an
>   empty command line. The same shadow guarantees on every platform that a
>   path labelled as OS-reported is not an `argv[0]` guess. This depends on
>   psutil reaching that fallback through the instance's public `cmdline`;
>   tests drive a real `psutil.Process` with the platform layer stubbed and
>   recorded, on every CI platform including Windows, and fail if that
>   routing changes. A psutil upgrade that turns those tests red is not
>   merged until this section is revisited.
> - **Injection and threads.** The shadow applies to real `psutil.Process`
>   objects (and subclasses) only; injected objects are never modified.
>   `process_iter` injection is a test seam: production callers, `guard`
>   included, use the default probe and never wrap psutil objects. The module
>   serialises itself with a lock: one inspection runs at a time per
>   interpreter, and a `process_iter` callable must not call back into the
>   module.
> - **`guard` and the probe.** `guard` treats `unknown`, and any exception
>   raised by the probe, as running (an M10-11T grader).

### 6.8 `combatlog` — tokenizer (M10-13)

Per `docs/LAB_FORMATS.md` §8. A streaming tokenizer: timestamp, event name,
and a list of fields with quoted strings, nested brackets and parentheses
handled. No event semantics in Wave 1. `follow(path)` yields new records as
the client appends, surviving truncation and rotation. Unparseable lines are
yielded as `Unparsed` with the raw text.

Whether Forever writes a useful combat log at all is an open question for
M10-03. If the owner's capture shows it does not, this module ships against
retail fixtures only and says so.

### 6.9 `snapshot` — content-addressed store (M10-10)

Store location: `platformdirs.user_data_path("wowlab")/store/`. Never inside
an install (L1), never inside the repository.

- Objects: SHA-256 of file content, stored once, zlib-compressed,
  `objects/ab/cdef…`.
- A snapshot is a manifest: id (UTC timestamp plus short hash), label,
  install root, flavor folder, flavor version at capture, the subtrees
  captured, and a sorted list of `(relative path, sha256, size, mode,
  mtime)`. Manifests are JSON with sorted keys; byte-deterministic for the
  same tree.
- Default subtrees: `WTF/`, `Interface/AddOns/`, `Fonts/`, and loose files
  under `Interface/` outside `AddOns/`. `Cache/`, `Logs/`, `Screenshots/`,
  `Errors/` and everything under `Data/` are excluded by default;
  `Screenshots/` can be opted in.
- `create`, `list`, `show`, `diff(a, b)` (added / removed / changed, with a
  `luadata`-aware structural diff for `.lua` SavedVariables when both sides
  parse), `verify` (re-hash objects), `gc` (unreferenced objects, dry-run
  first).
- Snapshots are immutable. There is no edit; a label change is a new
  manifest field write guarded by id, and nothing else mutates.
- Creating a snapshot while the client runs is allowed and recorded in the
  manifest (`client_running: true`); SavedVariables on disk are then stale
  relative to the session, and `show` says so.

### 6.10 `guard` — the write gate and restore (M10-11) — load-bearing

The only module that writes into an install (L2, ADR-0021).

```python
with guard.transaction(flavor, label="try new keybinds") as tx:
    tx.write(rel_path, data)  # bytes
    tx.delete(rel_path)
    tx.restore(snapshot_id, paths=None)
```

On enter: refuse if `process` reports the client running or unknown; take a
pre-write snapshot of the default subtrees (deduplicated, so cheap); open a
journal entry under the store. On each operation: resolve the path, refuse
anything outside the allowlist (`WTF/`, `Interface/AddOns/`, `Fonts/`, loose
`Interface/` overrides), refuse symlink escapes and case-collision tricks,
refuse `Data/`, executables, `.build.info`, `.flavor.info`, `.product.db`
and anything at the install root; write to a temp file in the same
directory, fsync, atomic replace. On exit: close the journal with the list
of paths touched and their before/after hashes. On exception: roll back
from the pre-write snapshot and record that it did.

`guard.undo()` restores the pre-write snapshot of the most recent
transaction. `guard.history()` lists transactions.

A dry-run mode returns the plan (paths, before/after hashes, bytes) without
touching anything; the CLI prints the plan and asks unless `--yes`.

### 6.11 CLI — `wowlab` (M10-14)

```
wowlab doctor                       # install(s), flavors, builds, client running?, store health
wowlab install show [--json]
wowlab tree [PATH] [--explain]      # inventory; --explain adds the file-map entry per path
wowlab explain PATH                 # what is this file, who writes it, is it safe to edit
wowlab sv list [--account A] [--character C]
wowlab sv dump FILE [--json] [--path 'Var.key[3].name']
wowlab cvar list|get NAME [--scope global|account|character]
wowlab binds list / wowlab macros list
wowlab addons list [--json]
wowlab db2 builds | wowlab db2 fetch TABLE [--build B] | wowlab db2 head TABLE
wowlab log tail [--follow]
wowlab snap create [-m LABEL] | list | show ID | diff A B | verify | gc
wowlab snap restore ID [--paths …] [--dry-run] [--yes]
wowlab undo
```

`--flavor` selects a flavor when more than one is installed; with exactly one
it is implied. Every command that prints data has `--json`. Exit codes:
0 ok, 1 error, 2 usage, 3 refused by guard.

## 7. Repository layout

```
lab/
├── README.md
└── core/                         # uv workspace member "wowlab-core"
    ├── pyproject.toml
    ├── src/wowlab_core/
    │   ├── __init__.py
    │   ├── install.py  layout.py  toc.py
    │   ├── luadata.py            # load-bearing
    │   ├── wtfconfig.py  combatlog.py
    │   ├── gamedata.py  process.py
    │   ├── snapshot.py
    │   ├── guard.py              # load-bearing
    │   ├── filemap.py  filemap.toml
    │   └── cli.py
    └── tests/
        ├── fixtures/             # real captures + README.md index (provenance)
        ├── parser/               # @pytest.mark.parser — runs under make test-parser
        ├── review/               # probes committed by code-reviewer, new files only
        └── …
scripts/lab_capture.py            # M10-02: capture + scrub tool
```

Later waves add siblings of `core/` under `lab/`. Apps depend on
`wowlab-core`; `wowlab-core` depends on none of them.

## 8. Fixtures and scrubbing

`lab/core/tests/fixtures/README.md` is the index and states the rules:
every file has a provenance row, consent is recorded, a committed fixture is
never edited. Every capture passes through `scripts/lab_capture.py`, which

- replaces account folder names, character names and realm names with
  stable pseudonyms (same input → same pseudonym within a capture set, so
  cross-file references still line up),
- drops or blanks CVars known to carry identity (`accountName`,
  `accountList`, `lastCharacterGuid`, `portal` stays) per a deny-list kept in
  the tool and extended as found,
- refuses to emit anything matching an email address, a BattleTag, or a
  `Player-<realm>-<hex>` GUID outside the pseudonym map,
- records what it rewrote in the provenance row (`identity-rewritten`,
  `cvars-dropped: n`).

> **Amendment 2026-09-21 (M10-02 reviews).** The tool blanks rather than
> drops: the line is kept as `SET name ""`, so the row says
> `cvars-blanked: n`, alongside `guids-rewritten: n` and `embedded: n` (the
> number of replacements that had a word character next to them, for the
> owner to read). A blanked line is a scrub artefact, not evidence that the
> client writes empty values. The deny-list grew to eight names
> (`docs/LAB_FORMATS.md` §5) and stays provisional until the M10-03 capture.
> Pseudonyms are ASCII and keep the real name's separators (space, hyphen,
> apostrophe), so the corpus still shows how a realm's folder spelling
> relates to its spelling inside SavedVariables keys; nothing else about the
> name survives, and a uniquely shaped realm name is, to that extent,
> guessable. The corpus therefore holds no non-ASCII folder or character
> name; M10-06 covers those with a constructed test. CVar names on `SET`
> lines, and TOC keys on the client's closed directive list
> (`docs/LAB_FORMATS.md` §3), are never rewritten, because they are the
> client's vocabulary and M10-03 reads **[verify]** answers from them; a
> CVar name that contains one of the owner's names refuses the file instead,
> and any other TOC key is ordinary text. The refusal list is wider than the
> three cases above: also a `BNetAccount-`, `Guild-` or `ClubFinder-` GUID,
> another player's name joined to one of the owner's realms, an identity
> CVar the blanker could not match, any surviving case variant of an
> identity string, and an install whose identity folders or config files
> cannot all be read (nothing is captured then). What the tool cannot know
> (guild names, friends on other realms, real names in free text) it counts
> and points at; the owner's eye and `--extra-name` are the control
> (`docs/handoffs/M10-03.md`).

Structure, key order, number text, escapes, line endings and everything else
stay byte-for-byte. The scrubber works at the byte level with targeted
replacements; it does not parse and re-serialize (the parser does not exist
yet, and a fixture produced by the thing under test proves nothing).

## 9. Testing

- `make test-parser` is the parser suite: `pytest -m parser
  lab/core/tests/parser`. It is its own required CI check ("Parser fixtures
  and round-trip") and the gate for any change to `luadata.py` or another
  format parser. Its day-one test holds the fixture index to the files on
  disk, so the check is never an empty job.
- `luadata.py` and `guard.py` follow the `[TEST]` / `[IMPL]` separation: the
  graders land first as `xfail(strict=True)`.
- `guard` is tested against a synthetic install tree built in `tmp_path`
  (our own format, so constructed input is legitimate) with a fake process
  probe injected. No test writes to a real install. No test needs a real
  install to be present.
- Windows path behaviour (case-insensitive collisions, reserved names, `\\?\`
  long paths, a locked file) is covered with `pytest.mark.skipif` per
  platform and at least one CI run on `windows-latest` for the `lab` suite,
  added in M10-01 as a non-required job.

## 10. Wave 1 tickets

`docs/BACKLOG.md`, milestone `M10`. Dependency shape:

```
M10-01 scaffold ─┬─ M10-02 capture tool ── M10-03 owner capture ─┬─ M10-04T → M10-04 luadata parse ── M10-12T → M10-12 serialize
                 │                                               ├─ M10-05 install ── M10-06 layout + toc
                 │                                               ├─ M10-07 wtfconfig
                 │                                               └─ M10-13 combatlog
                 ├─ M10-08 gamedata
                 ├─ M10-09 process ─┐
                 └─ M10-10 snapshot ┴─ M10-11T → M10-11 guard ── M10-14 CLI ── M10-15 wave review (owner)
```

M10-01 is done: it landed with the repository reset (ADR-0025). M10-03 is the
long pole: it needs ten minutes of the owner's time at the machine with the
game installed. M10-08, M10-09 and M10-10 do not wait on it.

## 11. How waves work (ADR-0024)

1. A wave is one milestone (`M10`, `M11`, …) with its own section in the
   backlog and its own section or document in `docs/`.
2. When a wave's last ticket merges, the conductor writes
   `docs/handoffs/M<n>-review.md`: what shipped, what was learned about the
   client, which `docs/LAB_IDEAS.md` entries are now unblocked, which are
   cheaper or more expensive than estimated, and a recommended next wave of
   at most three ideas with a one-paragraph scope each.
3. The owner picks. The conductor turns the pick into a plan section, ADRs
   if needed (`Proposed`), and tickets, in one docs PR. Nothing from
   `docs/LAB_IDEAS.md` is built before that PR merges.
4. Ideas are never pre-ticketed. An agent that finds itself building
   something from the ideas file without a ticket stops.

## 12. Open questions for the owner

1. Which machine and OS holds the primary install (decides which platform
   the first fixtures come from; the other follows).
2. Whether the Forever beta client is installed yet; if not, Wave 1 proceeds
   on retail fixtures and Forever fixtures are added when it is.
3. Whether the non-required Windows CI job stays (it was added with M10-01)
   or is dropped until `guard` needs it.
4. ~~Accept or reject ADR-0019 through ADR-0025.~~ Answered 2026-09-21: all
   accepted, together with ADR-0013 and ADR-0014.
