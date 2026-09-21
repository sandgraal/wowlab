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
`.build.info`. `scrub` records what the tool rewrote (`identity-rewritten`,
`cvars-dropped: n`, or `none`).

## Index

| file | kind | flavor | client_version | platform | captured_by | consent | scrub | edge_cases |
|------|------|--------|----------------|----------|-------------|---------|-------|------------|
| `wago/builds.json.gz` | wago-builds-json | n/a (all products) | n/a (listing of every build) | n/a (http) | implementer M10-08 | owner | none | GET https://wago.tools/api/builds on 2026-09-21; 200 application/json. Stored gzip -9 (mtime 0) of the verbatim body because the body is 539664 bytes and the repository limit is 512 KB; sha256 of the decompressed body c0cc466b356b5ebbdc8b8ea8369b251f7d51bed6ebfa3d1c471f9392e5b18c8c. 13 products, 2271 entries, 1695 distinct versions; `product_config` null on some entries; every product list sorted by version descending, and a product code reused across game versions (wow_classic_beta carries 1.13, 2.5, 3.4, 4.4, 5.5 and 1.60), so neither position nor highest version means newest; trailing build number not unique (10.0.0.46479 / 10.0.2.46479, 2.5.5.68575 / 2.5.6.68575) |
| `wago/ChrClasses.1.60.1.69876.csv` | wago-csv | n/a (listed under wow_classic_beta) | 1.60.1.69876 | n/a (http) | implementer M10-08 | owner | none | GET https://wago.tools/db2/ChrClasses/csv?build=1.60.1.69876 on 2026-09-21; 200 text/csv; charset=UTF-8; Content-Disposition filename="ChrClasses.1.60.1.69876.csv". Not the newest build of its product, which is what proves `build=` selects. 9 rows, 43 columns, LF, no BOM, quoted fields containing commas, empty fields. Re-fetched the same day by the live test: identical bytes |
| `wago/ChrClasses.1.60.1.1.404.html` | wago-error-html | n/a | 1.60.1.1 (never published) | n/a (http) | implementer M10-08 | owner | none | GET https://wago.tools/db2/ChrClasses/csv?build=1.60.1.1 on 2026-09-21; 404 text/html; charset=utf-8. Body only: the response set XSRF and session cookies, which are not committed |
