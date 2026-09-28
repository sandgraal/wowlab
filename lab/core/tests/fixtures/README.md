# Fixture corpus

Real captures from a real World of Warcraft install, plus small recorded
responses from wago.tools. **No constructed examples here.** Constructed
inputs are allowed only for hostile-input and boundary tests, live inside
the test that uses them, and are labelled `constructed` (invariant L8).

## Rules

- **Capture tool only.** Every install file arrives through
  `scripts/lab_capture.py` (ticket M10-02), which replaces account folder,
  character and realm names with stable pseudonyms, blanks identity CVars,
  and refuses to emit a file that still contains an email address, a
  BattleTag or an unmapped `Player-<n>-<hex>` GUID. The repository is
  public; an unscrubbed capture is a leak.
- **Nothing else is edited.** Structure, key order, number text, escapes and
  line endings stay byte for byte. `.gitattributes` turns off end-of-line
  conversion for this directory.
- **Provenance.** Every file has a row below. A file without a row, or a row
  without a file, fails `make test-parser`.
- **Consent.** `owner` for the repository owner's own install; `explicit`
  for a file from anyone else's.
- **Staging.** Raw captures land in `incoming/` (gitignored). Review them,
  move them to `<platform>/<flavor-kind>/`, then add the rows. Never commit
  from `incoming/`.
- **Immutability.** Never edit a committed fixture. A new client version is
  a new file.
- **Not fixtures.** Blizzard's exported interface code and art are never
  committed.
- **wago.tools recordings** go under `wago/`, small tables only, with the
  URL and capture date in `edge_cases`.

Runbook for the owner's capture: `docs/handoffs/M10-03.md`.

## Columns

`file` is the path relative to this directory. `kind` is the format
(`build-info`, `flavor-info`, `savedvariables`, `config-wtf`, `bindings`,
`macros`, `toc`, `combatlog`, `wago-csv`, …). `flavor` is the flavor folder
as found on disk. `client_version` is the full version string from
`.build.info`. `platform` is the client that last wrote the file (`macos`,
`windows`), not the machine the capture ran on: it is there to explain line
endings and path spelling, so a capture made under Wine/Proton or from a
mounted drive must pass `--platform`. `scrub` records what the tool rewrote,
as it prints it: `identity-rewritten: n` (or `path only`), `cvars-blanked: n`,
`guids-rewritten: n`, `unit-guids-rewritten: n`,
`other-players-pseudonymised: n`, `timestamps-shifted`, `embedded: n`,
joined with `; `, or `none`. The tool
prints each row ready to paste. Run it with
`--kind <flavor folder>=<flavor-kind>` and the `file` cell is already the
final path: move `incoming/<platform>/` up one level and the rows match.

What the scrub leaves behind is an artefact of the scrub, not evidence about
the client:

- A blanked line (`SET accountName ""`) says only that the CVar exists and
  how its line is shaped. It is not evidence that the client writes empty
  values.
- Pseudonyms (`Labchara`, `Labrealma Partb`, `90000001#1`,
  `Player-9999-00000001`) keep the real name's spaces, hyphens and
  apostrophes, so the relation between a realm's folder spelling and its
  normalised spellings survives. Their length, letters and casing pattern
  are invented, and they are always ASCII: a non-ASCII name becomes an ASCII
  pseudonym, so an ASCII name in a fixture says nothing about the real one.
  Non-ASCII coverage comes from the `non-ASCII strings` SavedVariables pick,
  a note that is only given to a file that still has such bytes after the
  scrub.
  The Forever second name gets a realm-style pseudonym (`Labrealm…`, as in
  `Labcharb-Labrealmd`) although it is not a realm (`docs/GLOSSARY.md`,
  Second name).
  Pseudonyms are stable within one capture run only: the tool assigns them
  in sorted order of the real names over the whole install, so part 1
  (`Labcharb-Labrealmd`) and part 2 (`Labchard-Labrealme`) are two runs and
  a pseudonym in one is not proven to mean the same real name in the other.
  Part 3 is a third run (build 70009): its log and `DBM-Party-Vanilla.lua`
  use its own spellings (`Labchard`, `Labrealmg`, `LabrealmbPartbPartcPartd`),
  which prove nothing about parts 1 and 2.
