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
  `sim_jobs.simc_version`, and `CLIENT_DATA_WOW_VERSION` at that SHA is the
  `game_version` of the `game_*` rows. A change to
  `engine/dbc/generated/client_data_version.inc` on the branch is a
  `/patch-day` event (ADR-0004 amendment 2026-09-10).
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

## Breakage log

| Date | Source | What changed | Fixture / evidence | Decision |
|---|---|---|---|---|
| 2025-08 (historical) | Blizzard | `loadouts` absent from specializations | — | ADR-0005: `/simc` primary |
| 2026-09-10 | SimulationCraft | No release tag since `release-830-01`; 12.x work lives on the `midnight` branch as nightlies | `worker/Dockerfile` | The worker pins a commit SHA on `midnight`, exported as `SIMC_REF` (branch as `SIMC_BRANCH`); a bump is an ADR amendment |
