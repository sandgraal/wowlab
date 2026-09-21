# Data sources

Per source: what we use it for, auth, limits, where the fixtures live, terms,
and a dated breakage log. ADR-0012: no test calls any of these live and no
test needs a real install; CI replays fixtures. Manually-run live checks are
marked `@pytest.mark.live`.

## Local game install

- **Use:** the primary source: everything under the owner's WoW install,
  read directly (`docs/LAB_FILE_MAP.md`). Nothing read from it leaves the
  machine (ADR-0019).
- **Auth:** none. Filesystem access as the owner.
- **Limits:** SavedVariables are current only after logout or `/reload`;
  Windows can hold files open; writes only through `wowlab_core.guard` with
  the client closed (ADR-0021).
- **Fixtures:** `lab/core/tests/fixtures/`, captured and scrubbed with
  `scripts/lab_capture.py` (ticket M10-02; runbook: `docs/handoffs/M10-03.md`).
- **Terms:** reading the install and editing `WTF/`, `Interface/AddOns/`,
  `Fonts/` and base-texture overrides is ordinary user behaviour. ADR-0023
  lists what this repository never does.
- **Known state:** to be filled by M10-03 (flavor folders present, product
  codes, versions, interface numbers, executable names, line endings, whether
  the Forever client writes a combat log).

## wago.tools

- **Use:** DB2 tables as CSV for any product and build (ADR-0022), and the
  list of builds per product. The game-data source until local CASC reading
  exists.
- **Auth:** none.
- **Limits:** undocumented; community-run. One connection, descriptive
  `User-Agent`, backoff on 429/5xx, cache forever by `(table, build)`.
- **Endpoints:** a builds listing and a per-table CSV export selected by
  build. Exact URL shapes and the build query parameter are recorded by
  M10-08 from real responses; community tooling shows both `build=` and
  `version=` in use.
- **Fixtures:** `lab/core/tests/fixtures/wago/` (created by M10-08), small
  tables only.
- **Terms:** data is Blizzard's, extracted by the community; personal,
  non-commercial use. Do not mirror tables publicly.

## Format references

- wowdev.wiki (CASC, TACT, DB2, M2, BLP, WMO, ADT), warcraft.wiki.gg (TOC
  format, SavedVariables, CVars, console commands, API change summaries per
  patch), WoWDBDefs (DB2 column definitions per build). References, not
  runtime dependencies. `docs/LAB_FORMATS.md` cites what it takes from them
  and defers to real fixtures.

## World of Warcraft: Forever — reported state

Collected 2026-09-20 from Blizzard's announcement coverage, community
datamining sites and addon-author reports during beta week one. **None of it
is verified on the owner's machine yet**; M10-03 replaces this list with
findings. Library code must not depend on any of it (L6).

- Beta 2026-09-17 to 2026-10-21, PC (Windows, macOS) through Battle.net;
  launch announced for 2026-11-04; beta characters are wiped.
- Beta build reported as `1.60.1.69913`; interface version reported as
  `16001` (one report argues `160001`).
- Product code reported as `wow_classic_beta`, flavor folder `_classic_beta_`.
- Addon API reported as the modern mainline API (12.1.5 surface), including
  secret values in combat; `WOW_PROJECT_ID` reported equal to the mainline
  value, so library guards written for Classic do not fire; legacy globals
  (`GetItemInfo`, `GetSpellInfo`, old talent functions) replaced by `C_Item`,
  `C_Spell`, `C_Traits`.
- A new playable race with a large customization option set, and a working
  barber shop, are reported; customization tables are in the DB2 exports.
- Some items are reported to be delivered by the server on first encounter
  rather than shipped in the client tables, so table exports are incomplete.

## Breakage log

| Date | Source | What changed | Fixture / evidence | Decision |
|---|---|---|---|---|
| 2026-09-20 | Forever beta | New product; modern addon API on a level-60 client; flavor folder, product code and interface number known only from reports | — (M10-03 will capture) | Flavor, product and build are discovered at run time (ADR-0020); nothing hard-coded |
