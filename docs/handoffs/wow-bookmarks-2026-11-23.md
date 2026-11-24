# The owner's Gaming/Wow bookmarks: what each is worth to wowlab

This is the conductor's review of 2026-11-23, at the owner's request. It covers the 45 links in Chrome's `Gaming/Wow` folder outside its `Forever` subfolder; that subfolder is reviewed in `forever-bookmarks-2026-11-23.md`.

Facts were read from each site, its source, GitHub's metadata (`gh api`) and Blizzard's forums between 2026-11-23 and 11-24. Several sites refuse automated fetches (Warcraft Logs, Raider.IO, Raidbots, WoWAnalyzer, ArenaMaster). For those, the facts come from their own GitHub code, API specs and announcements, and each row says where that is the case.

An earlier review of 28 of these (Bronze era, commit 63a08e25) is retired with ADR-0025. ADR-0003 (self-hosted SimC) and ADR-0018 (AllTheThings data) bind nothing now. The tickets are M21-03 (amended) and M22-16 to M22-21 in `docs/BACKLOG.md`. Ideas not ticketed are new rows in `docs/LAB_IDEAS.md`.

## Facts that shape the plan

- **Forever has profiles on the big sites.** Raider.IO (`forever.raider.io`), Warcraft Logs (`forever.warcraftlogs.com`), Lorrgs, Keystone.guru, WoWSims and Saddlebag all have Forever sections. Most retail PvP trackers do not, and Forever has no rated arenas or rated battlegrounds at launch (Blizzard news 24303316).
- **Blizzard's API serves Forever, though undocumented** (forum threads 59774 and 60334):
  - **Namespaces:** `profile-forever-{region}`, `dynamic-forever-{region}` and `static-forever-{region}`. The static namespace can be pinned to a build, for example `static-1.60.1_70399-forever-us`.
  - **Character route:** `/profile/wow/character/{first}-{second}`. Responses carry `surname` and `ruleset`, and no realm.
  - **What works:** equipment, appearance, collections, professions, PvP summary, specializations, statistics, titles, guilds, and auctions per house.
  - **Gaps:** character media often 404s for hours after a character appears, and characters below level 10 return 404.
- **One codec reads three addons' share strings.** ElvUI (`!E2!`), GSE (`!GSE3!`) and GRIP-EMS (`!EMS1!`/`!GRIP1!`) all moved to the client's `C_EncodingUtil` pipeline: CBOR, then raw Deflate, then base64. In stdlib Python that is `base64`, `zlib.decompress(data, -15)` and a small CBOR reader, which wowlab already has for QuestieDB (M22-08).
- **WowUp knows where each addon came from.** WowUp supports Forever, and its `addons.json` on the owner's Mac records each addon's provider, project id and version.

## Assessment

**Verdict key:**
- **Build:** a ticket.
- **Reference:** read when building, no integration.
- **Later:** a LAB_IDEAS row.
- **No:** nothing for wowlab.
- **Never:** crosses L7 or ADR-0023.

### Sites

| Bookmark | What it is | Forever | Verdict | How wowlab uses it |
|---|---|---|---|---|
| raider.io | Profiles, leaderboards, Journeys | Yes, `forever.raider.io` | **Build** (M21-03) | A profile link `https://forever.raider.io/characters/{region}/{ruleset}/{name}` for own characters and crew/PvP rows. The ruleset (`normal`, `pvp`, `rp`, `pvp-rp`) comes from the Blizzard profile (M22-11) or the addon. Name form is [verify]. |
| Blizzard API (api-wow-docs) | Official profiles and game data | Yes, undocumented `*-forever-*` | **Build** (M22-11 amended) | Opponent gear, PvP summary, portraits, ruleset, auctions. |
| wowsims.github.io | Go/WASM sims, MIT | Yes, v1.0.3 at 71037 | **Build** (M22-17) | "Open in WoWSims" with no paste: a link whose hash is the sim's settings (protobuf, deflate, base64). Copy for WoWSims already ships. |
| warcraftlogs.com | Log hosting and parses | Yes, `forever.warcraftlogs.com` | **Build (link)** (M21-03) / **Later** (import) | A report link `https://forever.warcraftlogs.com/reports/{code}` for a code the player pastes. Opt-in import through the player's own API client is a LAB_IDEAS row. Character URL is unknown. |
| keystone.guru | Dungeon routes and maps | Yes (`forever`) | **Build (link)** (M21-03) | A dungeon-map link beside Questie's dungeon and NPC lookups. Useful for twink dungeon runs. |
| lorrgs.io | Top-parse cooldown timelines | Yes | **Later** | A "top warlocks on this boss" link once Forever raids open (2026-12-09). |
| saddlebagexchange.com | Auction-house tools | Partial | **Later** | market-memory uses Blizzard's Forever auctions instead; Saddlebag only as a timing reference. |
| raidbots.com | Cloud SimC | No | **No** | No Forever product, no API. SimC's `forever` branch (beta 70425) stays a simc-bridge note. |
| wowanalyzer.com | Report analyser (AGPL) | No | **Reference** | Its suggestion wording is prior art for the coach. |
| bloodmallet.com | SimC charts | No | **No** | Retail only. |
| murlok.io | Top-50 PvP builds | No | **No** | No rated brackets on Forever. |
| arenamaster.io | Arena leaderboards | No | **No** | The crew and PvP books already do its "inspect" idea locally. |
| wow-mate.com | PvP tracker, Deep Diff | No | **Later** (idea) | "Deep Diff" of your gear against an opponent's, from M22-11's equipment data. |
| ratedtracker.com | Rated PvP analytics | No | **Later** (idea) | First-death analysis and situational win rates over battleground logs (after M22-15). |
| simplearmory.com repo | Collections armory | No | **Reference** | Category layout for a collections view. |
| savecraft.gg (+ companion) | Hosted MCP product | No | **Reference** | Coach tool naming only; its hosted model is the opposite of wowlab's. |
| mcpmarket WoW skill | A SKILL.md | No | **Reference** | The SKILL.md pattern for lab-mcp and the coach. |
| dart-arena.eu | 1.12 private server in a browser | n/a | **Never** | ADR-0023. |

