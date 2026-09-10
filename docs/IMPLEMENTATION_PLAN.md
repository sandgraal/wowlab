# Bronze — Implementation Plan

**Working name:** Bronze (placeholder; the Bronze Dragonflight governs time, and character history over time is the core differentiator).

**Status:** Pre-implementation specification. Written 2026-09-09 against WoW: Midnight, patch 12.1, Season 2, level cap 90.

**Audience:** Development agent building this repository from zero.

---

## 1. Purpose

Bronze is a persistent character workbench for World of Warcraft. A player points it at a character once and gets a durable, versioned record of that character plus a planning layer on top of it.

The three things it does that no existing tool does:

1. **Remembers.** Character state is stored as immutable, timestamped snapshots. You can diff July against today and see what changed and what it was worth.
2. **Sims without a meter.** Bronze runs its own SimulationCraft workers. Sims are free and unlimited for the user, which makes exploratory planning viable in a way credit-gated services do not.
3. **Closes the loop.** Sim output is theoretical throughput. Warcraft Logs is actual throughput. Bronze joins them and tells you where the gap is.

Everything else in this document exists to serve those three.

---

## 2. Landscape

### What exists

| Tool | What it does well | What it does not do |
|---|---|---|
| Raidbots | Cloud SimulationCraft. Droptimizer, Top Gear. The de facto sim frontend. | Stateless. No character history. Credit-gated. No programmatic submission API. |
| Wowhead | Item and spell database, talent calculator, guides, comments. | Talent calculator has no knowledge of your character or gear. No sim. |
| Warcraft Logs | Combat log storage, parses, rankings. GraphQL API v2. | No forward planning. Does not know what you *could* be doing. |
| Raider.IO | M+ score, run history, public API. | Score only. No gear or build planning. |
| Archon.gg / Murlok.io | Aggregated top-player builds from logs / API. | Descriptive of the meta, not prescriptive for you. Murlok was broken by the Blizzard talent API removal. |
| Bloodmallet | Pre-computed trinket and embellishment sim charts. | Generic profiles, not your character. |
| WoWAnalyzer | Log-based play quality analysis. | Spec coverage is inconsistent. No gear or sim integration. |
| Blizzard Armory | Official current-state character view. | Current state only. No history, no planning, no sim. |
| SimulationCraft addon | Exports a complete, high-fidelity character string. | Requires manual copy-paste every single time. |
| Wago.io | WeakAura and UI profile sharing. | Unrelated to character planning. |
| Lorrgs | Top-parse cooldown timelines per boss, one row per log, from the WCL API. | Current tier only; no memory of *your* pulls. |
| WoWSims | In-browser sim with a gear picker and stat-sweep charts. | Its own spec reimplementation, not SimC; parity is inconsistent. |
| RatedTracker / WoW Mate / ArenaMaster | Rated PvP history, "why you died" breakdowns, character-vs-character Deep Diff. | PvP only; no sim, no gear planning. |
| Keystone.guru | M+ route viewing and sharing, MDT import. | Routing only. |
| SaddleBag Exchange | Auction-house alerts by Discord bot, public API. | Economy only. |
| AllTheThings (addon) | Collection completion tracking, MIT-licensed mapping data. | In-game only; no web view, no history. |

The long form, with what players love and hate about each and what Bronze takes from it, is `docs/COMPETITIVE_LANDSCAPE.md`.

### The structural observation

Every one of these is a single-purpose tool with no memory of the user. A player answering "what should I do this week" opens six tabs and holds the synthesis in their head. That synthesis is the product.

---

## 3. Gap analysis — what is possible but unbuilt

Ranked by (demand × feasibility × defensibility).

**3.1 Great Vault decision support.** Every player, every week, picks one of nine items and guesses. It is a pure sim problem — nine candidate items, one baseline profile, rank by DPS/HPS delta. Nobody productizes it because everyone assumes you'll manually run a Top Gear. Universal demand, trivially solvable once you own sim capacity. *This is the wedge feature.*

**3.2 Frictionless sync via a companion agent.** Addons cannot make HTTP requests; their only I/O is SavedVariables, written at logout or `/reload`. Consequence: every character-sync workflow in WoW is copy-paste. A small local agent that watches the SavedVariables file and POSTs to Bronze eliminates that step permanently. Precedent exists (tray apps that bridge SavedVariables to web APIs for loot council tools), but nobody has built it as a general-purpose character sync. This is the retention moat — once installed, Bronze stays current with zero user effort.

**3.3 Sim-versus-log gap analysis.** SimC json2 output includes per-ability expected damage and APL execution counts. Warcraft Logs gives actual casts, actual damage, actual uptimes. Joining them produces: "your sim projects 1.24M, you did 0.91M, and 61% of the gap is Ebon Might downtime." Nobody does this. It is the highest-value analytical feature in the space and it is genuinely defensible because it requires owning both halves.

**3.4 Character time-series and change attribution.** Store snapshots, diff them, sim the delta. "Since last Tuesday you gained 4 ilvl and 2.1% sim DPS; 1.6% of that came from the trinket." No tool has memory. Cheap to build once the snapshot model is right.

**3.5 Multi-character weekly planning.** Most engaged players run 3-10 characters. Nobody offers a unified view of what each needs, what's capped, what's worth doing. Combine vault progress, crafting cooldowns, catalyst charges, currency caps, and sim-informed upgrade priority into one ranked list per character per week.

**3.6 Persistent acquisition planning.** Droptimizer is a one-shot answer to "what drops here." Nobody maintains a standing plan: this crafted piece now, this vault slot next week, this boss the week after, with expected value and effort cost attached.

**3.7 Talent build library with real diffing.** Store N loadouts, show what actually differs between two, sim each against *your* gear. Wowhead's calculator cannot do this because it has no character context.

**3.8 Group-relative valuation.** Support and buff specs (Augmentation Evoker being the extreme case) have value that depends entirely on the group they're in. Nobody models "what does this spec add to *this* roster." Hard, but nothing else exists in the space and raid leaders would pay for it.

