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
*Noted 2026-09-22 (M10-03):* the owner's capture and in-game checks
confirmed `_classic_beta_`, `wow_classic_beta`, build `1.60.1.69913` then
`1.60.1.69977`, and interface `16001` (`docs/DATA_SOURCES.md`, Known
state). The rule above still holds: library code reads all of them from
discovery.

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
(1) **Windows default search locations.** For each drive `os.listdrives()` returns, the
search tries `<drive>\World of Warcraft`, then
`<drive>\Program Files (x86)\World of Warcraft`, then
`<drive>\Program Files\World of Warcraft`. The system drive
(`%SystemDrive%`, else `C:`) comes first. Every listed drive is probed,
because telling fixed drives from removable or network ones needs a Win32
call, which is out of scope. (2) **Explicit root and `WOWLAB_WOW_ROOT` are
final.** If either names a directory that is not an install, discovery raises
`NotAnInstallError` and does not fall through to a default. A default
location that is not a directory, or cannot be checked (including a drive
that is not ready), is passed over (wording corrected 2026-09-22 after the
final M10-05 domain review); one that is a directory holding an unusable
`.build.info` (not a regular file, or unreadable) is reported, not skipped. (3) **Frozen and hashable.** Each `.build.info` row is
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
as flavors, because they have no regular `.flavor.info`, their
`.flavor.info` is a symlink, or the folder is a symlink. Symlinks are not
followed, whether the folder or its `.flavor.info`, consistent with M10-06.
(7) **Typed errors.** `NotAnInstallError` carries a `reason` (`missing`,
`not_regular`, `unreadable`); a permission refusal is never reported as
absent. The one error is a root without a readable, regular `.build.info`.
A root that holds a regular `.flavor.info` is called a flavor folder and
gets a pointer to its parent; any other root whose parent holds
`.build.info` is told its parent is an install. A root that does not exist
says so (`reason` stays `missing`). When no
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

*Amended 2026-09-22 (M10-06 reviews):* (1) **Symlinks and junctions are never
followed**, whether they point inside the install or outside it. Each is
reported in `Inventory.symlinks` with its link text and `inside_install`,
and left out of the typed lists. `inside_install` uses `Path.resolve()`,
which reads link text and metadata but never lists or opens anything
outside. (2) **Signatures:** `classify(path, install, *, is_dir=None)`
(flavor folders are the ones discovery reported; the install root and
flavor folder are also matched by their on-disk spelling on a
case-insensitive volume) and `Layout.classify(path, *, is_dir=None)`. Both
return `Classified` or `Unclassified`. (3) **`Layout.inventory()` returns
`Inventory`**, which holds everything the listing methods return plus
symlinks, OS-metadata files, read errors and `truncated`. Bounds are set
with `Limits` (`max_depth`, `max_entries`, `max_toc_bytes`). (4) A TOC over
`max_toc_bytes` is reported with an error and not read. (5) OS-metadata files
(the file map's `os-metadata` row: `.DS_Store`, `._*`, `Thumbs.db`,
`desktop.ini`) met by the walk (the flavor root, everything under `WTF/`,
the area folders and their contents, `Interface/` outside `AddOns/`,
`Interface/AddOns/` itself, and the top level of each addon folder) are
listed in `Inventory.os_metadata`; deeper addon contents are not walked
(item reworded 2026-09-22 after the final M10-06 domain review). They are kept out of the SavedVariables, WTF-file,
override and TOC lists and out of area counts.
(6) The file map's single source is `wowlab_core/filemap.toml`. The doc
tables are generated from it by `scripts/gen_file_map.py`, and a test fails
when the two drift.

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
- `LuaString` stores the decoded value and the raw source; escapes are
  decoded as the 2026-09-22 amendment below says (item 2); an unknown escape
  raises.
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
serialize to their original bytes; new entries follow the document's own
detected style (owner decision 2026-09-22): its indentation unit or none,
its array comments or none, its separators and its line endings, so an
unindented Forever file stays unindented and a tab-indented file with
`-- [n]` comments (§4.2's reference form; no client has yet been seen
writing one, **[verify]**) keeps them. A new file with no document to copy
from uses the style detected, at run time, in the other SavedVariables
files under the same flavor folder (never a per-flavor constant, L6); with
none to read, §4.2's. A property the document does not show (indentation
in a file with no table, the form of a `[number]` key, an empty table's
form) falls back as for a new file.
Output is byte-deterministic: same document, same bytes, on every
platform and locale.

*Amended 2026-09-27 (owner decisions after the M10-12T reviews; replaces
"with none to read, §4.2's" above):*

1. **Seam.** `luadata.serialize(document, *, target=None,
   lab_written=frozenset()) -> bytes` writes nothing (L1, L2). A trivia slot
   (`lead`, `eq_lead`, `key_close_lead`, `sep_lead`, `close_lead`, `tail`)
   or `sep` holding `None` is filled from the detected style; bytes are
   written as given. To append to a parsed table the caller sets its
   `close_lead` to `None`; the old last entry's `Entry.comment` is written
   after its separator with one space, and own-line comments that stood
   before the old `}` are dropped by that edit. Identical nodes may be
   shared (M10-04), so edits are never tracked by object identity.
2. **Data only (owner).** `serialize` raises `LuaDataError` when a given
   trivia slot holds anything but whitespace and `--` line comments (no long
   comments, no NUL), or a `raw`, key or name is not a literal §4.1 accepts.
   Its output always parses as data: L3 holds for writes as well as reads.
3. **Siblings.** Style is detected, at run time, in the client-written
   SavedVariables of the flavor folder that holds `target`:
   `WTF/Account/*/SavedVariables.lua`, `WTF/Account/*/SavedVariables/*.lua`
   and `WTF/Account/*/*/*/SavedVariables/*.lua` (the retail
   `<Realm>/<Character>` and the Forever `<digits>/<First>-<Second>` shapes
   alike), account-wide and per-character pooled, since one client binary
   writes both. Never the target itself, never `*.lua.bak`, never another
   flavor folder, never a renamed copy of `WTF`, never `Interface/`, and no
   per-flavor constant (L6). A sibling that cannot be read, does not parse
   or is over a bound is skipped and never makes `serialize` raise;
   detection may read a bounded prefix of a sibling.
