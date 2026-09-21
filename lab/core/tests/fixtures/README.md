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
