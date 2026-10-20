# Lab — Plan

**Status:** Wave 1 specified (2026-09-20), revised 2026-09-21 when the Lab
became the whole repository (ADR-0025). Later waves are chosen by the owner
after each wave ships (ADR-0024). Each later wave is specified only once the
owner has picked it, in its own section: Wave 2 in §13, Wave 3 in §14
(re-scoped 2026-10-14, §14.8), Wave 4, the first public release, in §15
(amended 2026-09-30 and 2026-10-14; this line used to say that nothing
beyond Wave 1 is specified here) and Wave 5, `wowlab ui`'s confirm flow
and the character, in §16 (2026-10-18).

The Lab is the application this repository exists for, not a track beside
another product. It is a local toolchain for each player's own machine: it
reads a World of Warcraft install directly, explains what is in it, snapshots
it, and lets the player change things and change them back (ADR-0019). Other
players may install it from a tagged GitHub release (ADR-0031).

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

- **Local-only.** The Lab runs on the player's own machine, as that player,
  against their own install. Players install it from a tagged GitHub release
  (ADR-0031); it is never deployed as a service (ADR-0019).
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

*Amended 2026-10-17 (M13-37):* the "symlink" of (6) is a link by
`install.is_link`, the one link test discovery shares with `layout`, `tree`,
`filemap gaps` and `explain` (`layout.is_link` and `gaps.is_link` are the
same function; the M13-35 amendment under §6.2): a symlink, a junction, or
any other Windows reparse point with the name-surrogate bit (a WCI link, a
WSL symlink). A child named `_*_` that is such a link, or whose
`.flavor.info` is one, is named in `other_dirs` and nothing is `stat`ed,
listed or read through it, so no command builds a `Layout` for it. A
reparse point without that bit (a cloud placeholder) is taken as before.

