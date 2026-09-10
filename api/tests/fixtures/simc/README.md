# SimC fixture corpus

Real `/simc` addon exports. **No constructed examples.** A test that passes
against a made-up string proves nothing (`CLAUDE.md`, ADR-0012). Add fixtures
with the `/fixture` skill, which validates the file and appends the row below.

## Rules

- **Provenance.** Every file has a row naming who captured it and from which
  game version. A fixture without a row fails `make test-parser`.
- **Consent.** `owner` (the repo owner's own character), `explicit` (the
  player agreed to their export being published here), or `public-post`
  (the player posted it publicly themselves, link in `captured_by`). Exports
  from guildmates need `explicit`.
- **Privacy rewrite.** The character name and realm may be replaced before
  committing (`/fixture --rewrite-identity`). Nothing else is edited: bonus
  IDs, gems, crafted stats, talent strings, and comment sections stay
  byte-for-byte. Record `identity-rewritten` under `edge_cases`.
- **Secrets.** `gitleaks` runs on every fixture. Talent strings and checksum
  lines are allowlisted in `.gitleaks.toml`; anything else that trips it is a
  real problem.
- **Immutability.** Never edit a committed fixture. A patch that changes the
  format gets a *new* fixture with a new `game_version`.

## Coverage targets (M1-01)

One per class (13), plus: crafted gear with `crafted_stats` and
`crafting_quality`, empty sockets, tertiary stats, items in the bag section,
an Augmentation Evoker (support-spec copy), a Death Knight (runeforge as
`enchant_id`), a two-hander (no `off_hand` line), a profession line, an
export captured **with the Great Vault window open** (`### Weekly Reward
Choices`), one with saved loadouts, one healer, one tank, a realm whose name
has a space and an apostrophe (`Area 52`, `Kel'Thuzad`) and an EU realm, a
non-English client (localized header), and a sub-max-level character.

## Index

| file | class | spec | game_version | captured_by | consent | sections | edge_cases |
|------|-------|------|--------------|-------------|---------|----------|------------|