4. **Disagreement (owner).** Where siblings disagree on a property, the most
   recently modified sibling that shows it decides, excluding the paths in
   `lab_written` (the caller passes the files the guard journal records as
   last written by the Lab); ties break on the byte-wise path relative to
   the flavor folder, so the output never depends on directory order.
   Listing takes every account's `SavedVariables.lua` and
   `SavedVariables/*.lua` first, since the account-wide file of every addon
   loaded in a session is rewritten at each logout or `/reload` of any
   character, then the character folders, each folder's entries in
   byte-wise name order; past 16384 entries the rest is not listed, so on a
   very large install an older character file may decide where a newer,
   unlisted one would have, and at most the 64 newest siblings are read. (A
   file of an addon not loaded at logout, or of a character not logged in
   since a patch, keeps an older client's layout **[verify]**.)
5. **Pairing (owner).** Indentation and array comments are one pairing: a
   document or sibling that shows either decides both. Every capture shows
   neither; the remembered retail form shows both; no file has been seen
   with one and not the other **[verify]**. The other properties (separator,
   line ending, leading empty line, `[number]` key form, empty-table form)
   are decided one by one in the same order: document, siblings, fallback.
6. **Fallback (owner).** With nothing to read, the layout every captured
   file shows: no indentation, no `-- [n]`, CRLF, a leading empty line, `,`
   after every entry, an empty table as `{` and `}` on two lines (Forever
   beta 1.60.1, macOS, 105 of 105 files). §4.2's tab-indented, commented
   form is the remembered retail layout **[verify]**: it is followed when a
   document or sibling shows it and graded on constructed documents, never
   chosen by default.
7. **Scope of "exactly".** Style preservation gives the smallest diff until
   the client's next write of that file, which re-emits it in the client's
   own layout, key order and number format; the placement of new entries is
   the Lab's own, not the client's.
8. **Clarifications (conductor, 2026-09-27, from the M10-12T fix round).**
   A tie in item 4 goes to the lowest byte-wise relative path. `lab_written`
   paths and the target are matched as the same file (`st_dev`, `st_ino`),
   whatever their spelling; a path that does not exist is compared after
   `Path.resolve()`. With nothing to read, a
   `[number]` key is written `[n] = ` (§4.1, §4.2). A document whose only
   positional entry shares the `{` line shows array comments but not
   indentation, and by item 5 that decides both (tab indentation and
   comments). A `nil` value inside a table is refused under item 2, because
   the parser refuses it (§4.1: `nil` only as a top-level value) and the
   client never writes one; this corrects an earlier wording of this item.
9. **Data only, precisely (conductor, 2026-09-27, from the M10-12T
   re-review).** Item 2 covers every slot: a `--` comment in any slot but
   the document's `tail` ends with a line break inside that slot; `sep` is
   only `,`, `;` or empty; `Entry.comment` is one line comment with no line
   break; `sep_lead` and `key_close_lead` follow the trivia rule. A slot
   whose text, placed in the output, would comment out or swallow a
   following entry is refused. On refusal, `LuaDataError.line` and
   `.column` give where the refused bytes would start in the output built so
   far, and `.token` holds at most the first 40 bytes of the refused slot.

*Amended 2026-09-28 (M10-18T/M10-18, owner-approved follow-ups from the
M10-12 reviews; conductor rulings on #86):*

10. **Per-folder cap.** During detection, a folder that is listed
    (`WTF/Account` itself, a per-account folder, a `SavedVariables` folder,
    a realm or `<digits>` folder, a character folder) and holds more than
    65,536 entries is skipped whole: at most 65,537 of its entries are read
    from the listing, it is never sorted, and whether it is skipped depends
    only on its entry count, never on listing order. Exactly 65,536 is
    listed. A skipped `WTF/Account` leaves the item 6 fallback.
11. **Scan budget (ruling d).** A skipped folder takes nothing from item 4's
    16,384-entry budget; only entries of folders that are listed count.
12. **Case of a missing path.** Item 8's comparison of a missing target or
    `lab_written` path, after `Path.resolve()`, folds case only where its
    volume is case-insensitive. Case sensitivity is probed read-only: the
    nearest existing name on the resolved path that holds an ASCII letter
    (its parent on the same device) is looked up again in swapped ASCII
    case; the same file (`st_dev`, `st_ino`) means the volume folds case,
    a missing name means it does not. Nothing is written to probe. Where it
    cannot be told, case is folded, which can only drop a style source. So
    on a case-sensitive volume a missing `a.lua` no longer excludes a
    separate sibling `A.lua`.

Performance: a 50 MB SavedVariables file (auction or collection addons get
there) parses in under 10 s and under 1.5 GB RSS on the owner's laptop
(within the `luadata.MAX_COST` budget; see the amendment below).
Measure it in the PR; if pure Python misses the target, report the numbers
and stop. Do not reach for a C extension or a third-party parser without an
ADR.

*Amended 2026-09-22 (M10-04T domain review; conductor decisions within
§4 and L4):* The client's Lua is taken to be Lua 5.1 (community
documentation, warcraft.wiki.gg "Lua"; confirmed for the Forever client on
2026-09-22: `/dump _VERSION` in game printed `"Lua 5.1"`, the client's
version string; the escape set below is still from the 5.1 manual, not
observed). Grammar changes are mirrored in
`docs/LAB_FORMATS.md` §4 amendments.

1. **Strings are bytes.** A Lua 5.1 string is an 8-bit byte string.
   `LuaString.data` is the decoded bytes; `LuaString.value` is `data`
   decoded as UTF-8 with `surrogateescape`, so invalid UTF-8 (reported
   causes: a name cut mid-character, binary an addon stored, **[verify]**)
   parses and rebuilds byte for byte; `LuaString.raw` is the source bytes
   of the literal, quotes included. `\ddd` is
   one byte (0 to 255), so `"\195\169"` is `"é"`. Raw bytes 0x80 and above
   are kept as they are. A `value` holding lone surrogates is not JSON-safe:
   in JSON a string that is valid UTF-8 is carried as text, and one that is
   not is carried as base64 of `data` with a flag saying so; M10-14's output
   models name the fields, and the human output says the same thing.
2. **Escapes are Lua 5.1's.** Accepted, with their Lua 5.1 meanings: `\a`,
   `\b`, `\f`, `\n`, `\r`, `\t`, `\v`, `\\`, `\"`, `\'`, `\ddd` (one to
   three digits, at most 255), and a backslash before a line break (CRLF,
   LFCR, LF or CR counts as one line break, decodes to `"\n"` and advances
   the line count by one). Rejected with a position: `\x`, `\u{…}`, `\z`, a
   `\ddd` above 255, and any other character after a backslash. Which of
   these the client actually writes stays **[verify]** (§4.2 amendment).
3. **Raw control bytes.** Any byte other than CR, LF and NUL inside a
   string literal is accepted and kept (a raw tab, 0x02); a raw CR or LF
   ends the string with an "unterminated string" error; a raw NUL is
   rejected as §4.3 says
   (**[verify]**: the same client writes raw control bytes into other
   files).
4. **`to_python()`.** A top-level `X = nil` is kept as the key with `None`,
   so it differs from a file that never names `X`. An empty table becomes
   `{}`. Every Lua 5.1 number is a double; `int` or `float` in the output
   follows how the number is written, not a client type, and the
   docstrings say so.
5. **Performance.** Until a real file of that size is captured, the M10-04
   PR measures the target on a constructed input and says it is
   constructed.
6. **Positional against bracketed keys.** Lua 5.1 stores pending
   positional entries in batches of 50 (`LFIELDS_PER_FLUSH`): before the
   next field once 50 are pending, and at the closing brace. So in
   `{"x", [1] = "y"}` the client loads `"x"` for key 1, but after 50
   positional entries a following `[1] = "y"` loads `"y"` (**[verify]**,
   from memory of `lparser.c`). The parser flags the later entry in the source
   as the duplicate; `to_python()` takes the value Lua 5.1 would load.
7. **The document alone rebuilds the source.** Every token keeps the exact
   bytes in front of it (whitespace, line breaks, comments) and its
   separator (`,` or `;`), and the document keeps the bytes after the last
   token. Comments therefore have a place wherever they occur: between
   top-level assignments, on their own line inside a table, after a value
   on the same line, after an opening brace. Rebuilding bytes from the
   document, without the source, gives the source exactly. M10-04T grades
   this with a rebuild that uses only the document; M10-12's serializer is
   then this rebuild for unmodified documents, and M10-04 does not need
   reopening for it. A comment the client never writes is still kept (L4). A long-comment
   opener (`--[`, zero or more `=`, `[`) is rejected with a position, as
   long-bracket strings are, unless a fixture shows the client writing one;
   `-- [1]` and `--[1]` are line comments.
8. **Key styles.** Besides `positional`, `["string"]`, `[number]` and bare
   `name`, a `[true]`/`[false]` key (allowed by §4.1) has the style
   `boolean`. Only the later of two equal keys (Lua key equality: `a` and
   `["a"]`, `[1]` and `[1.0]` and the first positional entry) is flagged
   `duplicate`.

*Amended 2026-09-23 (owner decision after the M10-04 reviews: option 1;
rewritten after fix rounds 2 to 4):* two more bounds, each raising
`LuaLimitError` with a position. A number literal longer than 4300
characters is refused, so no conversion is quadratic (the same limit CPython
sets on int parsing). And every document is charged against a parse budget,
`MAX_COST` (a module constant, in bytes): the input buffer, then for each
table entry and each top-level assignment a base cost plus every object
built for it that is not shared (each unshared trivia, lead, comment, key
and value text, counted with its object overhead; a trailing comment twice,
as it is held twice), plus time charges: an entry the fast regex cannot
take, a table, every backslash in a string literal (which bounds the escapes
decoded later), every escaped key, and decoding each distinct escaped key
at parse. An entry or assignment shared whole with an identical earlier one
still costs a fixed amount, for its parse time. A document over the budget
is refused at the entry or assignment that crosses it. The target covers
every document within the budget, whatever its shape. The constants are
calibrated by `lab/core/tests/luadata_bench_constructed.py --at-budget`,
which fills each constructed shape to just under the budget and parses it
in a fresh interpreter. Measured on the owner's M1 (16 GB) on 2026-09-27,
load average 1.8 to 3.7, peak memory as `/usr/bin/time -l` reports it
(which counts compressed memory; `ru_maxrss` can under-report):

| Shape at the budget | Entries | File | Peak memory | Parse |
|---|---|---|---|---|
| Most memory: `[1000000] = 1,` one per CRLF line (distinct number keys) | 2,956,296 | 45.1 MiB | 1,121 MiB | 4.3 s |
| Slowest (±5 %, see below): `  [  "k…"  ]  =  {  }  ,  -- …` (wide trivia, distinct comments) | 1,408,267 | 59.1 MiB | 810 MiB | 6.4 s |
| `["n"]=true;` (one short key, repeated) | 4,771,781 | 50.1 MiB | 764 MiB | 6.0 s |
| `["\1"]=0,` (one escaped key, repeated) | 2,744,626 | 23.6 MiB | 442 MiB | 4.5 s |
| `["\1…"]=0,` (distinct escaped keys) | 1,057,499 | 16.1 MiB | 409 MiB | 4.3 s |
| `["\` line break `"]=0,` (escaped line break key, repeated) | 2,744,626 | 23.6 MiB | 442 MiB | 4.1 s |
| Dense client shape: `0,` one per CRLF line | 6,609,191 | 25.2 MiB | 194 MiB | 5.4 s |
| Dense client shape: `true,` one per CRLF line | 6,497,171 | 43.4 MiB | 210 MiB | 5.1 s |

"Slowest" holds only within about ±5 %, which is the spread machine load
alone produces at the budget. The comment-led `--`-line-break-`0,` list
(3,221,286 entries, 15.4 MiB, 651 MiB) is as slow within that margin: the
M10-04 code review's interleaved rounds measured it at 6.3 s against 6.1 s
for wide trivia. Re-measured on 2026-09-27 on the owner's M1 with
`--at-budget wide-tables` and `--at-budget comment-zeros`, interleaved, three
rounds, load average 1.3 to 2.3: wide trivia 6.04, 6.05 and 6.06 s; the
comment-led list 5.95, 5.96 and 5.99 s.

The bench's other shapes (distinct numbers and strings, string keys, empty
tables, top-level assignments, `0;` and `\v0,` lists, comment-led lists,
§4.2 reference-layout comments with distinct strings, 70-byte distinct
comments) all measured between these rows. *Owner decision 2026-09-24:* the
budget, not the file size, decides what parses. A document over `MAX_COST`
is refused with `LuaLimitError` even when it is smaller than the 50 MB the
performance target names; the target holds for documents within the
budget. Measured refusal points for 50 MiB files in the Forever layout
(`lab/core/tests/luadata_bench_constructed.py --shape ids|zeros`): a flat
positional array of distinct six-digit integers, 5.33 million entries in the
file, is refused at about 3.51 million entries; an array of `0,`, 13.1
million entries, is refused at about 6.46 million. The same holds, sooner,
for the §4.2 reference layout that the retail client writes (tab indentation
and a `-- [n]` comment after each positional entry; each comment is charged
twice, as it is held twice). A 50 MiB array of distinct six-digit integers
in that layout, 2.18 million entries, is refused at about 2.03 million; an
array of `0`, 2.82 million entries, at about 2.62 million. The Forever
client writes no `-- [n]` comments (`docs/LAB_FORMATS.md` §4.2 amendment of
2026-09-22), so this matters only if a retail install's SavedVariables are
read. The auction, collection and `true` shapes at 50 MiB parse in both
layouts. The parse tree uses immutable tuples for speed; Pydantic models
are built at the CLI output boundary (M10-14).

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

*Amended 2026-09-28 (M10-13 review):* an exception to L4 granted by the
owner on 2026-09-28, recorded in AGENTS.md. A line longer than 1 MiB (`combatlog.MAX_LINE_BYTES`; hostile input, since no real line
comes near it) is yielded as `Unparsed` holding only its first 1 MiB, flagged
`truncated`, with the line's full byte `length` and its real ending. That
keeps memory bounded, and the same line gives the same entry however the file
is read or chunked. Every other line keeps its text, ending and offset, so a
log with no such line is rebuilt byte for byte.

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

*Amended 2026-09-28 (M10-19, from the M10-17 security review):*
`SnapshotStore.refuse_holding(root)` refuses, reading only, a store that is
an ancestor of `root` (`StoreLocationError`, the message `create` uses).
That is the one overlap a store outside every install can have, so
`wowlab snap create` runs it before `guard.store_lock(create=True)`. That
case is refused with exit 1 and nothing created, not even `<store>/lock`.
A store inside any install, including one that also holds the captured
install, is not `refuse_holding`'s case: the gate still refuses it, with
exit 3.

*Amended 2026-09-28 (M11-08):* `SnapshotStore.list_tree(root, subtrees, *,
exclude=())` returns every path `create` would walk, sorted, with its kind
(`file`, `symlink`, `other`) by `lstat`. It reads listings and `lstat` only,
opens no file and creates nothing. `profiles` uses it to find the files added
under a profile's subtrees since the save (§13.3). A manifest gains an
optional `purpose` field (`"profile"`, set through `create(...,
purpose=...)` by `profiles.save`); when it is `None` it is left out of the
manifest file, so every manifest without one is byte-for-byte as before.
Like the label, it is not in the fingerprint.

*Amended 2026-09-28 (M11-11, #107):* every read of a store file (objects,
manifests, the manifest already at an id) and every file `create` captures
goes through one helper that opens with `O_NOFOLLOW`, `O_NONBLOCK` and
`O_NOCTTY` where the platform has them, requires `fstat` to show a regular
file, and reads a bounded length; a linked or non-regular store file is
refused (a linked object is never reused), and a FIFO swapped in during a
read can no longer block. Capping how far an object inflates is M11-15.

