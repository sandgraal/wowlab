---
name: fixture
description: Add a real /simc addon export to the parser fixture corpus with provenance, consent, a secrets scan, and an index row — rejects constructed examples. Use for every fixture under api/tests/fixtures/simc/.
argument-hint: <path-to-export.txt> --class evoker --spec augmentation --version 12.1.0 --by "owner" --consent owner|explicit|public-post [--rewrite-identity]
---

Add the export at the path in `$ARGUMENTS` to `api/tests/fixtures/simc/`.

1. **Validate it is a real addon export.** Line 1 is the addon header
   comment (`# <Name> - <Spec> - <date> - <Region>/<Realm>`); within the
   first 12 lines there is a class-key name line (`evoker="Name"` etc.,
   one of the 13 keys in `docs/SIMC_FORMAT.md`); there are `level=`,
   `spec=`, `talents=` lines and at least ten slot lines with `,id=`. If
   any of this is missing, refuse: the corpus takes real exports only.
2. **Consent.** Require `--consent` to be `owner`, `explicit`, or
   `public-post` (for `public-post`, `--by` must include the public link).
3. **Identity rewrite (optional).** With `--rewrite-identity`, replace the
   character name in the header and the name line, and the realm in the
   header and `server=` line, with `Fixture<Class>` / `fixture-realm`.
   Change nothing else — not one bonus id, gem, crafted stat, talent
   string, or comment section. Note `identity-rewritten` in `edge_cases`.
4. **Secrets.** Run `gitleaks detect --no-git --source <file>` (or
   `uv run pre-commit run gitleaks --files <file>`); anything that trips
   beyond the allowlisted talent/checksum patterns is a hard stop.
5. **Name and place.** `<class>_<spec>_<version>[_<edge>].simc`, bytes
   unchanged (`.gitattributes` disables line-ending conversion for `*.simc`).
6. **Sections and edge cases.** Record which comment sections are present
   (`bags`, `vault`, `loadouts`, `extra`) and edge cases (`crafted`,
   `empty-socket`, `tertiary`, `professions`, `healer`, `tank`, …).
7. **Index row.** Append to the table in `api/tests/fixtures/simc/README.md`:
   `| file | class | spec | game_version | captured_by | consent | sections | edge_cases |`.
8. `make test-parser` must pass (the index test enforces steps 5–7).