**3.9 Patch delta impact.** Guides say what changed. Nobody says what it means for your specific character. Re-sim the stored snapshot against the new SimC build and report the delta per talent, per item, per stat.

**3.10 Alt-aware gear routing.** Which of your characters should get the crafted piece, given each one's marginal gain. Requires the multi-character model; falls out cheaply once 3.5 exists.

**3.11 Personal comparison charts.** bloodmallet's trinket, secondary-distribution, race and talent charts exist only for generic profiles; the personalised run is its paid tier. Run the same chart set on the user's own snapshot, free, with a stat-sweep view (SimC scale factors and profilesets; the browser only draws). Falls out of the M2 sim queue; ships with M3.

**3.12 Expected value per loot source, with trend.** Raidbots' most legible output is one Expected Value per boss. Bronze's Droptimizer (M3) ships the same number and, from snapshot history, whether this source has been the best offer for weeks. Per-item deltas stay one click away.

**3.13 Cross-character diff.** The snapshot diff (M1-07) generalises to any two snapshots, including another player's public page. Cheap once the diff exists; serves the officer persona.

**3.14 A descriptive lane next to the prescriptive one.** Archon.gg and Murlok show what top players run; neither shows sample size, rating floor or recency, and Murlok broke when the Blizzard API dropped loadouts. After M5, Bronze can show the same from WCL rankings or its own opt-in corpus, labelled "what people run" and never "recommended".

**3.15 Notifications and a public API.** SaddleBag Exchange and Raider.IO grew ecosystems by delivering alerts to Discord and documenting a public API. Bronze's events (reset, vault simmed, snapshot uploaded, gap widened) feed the same channel from M4; the read-only API is published from M1 (ADR-0017).

**3.16 Collections.** The one feature that turns a raider workbench into "all things WoW". AllTheThings maintains MIT-licensed item-to-collection mappings; the companion agent can read its SavedVariables as data. After M5 (ADR-0018).

### Scope decision

Build 3.1, 3.4, and 3.7 first — they share one substrate (snapshots plus a sim queue) and 3.1 alone justifies the product. Then 3.2 (retention), then 3.3 (differentiation), then 3.5 (breadth).

---

## 4. Product definition

### In scope

- Character ingest from `/simc` paste, Blizzard API, or companion agent
- Immutable snapshot history with diffing
- Self-hosted SimulationCraft execution: baseline, droptimizer, top-gear, vault, talent comparison, stat weights
- Talent loadout library with structural diffing
- Great Vault ranking
- Warcraft Logs import and sim-versus-actual gap analysis
- Multi-character account view and weekly planning
- Public read-only character pages (shareable URLs)

### Explicitly out of scope

- **Do not compute item stats from raw data.** Upgrade tracks, crafted quality, tertiary stats, set bonuses, and scaling curves are a deep well. SimC already solves this correctly. Bronze stores and presents; SimC computes.
- **Do not build a game-data scraper from the Blizzard API item endpoint.** See §6.2.
- **Do not build an in-game addon that does rotation advice.** Out of scope and adjacent to ToS risk.
- **Do not attempt Raidbots sim submission.** No sanctioned path exists. Report *import* is fine.
- **Do not build a new sim engine.**

---

## 5. Architecture

```
                    ┌─────────────────┐
   /simc paste ────▶│                 │
   Companion agent ▶│   Ingest API    │──▶ snapshots (append-only)
   Blizzard API ───▶│   (FastAPI)     │
   Raidbots import ▶│                 │
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │    Postgres     │◀── static game data
                    │  snapshots      │    (SimC-derived, per patch)
                    │  loadouts       │
                    │  sim_results    │
                    │  log_reports    │
                    └────────┬────────┘
                             │
              ┌──────────────┼──────────────┐
              │              │              │
     ┌────────▼──────┐ ┌─────▼──────┐ ┌────▼─────────┐
     │  Sim queue    │ │  Read API  │ │ Log ingest   │
     │  (SQS/Redis)  │ │            │ │ (WCL GraphQL)│
     └────────┬──────┘ └─────┬──────┘ └────┬─────────┘
              │              │              │
     ┌────────▼──────┐  ┌────▼──────────────▼───┐
     │ SimC workers  │  │      Web frontend      │
     │ (containers,  │  │      (Next.js)         │
     │  spot capacity)│ └────────────────────────┘
     └───────────────┘
```

### Component responsibilities

**Ingest API** — validates and normalizes input from any of four sources into a canonical snapshot payload. Deduplicates by content hash. Never mutates existing snapshots.

**Read API** — serves character pages, snapshot diffs, sim results, plans. Heavily cached; snapshots are immutable so cache invalidation is trivial.

**Sim orchestrator** — builds SimC profile text from a snapshot plus job options, hashes it, checks cache, enqueues on miss.

**SimC workers** — stateless containers. Pull job, write profile file, exec `simc`, parse json2, write result, exit. CPU-bound and bursty; run on spot capacity.

**Log ingest** — polls or accepts WCL report codes, pulls fight and table data via GraphQL v2, normalizes into a comparable shape.

**Static data pipeline** — a scheduled job that ingests SimC's generated data files per patch version and loads item/spell/talent reference data.

---

## 6. Data sources — capabilities and constraints

### 6.1 Blizzard Battle.net API

**Auth:** OAuth 2.0 client credentials against `https://oauth.battle.net/token`. An app-scoped token works for per-character and per-guild Profile endpoints; per-user OAuth is only required for account-scoped endpoints (`/profile/user/wow`, Protected Character Profile).

**Rate limits:** Documented as 36,000 requests/hour and 100 requests/second per client. *Verify current values against the developer portal at implementation time.*

**Endpoints worth wiring:**

