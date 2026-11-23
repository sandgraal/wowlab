# Lab — File format reference

The grammar reference for every client file `wowlab_core` parses.
`test-writer` derives graders from this document and from real fixtures,
never from an implementation.

**Authority order:** a real fixture beats this document; this document beats
memory. Where the two disagree, the fixture is right, this file gets a dated
amendment, and the disagreement is a finding in the PR. Items marked
**[verify]** are recalled from community documentation (wowdev.wiki,
warcraft.wiki.gg) and have not yet been checked against a capture from the
owner's install; M10-03 closes them.

Fixture index: `lab/core/tests/fixtures/README.md`.

## 1. `.build.info` (install root)

Pipe-separated text. Line 1 is a header; each later line is one installed
product. Header cells have the form `Name!TYPE:size`:

```
Branch!STRING:0|Active!DEC:1|Build Key!HEX:16|CDN Key!HEX:16|Install Key!HEX:16|IM Size!DEC:4|CDN Path!STRING:0|CDN Hosts!STRING:0|CDN Servers!STRING:0|Tags!STRING:0|Armadillo!STRING:0|Last Activated!STRING:0|Version!STRING:0|KeyRing!HEX:16|Product!STRING:0
us|1|<32 hex>|<32 hex>|…|…|tpr/wow|…|…|Windows x86_64 US? enUS speech?:Windows x86_64 US? enUS text?|…|…|12.1.5.65432|…|wow
```

- Column set and order vary by agent version. Parse by header name; keep
  unknown columns (`extra`). Columns the library reads: `Product`, `Version`,
  `Build Key`, `Branch`, `Active`, `Tags`.
- `Version` is `major.minor.patch.build`. `build` is the last component.
- The `?` characters inside the `Tags` cell are literal.
- A cell may be empty. A row may have fewer cells than the header (pad with
  empty) **[verify]**.
- Line endings and a possible UTF-8 BOM: record from the fixture.

## 2. `.flavor.info` (flavor folder)

Two lines: a header `Product Flavor!STRING:0` and the product code (`wow`,
`wow_classic`, `wow_classic_era`, `wowt`, `wow_beta`, …). The code joins to
`.build.info`'s `Product` column. The Forever beta's code is reported as
`wow_classic_beta` **[verify]**.

## 3. TOC files (`Interface/AddOns/<Addon>/<Addon>[_Suffix].toc`)

Line-oriented, UTF-8, optional BOM.

| Line | Meaning |
|---|---|
| `## Key: Value` | Directive. Key is case-insensitive; keep as found. Whitespace around `:` is insignificant. |
| `# text` (one `#`, or `##` without a colon) | Comment |
| blank | Ignored, preserved |
| anything else | A file to load, path relative to the addon folder; `\` and `/` both occur |

- `## Interface:` is one number or a comma-separated list
  (`## Interface: 120105, 50503, 11508`). Parse to ints.
- Known directives: `Title`, `Notes`, `Author`, `Version`, `SavedVariables`,
  `SavedVariablesPerCharacter`, `SavedVariablesMachine`, `Dependencies` /
  `RequiredDeps`, `OptionalDeps`, `LoadOnDemand`, `LoadWith`, `LoadManagers`,
  `DefaultState`, `IconTexture`, `IconAtlas`, `AddonCompartmentFunc*`,
  `Category*`, `Group`, `AllowLoad`, `AllowLoadGameType`, `OnlyBetaAndPTR`,
  and any `X-…`. Unknown directives are kept.
- Localized variants: `## Title-deDE: …`.
- File lines can carry bracketed conditions and variables in modern clients,
  e.g. `[AllowLoadGameType mainline] Foo.lua`, `Locales\[TextLocale].lua`,
  `[Family]\Bar.xml` **[verify exact spellings]**. The parser keeps the
  condition text and the path text separately and evaluates nothing.
- Suffix selection: a client loads `<Addon>_<Suffix>.toc` for its game type
  in preference to `<Addon>.toc`. Known suffixes: `Mainline`, `Classic`,
  `Vanilla`, `TBC`, `Wrath`, `Cata`, `Mists`; older files use `-` instead of
  `_`. Which suffix (if any) the Forever client prefers is **[verify]**; do
  not encode a guess.

## 4. SavedVariables (`WTF/Account/…/SavedVariables/*.lua`, `SavedVariables.lua`)

Written by the client's own serializer on logout and `/reload`, so the
output format is narrow and regular. The parser accepts that format and the
small superset below; it is not a Lua parser.

### 4.1 Grammar accepted

```
document   := { blank | comment | assignment }
assignment := NAME "=" value
value      := table | string | number | "true" | "false" | "nil"
table      := "{" { entry ("," | ";") } [ entry ] "}"
entry      := value                       -- positional
            | "[" (string | number | "true" | "false") "]" "=" value
            | NAME "=" value              -- hand-edited files only
comment    := "--" to end of line         -- kept, attached to the preceding entry when on the same line
string     := '"' … '"' | "'" … "'"
number     := decimal int | decimal float | exponent form | hex int (0x…) | leading "-"
```

`NAME` is a Lua identifier. `nil` is legal only as a top-level value. *(Narrowed 2026-11-14 by the amendment below: a positional `nil` inside a table, a hole, is also legal.)*