- Combat logs from part 3 on are captured with shifted timestamps (M10-02
  follow-up 5, `timestamps-shifted`): every stamp and the file name moved by
  one secret offset per log, format kept, so neither the date nor the time
  of day in a combat-log fixture is real.
- In a combat log, the server, instance, zone and spawn parts of every
  non-player unit GUID (`Creature-0-1-0-2-<npc id>-0000000000`) are invented,
  numbered in order of first appearance, from 2026-09-24 on
  (`unit-guids-rewritten: n`). The type and the NPC or object id are real. A
  zero server, instance or zone field is kept; a spawn UID is always
  renumbered. Equal invented values mean equal real values within one run
  only; the numbers themselves say nothing about the server, zone or spawn
  time.
- From 2026-09-24 on, a combat log's timestamps and the date in its file
  name are moved by a secret offset drawn for that log alone
  (`timestamps-shifted`). Every shifted timestamp is written in one fixed
  shape, `M/D/YYYY HH:MM:SS`, with the milliseconds and the UTC-offset suffix
  copied as written. That shape was verified on 2026-09-24 against the
  owner's real logs (count-only check). The time of day and the exact date are not the
  session's. The date is still bounded by the build's live window and by the
  commit date. The suffix still shows whether the session was in
  daylight-saving time. Durations and the order of events within a log are
  real.
- `embedded: n` counts replacements that touched a neighbouring letter or
  digit (a character named like the start of a longer word). Read those
  lines before trusting the file's vocabulary. CVar names and TOC directive
  keys are never rewritten.

## Index