- `/profile/wow/character/{realm}/{name}` — profile basics
- `/profile/wow/character/{realm}/{name}/equipment` — equipped items with bonus IDs
- `/profile/wow/character/{realm}/{name}/specializations` — spec and (formerly) talent loadouts
- `/profile/wow/character/{realm}/{name}/mythic-keystone-profile` — M+ runs and rating
- `/profile/wow/character/{realm}/{name}/statistics`
- `/profile/wow/character/{realm}/{name}/professions`
- `/profile/wow/character/{realm}/{name}/character-media` — render URLs
- `/data/wow/playable-specialization/{id}` — spec reference data

**CRITICAL BLOCKER — verify before designing around it.** Talent loadouts disappeared from the Character Specializations endpoint in patch 11.2 (August 2025) and were still absent a year later with no official response. This broke Murlok and similar sites. Verify current 12.1 state as the first task of Milestone 0:

```bash
TOKEN=$(curl -s -u "$BNET_CLIENT_ID:$BNET_CLIENT_SECRET" \
  -d grant_type=client_credentials https://oauth.battle.net/token | jq -r .access_token)

curl -s -H "Authorization: Bearer $TOKEN" \
  "https://us.api.blizzard.com/profile/wow/character/<realm>/<char>/specializations?namespace=profile-us&locale=en_US" \
  | jq '.specializations[] | {spec: .specialization.name, loadouts: .loadouts}'
```

If `loadouts` is null, the Blizzard API cannot supply talents for any character. The `/simc` and companion paths become the only sources of build data, and the API path degrades to gear-and-identity only. Design for this outcome; treat a working loadouts field as a bonus.

**Known data quirk:** even when loadouts were present, Priest and Evoker loadout strings had a different shape from other classes (slashes embedded in the string). If you consume them, test Evoker explicitly.

**ToS:** Non-commercial use restrictions and attribution requirements apply. Read the Blizzard API Terms of Use before any public launch.

### 6.2 Static game data

**Do not** build the item catalog from the Blizzard Game Data item endpoint. Tens of thousands of individual calls against a 36k/hour budget, to build a catalog that goes stale every patch, is a losing pipeline.

**Do** ingest SimulationCraft's generated data files. SimC ships normalized, patch-versioned item and spell data (`engine/dbc/generated/`) extracted from the client DB2s. It is already shaped for exactly this use case and tagged per patch, giving you a clean version boundary for the pipeline.

**Fallback:** wago.tools for DB2 tables SimC does not carry.

**Pipeline shape:** when the worker's pinned SimC commit moves (ADR-0004 amendment 2026-09-10; SimC no longer tags releases), pull that commit, parse the generated data, load into `game_items` / `game_spells` / `game_talents` keyed by `game_version`. Never overwrite a prior version's rows — old snapshots must remain interpretable.

### 6.3 Warcraft Logs API v2

**Auth:** OAuth 2.0 client credentials. Endpoint is GraphQL.

**Rate limits:** a points-per-hour budget, commonly 3,600 points/hour on a standard client. Points are consumed unevenly — event-level queries are dramatically more expensive than summary tables. Query `rateLimitData { limitPerHour pointsSpentThisHour pointsResetIn }` alongside real work and record the cost of every query shape you use.

**Implementation requirements:**
- All WCL calls go through a queue with priority tiers. Background enrichment pauses when the budget drops below a floor.
- Cache aggressively. A report is immutable once uploaded; never fetch the same report twice.
- Prefer `table` queries over `events` queries. Only drop to event level when the analysis genuinely requires it (buff application targeting, for example).
- Times in the API are relative milliseconds from report start, not wall-clock. Normalize on ingest.

### 6.4 Raider.IO

Public REST API for M+ scores, run history, and guild data. Free tier with rate limits; verify current terms. Low-risk, low-value — wire it for completeness in the character page, do not build features that depend on it.

### 6.5 Raidbots

**Submission: unavailable.** No documented public API for creating simulations. The web app communicates with internal JS-rendered endpoints. The only third-party wrapper was abandoned and never supported submission.

**Reading: available and stable.** Given a report ID or URL, you can fetch the completed report, extract the SimC input string that produced it, and parse standard json2 results.

**Use it for:** an import path. "Paste a Raidbots report URL" gives you a free, zero-friction onboarding vector for users who already have sims — you get their exact SimC profile and their results without them installing anything.

### 6.6 In-game addon constraint

WoW addons cannot make HTTP requests. Their only persistent I/O is their own SavedVariables Lua file, written to disk on logout or `/reload` and read back on login or `/reload`.

This is the constraint that shapes the companion agent design (§8.5). Any "automatic sync" must be a process outside the game client that reads that file.

---

## 7. Data model

Postgres. The central invariant: **snapshots are immutable and append-only.** Nothing ever updates a character's state; new state is a new row. This gives history, diffing, cache-correctness, and audit for free.

