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

`NAME` is a Lua identifier. `nil` is legal only as a top-level value.

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

Identity-bearing CVars the scrubber blanks: `accountName`, `accountList`,
`lastCharacterGuid`. The list is confirmed and extended from the first
capture **[verify]**; the deny-list lives in `wowlab_core.redact`
(`IDENTITY_CVARS`, moved there from `scripts/lab_capture.py` on 2026-10-14,
M13-09), which the scrub tool imports.

## 6. `bindings-cache.wtf`

`bind KEY ACTION` per line, e.g. `bind CTRL-1 ACTIONBUTTON1`,
`bind BUTTON4 "CLICK SomeAddonButton:LeftButton"`. Header or mode lines
exist in some versions **[verify]**; keep anything unmatched as `Unknown`.

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
header vertices (**[verify]** why: LOD skins, probably).

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
  the default is **[verify]**). Also recorded verbatim, meaning unknown:
  `portal "test"`, `currentGameMode "15"`, `engineSurveyPatch "16001"`.
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
  be schema 4 with its own capture. `looks import-char` keeps saying "as of
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
