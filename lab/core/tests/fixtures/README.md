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
`guids-rewritten: n`, `embedded: n`, joined with `; `, or `none`. The tool
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