| file | kind | flavor | client_version | platform | captured_by | consent | scrub | edge_cases |
|------|------|--------|----------------|----------|-------------|---------|-------|------------|
| `wago/builds.json.gz` | wago-builds-json | n/a (all products) | n/a (listing of every build) | n/a (http) | implementer M10-08 | owner | none | GET https://wago.tools/api/builds on 2026-09-21; 200 application/json. Stored gzip -9 (mtime 0) of the verbatim body because the body is 539664 bytes and the repository limit is 512 KB; sha256 of the decompressed body c0cc466b356b5ebbdc8b8ea8369b251f7d51bed6ebfa3d1c471f9392e5b18c8c. 13 products, 2271 entries, 1695 distinct versions; `product_config` null on some entries; every product list sorted by version descending, and a product code reused across game versions (wow_classic_beta carries 1.13, 2.5, 3.4, 4.4, 5.5 and 1.60), so neither position nor highest version means newest; trailing build number not unique (10.0.0.46479 / 10.0.2.46479, 2.5.5.68575 / 2.5.6.68575) |
| `wago/ChrClasses.1.60.1.69876.csv` | wago-csv | n/a (listed under wow_classic_beta) | 1.60.1.69876 | n/a (http) | implementer M10-08 | owner | none | GET https://wago.tools/db2/ChrClasses/csv?build=1.60.1.69876 on 2026-09-21; 200 text/csv; charset=UTF-8; Content-Disposition filename="ChrClasses.1.60.1.69876.csv". Not the newest build of its product, which is what proves `build=` selects. 9 rows, 43 columns, LF, no BOM, quoted fields containing commas, empty fields. Re-fetched the same day by the live test: identical bytes |
| `wago/ChrClasses.1.60.1.1.404.html` | wago-error-html | n/a | 1.60.1.1 (never published) | n/a (http) | implementer M10-08 | owner | none | GET https://wago.tools/db2/ChrClasses/csv?build=1.60.1.1 on 2026-09-21; 404 text/html; charset=utf-8. Body only: the response set XSRF and session cookies, which are not committed |
| `wago/builds.2026-09-28.json.gz` | wago-builds-json | n/a (all products) | n/a (listing of every build) | n/a (http) | implementer M11-05 | owner | none | GET https://wago.tools/api/builds on 2026-09-28 through `GameData.builds`; 200 application/json. A new recording beside `builds.json.gz`, which predates 1.60.1.70009 and is not edited. Stored gzip -9 (mtime 0) of the verbatim body, as the earlier listing; the body is 540921 bytes; sha256 of the decompressed body 303a11ec5f5b3d75d7e02026a2a6308cd6b436be68b8d3acb8586fb345e55c50. 13 products, 2276 entries, 1700 distinct versions; wow_classic_beta lists 1.60.1.70009 (created_at 2026-09-24 22:02:03), 69977, 69913, 69893 and 69876 |
| `wago/ChrRaces.1.60.1.70009.csv` | wago-csv | n/a (listed under wow_classic_beta) | 1.60.1.70009 | n/a (http) | implementer M11-05 | owner | none | GET https://wago.tools/db2/ChrRaces/csv?build=1.60.1.70009 on 2026-09-28 through `GameData.table` (Content-Disposition filename checked); 200 text/csv. 18654 bytes, sha256 29c3157eb94f9eaa3aa8981e434a051478cd58283fefe223b5976dd2bb1e7ba9. 58 rows, 62 columns, LF, no BOM, quoted fields containing commas, empty fields, `Field_9_1_0_38312_*` unnamed columns. Retail's races are present; only races 1-8, 95 and 96 have a `PlayableRaceBit` and lack flag 0x1 |
| `wago/ChrModel.1.60.1.70009.csv` | wago-csv | n/a (listed under wow_classic_beta) | 1.60.1.70009 | n/a (http) | implementer M11-05 | owner | none | GET https://wago.tools/db2/ChrModel/csv?build=1.60.1.70009 on 2026-09-28 through `GameData.table` (Content-Disposition filename checked); 200 text/csv. 12765 bytes, sha256 e4a38dffcbdfb9a0b72deced463475d5bf15b3f6deb38e8b0092b87aa7bb751e. 127 rows, 21 columns, LF, no BOM, decimal fractions |
| `wago/ChrRaceXChrModel.1.60.1.70009.csv` | wago-csv | n/a (listed under wow_classic_beta) | 1.60.1.70009 | n/a (http) | implementer M11-05 | owner | none | GET https://wago.tools/db2/ChrRaceXChrModel/csv?build=1.60.1.70009 on 2026-09-28 through `GameData.table` (Content-Disposition filename checked); 200 text/csv. 1616 bytes, sha256 f4fbed956c442dc7829f7552ab1392dbe8888c866dcc68b60d5750f6835065c5. 116 rows, 5 columns, LF, no BOM. One model can serve both `Sex` values (Dracthyr, the drakes) and several races (the Pandaren, Earthen and Skyborne pairs) |
| `wago/ChrCustomizationOption.1.60.1.70009.csv` | wago-csv | n/a (listed under wow_classic_beta) | 1.60.1.70009 | n/a (http) | implementer M11-05 | owner | none | GET https://wago.tools/db2/ChrCustomizationOption/csv?build=1.60.1.70009 on 2026-09-28 through `GameData.table` (Content-Disposition filename checked); 200 text/csv. 54342 bytes, sha256 a2d03ab0041a40debe40cf3eaba1a31f9cdc7fa27bab1bbd3a756d06ce99f6b7. 1173 rows, 13 columns, LF, no BOM, 3 empty cells. The requirement column is named `Requirement` |
| `wago/ChrCustomizationChoice.1.60.1.70009.csv` | wago-csv | n/a (listed under wow_classic_beta) | 1.60.1.70009 | n/a (http) | implementer M11-05 | owner | none | GET https://wago.tools/db2/ChrCustomizationChoice/csv?build=1.60.1.70009 on 2026-09-28 through `GameData.table` (Content-Disposition filename checked); 200 text/csv. 427226 bytes, sha256 bd97d1d7f844d6ed2105a2574cd44d431d8cd59b50a93641660cbfaf972ce86d. 10447 rows, 12 columns, LF, no BOM, one quoted field containing a comma, 5114 empty cells (most choices have no name). Every choice names a requirement |
| `wago/ChrCustomizationReq.1.60.1.70009.csv` | wago-csv | n/a (listed under wow_classic_beta) | 1.60.1.70009 | n/a (http) | implementer M11-05 | owner | none | GET https://wago.tools/db2/ChrCustomizationReq/csv?build=1.60.1.70009 on 2026-09-28 through `GameData.table` (Content-Disposition filename checked); 200 text/csv. 14436 bytes, sha256 0b631ab06f2fdc007788471788b8a74841c92fb012eed51ce36603c0e8b21650. 492 rows, 11 columns, LF, no BOM. Negative masks (`-1`, `-2081`, `-7168`); the race mask split across `RaceMasks_0` and `RaceMasks_1`; no row has an achievement, quest or item-appearance id; `ReqType` values 0, 1, 2, 3, 4, 11; `RegionGroupMask` 16 on two rows, `OverrideArchive` 1 on one |
| `wago/ChrCustomizationReqChoice.1.60.1.70009.csv` | wago-csv | n/a (listed under wow_classic_beta) | 1.60.1.70009 | n/a (http) | implementer M11-05 | owner | none | GET https://wago.tools/db2/ChrCustomizationReqChoice/csv?build=1.60.1.70009 on 2026-09-28 through `GameData.table` (Content-Disposition filename checked); 200 text/csv. 5746 bytes, sha256 810ca3e0cbe18f6744db6c1329d8695f6725aced54b6596a45100c65ec9d2dbb. 446 rows, 3 columns, LF, no BOM; 79 requirements, each naming choices of one option |
| `wago/ChrCustomizationElement.1.60.1.70009.csv.gz` | wago-csv | n/a (listed under wow_classic_beta) | 1.60.1.70009 | n/a (http) | implementer M11-05 | owner | none | GET https://wago.tools/db2/ChrCustomizationElement/csv?build=1.60.1.70009 on 2026-09-28 through `GameData.table` (Content-Disposition filename checked); 200 text/csv. Stored gzip -9 (mtime 0) of the verbatim body because the body is 1646699 bytes and the repository limit is 512 KB; sha256 of the decompressed body bc101374481ac0e9fadcb3e479026d2909799ae7fa352efc639fd10c34ff68c1. 39109 rows, 14 columns, LF, no BOM |
| `wago/ChrCustomizationCategory.1.60.1.70009.csv` | wago-csv | n/a (listed under wow_classic_beta) | 1.60.1.70009 | n/a (http) | implementer M11-05 | owner | none | GET https://wago.tools/db2/ChrCustomizationCategory/csv?build=1.60.1.70009 on 2026-09-28 through `GameData.table` (Content-Disposition filename checked); 200 text/csv. 2861 bytes, sha256 8ded9a140dd9768fa2dd3f193dbb04178e54a041aae184a2d10d6ad94201fb6f. 62 rows, 9 columns, LF, no BOM |
| `wago/ChrCustomizationConversion.1.60.1.70009.csv` | wago-csv | n/a (listed under wow_classic_beta) | 1.60.1.70009 | n/a (http) | implementer M11-05 | owner | none | GET https://wago.tools/db2/ChrCustomizationConversion/csv?build=1.60.1.70009 on 2026-09-28 through `GameData.table` (Content-Disposition filename checked); 200 text/csv. 69728 bytes, sha256 d5e23493a78a74762647bf73e6233e30fe1579bdac64a616661e34cb5154994b. 2815 rows, 9 columns, LF, no BOM, an unnamed `Field_3_4_0_45166_007` column (-1 on every row). It maps (race, sex, legacy option 1-5, byte value) to a choice id, not alternate forms |
| `macos/.build.info` | build-info | (install root) | 1.60.1.69913 | macos | owner | owner | none | LF |
| `macos/forever/.flavor.info` | flavor-info | _classic_beta_ | 1.60.1.69913 | macos | owner | owner | none | LF |
| `macos/forever/WTF/Config.wtf` | config-wtf | _classic_beta_ | 1.60.1.69913 | macos | owner | owner | cvars-blanked: 3 | LF |
| `macos/forever/WTF/Account/90000001#6/config-cache.wtf` | config-wtf | _classic_beta_ | 1.60.1.69913 | macos | owner | owner | identity-rewritten: path only | LF |
| `macos/forever/WTF/Account/90000001#6/bindings-cache.wtf` | bindings | _classic_beta_ | 1.60.1.69913 | macos | owner | owner | identity-rewritten: path only | CRLF |
| `macos/forever/WTF/Account/90000001#6/macros-cache.txt` | macros | _classic_beta_ | 1.60.1.69913 | macos | owner | owner | identity-rewritten: path only | 0 bytes (no account macros) |
| `macos/forever/WTF/Account/90000001#6/chat-frontend-cache.txt` | chat-frontend-cache | _classic_beta_ | 1.60.1.69913 | macos | owner | owner | identity-rewritten: path only | 0 bytes |
| `macos/forever/WTF/Account/90000001#6/flagged-cache-account.txt` | flagged-cache | _classic_beta_ | 1.60.1.69913 | macos | owner | owner | identity-rewritten: path only | no trailing newline |
| `macos/forever/WTF/Account/90000001#6/tts-cache-account.txt` | tts-cache | _classic_beta_ | 1.60.1.69913 | macos | owner | owner | identity-rewritten: path only | mixed CRLF and LF |
| `macos/forever/WTF/Account/90000001#6/edit-mode-cache-account.txt` | edit-mode-cache | _classic_beta_ | 1.60.1.69913 | macos | owner | owner | identity-rewritten: path only | no trailing newline |
| `macos/forever/WTF/Account/90000001#6/1/Labcharb-Labrealmd/config-cache.wtf` | config-wtf | _classic_beta_ | 1.60.1.69913 | macos | owner | owner | identity-rewritten: path only; cvars-blanked: 1 | LF |
| `macos/forever/WTF/Account/90000001#6/1/Labcharb-Labrealmd/macros-cache.txt` | macros | _classic_beta_ | 1.60.1.69913 | macos | owner | owner | identity-rewritten: path only | CRLF |
| `macos/forever/WTF/Account/90000001#6/1/Labcharb-Labrealmd/layout-local.txt` | layout-local | _classic_beta_ | 1.60.1.69913 | macos | owner | owner | identity-rewritten: path only | LF |
| `macos/forever/WTF/Account/90000001#6/1/Labcharb-Labrealmd/chat-cache.txt` | chat-cache | _classic_beta_ | 1.60.1.69913 | macos | owner | owner | identity-rewritten: path only | LF |
| `macos/forever/WTF/Account/90000001#6/1/Labcharb-Labrealmd/click-bindings-cache.txt` | click-bindings-cache | _classic_beta_ | 1.60.1.69913 | macos | owner | owner | identity-rewritten: path only | LF |
| `macos/forever/WTF/Account/90000001#6/1/Labcharb-Labrealmd/flagged-cache-character.txt` | flagged-cache | _classic_beta_ | 1.60.1.69913 | macos | owner | owner | identity-rewritten: path only | no trailing newline |
| `macos/forever/WTF/Account/90000001#6/1/Labcharb-Labrealmd/tts-cache-character.txt` | tts-cache | _classic_beta_ | 1.60.1.69913 | macos | owner | owner | identity-rewritten: path only | mixed CRLF and LF |
| `macos/forever/WTF/Account/90000001#6/1/Labcharb-Labrealmd/edit-mode-cache-character.txt` | edit-mode-cache | _classic_beta_ | 1.60.1.69913 | macos | owner | owner | identity-rewritten: path only | no trailing newline |
| `macos/forever/Interface/AddOns/DBM-Challenges/DBM-Challenges.toc` | toc | _classic_beta_ | 1.60.1.69913 | macos | owner | owner | none | bracketed load condition or variable; CRLF |
| `macos/forever/WTF/Account/90000001#6/Labrealmb Partb Partc Partd/Labchard/AddOns.txt` | addons-txt | _classic_beta_ | 1.60.1.69977 | macos | owner | owner | identity-rewritten: path only | CRLF |
| `macos/forever/WTF/Account/90000001#6/SavedVariables/RareScanner.lua` | savedvariables | _classic_beta_ | 1.60.1.69977 | macos | owner | owner | identity-rewritten: path only | smallest file (23 bytes, no nesting; `X = nil`); CRLF; leading blank line |
| `macos/forever/WTF/Account/90000001#6/SavedVariables/Blizzard_GamepadSmartNavigation.lua` | savedvariables | _classic_beta_ | 1.60.1.69977 | macos | owner | owner | identity-rewritten: path only | single `= nil` assignment (37 bytes, no nesting); named as a Blizzard addon **[verify]**, with no `Interface/AddOns/` folder (conductor's listing of the install); file named after the addon, not the variable; CRLF; leading blank line |
| `macos/forever/WTF/Account/90000001#6/SavedVariables/Syndicator.lua` | savedvariables | _classic_beta_ | 1.60.1.69977 | macos | owner | owner | identity-rewritten: 8 | largest file that passed the scrub (14484 bytes); nesting depth 6, no indentation; 82 positional entries with no `-- [n]` comment; two-line empty tables; empty key `[""]`; named colour-code and item-link escapes; character stored as `"<First> <Second>"` with `realm = ""`; CRLF; leading blank line |
| `macos/forever/WTF/Account/90000001#6/SavedVariables/DBM-StatusBarTimers.lua` | savedvariables | _classic_beta_ | 1.60.1.69977 | macos | owner | owner | identity-rewritten: path only | negative numbers and long floats (3680 bytes); nesting depth 3, no indentation; `\\` escapes; CRLF; leading blank line |
| `macos/forever/WTF/Account/90000001#6/SavedVariables/RareScanner.lua.bak` | savedvariables | _classic_beta_ | 1.60.1.69977 | macos | owner | owner | identity-rewritten: path only | previous write (.lua.bak), byte-identical to `RareScanner.lua`; CRLF; leading blank line |
| `macos/forever/Interface/AddOns/DBM-Brawlers/DBM-Brawlers.toc` | toc | _classic_beta_ | 1.60.1.69977 | macos | owner | owner | none | single-TOC addon; no blank line between the directives and the file lines; single-value `## Interface: 120100` (the author's claim); raw UTF-8 in localized keys; no BOM; CRLF |
| `macos/forever/WTF/Account/90000001#6/SavedVariables/Blizzard_AuctionHouseUI.lua` | savedvariables | _classic_beta_ | 1.60.1.70009 | macos | owner | owner | identity-rewritten: path only | mixed table (2360 bytes): 23 positional entries without `-- [n]` comments, 20 holding two tables and 3 empty ones written over two lines, then the string key `["auctionHouseSortVersion"]`; nesting depth 3, no indentation; CRLF; leading blank line |
| `macos/forever/WTF/Account/90000001#6/SavedVariables/DBM-Party-Vanilla.lua` | savedvariables | _classic_beta_ | 1.60.1.70009 | macos | owner | owner | identity-rewritten: 2 | largest SavedVariables fixture (19592 bytes, 1757 lines, nesting depth 4); one top-level variable `DBMPartyVanilla_AllSavedVars`; character key `"<First> <Second>"` with a space (both pseudonymised; the second name gets a `Labrealm…` pseudonym and is not a realm); no indentation, no `-- [n]`; ASCII only; CRLF; leading blank line; explicit integer key `[0]` (350) and digit-string keys such as `["452"]` (188), the first of either in the corpus |
| `macos/forever/Logs/WoWCombatLog-040126_021630.txt` | combatlog | _classic_beta_ | 1.60.1.70009 | macos | owner | owner | identity-rewritten: 154; guids-rewritten: 97; unit-guids-rewritten: 104; timestamps-shifted | whole log, 79 lines, one solo open-world fight against one NPC, no other players; `client_version` is the install's build at capture, the log header names only the patch (`BUILD_VERSION,1.60.1,PROJECT_ID,18`); file name and every stamp shifted by a secret offset (shape `M/D/YYYY HH:MM:SS.mmm-4`; the single-digit day and month are written by the tool); unit names `"<Name>-<Realm>-"` (77) with an empty third component, the realm without its spaces and no second name; `0000000000000000,nil,0x80000000,0x80000000` for an absent unit; flags owner `0x511`, NPC `0x10a48`; advanced block on cast-success, damage, periodic-damage, heal and swing records; `SWING_DAMAGE_LANDED` repeating every `SWING_DAMAGE`; `SPELL_AURA_REFRESH`; `SPELL_CAST_FAILED` ending in a quoted reason; no `COMBATANT_INFO` and no `[...]` or `(...)` groups; CRLF |
