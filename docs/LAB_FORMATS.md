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

> **Note 2026-09-22 (M10-07 domain review), all [verify], from memory, none
> observed:** other line kinds a `bindings-cache.wtf` may carry are
> `BINDINGMODE <n>`, `modifiedclick <ACTION> <MODIFIER>`, and cleared keys
> written as `bind KEY NONE` or with an empty action. `wowlab_core.wtfconfig`
> keeps `BINDINGMODE`, `modifiedclick` and an empty-action line as `Unknown`,
> and types `bind KEY NONE` as-is (action `NONE`).

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
  `macros-cache.txt`, the TOCs), neither (the edit-mode caches, one line
  ending in a NUL byte; the flagged caches, exactly `2` then a NUL) and both
  within one file (the text-to-speech caches: first line LF, the rest CRLF)
  in the same install. A text reader that drops a trailing NUL breaks L4. Serializers keep each document's
  own endings; a writer creating a new file chooses by file kind.
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