```sql
create extension if not exists "pgcrypto";

-- ─── identity ────────────────────────────────────────────────────────────

create table users (
  id              uuid primary key default gen_random_uuid(),
  email           text unique,
  bnet_account_id text unique,           -- null until they OAuth with Blizzard
  created_at      timestamptz not null default now()
);

create table characters (
  id            uuid primary key default gen_random_uuid(),
  region        text not null check (region in ('us','eu','kr','tw','cn')),
  realm_slug    text not null,
  name_slug     text not null,
  display_name  text not null,
  class_id      int,
  race_id       int,
  faction       text,
  owner_user_id uuid references users(id),   -- null = unclaimed / public lookup
  created_at    timestamptz not null default now(),
  unique (region, realm_slug, name_slug)
);

-- ─── snapshots: the spine ────────────────────────────────────────────────

create table snapshots (
  id             uuid primary key default gen_random_uuid(),
  character_id   uuid not null references characters(id),
  captured_at    timestamptz not null,          -- when the game state was true
  ingested_at    timestamptz not null default now(),
  source         text not null
                 check (source in ('simc_paste','companion','blizzard_api','raidbots_import')),
  game_version   text not null,                 -- '12.1.0'
  simc_raw       text,                          -- full /simc string; null if API-sourced
  parsed         jsonb not null,                -- canonical normalized state (see §8.1)
  content_hash   text not null,                 -- sha256 over canonicalized `parsed`
  spec_id        int,
  ilvl_equipped  numeric(6,2),
  ilvl_bag       numeric(6,2)
);

create index on snapshots (character_id, captured_at desc);
create unique index on snapshots (character_id, content_hash);  -- identical state = no new row

-- ─── talent loadouts ─────────────────────────────────────────────────────

create table loadouts (
  id            uuid primary key default gen_random_uuid(),
  character_id  uuid references characters(id),   -- null = library/shared build
  owner_user_id uuid references users(id),
  spec_id       int not null,
  label         text not null,
  talent_string text not null,                    -- Blizzard import string
  decoded       jsonb,                            -- node/rank map, see §8.2
  game_version  text not null,
  source        text not null
                check (source in ('snapshot','manual','imported','aggregated')),
  created_at    timestamptz not null default now()
);

create index on loadouts (character_id, spec_id);

-- ─── simulation ──────────────────────────────────────────────────────────

create table sim_jobs (
  id            uuid primary key default gen_random_uuid(),
  snapshot_id   uuid not null references snapshots(id),
  requested_by  uuid references users(id),
  kind          text not null
                check (kind in ('baseline','droptimizer','top_gear','vault',
                                'talent_compare','stat_weights')),
  options       jsonb not null,      -- iterations, fight_style, max_time, desired_targets, ...
  profile_hash  text not null,       -- sha256 of (generated profile text + canonical options)
  simc_version  text not null,
  status        text not null default 'queued'
                check (status in ('queued','running','complete','failed','cancelled')),
  error         text,
  enqueued_at   timestamptz not null default now(),
  started_at    timestamptz,
  finished_at   timestamptz
);

create index on sim_jobs (status, enqueued_at);
create unique index sim_cache_idx on sim_jobs (profile_hash, simc_version)
  where status = 'complete';        -- THE cost control. Never sim the same thing twice.

create table sim_results (
  sim_job_id    uuid primary key references sim_jobs(id),
  dps_mean      numeric,
  dps_stddev    numeric,
  hps_mean      numeric,
  iterations    int,
  raw_json      jsonb not null,      -- full SimC json2 output
  per_ability   jsonb,               -- extracted: {spell_id: {count, damage, ...}}
  computed_at   timestamptz not null default now()
);

-- ─── logs ────────────────────────────────────────────────────────────────

create table log_reports (
  id            uuid primary key default gen_random_uuid(),
  wcl_code      text not null unique,
  owner_guild   text,
  start_time    timestamptz,
  end_time      timestamptz,
  fetched_at    timestamptz not null default now(),
  raw           jsonb
);

create table log_fights (
  id              uuid primary key default gen_random_uuid(),
  report_id       uuid not null references log_reports(id),
  fight_id        int not null,
  encounter_id    int,
  encounter_name  text,
  difficulty      int,
  kill            boolean,
  duration_ms     int,
  unique (report_id, fight_id)
);

create table log_performances (
  id             uuid primary key default gen_random_uuid(),
  fight_id       uuid not null references log_fights(id),
  character_id   uuid references characters(id),
  actor_name     text not null,
  spec_id        int,
  dps            numeric,
  hps            numeric,
  ability_casts  jsonb,      -- {spell_id: count}
  buff_uptimes   jsonb,      -- {spell_id: uptime_pct}
  unique (fight_id, actor_name)
);

-- ─── analysis ────────────────────────────────────────────────────────────

create table gap_analyses (
  id                uuid primary key default gen_random_uuid(),
  log_performance_id uuid not null references log_performances(id),
  sim_job_id        uuid not null references sim_jobs(id),
  projected_dps     numeric,
  actual_dps        numeric,
  gap_pct           numeric,
  attribution       jsonb not null,   -- [{cause, est_pct_of_gap, evidence}]
  computed_at       timestamptz not null default now()
);

-- ─── static game data, versioned per patch ───────────────────────────────

create table game_items (
  game_version text not null,
  item_id      int  not null,
  name         text,
  quality      int,
  inv_type     int,
  item_level   int,
  stats        jsonb,
  primary key (game_version, item_id)
);

create table game_spells (
  game_version text not null,
  spell_id     int  not null,
  name         text,
  icon         text,
  primary key (game_version, spell_id)
);

create table game_talents (
  game_version text not null,
  spec_id      int  not null,
  node_id      int  not null,
  entry_id     int,
  spell_id     int,
  name         text,
  max_rank     int,
  tree         text check (tree in ('class','spec','hero')),
  position     jsonb,
  primary key (game_version, spec_id, node_id, entry_id)
);
```

### Model notes for the implementer

- `content_hash` on snapshots means a user can paste the same string ten times and produce one row. The hash is taken over the canonical `parsed` payload (sorted keys, `_raw` and the local-time header excluded), **including** the comment-section collections (`bags`, `vault_choices`, `loadouts`, `extra`): those are state, and a paste taken with the vault open must be a new snapshot, not a dedupe hit. The single definition lives in `docs/SIMC_FORMAT.md` § Canonicalization. *(Amended 2026-09-09; the earlier wording — strip comments before hashing — would have discarded the vault choices.)*
- The partial unique index on `sim_jobs (profile_hash, simc_version) where status = 'complete'` is the single most important line in this schema. It is what makes free unlimited sims economically possible.
- `game_*` tables are versioned rather than overwritten so that a snapshot from three patches ago still renders correctly.
- `characters.owner_user_id` is nullable on purpose. Anyone can look up any character; claiming is a separate, optional flow.

---

## 8. Subsystems

### 8.1 SimC parser

The highest-leverage component. Everything downstream depends on a correct, lossless parse.

**Input shape** — `/simc` addon output looks approximately like this:

```
# Charname - Augmentation - 2026-09-09 14:22 - US/Realmname
evoker="Charname"
level=90
race=dracthyr
region=us
server=realmname
role=spell
professions=blacksmithing=100/jewelcrafting=100
spec=augmentation

talents=CkEBb...

head=,id=228851,bonus_id=10356/9633/8902,gem_id=213743,enchant_id=7936,ilevel=protect
neck=,id=228843,bonus_id=10356/9633,gem_id=213482/213482
...
main_hand=,id=228906,bonus_id=10356,enchant_id=7448,crafted_stats=40/36

### Gear from Bags
# head=,id=228763,bonus_id=10353/9627
# trinket1=,id=225649,bonus_id=10356
```

**Parser requirements:**

- Header comment yields character name, spec, capture timestamp, region, realm. Treat as advisory — cross-check against the `evoker=`/`priest=`/etc. line, which is authoritative for name and class.
- The class is encoded as the *key* of the name line. Build a class-key map; do not assume position.
- Key-value lines: `level`, `race`, `region`, `server`, `role`, `spec`, `talents`, `professions`.
- Slot lines: slot name, then comma-separated sub-attributes. Sub-attribute keys seen in the wild include `id`, `bonus_id` (slash-delimited list), `gem_id` (slash-delimited, one per socket), `enchant_id`, `crafted_stats` (slash-delimited), `ilevel`, `context`, `drop_level`. **Treat the sub-attribute key set as open** — preserve unknown keys verbatim in `parsed` rather than dropping them, so a new patch adding a field does not silently lose data.
- Commented-out lines under `### Gear from Bags` are alternate items, not equipped. Parse them into a separate collection; they are the candidate pool for Top Gear.
- Preserve the raw string in `snapshots.simc_raw` unconditionally. If the parser is wrong, you can reparse history.

**Canonical `parsed` shape:**

```json
{
  "name": "Charname",
  "class": "evoker",
  "spec": "augmentation",
  "level": 90,
  "race": "dracthyr",
  "region": "us",
  "server": "realmname",
  "role": "spell",
  "talents": "CkEBb...",
  "professions": {"blacksmithing": 100, "jewelcrafting": 100},
  "equipped": {
    "head": {"id": 228851, "bonus_id": [10356,9633,8902], "gem_id": [213743],
             "enchant_id": 7936, "_raw": "head=,id=228851,..."}
  },
  "bags": [
    {"slot": "head", "id": 228763, "bonus_id": [10353,9627], "_raw": "# head=,id=..."}
  ]
}
```

**Amendment (2026-09-09, ADR-0015):** the canonical shape gains three collections parsed from the comment sections the addon writes after the equipped block:

```json
{
  "vault_choices": [{"slot": "trinket1", "id": 225649, "bonus_id": [10356], "_raw": "# trinket1=,id=..."}],
  "loadouts": [{"name": "Raid ST", "talents": "CkEBb..."}],
  "extra":    {"upgrade_currencies": "c:2245:1234/..."}
}
```

`vault_choices` is present only when the export was taken with the Great Vault window open (`### Weekly Reward Choices`); `loadouts` comes from `### Saved Loadouts`; `extra` preserves every key under `### Additional Character Info` verbatim. All three are hypotheses until `docs/SIMC_FORMAT.md` records a confirming fixture.

**Testing:** build a fixture corpus of real `/simc` strings covering every class, both a caster and a physical spec, a character with crafted gear, one with empty sockets, one with tertiary stats, and one Evoker (see §6.1 on Evoker string quirks). Parser tests run against fixtures, never against generated examples.

**Round-trip test (required):** parse → regenerate profile → the regenerated profile must produce a SimC result within statistical noise of the original string's result. This is the only test that proves the parse is lossless in the way that matters.

### 8.2 Talent string decoding

Blizzard talent loadout strings are a documented bit-packed format (see `Blizzard_ClassTalentImportExport.lua` in the game client for the reference implementation). The header carries a serialization version and a spec ID.

**Requirements:**
- Decode to `{node_id: {entry_id, rank}}` and store in `loadouts.decoded`.
- Check the serialization version byte and fail loudly on an unknown version rather than producing garbage. Blizzard has bumped this before and will again.
- The spec ID in the header reflects the *character's current spec at export time*, not necessarily the loadout's spec. If you accept strings for off-specs, the header may lie. Validate against the decoded node set.
- Do not build the diffing UI on the string. Diff on `decoded`. Two strings can encode the same build.

**Fallback:** if decoding proves brittle across patches, you can treat the string as opaque, pass it to SimC directly (SimC accepts `talents=<string>`), and get build diffing from the *sim results* rather than the structure. Ship opaque first if decoding stalls the milestone.

### 8.3 Sim orchestration

**Profile generation:** snapshot `parsed` → SimC profile text. For `simc_paste` and `companion` sources this is close to the identity function on `simc_raw` with option lines appended. For `blizzard_api` sources it is a real synthesis and will be lower fidelity — label it as such in the UI.

**Job options by kind:**