*Amended 2026-09-28 (M11-15, from the #107 security review):* (a) `gc` lists
`objects/` by `lstat` and follows nothing: a shard or item that is a link
(on Windows also a junction or other name-surrogate reparse point) or not a
regular file is never a candidate and never listed into, and is named in a
new `GcReport.skipped`; a delete goes through descriptors on `objects/` and
the shard opened without following a link and checked against the listing
(on Windows, which has no such descriptors, the item is renamed into the
store's `tmp/`, deleted there only if it has the listing's device and inode
and is not a link, and renamed back otherwise; an empty shard is removed the
same way), so nothing is ever deleted through a link, and an object whose
path changed since the listing is skipped, not deleted. `gc` also refuses
outright, as for a manifest that does not load, when `manifests/` is a link
(or junction) or not a directory, or is missing while `objects/` holds
anything: a dangling link or an unmounted volume would otherwise make every
object look unreferenced. `create` makes `manifests/` before it stores its
first object, so a store it wrote always has one. (b) `read_object(sha256, *, size=None)`
takes the manifest entry's recorded size and inflates at most `size + 1`
bytes, refusing an object that would inflate past it; `read_file`, guard's
restore, undo and rollback, and `snap diff` pass it, and `None` (unbounded)
is left for a caller holding a bare digest. (c) `create`'s reuse check
freshens an object's mtime with `os.utime` on the descriptor it re-hashed
where the platform takes one; on Windows it `utime`s the path only after
`lstat` shows it is still that file and not a link, and a path that no
longer names the checked file is rewritten, never reused.

*Amended 2026-09-28 (M11-18, from the #112 security review):* (a) `create`
never writes through a link. A store whose `objects/`, `manifests/` or `tmp/`
is a link (on Windows also a junction), dangling or not, or is not a
directory, is refused (`SnapshotError`) before the walk, and nothing is made
behind it. Every object and manifest is staged in `tmp/` and moved into its
object shard or `manifests/` through descriptors on those directories, each
opened with `O_DIRECTORY | O_NOFOLLOW` relative to its parent, checked against
its `lstat`, and used for `os.replace(..., src_dir_fd=, dst_dir_fd=)` (POSIX);
on Windows each directory is checked by `lstat` and a link refused, then
checked again just before the rename. A shard that is a link refuses the
`create` when it is reached; the reuse check never reuses an object through
one (the object must be the entry of the checked shard). The store root is
opened as named, so a store below a linked directory still works. `set_label`
writes its manifest the same way. *2026-09-29 (M11-18 fix rounds 1 and 2):*
one `create` holds the store root, `objects/`, `manifests/` and `tmp/` open for
its whole run, at most four descriptors, and re-checks each against its path
by `lstat` through its parent's descriptor on every use (one that no longer
matches is reopened, which refuses a link). Object shards are opened per use
and closed after it: holding all 256 would exhaust macOS's default soft limit
of 256 descriptors. A directory that cannot be opened for any reason other
than being a link or not a directory (`EMFILE`, `EACCES`) is reported as
"cannot open", never as a link to move aside. What can still end up outside the store on
Windows, where there are no directory descriptors, if a directory is swapped
for a link after its last check: an empty two-hex shard directory (made by
the `mkdir` through a swapped `objects/`), a staged temp file (removed when
`create` refuses), or the finished object if the shard is swapped between
the re-check (`still_checked()`) and `os.replace`. Directories renamed away,
rather than replaced by a link, after their descriptor is opened are written
into where they now are; one writer per store is assumed. Reads are not
covered: `read_object` opens an object by path with `O_NOFOLLOW` on its last
component only, so restore, undo, rollback and `snap diff` still read
through a linked `objects/` or shard. (b) On Windows, gc's delete-by-rename checks
`tmp/` only before its rename: a `tmp/` swapped for a link at that last moment
moves gc's own, already unreferenced object into the outside directory, where
it is deleted; nothing else is deleted. (c) Parked `tmp/gc-<uuid>` leftovers
get no cleaner: one exists only after a failed move back (which raises naming
it) or a crash mid-delete, and it may be a file a swapped link made the rename
move from outside the store, so deleting it unasked would be the delete
through a link gc refuses; nothing reads `tmp/`, so it costs only space until
the owner removes it. (d) `verify` names a linked `objects/` as `objects/`
(`OBJECTS_DIR_ENTRY`) instead of `.`, and `snap verify` says nothing under it
was checked and that reads still follow it. (e) `GcReport` gains
`removed_bytes`, the size of the objects actually removed; `snap gc` reports
it, not `unreferenced_bytes`, after a real run.

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

*Amended 2026-09-22 (owner decisions after the M10-11 reviews, worded
after the PR #49 security review; tickets M10-16T and M10-16):*

1. **One writer at a time.** `transaction()` (and so `tx.restore`) and
   `undo()` hold two exclusive, non-blocking OS advisory locks for their
   whole duration: the store lock on `<store>/lock`, and the install lock on
   `<user data dir>/locks/<key>.lock`, never inside the install (L1).
   `<key>` is the SHA-256 hex of `"<st_dev>:<st_ino>"` of the install root
   as guard validated it: the directory's identity, not its spelling, so a
   symlinked, case-variant, Unicode-variant or firmlinked spelling of one
   install maps to one lock. guard takes the user data directory from
   `wowlab_core.snapshot`; it does not import `platformdirs`.
   - **Order.** `transaction()` validates the flavor and runs the
     store-overlap check (lock paths included), then takes the store lock,
     then the install lock, then runs the client check. `undo()` reads the
     most recent record once without a lock only to find the flavor,
     validates it and runs the store-overlap check, then takes the store
     lock (never creating `<store>/`), re-reads the journal and plans only
     from what it reads under the lock, then takes the install lock for the
     flavor the re-read record names (a different install by identity
     raises `GuardError`) and runs the client check. `undo()` on a store
     directory that does not exist raises `GuardError` without creating it.
     The locks are released on exit, including on an exception, and the OS
     releases them when the process ends.
   - **Mechanism.** `fcntl.flock(fd, LOCK_EX | LOCK_NB)` on POSIX and
     `msvcrt.locking(fd, LK_NBLCK, 1)` on one byte at offset 2^30 (1 GiB)
     on Windows, far past the end of the empty lock file, so no ordinary read
     of the file overlaps the mandatory lock (reworded 2026-09-23 after the
     `lab (windows)` run on PR #59: a lock at offset 0 made reads of the store
     fail with a permission error; SQLite uses the same technique; the same
     offset is used to unlock, nothing is ever written through the
     descriptor (so the file stays empty and is never extended), and the
     offset is fixed, since guards locking different offsets would not
     exclude each other); `lockf` and
     `fcntl(F_SETLK)` are not used. A process also refuses, without asking
     the OS, a lock it already holds, so a nested transaction raises
     `GuardBusyError`. `undo()`'s own transaction runs under the locks
     `undo()` already holds; it does not take them again. A held lock raises `GuardBusyError` (a `GuardError`)
     and nothing is written or journaled; any other failure to open or lock
     (unsupported on the volume, permission, I/O) raises `GuardError`, never
     `GuardBusyError`. guard never proceeds unlocked.
   - **Lock files.** Opened with `O_CREAT | O_RDWR | O_NOFOLLOW` (on Windows,
     refused if a reparse point), never truncated, written or deleted; each
     must be a regular file by `fstat` with a link count of 1 (a hard link
     to another file is refused). The lock files and `locks/` are part of
     the store-overlap check. *Added 2026-09-23 (M10-16 security review):*
     the store directory, `<store>/lock`, `<user data dir>/locks/` and the
     install lock are each refused, before anything is created, if they are
     inside any install, not only the one being written. Each is resolved
     (following links and junctions), and it and every existing ancestor are
     examined: a directory holding an entry named `.build.info` or
     `.flavor.info` (of any kind, by `lstat`) is an install, and an error
     other than not-found while examining one counts as an install. On enter, a
     journal that cannot be read in full (an I/O error, or any record
     `history()` would refuse as damaged) raises `GuardError` under the locks
     and before the pre-write snapshot, with nothing written or journaled
     (not in a dry run), since a change `undo()` could not reverse
     must not be written. A path taken from a journal record or a snapshot manifest is
     looked up (`stat`, `lstat`, `samefile`, `resolve`) only when it is a
     local absolute path: absolute, not beginning with `\\` or `//`, and on
     Windows with a drive letter. Anything else is treated as another install
     without being looked up, and `undo()` refuses a most recent record whose
     flavor or install path is not such a path.
   - **Dry run.** A dry run takes the same locks; creating the lock files and
     their directories is the only thing it writes, never inside the install.
   - **Scope.** The exclusion covers processes of one OS user sharing one
     user data directory on one machine. Other OS users, another
     `XDG_DATA_HOME`, or another machine on a network install are not
     excluded. Within that scope, journal sequence numbers are unique per
     store and `undo()`'s "most recent transaction" is well defined.
2. **Leftover temp files are cleaned up.** Before creating a temp file
   inside the install, guard records its flavor-relative path in the
   journal, written and fsynced first, like the before-hash. On enter, after
   the pre-write snapshot and the new journal record are written (not in a
   dry run), `transaction()` and `undo()` consider each temp path named by an
   earlier record whose install root and flavor folder are this flavor's (by
   identity), limited to records of this flavor from the most recent one of
   this flavor whose cleanup finished, that record included (its own temp
   paths and its `temps_left` are considered again). A
   temp path is removed only if its last component fullmatches
   `\.wowlab-[0-9a-f]{32}\.tmp` exactly as the directory lists it, the path
   passes the write rules (allowlist, the walk that follows no link,
   junction or reparse point, the case/Unicode-collision and short-name
   refusals), and the target is a regular file; the directory chain is
   re-checked before and after the unlink as for any delete. Each removal is
   recorded in the new record and fsynced before the unlink; a removal whose
   unlink fails is moved from `temps_removed` to `temps_left` in the same
   record. A path that
   fails any check, or whose removal fails, is left alone and listed; it
   does not stop the transaction. A named temp path is looked up only after
   it passes the write rules, and only by the walk that follows no link,
   junction or reparse point; a path that fails a rule is listed in
   `temps_left` without being looked up. A path that passes them and that
   the walk finds absent (its last component or a parent directory missing)
   is listed in neither `temps_removed` nor `temps_left`; a link, junction,
   reparse point or directory at the name is not absent. The cleanup never
   deletes anything else.
3. **A file changed after the pre-write snapshot is refused** (not in a dry
   run, which has no pre-write snapshot). At the first touch of a path in a
   transaction (write, delete or restore), guard compares the disk with the
   pre-write snapshot's entry for that path (content hash and kind, or
   absent against present). If they differ, the operation raises
   `ChangedSinceSnapshotError` (a `GuardError`) naming the path and telling
   the caller to retry; nothing is written for that path, every later
   operation in the transaction raises `GuardError` without touching the
   disk, and the transaction rolls back on exit and cannot commit, even if
   the caller catches the error. After the temp file is written and fsynced,
   and before each replace or unlink, guard re-reads the target and requires
   the hash it last read or wrote there (for a create: that it is still
   absent), then checks identity, size and mtime by `lstat` as the last step
   before the call; a mismatch raises the same error. Where the platform
   offers a rename that does not replace (`os.rename` on Windows), a create
   uses it, and a create whose rename finds the target present raises
   `ChangedSinceSnapshotError`. The window between the last check and the call is not closed
   and is documented like the module's existing residual window. Rollback
   and undo therefore only ever need bytes the pre-write snapshot holds.
   *Clarified 2026-09-22 from the M10-16T graders:* "the hash it last read
   or wrote there" is guard's own record of the path within this
   transaction (the hash of its last write there that landed, else the hash
   read at its first touch), not a fresh read at the start of the
   operation. So a path this transaction already wrote, then changed by
   something else, then touched again, raises `ChangedSinceSnapshotError`,
   with the same consequences as a refusal at first touch: nothing more is
   written for it, every later operation raises `GuardError` without
   touching the disk, and the transaction cannot commit. On rollback each
   touched path is compared with that record. A path whose disk matches it
   is put back to its pre-transaction content; a path whose disk already
   holds its pre-transaction content needs nothing and counts as rolled
   back; any other path is left as it is, named in the record's note and in
   the error raised at exit, and the record ends `rollback_incomplete`.
   Rollback replaces or removes only content whose hash is guard's own
   record for that path; it never overwrites content that no snapshot holds
   and this transaction did not write. A path whose only operation was
   refused (at first touch or at the re-check) is compared the same way
   with what guard read there: it keeps whatever is on disk, makes the
   record `rollback_incomplete` only if the disk no longer matches that
   read, and is left as it is by an `undo()` of the record. `undo()` of a
   `rollback_incomplete` record restores its other journaled paths from the
   pre-write snapshot as for any record, replacing what rollback left; its
   own pre-write snapshot holds those bytes, so a second `undo()` puts them
   back.