*Amended 2026-10-17 (M13-45):* **`Install.not_taken`** names the folders of
`other_dirs`, in the same order, each as a `NotTaken` with why it was not
taken as a flavor folder. `name`. `reason`, one of `link` (the folder is a
link by `is_link`, never followed), `no_flavor_info` (no regular
`.flavor.info`, which covers one that is a link), `not_checked` (its `lstat`
was refused) or `flavor_info_changed` (its `.flavor.info` was regular at the
scan and was refused at the read, M13-46: it became a link or no longer a
regular file, or another file took its place; never worded as a link, since
that file may be regular). `target`, a link's text as stored when it can be
read (read from the link itself with `readlink`, never through it, and on
Windows spelled without the extended-length prefix as `tree` spells it,
M13-43; None for a WCI link or a WSL symlink, and for every other reason).
`note`, the reason in the words the commands print:
`install.NOT_A_FLAVOR` ("not taken as a flavor folder (no regular
.flavor.info file)"); "link to <target>, not followed; " or "link (where it
points is unknown), not followed; " followed by `install.NOT_A_FLAVOR_LINK`
("not taken as a flavor folder"); "not taken as a flavor folder (it could
not be checked: <the operating system's words>)" (`NOT_A_FLAVOR_UNCHECKED`
without them); or `install.NOT_A_FLAVOR_CHANGED` ("not taken as a flavor
folder (its .flavor.info changed as it was read; run the command again)").
The first two constants moved from `gaps`, which re-exports them unchanged.
`other_dirs` stays, for scripts that read it. The field is left out of the
JSON when it is empty and reads back as empty, so the JSON of an install with
no such folder is byte for byte what it was (`Field(exclude_if=...)`, which
needs pydantic 2.12, so the floor is `pydantic>=2.12`). `doctor --json`
carries the same list as `not_taken` (redacted under `--redact`: `reason` as
is, the rest replaced), and `explain --json` carries the one entry a path's
first part names as `not_taken`, left out when there is none.

The words say "not taken", never "is not a flavor": the client reads through
a linked flavor folder like a folder, and only wowlab declines to. `doctor`
and `install show` print "Folders named _..._ not taken as flavors:" and one
line per folder, `<name>: <note>`. `tree --install` and `filemap gaps` give
a folder its own note (`install.folder_note`; `NOT_A_FLAVOR` was given to
every folder before) and a link `NOT_A_FLAVOR_LINK` after their link words,
as before. A `--flavor` that names such a folder is answered with its note
(and, for a link, `install.NOT_TAKEN_LINK`, "wowlab reads a flavor only
from a real folder in the install"), then "; flavors found: ...", instead of
"no flavor folder". When there is no flavor and `not_taken` is not empty,
"<root> has no flavor folders" (literally false of a junctioned `_retail_`)
becomes "<root> has no flavor folder wowlab can read (a real folder named
_..._ holding a regular .flavor.info)" (`install.NO_READABLE_FLAVOR`) in the
refusal of a command that needs a flavor, which then adds "not taken: <name>
(<reason>), ...", in the first run, and in `doctor`'s `problems`; with
`not_taken` empty the old words stay. The first run says a linked folder is
not followed, that its WTF (settings and SavedVariables), Interface and
Fonts are not snapshotted, and to back up the folder it points to.
`explain` reads a relative path whose first part is such a folder at the
install root, not in the one flavor folder (M13-29 reads it there
otherwise); a link there, or a path inside one, prints the verdict's words
first, and any path under such a folder then prints `<name>: <note>`. Every
other link prints as before.

*Amended 2026-10-18 (M13-56):* `doctor`'s no-flavor problem, when
`not_taken` is not empty, is the refusal's words in full, "not taken:
<name> (<reason>), ..." and, for a link, `install.NOT_TAKEN_LINK` included,
so `--json` says why as the text does. Whenever `problems` is not empty, the
text of `doctor` prints "Problems:" and one indented line per entry after
the snapshot-store lines and before any first-run section (a run that skips
the first run, under `--redact` or once a snapshot of the install exists,
exited 1 with no line saying why); with no problem nothing is added. A
failed first snapshot's "first snapshot: <error>" is added to `problems`
after that block is printed, so it is never in it; the first run has
already said "The snapshot could not be taken: ..." (or the write gate's
refusal). A first run that finds a flavor prints, unindented under the
flavor list, one line per folder of `not_taken` that the client may play
from (`firstrun.beside_lines`): for a link, the line the link-only first
run gives it ("<name> is a link to <target>, not followed, so its WTF
(settings and SavedVariables), Interface and Fonts are not snapshotted: ...
Back up the folder it points to yourself."); for `not_checked` and
`flavor_info_changed`, "<name> could not be read as a flavor (<why>), so
nothing in it is snapshotted; " and what to do ("check that you can open
it, run wowlab doctor again", or "run wowlab doctor again"), then ", and
once it is listed as a flavor: wowlab snap create --flavor <name>" (once
`doctor --yes` has snapshotted the readable flavor, the next `doctor` is not
a first run and neither offers a snapshot nor says this folder has none). A
folder with no `.flavor.info` is not repeated there. On Windows, a link
whose target cannot be read (a WCI link or a WSL symlink) adds, in both
first-run cases, "To see where it points: `dir /AL` in the folder that
holds it shows a junction's or symbolic link's target; a link made in WSL
shows its target with `ls -l` inside WSL." `FirstRunReport.not_snapshotted` stays flavor
folders only; `--json` readers find links and the other folders in
`doctor`'s `not_taken`.

*Amended 2026-10-18 (M13-54):* `O_NOFOLLOW` (M13-46) guards only the last
component of a path, so a `_*_` folder swapped for a link to a directory
between the scan and the read of its `.flavor.info` was read through. The
read now holds the folder to the directory the scan `lstat`ed (same device
and inode, a directory, no link by `is_link`): the folder is `lstat`ed again
just before and just after the read. On POSIX it is also opened with
`O_DIRECTORY | O_NOFOLLOW`, for search only where the platform can
(`O_SEARCH` on macOS and the BSDs, `O_PATH` on Linux, so a folder that may
be searched but not listed is held like any other), the descriptor's `fstat`
checked against the scan's `lstat`, and `.flavor.info` `lstat`ed and opened
relative to that descriptor (`snapshot.open_regular_file(..., dir_fd=)`), so
nothing put in the folder's place is ever opened. A refused open is a changed
folder, never a reason to read by path (M13-54 review: a directory with no
permissions put in the folder's place for a moment would otherwise force the
read by path, and a link swapped in next would be read through). Where
neither search flag exists the open is for reading, and a refusal falls back
to the `lstat` floor below only when the scan's own `lstat` already denied
this user read permission on the folder. A folder that fails any of these is named
in `other_dirs` with the new `not_taken` reason **`folder_changed`**, note
`install.NOT_A_FLAVOR_FOLDER_CHANGED` ("not taken as a flavor folder (it
changed as it was read; run the command again)"), never worded as a link:
what took its place may be a real folder. When the folder changed, that
reason is given even if its `.flavor.info` was refused too. The first
run's line for a folder beside a flavor (M13-56, `firstrun.beside_lines`)
treats it as `flavor_info_changed`: "<name> could not be read as a flavor
(it changed as it was read), so nothing in it is snapshotted; run wowlab
doctor again, and once it is listed as a flavor: wowlab snap create
--flavor <name>". On Windows
(no `O_NOFOLLOW`, no `dir_fd`), and in the POSIX case just named, there is
no descriptor and the two `lstat`s are the floor. What that floor leaves
open: a folder swapped for a link (a junction, say), or for another real
directory, and swapped back again, both between the two `lstat`s, i.e.
during the read of one short file. `.flavor.info`'s own `lstat` and open
would then both name the file in what stood there and agree, so up to 4096
bytes of it would be read as the folder's product code. It takes two renames in
the install root inside that window by a process that may already write
there; nothing is written, the folder is reported under its own name, and
every later command (`layout`, `tree`, `filemap`) judges the folder again by
its own `lstat`.

The root `.build.info` was checked by `stat` and then read by a plain open,
so a FIFO or device put there between the two blocked discovery for ever. It
is now read by `snapshot.read_regular_file` with that `stat` as `expect`:
the open never blocks, the descriptor must be a regular file and the file
the check saw. A `.build.info` that is a link to a regular file is still
followed, as the check follows it: the path it resolves to is `lstat`ed
and must name the checked file before it is opened, and the opened file is
held to it again. One that changed between the check and the read
raises `NotAnInstallError` with `reason` `unreadable` and the words "cannot
read .build.info (it changed as it was read; run the command again)". The
discovery output of an unchanged install is byte for byte what it was.

### 6.2 `layout` — typed walk of a flavor (M10-06)

Pure read. Produces the inventory every later tool starts from:

- `accounts()` → account folders under `WTF/Account/`, each with its realms
  and characters (`WTF/Account/<ACCOUNT>/<Realm>/<Character>/`).
  *Amended 2026-10-18 (M13-50):* a folder there is an account folder when its
  name has the `<digits>#<n>` shape of a current client's account folder, or
  when it holds a realm or digits folder with a character folder inside (an
  old account name). Any other folder (the client's own
  `WTF/Account/SavedVariables/`, `docs/LAB_FILE_MAP.md`) is no account: it is
  named in `Inventory.not_accounts` and `WtfWalk.not_accounts`, the files
  below it are WTF files of scope `other` with no account, and no account
  row of the file map applies to them (`Layout.is_account_folder` judges one
  folder alone).
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

*Amended 2026-10-17 (M13-35):* a "link" here is what `layout.is_link` says,
the one test `filemap gaps` (`gaps.is_link`), `explain` and `tree` share: a
symlink, a junction, or any other Windows reparse point with the
name-surrogate bit (a WCI link, a WSL symlink). Each is reported in
`Inventory.symlinks` and never walked. A WCI link or a WSL symlink has no
link text `os.readlink` can read, so it has no target and no inside or
outside answer: `Symlink.target` and `Symlink.inside_install` are None, and
`tree` says "link (where it points is unknown), not followed". A link whose
text reads but whose resolution fails (a loop) has a target and no inside
or outside answer either: "link to <target> (where it points is unknown),
not followed". `tree` words
a link the same way at the install root and in a flavor folder: "link to
<target> (inside|outside the install), not followed". A flavor folder that
is such a link is listed by `tree` as a link, and nothing in it is listed
(`tree --install` notes it as "flavor folder; link …, not followed; nothing
in it is listed"). Since M13-37 discovery uses the same test, so such a
folder is named in `Install.other_dirs` rather than taken as a flavor, and
`tree --install` notes it as "link …, not followed; not taken as a flavor
folder" (`gaps.NOT_A_FLAVOR_LINK`, worded so since M13-37 so that "not
followed" is said once); the flavor-folder wording above
remains for a folder that becomes a link after discovery. A reparse point
without that bit (a cloud placeholder, a deduplicated file) is walked and
named in `Inventory.reparse_points` (M13-33).

*Amended 2026-09-22 (M10-06 reviews):* (1) **Symlinks and junctions are never
followed**, whether they point inside the install or outside it. Each is
reported in `Inventory.symlinks` with its link text and `inside_install`,
and left out of the typed lists. `inside_install` uses `Path.resolve()`,
which reads link text and metadata but never lists or reads anything
outside; on Windows, resolving opens a link with no access to read its
final name (wording corrected 2026-10-17, M13-35). (2) **Signatures:** `classify(path, install, *, is_dir=None)`
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

*Amended 2026-10-17 (M13-29, #242):* **a path that does not exist is not a
folder.** `classify` with no `is_dir` given probes the disk with `lstat`
(never through a link: the probe walks down from the install root one part
at a time and stops at any symlink, junction or name-surrogate reparse
point, treating the rest as unknown). A missing path is classified from its
shape only where a row says so: file rows (a name of a known shape, such as
`SavedVariables/<x>.lua`) and `**` rows still answer for a missing path;
folder-only rows (`realm-folder`, `numeric-folder` and the like) answer
only when the path ends in a separator or `is_dir=True`. Otherwise the
result is `Unclassified` with the reason "not found; nothing is at this
path", and `explain` says so in plain words and adds "end it with / to see
what a folder by this name would be". "Not found" is said only when nothing
is on disk under every reading of the path: for a path given relative to
the install, at the install root and in the chosen flavor (`--flavor`, or
the only one), or in every flavor folder when several exist and none was
chosen; when any reading finds something, the output is unchanged.
`ExplainReport` gains `absent`; `layout.absent()` is public. A missing path
that a row does describe keeps its row and gets an "on disk: nothing at
this path" line; views are not offered for it. Callers that pass `is_dir`
(`profiles`, the tree builders, `snap restore`) are unaffected.

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

*Amended 2026-09-29 (Wave 3 plan; ADR-0028 accepted by the owner 2026-09-30):* typed columns arrive in Wave 3
as `wowlab_core.db2lake` (§14.2, ADR-0028), inferred from the build's data
rather than from WoWDBDefs, and `gamedata.rows` stays untyped. A table the
build lacks answers `HTTP 400` at wago, which M12-02 maps to
`TableNotPublished`.

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

*Amended 2026-10-18 (M13-41):* `read_log` and `tokenize` take `events=`, the
event names a caller reads. The 1 MiB rule comes first and is unchanged: an
over-long line is the truncated `Unparsed` it always was, whatever its event.
A line is then yielded as `Skipped` (its offset, event name, text and ending)
and not tokenized when the text before its first two spaces is a timestamp
and its first comma-separated field after them is a bare event name
(`[A-Za-z0-9_]+`) not among `events`. Every other line, including one whose
first field is quoted, holds a quote or bracket, or is not ASCII, is
tokenized exactly as without `events`. `Skipped` keeps the line's bytes and
ending, so a filtered read still rebuilds the file byte for byte. A
malformed line of an event not asked for is not tokenized, so it is a
`Skipped`, not an `Unparsed`. `wowlab binds coach` reads with
`events=CAST_COUNT_EVENTS` (`COMBAT_LOG_VERSION`, `SPELL_CAST_SUCCESS`), so
its count of lines the tokenizer could not read covers only those events'
lines and lines with no timestamp and bare event name.

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
  `Screenshots/` can be opted in; a restore never writes it back (§6.11,
  M13-30).
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

*Amended 2026-10-17 (M13-15, long paths on Windows):* `create` and
`list_tree` reach a path past Windows' plain limit (`MAX_PATH`) whatever
`LongPathsEnabled` says: `wowlab_core.longpath` spells a path whose absolute
form is 240 UTF-16 units or more (the unit Windows counts in; a character
outside the Basic Multilingual Plane counts two) as `\\?\C:\...` (or
`\\?\UNC\server\share\...`) as the argument of the system call only. Paths
are plain everywhere else. A root given relative is made absolute first; a
root given in `\\?\` form is recorded plain in the manifest's
`install_root`, and refused when it has no plain spelling (a volume GUID
path, a `..` component, a trailing dot). A file or folder the walk is told
is missing (`FileNotFoundError`, which Windows also raises for a plain path
past the limit) is skipped as removed only when an extended-length `lstat`
agrees; that `lstat` prefixes the listed path as it is, without normalising
it, so a name such as `foo.` is looked at itself. Otherwise `create` raises
`SnapshotError` and writes no manifest. The same spelling reaches `install`
discovery, `layout`, the first-run survey, `doctor`'s client and config
reads and the readers (`luadata.read`, `wtfconfig.read_*`, `toc.read_toc`,
`combatlog.read_log`). `Install.root` and `doctor`'s output are plain:
discovery reports a `\\?\` root with no plain spelling as not an install
(`unreadable`; the message quotes the root as given).

*Amended 2026-10-18 (M13-44, the readers M13-15 left on plain paths):* the
same spelling reaches `luadata`'s sibling reads for `serialize(...,
target=...)` (the folder listing, each sibling's `lstat`, real-path check and
open), `profiles` (the folder speller and the `lstat` walk `disk_state`
makes on Windows), `pathfacts` (`explain`'s spelling, `stat` and snapshot
facts), `gaps`, `addoninstall` (the source folder and the installed addon
folder), `combatlog.find_logs` and `follow`, and the plain-path lookups in
the CLI (`tree`, `explain`, `sv dump`, `sv merge`, `log tail`, `snap
restore`'s checks). A containment check resolves both sides by
`longpath.realpath` (the extended spelling resolved, then made plain), so a
link past the plain limit is still seen. A path argument typed in `\\?\`
form with a plain spelling (`explain PATH`, `sv dump FILE`, `sv merge FILE`)
is taken by that plain spelling, so `sv dump --json`'s `file` and
`explain`'s `query` name it plainly.

*Amended 2026-10-18 (M13-52, owner decision; M13-58):* a snapshot manifest
keeps a link's target text as `readlink` answered it, `\\?\` prefix
included on Windows, because `guard` and `profiles` compare that text raw
to raw and it is evidence of what the disk said; the two printers that show
a stored target (`snap show` and `profile save`/`profile show`) print it
through `longpath.plain_text` on Windows, relative text as stored, and
every `--json` keeps the raw string. The plain-path readers above also
include `labaddon`'s `WowLab.lua` reads (`char list`, `char show`, the
survey) and `tree PATH` / `tree --install PATH`, which take a `\\?\`
argument with a plain spelling by that spelling.

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

*Amended 2026-10-17 (M13-15, long paths on Windows):* every lookup, read and
write the gate makes in an install (the walk, the temp file beside the
target, the replace or rename, an unlink, a folder it creates or removes)
passes a path past the plain limit to the system in extended-length form
(`wowlab_core.longpath`, as in §6.9), so a file deep in an install is
written, restored and undone with `LongPathsEnabled` off. That includes a
target whose plain path is at the limit, where only the temp file beside it
is past it. The journal, the pre-write manifest and every message name the
plain path. A flavor handed to `transaction()` in `\\?\C:\...` form is taken
by its plain spelling, so `undo()` and restore work on what it journals. A
flavor that resolves to anything but a local absolute path (a network path,
`\\?\UNC\...` included, or a volume with no drive letter) is refused on
enter with nothing written or journaled (on Windows a network spelling is
refused before any lookup; on POSIX, where `//x` is the local `/x`, only
the resolved path is held to it): `undo()` and restore accept only a
local absolute root (item 1), and a change `undo()` could not reverse is
never written.

### 6.11 CLI — `wowlab` (M10-14)

```
wowlab doctor                       # install(s), flavors, builds, client running?, store health
wowlab install show [--json]
wowlab tree [PATH] [--explain]      # inventory; --explain adds the file-map entry per path
wowlab explain PATH                 # what is this file, who writes it, is it safe to edit
wowlab tree --install               # §15.6 (M13-22): install root and every flavor
wowlab filemap gaps [--json]        # §15.6 (M13-22): paths with no file-map row
wowlab ui                           # §15.6 (M13-24), ADR-0029: local UI, read-only in v0.1
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
wowlab char list [--account A] [--json]             # §14.4 (M12-09)
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

*Amended 2026-10-17 (M13-30, from the #195 domain review):* a snapshot
taken with `snap create --screenshots` holds `Screenshots/`, whose file-map
row (`screenshots`) has Edit `n/a`; the write gate refuses it. A
whole-snapshot restore of such a snapshot sets aside every entry under a
top-level folder whose row is `screenshots` (only that row; no other row
with Edit `n/a` is a snapshot subtree), and restores the rest. Each
screenshot is listed in `RestoreReport.not_restored` (apart from the Edit
`no` list in `skipped`) with what the disk holds now (`same`, `differs`,
`missing`, or `behind_link` when a folder on its way, `Screenshots/`
itself, is now a link: then nothing behind it is read; read-only, size
first, then content); it stays in the snapshot. The text plan counts them,
counts those that differ or are missing apart from those not read, names
the first ten of either kind with its state, and never offers `--paths`
for them; when every one matches it says the snapshot also holds them and
all still match. Naming one with `--paths` is refused
with the gate's reason (exit 3), followed by one line saying wowlab never
writes `Screenshots/`. Because the gate's whole restore cannot leave
entries out, the rest is restored by name, and every entry a whole restore
would check is named, so the gate refuses what it would refuse there (a
reserved name, a case collision, a changed link or executable) and a file
that changes between the plan and the confirm shows in its plan and rolls
the restore back. The only entries left unnamed are unchanged links and
unchanged executables, which a whole restore leaves alone and a named one
refuses; the CLI finds them by reading the disk (`profiles.disk_state`,
following no link; a Windows junction counts as a link, as it does for the
gate; an executable is unchanged only with the entry's bytes and permission
bits, as `guard` judges it) and reads them again before the commit. A whole restore that skips
an Edit `no` file is restored by name the same way. `guard` is unchanged.

*Amended 2026-10-17 (M13-38, from the #231 reviews):* an executable under
`Interface/AddOns/` has its own file-map row (`addon-executable`, Edit
`no`, M13-28). A whole restore skips a file named like an executable whose
row has Edit `no` (that row, or `os-metadata` for the `._helper.sh` macOS
writes beside `helper.sh`) when the disk holds a plain change of it: a
regular file with other content, every part spelled exactly as in the
snapshot and no link, junction or other reparse point on the way, or
nothing there: the file, or a folder on its way (a deleted addon,
sub-folder or `.app` bundle), absent in every letter case from a real
parent folder's listing. It is listed in `skipped` with its row, and the rest is
restored by name as above; the text plan lists these under their own
"Skipped … executable(s)" header, which does not offer `--paths` but says
to reinstall the addon. Only a regular-file entry the gate would refuse for
being an executable and nothing else is skipped: a reserved name, a link or
a folder where the file was, a name spelled otherwise on disk, one
behind a linked folder, one whose content
matches but whose permission bits do not, and an executable whose row is
not Edit `no` (outside `AddOns/`) still refuse the whole restore as the
gate does. Naming one with `--paths` is refused with the gate's reason,
followed by one line: name the other files with `--paths`, or (for the
`addon-executable` row) reinstall the addon to get its executable back.
`guard` is unchanged.
*Amended 2026-10-18 (M13-48, the owner's option (b)):* the behaviour above
stays, and a whole restore that the gate refuses because its reason names
an executable in this flavor now prints the same advice line after the
gate's line, exit 3, nothing written. That covers an executable whose
content matches but whose permission bits do not, a link or a folder where
the file was, a name spelled otherwise (a case variant), and an executable
outside `AddOns/`, which gets the shorter line without the reinstall advice.
A reserved name is refused for its name, so it gets the gate's line alone.

*Amended 2026-09-29 (M11-30):* text output is terminal-safe. No C0
control (tab and line feed included), no DEL, no C1 control and no Unicode
format or separator character (Cf, Zl, Zp) reaches stdout or stderr raw.
Each is shown as `\xNN` escapes, one per byte of the text as stored: C0 and
DEL as their one byte (`\x09`, `\x0a`, `\x1b`), C1, format and separator
characters as their UTF-8 bytes (U+0085 is `\xc2\x85`, U+202E is
`\xe2\x80\xae`), and a byte that is not UTF-8 as itself (`\x85`), so a C1
character and an invalid byte never print alike. The Cf, Zl and Zp set is
the one table §13.4's printed paths use, pinned to Unicode 15.0 (Python
3.12's database); a test over every code point of the running Python's
database is what catches a newer Unicode version that adds one. Letters,
accented or not, print as they are. A backslash is not escaped (every
Windows path has one), so a name holding the literal text `\x85` prints
like an invalid byte 0x85; that ambiguity is accepted.
This covers every text line: `_say` on stdout; messages on stderr, which
keep only their own line breaks (a value inserted into one, such as a path
or a folder name, fills a `{}` of the message with its line breaks escaped;
there is no other field syntax, so a value holding `{}` or `{:>9}` prints as
it is); errors, always one line, followed by any notes the exception
carries (such as the write gate's "the rollback did not finish"), one line
each; the library's log records (the write gate's warnings), which go to
stderr through the CLI's own handler, escaped and one line each, while a
command runs, instead of Python's fallback handler; and `db2 head`, whose
column names and cells are escaped before the CSV is written. `--json`
output is unaffected (JSON's own escapes, ASCII only). An uncaught
exception's traceback is not covered yet (a follow-up).

*Amended 2026-09-30 (M11-33):* that follow-up is done. Typer's pretty
(Rich) exceptions are off, and an exception nothing caught prints through
`_say_err` like every other line. That covers an exception `_handled` does
not catch in any command body, the `main()` callback and its
`ctx.with_resource`, and the eager `--version` callback. The `wowlab`
console script (`wowlab_core.cli:app`) sets `sys.excepthook` as an
exception leaves the app, because Typer's own `__call__` replaces the hook
on every call. A normal exit puts the hook back as it was. The hook prints
the traceback line by line in Python's layout, with these differences:
- the exception's message and each of its notes are one line each,
  whatever they hold, so text from the install never starts a line;
- a SyntaxError shows its `str()` (message, file, line) instead of the file,
  source and caret lines;
- there is no "Did you mean" for a NameError, AttributeError or ImportError;
- an exception group's sub-exceptions are listed as "sub-exception n of m:"
  with their lines indented instead of in Python's boxes. Python's limits
  still apply: 15 per group, then "and N more exceptions", and a group 10
  deep is one line;
- an exception is printed once. A cycle through `__cause__` or `__context__`
  stops where it meets one already printed, as Python's does. A group
  member already printed elsewhere in the traceback is the one line
  "[printed elsewhere in this traceback]", where Python prints it again.

Python then exits non-zero as usual (1, or by the signal for
KeyboardInterrupt). The hook never raises, not even on Ctrl-C while it
escapes a long message: a hook that raised would make Python print the
original exception itself, raw. Whatever stops it prints one line naming the
exception's type instead. Its last guard makes no call, because Python checks
for a pending signal at a call and a second Ctrl-C could escape it. A
`SystemExit` whose code is text, which Python prints raw without calling a
hook, prints as one escaped line and exits 1. Ctrl-C while that line is
escaped prints nothing more.

A click error (an unknown option, an extra argument, a bad value) prints its
usage and help hint as click does. Its `Error:` line, which can hold the
user's own arguments, prints as one escaped line. That holds also for one
raised as the root context closes. The help that `no_args_is_help` prints is
unchanged.

A library log record that carries a traceback (`_log.exception`, such as the
write gate's "could not record the rollback") or a stack prints its message
as one line and then the traceback line by line, rendered as the hook
renders it. Before this, the whole record printed on one line with `\x0a`
escapes. A record that cannot be formatted prints one escaped line naming it
instead of logging's own error report, which prints the record raw.

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

*Amended 2026-10-17 (owner decision; ADR-0019 amendment of the same date, accepted):* character names and realm names are not identifiers for this repository and may appear as they are; the scrub keeps rewriting and refusing only the account folder, email addresses and BattleTags, the owner's real name, a home path carrying the owner's login, the owner's own GUIDs, other players' GUIDs in combat logs with the unit name beside each, and the identity CVars on the deny-list. `wowlab doctor --redact` is unchanged. Existing fixtures keep their pseudonyms. M13-49 narrows the tools; the bullet list below carries its dated amendments.

*Amended 2026-09-29 (Wave 3 plan, proposed):* a wago.tools recording of a
large table may be an ID-filtered subset of whole verbatim lines, produced
byte for byte and recorded as such (§14.6).

`lab/core/tests/fixtures/README.md` is the index and states the rules:
every file has a provenance row, consent is recorded, a committed fixture is
never edited. Every capture passes through `scripts/lab_capture.py`, which

- replaces account folder names with stable pseudonyms (same input → same
  pseudonym within a capture set, so cross-file references still line up),
  and each `--extra-name` the owner gives (a real name, a guild name) the
  same way. *Amended 2026-10-17 (M13-49, ADR-0019 amendment):* character
  names, realm names and the second name of a Forever `<First>-<Second>`
  folder are no longer rewritten; they pass through as they are, in paths
  and in contents. The digits-only Forever group folder keeps its path-only
  numeric pseudonym. *Amended 2026-10-18 (M13-55):* the value of every
  `accountName` CVar in a `Config.wtf` or `config-cache.wtf` is rewritten
  the same way, as an account, wherever else it appears (one account per
  casing-folded value; an email address in it is left to the email
  refusal; a value under five letters that has a separator in it stops the
  capture). `accountList` is blanked but its entries are not yet hunted:
  its separator is unconfirmed **[verify]**,
- drops or blanks CVars known to carry identity (`accountName`,
  `accountList`, `lastCharacterGuid`, `portal` stays) per a deny-list kept in
  the tool and extended as found (`realmName` stays on it and is blanked),
- refuses to emit anything matching an email address, a BattleTag, or a
  `Player-<realm>-<hex>` GUID outside the pseudonym map. *Amended
  2026-10-17 (M13-49):* a character or realm name inside a longer word, in
  a SavedVariables file's own name, in an addon file, joined to an own
  realm, or in an account's character list no longer refuses anything; the
  same rules still refuse for the account folder and `--extra-name` strings.
  A home path that carries a login (the shape `wowlab doctor --redact`
  replaces, `/Users/<name>`, `C:\Users\<name>` and the like, and the
  capturing machine's own home) now refuses the file too. The own-realm
  partner checks and their notes, and the `Name-Realm-shaped string` note,
  are retired,
- with `--pseudonymise-other-players`, rewrites another player's GUID in a
  combat log, and the character name of the unit name beside it. *Amended
  2026-10-17 (M13-49):* the realm part of that unit name is left as written
  and is no longer hunted elsewhere in the log; the owner's own unit names
  keep their names, beside a rewritten own GUID,
- records what it rewrote in the provenance row (`identity-rewritten`,
  `cvars-dropped: n`). *Amended 2026-10-17 (M13-49):* `identity-rewritten`
  counts only rewrites of the kept set (account folders, `--extra-name`
  strings, and since 2026-10-18 (M13-55) `accountName` values); a
  path whose only rewrite is the account or group folder still
  reads `identity-rewritten: path only`. Committed fixtures and their rows
  keep their pseudonyms (ADR-0012).

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

> **Amendment 2026-10-16 (M13-34).** In `.build.info`, each row (one per
> installed product) has its own `Tags` cell, and each holds two country
> tokens: `geoip-<CC>?`, the country the client was in when it last updated,
> and `acct-<CCC>?`, the account's country. In every row, the tool replaces
> the geoip letters with `ZZ` and the account letters with `ZZZ` (ISO 3166
> user-assigned codes, so each token keeps its shape). It leaves the region
> token and the `Branch` column as they are. A token is placed by the
> header's `Tags` column, in rows with as many cells as the header, as a
> whole token bounded by the cell's edges, a space or a `:`. Any other
> `geoip` or `acct`, in any casing or shape and in any row, refuses the whole
> file with a count only. The provenance row records
> `geoip-country-replaced: N` and `acct-country-replaced: N`. The two
> `.build.info` fixtures committed before this rule keep their real tokens
> (owner decision, 2026-10-16).

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
- Asset formats (ADR-0033, L8's exception): `scripts/casc_facts.py`,
  read-only, writes a *derived* fixture per real file as JSON (file data
  id, size, chunk table, counts, section hashes, the decoded values a test
  asserts) with a provenance row in `lab/core/tests/fixtures/README.md`;
  the parsers are graded on those, on labelled constructed files, and on
  `@live` tests against the owner's install that CI never runs.
- `guard` is tested against a synthetic install tree built in `tmp_path`
  (our own format, so constructed input is legitimate) with a fake process
  probe injected. No test writes to a real install. No test needs a real
  install to be present.
- Windows path behaviour (case-insensitive collisions, reserved names, `\\?\`
  long paths, a locked file) is covered with `pytest.mark.skipif` per
  platform and at least one CI run on `windows-latest` for the `lab` suite,
  added in M10-01 as a non-required job.

  *Amended 2026-09-30 (owner decision):* the `windows-latest` job is removed.
  The only install is on macOS. The Windows branches in `guard`, `snapshot`
  and `process` stay in the code, and their `skipif` tests stay in the suite,
  but CI no longer runs them. Before the Lab is ever used against a Windows
  install, the job comes back first; it caught the byte-0 lock bug on PR #59.

  *Amended 2026-10-15 (ADR-0031, M13-02; owner decision):* the job is back.
  It ran on every PR as a required check from #173 until later the same day,
  when the owner took it off the PR path because every PR waited 15 to 30
  minutes on it. It now runs in `.github/workflows/windows.yml` after each
  merge to `main`, nightly, on demand for a branch, and (since 2026-10-17) on
  a pull request that touches `lab/**`, the lockfile, the root `pyproject.toml`,
  `conftest.py` or the workflow itself; it is not required (ADR-0014 amendments). The `skipif` tests run there, so a Windows regression
  is found on the PR when the PR touches those paths and after merge
  otherwise; the conductor checks the latest runs
  (`docs/AGENT_WORKFLOW.md`).

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

*Amended 2026-09-30:* the Wave 1 captures answered 1 and 2 in practice. The
primary install is on macOS, and the Forever beta client is installed; the
committed fixture roots are `macos` and `macos-<build>`. Question 3 was
answered by the owner on 2026-09-30: the Windows job is removed (§9
amendment).
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
  nothing), as a versioned table (`schema = 1`; schema 2 since M11-29,
  below):
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
    after an applied change (`BARBER_SHOP_APPEARANCE_APPLIED`; in the 70425
    capture a later `BARBER_SHOP_OPEN` replaced that record, amendment of
    2026-10-16 below),
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
  `collections.appearances`, `currencies`, `professions` (and, since schema
  3, `spellbook`, `actionbars`, `bindings`; since schema 4, `identity`). The list is
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

  Amended 2026-09-29 (M11-29, from the M11-23 capture on 1.60.1.70058,
  `docs/LAB_FORMATS.md`): schema 2. After one applied barber-shop change the
  customization record's last write came from an open
  (`recorded_at = "open"`), and it had no `chr_model_id`; the file could not
  say why. Confirmed by that capture: `BARBER_SHOP_OPEN` fires and reaches
  the section; `C_BarberShop.GetAvailableCustomizations`,
  `GetCurrentCharacterData` (`sex` 0) and `UnitRace` ran; no name field was
  stored. Still **[verify]**: that `BARBER_SHOP_APPEARANCE_APPLIED` ever
  reaches the section (the client did not refuse it, yet the record's last
  write came from an open), what `GetViewingChrModel` returns, and
  `currentChoiceIndex` being 1-based (consistent with the tables, not
  proven). The addon changes only so that the next capture can say why, not
  on a guess at the cause, and nothing changes about when the record is
  made. This is the first change to the format after M11-04, so, by the rule
  below, it is schema 2: `WowLabCharDB` and `WowLabDB` are written with
  `schema = 2`, and schema 2 is schema 1 plus two keys on `customization`.
  The reader reads schema 1 files (the M11-03 and M11-23 captures) exactly as
  before: it refuses `chr_model_id_absent` in them (text in an unknown key)
  and keeps `events_received` as an unknown key, as it always did. It
  refuses a schema it does not know. The addon reads the probe, the skip list and the
  carried customization record back by key, never by schema, so a schema-1
  file's `probe.loads` keeps rising across the first schema-2 write, and
  §13.4's loader check (which reads `WowLabCharDB.probe` by key) is
  unaffected. (1) The section writes `events_received`: every event it
  registered this session, with how many times it reached the section's
  handler (0 when it never did). With `recorded_at` it tells "the applied
  event never came" from "an open came after it". It goes on whatever record
  is written for the section, like `events_unregistered`, and is this
  session's: `carry` drops it. (2) The section also registers six
  count-only events, **[verify]** on Forever: `BARBER_SHOP_RESULT`,
  `BARBER_SHOP_CLOSE`, `BARBER_SHOP_FORCE_CUSTOMIZATIONS_UPDATE` and
  `BARBER_SHOP_COST_UPDATE` (Retail names), `BARBER_SHOP_SUCCESS` (the name
  before 9.0), and `WOWLAB_CONTROL_NOT_A_REAL_EVENT`, a made-up control
  whose entry, or its absence, shows whether this client refuses a name it
  does not know. They are counted and nothing else, and no event argument is
  read: the record still comes only from `BARBER_SHOP_OPEN` and
  `BARBER_SHOP_APPEARANCE_APPLIED`, never at close. A count-only event the
  client refuses has no entry and is not in `events_unregistered`, which
  stays the list of refused change events. (3) A gathered record holds
  exactly one of `chr_model_id` and `chr_model_id_absent`, one of
  `"C_BarberShop.GetViewingChrModel missing"`,
  `"C_BarberShop.GetViewingChrModel raised an error"`,
  `"C_BarberShop.GetViewingChrModel returned nil"` and
  `"C_BarberShop.GetViewingChrModel returned no number"`: which outcome the
  call had when the section gathered (the function is not on this client;
  it exists but refused this call; it named no model at that moment; it
  returned something else). None says why the client gave no id: that stays
  **[verify]**, and the reader keeps taking the body type from `sex`. A
  hypothesis only: the function names a model only while the shop shows an
  alternate form, and returns nil for the character's own form;
  forever-addon-kit's 69893 API list (`HasAlteredForm`,
  `IsViewingAlteredForm`, `SetViewingAlteredForm`, `SetViewingChrModel` in
  `C_BarberShop`) is consistent with it and does not prove it. The call
  goes through `pcall` directly, so an error is not taken for nil. `carry`
  keeps only the number, so a carried record may hold neither. Whether the
  applied event reaches the section, and why there was no model id, stays
  open until the owner's capture in `lab/addon/README.md` ("M11-29 capture
  step").

  Amended 2026-10-14 (M13-05, with M11-35 folded in; §15.2): schema 3.
  `WowLabCharDB` and `WowLabDB` are written with `schema = 3`, and schema 3
  is schema 2 plus one pair of keys on `talents.legacy` and three sections.
  The reader reads schema 1 and 2 files exactly as before and refuses a
  schema it does not know. (1) `talents.legacy` holds exactly one of
  `left_out_config`, the active class config id the addon left out of
  `configs` at that gather (a config with that id is never listed), and
  `left_out_config_absent`, why no id was left out
  (`C_ClassTalents.GetActiveConfigID` missing, raised an error, returned
  nil, or returned no number; called through `pcall` directly). (2)
  `spellbook`: the player's spellbook (the bank is the client's
  `Enum.SpellBookSpellBank.Player`), walked by skill line through
  `C_SpellBook`, future (not yet learned) spells included: per item its
  slot, skill line, raw item type, action id, spell id, `spell_name` and
  `subtext` (the subtext is where a rank shows, when the client gives one),
  and the passive and off-spec flags, plus `item_types`, the client's
  `Enum.SpellBookItemType` as a name-to-number map, so a reader names each
  type from the file; skill-line names are not read. Whether a hunter's
  named pet shows in a spell's name or subtext is **[verify]**. (3)
  `actionbars`: slots 1 to 180 (the addon's own bound: fifteen runs of
  twelve, the main bar's pages, the stance and bonus bars and the
  multi-action bars **[verify]**) through `GetActionInfo`, each filled
  slot with its type, its id when the id is a number, and its subtype. A
  macro slot keeps an id only, never the macro's name or body: with no
  subtype the id is the macro index **[verify]**, with one it is the spell
  or item id the macro shows now **[verify]**; `GetMacroInfo` is asked only for a macro
  index (an out-of-range index into a C API is the crash class in
  `docs/DATA_SOURCES.md`, 2026-09-28), only whether it names a macro (its
  count is kept, nothing else); an equipment-set slot, whose id is the set's
  name, keeps no id. (4) `bindings`: per binding command `GetBinding` lists
  that is a plain upper-case name, the keys `GetBindingKey` returns now (to
  cross-check `bindings-cache.wtf`), the raw `GetCurrentBindingSet` value,
  and the client's `ACCOUNT_BINDINGS` and `CHARACTER_BINDINGS` that name it
  **[verify]**; a command that is not a plain upper-case name (text after
  the name, as in `MACRO x`, or other characters; `MACRO <name>` holds text
  the owner typed) is counted, never asked about or stored. Whether
  `GetBinding` lists a key bound straight to a spell, macro, item or click
  at all is **[verify]**. The three new sections read only out of combat
  (ADR-0026, GLOSSARY "Secret value"): each gather asks `InCombatLockdown`
  first and reads nothing when the player is in combat or the client cannot
  say. Such a gather is blocked: the section keeps what it gathered earlier
  in the session, stays dirty, and is written absent with the reason only
  when nothing was gathered for it in that session; `PLAYER_REGEN_ENABLED`
  is each section's retry event, re-gathering it only while it is still
  dirty. Values are read only when `InCombatLockdown` says not in combat;
  whether these values can be secret outside combat in restricted content
  is **[verify]**. Where the client has `issecretvalue` (the name is
  **[verify]**), every value is tested before it is used or stored, and one
  it calls secret (or cannot say about) is dropped and counted. No
  `issecretvalue` means values are not tested, not that none are secret; a
  secret value then makes the gather raise and the section is written as an
  error, unless it was gathered earlier in the session: a section with a
  retry event keeps that record through an error as through a block, and
  stays dirty. `C_Secrets` and `canaccessvalue` (**[verify]**) are looked up,
  never called; their probes say only whether the client has them.
  forever-addon-kit lists the other names for 69893 and presence proves
  nothing, so each of the three records `probes`: per API it asks, whether
  the function was there and how many calls were made, raised an error and
  answered, and how many of its values were dropped as secret (the way
  `spec.api` names the API that answered). Every new call sits in a section
  `/wowlab skip` switches off (section keys `spellbook`, `actionbars`,
  `bindings`), and every new API is **[verify]** until the owner's schema-3
  capture (`docs/handoffs/M13-05.md`).

  Amended 2026-10-16 (M13-05 and M11-29, from the owner's capture on
  1.60.1.70425, fixtures under `lab/core/tests/fixtures/macos-70425/`;
  `docs/LAB_FORMATS.md`, amendment of that date). The reader reads it as
  schema 3 with no unknown key, and the schema-1 captures read as before.

  Confirmed on that build:
  - every API the three sections ask is present and ran with no error and
    no secret value, out of combat;
  - a global `issecretvalue` function was present (the kit's 69893 list
    lacks it) and answered every call. The probe cannot tell the client's
    function from an addon's, so where it comes from is **[verify]**.
    `C_Secrets` and `canaccessvalue` were present too;
  - ranks show as `Rank n` subtexts, one spellbook item per rank, each with
    its own spell id, and there is no "show all ranks" toggle;
  - a flyout has an action id and no spell id;
  - every action slot up to 180 answers nothing or a value, never an error;
  - the eight lines of `bindings-cache.wtf` match the live `GetBindingKey`
    keys. The other 118 bound commands are unexplained;
  - `left_out_config` is the active class config;
  - the new sections' change events are names the client knows;
  - no macro name or body is stored.

  Contradicted:
  - a `#showtooltip` macro slot has no subtype, so its id is not the spell's;
  - `ACCOUNT_BINDINGS` and `CHARACTER_BINDINGS` were not numbers at the
    last gather (nil or another type; the file cannot say which). So
    `binding_set` (2 with character bindings ticked) is not named from the
    file. `Enum.BindingSet` is a **[verify]** candidate for a later schema.

  Still **[verify]**:
  - how macro indices are numbered;
  - whether `GetBinding` lists a spell dragged onto a key;
  - whether the client rewrites `macros-cache.txt` and `bindings-cache.wtf`
    at logout on Forever. The owner's `/say` macro and the dragged spell
    are in neither, so the caches may predate the session;
  - future spells, a hunter's named pet, combat, restricted content and
    hitches.

  For `customization`, `BARBER_SHOP_APPEARANCE_APPLIED` fires and reaches
  the section. With one sit, though, a second `BARBER_SHOP_OPEN` reached it
  after the Accept, and that open's record, holding the new look, replaced
  the applied one. Whether the shop actually reopens, and why, is
  **[verify]**: the owner saw it close. So in this capture the applied
  change ended as `recorded_at = "open"` (M11-29's "reason it cannot").
  `currentChoiceIndex` is consistent with 1-based: the 70058 record's index
  7, for an option with 7 choices, could not come from a 0-based client.
  That the client's list has the table's 7 choices is **[verify]**.
  `GetViewingChrModel` returned nil. M11-29's other question, why, stays
  **[verify]**. The format does not change: the next change is still
  schema 4.

  Schema 1 may change after M11-03. Edits from the capture land before
  M11-04 starts; after M11-04 merges, any change is a new schema (schema 2
  is M11-29's and schema 3 is M13-05's, above; the next change is schema 4).

  Amended 2026-10-19 (M15-01; §16.3's amendment of the same date): schema 4.
  `WowLabCharDB` and `WowLabDB` are written with `schema = 4`, and schema 4
  is schema 3 plus one section, `identity` (`lab/addon/WowLab/Identity.lua`):
  `race_id` (the third return of `UnitRace("player")`), `sex_raw` (the first
  return of `UnitSex("player")` as the client gives it), `class_id` and
  `class_file` (the third and second returns of `UnitClass("player")`), each
  exactly one of the value and `<field>_absent`, a reason. It is gathered on
  entering the world and again on `BARBER_SHOP_APPEARANCE_APPLIED` (the
  event's firing is confirmed by the 70425 capture; that `UnitSex` already
  gives the new body type at that gather is **[verify]**, not tested by the
  M15-01 capture), and can be switched off like any section. The reader derives `sex`, the barber
  shop's `Enum.UnitSex` value, from `sex_raw` (2 to 0, 3 to 1, anything else
  absent with the reason "UnitSex returned <n>"), since
  `C_BarberShop.GetCurrentCharacterData`, which `customization` reads,
  is called only in the barber shop (how it answers outside is untested). All three APIs ran in the
  owner's 70450 captures (`docs/handoffs/M15-01.md`, fixture root
  `macos-70450/`, `docs/LAB_FORMATS.md` amendment of 2026-10-19): `UnitSex`
  2 maps to `Enum.UnitSex` 0 there; `UnitSex` 3 and 1 stay **[verify]**. The reader reads schema 1 to
  3 files exactly as before, with `identity` absent; the next change is
  schema 5. `CharShowReport` (after `record`) and `CharGearReport` (after
  `spec`) gain one field each, `identity` (`labaddon.CharIdentity`:
  `race_id`, `sex`, `sex_raw`, `class_id`, `class_file`, each with its
  `_absent` reason), and `char show` prints one `Identity:` line after the
  spec for a schema-4 record; nothing else in either command's output
  changes.
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
  *Amended 2026-09-29 (M11-32):* the line under the Legacy headline says
  how the addon found the configs: "the addon lists every trait config it
  found by type (except the types below) or by a client system id, less the
  active class talents when the client gave their id; which of these is the
  Legacy system is not recorded". This replaces both the M11-27 line and
  the ticket's proposed "except the active class talents and the types
  below" (changed in review on the conductor's authority), because the
  addon does less than that wording claims. It leaves the active class
  config out by id, whatever its type, but only when
  `C_ClassTalents.GetActiveConfigID` gave an id. `talents.legacy` does not
  record the id it left out (schema 3 does: the M13-05 amendment above). `talents.class` records the active class
  config as of its own gather, which may be a different moment, so a class
  config missing from the list proves nothing; one whose id is in the list
  shows the addon did not leave it out, a check the reader does not make
  yet. A config
  found through `C_Traits.GetConfigIDBySystemID` is not type-checked, so
  it may have one of the types the next line says were not searched. The
  line is used whenever `talents.legacy.configs` lists more than one
  config, including when every one is absent with a reason. With one
  config, the line still says the Legacy system is inferred by
  elimination; with none, it says "no candidate config listed". Every
  variant ends with the panel opener clause as computed.
  *Amended 2026-09-30 (M11-34):* two cases no capture has shown. (1) With
  one config listed, the reader compares its id with the one
  `talents.class` records (its config's id, present or absent with a
  reason). This is the id check the M11-32 amendment deferred, made for the
  one-config case only; the reader makes it in one place, and the headline
  and the line under it both use the answer. An id is compared only with an
  id: a missing id on either side is never a match. On a match, the M11-27
  one-config
  headline, which gave the lone listed config's figures as the candidate's,
  no longer applies: the figures are the class talents', so the headline
  reads "Legacy candidates: only the active class config listed (config N;
  its figures are under Class talents)" ("Class talents shows it absent with
  a reason" when that section's config is absent), with the level after it
  when the file records one. The line reads "the one config listed is the
  active class config (config N, as Class talents records it), which the
  addon did not leave out, so no config is inferred to be the Legacy
  system". When `talents.class` records no config id (not in the file,
  absent with a reason, or its config absent without an id), or the listed
  config has none, the M11-27 headline stays and only the line under it is
  qualified: "inferred by elimination" gains ", but Class talents records
  no config id, so the config listed may be the active class config" (or
  ", but the config listed has no id, so it may be the active class
  config") " (the addon leaves that out only when the client gave its
  id)". The several-config, no-config and different-id lines are
  unchanged: with a different id, the M11-27 headline and "inferred by
  elimination" stay. (2) The addon leaves `skipped_types` empty when it did
  not search by type at all (`C_Traits.GetConfigsByType` or
  `Enum.TraitConfigType` missing), and also when the search ran and the
  client's enum lists none of Invalid, Combat and Profession. A config
  found by type shows the search ran, and the line keeps "config types not
  searched: none recorded". Without one, it reads "none recorded, and no
  config was found by type, so the type search may not have run (the addon
  records none when C_Traits.GetConfigsByType or Enum.TraitConfigType is
  missing, or when the enum has none of Invalid, Combat and Profession)".
  No line these cases add is longer than the longest `char show` printed
  before (265 characters, the M11-32 several-config line). Every committed
  capture lists one config, found by type, that is not the class config,
  so its output is unchanged.
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
removed or added): the rest of the apply goes ahead. *Note, 2026-10-17
(M13-28):* under `Interface/AddOns/`, an executable added since the save is
skipped by its own file-map row (`addon-executable`, Edit `no`) and listed
under "skipped" with action `delete`, before the gate is asked; a changed or
removed one is still listed as left alone with the gate's reason. The
sentence above still holds for executables outside `AddOns/`. When the gate refuses
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
    - *Amended 2026-09-30 (M11-31, graded by M11-31T, with the conductor
      ruling on M11-31T of 2026-09-29):* `snap gc` keeps a journal-named
      object only when it is the `after` of a committed write to a
      character's `WowLab.lua`: every such write, not only the latest,
      since the check compares any character's file and walks back over
      older spans. The kept set is decided from the journal record's
      `path` alone (gc takes no install and reads none) by one rule: seven
      parts, `WTF/Account/<account>/<realm or digits>/<character>/SavedVariables/WowLab.lua`,
      with `WTF`, `Account`, `SavedVariables` and `WowLab.lua` compared
      case-folded, since the journal records the spelling on disk, and the
      path split no further than seven separators. That set is a superset
      of what the check's `_character_sv` can return for a character's own
      lab file, which is the safe direction: it also keeps the `after` of
      a few writes the check never reads from (to a path whose realm or
      character folder is named `SavedVariables`, or that has an empty or
      `..` part, and a write whose `before` equals its `after`), which
      costs only space. Every
      other `after` (the account-wide `WowLab.lua`, a `WowLab.lua.bak`,
      any other file a `snap restore` or profile apply wrote from a
      snapshot since deleted) is collected like any object no snapshot
      refers to, through the same link-safe removal (§6.9, M11-15).
      `snap gc --help` says what is kept. `put_object` raises any
      `OSError` on the way (a directory planted at the object's path, for
      one) as `SnapshotError`, naming the object's path and what is in its
      place, so `sv merge` stops with "cannot keep the merged … in the
      snapshot store (…); nothing was written".
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

## 14. Wave 3 (M12): db2lake, char-planner (talents first), alt-dashboard

Owner pick, 2026-09-29 (ADR-0024): **db2lake**, **char-planner with talents
first**, and **alt-dashboard**. Decision: ADR-0028 (Accepted by the owner, 2026-09-30). Every other
choice below is the conductor's proposal, with a default, and §14.5 lists the
ones the owner may want to overrule when reviewing this PR. L1–L8 apply to
every ticket. Nothing here writes into an install: all three are data-only,
like the customization sandbox (§13.2).

### 14.1 What the tables and the captures show (2026-09-29)

Checked by hand with `wowlab db2 head` (one request per table, the polite
client, nothing from a test) against build 1.60.1.70058, which is the owner's
installed build, is listed by wago.tools, and is the build of the
`macos-70058` capture. The tables went into the owner's cache, not into
fixtures.

- **The talent tables exist and are small.** `TraitTree` 17 rows (533 bytes),
  `TraitNode` 558 rows (15,719), `TraitNodeEntry` 655 (13,154),
  `TraitDefinition` 654 (14,907), `TraitNodeXTraitNodeEntry` 561 (14,046),
  `TraitEdge` 96 (2,453), `TraitCond` 172 (8,965), `TraitCurrency` (147),
  `TraitTreeXTraitCurrency` (259). Together under 100 KB, so a whole
  talent dataset fits the repository's 512 KB file limit many times over.
- **Names need spell tables.** `TraitDefinition.SpellID` names the spell;
  `SpellName` (`ID`, `Name_lang`) has 31,716 rows, 832,295 bytes (254,495
  gzipped). `SpellMisc` is 3.0 MB. `CurrencyTypes`, `SkillLine` and
  `ChrSpecialization` exist at this build (the dashboard's name lookups).
- **Some tables the retail client has are not there.** `TraitSubTree`,
  `TraitTreeLoadout`, `TraitTreeLoadoutEntry` and a made-up table name all
  answered `HTTP 400` at this listed build. `gamedata` reports the bare URL
  and `HTTP 400` (M12-02). A table is not "not found" (404) here; the answer
  for an unknown table is 400.
- **Forever has 17 trees.** `TraitSystemID` is 10 for nine trees (1066, 1083,
  1091, 1100, 1111, 1112, 1114, 1116, 1117), 45 for four (1118, 1187, 1188,
  1189) and 0 for four (1058, 1081, 1082, 1089). What each system id means
  is **[verify]** except where a capture says so: the `macos-70058`
  capture's class config (type 4) has one tree, 1116, system 10,
  52 nodes, one node with an active rank, tree currency 3820 (quantity 0,
  maximum 4, spent 4) and a 36-character export string; its one Legacy
  candidate (type 3, found by type) has four trees, 1118 with
  no nodes and 1187 to 1189 with nine each, all at rank 0, all system 45.
  Those four are exactly the four system-45 trees in `TraitTree`, and 1116 is
  in the table as system 10, so the addon's records join to wago's tables.
  Whether system 45 is *the* Legacy system stays **[verify]** (the addon does
  not record which candidate is Legacy). `TraitCurrency.SourcedMax` for 3820
  is 51; what that number caps is **[verify]**.
- **The unnamed columns are a WoWDBDefs gap.** `TraitTree` carries
  `Field_10_0_0_45697_006` and `_007`: wago names what WoWDBDefs knows and
  numbers the rest (`docs/DATA_SOURCES.md`). Types are not in the CSV at all.
- **`looks` has its own coercion.** `wowlab_core.looks` turns cell text into
  numbers row by row (`_Row.number`). db2lake's typed rows are what a second
  consumer should use; moving `looks` onto them is not part of this wave.

*Amended 2026-09-30 (M12-02):* `gamedata` no longer reports the bare URL
and `HTTP 400`. A `400` from the table endpoint is `TableNotPublished` when
the builds listing lists the build, with a message that names the status and
the URL and says the table may not exist in this game version, and
`BuildNotPublished` when it does not, as a `404` is. The status alone
decides (the body is not read); a `400` is never retried and never cached.
The committed listing that lists 1.60.1.70058 is
`wago/builds.2026-09-30.json.gz`.

### 14.2 db2lake

Decision: ADR-0028. The design, so tickets can be graded:

- **Module** `wowlab_core.db2lake`, new; `gamedata` stays the only network
  client. A `Lake` is opened for one full build string and reads tables
  through `GameData.table` (so L5 and the cache location rules already hold).
- **Database.** One SQLite file per build,
  `<user data>/wowlab/db2lake/<build>.sqlite`, refused inside an install (the
  same check `gamedata` makes). A table is loaded on demand, in one
  transaction, from its cached CSV; `_lake_tables` records the table name,
  the CSV's SHA-256 (from the sidecar) and the row count. A loaded table is
  never replaced; a second load is a no-op; a source whose hash differs is an
  error that cites L5. The file is derived: deleting it loses nothing.
- **Types.** Inferred per column as ADR-0028 says (canonical integer, other
  finite number, else text; an empty cell is NULL in a numeric column and the
  empty string in a text column). Names are wago's, unchanged, placeholder
  names included. A header with a repeated name is `MalformedTable`.
- **Rows API.** `Lake.rows(name)` yields dicts of `int | float | str | None`;
  `Lake.schema(name)` gives columns, inferred types and row count. This is
  the read path the talent model uses.
- **Cross-build.** `Lake.attach(build)` attaches another build's file
  read-only under the alias `b<build digits>`; a join across builds is plain
  SQL. The schema output shows each column's type so a build-to-build type
  change is visible.
- **CLI** `wowlab db2 tables [--build V]` (tables the cache holds and whether
  each is loaded), `db2 schema TABLE`, `db2 sql "SELECT …" [--build V]
  [--attach V …] [--max-rows N] [--timeout S] [--offline] [--json]`, and
  `db2 repl` (reads statements from a terminal or a pipe). A table the query
  names but the lake lacks is fetched and loaded once, unless `--offline`,
  and the output says so; the name is checked against `gamedata`'s key rule
  before any request.
- **SQL safety** (the part that needs adversarial review): one statement,
  which must begin with `SELECT`, `WITH` or `VALUES` after comments (so
  `VACUUM INTO 'file'`, which writes a file even from a read-only
  connection, `PRAGMA`, `ATTACH`, `CREATE`, `INSERT` and the rest are
  refused by name); a read-only connection; an authorizer as the second
  layer; extension loading off; `temp_store` in memory; an operation budget
  and a wall-clock deadline (a progress handler); a row cap with truncation
  flagged. Text output goes through the CLI's escaping (M11-30), since game
  strings carry client markup and can carry control characters.

*Amended 2026-09-29:* §6.6's "column typing arrives with whichever wave needs
it (it requires WoWDBDefs)" is replaced, if ADR-0028 is accepted, by this
section: Wave 3 types by inference; WoWDBDefs waits for foreign keys.

*Amended 2026-09-30 (M12-03, what the code settled; ADR-0028 unchanged):*

- **SQLite.** The standard library's `sqlite3`, version 3.37 or later
  (checked on first connection; the bundled one here is 3.49.1), because the
  tables are `STRICT`: a value of the wrong type is an error, so SQLite is a
  second line behind the inference, never a silently mixed column. Also used:
  URI filenames (`mode=ro` for an attached build), the table-valued
  `pragma_table_info` for `schema`, and Python 3.12's `Connection.setconfig`.
  At most 10 builds attach at once (SQLite's default limit).
- **The connection** (security review, 2026-09-30). `query_only` alone does
  not stop a statement opening a file: `ATTACH` creates its file and
  `VACUUM INTO` creates its target before failing. So the lake's connection
  has an authorizer that refuses every `ATTACH` (which is also how
  `VACUUM INTO` opens its file), `DETACH` and `PRAGMA` except the statement
  the module itself is issuing at that moment (`Lake.attach`'s own
  `ATTACH`, `_writing`'s `query_only`), with `table_info` open to all; the
  statement cache is off, so every execution is authorized afresh.
  `query_only` stays on outside a write, as a second layer. A lake file may
  have been planted: triggers and views are switched off on the connection,
  the schema is untrusted (`TRUSTED_SCHEMA` off), `DEFENSIVE` is on and the
  two-argument `fts3_tokenizer` is off. SQLite writes a database and its
  journal in place, so the file (resolved once, and connected to by that
  resolved path) is refused when it has a second hard link, and so is a
  `-journal`, `-wal` or `-shm` beside it that is a hard link or a symlink, at
  connection, before every operation (a read can roll back a hot journal)
  and before every write; an attach target likewise. A progress handler runs
  every 10,000 SQLite steps: Ctrl-C stops any statement (`KeyboardInterrupt`),
  and `Lake.query` stops at a deadline (300 s by default, wall clock from the
  call) or an operation budget (`QueryStopped`).
- **A file the lake did not write** is refused and never written into: an
  empty file gets the metadata tables; any other must hold exactly the two
  metadata tables as this module creates them (their `CREATE` text is
  compared), one `STRICT` table per `_lake_tables` row, and nothing else (no
  view, trigger, index or other table). The messages say what is known:
  damaged or not a database (delete it, it is derived), busy (another process
  is writing it), or refused (the authorizer), naming the file concerned.
- **The rule as coded.** An INTEGER cell is `-?(0|[1-9][0-9]*)` in ASCII
  digits, within signed 64 bits. `-0` fits the ADR's words (an optional minus,
  then `0`) and reads as `0`. "Another finite decimal number" is the same
  integer part with an optional `.digits` fraction, finite as a double:
  decimal notation only, which is what wago writes in every recording
  (fixed-point, up to 11 decimals, trailing zeros stripped). So `007`, `00.5`,
  `+1`, a space, `.5`, `5.`, `1e5`, `inf`, `1_000` and non-ASCII digits are
  text, and one such cell makes its column TEXT, where the numbers keep their
  text. An integer outside 64 bits is a finite decimal number, so its column
  is REAL and the value is the nearest double (exact to 2^53). SQLite stores
  `-0.0` in a REAL column as `0.0`. A column is typed over every row of the
  build's table.
- **An all-empty column** is TEXT and keeps its empty strings: the owner's
  ruling of 2026-09-30 (recorded against ADR-0028 in `docs/DECISIONS.md`),
  where the ADR's words read literally gave INTEGER with NULLs. In the
  recordings there are 20 such columns in 9 recordings of 6 tables, every one
  a `_lang` or other string column (`TraitTree.TitleText_lang`,
  `TraitDefinition.OverrideSubtext_lang` and `OverrideDescription_lang`,
  ChrClasses, ChrRaces, and the ChrSpecialization and SkillLine subsets). A
  column with any non-empty cell is typed as before:
  `TraitDefinition.OverrideName_lang` holds one cell, `16972`, beside 653
  empty ones, so it is INTEGER and its empty cells are NULL (the ADR's
  Consequences).
- **Metadata.** `_lake_tables (name, sha256, row_count)`; `name` compares
  without ASCII case, as SQLite's names do, so `traittree` after `TraitTree`
  is refused, not fetched. `_lake_meta` records the format (1) and the build;
  a file recording another build is refused, opened or attached. Table names
  beginning `_lake_` or `sqlite_` are refused, as is any name `gamedata`'s key
  rule refuses, before anything is asked.
- **The hash.** The SHA-256 recorded is of the bytes loaded, hashed as they
  are read. A cached CSV whose sidecar gives another SHA-256 is not loaded
  (`SourceChanged`, citing L5). `load` on a loaded table hashes the cached CSV
  again: the same, a no-op; different, `SourceChanged`. `rows` and `schema`
  compare once per `Lake` object. If the cache no longer holds the CSV, the
  lake keeps the table and nothing is refetched.
- **Batches and bounds.** Two streaming passes: the first infers the types
  and hashes; the second, inside one `BEGIN IMMEDIATE` transaction, inserts in
  batches and hashes again, and a file that changed between them is refused
  and rolled back. A batch is cut at 5,000 rows (`BATCH_ROWS`), 50,000 cells
  (so a 2,000-column table takes 25 rows) or 4 MiB of CSV, whichever comes
  first. One record (all its physical lines) may be at most 1 MiB
  (`MAX_RECORD_BYTES`), refused before it is decoded; the longest record in
  the recordings is under 2 KB. A header wider than SQLite's column limit
  (2,000) is `MalformedTable` before any per-name check, and repeated names
  are found in linear time. Measured on the owner's M1 (shared with other
  work): a constructed 200,000-row, 10-column CSV (13.3 MB) loads in 1.2 to
  1.8 CPU s; Python's peak allocation during a load is about 2.4 MB, the same
  for a 2.5 MB and a 13.3 MB file, about 0.7 MB for a 2,000-column table and
  about 4.4 MB for 20 KB rows, whatever the row count (SQLite's page cache,
  2 MB by default, is apart).
- **Rows** come back in CSV order: ordered by the row id, under whichever of
  `rowid`, `_rowid_` and `oid` no column shadows. `Lake.query(sql,
  parameters)` runs SQL the library writes, on the connection that holds the
  attachments, under the authorizer, `query_only` and a budget; it is not the
  owner's SQL surface, which M12-04 builds with its own guards.
- **The file** is created by `load`, `rows`, `schema`, `attach` and `query`
  (holding only the metadata until a table is loaded); `tables` never
  creates it.

### 14.3 char-planner, talents first

Class talents only. Legacy talents wait for a level-25+ capture (the owner's
level-25+ characters come after the beta, decision of 2026-09-29); gear, item
levels and SimulationCraft export are a later wave. A plan is data: nothing here reaches the game, and the client keeps
talents on the server, so the owner applies a plan in the game (a loadout
string, once encoding is possible, §14.5 D6).

- **Tables.** `TraitTree` (trees and systems) → `TraitNode` (`TraitTreeID`,
  `PosX`, `PosY`, `Type`, `Flags`) → `TraitNodeXTraitNodeEntry` (`TraitNodeID`,
  `TraitNodeEntryID`, `_Index`) → `TraitNodeEntry` (`TraitDefinitionID`,
  `MaxRanks`, `NodeEntryType`) → `TraitDefinition` (`SpellID`, the
  `Override*_lang` texts) → `SpellName` (`Name_lang`); `TraitEdge`
  (`LeftTraitNodeID`, `RightTraitNodeID`, `Type`); `TraitCond` (`CondType`,
  `TraitTreeID`, `SpentAmountRequired`, `TraitNodeGroupID`, `TraitCurrencyID`,
  `RequiredLevel`, …); `TraitCurrency` and `TraitTreeXTraitCurrency` (the
  point pools). The meaning of every enum column above is community
  documentation and **[verify]**. A node with several entries is a choice
  node (entry type **[verify]**).
- **Which class a tree belongs to** is not a `TraitTree` column in the
  recording. The model labels a tree by id, system and its root nodes' names,
  and by class only where the capture says so (`spec.id` →
  `ChrSpecialization.ClassID`, both real: 1490 in the capture). A tree to
  class map is **[verify]** and is found in M12-05 or left out.
- **Model** `wowlab_core.talents`, new: `TalentTree`, `Node`, `Entry`,
  `Edge`, `Plan` (a name, a tree, the build it was checked against, and per
  node a rank and the chosen entry) and `check(plan)`, on the `looks`
  principle (§13.2): **refuse only what the tables decide** (an unknown node
  or entry, a rank above the entry's `MaxRanks`, an entry that is not on the
  node, more points spent than the tree currency's `SourcedMax` allows);
  **note the rest** (a node with no purchased neighbour along an edge, an
  unmet `TraitCond` gate, a `RequiredLevel`, more points than the character
  currently holds), each note saying what is unverified. An imported plan
  with an id the recorded build lacks is "unknown to build V (possibly a
  hotfix)", not refused.
- **Import from a character.** `import-char` reads `talents.class` through
  the M11-04 reader: the tree, and per node `active_rank` (the committed
  rank; `ranks_purchased` and `current_rank` may include staged changes
  **[verify]**) and `active_entry`. A character whose class talents are
  absent gets the addon's reason (exit 1), as `looks import-char` does.
- **Loadout string.** `wowlab_core.talentstring` decodes the client's export
  string: the header (a serialization version, the spec id, a tree hash) and
  one field group per node in tree order, **[verify]** the layout on Forever.
  It is graded on the strings in the real captures, comparing the decoded
  ranks with the ranks recorded beside the string in the same file. The
  capture's string is 36 characters (216 bits); 8 + 16 + 128 header bits and
  52 node bits is 204, which fits and is a hypothesis to test, not a
  finding. Encoding needs the tree hash, which the Lab cannot compute; it
  waits for a capture that shows how (§14.5 D6).
- **CLI** `wowlab talents trees | tree ID | import-char NAME | save NAME … |
  show [NAME] | compare A B | decode STRING | page`, mirroring `looks`: plans
  are `<user data>/wowlab/talents/<name>.json` (`SavedPlan`, format 1, the
  build and the plan), names follow the profile rule, `save` refuses a taken
  name unless `--replace`, every data command takes `--json`.
- **Page** `wowlab talents page [--out PATH]`: one static HTML file
  (ADR-0027). Version 1 is read-only (§14.5 D3): the tree drawn from
  `PosX`/`PosY` and the edges, the character's recorded build and saved plans
  overlaid, a tooltip per node from the tables. Planning is done with `save
  --rank NODE=RANK`, so every rule stays in graded Python.

### 14.4 alt-dashboard

A static local page over every character's `WowLab.lua`.

- **Reader.** `labaddon.read_all` (M12-09) walks every character folder the
  layout finds (the digits folder shape and the retail-style twin, both), reads
  each `WowLab.lua`, and returns per character its label (the folder name),
  the file's modification time, and either the parsed record or the reason
  it could not be read. One unreadable file names itself and exits 1 (as
  `looks show` does) and never blocks the others. `wowlab char list` prints
  the table.
  *Amended 2026-09-30 (M12-09), the reader as built:* `labaddon.survey(layout,
  account=None, keep_records=True, budget=MAX_SURVEY_BYTES)` makes one
  walk of `WTF/` (`Layout.wtf_walk`, which also names the folders, links,
  read errors and non-regular entries the typed lists leave out) and
  returns `AllCharacters`: one `CharacterFile` (`label`, `account`,
  `realm_folder`, `shape`, `file`, `place`, `mtime_ns`, and either
  `summary`, with `record` unless the caller asked not to keep it, or
  `error`) per character folder, of either shape, whose `SavedVariables/`
  holds a `WowLab.lua`, and `not_looked_at` (`path`, `reason`, and the
  `account` it is in). `read_all` is `survey(...).characters`. A row is a
  character folder, not proof of a character: a renamed or transferred
  character (and a deleted one **[verify]**, `docs/LAB_FILE_MAP.md`) keeps
  its old folder and last save (GLOSSARY, Identity). `shape` says whether
  the folder above it is a realm name (`realm_name`) or, on Forever, a
  digits folder (`numeric_folder`), which is not a realm name. A folder
  without the file (the `<Realm>/<First>/` twin, a character never saved
  with the addon enabled; a crash writes nothing) is not listed, and
  `.lua.bak` siblings are never read. The rule for which file is the
  character's is `labaddon.choose_lab_file`: the whole name `WowLab.lua`
  exactly, else its one case variant; two with the exact name, or several
  case variants and no exact one, are refused. `char show` and `looks
  import-char` choose by the same rule (they used to take the first such
  file in path order, which changed only for case variants), but they give
  it regular files only, while `survey` also gives it a link, FIFO or
  folder of that name and names a `SavedVariables/` folder it cannot list.
  So there they can still answer differently from `char list`: the client
  opens the exact name and follows links; on a case-sensitive volume (under
  Wine, say) `char show` can read a case variant the client does not; and
  where a character's `SavedVariables/` cannot be listed, `char show
  --character` says the character has no `WowLab.lua` and gives install
  advice (exit 1) and `looks import-char` exits 2, while `char list` says it
  could not look inside that folder (a follow-up makes `char show` take its
  entry from `survey`). **Whatever wowlab could not look
  at is named and makes `char list` exit 1:** a `WowLab.lua` it cannot read
  (the reader's, the parser's or the system's reason, never an absolute
  path), or that is a link (not followed, as wowlab never follows links), a
  FIFO or other non-regular entry (never opened) or a folder; and a
  character folder or its `SavedVariables/` that is a link or cannot be
  listed (`place` says which). Each gives that character an entry with the
  reason (`mtime_ns` None when no regular file was looked at). The flavor
  folder itself when it cannot be listed (named by its own name, so `WTF/`
  is never found), a link or unlistable folder at `WTF/`, `WTF/Account/`,
  an account folder or a realm or digits folder, and a walk that hit its
  bound are in `not_looked_at`. A linked name directly in an account folder
  counts only where the file map would take it for a realm or digits
  folder (a linked `config-cache.wtf` hides nothing), and nothing in the
  account's own `SavedVariables/` counts (a linked `WeakAuras.lua` shared
  between accounts hides no character). A FIFO or folder counts only where
  it carries the file's own name. A file over the per-file bound
  (`luadata.MAX_FILE_BYTES`) is refused on that bound before a byte is read
  ("the file holds N bytes, more than M") and never charged to the total,
  so it blocks nothing after it. One run reads at most `MAX_SURVEY_BYTES`
  (64 MiB) of files in all, and one byte more when a file grows while it is
  read (the read's limit is what is left, and that byte is how the growth
  is noticed); each file is charged the bytes actually read. The bound
  counts bytes, not parse cost: a dense file takes longer and keeps more
  memory per byte than a real one (charging parse cost is a follow-up).
  A file by itself larger than the whole total bound (but under the
  per-file one) is likewise refused alone before a byte is read, never
  charged, and blocks nothing after it: ``not read: the file holds N bytes,
  more than the listing's total size bound (wowlab reads at most 64 MiB of
  WowLab.lua files in one listing); `<command>` reads this one on its
  own`` (round 4 of the review, 2026-10-14). Once a file that fits the
  total would pass what is left, that file and every one after it in the
  order are ``not read: the listing's total size bound was reached (wowlab
  reads at most 64 MiB of WowLab.lua files in one listing, and the files
  before this one used it up); `<command>` reads this one on its own``. A
  file that grows past what is left as it is read stops the run the same
  way (its read may have cost what was left), its own row saying `and this
  file grew past what was left as wowlab read it`. *Amended 2026-10-14
  (round 5 of the review):* `<command>` is `wowlab char show [--root
  <root>] --flavor <flavor folder> --account <A> --character <realm or
  digits folder>/<character folder>`, every argument shell-quoted
  (`shlex.join`), so a realm folder such as `Argent Dawn` or `Kel'Thuzad`,
  or an account folder holding `#`, pastes as one argument: for example
  ``--account '90000001#6' --character 'Kel'"'"'Thuzad Argent
  Dawn/Labcharq'``. `--root` is repeated only when `char list` was given
  `--root`; otherwise `WOWLAB_WOW_ROOT` or discovery finds the install
  again, and no absolute path is printed. Like every line of text output,
  the row goes through the CLI's escaping (M11-30). A file
  already read and parsed in the run (the same device and inode, and an
  inode other than 0, which some file systems report for every file) is not
  read again: its row is `the same file as <realm or digits
  folder>/<character folder> [in account <A>] (a hard link), read once, on
  that character's row` (`same_as` in `--json`), and it is an error (exit
  1), since the row cannot vouch that the record is this character's. A
  link to a file that could not be read or parsed is read on its own and
  reports its own error. Text output keeps each file's summary, not its
  parsed record. The order is account, realm or
  digits folder, character folder, each compared with case folded and then
  as spelled; it never follows modification times. `char list` covers
  every account unless `--account` names one (as `sv list` does; `char
  show` instead needs `--account` when there are several); `--account A`
  where A is, or is behind, a place wowlab could not look inside names that
  place and exits 1 instead of "no account folder" (exit 2). Deciding that
  takes the listing only (`survey(budget=0)` opens no file), and the usage
  error for an account found nowhere lists the "accounts wowlab could look
  inside" (every command that takes `--account`). The heading
  counts character folders (and those wowlab could not look inside); a row
  is `<realm or digits folder>/<character folder>`, the file's modification
  time (the client's last save, unless something wrote the file since: a
  wowlab snap restore, undo or sv merge, or a copy; profiles never hold
  `WowLab.lua`; "time unknown" when none was read), and the schema, the
  client that saved it as its full version (`saved by client
  1.60.1.70009`, which need not be the installed build) and the spec id
  (`labaddon.summary`), or `not read:` and the reason. On stderr, one line
  each: `could not read <file>: <reason>`, `could not look inside <folder>
  (the character folder | the character's SavedVariables folder):
  <reason>`, `<file> is the same file as … (a hard link), read once, on
  that character's row`, and `could not look inside <place>: <reason>`; the
  command exits 1 after
  printing everything, `--json` (`CharListReport`, with `not_looked_at`)
  included. The text then says "wowlab could not look inside N place(s)
  where character folders can be (named on stderr); any character folder
  inside it (them) is not listed", and with nothing listed it says so
  instead of giving install advice.
- **Page** `wowlab char page [--out PATH] [--anonymize] [--offline]`: one
  static file (ADR-0027), a card per character: when the client last saved the
  file (a time from the file, not from the addon, which records none), the
  client build, spec, equipped gear (item names come from the recorded links'
  bracketed text, never executed or fetched) and the average item levels, the
  class-talent points spent against the tree currency, whether a
  customization record exists, collection counts, currencies and professions
  with names from `CurrencyTypes` and `SkillLine` when the tables are cached
  (ids otherwise; `--offline` never fetches), and every section's `absent`
  reason, so a gap reads as a gap. A comparison table lists one row per
  character and one column per profession and per currency.
- **Names.** The character label is the folder name on the owner's disk. The
  page is personal: it says so in its header, `--anonymize` replaces every
  label with `Character 1…`, and the label, like every string from the
  install or the record, is escaped for HTML and for the embedded JSON
  (a folder named `</script>` must not break out).
- **Freshness.** The page says that each card is the character's last saved
  session (SavedVariables are written at logout, `/reload` or a clean exit).

### 14.5 Decisions for the owner

Each has a default and the work is planned on the default. Say so on this PR
if you want one changed.

- **D1. db2lake engine.** Default: stdlib `sqlite3`, no new dependency
  (ADR-0028). Alternative: DuckDB, which `docs/LAB_IDEAS.md` names; it is
  faster on large tables (ours are small), adds a large compiled dependency,
  and its SQL can read and write files unless external access is disabled.
- **D2. Column types.** Default: inferred from the build's data. Alternative:
  WoWDBDefs, which adds foreign keys and exact widths but a second data source
  to record and keep matching to builds. Wave 3 does not need either.
- **D3. Talent page.** Default: read-only view; planning through the CLI.
  Alternative: click-to-allocate in the page. ADR-0027 grades the embedded
  data and the CSP, not the browser, so allocation rules written in
  JavaScript would be untested by the repository's own standard.
- **D4. Large-table recordings.** Default: ID-filtered subsets (§14.6), so
  the repository holds hundreds of rows of `SpellName`, not 31,716 (the
  source's terms say do not mirror tables publicly). Alternative: whole
  tables, gzipped (`SpellName` would be 254 KB).
- **D5. Names on the dashboard.** Default: folder names shown, `--anonymize`
  to hide them. Alternative: hidden unless `--names`.
- **D6. Loadout strings.** Default: decode in this wave, encode later. Encoding
  needs the tree hash in the header, which comes from the client; a later
  addon change (a new **[verify]** call, a section that can be switched off,
  an owner capture) could record it. Alternative: put that addon change in
  this wave.

### 14.6 Fixtures for Wave 3 (M12-01)

Amends the fixtures rule "small tables only" (`lab/core/tests/fixtures/README.md`).
A recording of a table that is large or that the repository has no reason to
hold whole is an **ID-filtered subset**: the header line and the whole,
verbatim lines whose value in one named column is in a given set, in file
order. `scripts/wago_subset.py` selects lines byte for byte and never parses
and re-serializes (the rule the scrub tool follows); it refuses when the
source's SHA-256 is not the one given, and prints the filter. The provenance
row has kind `wago-csv-subset` and states the full download's SHA-256 and
size, the column, where the id set came from, and the counts. The tables the
talent model reads whole (about 70 KB in all) are recorded whole. The
recordings are made by hand through `GameData.table` (ADR-0012), never from a
test.

### 14.7 Order

```
M12-01 record tables ─┬─ M12-02 gamedata 400
                      ├─ M12-03 db2lake ──┬─ M12-04 db2 SQL surface
                      │                   └─ M12-05 talents model ── M12-06 talents CLI ─┬─ M12-07 loadout decode
                      │                                                                  ├─ M12-08 talents page
                      │                                                                  └─ M12-11 owner capture (optional)
                      └─ M12-10 alt-dashboard page ── (also needs) M12-09 all characters + char list
                                                       all but M12-11 ── M12-12 wave review
```

M12-01 and M12-09 start first and touch nothing in common (fixtures and a
script; `labaddon` and the `char` group). M12-03 onward need ADR-0028
Accepted or amended by the owner; M12-01, M12-02 and M12-09 do not. M12-04,
M12-06, M12-07, M12-08, M12-09 and M12-10 each add commands to `cli.py`; each
puts its logic in its own module and its command bodies beside its group, so
the merges are textual, not semantic, and `pr-shepherd` rebases them. M12-04
needs M11-30 (the escaping it uses) merged first. M12-05 is where a talent
rule the tables cannot settle is found; anything it cannot settle becomes a
note and a **[verify]**, not a refusal, and the owner's capture (M12-11)
settles it later. The wave was planned at ten development tickets; Wave 2's
ten became twenty-five, mostly review hardening, so expect growth.

### 14.8 Re-scope, 2026-10-14 (accepted by the owner, 2026-10-14)

On 2026-10-13 the owner decided that wowlab will be usable by other players
(ADR-0031, accepted 2026-10-14) and asked for Wave 3 to be re-scoped around that. Most
of what is left in it builds things players already have elsewhere: Wowhead
and Forever++ have talent planners, and ForeverGuide and Questie cover
quests. The owner's rule for db2lake: keep M12-03 and M12-04 only if
`wowlab macros check` (M13-06) needs a table it cannot get from the
lab-addon.

| Ticket | State on 2026-10-14 | Recommendation | Why |
|---|---|---|---|
| M12-01, M12-02, M12-13 | merged | stay | done |
| M12-09 `char list` | PR #145, draft, in its last fix round | **keep** | Small, nearly done, and useful to any player with the lab-addon. The owner's message does not name it; keeping it is the conductor's call. |
| M12-14 `wiki-daily` | PR #159, draft | **keep** | Player documentation is part of shipping. |
| M12-03 db2lake, M12-04 `db2` SQL | PR #158 (M12-03) draft, checks green, idle since 2026-09-30; M12-04 not started | **defer** | `macros check` needs no table: macro text comes from `macros-cache.txt`, the spellbook and action bars from the lab-addon (M13-05). `binds coach` (M13-08) takes spell names from the combat log itself. PR #158 stays open as a draft and its branch is kept, so the work comes back with a later wave without being redone. |
| M12-05 to M12-08 talents | not started | **defer** | Incumbents have talent planners. |
| M12-10 alt-dashboard, M12-11 talent capture | not started | **defer** | M12-10 is a page over M12-09's data for one player's alts; M12-11 only serves the talent tickets. |
| M12-12 Wave 3 review | — | **keep, smaller** | It now waits on M12-09 and M12-14 only, and records what was deferred. |

The deferred tickets move, word for word, to `docs/handoffs/M12-deferred.md`.
They return only through a later wave pick (ADR-0024). §14.1 to §14.7 stay
as the spec for when they do. M11-35 (schema 3, the config id) is folded into
M13-05 at the owner's instruction, so there is one schema 3, not a schema 4.
M11-37 (PR #153) and M11-29 are unaffected.

**Waves overlap once.** The remaining M12 tickets finish alongside Wave 4;
M13 does not wait for M12-12. ADR-0024 normally keeps one wave in the
backlog at a time; the owner asked for this one on 2026-10-13, as Wave 3
already carried the owner's wiki requests.

*Closed 2026-10-15 (M12-12):* the review is `docs/handoffs/M12-review.md`,
and the wave's tickets are archived in `docs/handoffs/M12-backlog.md`.
M12-03 (db2lake) merged from the owner's account as #158 despite the
deferral. It is on `main` as a library with no command, and M12-04 stays
deferred.

## 15. Wave 4 (M13): the first public release

Owner decision, 2026-10-13. Decision record: ADR-0031 (accepted by the owner,
2026-10-14, together with the defaults in §14.8 and §15.3). **Nothing in
this section starts until the owner accepts ADR-0031**: that acceptance is
M13-01, and every other M13 ticket depends on it. L1–L8 apply to every
ticket. ADR-0021, ADR-0023, ADR-0026 and ADR-0027 are unchanged; no ticket
adds telemetry, an account or an upload, and no ticket calls a platform
supported before M13-02's acceptance holds.

**Numbering.** Wave 4 was the name given on 2026-09-30 to `wowlab ui`
(ADR-0029). That wave keeps its scope and becomes Wave 5 (M14), aimed at the
second public release.
*Amended 2026-10-15 (owner decision 2026-10-14):* the read-only first stage
of `wowlab ui` is pulled forward into this wave and into v0.1. Its first
view is the install explorer (§15.6, M13-21 to M13-27). Wave 5 keeps the
confirm flow for the `--yes` commands and the embedded pages.

### 15.1 Tickets

The owner's working names are kept beside the ticket ids.

| Owner | Ticket | What | Who |
|---|---|---|---|
| D-01 | M13-01 | ADR-0031 accepted; the wording that contradicts it | owner, then conductor |
| D-02 | M13-02 | Windows is a first-class CI target | implementer; owner finds a Windows tester |
| D-03 | M13-03 | Release pipeline | implementer |
| D-04 | M13-04 | First-run flow and `docs/INSTALL.md` | implementer |
| D-05 | M13-05 | lab-addon schema 3: spellbook, action bars, bindings (and M11-35) | implementer; owner capture |
| D-06 | M13-06 | `wowlab macros check` | implementer |
| D-07 | M13-07 | `wowlab macros fix`, only after the owner's persistence test | owner test, then implementer |
| D-08 | M13-08 | `wowlab binds coach` | implementer |
| D-09 | M13-09 | Redaction for issue reports | implementer |
| D-10 | M13-10 | Player-facing README | implementer |
| D-11 | M13-11 | First public release, v0.1 | owner (the tag) |
| — | M13-12 | Wave 4 review | conductor |
| — | M13-13 | The 30-day gate (§15.4) | conductor counts, owner decides |
| — | M13-21 | `wowlab_core.verdict`: the safe-to-edit answer in core (§15.6) | implementer |
| — | M13-22 | The whole install, with file-map gaps (§15.6) | implementer |
| — | M13-23 | Per-path detail: snapshot facts, views, hints (§15.6) | implementer |
| — | M13-24T / M13-24 | `wowlab ui` server: graders, then the server (§15.6, ADR-0029) | test-writer, then implementer |
| — | M13-25 | The explorer view, first stage: tree, badges, detail (§15.6) | implementer |
| — | M13-26 | Generic forms for read commands; the parity test (§15.6) | implementer |
| — | M13-27T / M13-27 | The explorer view, second stage: whole install, gaps, snapshot facts, previews (§15.6) | test-writer, then implementer |

**v0.1** contains M13-02 to M13-04, M13-09 and M13-10, M13-21 to M13-27
(*amended 2026-10-15*), and M13-06 if M13-05 has landed. M13-08 ships in v0.2 if it has not landed; v0.1 is not held for
it. M13-07 ships only in the form its owner test allows.
*Amended 2026-10-16 (owner):* v0.1 does not wait for M13-02's human
Windows run. It ships with Windows marked untested (ADR-0031's platform rule),
and the CI job stays on.

### 15.2 What is known, and what is [verify]

- **Macros** (`docs/LAB_FORMATS.md` §7). `macros-cache.txt` holds `VER 3`
  headers (version, macro id in hex, name, icon) and a body up to a line that
  is exactly `END`; account macros live in the account folder, character
  macros in the character folder. The 255 limit is the client's; whether it
  counts characters or UTF-8 bytes is **[verify]**, and `macros check`
  reports which count it used. Whether an explicit rank (`Frostbolt(Rank 3)`)
  goes stale is **[verify]** (`docs/LAB_IDEAS.md`, macro-doctor), so those
  macros are reported as "cannot judge" until a capture settles it. Whether
  an edit to `macros-cache.txt` made with the client closed survives the
  next login, with `synchronizeMacros` on and off, is unknown; M13-07's
  first step settles it.
- **Bindings.** `bindings-cache.wtf` maps keys to binding commands such as
  `ACTIONBUTTON3`, not to spells, and what sits in an action-bar slot is
  server state (`docs/LAB_IDEAS.md`, keybind-coach). So spell-to-key needs
  M13-05's action-bar record, and M13-05's live `GetBindingKey` results
  cross-check the file.
- **The addon calls.** forever-addon-kit's API list for build 69893 names
  `GetActionInfo`, `GetBindingKey`, `GetMacroInfo`, `C_SpellBook` and
  `GetSpellBookItemInfo`. Presence in the global table does not prove a call
  works, so each is probed and recorded the way `spec.api` is (§13.1). Values
  are read only when `InCombatLockdown` says not in combat (ADR-0026,
  `docs/GLOSSARY.md`); whether these values can be secret outside combat in
  restricted content is **[verify]**, so where the client has
  `issecretvalue` each value is tested before it is used or stored (§13.1,
  M13-05 amendment). Action-bar macro slots carry an id only, never the
  macro's name or body: macro text is player-written (ADR-0026's no-names
  rule). The id is the macro index when the slot has no subtype, and
  otherwise the spell or item id the macro shows now **[verify]**.
- **Cast counts.** `combatlog` has no cast counting today. The player's own
  casts are picked by the source-flags bits (affiliation "mine", type
  "player" and, since M13-08, controller "player": `& 0x300` = `0x100`,
  `docs/LAB_FORMATS.md` §8) rather than by a GUID or a name, since the lab-addon stores no
  GUID (ADR-0026); that this holds for `SPELL_CAST_SUCCESS` in a Forever log,
  and the field layout of those events in format V22, are **[verify]**
  against one real log before the rules are written. The player has to turn
  logging on (`/combatlog`); the command says so.
- **First run.** `wowlab doctor` asks wago.tools whether each installed
  version is listed unless `--offline` is given; the first-run text says so.
  A WTF folder under OneDrive, Dropbox or Syncthing may hold placeholder or
  conflict files; snapshots never follow a link (M11-18); a sync folder is
  detected by heuristics and reported as such, never refused.
- **Redaction.** The scrub rules live in `scripts/lab_capture.py`, which is
  not part of the wheel. M13-09 moves the shared identifier patterns into the
  package (default: `wowlab_core.redact`) and the capture tool imports them,
  with its behaviour and its refusal tests unchanged. Redaction for an issue
  replaces with placeholders (`<CHARACTER>`, `<REALM>`, `<ACCOUNT>`,
  `<HOME>`); the capture tool keeps its pseudonyms.

### 15.3 Decisions for the owner

Each has a default and the work is planned on it. *The owner accepted every
default on 2026-10-14.*

- **E1. Wave 3 re-scope.** Default: §14.8 as recommended (M12-09 and M12-14
  kept; M12-03 to M12-08, M12-10 and M12-11 deferred).
- **E2. Linux.** There is no native Linux client, so a Linux install means
  Wine or Proton, and nobody has run the Lab against one. Default: CI keeps
  running on `ubuntu-latest`, and the README says "untested on Linux".
- **E3. macOS.** Default: macOS counts as supported once a macOS CI job runs
  the suite (today CI runs only on Linux); the owner's own first run covers
  the human half. Alternative: say "untested" until then. Adding the job is
  part of M13-02.
- **E4. The redaction entry point.** Default: `wowlab doctor --redact`, as
  named. Alternative: a separate `wowlab report` command.
- **E5. Version and tag.** Default: the tag equals the package version
  (`v0.1.0` for `wowlab-core` 0.1.0), and the release job fails before
  publishing when they differ.

### 15.4 The gate

From ADR-0031, measured without telemetry, from GitHub's own counts:
release-asset downloads (the sum of `download_count` over the releases'
assets), distinct non-owner authors of issues and discussions (bots
excluded), and stars (recorded, not part of the threshold). Thirty days after
v0.1, public work continues only with at least 50 downloads and at least 5
issues or discussions from non-owner accounts; the issue count carries more
weight than downloads. The conductor writes the counts and the commands that
produced them to `docs/handoffs/M13-gate.md`. If the gate fails, the public
surface is frozen: releases stay up, no new public-facing tickets are taken,
and the Lab returns to personal-lab mode. **The owner makes the call.**

### 15.5 Order

```
M13-01 ADR-0031 accepted ─┬─ M13-03 release pipeline ──────────┐
                          ├─ M13-09 redaction ─────────────────┤
                          ├─ M13-10 README (needs 03's line) ───┤
                          ├─ M13-04 first-run ── M13-02 Windows ┼─ M13-11 v0.1 ─┬─ M13-12 review
                          │                      (human run)    │               └─ M13-13 gate (+30 days)
                          └─ M13-05 schema 3 (owner capture) ─┬─ M13-06 macros check ─ (v0.1 if ready)
                                                              ├─ M13-07 macros fix (owner test first)
                                                              └─ M13-08 binds coach ─ (v0.2 if late)
```

*Amended 2026-10-16 (owner):* in the graph above, M13-02's human Windows run no longer gates M13-11. v0.1 ships with Windows untested (§15.1), and M13-02 stays open for a later release.

M13-01 comes first. M13-03, M13-09 and M13-10 run alongside M13-02 and
M13-04. M13-02's CI work starts at once; its human run needs M13-04. M13-05
needs an owner capture and is the critical path for the analysis features,
as M11-03 was in Wave 2, so it starts as soon as M13-01 merges. M13-04 and
M13-09 both touch `wowlab doctor`; each keeps its logic in its own module and
`pr-shepherd` rebases the second. If a Windows fix changes what `guard.py`,
`svmerge.py` or `profiles.py` does, it is split and a `T` grader ticket lands
first (`AGENTS.md`). Wave 2's ten tickets became twenty-five; expect growth.

*Amended 2026-10-15:* the explorer and the first `wowlab ui` (§15.6) are
added to the order:

```
M13-21 verdict  ─┬─ M13-22 whole install + gaps
                 └─ M13-23 per-path detail
M13-24T graders ─── M13-24 ui server ─┬─ M13-26 forms + parity
                                      └─ M13-25 explorer, first stage   (needs M13-21, M13-24)
                                           └─ M13-27 explorer, second stage (needs M13-22, M13-23, M13-25)
M13-25, M13-26, M13-27 ─── M13-11 v0.1
```

M13-21 and M13-24T start at once, alongside the other open M13 work. The
critical path to the owner's first use is M13-24T, M13-24, M13-25; to v0.1 it
continues through M13-27. *Amended 2026-10-15 (owner):* the
explorer is split so the owner can use it sooner. M13-25 (tree, badges, detail)
needs only M13-21 and the server; M13-27 adds the whole install, gaps,
snapshot facts, hints and previews.

### 15.6 The install explorer and the first `wowlab ui` (owner decision 2026-10-14)

The owner wants a GUI over the part of the Lab that says what every file in
an install is and whether it is safe to touch (`wowlab explain`, `wowlab
tree --explain`, §6.2 and §6.11). They want to use it before deciding how far
to take it. They chose to pull `wowlab ui` (ADR-0029) forward from Wave 5,
with the explorer as its first view, and to put it in v0.1. For a stranger,
"what is this file, and can I touch it" is the trust model of ADR-0031 made
visible.

**The data flow.** Data goes from core to the CLI's `--json` output, and from
there to the UI. The UI runs CLI commands as subprocesses, as ADR-0029
decides. Every fact the explorer shows is a field in a CLI JSON model,
computed and tested in Python. The JavaScript lays the data out and holds no
file-map logic or game rules (D3, §14.5).

**What the explorer adds to the CLI.**
- **The verdict, in core (M13-21).** The safe-to-edit wording now lives in
  the CLI (`_edit_words`, `_TIER_WORDS`, `_restorable`). It moves to
  `wowlab_core.verdict` as a model,
  `Verdict{level: gate | no | folder | not_applicable | unclassified, restorable, words}`.
  `explain` and `tree --explain` print from that model, and their text output
  does not change. Every Edit cell in `filemap.toml` maps to exactly one level:
  `gate` and `gate (…)` to `gate`, `no` to `no`, `—` (a folder row) to
  `folder`, `n/a` to `not_applicable`. `unclassified` means only "no file-map
  row", the set `filemap gaps` lists.
- **The whole install (M13-22).**
  - Today `tree` covers one flavor folder. `tree --install` adds the
    install root (the file map's `root` section) and every flavor folder,
    and each item carries `base` and `flavor_folder`.
  - `wowlab filemap gaps` lists the paths with no file-map row. They are
    grouped under their nearest classified parent and ranked by file count
    and bytes.
  - The walk is bounded, read-only (L1), never follows a link, and reports
    `truncated`. The gap report shows the owner where the map is thin, so
    they can decide where to extend it.
- **Per-path detail (M13-23).** `explain --json` gains four fields:
  - `stat`: size and modification time.
  - `snapshot`: the latest snapshot holding the path, and whether the live
    file's hash differs from it.
  - `views`: the argv of an existing read command that decodes this kind of
    file. For example, `sv dump` for SavedVariables, `cvar list` for
    `Config.wtf`, and `binds list`, `macros list` and `addons list` for
    theirs. A table keyed by file-map entry id supplies these, so no new
    decoder is written.
  - `hints`: copyable command text, for example `wowlab snap restore ID
    --paths P`, which `explain` never runs.

**The UI, first stage.**
- **The server (M13-24T, M13-24).** It is built exactly as ADR-0029 decides.
  This stage runs read-only commands only. Every leaf command that declares
  `--yes` is refused, and the set is derived from the Click tree, not from
  a count; on 2026-10-15 that is eight, `doctor` included (its `--yes` takes
  a first snapshot). The ADR-0029 amendment of 2026-10-15 records the
  staging.
- **The explorer view (M13-25, then M13-27).** M13-25 ships the tree,
  the badges and the detail pane on one flavor folder; M13-27 adds the rest
  below.
  - A tree pane with a verdict badge on every item. It filters by verdict,
    by "gaps only", or by text.
  - A detail pane showing what the file is, who writes it, the verdict
    words, the snapshot facts, and the hints with copy buttons.
  - A Preview button that runs the entry's first view and draws the JSON as
    a generic, collapsible, read-only tree.
  - A gaps tab.
  - All rendering uses `textContent`, and home paths are shown as `~`.
- **Generic forms and the parity test (M13-26).** Every other read command
  gets a form drawn from its declared parameters. A parity test in the
  required `quality` job asserts that every leaf command is reachable or
  excluded with a reason.

**Previews show the player's own data** (character names, addon settings) on
their own machine and nowhere else. Nothing is uploaded (ADR-0019). The
server is bound to `127.0.0.1` and checks a token on every request.

**What stays in Wave 5 (M14).** The confirm flow for every command that declares
`--yes` (`snap restore`, `undo`, `profile apply`, `addon install`, `addon
remove`, `sv merge`, `snap gc` and `doctor` on 2026-10-15), and the embedded
looks, talents and character pages. *Specified 2026-10-18 in §16, with the
owner's addition of the character view (gear, keys, macros); the talents
page waits for char-planner (§14.8).*

## 16. Wave 5 (M14): `wowlab ui`, the confirm flow and the character

Specified 2026-10-18 at the owner's request, after the Wave 4 review
(`docs/handoffs/M13-review.md`). Wave 5 was already named (`wowlab ui`,
ADR-0029) and owes the confirm flow for the commands that declare `--yes`
and the embedded pages (§15.6, "What stays in Wave 5"). On 2026-10-18 the
owner added the second half: **the player's character in the UI**, with
its equipped gear, its key bindings and its macros, read-only first, and
built so that a later wave can change and optimize them from the UI with a
model's help. This section specifies both halves. The owner's pick of
2026-10-18 holds: the twelve M13 follow-ups merge before any M14 ticket is
dispatched (`docs/BACKLOG.md`, M14-01).

Nothing in M14 changes ADR-0019, ADR-0021, ADR-0023, ADR-0026 or
ADR-0027, and L1 to L8 apply to every ticket; §16.5 F4's second stage
amends ADR-0019 through ADR-0032, accepted 2026-10-18, and is not M14 work. ADR-0029 gets one dated amendment: its
2026-10-15 amendment named an embedded "character page", and §16.3 builds
a native tab instead, drawn from `--json` as the explorer is (F-A); the
amendment is under ADR-0029, accepted by the owner on 2026-10-18. The read-only stage uploads
nothing and writes nothing into an install. The modify and optimize stages
are specified in §16.5 for the owner's next pick; they are not M14 tickets.

### 16.1 The confirm flow (ADR-0029)

Built exactly as ADR-0029 decides. A command that declares `--yes` is run
first without it, as every UI run is made: with `--json`, stdin closed, and
the argv built from the Click tree. The *plan run* has one of three shapes,
chosen by the server (Python, never the page) from what the leaf declares,
and the server offers Confirm only when the run ended with that shape's
marker; every other outcome, a refusal (exit 3) included, is shown as the
CLI printed it with no Confirm:

- a leaf that declares `--dry-run` (`snap gc`, `snap restore`, `profile
  apply`, `addon install`, `addon remove`) is run with `--dry-run --json`;
  the marker is the JSON report with `dry_run: true`, and it is the plan;
- a leaf with no `--dry-run` that confirms through `cli._confirm` (`undo`,
  `sv merge`) is run with `--json` and no `--yes`; the CLI writes the text
  plan to stderr, `_confirm` gets no answer, and the run ends with
  `Nothing was changed.` and exit 1; the marker is that ending, read by a
  constant the CLI exports, and the plan is the stderr text before it;
- `doctor` never reaches `_confirm`: with `--json` it reports
  `first_run.outcome == "not_asked"` when a first snapshot could be
  offered. A plan run without `--yes` never returns `choose_flavor`: on an
  install with several flavors it returns `not_asked` with `flavor` unset
  (`firstrun.run`), and the confirmed `--yes` run would then exit 2. So
  the marker is `not_asked` **with `flavor` set** (exit 0; *corrected
  2026-10-18, M14-02: the report's field is `first_run.chosen`, and the
  marker reads it*); `not_asked`
  without a flavor is shown with a `--flavor` field to fill and no token,
  and any other outcome offers nothing. (Automated review of #265, finding
  2, 2026-10-18.)

After the click the server runs the same argv again with `--yes` appended
(and `--dry-run` removed where the plan run added it); the result shows
only the hint the CLI prints (`wowlab undo` for a `guard` transaction;
`snap gc` and `doctor` print none, and no undo is offered for them).

**The confirmed run applies the plan that was shown, not a newer one.**
The token binds the argv; it cannot bind state that moved between the two
processes (automated review of #265, finding 1). Each command pins its
own plan: `snap restore`, `addon install` and `addon remove` are pinned by
their arguments (a snapshot id and paths, an addon name); `profile apply`
is pinned by a profile name only, and a name can be deleted and saved
again between plan and confirm (`profiles.save` refuses a name only while
it exists), so its drift is captured, not pinned: `guard` snapshots the
live files before any change, so nothing is lost, and the result names
the profile's snapshot id so the player can see what was applied. `snap gc` removes only
what is unreferenced at confirm time, so a snapshot taken in between is
kept; harmless. `doctor --yes` takes a first snapshot; harmless. The two
that can apply a different plan are `undo`, which undoes the newest
journal record at run time (the CLI's own re-read in `undo`, and
`guard.undo(expected_id=…)`, protect one process, not two), and `sv
merge`, which hashes the target at its own start. M14-03 gives both an
`--expect` option: `undo --expect <journal id>` and `sv merge --expect
<sha256 of the target>`, each printed in the plan JSON, each passed back
by the confirmed run, each compared by the CLI under `guard`'s lock and
refused with exit 3 on mismatch. The UI never confirms either without it. The set of commands is derived from the Click tree, not
from a count (on 2026-10-18: `snap restore`, `undo`, `profile apply`,
`addon install`, `addon remove`, `sv merge`, `snap gc` and `doctor`). It
ships in two steps so that the mechanism is proven on the two commands
whose worst case is wowlab's own store before it reaches an install:

- **M14-02, the mechanism on the store-only commands.** `snap gc` and
  `doctor --yes` (the first snapshot). The server keeps the plan run and the
  confirmed run as one pair: the confirm request carries a one-time token
  minted for that plan, bound to the same argv, and spent on use, so a page
  cannot confirm a plan it did not show. The parity test loses the reason
  "confirm flow: M14" for these two and gains the assertion that every
  `--yes` command goes through the confirm step (ADR-0029, "Parity").
- **M14-03, the install writes.** `snap restore`, `undo`, `profile apply`,
  `sv merge`, `addon install` and `addon remove` behind the same mechanism,
  with the plan shown as `guard` prints it and the undo offer after. Every
  one of these reaches `guard`, so `security-reviewer` reviews the ticket.
  The exclusion list then holds `ui` itself alone; the two refused
  parameters, `log tail --follow` and `looks page --out` (owner rulings of
  2026-10-15), stay refused.

The server itself still writes nothing (L2). The UI never adds `--yes` to a
command the player did not confirm, and never runs a confirmed argv other
than the one it showed.

*Amended 2026-10-19 (M14-03, as built):* the server lists no confirm set; it reads each `--yes` leaf's shape from what the leaf declares (`--dry-run` → the first shape; `doctor` → the second; no `--dry-run` with a one-value `--expect` and a report field named in `cli.EXPECT_FIELDS` → the third, `cli.CONFIRM_DECLINED` as the exact last stderr line of an exit-1 run with one JSON object on stdout), so a future `--yes --dry-run --json` leaf joins the step with no UI change. The confirmed argv of the third shape is the plan argv plus `--yes` and `--expect=<pin>`, the pin read from the plan's own report when the token is minted (`undo`: the journal record id `undone`; `sv merge`: `target_sha256`), never from the confirm request; the pin is checked once before `guard`'s lock and again under it. Only those two commands refuse when something changed after the plan: `undo` on a newer journal record, `sv merge` on its target's bytes. The dry-run shape carries no pin: `snap restore` is held to its snapshot id and `--paths` and re-checks its plan inside the transaction, `addon install` and `addon remove` to the addon name, and `profile apply` to a profile name only, so its drift is captured by the pre-write snapshot, not pinned; each applies what is there at confirm time, after that snapshot. `sv merge`'s source is not pinned either (M13-74). A confirmed run killed at the UI's limit (120 s, or the output cap): the lock goes with the process, the snapshot taken before the first change holds every file, and the page says to show `undo`'s plan and confirm it only if the record it names is this command's, in state `open`; `snap gc` and `doctor` get a store-only sentence instead.

### 16.2 The character: what the CLI adds

The data flow is §15.6's: core to a CLI `--json` model to the UI, which
lays the data out and holds no game rules (§14.5 D3). Every fact the
character view shows is a field in one of three new models, computed and
tested in Python, each with a text form too. The lab-addon's record is the
source for gear, action bars and spellbook (§13.1, schema 3); the WTF text
files are the source for the bindings and macros on disk (§6.5); the view
is their join. Each command takes `--character`, `--account`, `--root` and
`--flavor` exactly as `char show` does; without `--character` it picks the
latest-written `WowLab.lua`, as `char show` does, and says so.

- **`wowlab char gear` (M14-05).** The equipped gear of one character,
  slot by slot, from `gear` in the addon record. A new parser,
  `wowlab_core.itemlink`, decodes the item-link string losslessly (L4: the
  raw link is kept beside every decoded field): the colour code (named
  `|cnIQn:` or `|cffRRGGBB`, `docs/LAB_FORMATS.md` §4.2 amendments), the
  item id, the enchant, the gem fields, the suffix, the specialization id,
  the bonus-id list and the upgrade fields as the `|Hitem:` payload orders
  them, and the bracketed name. Field meanings are **[verify]** and the
  parser is graded on the links in the committed `WowLab.lua` fixtures
  (L8), with constructed links only for the hostile cases (no closing `|h`,
  a GUID-shaped run where the crafter field is). The name is the bracketed
  text and nothing else; no item name, icon or tooltip is ever fetched
  (§14.4; ADR-0022's fetcher stays for DB2 tables only). The slot's number
  is the client's; its name (`Head`, `Neck`, …, `Main Hand`) comes from a
  data file shipped with the package, keyed by slot number and marked
  **[verify]** on Forever, and the model carries both. Item level and the
  averages are the client's figures as recorded (never derived from bonus
  ids, GLOSSARY "Item level"). Quality is the colour code as decoded; the
  mapping from colour to a quality word is community documentation,
  **[verify]**, and shipped in the same data file so the JavaScript never
  holds it. Model `CharGearReport{character, file, mtime_ns, spec, average,
  slots: [{slot, slot_name, raw_link, item_id, name, colour, quality,
  item_level, item_level_api, enchant_id, gem_ids, suffix_id, spec_id,
  bonus_ids, upgrade, crafter_removed}], empty_slots, notes}`. A record
  without `gear` (switched off with `/wowlab skip gear`, or a schema the
  reader does not know) says so and exits 1, as `char show` does for a
  missing file.
- **`wowlab char binds` (M14-06).** What each key does for one character:
  the join of the bindings (the account and character `bindings-cache.wtf`
  files through `wtfconfig`, and the addon's `bindings` section from
  `GetBindingKey`), the action bars (`actionbars`) and the spellbook
  (`spellbook`). The join exists today inside `bindscoach` (`command_slot`,
  `_spellbook`, the page rule of GLOSSARY "Action slot"); M14-06 lifts it
  into `wowlab_core.keymap`, used by both, with `binds coach` output
  byte-identical before and after. Per key: the command, the slot it
  presses on the default page, what that slot holds (a spell with its name
  and rank subtext from the spellbook, a macro by id, an item by id, a
  flyout, an equipment set), and which source said so: the file, the addon
  record, or both. Where the two sources disagree (a key in the file the
  client did not report, or the reverse) the row says so rather than
  choosing; §15.2 names that cross-check. Also: the filled slots no key
  reaches (the "clicked" ones the coach reports as cast-without-bind), and
  how many commands and keys the addon left out (`commands_left_out` and
  `keys_left_out` are counts, not lists). A character
  without a schema-3 record gets the file rows alone with "cannot place:
  no action-bar record", the two lab-addon rules of `macros check` being
  the precedent. Model `CharBindsReport{character, files, record_file,
  keys: [{key, command, slot, page_note, holds: {type, id, name, subtext}
  | null, sources}], unreached_slots, commands_left_out, keys_left_out,
  notes}`.
- **`wowlab char macros` (M14-07).** The macros of one character: every
  record of the account and character `macros-cache.txt` (`macros list`'s
  `MacroItem`, body kept line by line), each with the action slots that
  hold it and the keys that reach those slots (through `keymap`), and the
  `macros check` findings for it (`MacrosCheckReport`'s rules, unchanged,
  joined by macro). Which action-slot macro id names which file record is
  **[verify]** (§15.2: the macro index when the slot has no subtype,
  otherwise the spell or item id the macro shows now). Placement therefore
  has three states: `unconfirmed`, the slots the candidate rule would give,
  shown as "may sit at slot n (unconfirmed)"; `none`, no candidate slot;
  and `confirmed`, used only once a dated amendment in `docs/LAB_FORMATS.md`
  records the rule a capture confirmed. The ticket writes the owner's
  capture step into `docs/handoffs/`. Model `CharMacrosReport{character,
  files, macros: [{scope, id, name, icon, body_lines, complete, placement:
  {status, slots, keys, reason}, findings}], notes}`.

All three are read commands: no `--yes`, nothing written anywhere (L1), so
the UI's generic forms (M13-26) reach them the day they merge, and the
parity test needs no new exclusion. Each is a new leaf command, so each
ticket names the wiki page that documents it (`docs/AGENT_WORKFLOW.md`, "A
new command and the wiki"). `char show` is unchanged.

*Amended 2026-10-19 (M14-06, the final `CharBindsReport`):* `flavor_folder`, `account`, `character`, `chosen_by`, `files[{scope, path, mtime_ns, bind_lines, unknown_lines, note}]`, `record_file`, `record_mtime_ns`, `record_schema`, `record_error`, `keys[{key, command, slot, page_note, holds{type, id, sub_type, name, subtext} | null, does, sources, agreement ("both" | "file only" | "record only" | "not in a file" | "cleared" | null), disagrees, note}]`, `unreached_slots[{slot, page_note, unfollowed_page, holds, does}]`, `commands_left_out`, `keys_left_out`, `cannot_place`, `cannot_compare`, `notes`. One row per (key, command); a key only the record names and no file line mentions is "not in a file", not a disagreement; a file and the record putting one key on different commands mark both rows as disagreeing and name each other. M14-09's keys panel reads `does`, `agreement`, `disagrees`, `unreached_slots[].unfollowed_page` and `cannot_place`.

*Amended 2026-10-19 (M14-07, the final `CharMacrosReport`):* `flavor_folder`, `account` (the Battle.net account folder name; local output only, ADR-0019), `character`, `chosen_by`, `files[{scope, path, mtime_ns, macros, unknown_lines}]`, `bindings_files` (`CharBindsFile`), `record_file`, `record_mtime_ns`, `record_schema`, `record_error`, `macros[{scope, file, position (1-based, file order), line (the VER header), id (the hex macro id, not the index), name, icon, body_lines, complete, bytes, characters, placement{status ("unconfirmed" | "none" | "confirmed"; no rule in M14 emits "confirmed"), candidate_index (the index the candidate numbering gives the record, not the client's), slots[{slot, page_note, unfollowed_page}], keys (keymap.KeyRow), reason}, findings (macrocheck.Finding), cannot_judge (macrocheck.CannotJudge)}]`, `cannot_judge` (not tied to one macro), `keys_incomplete` (why a bound key may be missing from `keys`; null when the bindings record is complete), `notes`. The candidate numbering is `macroplace.CANDIDATE_RULE`: the 120 offset is Retail's, from memory **[verify]**; counting records in file order is this tool's assumption **[verify]**; a `macros-cache.txt` older than the record moves every candidate. `docs/handoffs/M14-macro-place.md` is the owner's capture step that settles it.

### 16.3 The character in the UI

A fourth tab, **Character**, beside Explorer, Gaps and Commands, drawn
from the three models and `char list`. Read-only: it runs only read
commands, through the same `/api/run` as the explorer's previews, and it
offers no edit control in M14. All rendering uses `textContent`; macro
bodies and item names are player text and are never interpreted as markup.

- **M14-08, the character and its gear.** A character picker from `char
  list --json` (folder labels, the file's time; the latest-written first),
  a header (the character label, spec id, the equipped and overall average
  item level, and the two notes `char show` prints about what the file is),
  and the gear laid out in the client's slot order: slot name, item name in
  its quality colour, item level, and a detail row on click with the item
  id, enchant, gems, bonus ids and the raw link with a copy button. An
  empty slot is drawn empty, and a record with no `gear` shows the
  command's reason. Nothing in the JavaScript knows a slot number's name or
  a colour's quality: both come from the model.
- **M14-09, keys and macros.** Two panels in the same tab. **Keys:** the
  rows of `char binds --json` as a table (key, what it does, source), a
  text filter, a "disagreements only" filter and the unreached-slots list.
  **Macros:** the rows of `char macros --json`, each with its body shown
  as lines, where it sits and which keys reach it, and its findings as the
  checker words them, with a "findings only" filter. Both panels say which
  files and which record they came from, with the file's time.
- **M14-04, the embedded page.** `looks page` shown inside the UI, as
  ADR-0029 names. Three facts of the server constrain the route: every
  response carries `frame-ancestors 'none'` (owner ruling 2026-10-15), the
  token is required on every request and lives only in the opened URL, and
  the UI's CSP allows its own inline script and style by hash and nothing
  else. So the page is not served as a framable document. The default
  route: the page reads the file through the token-checked API (the server
  builds the path under the data directory's `pages/` itself, never from a
  request parameter) and puts the text into an `iframe` with
  `sandbox="allow-scripts"` and `srcdoc`; a `srcdoc` document inherits the
  UI's CSP, so the UI's `script-src` and `style-src` gain the hashes of the
  looks page's static script and style (`lookspage._SCRIPT`, `_STYLE`,
  already hashed by `lookspage` for its own CSP; the data block is
  `type="application/json"` and runs nothing). No header is loosened and no
  ruling changes; a test asserts the hashes match the templates. If the
  security review finds the route unsound, the fallback is the file's path
  with a copy button, which is what the CLI gives today, and the owner is
  asked for a ruling. The talents page is not built in M14: §14.3 is
  deferred (§14.8) and there is no `talents` command to embed; it returns
  with char-planner.

*Amended 2026-10-19 (M15-01 and M15-02, the first Wave 6 tickets, owner's ask of the same day):* the lab-addon records the character's identity (race id, body type, class id and file; schema 4, M15-01), since the gear report carried no race or class and the `customization` record exists only after a barber-shop visit. On that, the Character tab gains one button under the gear table, **See it dressed** (M15-02), which opens, in the player's own browser, an outside dressing room (one per game version on the site, from memory **[verify]**) at an address the CLI builds from the identity and the equipped items' item ids and bonus ids and returns in `char gear --json` as `dressing`. The address holds no name, realm, account, path or token. The Lab makes no request: the page sets the `href` of a static `<a target="_blank" rel="noopener noreferrer">` only when the parsed url is `https:` with exactly the site's origin, the CSP is unchanged, and clicking is the player's act (ADR-0019 and ADR-0031 amendments of 2026-10-19; the README and SECURITY trust-model sentences gain one clause saying so). The site's address grammar is undocumented, so the builder is graded on recorded examples with a provenance row and fails closed: when the identity is absent, no equipped item can be placed, or the site's version label for this client is unknown (chosen by the client's discovered major version, never a product code: L6), the button is replaced by the list of item ids with a Copy button. Whether a Forever-era character's items render there is **[verify]** until the owner tries it.

*As built, 2026-10-19 (M15-02):* the site's address grammar lives in the shipped data file `dressing_sites.toml` (the hash codec's alphabet, delimiters and field order as data, the site's slot table, one room per site version label) and is graded on two recorded examples in the derived fixture `macos-70450/derived/dressing-room.json`: addresses the site's own encoder produced in the conductor's browser for the two real 70450 characters, which the site then decoded into the same identity and items. Three facts the examples settled beyond the grammar as read from the site's script: the site drops the trailing zeros of the encoded fields (both examples end that way, so the builder trims them and the decoder reads an absent final field as 0); the address also carries the character's level (read from the record's talent block, else the site's default of 100 **[verify]**), so ADR-0019's amendment lists it; and the version band is a range at major.minor precision (`1.60` and above, below `2`, is the Forever room), narrower than "major version" on purpose, since a Classic Era client is also a 1.x version and was not tried. An address whose empty slots would need more than 116 compressed pairs is not built (the chained form of that compression is untested against the site: owner question); such a character gets the item-id list. The page's two test exemptions became four: the ticket's constant and `.href` assignment, plus the literal `new URL(value)` the ticket itself requires (its `url(` would otherwise trip the external-url marker) and the same constant in the parity test; each is pinned to exactly one occurrence. Whether the model draws in a real browser is still the owner's check.

### 16.4 What is known, and what is [verify]

- **Item links.** `docs/LAB_FORMATS.md` §4.2 and its amendments: the
  committed `WowLab.lua` files carry 38 links in all (nine in each of the
  three 70009 and 70058 files, eleven in the 70425 capture), every one in the named
  colour form `|cnIQn:` and none with a crafter; the `|cffRRGGBB` form is
  unobserved and is covered by a labelled constructed boundary test only.
  The field positions after the item id are **[verify]** against community
  documentation, and the parser keeps every field raw. The specialization
  field matched `spec.id` in the 70009 captures (`docs/LAB_FORMATS.md`,
  M11-23 follow-up table, "Spec found by testing for the API") and again in
  the 70425 capture (`:15:1490:`). Note, 2026-10-18 (M14-05): "none with
  a crafter" holds for the links as stored, but one 70425 slot (15) has
  `crafter_removed = true`: its crafter field is blank because the addon
  removed a GUID-shaped run before storing the link (`docs/LAB_FORMATS.md`,
  item-link entry of that date).
- **Slot names and quality words** are game knowledge the client does not
  write into the record. They ship as a data file with **[verify]** marks,
  overridable from the user config directory as `binds_coach_costs.toml`
  is, and the output says it is a default. A later schema may record the
  client's own slot names; that is not filed.
- **Bindings on disk versus bindings reported.** The account file is the
  one the client keeps; the character file exists only with
  character-specific bindings on (`docs/LAB_FILE_MAP.md`). The addon
  reports what `GetBindingKey` returned at save time. Neither is "the
  truth"; the view shows both and marks disagreement.
- **Macro placement** is **[verify]** (§16.2).
- **Persistence of edits** to `bindings-cache.wtf` and `macros-cache.txt`
  is unknown (the file map says "server may replace"); M13-07's runbook
  settles macros, and §16.5 adds the same test for bindings. Nothing in
  M14 depends on the answer.

### 16.5 The road to modify and optimize (for the owner's next pick)

The owner's intent is that the character view becomes the place where
bindings and macros are changed and improved, with a model's help. That is
not M14 work: it needs two owner tests, the writers, and one new ADR. It is
written here so that the next wave pick is one step, and so that M14 builds
nothing that has to be undone. Each item below is a ticket shape, not a
ticket (ADR-0024, §11).

**F1. Two persistence tests, owner-run.** M13-07's runbook for
`macros-cache.txt`, and a twin for `bindings-cache.wtf`
(`docs/handoffs/M14-binds-persist.md`, written by the conductor in M14):
with the client closed, change one binding in the file, log in, and record
whether the change persists and whether the client rewrote the file, once
with binding sync on and once off. The answers go into
`docs/LAB_FORMATS.md` §6 and §7 as dated amendments. Nothing that writes
these files is built before its answer is in.

**F2. The writers, through `guard`.** If an edit persists, a writer for
`bindings-cache.wtf` and one for `macros-cache.txt` in `wtfconfig`,
lossless (L4: only the lines that change are rewritten, every other byte
kept), each applied by a `guard` transaction with `--yes`, a snapshot
first and `wowlab undo` after (L2, ADR-0021). These are the writers
`docs/LAB_IDEAS.md` says **keybind-coach**, **macro-doctor** and
**wow-as-code** share. The commands are `wowlab binds coach --apply` (the
name the Wave 4 review used) and `wowlab macros fix` (M13-07), each taking
a *proposal file* (F3), each printing
its plan without `--yes`, and each reaching the UI through the confirm
flow of §16.1 with nothing UI-specific added. If the server replaces the
file, no writer is built: the same commands print the corrected text for
the player to apply in game, as M13-07 already provides for.

**F3. The proposal format, and its validator.** A proposal is a small
document (TOML, like the Lab's other data files) that names the character
and lists changes: `bind KEY COMMAND`, `unbind KEY`, `macro set NAME body`,
`macro add`, `macro remove`. `wowlab_core.proposal` parses it and validates
it against the character's own data before any plan is made: the key names
the client accepts, the binding commands the record lists, spell names in
the spellbook, the 255 limit (with the count `macros check` uses), no
duplicate names, balanced brackets. A proposal that fails validation is
refused with every reason, and nothing is planned. The validator is Python,
graded on labelled constructed proposals (hostile cases included: a
command with a shell character, a key of a thousand characters, a body
with `|` escape sequences), and it is the only path from any suggestion,
human or model, to a `guard` plan. **A proposal is data, never
instructions**: the same rule ADR-0031 sets for strangers' text.

**F4. The advice packet and the model (ADR-0032, accepted 2026-10-18, both stages).** How a model
gets to see the character's data is the one question the Lab's rules do
not already answer: ADR-0019 and ADR-0031 say nothing read from an install
is ever uploaded, and that sentence is on the README's first screen.
ADR-0032 sets two stages; the owner accepted both on 2026-10-18, and the
second is built after the first:

- *Stage one, no upload:* `wowlab char advise` writes an **advice packet**,
  one self-contained text file under the data directory: the gear, keys
  and macros reports in a compact form, the coach's and the checker's
  findings, the cost table in use, and the proposal grammar of F3 with
  three worked examples. The player gives it to whichever assistant they
  use, in a browser, by hand, and brings a proposal file back; `binds
  coach --apply` and `macros fix` validate it (F3) and plan it. The Lab
  makes no network request and holds no key. The UI shows the packet with a copy
  button and a drop target for the proposal, and the confirm flow does the
  rest. This stage needs no change to any accepted ADR.
- *Stage two, opt-in, if the owner accepts it:* a `wowlab char advise
  --send` that posts the same packet, redacted by `wowlab_core.redact`
  (character, realm and account placeholders; the packet never held a GUID
  or a path), to a hosted model the player configured, with the player's
  own key from the user config directory, never from the environment of a
  child run and never logged. It is off until configured, it shows the
  exact bytes before sending, it sends nothing else, ever, and it is never
  run by a test or a workflow (ADR-0012's live-service rule). The reply is
  parsed as a proposal (F3) and validated like any other; a reply that is
  not a valid proposal is shown as text and nothing is planned. The README
  sentence changes to name this one exception and its switch, and so does
  "the only network traffic is fetching public game tables" (ADR-0019,
  ADR-0022, ADR-0031 and `.claude/rules/lab.md`), which gains the same
  exception. This stage is the amendment to ADR-0019 that ADR-0032
  proposes.

Why staged this way: F3 is the part that has to be right under L2 and L4
whichever stage the owner picks, and stage one exercises it in full with
the trust model untouched. *The owner accepted both stages on 2026-10-18
(ADR-0032, Accepted).* Stage two is still built after stage one, off until
configured.

**F5. The views become editable.** Only after F2 and F3: the Keys and
Macros panels gain an editor whose output is a proposal file, never a
direct write; the proposal goes through the validator and the confirm flow
like one written by hand. Gear is never edited: the client owns it (ADR-
0023).

### 16.6 Decisions for the owner

Each has a default and the work is planned on it. *The owner accepted every
default on 2026-10-18, one by one: F-A (with the ADR-0029 amendment), F-B,
F-C, F-D (strengthened the same day by the `--expect` pin in §16.1, from
the automated review's finding), F-E (ADR-0032, both stages) and F-F
("build regardless").*

- **F-A. Where the character lives.** Default: a Character tab in `wowlab
  ui`, drawn from `--json` models (§16.3), the explorer's pattern.
  Alternative: a static page like `looks page` (ADR-0027), embedded, which
  is what ADR-0029's 2026-10-15 amendment named. The tab is chosen because
  the later editing stage (F5) needs the server, and because a page would
  duplicate the explorer's JSON rendering. The default is a dated
  amendment under ADR-0029 and takes effect with the owner's word on the
  plan PR.
- **F-B. New commands or a wider `char show`.** Default: three new leaf
  commands (§16.2), each small and each with its own model. Alternative:
  new sections on `CharShowReport`. New commands are chosen so that the
  joins are testable alone and `char show --json` stays what it is for
  scripts (ADR-0031: `--json` is an interface).
- **F-C. Slot names and quality words.** Default: a shipped data file with
  **[verify]** marks and a user override (§16.4). Alternative: a schema-4
  addon change recording the client's own slot names and quality names,
  which needs an owner capture first.
- **F-D. The confirm token.** Default: one-time, per plan, bound to the
  argv (§16.1). Alternative: re-running the plan at confirm time and
  comparing; costs a third process per write and still needs a binding.
- **F-E. The model, stage one or both.** ADR-0032 (§16.5 F4). Default:
  stage one in the next wave; stage two only if the owner accepts the ADR
  as a whole. Not an M14 decision; recorded here so it is not forgotten.
- **F-F. The gate and personal mode.** The Wave 4 review tied the apply
  halves to the gate (M13-13). Default: §16.5 is built whether or not the
  gate passes, since it serves the owner's own install first; what the
  gate decides is whether it is released. The owner may rule otherwise.

### 16.7 Tickets and order

| Ticket | What | Who | Size |
|---|---|---|---|
| M14-01 | Wave 5 opens: the M13 follow-ups are on `main` | conductor | XS |
| M14-02 | The confirm flow, on `snap gc` and `doctor --yes` (§16.1) | implementer; `security-reviewer` | M |
| M14-03 | The confirm flow, on the six install writes; `--expect` on `undo` and `sv merge` (§16.1) | implementer; `security-reviewer` | M |
| M14-04 | `looks page` embedded in the UI (§16.3) | implementer; `security-reviewer` | S |
| M14-05 | `wowlab char gear` and `wowlab_core.itemlink` (§16.2) | implementer; `domain-reviewer` | S |
| M14-06 | `wowlab_core.keymap` and `wowlab char binds` (§16.2) | implementer; `domain-reviewer` | M |
| M14-07 | `wowlab char macros` (§16.2) | implementer; `domain-reviewer` | S |
| M14-08 | The Character tab: picker, header, gear (§16.3) | implementer; `security-reviewer` | M |
| M14-09 | The Character tab: keys and macros (§16.3) | implementer; `security-reviewer` | M |
| M14-10 | Wave 5 review | conductor | S |

```
M14-01 ─┬─ M14-02 confirm (store) ─┬─ M14-03 confirm (installs) ──────────────────────┐
        │                          ├─ M14-04 looks page embed ────────────────────────┤
        │                          └──────────────┐                                    │
        ├─ M14-05 char gear ──────────────────────┴─ M14-08 tab: gear ─┐               │
        └─ M14-06 keymap, char binds ─┬─ M14-07 char macros ─┐         │               │
                                      └──────────────────────┴─────────┴─ M14-09 tab: keys, macros ─┴─ M14-10 review
```

*Opened 2026-10-18 at dafd223 (M14-01: the twelve M13 follow-ups merged, #269–#281; the `--yes` set is the eight of §16.1; `lab (windows)` green on `main`).* *Amended 2026-10-18 (owner: "want the gear view sooner"):* the UI queue is M14-02, M14-08, M14-03, M14-04, M14-09, so the Character tab's gear view lands before the install-write confirmations and the looks page; the dependency graph is unchanged.

M14-02, M14-05 and M14-06 are independent and dispatch together. Every
ticket that touches `ui/__init__.py` or the templates (M14-02, M14-03,
M14-04, M14-08, M14-09) edits the same three files, which is the named
collision AGENTS.md allows as a reason to hold one back: the conductor runs
them one at a time, in that order as amended above (2026-10-18), while the CLI tickets run beside them.
`wowlab_core.ui` is no longer in the graders-first set (AGENTS.md, owner
decision 2026-10-17), so no M14 ticket has a `T` twin; `security-reviewer`
reviews every ticket that touches the server or its templates, and
`domain-reviewer` every one that tells the player what a slot, key or macro
is. v0.2 carries all of M14 and the `--json` changes M13-47 guards.

## 17. Wave 6 (M15): character-studio, the data half

Picked by the owner on 2026-10-19 after the Wave 5 review
(`docs/handoffs/M14-review.md`): **character-studio** from
`docs/LAB_IDEAS.md`, the player's character rendered in the browser from
the player's own game files, with its gear. It is size L, so it is two
waves. This wave is the data half: it ends in `wowlab char model`, which
turns a recorded character into a standard glTF file under the data
directory, headless and graded by pytest, with no 3D on screen. Wave 7
(§17.8) puts it in the Character tab. Decisions: ADR-0033 (the archives are
read, read-only, in pure Python; how asset-format parsers are graded) and
ADR-0034 (a vendored three.js for Wave 7), both accepted by the owner on 2026-10-19 after the plan PR's reviews. The owner asked whether `Data/` could also be edited; L7 stands
as written and the reasons are in ADR-0033. L1 to L8 apply to every
ticket, with L8's asset-format exception. The dressing-room link
(M15-01, M15-02; §16.3's 2026-10-19 amendment) opened the wave because it
needs nothing below.

Everything in this section rests on a read-only research pass of
2026-10-19 against the owner's install, then at build 1.60.1.70450 (the Battle.net agent patched the client from 70425 that day), and on the reference wiki
(wowdev.wiki) and the MIT-licensed wow.export as the cross-check. Every
number quoted is measured there; every claim from memory is **[verify]**
(§17.7). Code is written clean-room from the wiki and WoWDBDefs; nothing is
copied from TACT.Net (GPL-3.0) or WoWFormatLib (no licence).

### 17.1 `casc` — the install's archives, read-only (ADR-0033)

`wowlab_core.casc` opens the install's `Data/`, which every product in the
install shares (`docs/LAB_FILE_MAP.md`; only the build config is per
product), and reads a file by its file data id for one flavor's build. The
chain, every step of it already parsed by the research spike with the
stdlib only (`struct`, `zlib`, `hashlib`, `bisect`, `array`):

1. **`.build.info`** (the Lab's `install` module already reads it): the
   flavor's `.flavor.info` gives the product, which picks the `Active` row
   of `.build.info` (a count other than one is reported, never guessed);
   that row's build key names `Data/config/<k[0:2]>/<k[2:4]>/<k>`, the
   build config, `key = value` text, with `root` (a content key) and
   `encoding` (a content key and an encoding key). The Battle.net agent
   writes `.build.info` and the configs; the game client reads them.
2. **The local index**, `Data/data/*.idx`: sixteen buckets, one file
   version per bucket on this install (1,042,994 entries for the whole
   install, every product together, read in 1.0 s); each 18-byte entry
   maps the first nine bytes of an encoding key to archive number, offset
   and size (the five-byte big-endian field: ten bits of archive, thirty
   of offset). The bucket of a key: `x = k[0] ^ … ^ k[8]`,
   `bucket = (x & 0xF) ^ (x >> 4)`. The header's hash and the entries'
   hash are checked, and the `4/5/9/30` spec is read from the header, so a
   half-written file is refused. `shmem` (recreated by the game client at
   start; a template after an agent update) and `index.lock.0` are never
   opened for writing, never created, never locked, and `shmem` is not read
   either: which `.idx` version wins when two of one bucket exist is
   **[verify]** (the spike found one; the reader takes the newest by version
   number, as CascLib does from memory, and says so in its report). The
   index files are read into memory and closed at once.
3. **The archive** `data.NNN` at that offset: a 30-byte record header,
   then **BLTE**: a chunk table and blocks, each with a mode byte: `N` raw,
   `Z` zlib, `4` LZ4, `F` recursive, `E` encrypted. The reader decodes `N`
   and `Z`; `4`, `F` and `E` raise typed errors (`UnsupportedBlockMode`,
   `EncryptedContent(key_name)`) and are reported, never guessed and never
   decrypted (ADR-0033: no key list, no `DBCache.bin` scraping). The spike
   met 4,775 blocks: `N` and `Z` only; a later census found `E` blocks too
   (M15-04, 2026-10-19: 19 Salsa20 blocks in 9 of 4,079 sampled records of
   the install's archives, every product's records the local index lists;
   `docs/LAB_FORMATS.md` §10, amendment of 2026-10-19), and still no `4` or
   `F`. An archive is opened for the length
   of one read and closed; no handle or memory map outlives a command, so
   the agent or the client can replace or extend a file at any time (on
   Windows an open handle or mapping would block that; a Windows `@live`
   check, **[verify]**).
4. **Encoding** (141.6 MB decoded in 0.2 s) maps a content key to its
   encoding keys: one content key may have several (an encrypted and a
   plain copy, for one), and the reader tries each against the index; a
   22-byte header, a page index, 4 KB pages; a lookup is a `bisect` over
   the page index and a scan of one page. No two-million-entry dictionary
   is built.
5. **Root** (50.5 MB, parsed in 0.6 s; **version 2** here, the format of
   11.1.0+) maps file data ids to content keys in blocks with locale and
   content flags; blocks for other locales and platforms are skipped (the
   `0x108` mask on macOS, `0x110` on Windows, and `LowViolence` `0x80`),
   the locale read from `.build.info`'s `Tags` column and the platform from
   the install's own layout (the client bundle on macOS, the executable on
   Windows), never a constant (L6); a file in a block flagged `Encrypted`
   (26 of 916) is tried through its other encoding keys before it is
   reported encrypted. Whether any of the player's own gear is encrypted
   is **[verify]**; none was met.

**Integrity for free.** The root's content key is the MD5 of the decoded
file, so every read is checked against it; on a mismatch the reader
re-opens the index once and reports if it persists, on every read, since
the agent (patches, background downloads, Scan and Repair) and the client
(streaming) both write `.idx` files and archives.

**Performance targets (§6.4, ADR-0020):** the index loaded in at most
2 s; open in at most 5 s; three hundred files read in at most 10 s;
resident memory measured and reported in the ticket (the spike: open 1.9 s
on Python 3.9; the 29.7 MB `humanmale_hd.m2`, its skin and three textures
in under 0.2 s; RSS not measured, expected a few hundred MB from the
encoding bytes and the two indexes; a sorted `array` with `bisect` is the
first optimisation). Missing a target is a stop-and-ask, not a C
extension. Windows is unmeasured: the research ran on macOS only.

**Four states for a file.** A file data id the plan asks for is *local*
(in the root and the index), *not in your client's local files* (in this
product's root but not in the index: content the client has not fetched;
whether and when it would is **[verify]**), *not part of this game
version's files* (a table row for content this product does not ship: not
in the root at all), or *encrypted*. The research sampled 300
`ModelFileData` rows: 178 local, 119 not in the root, 3 in the root but
not local; the 300 `TextureFileData` rows (128 local) were not split that
way, and 73 of the 77 `ChrModel` rows had local models (which races
Forever lets a player choose is not checked). So "the install streams" is
**[verify]** until the middle group is shown to be large; what is settled
is that the player's own gear, which the client has drawn, is expected
local, that the Lab never fetches from Blizzard's CDN (ADR-0033: no
network source is added), and that an outfit designer over arbitrary items
is out of both waves.
*Note, 2026-10-19 (M15-05, `lab/core/tests/fixtures/macos-70450/derived/casc-files.json`):* the reader's
census settles the question for this install at 70450. The middle state
holds 20 of the 1,243,524 ids the root gives for enUS on macOS (16 listed
only in rows with content bit `0x1`, whose content this client did not
fetch; 4 in one block flagged `Install`, meaning **[verify]**), and none of
300 sampled `ModelFileData` ids (157 local, 143 not in the root). The
spike's "3 in the root but not local" were probably `0x1` rows of ids
whose other row is local, which the reader now tries next. Whether a
partly downloaded install shows more is **[verify]**.

**What it writes:** nothing inside the install (L1). Decoded files go under
the data directory, `casc/<product>-<build>/`, keyed by product and build
(two products may share a version string) and never overwritten (L5): `wowlab casc cat --fdid N` writes `files/<fdid>-<ckey8>.bin` there by default (the content key's first eight hex digits in the name, so a patched file lands beside the old one; a repeat with the same key writes nothing and says so), `--out` may name another path inside the same folder or stdout, and the later modules share the same cache; both parts of the folder name are discovered strings checked as safe folder names. One subfolder is
exempt from never-overwrite: `characters/`, which holds derived output
that `char model` re-makes on request (§17.6).

**In the UI.** `wowlab ui` offers every command the CLI tree has (§15.6),
so `casc stat` appears in the Commands tab as a plain read when M15-06
merges, and `casc cat` and `char model` appear with their `--out` refused
as `looks page --out` is (owner ruling of 2026-10-15) and binary output to
stdout refused: in the UI they write only to their default place under the
data directory. Each ticket states this and gets `security-reviewer` for
the exposure.

### 17.2 `blp` — textures

BLP2: a 148-byte header (encoding 1 palette, 2 DXT, 3 BGRA; alpha depth 0,
1, 4 or 8; mip offsets and sizes), a 1024-byte palette, then mips. The
decoder returns mip 0 as RGBA rows and a PNG writer (stdlib: `zlib`,
`crc32`) writes it. DXT1 and DXT5 are what every character texture sampled
used. Two sources disagree on the DXT choice: the wiki says the alpha
type alone decides (0 → DXT1, 1 → DXT3, 7 → DXT5), wow.export says the
alpha depth decides first (`alphaDepth > 1 ? (alphaType == 7 ? DXT5 :
DXT3) : DXT1`); they differ for depth 8 with type 0, depth 1 with type 1, depth 4 with type 0, depth 0 with type 1, and depth 0 or 1 with type 7, so the decoder refuses, naming both values, any texture where the
two rules disagree, until a real texture settles it (**[verify]**; the
derived fixtures record both fields). Palette and BGRA are implemented
from the wiki with the 4-bit alpha case **[verify]**; BC5 (alpha type 11)
is refused with a typed error. The header's width and height bound every allocation (a
stated cap; hostile headers are refused). **Target:** a 1024² DXT1 decode
in at most 0.5 s (measured 0.15 s in pure Python).

### 17.3 `m2` — models and skins

The modern chunked M2 (`MD21`, `SFID`, `TXID`, `AFID`, `BFID`, `LDV1`,
`TXAC` and later chunks kept unread): the header's arrays at their fixed
offsets (vertices at 0x3C, 48 bytes each, Z-up; textures at 0x50, type and
flags; attachments at 0xF0), the skin file data ids from `SFID`, the
texture file data ids from `TXID` for type-0 textures. The `.skin` file:
the vertex lookup, the triangle indices, and the sections, whose
`skinSectionId` is the **geoset id** (`group * 100 + value`), with
`Level << 16` added to `indexStart` as wow.export does (the wiki says "to
startTriangle and alike"; at 70450 only the index start overflows, the
vertex lookup having 37,813 entries). The HD body models
on this build are version 272 (inside the wiki's table: MoP/WoD, and the
start of the 272–274 range for Legion to Shadowlands; a version above 274
is read with a note) with their bones and attachments inside the M2 (`humanmale_hd`: 215 bones, 45 attachments, 113 sections, 11 textures
with types 1, 6, 2, 15, 16, 18, 17, 19 and three type-0; no `SKID` chunk,
**[verify]** for every race). **No bones are
read in this wave:** the vertices are stored in the bind pose (the rest
pose, not the in-game stand; the wiki's M2 page, **[verify]**), so a static
render needs none and the player is told the render is the rest pose; a
posed render is a Wave 7 option (§17.8). Texture types 15 to 18 (guild
tabard colours and emblem) are left blank with a note, since the Lab holds
no guild data; type 2 (the cape) comes from the item's model material
resources.

### 17.4 The render plan

`wowlab_core.renderplan` turns a recorded character into the list of files
and rules a renderer needs, through the game tables the Lab already fetches
from wago.tools per build (ADR-0022, §6.6; about twenty tables, 23 MB of
CSV per build, cached by build). Inputs: the lab-addon's `identity`
(M15-01) and `customization` records, and the equipped items' item ids and
bonus ids from `char gear`.

- **Body:** `ChrRaceXChrModel` (race, sex) → `ChrModel` (its texture
  layout, its `DisplayID`) → `CreatureDisplayInfo` → `CreatureModelData`
  → the body M2's file data id.
- **Customization:** the recorded choices → `ChrCustomizationElement` →
  a geoset (`ChrCustomizationGeoset`, `type * 100 + id`) or a material
  (`ChrCustomizationMaterial` → `TextureFileData`, the diffuse row chosen
  by `UsageType` (**[verify]** which value), placed by
  `ChrModelTextureLayer` and `CharComponentTextureSections`), an element
  taken only when its `RelatedChrCustomizationChoiceID` is satisfied (face
  and skin materials depend on another choice). Skinned models, bone sets
  and display-info choices are listed as unsupported, as wow.export lists
  them. `ChrCustomizationCondModel` is not published for this build (HTTP
  400) and is skipped. Without a `customization` record (it exists only
  after a barber-shop visit with the addon on; the shop works on Forever
  since 70058, and `chr_model_id` may be absent because
  `GetViewingChrModel` returned nil, M11-29) the plan uses the model's
  default choices and says so, and a record older than a paid change that
  kept the race is stale without the Lab knowing (the addon README), so the
  report carries the record's date.
- **Gear:** item id and bonus ids → `ItemBonus` type 7 (APPEARANCE:
  `Value[0]` the appearance modifier, `Value[1]` the priority, lowest
  wins; from TrinityCore, **[verify]** against a real link) →
  `ItemModifiedAppearance` (modifier 0 preferred, else the lowest) →
  `ItemAppearance` → `ItemDisplayInfo` (at 70450 `ItemBonus` is 31 KB,
  so most links carry no bonus ids and use modifier 0): geoset groups per
  slot (the wow.export table after WoW Model Viewer: head 27 and 21,
  shoulders 26, shirt 8 and 10, chest 8, 10, 13, 22 and 28, belt 18, legs
  11, 9 and 13, feet 5 and 20, hands 4 and 23, back 15, tabard 12;
  **[verify]**), where a group value `v` selects geoset `group * 100 + 1 +
  v`, feet's `0` selecting `2` (the `+1` and the feet case are **[verify]**
  against wow.export's `calculateEquipmentGeosets`), attachment geoset
  groups, helmet visibility, model resources → `ModelFileData` filtered by
  `ComponentModelFileData` (race, sex, class, position), model textures →
  `TextureFileData`, body-texture pieces → `ItemDisplayInfoMaterialRes`
  (component section 0 to 8) filtered by `ComponentTextureFileData`; both
  filters fall back through `ChrModel`'s `ModelFallbackChrModelID`,
  `TextureFallbackChrModelID` and `HelmVisFallbackChrModelID` and the
  any-race, any-sex rows. Helmets hide hair and ears through
  `HelmetGeosetData`. `HelmetGeosetVisData` is not published for this build
  and is not needed; the appearance modifier is an id, not a table. **Transmog is not rendered:** the addon
  records none (`C_TransmogCollection` crashed Forever, M11-20), so the
  item's own look is drawn.
- **Geosets:** the default set is geoset id 0, every id of 100 or more
  with `id % 100 == 1`, and group 32, with groups 17 and 35 hidden
  (**[verify]**: wow.export compares decimal strings, so its hide rule also
  catches ids 17 and 35 in group 0, the hairstyles; the Lab states the
  rule numerically and does not port the quirk); then the customization
  geosets; then per slot the item's groups (`group * 100 + 1 + v`, the `+1`
  **[verify]**) with the sleeve and trouser priorities (gloves over chest
  over shirt for group 8; chest over shirt for group 10; chest over pants
  for group 13).
- **Attachments:** head → 11, shoulders → 6 and 5, back → 12, main hand →
  1, off hand → 2 or shield 0; the ranged slot (18: bow, gun, crossbow,
  wand, thrown; a relic has no model), which Classic-era characters carry,
  is listed as unsupported in this wave and shown in the report; in the bind pose an attached model sits at
  the attachment's position **[verify]**. "Collection" armour models (the
  modern chest, legs, boots and gloves) are skinned to the body and sit at
  the origin in the bind pose **[verify]**.
- **The report** lists every file by id in one of §17.1's four states, so
  the renderer and the player know what is missing, and carries the dates
  it rests on: the gear as of the record's last logout or `/reload`, the
  look as of the last barber visit.

### 17.5 The body texture

`wowlab_core.compositor` builds the body texture in Python so pytest grades
it: a canvas of the layout's size (2048×1024 for layout 103), the
customization materials drawn into their section rectangles in layer
order with their blend modes (alpha-over, multiply, overlay, screen as
wow.export's shader does; the full enum is **[verify]**), then the item
pieces over them. Rows are composed as byte slices. Hair and eyes are
separate materials and stay separate textures. **Target:** a 2048×1024
composite in at most 3 s.

### 17.6 `gltf` and `wowlab char model`

`wowlab_core.gltf` writes glTF 2.0 binary (`.glb`): one node per model
(the body with its collection pieces; each attached model at its
attachment position), POSITION, NORMAL, TEXCOORD_0 as float32, uint32
indices, one PNG material per texture, Y-up (the Z-up swap, axis signs
**[verify]** in the first viewer ticket). Stdlib only, about two hundred
lines; a round-trip parser in the tests checks the structure. `wowlab char
model [--character] [--account] [--root] [--flavor] [--out] [--json]` runs
the whole chain and writes
`<data>/casc/<product>-<build>/characters/<account>/<realm>-<character>.glb` plus its
PNGs, the folder and file names taken from the install's own folder names
and checked as safe names; `characters/` is re-made on request (the L5
exemption of §17.1), so a run after a gear change replaces the file.
`--json` is `CharModelReport` (the plan, every file's state, the sizes,
the timings, and the two as-of dates); the text output says "as of your
last logout or /reload" and "look as of <date>", and that the render is
the rest pose. It is a read of the install and a write of wowlab's own data
(no `guard`, no `--yes`); it appears in the Commands tab when it merges,
with `--out` refused there (§17.1, "In the UI"), and the Wave 7 viewer runs
it the same way.

### 17.7 What is known, and what is [verify]

Measured on the owner's install at 70450: the layout of `Data/`; root
version 2; 1,244,477 file data ids after locale and platform filtering
(*note, 2026-10-19, M15-05, `casc-files.json`:* that count keeps
`LowViolence`; the reader skips it and takes 1,243,524);
encrypted (`E`, Salsa20) blocks met in 9 of 4,079 sampled records of
the install's archives and no LZ4 or `F` block (`docs/LAB_FORMATS.md` §10,
amendment of 2026-10-19); the open and read timings; the streaming
counts; `humanmale_hd` and `humanfemale_hd` version 272 with bones
inside; layout 103's sections; every character texture DXT1 or DXT5 with
mips. From memory or from another project, **[verify]** until a test on
the install says so: which `.idx` version wins when two exist; whether LZ4
ever appears; whether any player gear is encrypted; the bonus-id
semantics (TrinityCore); the `+1` in the item geoset value; attachment
positions being model-space in the bind pose; collection models at the
origin; the blend-mode enum; the 4-bit palette alpha; the M2 vertex count
against the skin's (226,301 against 37,813: LOD skins, probably); the
axis signs for Y-up; why the client's `ecache/` folder exists; the DXT
choice enum; the HD body models of every race keeping their bones inside
(no `SKID`); the per-slot geoset-group table and the default geoset rule;
how a browser's CSP admits the vendored modules (Wave 7, ADR-0034);
whether the install streams at all (the middle of §17.1's four states;
*note, 2026-10-19, M15-05:* at 70450 it holds 20 of 1,243,524 ids and 0
of 300 sampled `ModelFileData` ids, so this install does not stream in
any way that matters here; a partly downloaded install is still
**[verify]**);
whether and when the client fetches a file it lacks; the Windows handle
question; the DXT rule conflict; the
`TextureFileData` usage type; whether the tables describe how Forever's
playable races look (the owner's look at the GLB settles it); whether
Forever has transmog in game at all; the rest pose.

### 17.8 Wave 7, sketched (the viewer; not M15 tickets)

ADR-0034: a pinned three.js (r185, the last release with official
minified builds, plus `GLTFLoader`, `OrbitControls` and the loader's two imports, `BufferGeometryUtils` and `SkeletonUtils`, from its `examples/jsm`, about 955 KB) vendored
inside the package and served as ES modules by the UI through an import
map, admitted by the CSP by hash (or by a scoped `'self'` on the owner's
ruling after the browser check, **[verify]**); a
token-checked route that serves the character's `.glb` and PNGs from the
data directory by a path the server builds; a viewer panel on the
Character tab (scene, orbit, lights) that runs `char model` as a plain
run; attachments; collection models and helmet hiding; the four file-state notices of §17.1 and the default-look notice; and, optional, the Stand
pose (bones, `AFID` animation files, skinning), an M of its own. The
outfit designer, transmog and animation stay out of both waves.

### 17.9 Tickets and order

M15-01 (the identity record) and M15-02 (the dressing-room link) are on
`main`'s backlog already. The rest, in `docs/BACKLOG.md`: M15-03 the
config and index reader, M15-04 BLTE, M15-05 encoding, root and
`CascSource.read` with the performance target, M15-06 `wowlab casc stat`
and `cat`, M15-07 BLP, M15-08 M2 and skin, M15-09 the render plan's body
and customization half, M15-13 its gear half, M15-10 the compositor,
M15-11 glTF and `char model`, M15-12 the wave review.
M15-03 → M15-04 → M15-05 → M15-06 are sequential (each reads through the
last); M15-07 and M15-08 need only M15-05; M15-09 (the body and the
customization) needs M15-01 and M15-05 (for `stat`) and the tables;
M15-13 (the gear chain) needs M15-09; M15-10 needs M15-07, M15-09 and
M15-13; M15-11 needs all of them. M15-02 runs beside the chain. M15-01,
M15-06 and M15-11 all edit `cli.py` and are dispatched so that no two are
in flight at once. Every parser ticket carries the three fixture
layers of ADR-0033, and the live tests are on the owner's list after each
merge.