| Kind | Iterations | Notes |
|---|---|---|
| `baseline` | 10,000 | Single profile, one result. Cached hard. |
| `vault` | 5,000 | The vault choices vs baseline — a Droptimizer with a fixed pool of up to nine, **plus the catalysed variant of every tier-slot choice** (bonus-id substitution from SimC's data) so set-bonus boundaries are visible to the sim. Every result records the fight profile it assumed. *(Amended 2026-09-09.)* |
| `droptimizer` | 5,000 | Candidate pool = all loot from a chosen instance/difficulty. |
| `top_gear` | 10,000 | Combinatorial over the bag pool. Cap combinations; reject requests above a ceiling. |
| `talent_compare` | 10,000 | N loadouts, identical gear. |
| `stat_weights` | 10,000 | `calculate_scale_factors=1`. Expensive; rate-limit per user. |

**Worker contract:**

```
1. Dequeue job.
2. Fetch snapshot + options.
3. Generate profile text; assert profile_hash matches the job.
4. Write /tmp/profile.simc.
5. exec: simc /tmp/profile.simc json2=/tmp/out.json iterations=N threads=$(nproc)
6. Parse out.json. Extract dps mean/stddev, per-ability breakdown, APL counts.
7. INSERT sim_results. UPDATE sim_jobs SET status='complete'.
8. Exit.
```

Workers are stateless and idempotent — a job re-run must produce a row-identical result modulo RNG variance. Set `deterministic=1` in options for any comparison sim so that candidate runs share a seed and A/B deltas are stable across re-runs. That is reproducibility, not precision: results still carry ±, and precision is governed by `target_error` under an iteration ceiling per kind, never by raising iterations until a test passes. *(Amended 2026-09-09.)*

**Cost control, in order of importance:**
1. Profile-hash cache (the partial unique index). Expect a very high hit rate on baselines.
2. Iteration caps per job kind. Interactive requests get fewer iterations; explicit "high precision" requests cost the user a rate-limit token.
3. Spot capacity. SimC is embarrassingly interruptible — a killed worker just re-queues.
4. Per-user concurrency limit. One running sim per free user.

**Version pinning:** `sim_jobs.simc_version` is part of the cache key. A SimC upgrade invalidates the cache by construction, which is correct — results are not comparable across engine versions.

### 8.4 Log correlation engine

The differentiating feature. Build it after the sim side is solid.

**Inputs:** a `log_performances` row (actual) and a `sim_results` row (projected) for the same character, ideally from snapshots taken close in time.

**Method:**

1. Normalize both to per-ability cast counts and damage, per unit time.
2. Compute the top-level gap: `(projected_dps - actual_dps) / projected_dps`.
3. Attribute the gap across a ruleset of causes. Each rule emits an estimated share and the evidence supporting it:
   - **Uptime loss** — a buff or debuff the sim maintains at ~100% shows measurably lower uptime in the log.
   - **Cast deficit** — sim executes ability X n times per minute; log shows fewer.
   - **Resource waste** — capped resource events, overhealing, wasted procs.
   - **Downtime** — GCD idle time above the sim's baseline.
   - **Target count mismatch** — the sim assumed a target count the fight did not present. Flag as *not a player error*.
   - **Movement / mechanics** — residual after the above. Label it honestly as unattributed rather than inventing a cause.
4. Store as `gap_analyses.attribution`.

**Design rule:** every attribution line must cite the specific numbers that produced it. "Ebon Might uptime 84% vs sim 99%, worth an estimated 6.2% of the 27% gap" is useful. "Improve your rotation" is not, and shipping it would destroy trust in the feature.

**Honest limitation to surface in the UI:** the sim's fight model is not the fight the player was in. For support and buff specs specifically, sim projections are approximations against generic allies. Present gap analysis as diagnostic, never as a score.

### 8.5 Companion agent

The retention mechanic. Small scope, disproportionate value.

**What it is:** a lightweight background process on the player's machine that watches the WoW SavedVariables file for a companion addon, extracts the character export payload, and POSTs it to Bronze.

**Why it is necessary:** WoW addons have no network access. SavedVariables is the only channel out of the client.

**Design:**

- A minimal companion addon writes a structured export to its SavedVariables on `PLAYER_LOGOUT` and on a manual `/bronze` slash command. Payload is the same content the SimC addon produces, plus currency, vault progress, crafting cooldowns, and catalyst charges — data the `/simc` string does not carry but the weekly planner needs.
- The agent watches `_retail_/WTF/Account/<ACCT>/SavedVariables/Bronze.lua` with a filesystem watcher, debounced.
- On change: parse the Lua table (a constrained parser, not a Lua interpreter — reject anything that isn't a data literal), sign with a device token, POST to `/v1/ingest/companion`.
- Cross-platform: Go single binary is the right choice. Tray icon optional; a background service with a log file is sufficient for v1.
- Auth: device pairing code generated in the web UI, exchanged once for a long-lived device token.

**Security requirements — non-negotiable:**
- The agent reads exactly one file path pattern and nothing else. No arbitrary filesystem access.
- The Lua parser must not execute. Parse as data; reject function definitions, metatables, and anything non-literal.
- Ship reproducible builds and publish hashes. Users are installing a binary that reads their game directory; earn that.
- The agent only ever uploads. It never writes to the game directory.

**Milestone placement:** M4. It is the retention feature, but it is worthless before there is something worth staying for.

---

## 9. API surface

Versioned under `/v1`. JSON. Auth via session cookie (web) or bearer device token (agent).

```
POST   /v1/ingest/simc              { simc_string }              → { snapshot_id, character_id }
POST   /v1/ingest/companion         { payload, device_token }     → { snapshot_id }
POST   /v1/ingest/raidbots          { report_url }                → { snapshot_id, sim_job_id }
POST   /v1/characters/refresh       { region, realm, name }       → { snapshot_id }   (Blizzard API)

GET    /v1/characters/{region}/{realm}/{name}                     → character + latest snapshot
GET    /v1/characters/{id}/snapshots                              → paginated history
GET    /v1/snapshots/{id}                                         → full snapshot
GET    /v1/snapshots/{a}/diff/{b}                                 → structural diff + sim delta

POST   /v1/sims                     { snapshot_id, kind, options } → { sim_job_id, cached: bool }
GET    /v1/sims/{id}                                              → status + results
GET    /v1/snapshots/{id}/vault                                   → ranked vault options

GET    /v1/characters/{id}/loadouts
POST   /v1/loadouts                 { character_id, label, talent_string }
GET    /v1/loadouts/{a}/diff/{b}                                  → node-level diff
POST   /v1/loadouts/compare         { snapshot_id, loadout_ids[] } → sim_job_id

POST   /v1/logs/import              { wcl_report_code }           → { report_id }
GET    /v1/logs/{report}/fights
POST   /v1/analysis/gap             { log_performance_id, snapshot_id } → { gap_analysis_id }

GET    /v1/users/me/characters                                    → account roster
GET    /v1/users/me/plan                                          → weekly plan across characters
```

**Contract rules:**
- Ingest endpoints are idempotent by content hash. Re-posting identical state returns the existing `snapshot_id` with HTTP 200, not a new row.
- `POST /v1/sims` returns `cached: true` and the existing result immediately on a profile-hash hit. The client should not distinguish.
- All list endpoints are cursor-paginated. Snapshots accumulate.

---

## 10. Tech stack

| Layer | Choice | Rationale |
|---|---|---|
| API | Python 3.12 + FastAPI | Async, typed, fast to write. Matches existing team competence. |
| DB | Postgres 16 | JSONB for snapshot payloads, partial indexes for the sim cache. Supabase is an acceptable managed path. |
| Queue | SQS (or Redis + RQ for local dev) | Sim jobs are coarse-grained and interruption-tolerant. |
| Sim workers | Docker image with SimC compiled from a pinned commit SHA (SimC stopped tagging at `release-830-01`) | Pinned version is part of the cache key. |
| Worker compute | Fargate Spot or K8s on spot nodes | CPU-bound, bursty, interruptible. |
| Frontend | Next.js + TypeScript | Server components suit the read-heavy, cache-friendly page model. |
| Companion agent | Go, single static binary | Cross-platform, no runtime dependency, easy to distribute. |
| Static data pipeline | Python, scheduled | Runs on SimC releases, not on a clock. |

**Rejected alternatives, with reasons:**
- *Running SimC in WASM client-side* — attractive for cost, but iteration counts that produce trustworthy results are too slow in-browser, and you lose result caching entirely.
- *Serverless sim functions* — SimC runs exceed typical Lambda ceilings at useful iteration counts.
- *A document store instead of Postgres* — the sim cache and the snapshot dedupe both depend on unique indexes over computed columns. Relational is correct here.

---

## 11. Milestones

Each milestone must be independently shippable and demoable.

### M0 — Foundation (target: 1 week)

- Repo scaffold, monorepo layout (§12), CI on push (lint, type-check, test), pre-commit hooks.
- Docker Compose local stack: Postgres, Redis, API, one worker.
- Migrations framework wired (Alembic), §7 schema applied.
- **Task 1, before anything else:** run the Blizzard talent loadout verification in §6.1 and record the result in `docs/DECISIONS.md`. It gates M3 scope.
- Blizzard OAuth client-credentials flow working, token cached and refreshed.

*Acceptance:* `docker compose up` gives a working stack; a smoke test creates a character row and reads it back.

### M1 — Ingest and character page (target: 2 weeks)

- SimC parser with fixture corpus (§8.1), including the round-trip test.
- `POST /v1/ingest/simc` → snapshot, deduped by content hash.
- Static game data pipeline for one patch version; item names and icons resolve.
- Character page: equipped gear, item levels, spec, talent string, snapshot list.
- Snapshot diff view: gear changes between any two snapshots.

*Acceptance:* paste a real `/simc` string, see a correct character page. Paste it again, get no duplicate snapshot. Paste a modified one, see a correct diff.

### M2 — Simulation (target: 3 weeks)

- SimC worker image with pinned version; profile generation from snapshot.
- Sim queue, job lifecycle, cache via profile hash.
- `baseline` sim on every new snapshot, automatically.
- **Vault ranking** (§3.1) — the wedge feature. Nine candidate items, ranked by delta, with confidence intervals.
- Snapshot diff gains a sim delta: "this change was worth +2.1%."

*Acceptance:* a user pastes a string and within 60 seconds sees a ranked vault recommendation with per-item DPS deltas. Second identical request returns instantly from cache.

### M3 — Planning layer (target: 3 weeks)

- Talent loadout library: save, label, list.
- Loadout diffing (structural if decoding shipped, sim-based otherwise — see §8.2 fallback).
- `talent_compare` sim.
- Droptimizer against a chosen instance and difficulty.
- Top Gear over the bag pool, with a combination ceiling.
- Raidbots report import as an onboarding path.
- Personal comparison charts and stat sweeps on the user's own snapshot (§3.11).
- Droptimizer Expected Value per source, with the week-over-week trend (§3.12).
- Cross-character diff on the M1-07 endpoint (§3.13).

*Acceptance:* a user compares three of their own builds against their own gear and sees a ranked answer.

### M4 — Companion agent and multi-character (target: 4 weeks)

- Companion addon writing the extended payload to SavedVariables.
- Go agent: file watching, constrained Lua parsing, device pairing, upload.
- Signed, reproducible builds published for Windows and macOS.
- Account roster view; per-character weekly state (vault progress, currencies, cooldowns).
- Weekly plan: ranked actions across all characters.
- Discord webhook alerts for reset, vault simmed, snapshot uploaded (§3.15).

*Acceptance:* a user installs the agent, plays, logs out, and their character page is current without any manual action.

### M5 — Log correlation (target: 4 weeks)

- WCL OAuth, GraphQL client with a points-budget-aware queue.
- Report import, fight and performance normalization.
- Gap analysis engine with the attribution ruleset (§8.4).
- Gap report UI with per-cause evidence.
- Report output spec: Major / Average / Minor severity, conditional phrasing, sample size on every claim (`docs/COMPETITIVE_LANDSCAPE.md` item 4).
- Own-history cooldown timeline per boss, optional top-parse band (item 5).

*Acceptance:* import a real raid log, get an attributed gap breakdown where every line cites its numbers.

---

## 12. Repository layout

```
bronze/
├── README.md
├── docs/
│   ├── IMPLEMENTATION_PLAN.md      # this file
│   ├── DECISIONS.md                # ADRs, incl. the talent-API verification result
│   ├── DATA_SOURCES.md             # per-source auth, limits, gotchas
│   └── SIMC_FORMAT.md              # parser reference + fixture index
├── api/
│   ├── src/bronze_api/
│   │   ├── main.py
│   │   ├── routers/                # ingest, characters, sims, loadouts, logs
│   │   ├── models/                 # SQLAlchemy
│   │   ├── schemas/                # Pydantic
│   │   ├── services/
│   │   │   ├── simc_parser.py
│   │   │   ├── talent_codec.py
│   │   │   ├── profile_builder.py
│   │   │   └── gap_analysis.py
│   │   └── clients/                # blizzard.py, wcl.py, raiderio.py, raidbots.py
│   ├── migrations/
│   └── tests/
│       └── fixtures/simc/          # real /simc strings, one per class
├── worker/
│   ├── Dockerfile                  # builds SimC from a pinned commit SHA
│   ├── src/
│   └── tests/
├── agent/                          # Go companion
│   ├── cmd/bronze-agent/
│   ├── internal/{watcher,luaparse,uploader}/
│   └── addon/Bronze/               # the in-game addon it reads
├── web/                            # Next.js
├── pipeline/                       # static game data ingest
├── infra/                          # terraform or cdk
└── docker-compose.yml
```

---

## 13. Testing strategy

**Parser:** fixture-driven, one real `/simc` string per class plus edge cases. The round-trip test (§8.1) is the gate on any parser change.

**Sim workers:** golden-profile tests. A known profile at a pinned SimC version must produce a DPS within a defined tolerance. This catches both worker regressions and SimC upgrades that shift results.

**Talent codec:** property test — encode(decode(s)) == s for a corpus of real strings. Explicit failure test for an unknown serialization version byte.

**API clients:** record real responses as fixtures; replay in CI. Do not hit live APIs from CI — rate limits are shared and finite.

**Gap analysis:** hand-labeled cases. Take three real logs where the cause of underperformance is known, assert the engine identifies it. This is the only defense against a plausible-sounding but wrong analysis engine.

---

## 14. Operations

**Cost model.** The dominant cost is SimC CPU. Controls, in order: profile-hash cache, iteration caps by job kind, spot capacity, per-user concurrency limits. Instrument cache hit rate from day one — if it drops below ~70% on baselines, something in profile generation is non-deterministic and is burning money.

**Patch churn is the recurring tax.** Every patch invalidates game data, may change the SimC profile format, and may change the talent string encoding. Version the data pipeline against patch number from M0 or you will be doing emergency migrations under user pressure. Budget engineering time for every patch, not just major ones.

**Observability.** Track: ingest success rate by source, parser failure rate with the failing string captured, sim queue depth and p50/p99 duration, cache hit rate, per-source API budget consumed. The parser failure capture matters most — a new patch adding an unknown `bonus_id` variant will show up there first.

**Blizzard API posture.** Blizzard has been tightening API access; the talent loadout removal appears to be part of that pattern. Do not build a single point of failure on any Blizzard endpoint. The `/simc` and companion paths must remain fully functional with zero Blizzard API availability.

---

## 15. Legal and terms

Resolve before public launch, not after.

- **Blizzard API Terms of Use** — non-commercial use restrictions and attribution requirements. Determine whether any monetization model is permissible before building one.
- **Warcraft Logs API terms** — check attribution and caching/redistribution rules for their data.
- **Raider.IO API terms** — attribution required.
- **SimulationCraft is GPL-licensed.** Running it as a separate process invoked by your service is standard practice and does not impose GPL obligations on your own code. Do not link against it or vendor its source into your codebase.
- **Raidbots** — importing a report the user provides is user-initiated data portability. Do not scrape, and do not attempt sim submission.

---

## 16. Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Talent loadouts still absent from Blizzard API | High | Medium | `/simc` and companion paths are primary; API path is gear-only. Verified in M0. |
| SimC profile format changes on patch | Medium | High | Version-pinned workers; round-trip test catches breakage before users do. |
| Talent string encoding version bump | Medium | Medium | Fail loudly on unknown version; opaque-string fallback keeps sims working. |
| Sim costs exceed budget | Medium | High | Cache, caps, spot, concurrency limits. Monitor hit rate. |
| Blizzard further restricts API access | Medium | Medium | No feature depends solely on a Blizzard endpoint. |
| WCL point budget insufficient at scale | Medium | Medium | Queue with priority tiers; cache reports permanently; prefer table over event queries. |
| Companion agent distrusted by users | Medium | High | Open source, reproducible builds, published hashes, read-only single-path access, clear documentation. |
| Gap analysis produces confident wrong answers | Medium | High | Evidence-cited attributions only; hand-labeled test cases; honest "unattributed" bucket. |

---

## 17. Open questions for the product owner

1. **Monetization**, if any — this determines whether the Blizzard API non-commercial restriction is a blocker. Free-and-open sidesteps it entirely.
2. **Scope of "all specs" vs "start narrow."** Building against one spec first (Augmentation Evoker) surfaces support-spec edge cases early, which is where the sim model is weakest. Broad launch is more marketable but hides those problems until later.
3. **Public character pages** — shareable URLs drive organic growth but raise a privacy question about characters whose owners never opted in. The Armory is public, so the data is public, but the aggregation is new.
4. **Hosting target** — AWS (Fargate Spot for workers) or a Supabase + Vercel + separate worker fleet split. The latter is faster to stand up; the former is cheaper at sim scale.
5. **Region coverage at launch** — US/EU only is simpler; KR/TW/CN add realm-slug and locale complexity.

---

## 18. Handoff notes for the development agent

Read §6 before writing any code. The constraints there — no Raidbots submission API, no addon HTTP, possibly no talent data from Blizzard — are what shape every design decision downstream, and they are not obvious from the outside.

Start with the parser. Everything hangs off it, and a wrong snapshot model is expensive to correct once there is history to migrate.

The single most important implementation detail in this document is the partial unique index on `sim_jobs (profile_hash, simc_version) where status = 'complete'`. It is what makes free unlimited simulation economically possible, and it only works if profile generation is deterministic. Any non-determinism in profile text — an embedded timestamp, unstable key ordering, a floating-point format difference — silently destroys the cache and the cost model with it. Test for it explicitly.

Ship M2 before adding anything not listed in it. Vault ranking alone is a product people will use every Tuesday.
