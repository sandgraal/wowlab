# The owner's Forever bookmarks: what each is worth to wowlab

Conductor's review of 2026-11-23, at the owner's request: every link in Chrome's `Gaming/Wow/Forever` folder (21), the WoW Forever Atlas one folder up, and forevertwinking.com and Wowhead, which the owner named. Read from the sites, their source and GitHub's metadata on 2026-11-23; forevertwinking.com and Wowhead block AI agents in robots.txt, so what is said of them comes from their public terms, other sites' code and search results. The tickets are M21 in `docs/BACKLOG.md`.

## Facts

- **Forever characters have no realm.** A character is region + first name + second name. Blizzard's own Forever armory is `https://worldofwarcraft.blizzard.com/{locale}/forever/{region}/armory/character/{first}-{second}`; guild pages are `…/forever/{region}/armory/guild/{slug}`.
- **The region is already on disk.** The `portal` CVar in `Config.wtf` holds it (`redact.py:37` already keeps it). For open-world opponents, `crew scan-log` keys players from the combat log's `Name-Realm-Region` (LAB_PLAN.md:7243).
- **The PvP book already records opponents.** `crew scan-log` covers the open world, the scoreboard reader covers battlegrounds (M20), and both land in crewdb. The deep dive therefore needs only links on rows the tool already has.
- **forevertwinking.com bars AI agents.** Its robots.txt blocks Claude agents, and its terms ban AI-agent content collection, scraping and reuse of its data. Linking is explicitly allowed. So: links only, never data, and the owner supplies example URLs (owner ticket below).
- **forevertwinking's armory has limited reach.** It lists only characters a player has registered through Battle.net, at twink levels (10, 19, 29, 39, 49, 59). A link to an arbitrary opponent will often land on "not found". Blizzard's armory is the universal profile link; forevertwinking sits beside it.

## Assessment, site by site

Verdict key: **Build** = a ticket in this wave. **Reference** = read it when writing our own code, no integration. **Later** = a LAB_IDEAS row, not now. **No** = nothing for wowlab.

