# Competitive landscape

What the other WoW tools do, what players love and hate about them, and
what Bronze takes from each. This is the long form of the landscape table
in `docs/IMPLEMENTATION_PLAN.md` §2. The ordered list at the end is the
"one-stop shop" roadmap: what to add, improve, or change, ranked. Items
that duplicate the existing plan (vault ranking, Droptimizer, Top Gear,
loadout library, companion agent, weekly plan, gap analysis, Raidbots
report import, Raider.IO score, Wowhead tooltips, OG cards) are not
repeated; only net-new work and concrete extensions appear.

Reviewed 2026-09-09 from the owner's bookmark set: 13 web tools and 15
open-source repositories. Six sites block bots (Raidbots, Warcraft Logs,
WoWAnalyzer, Lorrgs, WoWSims, Raider.IO); their facts come from their own
help docs, API docs, GitHub repositories and community threads, not from
the rendered pages. Every claim below is sourced that way; nothing is
guessed. Re-verify before quoting a competitor's price or limit in
player-facing copy.

`domain-reviewer` grades additions to this file for WoW correctness.

---

## Sim and log tools

### Raidbots — `raidbots.com`

Cloud SimulationCraft. Quick Sim, Top Gear (the most-used tool), Droptimizer
(sims every drop from a chosen source one swap at a time and gives a DPS
gain per item plus an **Expected Value per loot source**), SwiftSim
(Premium: 64 cores vs 32, *verify*). Input is the same `/simc` string Bronze uses, or
a Battle.net login.

| | |
|---|---|
| **Data** | `/simc` export or Battle.net; own SimC cluster |
| **Money** | Free tier on a shared FIFO queue; Patreon Premium buys queue priority, SwiftSim and some Droptimizer-only options |
| **Loved** | One Expected Value per boss is the most legible "where should I go this week" answer in the space; explicit caveat text ("evaluates a single-item swap") |
| **Hated** | Free-tier queue is slower than Premium's SwiftSim (community-reported) and users read it as pressure toward Premium; stateless, no history; Droptimizer cannot look past one drop |

**Bronze takes:** Expected Value per source on the M3 Droptimizer, plus the
trend Raidbots cannot show ("best offer three weeks running"). Positioning:
no pay-to-skip queue.

### Warcraft Logs — `warcraftlogs.com`

Combat-log storage, percentile parses (1–100, colour-coded, recomputed on a
rolling window), rankings, replay, death recap. Public GraphQL v2 API with
client-credentials for public data and a `rateLimitData` type for
self-monitoring.

| | |
|---|---|
| **Data** | Guild-uploaded combat logs |
| **Money** | Silver and Gold subscriptions: banners, faster processing queue, ad-free, and the **Logs Archive**: reports older than 12 months sit behind Gold (*verify*) |
| **Loved** | Percentile as colour is an instant read; "this boss is not locked yet" flags in-progress numbers |
| **Hated** | The 12-month archive wall; private-log guilds hide parses; percentile chasing distorts raid strategy |

**Bronze takes:** the M5 data source, already planned. Positioning: history
never archived. Percentile colour only as an optional lens next to ±, never
as a bare rank (PRODUCT principle 9).

### WoWAnalyzer — `wowanalyzer.com` (AGPL-3.0, TypeScript)

Open-source log analyser. Ingests a public WCL report and produces a
spec-aware Checklist, Suggestions tagged **Major / Average / Minor**,
Statistics, Timeline, Resource and Cooldown views. Spec modules are written
by volunteer per-spec teams.

| | |
|---|---|
| **Data** | Public or unlisted WCL report with advanced logging |
| **Money** | Open source; $2/mo Patreon for hosting, no paywall |
| **Loved** | Severity-tagged suggestions triage a report in seconds |
| **Hated** | Uneven spec support and patch-day rot ("not supported this patch"); grades against an idealised single-target fight so a deliberately held cooldown is flagged as a mistake; no history across pulls or weeks |

**Bronze takes:** severity tags and conditional phrasing for the M5 gap
report. The per-spec modules are prior art for the attribution ruleset
(§8.4). AGPL: read, never vendor.

### Lorrgs — `lorrgs.io`

