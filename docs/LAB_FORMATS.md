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

## 9. wago.tools responses

Recorded, not specified. The fixture for the builds endpoint and for one
small table's CSV export are the reference for `gamedata`
(`docs/DATA_SOURCES.md`).

## Amendments

Dated entries, newest last. Each names the fixture that prompted it.