### Repositories and tools

| Bookmark | What it is | Forever | Verdict | How wowlab uses it |
|---|---|---|---|---|
| Gethe/wow-ui-source | Blizzard UI mirror | Yes (`forever` @71037) | **Build** (M22-18) | Extend M21-09's script with the slash-command list (for macro-doctor), the binding-command list (for keybind-coach), the talent loadout string format, and `EncodingUtil` limits. |
| WowUp | Addon manager | Yes | **Build** (M22-16) | Addon provenance: read `addons.json` to label each addon folder with its source and version, and flag folders no manager claims. |
| ElvUI | UI suite (all rights reserved) | Yes (16001) | **Build** (M22-19) | Share-string reader: show, diff and back up `!E2!` profiles. |
| GSE-Advanced-Macro-Compiler | Sequence addon (all rights reserved) | Yes | **Build (read only)** (M22-19) | List and back up sequences; read `!GSE3!`. Never decrypt `!GSE3!+`, never press keys. |
| lazygrip-gg | GRIP-EMS sequence library | Yes | **Build (format)** (M22-19) | `!EMS1!`/`!GRIP1!` and the `GEMSCP1` CVar profile in the share-string reader. |
| Preheat/Pastes | Pasted exports, no licence | No | **Reference** (M22-19) | A catalogue of share-string prefixes for "what is this string?". Fixtures are constructed, never copied. |
| hated-wow-mcp | Addon-dev MCP server, MIT | Yes | **Reference** (coach, lab-mcp) | The model for lab-mcp, which serves the player's *own* data. It bundles decryption code ADR-0033 refuses; read only. |
| wow-ai-log-analyzer | AI log report, MIT | No | **Build (pattern)** (M22-13 amended) | Coach replies cite ids as `[Label](spell:id)`, and the validator rejects any id not in the player's data. Strict JSON, severity tiers. |
| gse-makrowerkstatt | SimC-tested GSE patterns, MIT, Python | No | **Build** (M22-20) | Its four combat-log traps become grader cases. Adds a "dummy session" summary: casts, GCD use, uptime. |
| AllTheThings | Collections tracker, MIT | Yes | **Later** | collection-router from its SavedVariables only. Its `db/Camelot` is Lua call trees, which L3 forbids reading as code. |
| gseintentlogger | Intent-vs-cast logger | No | **Later** (idea) | Addon-recorded press→cast success per macro, shown locally. |
| warcraft-helper | Claude skills over WCL/Raider.IO | No | **Reference** | Coach intent routes with per-domain reference notes. |
| wow-addon → dev-copilot | Claude addon-dev plugin, MIT | No | **Reference** | addon-audit: flag TOCs missing 16001. |
| WoWAddonDevGuide | Claude-written guide, archived | No | **Reference** | Check the addon README's secret-value notes against its 12.x secret-values guide. |
| contextwise.dev | MCP token-saving proxy | n/a | **Reference** | A local `search_tools` meta-tool and loop breaker if lab-mcp grows. |
| WeakAuras2 | Aura framework | No (no 12.x TOC) | **No** | Not runnable on Forever. |
| Mythic-Plus-Companion | M+ site boilerplate | No | **No** | Forever has no Mythic+. |
| houseofmacros.com | Sequence forum | Board only | **No** | At the input-automation edge; no data. |
| jordanellis GSE3 packs (×2) | Retail sequences, no licence | No | **No** | — |
| CastSequenceLite | Light GSE alternative | No | **No** | — |
| world-of-claudecraft, claude-of-warcraft | An original browser MMO and a stale fork | No | **No** | Not WoW tooling. |
| mangoszero/server, WoWee | Private-server emulator and client | No | **Never** | ADR-0023. |
| WowPacketParser | Packet parser (now parses Forever) | Yes | **Never** | Packet capture (L7). |
| tools.wowlazymacros.com | Macro builders + AutoHotkey generator | Unknown | **Never** | Input automation (L7). Never link it. |
| GSEController | Controller → rapid key events | n/a | **Never** | Input automation (L7). |