Pulls the top ~50 parses per spec and boss from the WCL API and lays every
log's cooldown casts on one shared encounter timeline, one row per log.
Public FastAPI JSON API with an OpenAPI spec. Patreon-funded, no paywall.

**Loved:** the aligned multi-log timeline is the most distinctive visual in
the whole set. **Hated:** narrow (cooldown timing only), current tier only,
"top 50" can be one guild's style.

**Bronze takes:** the same timeline over the user's *own* pulls across weeks
(data Bronze owns after M5), with an optional top-parse band from the WCL
API as a comparison lane, costed against the points budget in
`docs/DATA_SOURCES.md`.

### bloodmallet — `bloodmallet.com`

Static per-spec SimC charts: trinkets, secondary-stat distribution, races,
talents, Power Infusion targets. Every chart's subtitle carries its
generation timestamp; the FAQ prints the fixed method (target_error 0.1%,
up to 60,000 iterations, 300 s fights, 20% duration variance; *verify* per
release). Pipeline
(`bloodytools`) is open source.

| | |
|---|---|
| **Data** | Own SimC runs on generic profiles |
| **Money** | Free generic charts; Patreon unlocks a run on the supporter's own gear and talents |
| **Loved** | Method and timestamp printed under every number |
| **Hated** | Free charts are explicitly not about *your* character; irregular refresh |