4. **Public surface.** `GuardBusyError` and `ChangedSinceSnapshotError` are
   exported from `wowlab_core.guard`, and so is
   `guard.store_lock(store: Path | None = None)`, a context manager that
   takes the store lock alone (same mechanism, same in-process refusal, the
   same lock-file opening rules: `O_NOFOLLOW`, regular file with one link,
   never truncated, written or deleted; it has no install of its own to
   compare with, but a store directory inside any install is refused as
   above (reworded 2026-09-23 after the M10-16 security review; the
   earlier wording exempted it and would have let it create a lock file
   under `Data/`); `GuardBusyError` if held; a store directory that does not exist raises `GuardError` and nothing
   is created). `HistoryRecord` gains
   `temps_removed`
   and `temps_left` (flavor-relative paths). The journal format becomes 2;
   format-1 records read as naming no temp paths and as having run no
   cleanup.

*Amended 2026-09-28 (M10-17T/M10-17, from the M10-14 security and code
reviews):* `guard.store_lock(store=None, create: bool = False)`. With
`create=True`, the inside-any-install check runs first, on the store path
and every ancestor, through links and junctions (an ancestor that cannot be
examined counts as an install); only then are the store and any missing
parents created, and the lock taken as above. Nothing is created anywhere
when it refuses. `create=False` keeps the behaviour above: a missing store
raises `GuardError` and nothing is created. `guard.undo(*, store=None,
expected_id: str | None = None)`: with `expected_id`, the journal's last
record id is compared under the store lock, and a mismatch (another
transaction committed after the caller read the journal, or an id that names
no record) raises `GuardError` naming an id, with nothing in the store or the
install changed. Without it, `undo` behaves as before. The CLI passes both
(M10-17): the first `snap create` holds the store lock like every later one,
and `undo` passes the id of the record it showed the owner.
Out of scope (conductor, 2026-09-28, from the M10-17T security review): a
process running as the owner that swaps a link or plants a marker inside the
owner's user data directory between the check and the mkdir. It already has
the owner's rights, and some window always remains after the last check. The
check-before-create order stops every mistake short of that; an
implementation may re-check after creating, or create relative to directory
handles, as defence in depth.

### 6.11 CLI — `wowlab` (M10-14)

```
wowlab doctor                       # install(s), flavors, builds, client running?, store health
wowlab install show [--json]
wowlab tree [PATH] [--explain]      # inventory; --explain adds the file-map entry per path
wowlab explain PATH                 # what is this file, who writes it, is it safe to edit
wowlab sv list [--account A] [--character C]
wowlab sv dump FILE [--json] [--path 'Var.key[3].name']   # path grammar: §13.4, M11-25 note
wowlab cvar list|get NAME [--scope global|account|character]
wowlab binds list / wowlab macros list
wowlab addons list [--json]
wowlab db2 builds | wowlab db2 fetch TABLE [--build B] | wowlab db2 head TABLE
wowlab log tail [--follow]
wowlab snap create [-m LABEL] | list | show ID | diff A B | verify | gc
wowlab snap restore ID [--paths …] [--dry-run] [--yes]
wowlab undo
wowlab profile save|apply|list|show|delete …         # §13.3 (M11-08)
wowlab addon install|remove lab [--dry-run] [--yes]  # §13.1 (M11-02)
wowlab looks races|options|save|show|compare|page …  # §13.2 (M11-06, M11-07)
wowlab char show [--character C] [--json]           # §13.1 (M11-04)
```

*Amended 2026-09-28:* the Wave 2 commands above are listed for
completeness; their behaviour is specified in §13.

`--flavor` selects a flavor when more than one is installed; with exactly one
it is implied. Every command that prints data has `--json`. Exit codes:
0 ok, 1 error, 2 usage, 3 refused by guard.

*Amended 2026-09-27 (owner decision after the PR #68 reviews):* a
whole-snapshot `wowlab snap restore` (no `--paths`) skips every file whose
file-map row has Edit `no`: files the client writes (`*.bak`, `.old`),
`Blizzard_*` folders (most likely copied in by the owner **[verify]**) and
file-browser metadata. That covers `*.lua.bak`, `SavedVariables.lua.bak`,
`edit-mode-cache-account.old`, `Interface/AddOns/Blizzard_*`, `.DS_Store`,
`._*`, … and any other `no` row. The plan says how many were skipped and
why. Such a file is restored only when it is named with `--paths`.
`wowlab explain` words that Edit cell, for a path inside the gate's
subtrees, as "no; wowlab leaves it alone (a restore writes it only if you
name it with --paths)". `snap restore` and `undo` also take `--json` (the
plan, and with `--yes` the result).