*Amended 2026-11-14 (owner's install, Forever 1.60.1.70938; contradicts the line above):* the client writes `nil` as a **positional** entry, a hole in a list, on its own line as `nil,`. Seen in the game's own files: `Blizzard_AuctionHouseUI.lua` (per character; `g_auctionHouseFilters.filters` reads `false, false, false, false, nil, true, …`) and `Blizzard_CombatLog.lua` (account; two holes between colour tables), in the account's `Auctionator.lua` (two holes back to back), and in the `.bak` copies `Blizzard_CombatLog.lua.bak` and `Auctionator.lua.bak`. The files show where the holes are written, not how the client reads them back: by the Lua 5.1 manual §2.5.7 (table constructors), a positional `nil` takes its index like any other positional entry, so the entries after it keep their numbers. The parser accepts a positional `nil`, keeps its text and position byte for byte (§4.4), and reports it as a value with no content (the hole), never as an error. A top-level `X = nil` stays accepted as before. Inside a table, a keyed `nil` (`["k"] = nil` or `k = nil`) and `nil` as a key are still unobserved and stay rejected.

### 4.2 What the client writes **[verify each against fixtures]**

```
⏎
MyAddonDB = {⏎
⇥["profileKeys"] = {⏎
⇥⇥["Name - Realm"] = "Default",⏎
⇥},⏎
⇥["list"] = {⏎
⇥⇥"first", -- [1]⏎
⇥⇥"second", -- [2]⏎
⇥},⏎
⇥[42] = true,⏎
⇥["scale"] = 0.8500000238418579,⏎
}⏎
OtherVar = "text"⏎
```

- The file starts with an empty line.
- Indentation is one tab per level. Every entry ends with a comma,
  including the last.
- The array part (keys `1..n`) is written positionally with a `-- [n]`
  comment; everything else is `["string"]` or `[number]`.
  A hole in the array part is written as a positional `nil,` (§4.1, amendment of 2026-11-14).
- Strings are double-quoted. Escapes seen: `\"`, `\\`, `\n`, `\r`, and
  decimal `\ddd` for other control bytes. UTF-8 is written raw. A string can
  contain `|c…|r` colour codes, `|T…|t` texture tags and `|H…|h` links; they
  are ordinary characters to the parser.
- Numbers: integers without a point; floats with up to 17 significant
  digits; exponent forms occur (`1e+15`). Non-finite values and negative
  zero: spelling differs by platform and client era (`inf`, `-inf`, `nan`,
  `1.#INF`, `-1.#IND`, `-nan(ind)` have all been reported). Add each spelling
  here with the fixture that shows it; an unlisted spelling raises.
- Line endings: record per platform from fixtures. The serializer reproduces
  whatever the source document used.
- `*.lua.bak` is the previous write of the same file, same format.

### 4.3 Rejected

Anything in `docs/LAB_PLAN.md` §6.4 "Rejected". The error carries line,
column and the offending token. Hostile-input graders are constructed
(labelled `constructed` in the test id): `function() end` as a value, a call
as a value, `setmetatable({}, {})`, string concatenation, arithmetic, a bare
identifier, an unterminated string, an unterminated table, depth 10 000, a
NUL byte, a lone surrogate escape.

## 5. `Config.wtf` and `config-cache.wtf`

One CVar per line: `SET name "value"`. Value is always quoted; an embedded
quote is not expected **[verify]**. `Config.wtf` lives at `WTF/Config.wtf`
(machine-wide); `config-cache.wtf` exists per account and per character.
Lines that do not match are kept as `Unknown`. Names are case-insensitive to
the client.

*Amended 2026-11-09 (M16-25; applies to §5–§7):* no capture of these files
starts with a UTF-8 BOM, so whether the client writes one is **[verify]**.
The parsers keep a leading BOM as its own prefix token, never part of line 1
(`docs/LAB_PLAN.md` §6.5).

Identity-bearing CVars the scrubber blanks: `accountName`, `accountList`,
`lastCharacterGuid`. The list is confirmed and extended from the first
capture **[verify]**; the deny-list lives in `wowlab_core.redact`
(`IDENTITY_CVARS`, moved there from `scripts/lab_capture.py` on 2026-10-14,
M13-09), which the scrub tool imports.

## 6. `bindings-cache.wtf`

`bind KEY ACTION` per line, e.g. `bind CTRL-1 ACTIONBUTTON1`,
`bind BUTTON4 "CLICK SomeAddonButton:LeftButton"`. Header or mode lines
exist in some versions **[verify]**; keep anything unmatched as `Unknown`.

*Amended 2026-11-12 (M16-05, the owner's launch-client session, Forever
`_forever_`, product `wowf`, build 1.60.1.70888, interface 16001, macOS).*
The persistence tests of `docs/handoffs/M14-binds-persist.md` were run on a
**character** file (the character uses its own key bindings; the account
`bindings-cache.wtf` is 0 bytes): runs A and B of that runbook, on the
character file:

| Run | `synchronizeBindings` | Edit made with the client closed | Worked after login? | Key Bindings panel | Edit still in the file after logout? | File rewritten at logout? |
|---|---|---|---|---|---|---|
| A | `1` (no line in any file; `GetCVar` returned `"1"`) | `ALT-I` → `CTRL-F11` for `ITEMTREE_OPEN` | yes | not recorded | yes | **no** (mtime unchanged) |
| B | `0` (`SET synchronizeBindings "0"` in `Config.wtf`) | `CTRL-F11` → `CTRL-F12` | yes | `CTRL-F12` | yes | yes (same content) |

So, on this client, **an offline edit of a character `bindings-cache.wtf`
survives login whether `synchronizeBindings` is on or off**. The server did
not put its own copy back. With sync on, the client left the file untouched
at logout; with sync off, it rewrote it unchanged. Line endings stayed CRLF.
The account file and the sync-on case for a binding changed *in game* on
another machine were not tested. Of the three `synchronize*` names probed,
`GetCVar("synchronizeBindings")` and `GetCVar("synchronizeConfig")` returned
`"1"` and `GetCVar("synchronizeMacros")` returned nothing. The character
`bindings-cache.wtf` exists when character-specific bindings are on. The
matching **[verify]** in `docs/LAB_FILE_MAP.md` is generated from
`filemap.toml` and is left for M16-125.

The same session answered M16-05's other questions. The lab-addon loaded on
the launch client (interface 16001), was not marked out of date, and wrote
its record at both logouts. The account `edit-mode-cache-account.txt` was
rewritten during the session, not at logout; whether a hand edit to it is
kept was not tested. Macro persistence and numbering are in §7.

## 7. `macros-cache.txt`

Records:

```
VER 3 0000000000000001 "Macro name" "INV_MISC_QUESTIONMARK"
/cast Spell
/use 13
END
```

Header fields: format version, macro id (hex), name, icon (name or file id).
Body is every line up to a line that is exactly `END`. Account macros live in
the account folder, character macros in the character folder.

*Amended 2026-11-12 (M16-05, same session as the §6 amendment).*
- **Persistence (`docs/handoffs/M13-07.md`).** With the client closed, a
  character macro body was changed (`/cast Shadow Ward` → `/use Shadow Ward`),
  then (`/sit` → `/dance`) on a second login. Both times `/macro` showed the
  edit after login, and the file kept it after logout. The client rewrites
  the file at logout. `synchronizeMacros` does not exist on this client
  (`GetCVar` returned nothing), so there was no sync setting to flip. The
  second run had `synchronizeBindings` off and `synchronizeConfig` on.
- **Macro index = position in the file.** Account macros take indices 1 to
  120 and character macros take 121 onward. The index follows each record's
  order in the file as the client wrote it at logout; the macro id does not
  decide it. The client writes macros with a blank name (`" "`) first, then
  by name, so the in-game window's order and the file's order agree.
  Observed with `GetMacroIndexByName`: account `wowlab acc` = 1; with 29
  character macros, `aa wowlab two` = 147 and `zz wowlab one` = 149, at
  positions 27 and 29 in the character file. Action slots 66, 67 and 68
  (the bottom-left bar, buttons 6 to 8) held them. `wowlab char macros`
  placed all three correctly from the files alone, so its candidate numbering
  holds for this build. Deleting macros in game removes their records at the
  next logout, and the indices close up.
- `MAX_ACCOUNT_MACROS` and `MAX_CHARACTER_MACROS` are nil on this client;
  120 per set is the community value, not observed.
- A new account macro was written as
  `VER 3 0000000000000001 "wowlab acc" "134400"`, CRLF, like the character
  file. Whether the client writes a macro with an empty body was not tested.

## 8. Combat log (`Logs/WoWCombatLog*.txt`)

Written only while combat logging is on (`/combatlog`), flushed in batches.

```
9/20/2026 21:14:03.123-4  COMBAT_LOG_VERSION,22,ADVANCED_LOG_ENABLED,1,BUILD_VERSION,12.1.5,PROJECT_ID,1
9/20/2026 21:14:05.871-4  SPELL_DAMAGE,Player-1234-0ABCDEF0,"Name-Realm-US",0x511,0x0,Creature-0-…,"Target",0x10a48,0x0,12345,"Spell Name",0x4,…
```

- Timestamp, two spaces, then a comma-separated record. Timestamp shape
  (year present, fractional digits, UTC offset suffix) changed across
  patches; record what each fixture shows **[verify]**.
- Fields: bare tokens, quoted strings (may contain commas), `nil`, hex ints,
  and nested groups with `[...]` and `(...)` (notably `COMBATANT_INFO`).
  The tokenizer returns nested lists for groups.
- Restricted environments in 12.x change what is logged in instances. What
  the Forever client logs is an M10-03 finding.

*Amended 2026-09-28 (M10-03 part 3; fixture
`macos/forever/Logs/WoWCombatLog-040126_021630.txt`, Forever 1.60.1, macOS,
one solo open-world fight against one NPC, 79 lines, no instance, no group;
file name and timestamps shifted by the capture tool, so neither is real):*
the Forever client writes a combat log. What follows is what this one file
shows; anything wider is **[verify]**.

- File name `WoWCombatLog-MMDDYY_HHMMSS.txt`, matching the first line's
  local time. Whether the client starts a new file each time logging is
  turned on, and whether the header repeats inside a file, are **[verify]**.
- Header `COMBAT_LOG_VERSION,22,ADVANCED_LOG_ENABLED,1,BUILD_VERSION,1.60.1,PROJECT_ID,18`.
  `BUILD_VERSION` is the patch only (the retail example above is
  illustrative; retail writing the patch only is community documentation
  **[verify]**), so a log does not identify its four-part version. This
  install has run 1.60.1 as 69913, 69977 and 70009, and wago lists earlier
  1.60.1 builds. The install's build is the build at capture time, not
  necessarily the one that wrote the log: a log recorded before a patch came
  from an earlier build. Report a log's build as the patch alone, or as
  unknown, never as the install's current build. `PROJECT_ID` is 18, not 1,
  although Forever is reported to use the modern API; whether it equals
  `WOW_PROJECT_ID` is **[verify]**, and nothing infers an API or flavor from
  it.
- Timestamp shape `M/D/YYYY HH:MM:SS.mmm-4`: month not zero-padded, four-digit
  year, the hour always two digits, three fractional digits, local time with
  the UTC offset written as whole hours (verified 2026-09-24 on the owner's
  real logs by a count-only check). Day padding is unverified: no real log
  had a day below 10. The capture tool writes the shifted month and day
  unpadded and the hour padded, whatever the original was, so this fixture's
  single-digit day (`4/1/2026`) is the tool's, not the client's. The
  tokenizer accepts a one- or two-digit day, padded or not. The fraction and
  the offset suffix are copied from the real log. A positive or half-hour
  offset, and UTC itself, are **[verify]**.
- Unit names are `"<Name>-<Realm>-"` (all 77 player unit names). The third
  component is the region on retail per community documentation
  **[verify]**; it is empty on every name here, for reasons unknown. The
  realm component matches the retail-style `<ACCOUNT>/<Realm>/` folder name
  with its spaces removed, as checked against the real names at capture (the
  committed pseudonyms come from different capture runs and do not show it);
  whether hyphens and apostrophes are also dropped is
  **[verify]**. The owner's character appears as `<First>-<Realm>-`, with no
  second name. A unit name's second component is a realm; a
  `<First>-<Second>` folder's is not (`docs/GLOSSARY.md`, Second name).
  Whether first names are unique on a realm is **[verify]**; key units by
  GUID.
- An absent unit is `0000000000000000,nil,0x80000000,0x80000000`;
  `0000000000000000` also fills the advanced block's owner slot. Raid flags
  are `0x80000000` on every unit, present or absent, where the example above
  shows `0x0`; the meaning is **[verify]**. Unit flags: owner `0x511`, the
  NPC `0x10a48`. Per community documentation **[verify]**, unit flags
  describe a unit relative to the logging player (affiliation, reaction,
  control, type) and include a bit for that player's current target, so the
  same NPC can log other values. Do not key on them. *Amended 2026-10-17
  (M13-08):* do not key on the whole value; the affiliation field
  (`& 0xF`), the type field (`& 0xFC00`) and, on the **[verify]**
  assumption that a charmed player logs another controller, the control
  field (`& 0x300` = `0x100`, "controlled by a player") select the logging
  player's own casts (see the 2026-10-17 entry under Amendments below).
- Records seen: `SPELL_CAST_START`, `SPELL_CAST_SUCCESS`,
  `SPELL_CAST_FAILED`, `SPELL_AURA_APPLIED`, `SPELL_AURA_REFRESH`,
  `SPELL_AURA_REMOVED`, `SPELL_DAMAGE`, `SPELL_PERIODIC_DAMAGE`,
  `SPELL_HEAL`, `SWING_DAMAGE`, `SWING_DAMAGE_LANDED`, `SWING_MISSED`,
  `UNIT_DIED`, `PARTY_KILL`, `ZONE_CHANGE`, `MAP_CHANGE`.
- Advanced parameters on `SPELL_CAST_SUCCESS`, `SPELL_DAMAGE`,
  `SPELL_PERIODIC_DAMAGE`, `SPELL_HEAL`, `SWING_DAMAGE` and
  `SWING_DAMAGE_LANDED`; none on cast starts, cast failures, auras, misses,
  deaths or kills. Field names are not established **[verify]**.
- The advanced block is 19 fields wide on all 56 records that carry it. It
  describes the source on `SPELL_CAST_SUCCESS` and `SWING_DAMAGE`, and the
  target on `SPELL_DAMAGE`, `SPELL_PERIODIC_DAMAGE` and `SWING_DAMAGE_LANDED`
  (`SPELL_HEAL` is self-cast here, so undetermined). Its last field is `1`
  for the player and `9` for the NPC; do not read it as character level. The
  owner slot is `0000000000000000` because this log has no pet or guardian.
- `SWING_DAMAGE_LANDED` follows every `SWING_DAMAGE` here (11 each; same
  source, target and amount), but it is not a copy. The advanced block of
  `SWING_DAMAGE` describes the attacker; that of `SWING_DAMAGE_LANDED`
  describes the unit hit. On the killing blow the third damage field is `0`
  on one and `-1` on the other. The two need not be adjacent: the killing
  blow's `SWING_DAMAGE_LANDED` comes four lines after its `SWING_DAMAGE`,
  after `PARTY_KILL`. Counting both double-counts melee damage; which one a
  total should use is **[verify]**.
- Tokens: `1`/`nil` flags; a bare `ST` after spell damage fields, none after
  swing fields; bare `BUFF`/`DEBUFF`; `SPELL_CAST_FAILED` ends in a quoted
  reason, client-localized per community documentation **[verify]**;
  `UNIT_DIED` and `PARTY_KILL` end in one extra field. CRLF on every line,
  the last included.
- Still open: `COMBATANT_INFO`, every `[...]`/`(...)` group, and any quoted
  string holding a comma or a quote (none here: every quoted string in this
  log is a plain name). M10-13 grades these on constructed lines until a
  boss-pull log is captured. Also still open: values other than `ST` in that
  slot, pets and guardians (the advanced block's owner slot), instance
  logging, group logs, and Windows.

## 9. wago.tools responses

Recorded, not specified. The fixture for the builds endpoint and for one
small table's CSV export are the reference for `gamedata`
(`docs/DATA_SOURCES.md`).

## 10. The install's archives: `.build.info`, `Data/config`, `.idx`, `data.NNN`, BLTE, encoding, root (M15-03 to M15-05)

Reference: https://wowdev.wiki/CASC, https://wowdev.wiki/TACT and
https://wowdev.wiki/BLTE; wow.export (MIT) as the cross-check. Measured on
the owner's install at 1.60.1.70450 on 2026-10-19 (read-only).

- **`.build.info`**: a pipe-separated table with `Name!TYPE:n` headers;
  `Build Key` names the build config at `Data/config/<k[0:2]>/<k[2:4]>/<k>`,
  `key = value` text; `root` is a content key, `encoding` a content key and
  an encoding key. The Battle.net agent writes `.build.info` and the
  configs; the product row is picked by `.flavor.info`'s product.
- **`Data/data/*.idx`**: a header (hash size, hash, `Unk0 = 7`, bucket,
  the spec `4/5/9/30`), then at the next 16-byte boundary the entries
  block (size, hash) of 18-byte records: nine key bytes (the first nine of
  the encoding key), a five-byte big-endian offset field (ten bits archive
  number, thirty bits offset), a four-byte little-endian size. Sixteen
  buckets, shared by every product in the install (the counts below are
  for the whole install); the bucket of a key: `x = k0 ^ k1 ^ … ^ k8`,
  `bucket = (x & 0xF) ^ (x >> 4)`. `shmem`, recreated by the game client at
  start and only a template after an agent update, lists the current
  version per bucket **[verify]**; the Lab neither reads nor writes it and
  takes the newest version per bucket. The agent (patches, repair) and the
  client (streaming) both write the index files and the archives. Also present: `index.lock.0`, an `.lru` file,
  `Data/indices/*.index` (CDN indexes, unused locally), `ecache/`
  (**[verify]** what it is).
  *Amended 2026-10-19 (M15-03; derived fixture
  `lab/core/tests/fixtures/macos-70450/derived/casc-index.json`, read from
  the owner's install at 1.60.1.70450; reference only, no change to the
  lookup).* The files are named `<bucket:2><version:8>.idx` in lowercase
  hex, one version per bucket here (versions 0x5e to 0x68). The 16-byte
  header is: u16 version 7, u8 bucket, u8 extra byte (0 in every file;
  meaning **[verify]**), the spec `4/5/9/30`, then a u64 that is
  1,098,437,885,952 = 1023 × 2^30 in every file. That cannot be one
  archive's size when offsets have 30 bits; its meaning is **[verify]**.
  The header hash is lookup3 `hashlittle`, seed 0, over the 16 header
  bytes. The entries hash is the `pc` of `hashlittle2` chained over each
  18-byte entry in turn, each seeded with the previous `(pc, pb)`,
  starting from `(0, 0)`. Each file is 1,245,184 bytes, zero after the
  entries. Keys rise strictly with no repeats (other builds **[verify]**).
  An entry's size includes the archive's 30-byte record header. The local
  build config's MD5 equals its name. **The bucket rule above has an
  exception.** Each file also holds one entry per archive (65 per file,
  1,040 in all) whose key's bucket is the one *before* the file's. Each is
  size 30 at offset 30 × the file's bucket. Its key is two varying bytes
  then `00 55 21 69 b0 19 89`. The 30-byte record at that offset repeats
  the key, reversed, in its hash field, and no BLTE follows it. These are
  the record headers at the head of every archive ("segment headers", from
  memory, **[verify]**). Every other entry (1,041,954) is in its own
  bucket. A lookup searches only the key's own bucket, so it never finds
  these, and none of them holds a file's data.
- **`data.NNN`**: at the index offset a 30-byte record header (the BLTE
  hash reversed, size, flags, two checksums), then BLTE of `size - 30`.
- **BLTE**: `BLTE`, a big-endian header size; when non-zero, a chunk table
  (`0x0F`, a 24-bit count, then per block compressed size, decompressed
  size, MD5) and the blocks, each starting with a mode byte: `N` raw, `Z`
  zlib, `4` LZ4 (**[verify]** whether WoW ever uses it; refused), `F`
  recursive (deprecated; refused), `E` encrypted (key name, IV, Salsa20 or
  ARC4; refused and reported, never decrypted). Met at 70450: `N` and `Z`
  only.
  *Amended 2026-10-19 (M15-04; derived fixture
  `lab/core/tests/fixtures/macos-70450/derived/casc-blte.json`, read from
  the owner's install at 1.60.1.70450; the reader follows the fixture, and
  nothing the ticket delivers changes. The census below walks the whole
  local index, which lists every product's records, and possibly records
  of earlier builds the agent has not cleared, from memory **[verify]**;
  "the install's archives" below means that, not 70450's files alone).*
  **The 30-byte record header** is
  the hash field (16 bytes), a u32 little-endian size that includes the 30
  bytes, two flag bytes, checksum A (u32 LE) and checksum B (u32 LE). Read
  back to front, the hash field starts with the nine bytes the index
  holds. In most records the other seven are zero; in some (71 of the
  4,079 sampled) they are the rest of the encoding key. Checksum A is lookup3
  `hashlittle` of the 22 bytes before it, seed `0x3D6BE971`, on every
  record sampled. Checksum B depends on the record's place and was not
  reproduced (**[verify]**); the reader reports it and does not check it.
  The flag bytes are `00 00` on every data record sampled and `01 00` on
  the archives' segment headers, whose hash field is all sixteen bytes,
  ending `…00552169b0198954679edc74b430` read back to front (meaning
  **[verify]**). **The encoding key of a chunked file is the MD5 of its BLTE
  header**: the magic through the last chunk row, not the whole stored
  file. This holds for the encoding file, whose full key the build config
  names, for every whole-key record header, and for the nine index bytes
  of every one of the 4,079 records sampled (the census's
  `header_md5_starts_with_key`). Every record sampled has a chunk table (flag `0x0F`,
  header size `12 + 24 × blocks`; none without one) whose stored sizes add
  up to exactly the record. A chunk row's MD5 is of the block's stored
  bytes, mode byte included. **`E` blocks are met in the install's
  archives** (the "`N` and `Z` only" above was the research spike's 4,775
  blocks of the files it read). A census of every 256th own-bucket record
  (4,079 records, 6,510 blocks) found 5,520 `Z`, 971 `N` and 19 `E`
  blocks, the `E` ones in 9 records under two key names, and no `4` or
  `F`. Every one of the 19 `E` blocks has a key-name length of 8, an IV
  length of 8 and cipher `S` (Salsa20) (the census's `e_block_shapes`);
  the IV length of 4 that is sometimes documented was not met, and both
  lengths are accepted by community readers, from memory **[verify]**.
  The key name is reported as its eight bytes read as a little-endian
  number, in sixteen hex digits, which is how community tools print it
  (CascLib and wow.export, from memory; **[verify]**); nothing ties the
  two names met to a Classic-era build. A read-only survey of the first 42
  bytes of every one of the 1,041,954 own-bucket records on the same day
  (seen on the run, not recorded in the fixture) found every header
  matching its index key and checksum A, at most 913 blocks in one chunk
  table, and **one record that is not BLTE**, stored as it is with the
  magic `PA`: its index key is the first nine bytes of the build config's
  `patch` value, so it is the patch manifest; the reader refuses it as
  unsupported. The encoding file is 7
  blocks (`N Z N N N N Z`); it read and decoded in 0.27 to 0.36 s on the
  owner's machine across the runs of the day, 0.23 to 0.32 s of it
  decoding.
- **Encoding**: a 22-byte header (`EN`, version 1, hash sizes, page sizes
  in KB big-endian, page counts, flags, ESpec block size), the ESpec
  strings, a page index of first content key plus MD5 per page, then 4 KB
  pages of entries (key count, a 40-bit big-endian size, the content key,
  the encoding keys: one content key may have several, an encrypted and a
  plain copy for one). 141.6 MB decoded at 70450.
- **Root**: magic `MFST` (bytes `TSFM`), header size, version, total and
  named file counts; blocks of (count, flags, int32 file-id deltas with the
  first absolute, MD5 content keys, optional 64-bit name hashes). Version 2
  (11.1.0+): locale flags, two flag words and a byte combined as
  `f1 | f2 | (f3 << 17)`; `Encrypted` is `0x8000000`, `LowViolence` `0x80`; a macOS client skips
  `flags & 0x108`, Windows `flags & 0x110`, and wow.export skips
  `LowViolence` too; enUS is locale `0x2`. At 70450:
  version 2, 2,433,102 records in 916 blocks, 26 flagged encrypted,
  1,244,477 ids after filtering. The content key is the MD5 of the decoded
  file.
  *Amended 2026-10-19 (M15-05; derived fixture
  `lab/core/tests/fixtures/macos-70450/derived/casc-files.json`, read from
  the owner's install at 1.60.1.70450; the reader follows the fixture).*
  **Encoding:** the header reads `EN`, version 1, hash sizes 16 and 16,
  pages of 4 KiB, 20,647 content-key pages and 13,554 encoding-key spec
  pages, flags 0, an ESpec block of 393,134 bytes; the pages end 61 bytes
  before the decoded size the build config gives (the encoding file's own
  ESpec string, from memory **[verify]**). Every page's MD5 matched its
  page index row. Of 2,209,148 content keys, one has two encoding keys and
  the rest one. **Root:** the version-2 block header is count, locale
  flags, two flag words and a flag byte, in that order (17 bytes); the
  blocks give rising ids, each delta at least 0. Taken for enUS on macOS:
  454 of 916 blocks (446 skipped for the locale, 6 for the platform mask,
  10 for `LowViolence`), 1,341,067 rows, 1,243,524 unique ids. **The
  1,244,477 above is the count with `LowViolence` kept**; the reader skips
  it, as the ticket and wow.export do. 97,543 ids have rows in two blocks
  taken; 96,919 of those ids differ in content, and in almost every case
  one of the two blocks carries bit `0x1` of the content flags (meaning
  **[verify]**; it is not one of the flags named above). Checked against the
  local index, the content of the `0x1` row is absent for 96,382 of them
  and the other row's present, and no id has only its `0x1` row local
  when the other is absent: this client did not fetch the `0x1` rows'
  content for 96,382 ids (22 have both rows local), so the reader tries rows without the bit first (`casc`'s
  `ROOT_FLAG_1`). 515 more ids have two local rows of different content,
  neither with `0x1`; in every one the first row in the file is from a
  narrow-locale block (`0x202`, enUS and enGB, with at most two more
  locales) and the second from the all-shipped-locales block (`0x173f6`, which lacks enCN, enTW
  and itIT). The
  reader returns the first, wow.export's rule from memory; whether the
  client picks the narrower block too is **[verify]**. Rows in blocks
  flagged `Encrypted` are tried after plain ones; one such file at 70450 (id 874570) has only an encrypted copy,
  under a key name not met in M15-04's census (`4A65D29877A83715`).
  **Streaming (§17.1's middle state):** of the 1,243,524 ids taken, 20
  have no copy in the local index: 16 listed only in `0x1` blocks and 4 in
  one macOS-only block (`0x10660014`) whose flags include `0x4`, `Install`
  in wowdev.wiki's table from memory (**[verify]**: possibly files the
  Battle.net app writes loose outside the archives); a
  sample of 300 `ModelFileData` ids splits 157 local, 143 with no row
  taken for enUS on macOS, none in the root but absent (the research spike's 3 were probably the
  `0x1` rows of ids whose other row is local). The reader's and
  `scripts/casc_facts.py`'s `not_in_build` covers both an id with no root
  row and one listed only in skipped blocks (another locale, the other
  platform, `LowViolence`), which is wider than §17.1's definition; M15-06's
  wording review starts from it. **Locale:** `.build.info`'s
  `Tags` cell is `:`-separated groups of space-separated tokens, each
  group ending in a locale and `speech?` or `text?`; the reader filters
  the root by the `text?` group's locale alone (enUS here; the `speech?`
  group's stands in when there is no `text?` group), reports the `speech?`
  group's locale beside it (enUS here too) and names it in `stat`'s note
  when a file is listed only for it, and never records the rest of the
  cell, which holds a geo-IP token. Such a file is still reported
  `not_in_build`; its note leads with "listed for your speech language
  (X), which this reader does not filter by" and does not say the client
  skips it. Whether the client loads a file listed only for its speech
  locale (spoken audio, for example) is **[verify]**.
  *Amended 2026-11-18 (M15-06 domain review; `docs/LAB_PLAN.md` §17.1, the
  note of 2026-11-18):* the state is still `not_in_build`, but its words are
  now "listed only for your speech language, which wowlab does not read",
  and its note begins "your speech language is X; wowlab reads the files
  for your text language (Y) alone". An id listed only in other skipped
  blocks (another locale, the other platform, `LowViolence`) is worded "not
  among the files this client loads for your language and platform"; "not
  part of this game version's files" is kept for an id with no root row.

## 11. BLP textures (M15-07)

Reference: https://wowdev.wiki/BLP. `BLP2`, version 1, a 148-byte header:
colour encoding (1 palette, 2 DXT, 3 BGRA), alpha depth (0, 1, 4, 8),
alpha type, has-mips, width, height, sixteen mip offsets and sizes; then a
1024-byte palette (256 BGRX). The DXT choice: the wiki says the alpha type decides (0 DXT1, 1 DXT3, 7
DXT5), wow.export says alpha depth first (`alphaDepth > 1 ? (alphaType ==
7 ? DXT5 : DXT3) : DXT1`); the two differ for depth 8 with type 0, depth 1 with type 1, depth 4 with type 0, depth 0 with type 1, and depth 0 or 1 with type 7 (**[verify]**; the Lab refuses such a texture naming both);
some stored mip sizes are wrong, so the block size is computed. Palette
alpha: 1 bit per pixel LSB-first, 4 bits (**[verify]**, the wiki is
incomplete), or 8 bits. BC5 (alpha type 11, the header's "preferred format" byte) is refused. Every
character texture sampled at 70450 was DXT1 or DXT5 with mips, 128² to
512².

## 12. M2 models and `.skin` files (M15-08)

Reference: https://wowdev.wiki/M2 and https://wowdev.wiki/M2/.skin. The
chunked form: `MD21` holds the whole MD20 blob with offsets relative to its
data; `SFID` the skin file ids (views, then LODs); `TXID` the texture file
ids (type-0 textures); `AFID`, `BFID`, `SKID`, `LDV1`, `TXAC` and the
later chunks (`EXP2` … `TEXL`) are kept unread. MD20 header arrays
(`count, offset`): bones at 0x2C, vertices at 0x3C (48 bytes: position,
four bone weights, four bone indices, normal, two UV pairs; Z-up),
skin-profile count at 0x44, textures at 0x50 (16 bytes: type, flags, a
filename array; types 1 body skin, 2 object skin, 6 hair, 19 eyes, …),
materials at 0x70, texture combos at 0x80, attachments at 0xF0 (id, bone,
position), the attachment lookup at 0xF8. The wiki's version table runs to
274; the HD body models at 70450 are version 272, inside it (MoP/WoD and the
start of the Legion to Shadowlands range), with bones and attachments
inside the M2 and no `SKID` (**[verify]** for every race).
`.skin`: `SKIN`, the vertex lookup (uint16), the indices (uint16
triangles), bones, the sections (48 bytes: `skinSectionId` = the geoset
id as `group * 100 + value`, level, vertex start and count, index start
and count, …; `level << 16` is added to the index start, "and alike" per
the wiki; at 70450 only the index start overflows), the batches
(24 bytes: flags, priority plane, shader id, section index, geoset
index, colour index, material index, material layer, texture count, texture
combo index, …). `humanmale_hd` skin 0 at 70450: 113 sections, 147,966
indices, levels 0 to 2, a 37,813-entry identity lookup against 226,301
header vertices (**[verify]** why: LOD skins, probably; *note, 2026-11-17,
M15-08:* settled for `humanmale_hd` and `humanfemale_hd` at 70959, where
the M2's vertices are the seven skins' sets one after another; whether a
LOD skin's lookup is relative to its set and what other models do stay
**[verify]**, amendment of 2026-11-17).

## 13. QuestieDB's data (third-party addon data; M22-08)

QuestieDB is the database the Questie addon reads. `wowlab lookup`
(`wowlab_core.questiedb`, `wowlab_core.lookup`) reads the player's own
installed copy, read-only, and executes none of it (L3). No byte of it is
committed: the tests run on constructed slices built in this encoding
(`lab/core/tests/_questie_build.py`) and an owner-run `@live` test reads the
installed copy (`test_questiedb_live.py`). The facts below are from the
owner's install of QuestieDB 1.1.4 (`QuestieDB_Forever.toc`, 18.8 MB,
`## X-Mode: baked`, `## X-Contract-Version: 4`), read on 2026-11-23.

The ticket's **[verify]**, "QuestieDB keeps its tables as Lua strings the
addon compiles at run time", is **settled, 2026-11-23: no, for the entity
data.** Quests, NPCs, objects and items live in the TOC itself, as
metadata the client hands to the addon (`C_AddOns.GetAddOnMetadata`) and
the addon decodes with the client's `C_EncodingUtil`
(`src/read/baked.lua`). Only the drop-chance tables are Lua strings
compiled with `loadstring`, and their text is a literal table.

**The baked store.** Every value is a `## X-...` directive of the TOC (§3).

- A value that is exactly `~N~` is a chunk header: the value is the
  directives `<key>-1` … `<key>-N` joined in order. Base64 never starts
  with `~`. The owner's largest is 79 parts (`X-Item-IDS`).
- `X-<Kind>-IDS` (Kind `Item`, `Npc`, `Quest`, `Object`): base64 of a zlib
  stream (`C_EncodingUtil.DecompressString(…, 1)`; the stream begins `eJ`,
  `78 9c`) of a CBOR array of the kind's ids. The owner's: 23,978 items,
  15,564 NPCs, 6,080 quests, 7,471 objects.
- `X-<Kind>-<id>-S`: base64 of a CBOR value, the entity's scalar row,
  keyed by field number: a map, or an array when the stored numbers run
  1..n (1,916 NPC rows and 1,726 object rows are arrays). `p`, a byte-string
  key, is the presence mask: bit `n - 1` set when table field `n` is
  stored. One NPC id in the header (of 15,564) has no row.
- `X-<Kind>-<id>-<n>`: base64 of CBOR, table field `n`, read only when its
  presence bit is set.
- CBOR as written: unsigned and negative integers up to 64 bits (a quest's
  race mask is 8,589,934,770), byte strings for every string (major type 2,
  not 3), definite-length arrays and maps, `null` (simple 22) for a hole in
  an array, doubles for coordinates. No tag, no indefinite length, no
  `undefined` was seen; the reader refuses them.
- Field numbers are the addon's (`src/meta/<kind>Meta.lua`). Read by
  `lookup`: item 1 name, 2 NPC drops, 3 object drops, 4 container items,
  5 the quest it starts, 6 quests that reward it, 9 item level, 10 required
  level, 14 vendors; NPC 1 name, 4 and 5 levels, 6 rank, 7 spawns
  (`{zone: [[x, y], …]}`, x and y map percentages; `-1, -1` inside an
  instance), 9 zone, 10 and 11 quests started and ended, 13 friendly to
  (`A`, `H`, `AH`), 14 title; quest 1 name, 2 starters and 3 finishers
  (`{1: NPCs, 2: objects, 3: items}`, as a map or an array with holes),
  4 required level, 5 level, 17 zone (positive: an AreaTable id; negative:
  a quest sort); object 1 name, 2 and 3 quests, 4 spawns, 5 zone. Zones are
  AreaTable ids, named from the game table (§9).

**The drop tables.** The TOC lists `support\<flavor>\DropTables\…lua`
files. `classicItemDrops.lua` assigns two Lua long strings,
`QuestieClassicItemDrops.wowheadData = [[return { … }]]` (1,219 items) and
`.cmangosData` (813 items): item id → NPC id → percent. The text after
`return` is a table constructor of numbers and comments only, so it is
parsed by `luadata` (§4) as data; anything else in that shape is left out.
They cover the items quests ask for, not every drop.

**Left out, by name** (listed in `lookup --json`'s `questiedb.left_out`
and behind `--notes`):

- the corrections under `src\corrections\…`, Lua functions the addon runs
  per character in game (the fixed ones are already folded into the store,
  as `foreverItemFixes.lua` says: "Static body stripped at package time");
- `itemDropCorrections.lua`, Lua code that names Questie's constants
  (`DropDB.correctionKeys.WOWHEAD`);
- `X-l10n-<locale>-<Kind>` blocks, the names in other languages: data, not
  needed for the English names the rows hold.

## Amendments

Dated entries, newest last. Each names the fixture that prompted it.

- **2026-09-21, §3, no fixture yet (M10-02 domain review).** Directives
  missing from the known list, from reviewer memory and all **[verify]**:
  `LoadSavedVariablesFirst`, `UseSecureEnvironment`, `AllowAddOnTableAccess`,
  `LoadFirst`, the singular `OptionalDep` and `RequiredDep`, `Dep`, and the
  legacy suffix `-BCC` (`## Interface-BCC:`). `-WOTLKC` is also reported as a
  legacy Wrath suffix **[verify]**; it is on neither list yet. `scripts/lab_capture.py` treats
  the §3 list as the client's closed vocabulary (those keys are never
  rewritten by the scrubber; any other key is ordinary text), so a directive
  found in a capture that is on neither list is added to both.
- **2026-09-21, §5, no fixture yet (M10-02 security and domain reviews).**
  The scrubber's identity deny-list is now eight names: `accountName`,
  `accountList`, `lastCharacterGuid`, `realmName`, `lastSelectedClubId`,
  `Sound_OutputDriverName`, `Sound_VoiceChatInputDriverName`,
  `Sound_VoiceChatOutputDriverName`. The last five come from reviewer memory
  (audio device names often carry the owner's name; a club id identifies a
  guild or community) and are **[verify]** against the first capture, which
  may also add names. `portal` stays. The tool blanks the value and keeps
  the line, so an empty-valued `SET` line in a fixture is a scrub artefact.

- **2026-09-22, §1, stage-0 capture of the Forever beta, macOS, build 1.60.1.69913 (M10-03; files staged, committed with this ticket).** `.build.info` has the 15 header cells of the §1
  example, in that order, and one row of 15 cells (a row shorter than the
  header is still unobserved, **[verify]** stays). Five cells are empty:
  `Install Key`, `IM Size`, `Armadillo`, `Last Activated`, `KeyRing`. LF, final
  LF, no BOM. `Branch` `us`, `Version` `1.60.1.69913`, `Product`
  `wow_classic_beta`. The `Tags` cell carries tokens the example lacks:
  `OSX x86_64 US? acct-USA? geoip-US? enUS speech?:…` (the `?` are literal).
  `Build Key` equals wago.tools' `build_config` for that build; `CDN Key` does
  not equal its `cdn_config`, so nothing joins on the CDN key. The install
  holds only this product, so a multi-row file is still unexercised.
- **2026-09-22, §2, same capture.** `.flavor.info` is exactly
  `Product Flavor!STRING:0\nwow_classic_beta\n` (41 bytes, LF, no BOM). The
  Forever beta's product code `wow_classic_beta` and its flavor folder
  `_classic_beta_` are **resolved**. `Config.wtf` also carries
  `SET agentUID "wow_classic_beta"`.
- **2026-09-22, §3, same capture (DBM-Challenges TOC; a Baganator TOC was read locally and not committed).** A bracketed
  load condition can *follow* the path, after one space:
  `Shadowlands\Torghast.lua [AllowLoadGameType standard]` (20 lines); the
  directive form is `## AllowLoadGameType: mists, standard`. `mainline` was
  not observed; `[TextLocale]` and `[Family]` stay **[verify]**. A directive
  can have no space after the colon (`## Title:|cff…`). The committed TOC is
  CRLF with a final CRLF and no BOM, and carries raw UTF-8 in localized keys.
  Observed in the uncommitted Baganator TOC only (so **[verify]** against a
  committed fixture): `##` directives after a blank line, so directive
  parsing must not stop at the first blank, and
  `## Interface: 120100, 16001, 50504, 38001, 20506, 11509`. An addon's
  `## Interface:` list is what its author claims, not client evidence. No
  installed addon ships a suffixed TOC, so Forever's preferred suffix stays
  **[verify]**. No directive outside the known list appeared.
- **2026-09-22, §5, same capture.** CVar names can contain `-`
  (`SET CACHE-WQST-QuestV2RecordCount "7760"`, eight such lines), and values
  can contain raw control bytes (0x02 in five `config-cache.wtf` lines), so a
  name is any run of non-space bytes and a value any bytes other than `"` and
  a line break. No embedded quote in 181 `SET` lines. Present and blanked by
  the scrubber: `accountName`, `accountList`, `Sound_OutputDriverName`
  (Config.wtf), `lastSelectedClubId` (character file). Absent on this build:
  `lastCharacterGuid` (Config.wtf has `lastCharacterIndex` instead), `realmName`,
  both `Sound_VoiceChat*`, and every `synchronize*` CVar (absent is not "off":
  the default is **[verify]**; *resolved 2026-11-12, M16-05, build 70888:*
  with no line in any file, `synchronizeBindings` and `synchronizeConfig`
  read `"1"` in game, and `GetCVar("synchronizeMacros")` returned nothing.
  Offline edits to `Config.wtf` (`farclip "8000"` → `"7999"`) and to the
  account `config-cache.wtf` (`showTargetOfTarget "1"` → `"0"`) both
  survived login with `synchronizeConfig` on. At logout the client removed
  the edited `showTargetOfTarget` line instead of writing `"0"`; that it
  drops every CVar set to its default is **[verify]**). Also recorded verbatim, meaning unknown:
  `portal "test"`, `currentGameMode "15"`, `engineSurveyPatch "16001"`.
  *(Resolved 2026-11-23, M21-02: `portal` is the client's region. The
  launch-client fixture `macos-70888` has `SET portal "US"`, and the
  owner's `GetCurrentRegionName()` returned `"US"` in game; `test` was the
  beta's value.)*
- **2026-09-22, §6, same capture.** Account `bindings-cache.wtf`: three
  `bind KEY ACTION` lines, CRLF, no header or mode line. The quoted
  `CLICK …` action form stays unobserved.
- **2026-09-22, §7, same capture.** `VER 3 0100000000000001 "PolyArc" "134400"`, one
  body line, `END`, all CRLF including after `END`. The icon is a quoted
  numeric file id (the icon-name form was not observed); a character macro's
  id starts `01`. An account `macros-cache.txt` with no macros is 0 bytes and
  must round-trip.
- **2026-09-22, line endings, same capture.** Line endings follow the file kind, not
  the platform: one macOS client wrote LF (`Config.wtf`, both
  `config-cache.wtf`, `chat-cache.txt`, `layout-local.txt`, `.build.info`,
  `.flavor.info`), CRLF (`bindings-cache.wtf`, the character
  `macros-cache.txt`; and, from part 2, `AddOns.txt` and every
  SavedVariables file, the 5 fixtures and all 105 on the install by a byte
  count, macOS build 69977), neither (the edit-mode caches, one line
  ending in a NUL byte; the flagged caches, exactly `2` then a NUL) and both
  within one file (the text-to-speech caches: first line LF, the rest CRLF)
  in the same install. A text reader that drops a trailing NUL breaks L4. Serializers keep each document's
  own endings; a writer creating a new file chooses by file kind. TOC files
  are written by the addon's author or packager, not the client, so their
  CRLF is not client evidence (reworded 2026-09-22 after the part-2 domain
  review).
- **2026-09-22, edit-mode caches, same capture.** `edit-mode-cache-account.txt`
  and `edit-mode-cache-character.txt` are one line of space-separated tokens
  ending in a NUL. Layout names are length-prefixed (`3 def`, `6 Priest`,
  `4 Mage`), so a name can contain spaces and any writer that renames a
  layout must rewrite the length; the token after each name (`59` here) is
  probably an entry count **[verify]**. The account file opens `3 38 …`
  (version and account-wide settings, **[verify]**), the character file
  `3 4 3 3 3 3` (possibly the active layout per spec, **[verify]**).
- **2026-09-22, other caches, same capture.** `click-bindings-cache.txt`:
  LF, ending `END`; lines like `2 0 1 118` read as
  `<button> <modifiers> <type> <id>` with type 1 a spell (118 and 1459 match
  the character's own macro spells) **[verify]**. `tts-cache-*.txt` hold
  small integers and a 20-digit value above the int64 maximum
  (14126315937103613183), probably a chat-type bitmask **[verify]**: keep it
  as text. `chat-cache.txt` ends with a blank line, and `ZONECHANNELS` is a
  bitmask, not a count. The macro icon `134400` is believed to be the
  question-mark icon, meaning "use the spell's icon" **[verify]**.
- **2026-09-22, §6, no fixture (M10-07 domain review).** All **[verify]**,
  from memory, none observed: other line kinds a `bindings-cache.wtf` may
  carry are `BINDINGMODE <n>`, `modifiedclick <ACTION> <MODIFIER>`, and
  cleared keys written as `bind KEY NONE` or with an empty action.
  `wowlab_core.wtfconfig` keeps `BINDINGMODE`, `modifiedclick` and an
  empty-action line as `Unknown`, and types `bind KEY NONE` as-is (action
  `NONE`).
- **2026-09-22, §3, the committed DBM-Challenges TOC (M10-06).** The
  comma-separated `## Interface:` form is now seen on a committed fixture:
  `## Interface: 50504, 120100` (so the list form no longer rests on the
  uncommitted Baganator TOC; directives after a blank line still do, and
  stay **[verify]**). The file is 38 directives, one blank line, then 28 file
  lines, and no comments. Conditions after the path are seen on the
  fixture: 20 file lines end in `[AllowLoadGameType standard]`. The
  before-path form in the §3 example (`[AllowLoadGameType mainline] Foo.lua`)
  and path variables stay **[verify]**. Whether the Forever client counts as
  game type `standard` is **[verify]**. Keys ending in a locale code also
  occur on `X-` directives (`X-DBM-Mod-Name-koKR`). For `X-` keys that is
  the addon's own naming; whether the client resolves a locale suffix on
  anything but `Title`/`Notes` is **[verify]**. Directive values carry
  backslash paths (`## IconTexture: Interface\AddOns\DBM-Core\…`).
  How `wowlab_core.toc` reads the rest of §3, none of it contradicted by the
  fixture: a key is a run of characters with no whitespace and no `:`, so
  `## Some words: text` is a comment; a `[…]` group separated from the path
  by whitespace is a load condition (after the path, seen above; before it,
  **[verify]**), and one touching path characters is a variable
  (`Locales\[TextLocale].lua`); a directive that appears twice is kept
  twice, and which one the client honours is **[verify]**.
- **2026-09-22, §4.2, Forever beta 1.60.1.69977, macOS (M10-03 part 2:
  `RareScanner.lua`, `RareScanner.lua.bak`,
  `Blizzard_GamepadSmartNavigation.lua`, `Syndicator.lua`,
  `DBM-StatusBarTimers.lua`).** **Contradicts §4.2:** no indentation and no
  `-- [n]` comments. Every line starts at column 0; positional entries (82
  in `Syndicator.lua`, tables and one `false`) carry no comment; no file has
  a comment of any kind. The conductor confirmed the same on every
  SavedVariables file of the owner's install (0 tab-indented lines, 0
  `-- [` comments; counts only). Nesting still reaches 6 levels
  (`Syndicator.lua`) and 3 (`DBM-StatusBarTimers.lua`). An empty table is two
  lines, `{` then `}` (47 in `Syndicator.lua`), never `{}`. **Confirms
  §4.2:** the file starts with an empty line, even when it holds only
  `X = nil`; every table entry ends with `,`, including the last, and the `}`
  closing a top-level assignment has none; strings are double-quoted with
  `'` raw inside; keys are positional or `["string"]`, including the empty
  key `[""]`; CRLF on every line, including the leading blank line and the
  last; no BOM. **New:** `X = nil` at top level is written (two files); the
  file is named after the addon, not the variable
  (`Blizzard_GamepadSmartNavigation.lua` holds `SmartNavigation_Mod_Options`,
  `DBM-StatusBarTimers.lua` holds `DBT_AllPersistentOptions`);
  `RareScanner.lua.bak` is byte-identical to `RareScanner.lua`; numbers are
  negative integers (`-260`), whole values with no point (`1`) and floats in
  shortest round-trip form with at most 16 significant digits
  (`0.6745098233222961`, `0.0117647058823529`), with no exponent, hex,
  negative zero or non-finite value (no value in the file needs 17 digits, so
  how the client writes one that does is **[verify]**; §4.2's "up to 17"
  stays open); the only escape seen is `\\`
  (`\"`, `\n`, `\r` and `\ddd` stay **[verify]**); colour codes include the
  named form `|cnIQ0:` … `|r`, not only `|cffRRGGBB`; item links are
  `|Hitem:<id>::::::::<a>:<b>:…|h[Name]|h` (field meanings **[verify]**).
  **Still unobserved:** `[number]` keys, tab indentation or `-- [n]` from
  any client **[verify]**, non-ASCII strings, single-quoted strings, a
  non-nil top-level scalar, a per-character SavedVariables file, the account
  `SavedVariables.lua`. **For M10-12:** a new entry follows the document's
  own detected style (owner decision 2026-09-22, `docs/LAB_PLAN.md` §6.4).
- **2026-09-22, AddOns.txt, part 2
  (`…/Labrealmb Partb Partc Partd/Labchard/AddOns.txt`, 1.60.1.69977).** One
  `<AddonName>: <state>` per line; CRLF with a final CRLF, no BOM, no header.
  Only `enabled` is observed; `disabled` is **[verify]**. The file lists two
  Blizzard addons and no third-party addon, although Syndicator demonstrably
  ran for a character of that first name, so a missing addon cannot be read
  as "disabled". Its effective state (the TOC's `DefaultState`?)
  is **[verify]**, and so is whether the Forever client reads this file.
- **2026-09-22, §3, part 2 (`DBM-Brawlers.toc`).** No blank line separates
  the 34 directives from the 21 file lines, so a parser must not need one. A
  single-value `## Interface: 120100` on a Forever install is only the
  author's claim. Localized keys carry raw UTF-8; no BOM.
- **2026-09-22, addon data spelling of a Forever character (part 2,
  `Syndicator.lua`).** Addon data spells the character `"<First> <Second>"`,
  with a space, and an empty realm (`realm = ""`, `ByRealm[""]`); checked on
  the raw file with names masked. That is the addon's view; which API
  returns it is **[verify]**. Match addon records to folders by first and
  second name, never by realm.
- **2026-09-22, §4.1 to §4.3, no fixture (M10-04T reviews; decisions in
  `docs/LAB_PLAN.md` §6.4, amendment of the same date).** The client's Lua is
  taken to be Lua 5.1 (community documentation; the Forever client reports
  `"Lua 5.1"` for `/dump _VERSION`, 2026-09-22, owner's screenshot; retail
  not checked in game). That is the client's own version string, not a test
  of the escape rules below, which stay as stated. Escapes accepted with Lua
  5.1 meanings: `\a \b \f \n \r \t \v \\ \" \'`, `\ddd` up to 255 (one byte),
  and a backslash before a line break; `\x`, `\u{}`, `\z`, a `\ddd`
  above 255 and any other character after a backslash are rejected. Strings are byte strings. A raw CR or LF inside a string is
  an unterminated string; other raw control bytes are kept; a raw NUL stays
  rejected (**[verify]**). `[true]`/`[false]` keys are allowed (style
  `boolean`). Long comments (`--[[`, `--[==[`) are rejected unless a fixture
  shows one; `-- [n]` is a line comment. Which escapes the client actually
  writes stays **[verify]** (§4.2 amendment).
- **2026-09-23, §4.1 and §4.3, no fixture (M10-04 code review).** Number
  spellings `luadata` refuses although Lua 5.1 lexes some of them: `.5` and
  `5.` (Lua 5.1 reads both) and hex floats (`0x1p4`); the client's own
  serializer has not been seen writing any of them. A NUL byte is refused
  everywhere in a SavedVariables file, inside comments included, not only
  between tokens and in strings. A number literal longer than 4300
  characters and a document over the `luadata.MAX_COST` parse budget are
  refused as bounds (`docs/LAB_PLAN.md` §6.4, amendment of the same date).
- **2026-09-27, §4.2, after the M10-12T reviews (owner decisions in
  `docs/LAB_PLAN.md` §6.4, amendment of the same date).** Line endings: CRLF
  on every line, the leading empty line and the last included, in every
  SavedVariables file captured (Forever beta 1.60.1.69977, macOS, 105
  files); Windows and retail are **[verify]**. An existing document keeps its
  own endings; a new file takes its siblings'; with none to read, CRLF. The
  §4.2 bullets "Indentation is one tab per level" and "written positionally
  with a `-- [n]` comment" describe the remembered retail form, not yet seen
  from any client **[verify]**; the Forever beta writes neither (amendment of
  2026-09-22). The 2026-09-22 wording "floats in shortest round-trip form"
  is corrected: the client writes floats with at most 16 significant
  digits, which do not always read back as the same double
  (`49.99999618530273`, uncommitted capture `Blizzard_PTRFeedback.lua`,
  1.60.1; **[verify]** on a committed fixture). The Lab keeps every number's
  text and never reformats a number it did not create; a number built from
  a Python float must not use `repr()`, which can give 17 digits.
- **2026-09-28, lab-addon output, Forever beta 1.60.1.70009 (interface
  16001), macOS (M11-03; fixtures
  `macos/forever/WTF/Account/90000001#6/SavedVariables/WowLab.lua`,
  `…/1/Labchard-Labrealmg/SavedVariables/WowLab.lua` and
  `…/1/Labcharb-Labrealmf/SavedVariables/WowLab.lua`).** Written by
  `lab/addon/WowLab/` after M11-20 (the first install crashed the client in
  the appearance scan; that session wrote nothing). Two low-level characters
  of one account, one capture run: the first at level 13 with `probe.loads`
  = 4 (session notes expected 2; the file wins), the second at level 10
  with `probe.loads` = 2. The counter goes up by one per addon load (each
  login and each `/reload`) whose previous session wrote the file; a value
  above 1 can only come from the addon reading the value the file held, so
  the counter persists and increments. Every section was gathered on both
  characters except `customization` (absent: "no barber-shop visit
  recorded with the addon enabled") and `collections.appearances` (absent by
  M11-20). No gathered section carries `events_unregistered`, so every
  event those sections asked for registered. The file cannot say the same
  of the barber-shop events: a section never gathered is written as the
  plain `not_gathered` reason, without `events_unregistered`
  (`lab/addon/WowLab/Core.lua`). The account file is `WowLabDB = { ["schema"] = 1, }`:
  schema 1 puts every section per character. Grammar: as §4.2 amended
  (CRLF, leading blank line, no indentation, no `-- [n]`, empty tables on
  two lines), nesting depth 10; `client.build` is written as a string
  (`"70009"`), `client.interface` as a number (16001). Keys are written in
  the client's table-iteration order, not insertion order: the addon puts
  `schema` into `WowLabCharDB` first, and the client writes it ninth of ten
  top-level keys. Item-link field meanings used below (the level and
  specialization fields after the item id) come from community
  documentation **[verify]**.
  Barber shop (owner, 2026-09-28): on Forever, walking into a barber shop
  and right-clicking a barber chair did nothing; no customization UI
  opened. Tried in one capital on two characters. That is an observation
  about reaching the UI in-world on this build, not evidence that the
  client lacks the feature: a paid service or a later build may differ. So
  the addon's customization record stays absent until the client fires
  `BARBER_SHOP_OPEN` with the addon enabled; whether that event (or
  `BARBER_SHOP_APPEARANCE_APPLIED`) exists on 70009 is not recorded,
  because a section never gathered carries no `events_unregistered`.

  Every **[verify]** in `docs/LAB_PLAN.md` §13.1, and the forever-addon-kit
  findings §13.1 says M11-03 re-verifies:

  | §13.1 item | Result | What Forever returned |
  |---|---|---|
  | Forever fills the crafter field of an item link (`crafter_removed`) | still open | 18 equipped items over two characters: `crafter_removed = false` on every slot and no GUID-shaped run in any link. Whether any of them is player-crafted was not checked against game data, so the capture cannot tell "Forever leaves the field empty" from "nothing crafted was worn"; it needs a worn item the owner knows a player crafted. 2026-10-18: answered in part for 70425 by the item-link entry of that date below (one slot with `crafter_removed = true`; its crafter field is blank because the addon removed the GUID). |
  | A ranged slot on Forever | confirmed | `INVSLOT_FIRST_EQUIPPED` 1 and `INVSLOT_LAST_EQUIPPED` 19; the first character has a wand in slot 18 (the second has nothing there) |
  | Per-slot item level and the equipped average as the client reports them | answered in part: the API returns values; what the average means on Forever is open | `C_Item.GetCurrentItemLevel` gave a level on every filled slot; `GetAverageItemLevel` returns one value three times (3.3125, 2.375). Forever's character sheet shows no item level (owner, 2026-09-28), so nothing in the UI backs `average.equipped` and the reader must not call it the character-sheet figure. Neither value is the mean of the slot levels over filled, 16 or 19 slots (sums 85, and 58 without the shirt). Both values times 16 are whole numbers (53, 38), which fits a 16-slot divisor over per-item levels other than those recorded (hypothesis). How the client computes it is open |
  | `talents.class` through `C_ClassTalents.GetActiveConfigID()` then `C_Traits` | confirmed | a config of `type` 4 with one tree (`system_id` 10; 52 and 54 nodes), trait currency 3820 (`spent` = `max_quantity` = 4 at level 13, 1 at level 10), and an export string from `C_Traits.GenerateImportString`. `last_selected_config` is neither written nor absent with a reason on either character: `GetLastSelectedSavedConfigID` exists and a spec id was there, so the call returned no number or failed inside `ns.Call` (M11-04 must allow the key to be missing). Hypothesis, from Retail memory: it returns nil when no saved loadout was ever selected |
  | Class talents and the Legacy trees both on `C_Traits` (kit, 69893) | confirmed (the Legacy half rests on the identification above) | both sections are `C_Traits` config dumps |
  | Legacy panel `ToggleLegacySystemUI` (kit) | confirmed: the global exists (not called) | `legacy_ui = true` on both |
  | `talents.legacy` "empty below level 25" | contradicted as written (not empty); nothing spent | at levels 13 and 10: one candidate config (`type` 3, found by `type:Generic`, not by a Constants system id), taken to be the Legacy system by elimination, since nothing in the file names it **[verify]**. Four trees with `system_id` 45: 1118 with no nodes and no currency; 1187, 1188 and 1189 with 9 nodes each, all `is_visible = true`, all at rank 0. The same tree, node and entry ids appear on both characters (warlock and mage); the config ids differ. Currency 4225 has `max_quantity` 0, so nothing can be spent at these levels. The reader shows "present, nothing spent, 0 points available", never "locked" and never "empty" |
  | Legacy points spent and the seasonal cap, if the client gives them | still open | each Legacy tree with nodes carries trait currency 4225 with `quantity`, `spent` and `max_quantity` all 0 below level 25; whether `max_quantity` becomes the cap after the unlock needs a character at 25 or above |
  | Legacy unlocked at level 25 (kit) | still open | no character at 25 |
  | No `GetSpecialization` global (kit) | still open | not tested: `C_SpecializationInfo.GetSpecialization` exists, so the addon never looked at the global |
  | Spec found by testing for the API; new spec ids (kit: paladin 1486) | confirmed | `spec = { api = "C_SpecializationInfo", index = 1, id = 1490 }` and `id = 1482`; item links carry the same id in their specialization field (`…:13:1490:…`) |
  | `BARBER_SHOP_APPEARANCE_APPLIED` | still open (the barber UI did not open in the owner's attempt on 70009) | whether the event registered is not recorded for a section that was never gathered |
  | The name fields of `C_BarberShop.GetCurrentCharacterData()` never stored | still open (the barber UI did not open in the owner's attempt on 70009) | never called; neither file holds a name |
  | Currencies: total cap, weekly cap, weekly earned, account-wide flag | still open | `list` empty, `headers_collapsed` 0 and `filter` 1 (meaning of 1 unknown) on both characters. The file does not record how many rows `GetCurrencyListSize` gave, so it cannot tell an empty panel from rows whose id could not be read; no currency field was seen. |
  | Forever's profession model | answered: Retail's five-position `GetProfessions` shape | positions 1 and 2 hold two primary skill lines (e.g. 182 and 393); First Aid (129 **[verify]**) in the position Retail gives archaeology (3); position 4 (Retail: fishing) empty; Cooking (185 **[verify]**) in position 5; caps 75/150 (Classic tiers, from memory); `modifier` 0 |
  | Which collections are account-wide (§13.1: "until the M11-03 capture shows") | still open | mounts, toys and pets: none collected among what the client listed (count not recorded), on both characters (mounts unfiltered; toys through the toy box filter with every switch shown; pets with default filters) |
  | A later patch makes the installed TOC out of date | still open | no patch during the capture; the unsuffixed `WowLab.toc` with `## Interface: 16001` loaded |
  | Forever's preferred TOC suffix | still open | only an unsuffixed TOC was tried, and it loads |
  | The client does not delete `WowLab.lua` on `remove` | still open | `remove` not exercised |

  Not done in this session, still open for the owner: a barber-shop visit
  that opens the UI (without it the customization APIs and events stay
  untested), a staged-talent `/wowlab save` test (whether `ranks_purchased`
  and `current_rank` include staged changes), a character at level 25 or
  above (Legacy cap and unlock), and the dungeon combat log. Seen in the
  staged capture but not committed (so **[verify]** against a committed
  fixture): on 70009 both characters' `<digits>/<First>-<Second>/` folders
  hold an `AddOns.txt` (371 bytes, `WowLab: enabled`) as well as the
  `<Realm>/<First>/` twin (`docs/LAB_FILE_MAP.md`, amended the same day).

  Follow-up, 2026-09-29 (M11-22; `docs/LAB_PLAN.md` §13.1 amended the same
  day). The addon now writes three keys the fixtures above predate, so a
  later capture answers what these could not: `events_unregistered` on
  every record of a section that registered its events, a never-gathered
  section's absent record included (the barber-shop events), as an empty
  list when every event registered. A missing key does not mean every
  event registered; only an empty list does. Currencies gain a `rows` count
  from `GetCurrencyListSize` and a `headers` count; `talents.class` gains a
  `last_selected_config_absent` reason (`"the client returned no
  last-selected loadout for this spec"` for nil, others for an error or a
  non-number) and an `export_absent` reason whenever `export` is missing
  (an empty string included). Schema stays 1; each key is optional to the
  reader.

  Follow-up, 2026-09-29 (M11-23; fixture
  `macos-70058/forever/WTF/Account/90000001#6/1/Labchard-Labrealmg/SavedVariables/WowLab.lua`,
  Forever beta 1.60.1.70058, interface 16001, macOS). A later run of the same addon (after M11-22)
  on one character. The owner opened the barber shop in Orgrimmar on 70058
  (the UI opened, with no Lua error) twice in one session: first sat down
  and cancelled, then sat again, changed the hair colour and clicked Accept,
  which also worked; no open came after the Accept. The rows below update the table above for this
  build; they leave its 70009 findings as they were.

  | Item | Result | What Forever 1.60.1.70058 returned |
  |---|---|---|
  | `BARBER_SHOP_APPEARANCE_APPLIED` (§13.1) | **contradicted in part** (event registers; record not rewritten) **[verify]** | the section's `events_unregistered` is `{}`, so the client accepted both barber-shop events, yet the record reads `recorded_at = "open"`, with `recorded_load` 8 equal to `probe.loads` 8 (the same session as the applied change). In the addon (`Core.lua` `listen` and `gather`), every call of the section's handler replaces the record: with `recorded_at = "applied"`, or with an absent marker if the gather fails. So either `BARBER_SHOP_APPEARANCE_APPLIED` did not reach the handler in that session, or the barber shop was opened again after the change (a second `BARBER_SHOP_OPEN` leaves `"open"` with the new colour). The owner opened the shop twice in that session: once with no change (cancelled), then again, changing the hair colour and accepting, with no open after the Accept. So `BARBER_SHOP_APPEARANCE_APPLIED` did not reach the handler, and `choices` are most likely the look at the second open, before the hair-colour change. Follow-up for the addon (not changed in M11-23). `looks import-char` says "as of the last barber-shop open" and never calls the look the character's appearance now |
  | `chr_model_id` from `C_BarberShop.GetViewingChrModel` (§13.1, capture checklist item 14) | **contradicted**: absent | the key is not in the record. The function is missing, raised an error, or returned no number: the addon writes the key only for a number, and the file cannot say which. `import-char` takes the body type from `sex` and says so |
  | `sex` from `C_BarberShop.GetCurrentCharacterData()` (`Enum.UnitSex` 0/1) | present: 0 | all seven recorded options are on `ChrModel` 9, the model that `ChrRaceXChrModel` gives race 5 with `Sex` 0 (70009 tables). So `sex` 0 reads as the tables' body type 0. Option ids are per model: race 5 `Sex` 1 is model 10, whose options are 63–67, 535, 559, 568, 6347 and 8531, so the record cannot come from model 10. Sex 1 is not yet seen **[verify]** |
  | The name fields of `C_BarberShop.GetCurrentCharacterData()` never stored | confirmed on this record | no name field and no name, realm or GUID anywhere in the file (checked by grep before commit) |
  | `currentChoiceIndex` 1-based (addon `Customization.lua` **[verify]**) | consistent with 1-based; 0-based ruled out for option 62 | For all seven choices, `choice_index` is the choice's 1-based position among its option's choices sorted by `UiOrderIndex` in the 70009 table (918: 6; 980: 10). Option 62 has ten choices (`UiOrderIndex` 0–9), so a 0-based 10 would point past the end. The addon takes each `choice` id from `option.choices[currentChoiceIndex]`, so the ids themselves are right only if the index is 1-based. The list the barber returned is not recorded. |
  | Which options the barber shop lists | 7 of model 9's 10 options | `race_id` 5 (Undead). Options 58, 59, 60, 61, 563, 62 and 534 were listed. Skin Type (567), Eyesight (6346) and Eye Style (8530), on model 9 in the 70009 tables, were not. Choice 918 depends on option 567, so a look imported from this record shows that dependency as undecided |
  | Currencies on this character | empty, with the M11-22 counts | `rows` 0, `headers` 0, `list` empty, `filter` 1, `headers_collapsed` 0. The client listed no rows at all, as the owner saw in game |

  Also in the file, and outside the table's scope: `professions` is
  `{ absent = "switched off by the owner" }`, but the file holds no `skip`
  list. That is what the addon writes when a section is off this session and the
  owner unskipped it before the save (§13.1: an unskip takes effect from the
  next `/reload` or login, never in the same session); the skip may be from
  this session or loaded from an earlier one. The owner reports `/wowlab
  skip professions` then `/wowlab unskip professions` in this session. Not
  an addon defect.

  Follow-up, 2026-09-29 (M11-29, schema 2; `docs/LAB_PLAN.md` §13.1
  amended the same day). Readings of the two rows above, from the addon's
  code and the owner's account rather than a new capture. The file shows
  only that the record's last write came from an open. The owner's account
  ("no open after the Accept") rules out a second sit, not an open the
  client fires itself, which the handler sees the same way; and with two
  sits in that session, the applied event could also have come between the
  cancelled first visit and the second open. So the first row's "So
  `BARBER_SHOP_APPEARANCE_APPLIED` did not reach the handler" is one of two
  readings until a new capture. `events_unregistered = {}` means that neither
  `C_EventUtils.IsEventValid` (when the client has it) nor an error from
  `RegisterEvent` refused the event; the addon does not read
  `RegisterEvent`'s return value. forever-addon-kit's API list for 69893 has
  `C_EventUtils.IsEventValid`, so the check most likely ran on Forever, but
  the file does not record whether it did on 70058. Three limits: every
  section's `events_unregistered` on 70058 is `{}`, so this client has never
  been seen refusing any name, and "not refused" is not yet evidence that a
  name is known; a known name means only that it is in the client's event
  table, and an engine shared across flavors may know events this flavor
  never fires; only a count of 1 or more shows that an event fires. The
  addon now writes schema 2 (§13.1: the first format change after M11-04),
  which adds, on `customization`, `events_received` (each registered event
  with how many times it reached the section in the session that saved the
  file) and `chr_model_id_absent` (which outcome the `GetViewingChrModel`
  call had when the section gathered, not why). `events_received` includes
  six count-only events, **[verify]**: `BARBER_SHOP_RESULT`,
  `BARBER_SHOP_CLOSE`, `BARBER_SHOP_FORCE_CUSTOMIZATIONS_UPDATE`,
  `BARBER_SHOP_COST_UPDATE`, `BARBER_SHOP_SUCCESS`, and the made-up control
  `WOWLAB_CONTROL_NOT_A_REAL_EVENT`. The control is for this reading: if it
  has no entry, every other entry, 0 included, is a name this client knows
  (0 means it did not reach the section in that session), and a missing
  entry means a name it does not know. If it has an entry at 0, the client
  accepts any name, and "not refused" proves nothing. The reading holds
  whether or not `C_EventUtils.IsEventValid` exists, because the control
  tests the addon's whole gate (that check, then `RegisterEvent`).
  Registering a name the client does not know is not known to be safe on
  Forever: the empty `events_unregistered` lists show only that no refusal
  was seen. It happens at `ADDON_LOADED`, before `/wowlab skip` can be
  typed, so if it ever crashed the client, untick WowLab at character
  select, or with the client closed `wowlab undo` the install. Schema 1
  files, these fixtures included,
  are still read as before. The cause behind each of the two rows stays open
  until a capture made after M11-29 (runbook: `lab/addon/README.md`, "M11-29
  capture step"). Settled for 70425 by the entry of 2026-10-16 below.
- **2026-10-16, lab-addon schema 3 and the barber-shop record after an
  applied change, Forever beta 1.60.1.70425 (interface 16001), macOS
  (M13-05, M11-29; fixtures
  `macos-70425/forever/WTF/Account/90000001#6/SavedVariables/WowLab.lua`
  and `…/1/Labchard-Labrealme/SavedVariables/WowLab.lua`).** Written by the
  schema-3 addon (#176) in one session, on one level-15 warlock with a
  summoned, named demon. Owner's notes:
  - logged in out of combat and saw the "recording in 15 s" pass;
  - put on the action bars one spell, one item, a `#showtooltip <spell>` +
    `/cast` macro and a plain `/say` macro;
  - "Character Specific Key Bindings" was ticked, and dragging a spell
    straight onto a key worked;
  - the spellbook has no "show all ranks" toggle; ranks show as text
    ("Rank 1"); no greyed-out future spell was visible;
  - barber shop: hair colour moved from swatch 7 to swatch 1 (counting left
    to right, then top to bottom), Accept clicked once; the window closed by
    itself and the new colour showed; no `/reload`, then a logout and an
    exit.

  Grammar as §4.2 amended (CRLF, leading blank line, no indentation, ASCII
  only). The account file is `WowLabDB = { ["schema"] = 3, }`. The run's
  `macros-cache.txt` and `bindings-cache.wtf` were read for items 7 and 8
  but not committed. Items are those of `lab/addon/README.md`, "M13-05
  [verify] checklist":

  | Item | Result | What Forever 1.60.1.70425 returned |
  |---|---|---|
  | 1. `schema = 3`; `probe.loads` rising | schema **confirmed**; rising **still open** | both variables hold `schema = 3`. `probe.loads` is 1 with no `probe.lost`: a first load (or a file that failed to load). No earlier file of this character was seen, so the counter was not seen rising across the first schema-3 write |
  | 2. `probes` | **confirmed**: every API present, no error, nothing secret | `present = true`, `errors` 0 and `secret` 0 on every probe of all three sections. A global `issecretvalue` function **was present** (forever-addon-kit's 69893 list lacks it) and answered a boolean on every call (spellbook 244 of 244, actionbars 76 of 76, bindings 432 of 432). The probe sees a global function, not where it came from: an addon could define one. `C_Secrets` and `canaccessvalue` are present too (looked up only), which makes an addon shim less likely. The run's character `AddOns.txt` lists 7 addons: 2 Blizzard modules enabled and 5 third-party addons disabled. An addon it does not list (WowLab among them) is at the client's default, so the file does not prove that no other addon ran. Where the client's function comes from stays **[verify]** |
  | 3. `spellbook` read; skill lines | **confirmed** in the file; on-screen counts not reported | not absent. Four skill lines, offsets 0, 9, 16, 23 and counts 9, 7, 7, 5, tiling items 1 to 28. No guild or hidden line, no spec ids. The owner did not report per-tab counts to compare |
  | 4. Subtexts and ranks | ranks as text **confirmed**; one item per rank; no toggle | subtexts seen: `Rank 1` to `Rank 3`, `Racial`, `Passive`, `Racial Passive`, `Summon`, and none (5 items). A spell with several ranks is **one item per rank**, each with its own spell id (Shadow Bolt 686, 695, 705 as `Rank 1`–`3`; Demon Skin, Curse of Weakness and Immolate as ranks 1–2). The spellbook has no "show all ranks" toggle, so there is no on/off pair. `item_types` is `None` 0, `Spell` 1, `FutureSpell` 2, `PetAction` 3, `Flyout` 4. Types seen: 1 (27 items) and 4 (1). The flyout (Summon Demon) has `action_id` 259 and **no `spell_id`**, so its action id is not a spell id. No `PetAction` item. The addon reads the player's bank only, never the pet spellbook, so that says nothing about pet spells |
  | 5. A future spell | **still open** | none visible in game and no type-2 item in the file. `FutureSpell` exists in the client's enum |
  | 6. `actionbars` range | slots past the filled ones **answer nothing, no error** | `GetActionInfo`: 180 calls, 0 errors, 26 answered. Highest filled slot 72; slots filled are 1–12, 49–50 and 61–72. Whether 180 is the client's own bound stays **[verify]** |
  | 7. Macro slots | type **confirmed**; `sub_type` **contradicted**; numbering **still open** | two slots with `type = "macro"`, ids 149 and 140, **neither with a `sub_type`**, the `#showtooltip <spell>` macro included, so on Forever a `#showtooltip` macro slot gives no spell id. `GetMacroInfo`: 2 calls, 2 answered, equal to the macro slots with no subtype, so both ids name a macro. The character `macros-cache.txt` lists 29 macros, `VER 3 0100000000000001` to `…1D`, each named a single space, each a `#showtooltip` line and a `/cast` line. The account `macros-cache.txt` is 0 bytes, and **no plain `/say` macro is in either file** although the owner put one on a bar. If character macro *n* were index 120 + *n* (Retail's numbering, from memory), the slots would be macros 20 and 29, both `#showtooltip` macros, and the plain macro's slot would not be identified. So how account and character indices are numbered, and whether `macros-cache.txt` is rewritten at logout on Forever, stay **[verify]**. No macro name or body is in the file (item 13) |
  | 8. `bindings` | the 8 cache lines **confirmed** against the live keys; the other bound commands **unexplained**; set names **contradicted**; a dragged spell **still open** | `binding_set` 2 with "Character Specific Key Bindings" ticked. `account_bindings` and `character_bindings` are **absent**: `ACCOUNT_BINDINGS` and `CHARACTER_BINDINGS` were not numbers at the last bindings gather, and the file cannot say whether they were nil or another type. So the file cannot name the set (2 is Retail's character set, from memory, **[verify]**). `Enum.BindingSet` (Account, Character; from memory) is a **[verify]** candidate for a later schema. The account and character `bindings-cache.wtf` are byte-identical: eight `bind` lines, CRLF. Each of its six bound commands has exactly that key in the live read, and its two `NONE` lines (`-`, `=`) match keys no command holds. That is all the comparison confirms. The other 118 commands with a key are not in either cache and stay unexplained: client defaults left out of a cache that holds only changes is one reading (from memory, **[verify]**). The owner's `/say` macro (item 7) and the spell dragged onto a key are missing from both caches too, so these caches may predate the session; whether and when the client rewrites them on Forever is **[verify]**. `GetBinding` listed 299 commands, 1 of them not a plain upper-case name (`commands_left_out` 1, never stored), and `GetBindingKey` was asked about the other 298, 124 with a key. `keys_left_out` 0; no `MACRO`, `SPELL`, `ITEM` or `CLICK` command stored. The dragged spell may be the one left-out command; the file cannot say. No action-bar addon was installed |
  | 9. `left_out_config` | **confirmed** | `talents.legacy.left_out_config` 640511 equals `talents.class.config.id`; no `left_out_config_absent` |
  | 10. `events_unregistered` on the new sections | **confirmed**: the four names are known | `[]` on `spellbook`, `actionbars` and `bindings`. On this build the barber-shop control `WOWLAB_CONTROL_NOT_A_REAL_EVENT` has no entry, so the addon's gate refuses a name the client does not know. An empty list therefore means `SPELLS_CHANGED`, `ACTIONBAR_SLOT_CHANGED`, `UPDATE_BINDINGS` and `PLAYER_REGEN_ENABLED` are names this client knows. That they fire is not shown: only `customization` counts events |
  | 11. Combat | **still open** | the combat steps were not done |
  | 12. Restricted content | **still open** | not done; outside it, every `secret` count is 0 |
  | 13. Privacy | macro check **confirmed**; hunter pet **still open** | no line of either macro file's bodies, no `#showtooltip`, `/cast` or `/say`, and no macro name in the file. The capture tool noted 27 Name-Realm-shaped strings; every one is a modifier-key chord in `bindings` (`SHIFT-PAGEDOWN`, `CTRL-F1`, …). Every distinct string in the file is a game string (spell, item, binding command or key, API name) or a fixed reason of the addon. This is no evidence about pet names. The addon never reads the pet spellbook, and a warlock demon's name is generated by the game, not typed by the owner (from memory, **[verify]**). That is why the checklist asks for a hunter, and a hunter with a named pet was not tried |
  | 14. Hitches | **still open** | not reported |

  The M11-29 barber-shop check (`lab/addon/README.md`, "M11-29 capture
  step"; items 11, 12 and 14 of the M11-03 checklist). The first row of the
  reading table that fits is "`recorded_at = "open"`, `APPLIED` at 1 or
  more, `OPEN` at 2 or more, and you sat once":

  | Item | Result | What Forever 1.60.1.70425 returned |
  |---|---|---|
  | `BARBER_SHOP_APPEARANCE_APPLIED` reaches the section (M11-03 item 12) | **confirmed: it fires and reaches the section**. "An applied change gives `recorded_at = "applied"`" is **contradicted** | `events_received`: `BARBER_SHOP_OPEN` 2, `BARBER_SHOP_APPEARANCE_APPLIED` 1, `BARBER_SHOP_CLOSE` 2, `BARBER_SHOP_RESULT` 1, `BARBER_SHOP_COST_UPDATE` 0, `BARBER_SHOP_FORCE_CUSTOMIZATIONS_UPDATE` 0. `recorded_at = "open"`, `recorded_load` 1 (this session), not carried. With one sit (the runbook's step) and one Accept, a second `BARBER_SHOP_OPEN` reached the section after the applied event, and its gather replaced the applied record. The client fired `BARBER_SHOP_OPEN` again after the Accept. Whether the shop actually reopened, and why, is **[verify]**: the owner saw the window close by itself and did not report a reopen. The counts do not record the order of the events. This is why the applied change ended as `"open"` in this capture. One capture does not show that it always will |
  | Which look the saved record holds | **the new look** | option 61 (Hair Color in the 70009 `ChrCustomizationOption` table) is at `choice_index` 1, `choice` 956: the swatch the owner picked. The applied record was overwritten before it was saved, so what it held is not known |
  | `currentChoiceIndex` base (M11-03 item 11) | **consistent with 1-based**; the counting order **consistent** for hair colour | the owner picked swatch 1, and the record has `choice_index` 1. The addon takes `choice` from `choices[index].id` (`Customization.lua`), so the ids match the index by construction and are no evidence of the base. The identity-free argument comes from the M11-23 record on 70058: it reports `choice_index` 7 for option 61, which has 7 choices (956–962) in the 70009 table, and a 0-based client could not report 7. That the client's list for option 61 has the table's 7 choices is **[verify]**. That record matches this one on all six other options and differs only on option 61, the swatch the owner calls the old 7th today. This is the strongest sign that the two runs are one character (pseudonyms of two runs still prove nothing). If they are, the second open on 70058 came after the Accept too, and M11-23's first row reads the other way. For choices 956–962, `OrderIndex` and `UiOrderIndex` are identical (0–6), so this does not tell which column the shop lists swatches by. The race-mismatch drop stays untested |
  | `sex`, `chr_model_id`, no name (M11-03 item 14) | `sex` 0; no name **confirmed**; model id: the call **returned nil** | `chr_model_id_absent = "C_BarberShop.GetViewingChrModel returned nil"`: the function exists and named no model at the last gather. M11-29's other question, why it returns nil, stays **[verify]** (the alternate-form hypothesis is untested); the Lab keeps taking the body type from `sex` |
  | Count-only events and the control | **settled for this build** | the control has no entry, so the client refuses unknown names, and every entry is a name it knows. `BARBER_SHOP_SUCCESS` has no entry: not a name on Forever. `BARBER_SHOP_CLOSE` came **2** times for one sit, not the 1 expected from Retail. `BARBER_SHOP_RESULT` came once, for the one Accept. `BARBER_SHOP_COST_UPDATE` stayed 0 although a colour was previewed and bought. `BARBER_SHOP_FORCE_CUSTOMIZATIONS_UPDATE` stayed 0 |

  Nothing in the addon changes for M11-29: its acceptance allows "the reason
  it cannot". The reason here is the second `BARBER_SHOP_OPEN` after the
  Accept. A later
  ticket could keep an applied record through a following open, which would
  be schema 4 (now schema 5; schema 4 is M15-01's) with its own capture.
  `looks import-char` keeps saying "as of
  the last barber-shop open", which is still true.
- **2026-10-17, §8, `SPELL_CAST_SUCCESS` layout and the unit-flag fields,
  Forever 1.60.1 (M13-08; fixture
  `macos/forever/Logs/WoWCombatLog-040126_021630.txt`, format 22, advanced
  logging on).** The seven `SPELL_CAST_SUCCESS` records (lines 5, 10, 14,
  30, 42, 57, 69) each have 30 fields after the event name, in this order:
  source GUID, source name (quoted), source flags, source raid flags;
  destination GUID, name, flags, raid flags; spell id (bare decimal), spell
  name (quoted), school (hex, e.g. `0x20`, `0x4`, `0x1`); then the 19-field
  advanced block, whose first field equals the source GUID on all seven and
  whose second is the zero owner GUID. Six records are the owner's, with
  source flags `0x511`; one is the NPC "Ravage" (id 3242), with `0x10a48`.
  Read as the `COMBATLOG_OBJECT_*` fields (community documentation
  **[verify]**; the bit values are not shown by a fixture): `0x511` is
  affiliation mine (`0x1`), reaction friendly (`0x10`), control player
  (`0x100`), type player (`0x400`); `0x10a48` is affiliation outsider
  (`0x8`), reaction hostile (`0x40`), control NPC (`0x200`), type NPC
  (`0x800`), plus the current-target bit (`0x10000`). The NPC logs `0x10a48`
  on every one of its lines, so "flags change with the target" is still
  only community documentation. `combatlog.cast_counts` (M13-08) selects
  the player's own casts by requiring affiliation exactly "mine", type
  exactly "player" and control exactly "player" (the last on the
  **[verify]** assumption that a charmed or mind-controlled player logs
  another controller; no capture shows it); the §8 text above is amended
  to say so. Spell ids in the
  log are per rank: Shadow Bolt 695 is the spellbook's "Rank 2" and
  Immolate 348 its "Rank 1", while the later schema-3 record (build 70425)
  holds ranks 705 and 707 on the bars; the log predates the record, which
  is why they differ. Still **[verify]**: the layout with advanced logging
  off (counted on the first eleven fields only); pets and guardians (no
  such source in this log); group and instance logs; mind control and charm
  (whether a charmed player's casts carry "mine"); auto-repeat spells (a
  wand's Shoot or Auto Shot may log one `SPELL_CAST_SUCCESS` per shot); a
  log holding more than one of the player's characters.
- **2026-10-18, §4.2, the item link the lab-addon records in
  `gear.slots[].link` (M14-05; the four committed per-character
  `WowLab.lua` files: `macos/` two characters, 70009; `macos-70058/`;
  `macos-70425/`).** 38 links, every one of the shape
  `|c<colour>|Hitem:<payload>|h[<name>]|h|r`, in the named colour form
  `|cnIQ0:`, `|cnIQ1:` or `|cnIQ2:`; the hex form `|cffRRGGBB` is
  unobserved on Forever (it is the hex form other clients write,
  **[verify]**) and `wowlab_core.itemlink`
  reads it from a constructed test only. The payload has 19, 20 or 22
  `:`-separated fields, which fit the field order community documentation
  gives (warcraft.wiki.gg, "ItemLink"), **[verify]** on Forever except where
  noted: 0 item id; 1 enchant; 2 to 5 gems; 6 suffix; 7 unique id; 8 link
  level; 9 specialization; 10 the modifier mask (older documentation: the
  upgrade type); 11 the item context; 12 the bonus-id count, then that many
  bonus ids; then the modifier count and that many `type:value` pairs; three
  relic groups (a count and that many bonus ids each); the crafter GUID; the
  extra enchant. With that order every count in every link fits the payload
  exactly (19 fields with no bonus id and no modifier, 20 with one bonus id,
  22 with one bonus id and one pair, e.g. `…:1:1:12720:1:28:5256:::::`).
  Fields 1 to 7 are empty in all 38. Field 9 equals the record's `spec.id`
  on all 38 (1482 and 1490, as the M11-03 table found); field 8 is 10, 13
  or 15, the character's level where the fixture index gives it (10 and 13
  at 70009, 15 at 70425; the 70058 row gives none, and its links say 13),
  which fits "link level" being the character's level. The modifier pair
  seen is type 28 with values 5244 to 5256; what type 28 means is
  **[verify]**. Field 11 holds 1, 11 or 75 where filled. Field 10 is empty
  on all 38, so the modifier mask (`upgrade.mask` in `char gear --json`) has
  never been seen filled. The suffix field (6) is empty on every "of the X"
  item, while the bonus ids repeat per suffix (12720 on both "of the Owl"
  items, 12745 on the four "of the Sorcerer", 12748 "of the Physician",
  12730 "of Spirit"), so on Forever the suffix appears to travel as a bonus
  id **[verify]**. The crafter field is empty in every link, the 70425
  cloak (slot 15, item 2310 Embossed Leather Cloak, a Leatherworking item
  by community documentation **[verify]**, `crafter_removed = true`)
  included: the addon blanked a GUID-shaped run there, so on 70425 a worn
  item did carry one, which answers the M11-03 row "Forever fills the
  crafter field" in part. `Gear.lua` removes only the `Player-…` text and
  keeps the colons, so every field stays in place; what cannot be told is
  which of the empty fields held it. The bracketed name is the only name
  source; nothing is looked up. The averages (`GetAverageItemLevel`):
  `equipped`, `overall` and `pvp` are equal in all four records. On 70425
  9.5 = 152/16, the sum of the recorded levels of slots 1 to 17 without the
  shirt (the ranged slot left out) over 16; both 70009 records fall below
  that rule (3.3125 against 71/16, 2.375 against 58/16). That supports "how
  the client computes it is not known" (M11-03 table) and settles nothing.
- **2026-10-19, lab-addon schema 4, `identity` (M15-01; fixture root
  `macos-70450/`, two owner captures on 1.60.1.70450 of
  `1/Dangling-Participle` and `1/Sangraal-Sett`).** `UnitRace`, `UnitSex`
  and `UnitClass` all ran: both records hold `race_id`, `sex_raw`,
  `class_id` and `class_file` with no `_absent` reason, and
  `events_unregistered = {}`. `UnitClass` gave (8, `MAGE`) and (9,
  `WARLOCK`), with `UnitRace` race 2 and race 5, matching the owner's report
  (an Orc mage, an Undead warlock). `UnitSex` gave 2 on both, two
  characters of the first body type. That 2 corresponds to `Enum.UnitSex` 0
  is settled on `Sangraal-Sett` by its carried `customization` record
  (`race_id` 5, `sex` 0, from `C_BarberShop.GetCurrentCharacterData`), and
  by the M11-23 table join (race 5 `Sex` 0 is `ChrModel` 9, the first body
  type), with one caveat: the two values come from different sessions and
  builds. The carried `sex` 0 was recorded at load 1 (`recorded_load` 1),
  most likely the 70425 session; `identity` is from load 2 on 70450. They
  describe the same body if, as is most likely, the carried record is from
  the M11-29 visit, which changed hair colour only; `recorded_load` 1 shows
  no barber visit with the addon since, and nothing in the record shows a
  body-type change at that visit.
  `UnitSex` 3 (the second body type, from memory) and 1 ("unknown") are
  still unobserved **[verify]**. `probe.loads` is 1 on `Dangling-Participle`
  (a first load) and 2 on `Sangraal-Sett`; whether the count rose across a
  first schema-4 write is still open, since no earlier file of either
  character is committed under its name (the fixture index does not claim
  `Sangraal-Sett` is the 70425 character). Still **[verify]**: whether
  `UnitSex` gives the new body type at the gather 2 s after
  `BARBER_SHOP_APPEARANCE_APPLIED` (not tested by this capture).
- **2026-11-09, AddOns.txt read as data (M16-34; the `addons-txt` row,
  part 2, 1.60.1.69977).** `wtfconfig.parse_addons` reads the file
  losslessly: a line is `<addon>: <state>` when the name is a run of bytes
  other than `:`, CR and LF, one `: ` follows, and the state is a run of
  bytes other than space, `:`, CR and LF; any other line is kept as
  `Unknown`, with its own ending. Both real lines match, and the file
  round-trips byte for byte. Names are compared with ASCII case folded, and
  when a name repeats the last line wins (both **[verify]**). `enabled` is
  the only state observed; `disabled` and any other word are kept as
  written. Nothing here settles the 2026-09-22 open points: an unlisted
  addon is at the client's default, and which copy the client reads stays
  **[verify]**. No contradiction with this reference.
- **2026-11-17, §11, BLP textures (M15-07; `lab/core/tests/fixtures/macos-70959/derived/blp.json`, Forever 1.60.1.70959, product `wowf`).**
  What `scripts/casc_facts.py --blp` read: 8,000 ids evenly spaced over the root (3,833 of them BLP2), and 300 of `ChrCustomizationMaterial`'s 25,116 textures (`TextureFileData` by `MaterialResourcesID`). The two samples may overlap. Seven points against the text above.
  (1) **Character textures are not all DXT.** Of the 300 character textures, 127 are DXT1, 87 DXT5 and 86 palette (alpha depth 0 or 8), from 128×32 and 64×128 up to 2048×1024. This contradicts "every character texture sampled at 70450 was DXT1 or DXT5 … 128² to 512²" for this build; the 70450 sample is not re-read here.
  (2) **No texture met where the two DXT rules disagree.** All 3,695 DXT headers with alpha type 0, 1 or 7 were depth/type 0/0, 1/0, 4/1, 8/1 or 8/7. BC5 (0/11, 8/11; 6 textures), which the wiki's rule does not cover, is refused before either rule is applied. The rule conflict stays **[verify]**: it was not met, so nothing settles which rule the client follows, and the decoder still refuses such a texture.
  (3) **Palette textures carry alpha type 8, and the palette's fourth byte (X) was 0 in every palette checked by hand: the fixture's five palette rows (depths 0, 1 and 8), all 256 entries of each; two of them, of depth 8, were also compared pixel by pixel with their alpha plane.** The palette bytes are not recorded in the fixture. The alpha comes from the separate plane after the indices. Depth 1 was met (6 textures); `blp` reads it LSB first as the wiki says, but no second decoder checked the order, so it stays **[verify]**. **Depth 4 was not met**, so the 4-bit palette alpha stays **[verify]**.
  (4) **The has-mips byte takes the values 0, 1, 2, 16 and 17**, so it is not a boolean; what the bits mean is **[verify]**. Mip counts (slots with nonzero offset and size) run from 1 to 12.
  (5) **No stored mip 0 size differed from the computed one** in the 4,127 textures decoded (BC5 skipped). Whether a later mip's stored size is ever wrong was not checked; the decoder still computes the size.
  (6) **BGRA (encoding 3) was met** at alpha depth 8 (11 textures) and 0 (1). That with depth 0 the stored alpha byte is ignored rests on that one texture with no second decoder, **[verify]**.
  (7) **A three-colour DXT1 block's index 3** is decoded as transparent black whatever the header's alpha depth. Whether the client draws it opaque in a texture of alpha depth 0 is **[verify]**. No depth-0 DXT1 texture met (2,053 in the root sample, 126 in the character sample) has an index 3 in a three-colour block, so on this build the question does not arise for those textures.
  Written first with the client running (2026-11-17) and regenerated with it closed (2026-11-18); every read was checked against its content hash, and every count the first run also took was the same; the index-3 counters and the `null` rules on BC5 rows are from the second run only.
- **2026-11-17, §12, the derived fixture `macos-70959/derived/m2-models.json`
  (M15-08; `humanmale_hd` 1011653 and `humanfemale_hd` 1000764 and their
  fourteen skins, read through `wowlab_core.m2`).** Confirmed on the launch
  client (`_forever_`, product `wowf`, 1.60.1.70959) as §12 states it for
  the beta (`_classic_beta_`, `wow_classic_beta`, 70450): version 272;
  chunks `MD21 TXAC AFID LDV1 SFID TXID BFID`, in that order, filling the
  file, no `SKID` (`AFID` and `BFID` point at external animation and bone
  files, by the wiki; unread here); bones (215, 201) and attachments (45,
  43) inside the M2; vertices 48 bytes, Z-up (the positions run in z from
  about 0 to 2.1 for the male and 2.0 for the female); textures 16 bytes,
  with `TXID` holding one id per texture, non-zero exactly for the type-0
  ones; attachments 40 bytes (id, bone, a u16, position, a 20-byte track;
  attachment id 11, the head by the wiki, at z 1.8476 and 1.7119,
  `head_attachment_z`); sections 48 and batches 24 bytes; `level << 16`
  added to the index start makes every section end inside the indices and
  the furthest end at the last one, while the vertex start needs no shift
  (the furthest section ends at the last lookup entry, below 65,536), so
  the wiki's "and alike" does not reach the vertex start here. The female
  model's textures are twelve (types 1, 6, 0, 2, 15, 16, 18, 17, 19, 0, 0,
  0), and its skin repeats some geoset ids across sections (3202, 3203,
  3204 twice each). Not in §12 until now: `SFID` holds the header's one
  view skin, then six LOD skins (the view count at 0x44 is 1). The skin
  header is 64 bytes, not 48 (`skin_header_size`, the lookup's offset, in
  all fourteen skins): after the five arrays a u32 at 0x2C, then a u32
  count at 0x30 of 104 (male) or 117 (female) in every skin
  (`shadow_batch_count`; shadow batches by the wiki, not read) and its
  offset, then eight zero bytes (`header_bytes_0x38_to_0x40_zero`).
  **Contradiction with the wiki, not with this reference:** the u32 at
  0x2C, which the wiki names `boneCountMax`, is in all fourteen skins where
  that skin's vertices start in the M2's vertex array: 0 for the view skin,
  then the running sum of the earlier skins' lookup counts (37,813,
  75,626, … for the male), the last skin's start plus its lookup count
  being exactly the model's vertex count (226,301 and 248,970). So, for
  these two models at 70959, §17.7's vertex-count question is settled: the
  M2's vertices are the seven skins' vertex sets one after another, the
  view skin's first (its lookup is the identity). That each LOD skin's
  lookup indexes its own set, relative to that start, rather than from
  vertex 0, is supported by one recorded comparison and stays
  **[verify]**: the lookup maxima fit both readings, but the mean triangle
  edge with the start added is near the view skin's (0.0423 to 0.0603 for
  the male against 0.0423; 0.0353 to 0.0500 for the female against
  0.0353), and from vertex 0 it is 2.7 to 5.2 times longer
  (`mean_edge_with_vertex_base`, `mean_edge_from_vertex_0`). Other models
  are **[verify]**. `m2` keeps the field as `Skin.vertex_base` and does not
  add it to the lookup (`Skin.triangles` is right as it stands for the
  view skin, whose base is 0). The MD20 name is empty in both models.
  The fixture was written three times (2026-11-17 twice, 2026-11-18 once
  for the review's added fields); every value that was in an earlier run
  was unchanged, the timings and `captured` aside.
- **2026-11-19, §4, the lab-addon's account file gains the crew book
  (M19-10; no fixture: ADR-0036 term 3 keeps every real crew record out of
  the repository, `docs/LAB_PLAN.md` §20.5).** From M19-10 the account
  `WowLab.lua` may hold a second top-level table, `WowLabCrewDB`, beside
  `WowLabDB` (the TOC's `## SavedVariables: WowLabDB, WowLabCrewDB`). It is
  absent until the player types `/wowlab crew on`, and the addon never
  creates it at load. Its layout is `docs/LAB_PLAN.md` §20.1's: `schema`
  (1, its own number; `WowLabDB`'s and the per-character schema do not
  change), `on`, `started`, `probe = { loads, lost? }` (as §13.1's, raised
  at every load while a book exists), `skipped`, `current` (keys to `true`)
  and `players`, keyed by the player's `Name-Realm` string, each entry with
  `class`, `level`, `role`, `guild`, `met`, `first`, `last`, `where`,
  `where_id`, `rating` and `note`, any of them left out. The keys are
  opaque strings to the reader: whether a cross-realm member's realm part
  arrives normalized, and whether a realm part can hold a space, are
  **[verify]** until the owner's in-game check (M19-07). How the client
  writes this table (key order, bracketed keys, CRLF) is expected to follow
  §4.2 as amended, like `WowLabDB`; no capture shows it yet **[verify]**.
  The empty shape the capture tool accepts is §20.5's "As built" note.
- **2026-11-20, §4, schema 2 of the crew book (M20-03; no fixture, as
  above: the reader is graded on constructed books and the owner's `@live`
  test).** `docs/LAB_PLAN.md` §21.2: `WowLabCrewDB.schema` 2 is the same
  table as schema 1 plus, at the top level, `matches` (a list, at most 500
  in game, oldest first; each entry a table of `at` and `map_id`, whole
  numbers, `map`, text, `kind`, `"bg"` or `"arena"`, `won`, true or false or
  left out, and `team` and `foes`, lists of player keys), `matched` (a table
  holding the map id and the time a finished match was first seen, emptied
  outside that instance; its field names are the addon's, not yet written),
  and `warned` (`time()` of the last near-full line, left out until the
  first). Each player entry may add `allied`, `faced`, `allied_won` and
  `faced_won` (whole numbers of 0 or more), `pvp_last` (a time), `pvp_map`
  (text) and `pvp_side` (`"with"` or `"against"`), all left out until the
  player's first match. `probe` may hold `pvp`, a list of one or two runs
  of the scoreboard probe, each a table of `calls` (call name to true or
  false), `positions` (a list of Lua type names), `fields` (field name to
  Lua type name), `requested`, `name_string`, `name_secret` and `name_dash`
  (true or false), and `score_events`, `complete_events`, `rows`,
  `first_side`, `own_side` and `winner` (whole numbers, each left out when
  not read); the shape is the one M20-04 pinned for the addon. The player
  limit is 20,000. The addon's migration of a schema 1 book that is on
  writes only the number and an empty `matches`. `wowlab` reads schema 1
  and 2 and writes back the schema it read; any other number is refused
  with it. How the client writes the lists (positional entries with
  `-- [n]` comments, §4.2) is expected and not yet seen in a capture
  **[verify]**, as is every field the addon of M20-05 and M20-08 writes.
- **2026-11-19, §12, bone tracks and their keys (M15-26,
  `macos-70959/derived/charmodel.json`, rows `sheath_rotation_keys` and
  `hand_bone_keys`; read by `scripts/charmodel_facts.py`, the library
  still reads no key).** The layout read, wowdev.wiki's, held on
  `humanmale_hd` (959310) and `humanfemale_hd` (917116) at 70959: an
  M2Track is interpolation u16 at 0, global sequence i16 at 2 (−1 when
  none), then two arrays of per-sequence arrays (`count, offset`),
  timestamps (u32) at 4 and values at 12; a rotation value is four i16
  (x, y, z, w), each decoded as `(s < 0 ? s + 32768 : s − 32767) / 32767`;
  a translation is three floats. The sequences are at MD20 0x1C (64 bytes
  each, animation id u16 at 0, flags u32 at 12), the global sequence
  durations at 0x14 (u32, ms). Observed: every track read that has a
  key array is linear (interpolation 1); the hand points' own bones' empty
  tracks (213, 214; 206 and 207's translation) are recorded with
  interpolation 0 and no array; every decoded rotation has a length within 5e-5 of 1,
  which supports the decoding rule (still **[verify]** as a rule); a track
  on a global sequence has one array, and its keys are inside the M2; each
  per-sequence array read belongs to a sequence with flag 0x20 and its
  keys are inside the M2 (the `.anim` case was not met). The array index
  is the sequence index, not the animation id: on 959310 sequence 0 is
  animation id 5. The first rotation keys of the bones under points 26 to
  33, as (x, y, z, w) and the angle turned (2·acos w):

  | point | 959310 | 917116 |
  |---|---|---|
  | 26 | (−0.304, 0.450, 0.638, 0.546), 113.9° | (−0.411, 0.282, 0.576, 0.648), 99.2° |
  | 27 | (0.304, 0.450, −0.638, 0.546), 113.9° | (0.411, 0.282, −0.576, 0.648), 99.2° |
  | 28 | (0.464, −0.412, 0.517, 0.590), 107.7° | (0.379, −0.542, 0.575, 0.481), 122.5° |
  | 29 | no track | (0, 0, 0, 1), 0° |
  | 30 | (−0.641, −0.579, −0.299, 0.406), 132.1° | (−0.572, −0.646, −0.416, 0.288), 146.6° |
  | 31 | (0.641, −0.579, 0.299, 0.406), 132.1° | (0.572, −0.646, 0.416, 0.288), 146.6° |
  | 32 | (0.252, 0.247, −0.933, 0.068), 172.2° | (0.225, 0.147, −0.963, 0.001), 179.8° |
  | 33 | (−0.250, 0.255, 0.932, 0.066), 172.5° | (0.235, −0.131, −0.961, 0.067), 172.3° |

  Global sequence and keys: 959310, all on 0 (1,079,000 ms), 207 keys
  (26 to 28, 30, 31) and 333 (32, 33); 917116, 2 (688,333 ms, 24 keys) for
  26 to 28, 30, 31, and 0 (1,060,333 ms, 476 keys) for 29, 32, 33. No key
  of these loops is more than 1.5° from its first. The hand points' parents
  (keys 66, 67), first array of animation id 0, translation: 959310
  (0.077, −0.791, 1.137) and (0.078, 0.792, 1.136); 917116
  (−0.020, ∓0.573, 0.909). What the numbers mean for drawing is
  `docs/LAB_PLAN.md` §17.6's note of the same date. Unverified before
  anything is drawn with the sheath keys: the decoding rule; composing a
  key with its animated spine parent; an item model's long axis and where
  its grip origin sits; the axis signs; whether the sheath bones have scale
  tracks (not read); the `SheatheType` mapping, which point of each
  left/right pair a main-hand or off-hand weapon uses, and whether the
  client adds its own adjustment per `SheatheType`. `animation_0_array`
  takes the first array with animation id 0 and keys
  (`scripts/charmodel_facts.py`, `_keys`) and ignores variations and alias
  sequences; the animation lookup array at MD20 0x24 is the reliable way
  to find Stand, and whether id 0 is Stand is from memory. Point 29 is
  believed to be a nameplate point, not a sheath point, which would
  explain its missing rotation on 959310 (**[verify]**).
- **2026-11-19, §12, drawing held items sheathed (M15-27; no new reading of
  the install: the derived rows of `macos-70959/derived/charmodel.json`
  grade it, rebuilt into constructed body models).** `m2.bone_chain` now
  reads, on request, one bone and its parents (key bone id, flags, parent,
  pivot at 76) and the first key of each one's rotation track at 36, only
  when that track runs on a global sequence; no other key and no
  translation or scale track is read. `char model` rests on these
  assumptions, every one **[verify]** until the owner's look in a viewer:

  | assumption | taken as (each a choice, not an observation) |
  |---|---|
  | the decoding rule | `(s < 0 ? s + 32768 : s − 32767) / 32767`, (x, y, z, w); a key whose length is more than 0.01 from 1 is refused, others normalised |
  | the carried angle | the sheath bone's first rotation key on its global sequence |
  | the transform | `T(pivot)·T(t)·R(q)·T(−pivot)` with t = 0; the parents' rotations are ignored (a choice: the body is drawn unposed, so the weapon matches the mesh, not the in-game Stand pose; it may be off by the spine's Stand tilt) **[verify]**; the point's position is turned with it, its rotation is the item's |
  | the item's axes | the item model drawn as stored in the point's axes: no axis is taken as its length, its origin is taken as its grip, and no per-`SheatheType` correction is added |
  | the axis signs | `gltf.to_y_up` for positions, the same mapping of the axis for the rotation |
  | `SheatheType` → point | 2 → 30/31, 3 → 32/33, 4 → 28, from the values the recorded 70959 `Item` subset gives the fixture items (staff 2, one-hander 3, shield 4, wand 0), not from any enum's names (TrinityCore's contradict that data); 1 → 26/27 plausible and unchecked; 0 and any other number not drawn sheathed |
  | left and right | the main hand takes 26, 30 or 32, the off hand 27, 31 or 33, a shield 28, by the community's names; 30 and 32 lie on the character's left (cross-draw), 26 on the right **[verify]** |

  On the fixture characters this puts Sangraal-Sett's dagger (type 3) at 32
  and its off-hand item (type 3) at 33, and Dangling-Participle's staff
  (type 2) at 30. The ranged slot stays unsupported.
