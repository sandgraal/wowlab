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
capture **[verify]**; the deny-list lives in `scripts/lab_capture.py`.

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
  same NPC can log other values. Do not key on them.
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
  | Forever fills the crafter field of an item link (`crafter_removed`) | still open | 18 equipped items over two characters: `crafter_removed = false` on every slot and no GUID-shaped run in any link. Whether any of them is player-crafted was not checked against game data, so the capture cannot tell "Forever leaves the field empty" from "nothing crafted was worn"; it needs a worn item the owner knows a player crafted. |
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