*Amended 2026-09-29 (M11-30):* text output is terminal-safe. No C0
control but tab, no DEL, no C1 control and no Unicode format or separator
character (Cf, Zl, Zp, the table §13.4's printed paths use) reaches stdout
or stderr raw: C0, DEL and C1 print as `\xNN` of the code point, format and
separator characters as `\xNN` of each UTF-8 byte (U+202E is
`\xe2\x80\xae`), and bytes that are not UTF-8 as `\xNN`. Letters, accented
or not, print as they are. A message on stderr keeps only its own line
breaks: a value inserted into it (a path, a folder name) has its line
breaks escaped, and an error is always one line. `--json` output is
unaffected (JSON's own escapes, ASCII only).

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

> **Amendment 2026-09-23 (M10-02 follow-up 4).** A combat log that names
> other players is refused by default, as above. With the opt-in flag
> `--pseudonymise-other-players` (combat logs only; the owner chose it for a
> log that cannot be recorded again), each other player's GUID becomes an
> invented one (`Player-9998-<n>`, numbered by first appearance, never
> derived from the real value) and the character-name part of their quoted
> unit name an invented pseudonym (`Labother…`), the same for the whole run.
> A later part of that unit name that is one of the owner's realms is
> scrubbed by the owner's rules; an unknown realm gets a realm-style
> pseudonym; a region word stays. The log is still refused, with a count
> only, if one of those real name parts appears anywhere else in the kept
> lines (in any normal form or casing, and an unknown realm also in spaced or
> split spellings), if a part cannot be searched for safely (a format or
> vocabulary word such as `nil`, `True`, `Default`), or if a GUID of theirs
> never stands next to its unit name. The provenance row records the number
> of other players rewritten as `other-players-pseudonymised: N`.

> **Amendment 2026-09-24 (M10-02 follow-up 5, with its review rounds 1 and 2 and
> the owner's decision of the same day).** Four changes to combat logs,
> applied with or without `--pseudonymise-other-players`.
>
> *Unit GUIDs.* Each non-player unit GUID
> `<Type>-0-<serverID>-<instanceID>-<zoneUID>-<ID>-<spawnUID>` (Creature,
> Pet, Vehicle, GameObject and any other type of that shape) keeps its type,
> the leading `0` and the NPC or object `<ID>`, which are game data. The
> server id, instance id and zone UID share one decimal sequence (1, 2, 3, …,
> in order of first appearance, keyed by field and value); of these three, a
> field that is all zeros stays as written. The spawn UID always becomes
> invented upper-case hex of the same width, numbered from 0 in order of
> first appearance (a real zero spawn UID is renumbered too). The mapping is
> one registry per run, so a real value maps to the same invented one in
> every log of the run, and two different real GUIDs never become the same
> one. `0000000000000000` and `nil` stay. The log is refused, with a count
> only, for any other token shaped like a GUID (letters, `-`, digits, `-`, …)
> that is not a Player, BNetAccount, Guild or ClubFinder GUID: a `Cast-`
> GUID, a field short or long, a lower-case spawn UID, a type glued to the
> byte before it. It is also refused for a GUID body with no type or with
> lookalike dashes (five or more dash-separated digit groups ending in six or
> more hex digits, dashes including U+2010 to U+2015, U+2212, U+FE63 and
> U+FF0D). The row records `unit-guids-rewritten: N`, and the per-file
> summary line gives the same count.
>
> *Timestamps.* Every record's timestamp, and the `MMDDYY_HHMMSS` stamp in a
> `WoWCombatLog-MMDDYY_HHMMSS.txt` file name, moves by an offset drawn for
> that log alone: a whole number of seconds from `secrets`, between 60 and
> 400 days either way, never printed, logged or recorded. No two logs share
> an offset by design. So if a log's unshifted original was ever published,
> comparing it with the shifted copy reveals that one log's offset and no
> other's. The shifted timestamp has one fixed shape,
> `M/D/YYYY HH:MM:SS<fraction><suffix>`: the month and day are unpadded, the
> year has four digits, the hour always has two, and the fraction and the
> `-4`-style UTC-offset suffix are copied as written. Verified on 2026-09-24
> against the owner's real logs (count-only check): the hour is always two
> digits, the month is unpadded, the year has four digits; day padding is
> unverified (no real log had a day below 10). The log is refused, with a
> count only, for:
>
> - a source line in any other shape (a zero-padded day, which the owner
>   reports; a padded month; a one-digit hour; a missing or two-digit year);
> - a UTC-offset suffix that changes partway through the log (a
>   daylight-saving change would give the offset away);
> - a line that does not start with a recognised timestamp;
> - a timestamp that is no valid date;
> - a date or time inside a record (`9/21`, `21:15`, `2026-09-21`; no record
>   type is known to carry one).
>
> The output path and the provenance row carry the shifted name, and the row
> records `timestamps-shifted`. `--combat-log` still takes the real name, and
> its messages name the option by position only ("the 2nd --combat-log"),
> never by the name, which encodes the real date.
>
> *What the shift hides, and what it does not.* It hides the time of day and
> the exact date of the session. The date stays bounded. It falls inside the
> live window of the build, which is in the row, in the log's header and in
> the committed wago.tools listing, and it falls before the date of the
> commit. The kept suffix shows whether the session was in daylight-saving
> time. Health, position, damage, healing and gear values stay real, and they
> are the same in any other group member's log of the same fight. Someone who
> already holds such a log can still match it to ours by those values, so a
> group fight stays linkable to that extent. A solo log has nothing to match
> against.
>
> *What is captured.* Only `WoWCombatLog.txt` and
> `WoWCombatLog-MMDDYY_HHMMSS.txt` (exact case) are picked, by default the
> newest non-empty one. `--combat-log NAME` (repeatable) captures only the
> named files, each directly under a flavor's `Logs/`. The newest is then
> not captured unless it is named too. A name of any other shape, or one that
> is not the combat-log kind, stops the run before anything is written, and
> the message names it by position, not by name. A
> log that starts with a gzip, zip, bzip2, xz or zstd signature, holds a NUL
> byte or is not valid UTF-8 is refused unscrubbed. Each captured log gets
> its own row and its own checks.
>
> *Folded spellings.* The owner's own identity strings, other players' names
> (`--pseudonymise-other-players`) and loose second names are also hunted in
> a folded copy of the text. The fold is NFKD, then format characters (Cf)
> and non-spacing marks (Mn) are dropped, then the text is casefolded. So
> full-width letters, added or dropped accents, zero-width spaces and soft
> hyphens are all caught. Between letters, the hunt reads past any run of
> blanks (`\s`: tab, NBSP, doubled spaces), apostrophes and U+2018, U+2019,
> U+02BC, hyphens and U+2010 to U+2015, underscores and `\'`. For the
> owner's names, a hit refuses the file with
> `surviving identity string (folded or separated spelling) xN`. This check
> only detects and refuses. The rewrite itself is unchanged: byte-exact, on
> the spellings listed above.
>
> Why: the unit GUID's location fields were the last real server numbers in
> a captured log. A spawn UID or a real timestamp can tie the log to another
> player's published log of the same fight, and that log names the owner.

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

## 13. Wave 2 (M11): lab-addon, customization-sandbox, profiles, sv-merge

Owner pick, 2026-09-28 (ADR-0024). Decisions: ADR-0026 (the addon is Lua
for the client only) and ADR-0027 (generated pages are self-contained static
HTML), both accepted by the owner on 2026-09-28. Owner choices of the same date: the addon is installed
by `wowlab` through `guard`; it records everything the idea lists; the
sandbox gets a local page as well as the CLI. L1–L8 apply to every ticket.

### 13.1 lab-addon

- **Addon** `lab/addon/WowLab/`: a TOC template and Lua sources. It gathers
  each section during the session (at entering the world and on that
  section's change events) and writes the tables at `PLAYER_LOGOUT`
  (`/wowlab save` refreshes the tables in memory; the file on disk changes
  only at the next `/reload`, logout or clean exit, and a crash writes
  nothing), as a versioned table (`schema = 1`):
  - equipped gear: item links as strings, so bonus IDs and enchants survive,
    with any player GUID in a link (the crafter field of a crafted item)
    blanked before storing and the slot flagged `crafter_removed`
    (ADR-0026; whether Forever fills the field is **[verify]**); each slot's current item level and the equipped average as the client
    reports them; slots come from the client's own first/last
    equipped-slot constants, not a fixed list (a ranged slot on Forever is
    **[verify]**);
  - talents, as two sections, each a `C_Traits` config dump (config, tree,
    node and entry ids and ranks) and each "absent with reason" when its API
    is missing:
    - `talents.class`: the class talents active at logout
      (`C_ClassTalents.GetActiveConfigID()` then `C_Traits` **[verify]**),
      the id of the last selected saved loadout if there is one, and the
      loadout export string; no loadout names;
    - `talents.legacy`: the Legacy trees, also on `C_Traits` (panel
      `ToggleLegacySystemUI`, unlocked at level 25, with a seasonal point
      cap), and the Legacy points spent and the cap if the client gives them
      **[verify]**; empty below level 25.

    Class talents and the Legacy trees both running on `C_Traits`, the
    Legacy panel and its level-25 unlock and point cap, the absence of
    `GetSpecialization` and related calls, and new spec IDs (paladin =
    1486) were measured on the live Forever client, build 1.60.1.69893, by
    https://github.com/Thunderz96/forever-addon-kit (README "Findings");
    each is re-verified in M11-03;
  - spec: whatever spec identifier Forever exposes, found by testing for the
    API function; `GetSpecialization` is never called unguarded. The field is
    optional in the schema-1 model;
  - customization choices (option id to choice id, mapped from the barber
    shop's choice index to the choice's `id`) for the model the barber shop
    was showing (`chr_model_id`), recorded only at `BARBER_SHOP_OPEN` and
    after an applied change (`BARBER_SHOP_APPEARANCE_APPLIED` **[verify]**),
    never at close after a cancelled preview. Appearance changes only in the
    barber shop or through a paid service at character select, so the last
    record is kept from session to session and describes the character
    until the next visit. It carries `recorded_load`, the `probe.loads`
    value of the session that recorded it; the reader shows it as "as of
    the last barber-shop visit with the addon enabled, N logins or reloads
    ago". The addon drops the record, with a reason, when the race it holds
    no longer matches the character. A paid appearance change that keeps
    the race is invisible to the addon, and the reader says so
    (2026-09-28, conductor ruling on the M11-01 reviews);
  - collections: mounts, toys and pets (by species id and count; no
    battle-pet ids, they are GUIDs), and appearances as collected source
    (item-modified-appearance) ids. Lists read through a filtered journal
    say so, and the addon never changes the owner's filters or collapsed
    headers. Amended 2026-09-28 (M11-20): appearances are not recorded on
    any client. On the first M11-03 login (Forever, build 1.60.1.70009),
    one call, `C_TransmogCollection.GetCategoryAppearances(9)` (9 is Waist
    on Retail), made about 4 s after entering the world, hit the client
    assertion `BC_ASSERT(this->m_has_value)` and crashed the client;
    `pcall` cannot catch a C++ assertion. That is one observation. Whether
    other categories were called before it is not known (the loop walked
    `Enum.TransmogCollectionType` with `pairs`), and the cause is not
    established: the wardrobe data may not have been loaded 4 s after
    login, the data for that slot may be missing, or transmog may be only
    partly built on Forever. The crash was seen on Forever, but the addon
    does not branch on flavor (L6): it calls nothing in
    `C_TransmogCollection` on any client, registers no transmog events,
    and always writes `collections.appearances` as absent with a reason.
    Any later appearance gathering, including the same call at another
    time, needs its own ticket and a live check first;
  - currencies: id and quantity, and the total cap, weekly cap, weekly
    earned and account-wide flag raw where the client gives them
    **[verify]**; weekly values follow the region's reset (GLOSSARY);
  - professions: skill-line id, rank, maximum and modifier as the client
    gives them (Forever's profession model is **[verify]**).

  Each capture records the client's version and build from
  `GetBuildInfo()` and the active spec, since spec and loadout describe a
  moment (GLOSSARY). It records no wall-clock time; the reader uses the
  file's modification time (if a time is ever added, the scrub tool must
  shift it like combat-log times). `WowLabCharDB` also holds
  `probe = { loads = <n> }`: at `ADDON_LOADED` the addon reads the value the
  file held, adds 1 and keeps it; if `WowLabCharDB` loaded as a table
  without a probe, it sets `probe.lost = true` for that load only (a later
  load that finds the probe clears it). The addon cannot tell a first load
  from a whole file that failed to load: both show `loads = 1`, and §13.4's
  two-snapshot check catches the second (amended 2026-09-28, M11-01
  review). The probe, the last customization record and the skip list
  (below) are the only state the addon carries from one session to the
  next; all three are lost if the SavedVariables loader fails (§13.4).
  Amended 2026-09-28 (M11-21): the owner can switch one section off.
  `/wowlab skip <section>` takes it off at once (its event handler removed,
  a pending gather dropped, what it gathered this session forgotten);
  `/wowlab unskip <section>` puts it back from the next `/reload` or login,
  never in the same session; `/wowlab skip` with no section lists the
  switched-off sections; a key that is not a section is refused with a
  chat line listing the valid keys. Section keys: `gear`, `spec`,
  `talents.class`, `talents.legacy`, `customization`,
  `collections.mounts`, `collections.toys`, `collections.pets`,
  `collections.appearances`, `currencies`, `professions`. The list is
  saved in `WowLabCharDB`, per character, as `skip = { "<section key>", ... }` (strings in
  section order, left out when empty; the reader keeps it as a list of
  strings and ignores a key it does not know) and read at `ADDON_LOADED`,
  before any section is carried, registers an event or is gathered. A
  section in it registers no event, is never carried or gathered (not on
  its events, not at entering the world, not by `/wowlab save`, not at
  `PLAYER_LOGOUT`) and is written as
  `{ absent = "switched off by the owner" }` (`collections.appearances`,
  which has no gather, keeps its own reason). The switch exists because a
  client assertion inside a section crashes the client at every login
  while the addon is enabled, and a crash writes nothing, so the addon
  cannot mark the culprit itself (M11-20); a switch typed before a crash is
  lost too, so it is saved by a `/reload` or logout. It stops only that
  section's own gather: another section may still call the same client API
  (`talents.class` asks for the spec). This is a schema-1 edit made before
  M11-04 starts. Amended 2026-09-29 (M11-21 review): the first on-world
  pass after each `ADDON_LOADED` (login or `/reload`) runs 15 s after
  `PLAYER_ENTERING_WORLD` on its own timer, announced in chat at that
  moment, so the owner can switch a section off before it runs; until then
  change events gather nothing, and after it they keep the 2 s debounce. A
  logout inside the 15 s still records (`PLAYER_LOGOUT` gathers what is
  waiting). Without `C_Timer` the pass runs at once and there is no window.
  The probe exists for
  §13.4's loader check (the sv-health idea in `docs/LAB_IDEAS.md`, cut down
  to what sv-merge needs; the full `doctor` check stays an idea). All
  sections go in
  `SavedVariablesPerCharacter` (`WowLabCharDB`) until the M11-03 capture
  shows which collections are account-wide on Forever; `WowLabDB` then holds
  only data that reads the same from every character, and the reader says
  it reflects whichever character logged out last. Whether a section is
  available is decided by testing for the API function itself, never by
  `WOW_PROJECT_ID`, the interface number or the flavor; a section the
  client cannot provide is written as absent with a reason, never guessed.
  No names, realms, GUIDs, guild or chat of anyone (ADR-0026). No text the
  owner typed (loadout, equipment-set or battle-pet names). No Battle.net
  identity (`BNGetInfo`, `C_BattleNet`). The name fields that
  `C_BarberShop.GetCurrentCharacterData()` returns are never stored
  **[verify]**.

  Amended 2026-09-29 (M11-22, from the M11-03 domain review): three places
  where a capture could not tell "the client returned nothing" from "the
  call failed" or "never ran". (1) A section that registered its events
  this session carries `events_unregistered` on whatever record is written
  for it (a gathered record, a carried customization record, and the
  never-gathered `{ absent = "<not_gathered reason>" }`): the change events
  the client refused, or an empty list when it accepted all of them. An
  empty list is the answer; a record with no key at all was written before
  M11-22 (the M11-03 fixtures, or any file from before the owner
  reinstalled) and says nothing about the client's events. On a carried
  customization record the list is this session's, not the recorded
  visit's: `carry` drops the saved list. A section switched off (at load,
  or by `/wowlab skip` this session) is written with its plain "switched
  off by the owner" reason and nothing else; `collections.appearances` has
  no events, so its reason gets nothing added. (2) `currencies` records
  `rows`, the count `GetCurrencyListSize` gave; it counts header rows and
  leaves out rows under a collapsed header (community documentation,
  **[verify]**). `headers` counts every header row seen, so
  `rows − headers − #list` is the rows the addon could not read an id
  from. `rows` is missing when the call raised an error or returned no
  number, or when the file predates M11-22 (the M11-03 fixtures). (3)
  `talents.class` writes exactly one of `export` and `export_absent` (the
  function missing, raised an error, returned an empty string, or returned
  no string), and exactly one of `last_selected_config` and
  `last_selected_config_absent`: `"the client returned no last-selected
  loadout for this spec"` when the call returned nil (nil is taken, from
  Retail behaviour, to mean no saved loadout is selected for this spec
  **[verify]**), with separate reasons when the function is missing, there
  is no spec id to ask with, the call raised an error, or it returned
  something other than a number. Both calls go through `pcall` directly,
  not `ns.Call`, so an error is told apart from nil. The keys are
  additive: the schema stays 1, and the reader treats each as optional
  (the M11-03 fixtures predate them).

  Schema 1 may change after M11-03. Edits from the capture land before
  M11-04 starts; after M11-04 merges, any change is schema 2.
- **Install** `wowlab addon install lab` / `wowlab addon remove lab`: copies
  the addon into `Interface/AddOns/WowLab/` through a `guard` transaction
  (client closed, snapshot first, undoable), filling the TOC's
  `## Interface:` from the discovered version by the patch-number rule
  (GLOSSARY, "Interface version"; L6). A later patch makes the installed TOC
  out of date and the client may stop loading it **[verify]**, so `wowlab
  doctor` reports the mismatch and re-running install fixes it. The addon
  ships one unsuffixed `WowLab.toc`, because Forever's preferred suffix is
  **[verify]** (`LAB_FORMATS.md` §3). `remove` deletes the code only;
  `WowLab.lua` SavedVariables stay (they are the captures), and the client
  does not delete them either **[verify]**.
- **Reader** `wowlab_core.labaddon`: reads `WowLab.lua` through `luadata`
  into Pydantic models, one per section, keyed by schema version; unknown
  keys are kept, not dropped (L4 spirit). CLI `wowlab char show [--json]`
  over the latest capture.
  Amended 2026-09-29 (M11-04), reader behaviour the bullets above left
  open: "the latest capture" is the character `WowLab.lua` with the newest
  modification time in the account, unless `--character` names one (the
  whole folder name, or `<realm folder>/<folder>`, as every other command).
  An unknown key is kept only when its name is a plain name
  (`[A-Za-z_][A-Za-z0-9_]*`, at most 64 characters) and its value holds no
  text anywhere (numbers, booleans, tables of them); otherwise the file is
  refused, and the message names the key path but never echoes a value
  (#97: text passes only in the fields the addon writes as text). A file
  that holds both a value and its `_absent` reason (`export`,
  `last_selected_config`) is refused. A section key missing from the file
  reads "not in the file": the addon's logout write did not run in a
  session that started with no WowLabCharDB (it creates only schema, probe
  and skip at ADDON_LOADED). It is neither nil inside a section nor absent
  with a reason. When an earlier table existed, the same failure instead
  leaves that earlier session's sections under the newer probe count, which
  one file cannot reveal. Also pinned (M11-04 reviews): only the key names
  the addon writes are fields (`schema_`, `list_`, `class_` in a file are
  unknown keys); every integer is bounded to ±2^53 (a Lua double's exact
  range), inside unknown keys too; absent reasons are printable ASCII; an
  empty `export` (written before M11-22) reads as "the client returned an
  empty string", never refused; a Legacy trait currency listed under several
  trees of one config counts once when the trees agree, and is not added up
  when they differ.
  *Amended 2026-09-29 (M11-27):* the file does not say which Legacy
  candidate is the Legacy system, so two candidates are never added
  together. When `talents.legacy.configs` holds exactly one config and it
  has figures, the headline is unchanged ("present, nothing spent, N points
  available (level L)"). Otherwise, when any config has figures, the
  headline names the count and the level once, then gives each config its
  own figures ("config 7: absent with a reason (see below)" for one without
  figures). The line under it then says the addon lists every trait config
  it did not rule out and that which one is the Legacy system is not
  recorded. `collections.appearances` registers no events, so its line
  says nothing about events. Only when the file holds `events_unregistered`
  there, a note says so (the list stays in `--json`). The three reason
  fields (`absent` at every level, `export_absent`,
  `last_selected_config_absent`) are clipped instead of refused. A reason
  longer than 1024 characters, or holding any byte outside printable ASCII
  (a long Lua error the addon stored), is taken as the bytes the file holds.
  If any byte is outside 0x20 to 0x7E, each such byte is shown as `\xHH`
  and each backslash as `\\`. The text is cut to at most 1024 characters,
  never inside an escape. Lengths are counted in bytes. Beside the field,
  the model and `--json` carry `<field>_clipped` =
  `{original_length, escaped, truncated}`, left out when nothing was done.
  A `_clipped` key inside a `WowLab.lua` is refused: only the reader writes
  one. An empty reason is still refused, and every other string limit is
  unchanged. Printable ASCII is still the only text that reaches the
  terminal or the JSON unescaped, and the file itself is never changed.
- **Capture** (owner): install; on each character log in, then log out or
  `/reload` (a crash writes nothing); on at least one character open the
  barber shop and close it without changing anything, then log out. Capture
  `WowLab.lua` in the account's `SavedVariables/` (holds `WowLabDB`) and in
  each character's `SavedVariables/` (holds `WowLabCharDB`) with
  `scripts/lab_capture.py`, plus, for sv-merge, a
  `## SavedVariablesPerCharacter` file from two characters that holds no
  other player's names (or `WowLab.lua` itself).

### 13.2 customization-sandbox (data-only)

- **Data** from `gamedata` for the flavor's full version string (ADR-0022):
  `ChrRaces`, `ChrModel`, `ChrRaceXChrModel` (race and body type to model;
  its `Sex` column is the body type, and one model can serve both),
  `ChrCustomizationOption`, `ChrCustomizationChoice`, `ChrCustomizationReq`,
  `ChrCustomizationReqChoice` (choice depends on choice),
  `ChrCustomizationElement` (how a choice is drawn),
  `ChrCustomizationCategory` (UI grouping; some druid-form categories carry
  a `SpellShapeshiftFormID`, but form options are not found by category: on
  1.60.1.70009 the Bear Form option sits in "Face", and the Bear Form,
  Aquatic Form and Moonkin Form categories hold no option), `ChrClasses`
  (which class ids the build has), and
  `ChrCustomizationConversion`, which maps the old appearance bytes (race,
  sex, legacy slot 1–5 for skin, face, hair style, hair colour and features,
  and the byte value, optionally keyed on the legacy skin byte) to a choice
  id. It is a conversion for characters stored in the old byte format, not
  alternate forms **[verify: when the client or server applies it]**.
  Alternate forms (the Worgen human form, the Dracthyr visage) are linked by
  `ChrRaces.UnalteredVisualCustomizationRaceID` and take their options from
  another race's model. Some options sit on models that no
  `ChrRaceXChrModel` row names. Those with `ChrModel.Sex` 3 are druid forms,
  warlock demons, a pet and dragonriding bodies, gated only by class and
  race masks **[verify]**. The others (Sex 0 or 1; on 70009 models 257–278,
  texture layout 203, most with the original races' display ids) look like
  the original pre-HD character models; what links them to a race is not in
  the recorded tables **[verify]**. The set is **[verify]** against the version's wago listing.
  Recorded as fixtures (ADR-0012). wago may not publish every Forever build
  (69977 was missing, breakage log); `BuildNotPublished` is reported, never
  worked around.
- **Model** `wowlab_core.looks`: per race and body type, the options, their
  choices and the requirements that gate them; a look is a named mapping of
  option to choice. A look is refused only for what the data decides: wrong
  race or body type for the option, a choice mapped to an option it does not
  belong to, a class mask that excludes the class, or a choice it depends on
  that the look sets to something else (a dependency on an option the look
  leaves unset is shown as undecided, since the client always holds some
  choice there; a dependency on an option's own requirement only decides
  whether the barber shop shows that option, and is shown as a condition
  **[verify]**). An unlock requirement (achievement,
  quest, item) is shown as "needs <unlock>", never refused. An imported look
  with a choice id the recorded build does not have is shown as "unknown to
  build <version> (possibly a hotfix)", not refused.
- **CLI** `wowlab looks races | options <race> | save <name> … | show | compare
  <a> <b> | import-char` (the character's choices as of its last recorded
  barber-shop visit, which is stale if it changed since then). Looks are JSON under the user data directory.
- **Page** `wowlab looks page [--out PATH]`: one self-contained HTML file
  (ADR-0027) to browse races and options and view saved looks.

*Amended 2026-09-28 (M11-06):* the looks CLI as built. `import-char` is
M11-04's. A look is `<user data>/wowlab/looks/<name>.json`, a `SavedLook`
(`wowlab_core.lookstore`): format 1, the build it was checked against when it
was saved, and the `Look`. Names follow the profile rule (a letter or digit,
then letters, digits, `.`, `_`, `-`; 64 at most) and are compared without
case. `save` refuses a taken name unless `--replace`, and refuses the folder
when it or a folder above it holds `.build.info` or `.flavor.info`. The build
is `--build`, else the flavor's version from discovery; only when no install
is found at all (no `--root`, no `$WOWLAB_WOW_ROOT`), it is the build the
looks were saved against, or else the one build whose customization tables
are all cached, and the output says which. `races` lists
`playable_races()`. `options <race> [--sex 0|1] [--class C]` lists
`options_for` per body type, each choice with the model's check of a look
that holds only that choice; findings every choice of an option shares are
shown once on the option. `save <name> --race R --sex S [--class C]
[--choice OPTION=CHOICE]…` runs the check: a look the tables refuse is not
written (exit 1), and notes ("needs <unlock>", "unknown to build <version>
(possibly a hotfix)", undecided dependencies) are shown and do not stop it.
A look without `--class` is not refused for a class-restricted choice,
including one whose ClassMask allows no class the build has; that choice is
noted as "no class in build <version> can use this", and any `--class` would
refuse it.
`show [NAME]` checks one look, or lists every look with its verdict and exits
1 after naming a damaged file. `compare <a> <b>` checks both against one
build and lists the options set to the same choice and those that differ.
Every data command takes `--json`.

*Amended 2026-09-28 (M11-07):* the page as built. `wowlab looks page [--out
PATH] [--build V]` picks the build as `show` with no name does (every saved
look's build counts) and writes one file, `<user data>/wowlab/pages/looks.html`
by default (`wowlab_core.lookspage`, ADR-0027). Its JSON block
(`LooksPageData` in `cli`) holds what the commands print, computed by the same
functions: the `races` list, one view per flagged playable race, body type and
class (none, then each `ChrClasses` id) with the options and findings
`options` gives (options stored once and referenced by index), every saved
look as `show NAME --build <that build>` reports it, the damaged files, and
the same caveats (the playable note, the exported-tables-only remark, where
class ids come from, what is refused and what is a note). Race and class are
not checked as a pair: every class is offered with every race, and each class
view says so **[verify]**. Paths on the page show the home directory as `~`.
The script only displays that data, with `textContent`; the CSP is
`default-src 'none'` with the one inline script and the one style allowed by
SHA-256, so the page fetches nothing. `--out` (or the default folder) is
refused before any work, and again before and after creating its folder, when
it or a folder above it holds `.build.info` or `.flavor.info`
(`lookstore.refuse_install`, which also refuses a path that cannot be
resolved), when it is inside the user data directory but outside `pages/`
(compared by NFC-normalized, case-folded text, and by file identity: the
folder's deepest existing folder against the path's existing folders, with
the folder's components that do not exist yet compared as folded text, so a
case, firmlink or Unicode spelling does not get in even before the folder
exists), when it would go into a `pages/` folder that is itself a link (a
symlink or junction), when it is within the `store`, `gamedata` or `looks`
folder by the same identity and text tests, even when that folder is a link
out of the user data directory or a dangling one (M11-17, 2026-09-28), when it
is a directory or not a regular file, and when it is an existing file that
does not begin with the page's own header (the doctype and the CSP meta tag):
exit 1, nothing written. While `pages/` is a link, a path inside the link's
target is refused too, however it is spelled, not only a path through the
link. An existing page there is replaced whole. Damaged
look files are listed on the page and named on stderr, and the command then
exits 1, as `show` does.

*Amended 2026-09-29 (M11-23):* `import-char` as built. `wowlab looks
import-char <name> [--character C] [--account A] [--class C] [--replace]
[--build V] [--json]` reads a character's `WowLab.lua` the same way as `char show`
(the newest by modification time unless `--character` names one; read only)
and turns its customization record into a look
(`labaddon.customization_import`): `race_id` is the race, `sex` (0 or 1) the body type, read as `ChrRaceXChrModel.Sex` (0 confirmed by the M11-23 capture, 1 **[verify]**), each option with a choice id one mapping. An option recorded
without a choice id is left out and named, and a record without a race or
`sex` (or with `sex` other than 0 or 1, an option listed twice, or more
than 256 choices) is not imported. A section absent with a reason gives the addon's reason, and a section
missing from the file gives "not in the file" (a reason the reader clipped
says so). When the file was picked as the newest, the error says so and
names `--character`. Both exit 1, write nothing
and ask for no tables. The look is checked and saved as `save` does it: a
refused look is not written (exit 1). The build is `--build`, else the
flavor's version. The record holds no class, so without `--class`
class-restricted choices are noted. Remarks say when the record was made,
by its own fields: "as of the last barber-shop open" (`recorded_at` "open"),
"as of the last applied barber-shop change" ("applied"), or "as of the last
barber-shop visit" (not recorded), next to `recorded_at`, the addon's
`as_of` and how many logins or reloads ago. They never call the look the
character's appearance now. They also say: a record left at "open" may miss
a change applied during that visit (M11-23 capture, **[verify]**); a paid
change that keeps the race is invisible; only the options the barber shop listed are recorded, for the model it showed, which can be fewer than the tables give that model (7 of 10 on the M11-23 capture); a choice depending on an unlisted option is undecided (the unlisted options are named); a missing `chr_model_id` means the body
type came from `sex`, and a `chr_model_id` that differs from the tables'
model for that race and body type is named; the recording client's build
when it differs from the checked build. A saved look now has `origin`
(`SavedLook`, additive, format stays 1): `typed` (`save`, and every file
written before it) or `imported`; an imported look also keeps
`recorded_client_build`, the recording client's full version (optional,
additive). Every command words an id the build lacks by origin: a typed id
as "is not in build <version>'s tables: check the id …" (as `save` did), an
imported one as "unknown to build <version> (possibly a hotfix)", or, when
the recording client was another build, "(recorded by client <build>, not
the build of these tables: the id may exist only in that build, or come
from a hotfix)"; only option and choice ids from the record get that
ending, not a requirement id the tables name.
`show`, `compare` and `page` of an imported look add a remark naming the
look: it holds the choices the addon had last recorded in the barber shop
before the import, and may miss a change applied during that visit
**[verify]** and any change since; plus the recording client's build.
`LookStore.save` refuses a look whose file would be over `MAX_LOOK_BYTES`,
the limit `read` applies, before creating anything. `char show` adds
"Recorded when the barber shop opened: a change applied during that visit
may not be in it [verify]." to a record with `recorded_at` "open".

### 13.3 profiles

Named sets of the client's local UI files (not the whole UI: action-bar
contents and talents live on the server **[verify]** and are not in any
profile), on top of `snapshot` and `guard`. `wowlab profile save
<name> [--preset P | --subtree S…]` takes a snapshot restricted to the chosen
subtrees and labels it; `profile apply <name>` restores those subtrees
through `guard` (plan, prompt, undo), skipping file-map Edit `no` files as a
whole restore does (owner decision 2026-09-27); `profile list | show |
delete`. Presets are data, not flavor constants: `ui`: `Config.wtf`
(machine scope: it also holds graphics, sound, locale and the last account),
account and character `config-cache.wtf`, the account and character
edit-mode caches, `layout-local.txt`, `chat-cache.txt`. `bindings`: account
and character `bindings-cache.wtf` (a character file overrides the account
one) and `click-bindings-cache.txt`. `macros`: account and character
`macros-cache.txt` (action buttons may refer to macros by slot, so a restore
can change what a button runs **[verify]**). `addons`: `Interface/AddOns/`,
`AddOns.txt` (on Forever in the `<Realm>/<First>/` twin), and addon
SavedVariables except the lab-addon's own `WowLab.lua`. `profile apply`
lists every `*-cache*` file it writes as "the server may replace this at
your next login (synchronize* CVars; see `wowlab doctor`) **[verify]**", and
says the result is proven only by logging in.
Whether `apply` also removes files added since the profile was saved is
decided in the ticket and stated in the plan.

*Decided 2026-09-28 (M11-08), confirmed as owner decisions of 2026-09-28
after the PR #96 reviews:*

- **A profile covers every character folder it recorded** (owner decision
  a). A preset profile records every subtree it considered, present or not:
  each preset name joined to every account and character folder `layout`
  found at save time (both Forever shapes). Applying a profile therefore
  affects every character it recorded, not only the one being played; the
  help and the plan say so (owner decision 2026-09-28). A character folder
  created after a preset save is not a recorded subtree and is not touched.
  An explicit `--subtree` is a whole file or folder: files added anywhere
  under it since the save are deleted, including every file in a character
  folder created under it since, and the plan says so. A `--subtree`
  broader than one account folder (`WTF`, `WTF/Account`, `Interface`,
  `Fonts`) is refused with a pointer to `snap create` and `snap restore`.
- **`apply` removes files added since the profile was saved, and rolls back
  addon updates** (owner decision b). A regular file now under a recorded
  subtree that the profile does not hold is deleted through the same `guard`
  transaction that writes the saved bytes back, so `apply` returns each
  subtree to what was saved (a character `bindings-cache.wtf` created after a
  `bindings` save is believed to mean that character switched to
  character-specific key bindings **[verify]**; deleting it is meant to
  switch it back, and with `synchronizeBindings` on the server may write it
  again at login), and `wowlab undo` puts the deleted files back. With
  `addons`, apply returns `Interface/AddOns/` to the saved code: addons
  installed since are removed with their SavedVariables, and addons updated
  since go back to the saved version, which an addon manager will not know
  about. One apply deletes at most 2,000 files; above that it is refused
  with the count and a pointer to `snap restore` or a narrower profile (the
  gate re-walks the install per delete; a gate-side listing cache is a
  follow-up).
- **The lab-addon is always left alone** (owner decision c). The lab-addon's
  code (`Interface/AddOns/WowLab/`) and its SavedVariables (`WowLab.lua`) are
  never saved, restored or deleted by a profile; only `wowlab addon
  install|remove lab` changes them. This holds for presets and `--subtree`
  alike (the `[exclude]` table of the presets file; `WowLab.lua.bak` too).

Also left alone, and listed: file-map Edit `no` files (never written or
deleted, as in a whole `snap restore`), anything added that is not a regular
file, and any path the gate will not write or delete (an executable, changed,
removed or added): the rest of the apply goes ahead. When the gate refuses
the whole restore and `profiles` compares entries with the disk itself, it
reads through no link: every folder from the flavor folder down is checked
with `lstat`, a file is opened with `O_NOFOLLOW` and must be a regular file
by `fstat`, and an entry behind a symlinked or junctioned folder is not read
and is listed as left alone (fix round 2, 2026-09-28). On POSIX that walk
goes by directory descriptor from the install root, each folder checked and
then opened relative to the one before with `O_NOFOLLOW`, so a folder
swapped for a link between the check and the open is never read through;
Windows, where `os.open` takes no `dir_fd`, keeps the `lstat` walk and the
junction check (M11-12, 2026-09-28). `profile save` and `profile show` note
each saved subtree root that is itself a symlink or junction ("<path> is a
link; this profile holds the link, not what is behind it"), in human and
`--json` output; a root reached through a linked folder (`WTF` itself a
link) is refused by the snapshot, so nothing is saved (M11-12, 2026-09-28).
On POSIX a real file whose name holds `:` or `\` always counts as changed in
that fallback comparison, which does not read it. This is harmless, because
the gate never writes or deletes such a name either: `profile apply` leaves
the file alone and lists it with the gate's reason, and applies the rest of
the profile. The cost is that a changed or removed file with such a name is
not put back by `profile apply`, and a whole `snap restore` of a snapshot
holding one is refused outright, as before (M11-14, 2026-09-28).
Folders emptied by a
deletion stay, since the gate deletes files only. Every `*-cache*` file in
the plan is listed, a write with "the server may replace this at your next
login (synchronize* CVars; see `wowlab doctor`)" and a delete with "the
server may write this file again at your next login (…)"; a plan that
touches `macros-cache.txt` also warns that action buttons may point at a
macro by its place in the list **[verify]**.

Presets live in `wowlab_core/profile_presets.toml`. A profile is a snapshot
whose manifest carries `purpose: "profile"` (§6.9, amended 2026-09-28) and whose
label is `profile:<name>` (optionally ` presets=<p>,…`); names are unique in
the store. `wowlab snap create -m` refuses labels starting with `profile:` or
`deleted-profile:` (exit 2). `profile delete` relabels the snapshot
`deleted-profile:<name>`, and the snapshot stays (snapshots are immutable).
`profile apply` also takes `--dry-run`.

### 13.4 sv-merge

`wowlab sv merge <file> --from <character|snapshot> --into <character>
[--key PATH…]`: a three-way structural merge of one SavedVariables document
with `luadata` (base: the same file in a snapshot that holds it for both
sides, i.e. `--from snapshot`; for two different characters there is no
common ancestor, so the merge is two-way: any key whose values differ is a
conflict, keys present on only one side are listed, and `--key` subtrees are
copied as a whole; ours: the target; theirs: the source), by key path. The
file's scope comes from `layout` (account or character,
`docs/LAB_FILE_MAP.md`). `--from/--into <character>` applies only to
per-character files (`## SavedVariablesPerCharacter`), and on Forever it
resolves to `<digits>/<First>-<Second>/SavedVariables/`, never to the
`<Realm>/<First>/` twin. For an account-wide file both characters share one
file, so a character-to-character merge is refused with that reason; the
account-wide case is `--from <snapshot>` (another machine or an earlier
state). Many addons keep per-character settings inside the account file,
keyed by a character string whose Forever spelling varies by addon
(GLOSSARY, "Second name"); copying between those keys is a `--key` copy
within one file, not a merge between files. A key missing from one side is
reported as "absent", not "deleted": many addons leave out values equal to
their defaults when the file is written **[verify]**, and the Lab cannot see
those defaults. A key changed on one
side is taken; changed on both sides the same way is taken once; changed
differently is a conflict, listed, never guessed, and nothing is written
unless `--take ours|theirs` resolves it. `--key` limits the merge to named
subtrees (copy one addon profile). Output goes through the serializer (the
document's own style) and is written by `guard`. The addon reads the
merged file at next login, may migrate it, and rewrites it at logout: the
merge is proven only after one login and logout, and a subtree taken from an
older addon version may be reset by the addon.

Before writing, `sv merge` checks the SavedVariables loader. The Forever beta
had a bug where SavedVariables were written but not loaded back
(https://github.com/nobewayo/ForeverSVFix, now reported fixed); if it
returns, the target addon loads defaults at the next login and saves them at
logout, overwriting the merge, which would look like an sv-merge bug.
`sv merge` reads the target character's `WowLab.lua` first and refuses with
exit 3 and a clear reason when `probe.lost` is true, or when two snapshots
of that file show `loads` not going up. `--force-loader-check` overrides the
refusal. With no capture of `WowLab.lua`, it warns and continues.

Graders come first (`M11-09T`), since it rewrites user data.

*Amended 2026-09-29, M11-09T conductor rulings:* the text above leaves
several choices open. The graders (`lab/core/tests/test_svmerge.py`) fix
them as follows, and M11-09 builds to them.

- **Three-way needs `--base SNAPSHOT`.** `--from <snapshot> --base
  <snapshot>` is the three-way merge: base is the file in the `--base`
  snapshot, theirs is the file in the `--from` snapshot, and ours is the
  target on disk. `--from` alone, whether it names a snapshot or a
  character, is the two-way merge. `--from` is either a snapshot id or a
  character folder; `--into <character>` is always required, because the
  loader check reads that character's `WowLab.lua`.
- **A copy within one file is `--key SRC=DST` with no `--from`.** `SRC` and
  `DST` are paths in `sv dump --path` syntax. `--key PATH` means the same
  path on both sides. In the two-way merge a `--key` subtree is copied
  whole. With `--base`, `--key` limits the three-way merge to that
  subtree: inside it the three-way rules apply, and changes outside it are
  neither taken nor listed as conflicts.
- **FILE is resolved like this.** A bare file name is looked up in the
  `--into` character's `SavedVariables/`, then in the account's. A path is
  taken as `sv dump` takes it (absolute, relative to the current folder,
  or relative to the flavor folder), and `layout` gives its scope. On
  Forever the `--into` character is the `<digits>/<First>-<Second>` folder,
  so the target path never lies in the `<Realm>/<First>/` twin. The graders
  cover this only indirectly.
- **Exit codes.**
  - 1: conflicts left unresolved. The report lists them and nothing is
    written.
  - 2: a character-to-character merge of an account-wide file (usage). The
    message says the file is account-wide.
  - 3: a refusal by the write gate or by the loader check.
- **Conflicts are leaves, keyed by Lua key.** Tables present on both sides
  are merged key by key. A conflict is a scalar, or a key holding a table on
  one side and a scalar on the other. Positional entries are keyed by their
  index (the Lua key they load as). On the M11-03 pair this gives 235
  conflicts, plus class-tree `nodes[53]` and `[54]` on one side only.
- **Keys on one side only.** Such a key is reported as `absent` with the
  side it is missing from, and it is never deleted from ours. In the
  two-way merge it is also never added. In the three-way merge a key theirs
  added (absent from base and ours) is a one-sided change and is taken.
  `--take ours|theirs` resolves conflicts, and resolved conflicts are still
  listed.
- **Paths are spelled as `wowlab sv dump` prints them:**
  `WowLabCharDB["probe"]["loads"]`, with `[n]` for a number key or a
  positional entry.
  *Amended 2026-09-29 (M11-25):* printed string keys are always
  double-quoted; `\a \b \t \n \v \f \r` are named escapes; other C0, DEL,
  C1 (as UTF-8 bytes), Unicode format and separator characters (as UTF-8
  bytes) and non-UTF-8 bytes print as three-digit `\ddd`; number steps are
  spelled as the file holds them; unknown escapes are refused (exit 2);
  whitespace inside brackets is Lua whitespace only; one grammar,
  `svmerge.parse_path`, serves `sv dump --path` and `sv merge --key`.
- **Output style.** Unchanged nodes keep their bytes and keys keep the
  target's order (the client writes in its own hash order; the Lab invents
  none). A taken number keeps its source text. Taken nodes are laid out in
  the target document's style (line endings, indentation, spacing).
- **Loader check.** "Two snapshots" are the two newest snapshots in the
  store that hold the `--into` character's `WowLab.lua`.
  - `loads` lower in the newer snapshot: refused.
  - `loads` equal: refused too, because a loader bug that persists makes
    every session write `loads = 1`, so snapshots read N, 1, 1, 1.
  - Exception: when the two snapshot entries of that file are
    byte-identical, the client did not write in between. The merge goes
    ahead with the note "no login between the two snapshots; the loader
    was not re-checked".
  - `loads` higher: passes.
  - `probe.lost = true`: refused.
  - No `WowLab.lua`: a warning, and the merge goes ahead.
  - *2026-09-29, M11-09 review ruling:* the file on disk is also compared
    with the newest snapshot that holds it. `loads` on disk strictly lower
    than that snapshot's: refused ("loads on disk (n) went down from the
    newest snapshot (m): the last session's SavedVariables did not load").
    Equal `loads` with other bytes passes with no note (the same session,
    the file edited after the snapshot); byte-identical passes. This
    catches a loader reset (N, then 1 on disk) whenever the newest snapshot
    holds N ≥ 2; a reset from a snapshot at 1 cannot be detected this way.
  - *2026-09-29, owner ruling for M11-24 (built in M11-24, as amended below):*
    - **`probe` stays ours.** When `WowLab.lua` itself is merged, the
      `probe` table of the `--into` file is kept whatever `--take` or
      `--key` says. It counts that character's logins, and copying it
      would make the next loader check read a counter the client never
      wrote. This supersedes the M11-09T grader that copies it, through new
      graders from a test-writer.
    - **Walk past identical snapshots.** When the two newest snapshots of
      the file are byte-identical, compare against the newest snapshot
      whose bytes differ from them, not against nothing. The "no login
      between the two snapshots" pass applies only when no older differing
      snapshot exists.
    - **A Lab write is not a loader failure.** A comparison that spans a
      committed guard write to that `WowLab.lua` (a `snap restore`, a
      profile apply, an earlier `sv merge`; the guard journal records each)
      is skipped. The merge prints the note "the Lab wrote WowLab.lua after
      that snapshot; the loader was not re-checked", instead of blaming the
      loader.
  - *2026-09-29, owner ruling amending "A Lab write is not a loader
    failure" (M11-24 security review; built in M11-24):* skipping hid real failures. Guard
    snapshots before it writes, so the comparison after a Lab write
    usually spans the next login too, and a loader failure in that session,
    or one that persists, passed. So a comparison that spans a committed
    guard write to that `WowLab.lua` is **compared from the state the write
    left**, never skipped. W is the latest such write in the span, and
    "after" is the hash the journal records for what W wrote.
    1. **No session since W.** If the newer side of the comparison (the
       disk, or a snapshot) is byte-identical to W's `after`, the client
       has not written since. The merge passes with the note "the Lab wrote
       WowLab.lua after that snapshot; the loader was not re-checked".
    2. **A session since W.** Otherwise the older side becomes W's result:
       the store object whose hash is W's `after`. Its `probe.loads` is
       compared with the newer side under the normal rules (lower: refused;
       equal: refused unless byte-identical; higher: passes). Every Lab
       write to `WowLab.lua` (`snap restore`, profile apply, `sv merge`)
       keeps the bytes it wrote as a store object, so this object exists
       for any write made after M11-24.
    3. **W's result cannot be read** (the object is missing, damaged or
       unparseable, or the write predates this rule): refused, naming the
       journal record, and `--force-loader-check` overrides. Never skipped.
    - A journal record or snapshot dated later than the current time is
      ignored for this check, with a note naming it: a clock that ran ahead
      must not turn the check off.
    - The graders for this amendment come from a test-writer (M11-24T2).
      The M11-24 implementation is not merged until they pass.
    - *Conductor rulings within the amendment, 2026-09-29 (M11-24T2, as
      built in #136):*
      - **Profile apply** never writes `WowLab.lua`: profiles leave out the
        lab-addon's SavedVariables (§13.3), so the "profile apply" in the
        lists above names no real case.
      - **Rule 1 is checked first.** It wins over rule 3: identical bytes
        prove no session ran, so a missing or damaged object does not refuse
        then.
      - **A rule-2 pass** prints no rule-1 note, since the loader was
        re-checked. Rule-1 notes print only when the whole check passes.
      - **Where the result comes from.** `snap restore` and undo write from
        snapshot objects that already exist; `sv merge` stores what it wrote
        with `SnapshotStore.put_object` inside the guard transaction, before
        the write.
      - **gc keeps them.** `snap gc` keeps every `after` a committed journal
        record names. M11-31 narrows this to what the check can need.
  - `--force-loader-check` overrides every refusal.
- **`--json` is required** (§6.11). It prints `SvMergeReport`, holding at
  least `mode` (`two-way` or `three-way`), `conflicts`, `absent` (with
  `missing_from`), `taken`, `written` and `notes`. The report is printed
  when conflicts stop the write, too.
- **The library entry point** is
  `wowlab_core.svmerge.merge(ours, theirs, *, base=None, keys=(),
  take=None)`. It works on `luadata` documents, reads and writes nothing,
  and returns the merged document with its `conflicts`, `taken` and
  `absent` lists.

### 13.5 Order

```
M10-13, M10-14, M10-18 ── M10-15 Wave 1 review ── M11
M11-01 addon + lint ── M11-02 addon install ── M11-03 owner capture ─┬─ M11-04 labaddon reader + char show
   (critical path)                                                   └─ M11-09T → M11-09 sv-merge
M11-05 customization tables + looks model ── M11-06 looks CLI ── M11-07 looks page
M11-08 profiles
                                                                        all ── M11-10 wave review
```

Wave 1 closes first (§11). M11-01 → M11-02 → M11-03 is the critical path:
it is dispatched first once Wave 1 closes, because the owner's capture
(M11-03) resolves most of §13.1's **[verify]** items and M11-04 (which
also carries `looks import-char`, moved from M11-06 on 2026-09-28) and
M11-09T/M11-09 wait on it. M11-05 and M11-08 run
alongside it. M11-02, M11-04, M11-06 and M11-08 add CLI commands, so they
depend on M10-14.

M11-01 and M11-05 are the only M11 tickets that may run in parallel with
the end of Wave 1: once M10-15's handoff is written and this plan has
merged, they can start while any remaining Wave 1 fix finishes. It is safe
because they add new files only (`lab/addon/WowLab/`, and recorded wago
tables plus a new `wowlab_core.looks` module) and touch no CLI command,
`luadata`, `guard` or other Wave 1 module, so they cannot collide with a
Wave 1 fix.