| # | Bookmark | What it is | Verdict | How wowlab would use it |
|---|---|---|---|---|
| 1 | forevertwinking.com | Twink database, BiS lists, gear planner, opt-in armory; Forever-only, very active | **Build (link only)** | "ForeverTwinking" profile link on the character page and on every crew/PvP row. Its gear-planner share link (ID-only, format unknown) can open our gear there. Its terms forbid using its data; we never fetch from it. |
| — | Wowhead `/forever/` (named by the owner) | The database | **Build** | Already used: the dressing room. Add `/forever/item={id}` on each gear row, `/forever/spell={id}` in the spellbook view, and (with #6) `/forever/talent-calc` once its encoding is confirmed. **No tooltip script**: `tooltips.js` would run remote code inside a page that can run wowlab commands. That is a security line, not a privacy one, so it stays. |
| — | Blizzard Forever armory (found in research) | Official profiles for every character and guild | **Build** | The primary profile link: own characters, crew, every opponent, guilds. It works for any character, unlike forevertwinking's opt-in list. |
| 2 | Purist (purist.pages.dev) | Transmog / outfit builder using Wowhead's model viewer; Forever-aware | **Build** | The best "see my character" link. Its hash `#2|label|race.sex.1.1.class.forever|0.0.0|opt:choice,…|item,…` takes exactly what the addon records: race, sex, class, customization option:choice pairs, item ids. Unlike the dressing room it shows the player's own face and hair. Pure builder, like `dressing.py`. |
| 3 | wowforeverbuilds.com | Talent calculator, guides; links explicitly welcome | **Build** | "Open my talents" link: `/talents/{class}?b={ranks per tree}&race={slug}`. Needs the Talent table's row/column per talent (wago.tools, already our table source). |
| 4 | wow4ever.quest | News, database, talent calculator (ES/EN), very active | **No (for now)** | Its item and talent links duplicate Wowhead and #3. News is not a tool feature. |
| 5 | wofwforever.com ("Outpost") | Guides; "Journey Companion" lookup by region + name + second name | **Build (one link)** | A third profile link, `…?region=&name=&surname=`, cheap once the profile-link builder exists. Guides: no. |
| 6 | guildsforever.com | Guild finder; slugs from guild names but unreliable (a homepage link 404'd); scraping banned | **No** | The Blizzard guild armory covers guild links. |
| 7 | foreverguild.com | One Horde guild's recruitment page, static since September | **No** | Nothing to link or read. |
| 8 | wow-forever.top | Thin fan portal; its "database" links out to Wowhead | **No** | Wowhead directly is better. |
| 9 | readycheck.fyi | Guides plus a Forever addon compatibility list (HTML only, code scan of build 69893) | **Later** | An "is this addon Forever-ready?" link beside each addon in the addon views, once a stable per-addon URL is confirmed. No machine-readable feed to use. |
| 10 | WoW Forever Atlas / Travelcraft | Static SVG map and route planner | **No** | No licence (no permission to reuse its data); deep links only by place name; no map ids. |
| 11 | ElliotWood/Forever (404) → **wowsims/forever** | Forever DPS simulator (Go/WASM web UI), MIT, released today | **Build (export)** | "Sim this character": write the character's gear and talents in wowsims' import JSON (schemas in its `proto/`), copied or saved for the player to paste into the sim. Its `tools/db2tool/README.md` is also the best reference for Forever's `DBCache.bin` hotfix cache. |
| 12 | wowdev/DBCD + WoWDBDefs | C# DB2 reader; `.dbd` column definitions already cover 1.60.1.71037 | **Reference → Later** | The basis of a future local DB2 reader that replaces the wago.tools fetch (ADR-0033 readers already do CASC/BLTE). WoWDBDefs data is CC BY-SA: fetch it like a game table, never vendor it. |
| 13 | Frostshake/WDBReader | C++ DB reader, GPL-3.0, no Forever | **No** | GPL, so we can't even port it; DBCD covers the same ground. |
| 14 | erorus/casc | PHP CASC extractor, Apache-2.0 | **Reference** | `BLTE/Chunk/Encrypted.php` and `Manifest/Root.php` document the encrypted (`E`) BLTE blocks `casc.py` refuses today. Read it when a needed file is encrypted. |
| 15 | wow.export | Export toolkit, MIT; Forever support since 0.2.20 | **Reference (already)** | Already the cross-check for `blp.py`, `compositor.py` and `charmodel.py`. ADR-0033 rejects a bridge; nothing changes. |
| 16 | warcraft.wiki "Viewing Blizzard's interface code" | How to export FrameXML | **Reference** | Points to Gethe/wow-ui-source, which has a `forever` branch at 1.60.1.71037 with Blizzard's own machine-readable API docs. That feeds #18. |
| 17 | Gethe/InterfaceExport | Lua UI extractor, stale since 2024, no Forever | **No** | Superseded by the wow-ui-source `forever` branch. |
| 18 | mparke/forever-addons | Archived; luacheck + strict fake client generated from Ketho/BlizzardInterfaceResources `forever` branch | **Build (pattern)** | Regenerate the lab-addon's API reference and `make lint-lua` globals from the current Forever build (71037), replacing the 69893 lists from forever-addon-kit. GPL, so we take only the idea, and the data comes from Ketho's repo. Its field guide (CLEU restricted on Forever, SavedVariables cold-start bug) is worth checking against our addon README. |
| 19 | mmobeus/luadata | Rust Lua-literal → JSON parser, MIT | **No** | `luadata.py` is stricter and lossless (L4); this one is not round-trip-safe. |
| 20 | SirAnthony/slpp | Python Lua parser | **No** | Loses numbers (`1e5` → 0, `.5` fails), mishandles escapes and index bases. At most a list of edge cases already covered by our graders. |
| 21 | IlyaSkriblovsky/slpp-23 | Older slpp fork (2018) | **No** | Worse than slpp: regex comment stripping cuts strings. |
| 22 | usrland: AzerothCore with Docker | Private-server emulator for WotLK 3.3.5a | **No** | Private servers are out of scope (ADR-0023) and against Blizzard's terms. |
| 23 | EllesmereGaming/EllesmereUI | Big UI suite, Forever-ready; "all rights reserved" | **Later** | Only if the owner uses it: decode its `!EUI_` profile strings (LibDeflate + a simple serializer) so `profiles` can show and back them up. Its SavedVariables are already snapshotted like any addon's. |

**Ranking for the owner's goal:** Blizzard armory, forevertwinking and Purist first; then Wowhead item and spell links and the wowforeverbuilds talent link; then the wowsims export and the addon API refresh.
