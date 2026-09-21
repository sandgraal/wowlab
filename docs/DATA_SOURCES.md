# Data sources

Per source: what we use it for, auth, limits, where the fixtures live, terms,
and a dated breakage log. ADR-0012: nothing under `api/tests/` calls any of
these live; CI replays fixtures. Manually-run live checks are marked
`@pytest.mark.live`.

## Blizzard Battle.net API

- **Use:** character identity and gear for characters the user does not own;
  a convenience path for owned characters. Never the only source of anything
  (ADR-0005).
- **Auth:** OAuth 2.0 client credentials at `https://oauth.battle.net/token`.
  App token suffices for Profile endpoints; per-user OAuth only for
  account-scoped endpoints.
- **Limits:** documented 36,000 requests/hour and 100/second per client.
  *Verify on the developer portal before launch.*
- **Endpoints:** profile, equipment, specializations, mythic-keystone-profile,
  statistics, professions, character-media; `data/wow/playable-specialization`.
- **Known state:** talent `loadouts` disappeared from the specializations
  endpoint in 11.2 (Aug 2025). M0-01 verifies 12.1; result recorded under
  ADR-0005. Evoker and Priest loadout strings historically had a different
  shape.
- **Fixtures:** `api/tests/fixtures/blizzard/`. Capture with the bearer token
  and any `Authorization` header stripped; gitleaks is the backstop.
- **Terms:** non-commercial use restriction and attribution requirements.
  ADR-0016 proposes a free, open launch partly for this reason.
- **Regions:** `cn` uses a separate gateway (`gateway.battlenet.com.cn`) and
  separate credentials; it is out of launch scope (ADR-0016) and the
  `region` check constraint keeps the value only so old data stays valid.

## SimulationCraft (engine and static data)

- **Use:** all sim execution (workers, pinned commit SHA) and static
  item/spell/talent data from `engine/dbc/generated/` (ADR-0006).
- **Versioning:** SimC stopped tagging at `release-830-01`; 12.x lives on
  the `midnight` branch. The worker's commit SHA (`SIMC_REF`) is
  `sim_jobs.simc_version`. The `game_*` rows carry the patch triple that
  snapshots carry, truncated from `CLIENT_DATA_WOW_VERSION` at that SHA, with
  the full build, hotfix date, and SHA in a per-version manifest (ADR-0006
  amendment 2026-09-10; the within-patch hotfix rule is an open ADR-0007
  question). A change to `engine/dbc/generated/client_data_version.inc` on
  the branch is a `/patch-day` event, expected roughly weekly during tuning
  (ADR-0004 amendment 2026-09-10).
- **License:** GPL. Run as a separate process; never link or vendor.
- **Fallback:** wago.tools for DB2 tables SimC does not carry.

## Warcraft Logs API v2

- **Use:** actual performance for gap analysis (M5).
- **Auth:** OAuth 2.0 client credentials; GraphQL endpoint.
- **Limits:** points per hour (commonly 3,600). Event-level queries cost far
  more than `table` queries. Every query shape we use has its cost recorded
  here once measured. Background enrichment pauses under a floor.
- **Rules:** all calls through a priority queue; reports are immutable, cache
  forever; prefer `table` over `events`; API times are milliseconds from
  report start, normalize on ingest.
- **Terms:** check attribution and redistribution rules before launch.

## Raider.IO

- **Use:** M+ score and run history on the character page. Nothing depends on it.
- **Auth:** public endpoints; optional key raises limits. Attribution required.

## Raidbots

- **Use:** import a completed report the user pastes (their SimC input string
  and results) as an onboarding path. **Never** submit sims; no sanctioned
  path exists (ADR-0003).

## Wowhead tooltips

- **Use:** item and spell tooltips on hover via Wowhead's tooltip script,
  with the required attribution. No scraping of Wowhead pages or data.

## SimC addon export (`/simc`)

- The primary ingest path. See `docs/SIMC_FORMAT.md`. Fixtures are real
  exports with consent; see `api/tests/fixtures/simc/README.md`.
- **Format authority:** the addon's Lua source at
  https://github.com/simulationcraft/simc-addon. It resolves most format
  questions before a fixture exists; cite the file and line in
  `docs/SIMC_FORMAT.md` when it does. Tests still run only against fixtures.
- **Realm names:** the addon writes a squashed display name, not the API
  slug; ingest normalises via the realm list (see Blizzard `data/wow/realm/index`).

## Local game install (Lab)

- **Use:** the Lab's primary source: everything under the owner's WoW install,
  read directly (`docs/LAB_FILE_MAP.md`). Never a Bronze source; Bronze sees
  an install only through the companion agent's one file (ADR-0009, ADR-0019).
- **Auth:** none. Filesystem access as the owner.
- **Limits:** SavedVariables are current only after logout or `/reload`;
  Windows can hold files open; writes only through `wowlab_core.guard` with
  the client closed (ADR-0021).
- **Fixtures:** `lab/core/tests/fixtures/`, captured and scrubbed with
  `scripts/lab_capture.py` (runbook: `docs/handoffs/M10-03.md`).
- **Terms:** reading the install and editing `WTF/`, `Interface/AddOns/`,
  `Fonts/` and base-texture overrides is ordinary user behaviour. ADR-0023
  lists what the Lab never does.
- **Known state:** to be filled by M10-03 (flavor folders present, product
  codes, versions, interface numbers, executable names, line endings, whether
  the Forever client writes a combat log).

## wago.tools (Lab)

- **Use:** DB2 tables as CSV for any product and build (ADR-0022), and the
  list of builds per product. The Lab's game-data source until local CASC
  reading exists.
- **Auth:** none.
- **Limits:** undocumented; community-run. One connection, descriptive
  `User-Agent`, backoff on 429/5xx, cache forever by `(table, build)`.
- **Endpoints:** a builds listing and a per-table CSV export selected by
  build. Exact URL shapes and the build query parameter are recorded by
  M10-08 from real responses; community tooling shows both `build=` and
  `version=` in use.
- **Fixtures:** `lab/core/tests/fixtures/wago/`, small tables only.
- **Terms:** data is Blizzard's, extracted by the community; personal,
  non-commercial use. Do not mirror tables publicly.

## Format references (Lab)

- wowdev.wiki (CASC, TACT, DB2, M2, BLP, WMO, ADT), warcraft.wiki.gg (TOC
  format, SavedVariables, CVars, console commands, API change summaries per
  patch), WoWDBDefs (DB2 column definitions per build). References, not
  runtime dependencies. `docs/LAB_FORMATS.md` cites what it takes from them
  and defers to real fixtures.

## World of Warcraft: Forever — reported state (Lab)

Collected 2026-09-20 from Blizzard's announcement coverage, community
datamining sites and addon-author reports during beta week one. **None of it
is verified on the owner's machine yet**; M10-03 replaces this list with
findings. Library code must not depend on any of it (LAB_PLAN L6).

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
| 2025-08 (historical) | Blizzard | `loadouts` absent from specializations | — | ADR-0005: `/simc` primary |
| 2026-09-10 | SimulationCraft | No release tag since `release-830-01`; 12.x work lives on the `midnight` branch as nightlies | `worker/Dockerfile` | The worker pins a commit SHA on `midnight`, exported as `SIMC_REF` (branch as `SIMC_BRANCH`); a bump is an ADR amendment |
| 2026-09-20 | Forever beta (Lab) | New product; modern addon API on a level-60 client; flavor folder, product code and interface number known only from reports | — (M10-03 will capture) | Lab discovers flavor/product/build at run time (ADR-0020); nothing hard-coded |