**Bronze takes:** the whole chart set, run on the user's own snapshot, free
by default (a strict superset of bloodmallet's paid tier), and the
methodology panel on every result.

### WoWSims — `wowsims.github.io` (open source, Go/WASM)

In-browser simulator, strongest for Classic-era WoW; retail coverage not
confirmed. Visual gear picker over a searchable item database, in-browser
rotation editor, **batch and stat-sweep sims with a live chart**, companion
addon for gear import. Donation-funded, no premium tier.

**Loved:** zero queue, instant iteration, explorable UI. **Hated:** a
from-scratch reimplementation of each spec, so parity with SimC (the
community's ground truth) is inconsistent.

**Bronze takes:** the stat-sweep view with a live chart and the gear picker
as a *display* layer over versioned `game_items`. Not the engine: rejected
in `docs/IMPLEMENTATION_PLAN.md` §10, and every number stays SimC's.

### Raider.IO — `raider.io`

M+ score, run history, raid progress, recruitment, weekly affixes, in-game
addon that shows the score in LFG tooltips. Public OpenAPI 3.0 API (200
req/min unauthenticated, *verify* before M1-10 copies the number). Premium raises addon refresh frequency and lets
users claim characters.

**Loved:** one sticky number everyone recognises. **Hated:** the best
documented backlash in WoW tooling: score gatekeeping, "gearscore"
comparisons, group leaders demanding scores far above what a key needs;
addon data goes stale after a few days without login.

**Bronze takes:** the API as a low-risk read on the character page (already
planned, §6.4) and a season summary next to the snapshot timeline. Never a
single aggregate score as the headline. No LFG board.

---

## Mythic+, PvP and economy tools

### Keystone.guru — `keystone.guru`

M+ route planner and viewer: dungeon maps, enemy-forces percentage per
pull, MDT import/export, Raider.IO-sourced heatmaps of how players actually
path, community route library with view counts. Free, Patreon optional.
Its own issue tracker admits new users cannot find the MDT import because
the site defaults to "create a route".

**Bronze takes:** nothing in scope (MDT owns routing); the lesson that the
import path must be the first visible action is already PRODUCT principle 1.

### Murlok — `murlok.io`

"What the top 50 players run" per class, spec and bracket (2v2, 3v3,
Shuffle, Blitz, RBG, M+): talents, gear, stats, enchants, gems, refreshed
every 8 hours from its own ladder crawler. Ad-supported plus Patreon. Was
broken by the patch 11.2 removal of talent loadouts from the Blizzard API.
Shows popularity without sample size, rating floor, recency or any
uncertainty.

**Bronze takes:** the bracket-aware framing and a descriptive lane that
does what Murlok does not: prints sample size and recency, and is labelled
"what people run", never "recommended".

### ArenaMaster — `arenamaster.io` · WoW Mate — `wow-mate.com` · RatedTracker — `ratedtracker.com`

PvP rankings and profiles (ArenaMaster: leaderboards, partner finder, own
inspect addon; WoW Mate: live cutoffs, guild rosters, and **Deep Diff**, a
side-by-side comparison of two characters' gear, gems, talents and stats;
RatedTracker: addon plus desktop companion that auto-syncs match history and
produces a per-death "why you died" breakdown with damage, healing, CC and
defensives at time of death, an account-wide dashboard across characters
with claims like "71% over 34 games", and an optional AI review with the
player's own API key).

**Bronze takes:** WoW Mate's Deep Diff as a cross-character extension of
M1-07. RatedTracker's house style (specific claim, sample size, attributed
cause) is the closest analogue to ADR-0010 in the wild and the model for the
M5 report. No PvP sims (SimC does not model PvP); at most saved loadouts grouped by
the name the player gave them and rating history from the Blizzard PvP
summary endpoint. The export carries only a loadout name and talent
string (`docs/SIMC_FORMAT.md`), no bracket field.

### SaddleBag Exchange — `saddlebagexchange.com`

Auction-house analytics: region-wide undercut alerts delivered by Discord
bot, cross-realm shopping lists, a desktop sniper, a public API with a
Postman collection. Addon exports AH data as JSON. Freemium ($10–20/mo, *verify*).

**Bronze takes:** the pattern, not the product: alerts go to Discord where
players already are, and a public documented API invites a bot ecosystem.
No auction-house features.

### Wowhead · Archon.gg (context, not bookmarked)

Wowhead is the reference database and tooltip provider (sanctioned embed
only, PRODUCT anti-features). Archon.gg, built by the Warcraft Logs team,
turns the full log corpus into popularity-based build guides filterable by
boss, difficulty, key level and affix. It proves demand for log-derived
"what people run" data and that the log owner sees it as a product; Bronze
offers it only as a labelled complement to the sim answer.

---

## Open-source repositories

| Repository | Licence · activity | Verdict | Why |
|---|---|---|---|
| WoWAnalyzer/WoWAnalyzer | AGPL-3.0 · active | **reference** | Closest prior art for the M5 attribution ruleset; read the spec modules, never vendor |
| ATTWoWAddon/AllTheThings | MIT · very active | **integrate (later)** | Item, quest and NPC to mount, pet, toy and transmog mappings; pure metadata, no item-stat arithmetic; the dataset behind a collections feature (ADR-0018) |
| Gethe/wow-ui-source | Blizzard code, mirrored · per patch | **reference** | Ground truth for SavedVariables and talent-string shape changes; a `/patch-day` step |
| WeakAuras/WeakAuras2 | GPL-2.0 · active | **reference** | The serialize → LibDeflate → base64 share-string convention and the wago.io Companion desktop app as prior art for a companion |
| tukui-org/ElvUI | licence unconfirmed · very active | reference | Second confirmation of the share-string convention; nothing else |
| Kruithne/wow.export | MIT · active | admin tool only | DB2 and icon extraction; the *tool* is MIT, the *assets* are Blizzard IP, so self-hosting icons is a stop-and-ask item; prefer the hosted icon CDN |
| WowUp/WowUp | GPL-3.0 · active | reference | Addon-provider abstraction, relevant only if Bronze ever installs an addon for the user |
| TimothyLuke/GSE-Advanced-Macro-Compiler | active | skip | Macro automation is ToS-adjacent (PRODUCT anti-features); third confirmation of the share-string convention |
| hardcano/GnomeSequencer-Enhanced | stale fork | skip | Superseded by the repository above |
| spawnixx/Mythic-Plus-Companion | 5 commits, inactive | skip | Evidence the "M+ companion" idea was tried without traction |
| Blizzard/api-wow-docs | dead, redirects to dev.battle.net | skip | Confirms the shrinking API surface behind ADR-0005 |
| mangoszero/server · Kelsidavis/WoWee · TrinityCore/WowPacketParser | GPL/MIT · active | **do not reference** | Private-server and packet-sniffing projects; legal and reputational risk for a public product |

---

## The ordered list

Ranked by value to a serious raider or M+ player who wants one site,
weighted by fit with the thesis (memory, free sims, sim-versus-log) and by
effort. Priority tiers borrow the item-quality scale.

### Legendary

**1. Personal comparison charts and stat sweeps** · bloodmallet, WoWSims · M3
bloodmallet's chart set (trinkets, secondary distribution, race, talent
variants) run on the user's own snapshot, free by default, plus a stat-sweep
view with a live chart beside one-shot results. SimC computes (scale
factors, profilesets); the browser draws. Every bar carries ±.

**2. Droptimizer Expected Value per source, with trend** · Raidbots · M3
One Expected Value per boss or slot, and "best offer three weeks running"
from snapshot history. Per-item deltas stay one click away; no opaque score.

**3. Cross-character Deep Diff** · WoW Mate · M1-07 extension
The snapshot diff endpoint and UI accept any two snapshots, including
another player's public page. Serves the officer. Unclaimed pages expose
only what the Armory shows (PRODUCT principle 10).

### Epic

**4. Gap-report output spec: severity, conditionality, sample size** ·
WoWAnalyzer, RatedTracker · M5
Major / Average / Minor on every attribution; conditional phrasing so a
held cooldown is not a "mistake"; every claim carries its sample ("uptime
84% over 6 pulls"). WoWAnalyzer's modules as prior art, AGPL, reference only.

**5. Own-history cooldown timeline** · Lorrgs · M5+
The user's pulls of one boss across weeks on one aligned timeline, with an
optional top-parse band from the WCL API.

**6. "What top players run", labelled and bounded** · Archon.gg, Murlok · post-M5
A descriptive lane beside the sim answer with sample size, rating floor and
recency on the surface, labelled "what people run" vs "what sims best".
Source: WCL rankings, or Bronze's own opt-in corpus once large enough.
Never a recommendation without a sim.

### Rare

**7. Notifications where players already are** · SaddleBag · M4
Discord webhook or bot alerts for events Bronze detects: reset happened and
the vault is simmed, the agent uploaded a snapshot, a long sim finished, the
gap widened. The deferred Discord bot becomes alert-first.

**8. Public, documented, read-only API from launch** · SaddleBag, Raider.IO, Lorrgs · M1–M3
FastAPI already emits OpenAPI; publish it with keys, limits and attribution
terms (ADR-0017, ticket M1-10). Write paths stay behind the agent's device
token.

**9. Methodology panel on every result** · bloodmallet, WCL, Raidbots · quality bar
Fixed sim parameters and a "computed at" timestamp next to ± and the SimC
version, including cached hits. Positioning copy: no pay-to-skip queue,
history never archived.

### Uncommon

**10. Collections and completion** · AllTheThings · after M5
The largest step from raider workbench to "all things WoW": mount, pet, toy
and transmog completion from the companion agent (ATT SavedVariables read as
data) with the Blizzard collections endpoints as a convenience path.
ADR-0018; needs its own versioning decision.

**11. Minimal PvP awareness** · ArenaMaster, WoW Mate, RatedTracker · later
Saved loadouts shown under the names players gave them (the export carries
only a name and a talent string, no bracket field; a bracket label would
have to be inferred from the name, which is unreliable, or from the
Blizzard PvP summary endpoint's bracket data) and rating history from
that endpoint. No sim claims, no ladders, no rankings.

**12. Harness and reference notes** · wow-ui-source, WeakAuras, ElvUI, GSE, wow.export
`/patch-day` checks wow-ui-source for SavedVariables and talent-string
changes; any Bronze export string uses the shared serialize + deflate +
base64 convention; wow.export stays an admin tool.

### Reviewed and not adopted

| Idea | From | Why not |
|---|---|---|
| M+ route planning | Keystone.guru | MDT owns it; at most link a route from the weekly plan |
| Auction-house and gold tools | SaddleBag | A different product; only the alert pattern is borrowed |
| LFG and recruitment boards | Raider.IO, ArenaMaster, WoW Mate | Out of scope; adjacent to the gatekeeping Raider.IO is criticised for |
| A single aggregate score | Raider.IO | PRODUCT principle 9: diagnostic, never a score |
| In-browser sim engine | WoWSims | `docs/IMPLEMENTATION_PLAN.md` §10; parity risk; SimC stays the only authority |
| Macro and rotation tooling | GSE | ToS-adjacent; PRODUCT anti-feature |
| Private-server and packet projects | mangoszero, WoWee, WowPacketParser | Legal and reputational risk; do not link |
| AI free-text match review | RatedTracker | Only ever over Bronze's own attributed numbers (ADR-0010), never free-form |
