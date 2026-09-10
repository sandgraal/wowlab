---
name: patch-day
description: Runbook for a World of Warcraft patch or SimulationCraft release — the recurring maintenance tax. Versions game data, refreshes fixtures, re-gates the parser and talent codec, and logs breakage per data source.
argument-hint: 12.2.0 [--simc-tag <tag>]
---

Run the patch-day checklist for game version `$ARGUMENTS`. Open one ticket
per failing step rather than fixing everything in one PR.

1. **Version boundary.** Confirm `game_version` for new snapshots becomes
   the new patch; nothing overwrites prior-version `game_*` rows (ADR-0007).
2. **SimC.** Check for a new SimulationCraft tag. A bump changes
   `sim_jobs.simc_version`, invalidates the sim cache by construction, and
   needs a worker image rebuild and golden-profile re-baseline. Record it
   with `/adr amend 0004`.
3. **Static data.** Run the pipeline for the new SimC tag into `game_items`
   / `game_spells` / `game_talents` at the new version. Old versions stay.
4. **Fixtures.** Ask the owner for fresh `/simc` exports at the new patch
   (one per changed class at minimum, one with the vault open). Add with
   `/fixture`. Diff the *shape* against the previous version's fixtures:
   new sub-attribute keys, new comment sections, removed lines. Update
   `docs/SIMC_FORMAT.md` with dated notes.
5. **Parser gate.** `make test-parser`. New unknown keys must be preserved,
   not dropped; if the parser errors, capture the input (M1-09) and fix
   forward with a real fixture.
6. **Talent codec.** Decode a fresh string. An unknown serialization
   version must fail loudly — do not guess the format; stop and ask.
  - **UI source diff.** Read the new patch's commit in
    `github.com/Gethe/wow-ui-source` (Blizzard's shipped interface code,
    mirrored per build) for changes to the SavedVariables, talent-string and
    loadout APIs the addon and codec depend on. It is a reference only:
    never vendor or copy code from it. Record findings as dated notes in
    `docs/SIMC_FORMAT.md`.
7. **External APIs.** Replay fixtures against a manually-tagged live check
   for Blizzard and WCL shapes. A shape change is a data-pipeline decision:
   log it in `docs/DATA_SOURCES.md` (breakage log) and stop and ask.
8. **Announce.** A dated entry in `docs/DATA_SOURCES.md` and a short note
   in the release changelog: what changed for players, what re-simmed.
