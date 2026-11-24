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
- **Known state (2026-09-22, owner's macOS machine, M10-03 stage 0):** one
  product installed, the Forever beta: flavor folder `_classic_beta_`, product
  code `wow_classic_beta`, version `1.60.1.69913`, branch `us`; no retail
  flavor. Executable `World of Warcraft Beta.app` in the flavor folder
  (directory listing; the process name is **[verify]**). Interface number:
  `16001` is consistent with the patch-number rule for 1.60.1
  (1×10000 + 60×100 + 1) and with `SET engineSurveyPatch "16001"`; an addon
  TOC listing it is only the author's claim. Confirmed in game on
  2026-09-22 (owner, on 1.60.1.69977 per the capture rows):
  `/dump select(4, GetBuildInfo())` printed `16001`, `""`, `" "`: the
  interface number as a number, then two strings, so `GetBuildInfo` returns
  six values on this client. Community documentation names the fifth and
  sixth returns `localizedVersion` and `buildType` **[verify]**; what these
  values mean is not established, and the Lab reads neither.
  `/dump _VERSION` printed `"Lua 5.1"`. Evidence: the owner's screenshot,
  not a committed fixture. Line endings follow the file kind
  (`docs/LAB_FORMATS.md` amendments of 2026-09-22). Combat log, SavedVariables
  line endings (resolved below), `WOW_PROJECT_ID` and the preferred TOC suffix: still open.
- **Known state update (2026-09-22, M10-03 part 2):** the beta patched from
  1.60.1.69913 to 1.60.1.69977 between the stage-0 and stage-1 captures. The
  69977 version is from the capture tool's rows; its `.build.info` is not
  committed. The patch is still 1.60.1, and the in-game check above
  confirms `16001`. SavedVariables line endings are resolved on macOS, build
  69977: CRLF with a leading blank line, no BOM, no indentation (all 105
  files on the install by a byte count); Windows is **[verify]**. No addon in the install ships
  more than one TOC, so the preferred suffix is still open; the Forever
  `Logs/` folder holds no combat log yet.
- **Known state update (2026-09-28, M10-03 part 3):** the beta patched again,
  to 1.60.1.70009 (capture rows). The Forever client writes a combat log
  (`docs/LAB_FORMATS.md` §8 amendment of 2026-09-28): CRLF; `PROJECT_ID,18`
  (not 1; whether it equals `WOW_PROJECT_ID` is **[verify]**);
  `BUILD_VERSION,1.60.1`, the patch only (retail is reported to do the same **[verify]**), so a log alone does
  not say which 1.60.1 build wrote it. The committed log is one short solo
  open-world fight with its file name and timestamps shifted by the capture
  tool. No instance or boss log was recorded, so `COMBATANT_INFO`, bracketed
  groups and instance logging are still open. Still open overall:
  `WOW_PROJECT_ID`, the preferred TOC suffix, and Windows line endings.

*Amended 2026-10-19 (M15, ADR-0033):* the install's game data archives under `Data/` are a source from Wave 6: read-only, in pure Python, by file data id, with the content key's MD5 as the check; nothing under `Data/` is ever written, no key is ever used to decrypt, and nothing is fetched from Blizzard's CDN for a file the client has not downloaded. Whether the install streams is **[verify]**: at 70450, 73 of the 77 `ChrModel` rows had local models and 128 of 300 sampled `TextureFileData` rows were local, but of 300 `ModelFileData` rows the 122 missing were mostly content this product does not ship (119 not in the root at all, 3 in the root but not local); the texture sample was not split that way. A file is reported in one of four states (§17.1). Decoded files go under the data directory keyed by product and build. Format notes: `docs/LAB_FORMATS.md` §10 to §12.

*Added 2026-11-23 (M22-08):* the player's own installed addons are a local source for `wowlab lookup`: QuestieDB (`Interface/AddOns/QuestieDB/`), read-only and as data, never executed (L3). Its quests, NPCs, objects and items are CBOR in base64 in its TOC's `## X-...` metadata, its drop chances are literal tables inside Lua long strings, and its drop corrections are read with a narrow token recognizer (`docs/LAB_FORMATS.md` §14). The corrections Questie picks by the character's faction and class are code and are not read, so a quest given on both sides lists both factions' givers, and some quests show no zone. It is third-party data: no byte of it is committed; the tests use constructed slices, and `test_questiedb_live.py` reads the installed copy when the owner runs it. What it says is what Questie's authors recorded, not what the game does, and it changes when the player updates the addon.

## wago.tools

- **Use:** DB2 tables as CSV for any product and build (ADR-0022), and the
  list of builds per product. The game-data source until local CASC reading
  exists.
- **Auth:** none.
- **Limits:** undocumented; community-run. One connection, descriptive
  `User-Agent`, backoff on 429/5xx, cache forever by `(table, build)`.
- **Endpoints** (recorded 2026-09-21 by M10-08; three GETs, no credentials):
  - `GET https://wago.tools/api/builds` → `200 application/json`, about
    540 KB (gzip on the wire). One object keyed by product code; each value
    is a list of `{product, version, created_at, build_config,
    product_config, cdn_config, is_bgdl}`. `version` is the full build
    string. `created_at` is `YYYY-MM-DD HH:MM:SS` with no zone.
    `product_config` can be `null`. `is_bgdl` **[verify]** is believed to
    mark a background-download (pre-patch) build. 13 products, 2271 entries,
    1695 distinct versions: one version is listed under several products, and
    all 510 such versions have a different `build_config` per product. Every
    product's list is sorted by version, descending, and a product code is
    reused across game versions (`wow_classic_beta` carries 1.13, 2.5, 3.4,
    4.4, 5.5 and 1.60 builds), so neither position nor the highest version
    means newest.
  - `GET https://wago.tools/db2/<Table>/csv?build=<full build string>` →
    `200 text/csv; charset=UTF-8`, LF line endings, no BOM, header row, RFC
    4180 quoting. **The parameter is `build=`.** Proof: the request named a
    build that was not the newest for its product and the response carried
    `Content-Disposition: attachment; filename="<Table>.<that build>.csv"`.
    `gamedata` refuses a response whose `Content-Disposition` filename is not
    exactly `<Table>.<build>.csv`, and one with no such header, so a silent
    fall-back to "latest" can never be cached (L5).
  - The same URL with a build that was never published → `404 text/html`
    (a generic "Not Found" page that also sets session cookies). There is no
    fall-back to another build. `gamedata` turns it into `BuildNotPublished`
    when the builds listing lacks the build and `TableNotPublished` when it
    has it.
  - *Amended 2026-09-30 (M12-02):* the same URL with a table the build
    lacks, at a build the listing lists → `400 application/json`, body
    `{"errors":"Table not found."}`, no `Content-Disposition`, session
    cookies set (recorded by M12-01; the listing that lists the build by
    M12-02). `gamedata` treats a `400` from the table endpoint as it treats
    the `404`: `TableNotPublished` when the listing lists the build, with a
    message that names the status and the URL and says the table may not
    exist in this game version, and `BuildNotPublished` when it does not.
    The status alone decides: the body is not read, so a `400` with an HTML
    or empty body is the same answer, and nothing from it is printed. A
    `400` is never retried and never cached. A `400` from `/api/builds` is
    still an unexpected response.
- **Fixtures:** `lab/core/tests/fixtures/wago/`. A whole recording is a
  public copy of the table and the terms below say do not mirror, so a
  table is recorded whole only when it is small and a test reads it whole
  (`docs/LAB_PLAN.md` §14.5 D4 and §14.6); everything else is an
  ID-filtered subset of whole verbatim lines cut by `scripts/wago_subset.py`
  (the rule in the fixture index). Response
  headers are not committed (the 404 sets cookies); what the tests need from
  them (status, content type, `Content-Disposition`) is in the index rows.
  The builds listing is stored gzip-compressed because the verbatim body is
  over the repository's 512 KB file limit; the index row has the SHA-256 of
  the decompressed body.
- **Live check:** `uv run pytest -m live lab/core/tests/test_gamedata_live.py`
  (two requests; by hand only, ADR-0012). It fails if the recorded table's
  bytes change upstream. Hypothesis, not yet observed: the expected cause is
  a header rename when the community column definitions are updated (the
  recording contains a WoWDBDefs placeholder column,
  `Field_9_0_1_34490_018`), not a change to the data. The cached copy stays
  as fetched either way (L5).
- **Client limits** (M10-08): no cookies are kept or sent; `https` only;
  decoded bodies are capped (16 MiB for the listing, 1 GiB for a table); a
  gzip body with anything after its first member is refused (the recording
  shows single-member bodies); each download has one 30-minute wall-clock
  deadline across all its attempts; a table response must carry
  `Content-Disposition`; a lookup that misses refetches the listing at most
  once per five minutes; a `404` or `400` from the table endpoint is decided
  by its status, and its body is never read (M12-02, 2026-09-30). Known limits: a crash between publishing a table
  and writing its sidecar leaves a valid table with no fetch record, which
  is never reconstructed; temp files from a killed process are never swept
  (nothing is pruned implicitly, ADR-0022); on a POSIX filesystem without
  hard links (exFAT, FAT, some network mounts) the cache is refused with
  `CacheLocationError` before any request, because `rename` there could
  replace a cached table. Remedy: choose a cache directory on a filesystem
  with hard links (`GameData(cache_dir=...)`). On Windows no location is
  refused for this: `rename` there fails instead of replacing, so when the
  hard link fails for any reason the table is published by rename, or the
  file that got there first is kept. The errno is not consulted on Windows,
  because CPython maps `ERROR_INVALID_FUNCTION` and every unlisted Windows
  error to `EINVAL` (`PC/errmap.h`); which error `CreateHardLinkW` returns
  on FAT, exFAT or a network share is **[verify]** and the code does not
  depend on it. A builds-listing meta file with a timestamp that has no
  zone (hand-edited or restored) is a cache miss, not an error.
- **Terms:** data is Blizzard's, extracted by the community; personal,
  non-commercial use. Do not mirror tables publicly.

## Format references

- wowdev.wiki (CASC, TACT, DB2, M2, BLP, WMO, ADT), warcraft.wiki.gg (TOC
  format, SavedVariables, CVars, console commands, API change summaries per
  patch), WoWDBDefs (DB2 column definitions per build). References, not
  runtime dependencies. `docs/LAB_FORMATS.md` cites what it takes from them
  and defers to real fixtures.
- The addon's API reference (M21-09, 2026-11-23): Ketho/BlizzardInterfaceResources,
  branch `forever` (no licence file; only the names are kept, in
  `lab/addon/forever_api/`, generated by `scripts/forever_api.py` from one
  pinned commit, fetched by hand), and Gethe/wow-ui-source, branch `forever`,
  for where a name the list leaves out is defined. Read at a pinned commit,
  never fetched by CI or a test (ADR-0012).
- The client's own commands (M22-18, 2026-11-23): the same script reads
  wow-ui-source (`forever`, commit `734f0653`, 1.60.1.71037: the slash-command
  code, the key-binding files, the `C_EncodingUtil` documentation) and
  Ketho's `GlobalStrings` (the texts a player types and the English binding
  names) into `lab/core/src/wowlab_core/client_lists/`, names and short
  labels only, which the macros and keys views read. Pinned, fetched by
  hand, never by CI or a test. The Forever talent loadout string format is
  in `docs/LAB_FORMATS.md` §13, from the same commit.

## World of Warcraft: Forever — reported state

Collected 2026-09-20 from Blizzard's announcement coverage, community
datamining sites and addon-author reports during beta week one. **None of it
is verified on the owner's machine yet**; M10-03 replaces this list with
findings. Library code must not depend on any of it (L6).

- Beta 2026-09-17 to 2026-10-21, PC (Windows, macOS) through Battle.net;
  launch announced for 2026-11-04; beta characters are wiped.
- Beta build observed as `1.60.1.69913`, then `1.60.1.69977`; interface version reported as
  `16001` (one report argues `160001`); confirmed `16001` in game on
  2026-09-22 (Known state above).
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
| 2026-09-21 | wago.tools | First recording. The build parameter is `build=`; an unpublished build is a 404 HTML page, not a fall-back. All 13 product lists are sorted by version, descending, and a product code is reused across game versions: `wow_classic_beta` carries 1.13, 2.5, 3.4, 4.4, 5.5 and 1.60 builds, so its list opens with `5.5.0.x` and has the `1.60.1.x` builds (69876, 69893, 69913) further down; neither position nor the highest version means newest. One version string appears under several products (510 of them, each with a different `build_config` per product). The trailing build number is not unique: `10.0.0.46479` / `10.0.2.46479` under `wowlivetest` and `2.5.5.68575` / `2.5.6.68575` under `wow_anniversary` share a `build_config` | `lab/core/tests/fixtures/wago/` (M10-08) | `gamedata` never reads "latest" from the listing and keys on the full version string, never the trailing build number. `resolve_build` matches the exact version, preferring the flavor's own product. wago's table endpoint is keyed by version string alone, so a version listed only under another product still selects the same export and is accepted; from such a match only `.version` describes the installed flavor, `product` and the config hashes do not |
| 2026-09-22 | Local install (Forever beta) | First capture. The account's folder tree is not the retail `<Realm>/<Character>/`: a digits-only folder (almost certainly the realm's numeric id) holds `<First>-<Second>` character folders: Forever characters have a player-chosen first and second name (owner, 2026-09-22), and retail-style `<Realm>/<First>/` twins hold only AddOns.txt. The scrub tool treated the digits-only folder name as a realm name and replaced it inside ordinary numbers, and scrubbed `<First>-<Second>` only as a whole. Separately, a placeholder `--extra-name GUILD` rewrote the client's own `GUILD` chat-channel token | M10-03 stage-0 staging, never committed; four corrupted files dropped | Scrub-tool follow-up (#33): digits-only folder names get a path pseudonym only, `<First>-<Second>` folders are split into both names, and an `--extra-name` equal to a word the client writes is refused; stage 0 re-run after it merges |
| 2026-09-22 | Local install (Forever beta) | Build changed mid-corpus. The fixture tree now mixes 69913 (`.build.info` and part 1) with 69977 (part 2). 69977 is missing from the 2026-09-21 wago recording, so replaying it gives `BuildNotPublished` | `lab/core/tests/fixtures/README.md` rows | Each row's `client_version` is authoritative; the tree is not a single-build install; re-record the wago listing before relying on 69977 |
| 2026-09-28 | Local install (Forever beta) | Build changed again: part 3 rows are 1.60.1.70009, while parts 1 and 2 are 69913 and 69977. The combat log header names only the patch, so a log's own build is not known | `lab/core/tests/fixtures/README.md` rows | Each row's `client_version` is the install's build at capture time; the tree is not a single-build install |
| 2026-09-28 | Local install (Forever beta) | The lab-addon crashed the client on its first login (M11-03): one call to `C_TransmogCollection.GetCategoryAppearances(9)`, about 4 s after entering the world on 1.60.1.70009, hit the client assertion `BC_ASSERT(this->m_has_value)`. `pcall` cannot catch a C++ assertion, so any unverified C API call from the addon can take the client down. Cause not established | The client's crash report (not committed: it names the character, account and guild) | M11-20: the addon calls nothing in `C_TransmogCollection` on any client; appearances are recorded as absent with the reason (§13.1) |
| 2026-09-29 | wago.tools | A table the build does not have answers `400`, not `404`. At build 1.60.1.70058, which wago lists, `TraitSubTree`, `TraitTreeLoadout`, `TraitTreeLoadoutEntry` and a made-up table name all gave `HTTP 400` from `/db2/<Table>/csv?build=…`; `gamedata` reports the bare URL and the status. The talent tables that do exist are small (`TraitTree` 17 rows, `TraitNode` 558) and `SpellName` has 31,716 rows (832,295 bytes). `TraitTree` carries the WoWDBDefs placeholder columns `Field_10_0_0_45697_006` and `_007` | A hand check with `wowlab db2 head` on 2026-09-29 (`docs/LAB_PLAN.md` §14.1); the 400 response is recorded by M12-01 | M12-02 maps a `400` at a listed build to `TableNotPublished`; Wave 3 types columns by inference (ADR-0028, proposed), not from WoWDBDefs. *Handled 2026-09-30 (M12-02):* a `400` from the table endpoint is `TableNotPublished` ("the table may not exist in this game version") at a listed build and `BuildNotPublished` at an unlisted one; never retried, never cached, body not read (the endpoint notes above) |
| 2026-09-29 | Local install (Forever beta) | After one applied barber-shop change on 1.60.1.70058 (M11-23), the lab-addon's customization record read `recorded_at = "open"`, though the client had not refused `BARBER_SHOP_APPEARANCE_APPLIED`, and it had no `chr_model_id`. The file could not say whether the applied event never came or an open came after it, nor why the model id was missing | `lab/core/tests/fixtures/macos-70058/forever/WTF/Account/90000001#6/1/Labchard-Labrealmg/SavedVariables/WowLab.lua`; `docs/LAB_FORMATS.md` (M11-23 follow-up) | M11-29 (schema 2): the addon records `events_received` (with six count-only barber-shop events, one a made-up control name) and `chr_model_id_absent`, and changes nothing about when it records; the cause stays open until the owner's next capture (`lab/addon/README.md`, "M11-29 capture step") |
| 2026-10-16 | Local install (Forever beta) | Owner's M13-05 schema-3 capture on 1.60.1.70425, with the M11-29 barber-shop check. After one Accept with one sit, the addon counted `BARBER_SHOP_APPEARANCE_APPLIED` 1 and `BARBER_SHOP_OPEN` 2. A second `BARBER_SHOP_OPEN` came after the applied event and replaced the applied record, so the saved record reads `recorded_at = "open"`, holding the new look. Whether the shop window really reopens is **[verify]** (the owner saw it close), and so is why `GetViewingChrModel` returned nil. Other surprises from the same capture: a `#showtooltip` macro slot has no `sub_type`. `ACCOUNT_BINDINGS` and `CHARACTER_BINDINGS` were not numbers at the last gather. A global `issecretvalue` is present, although forever-addon-kit's 69893 list lacks it. The owner's `/say` macro and a spell dragged onto a key are in neither `macros-cache.txt` nor `bindings-cache.wtf`, so those caches may predate the session **[verify]** | `lab/core/tests/fixtures/macos-70425/forever/WTF/Account/90000001#6/1/Labchard-Labrealme/SavedVariables/WowLab.lua`; `docs/LAB_FORMATS.md` (amendment of 2026-10-16) | No addon change. M11-29's "reason it cannot" is the second open. Keeping an applied record through a later open would be schema 4 (now schema 5; schema 4 is M15-01's) with its own capture. The README, §13.1 and LAB_FORMATS say what is confirmed and what stays **[verify]** |
| 2026-09-30 | wago.tools | Recorded for Wave 3 (M12-01). The `400` for a table the build lacks (`TraitSubTree` at 1.60.1.70058) is `application/json`, body `{"errors":"Table not found."}`, with no `Content-Disposition`; it sets two cookies, like the `404` page. The nine Trait tables are byte-identical at 1.60.1.70009 and 1.60.1.70058. Six `TraitDefinition.SpellID` values (20234, 22571, 407669, 426158, 1309389, 1310310) have no row in that build's `SpellName` export; one is on a node of tree 1083, five on entries no node lists | `lab/core/tests/fixtures/wago/` (M12-01 rows) | The body is M12-02's fixture. A spell the table lacks is a gap in the export, not in the subset; the M12-01 test pins the six so a new gap is seen. Whether they are hotfix-only is **[verify]** |
| 2026-09-30 | wago.tools | A new listing, recorded for M12-02 because no committed listing had 1.60.1.70058. Since 2026-09-28 wago lists a 14th product, `wow_cn_beta`, with one build, 1.60.1.70094, and `wow_classic_beta` has moved on to 1.60.1.70124 (created_at 2026-09-30 01:57:04) beside 70058, the build of the M12-01 tables | `lab/core/tests/fixtures/wago/builds.2026-09-30.json.gz` (index row) | Nothing in the library changes: products and builds are discovered, not named (L6), and `resolve_build` still matches the exact installed version. What `wow_cn_beta` is (a regional Forever beta?) is **[verify]** |
| 2026-10-18 | Local install (Forever beta) | `WTF/Account/` holds, beside the account folder, a `SavedVariables/` folder the client writes itself: `Blizzard_GamepadSmartNavigation.lua`, `Blizzard_PTRFeedbackGlue.lua`, `Blizzard_SavedSets.lua` and `Blizzard_SettingsDefinitions_Shared.lua`, each with a `.lua.bak`, written in the same second, at times that do not match the account folder's latest saves, in the client's SavedVariables layout (`NewSettingsSeen = {}`). `layout` took every folder there as an account, so every account-scoped command without `--account` refused with "2 accounts (<account>, SavedVariables)". When the client writes these files and whether it reads them back are **[verify]** | The owner's report of 2026-10-17 and a read-only directory listing of the owner's install on 2026-10-18 (not captured) | M13-50: an account folder has the `<digits>#<n>` name shape or holds a character folder; `WTF/Account/SavedVariables/` has file-map rows of its own (`client-savedvariables*`) and its files belong to no account (`LAB_FILE_MAP.md`, "Which folders under `WTF/Account/` are accounts") |
| 2026-10-19 | local install | The Battle.net agent patched the Forever client from 1.60.1.70425 to **1.60.1.70450** (product `wow_classic_beta`, `build-name WOW-70450patch1.60.1_ForeverBeta`) between the Wave 5 captures and the Wave 6 research pass. wago.tools and WoWDBDefs already carried 70450; three tables the render chain might want are not published for it (`ChrCustomizationCondModel`, `HelmetGeosetVisData`, `ItemAppearanceModifier`: HTTP 400, the Lab's `TableNotPublished`) | the research spike's read-only run; no fixture yet | the `macos-70425` fixtures stay; a 70450 capture is the next one (M15-01's runbook) |
| 2026-10-19 | Local install (Forever beta) | Owner's M15-01 schema-4 captures on 1.60.1.70450 (the patch in the row above), two characters (`Dangling-Participle`, an Orc mage; `Sangraal-Sett`, an Undead warlock), both the first body type. `UnitRace`, `UnitSex` and `UnitClass` all answered: races 2 and 5, classes (8, `MAGE`) and (9, `WARLOCK`), `UnitSex` 2 on both. `Sangraal-Sett`'s carried barber record (`sex` 0, `race_id` 5, from an earlier session and build) agrees with `UnitSex` 2 read as `Enum.UnitSex` 0. `UnitSex` 3 and 1 are unobserved, and whether `UnitSex` is fresh 2 s after `BARBER_SHOP_APPEARANCE_APPLIED` is untested | `lab/core/tests/fixtures/macos-70450/forever/WTF/Account/90000001#6/1/Dangling-Participle/SavedVariables/WowLab.lua`, `…/1/Sangraal-Sett/SavedVariables/WowLab.lua`; `docs/LAB_FORMATS.md` (amendment of 2026-10-19) | No addon change. The README API table and §13.1 record the three calls as run on 70450; the second body type stays wanted (`docs/handoffs/M15-01.md`) |
| 2026-10-19 | local install | Every one of the 16 `Data/data/*.idx` files at 1.60.1.70450 also holds one entry per archive (65 per file, 1,040 in all) whose key's bucket is the one before the file's: a 30-byte record header at offset 30 × the file's bucket ("segment headers", from memory, **[verify]**), so `docs/LAB_FORMATS.md` §10's bucket rule has an exception | `lab/core/tests/fixtures/macos-70450/derived/casc-index.json` (M15-03); `docs/LAB_FORMATS.md` §10, amendment of 2026-10-19 | No change to the lookup: it searches only the key's own bucket, and no entry of the exception holds a file's data; `casc` counts them per bucket in its report |
| 2026-10-19 | local install | The research spike's 4,775 BLTE blocks were `N` and `Z` only; the M15-04 census of every 256th own-bucket record of the local index (4,079 records, every product's records the index lists, 6,510 blocks) found 19 `E` blocks (Salsa20, key-name length 8, IV length 8) in 9 records under two key names, and a read-only survey of every one of the 1,041,954 own-bucket records' first 42 bytes found one record that is not BLTE: the build config's `patch` manifest, stored as it is with the magic `PA` | `lab/core/tests/fixtures/macos-70450/derived/casc-blte.json` (M15-04); `docs/LAB_FORMATS.md` §10, amendment of 2026-10-19 (the survey is in the amendment's text, not the fixture) | An `E` block is refused with its key name and never decrypted (ADR-0033); the non-BLTE record is refused as unsupported; `docs/LAB_PLAN.md` §17.1 step 3 and §17.7 amended: encrypted blocks are met, LZ4 stays [verify] |
| 2026-10-19 | local install | The root at 1.60.1.70450 (M15-05's reader, enUS on macOS) breaks four assumptions. (1) Content bit `0x1`, named nowhere in the reference: 96,382 ids have two rows taken, one in a `0x1` block and one not, of different content, and only the other row's content is in the local index. (2) §17.7's 1,244,477 ids keep `LowViolence`; skipping it gives 1,243,524. (3) Of the 20 ids with no local copy, 4 are in one macOS-only block flagged `0x4` (`Install` in wowdev.wiki, from memory, **[verify]**: possibly loose files outside the archives). (4) A third encryption key name, `4A65D29877A83715` (id 874570, an `Encrypted` root block, no plain copy), not among M15-04's two | `lab/core/tests/fixtures/macos-70450/derived/casc-files.json` (`root`, `local_census`, `rows`) | Rows without `0x1` are tried first (`casc.ROOT_FLAG_1`), then the rest; an encrypted copy is passed over for the next; `stat`'s note names `0x1`-only and `Install`-flagged rows; `docs/LAB_FORMATS.md` §10 amended and `docs/LAB_PLAN.md` §17.1 and §17.7 noted |
| 2026-11-17 | local install | The owner's install is at **1.60.1.70959**: flavor folder `_forever_`, product `wowf` (the launch client; the 70450 derived fixtures were of `_classic_beta_`, `wow_classic_beta`). The `@live` tests graded by the 70450 derived fixtures (`test_casc_live.py`, `test_casc_blte_live.py`, `test_casc_source_live.py`) each compare the install's build with its fixture's and skip on a mismatch, but only after opening the `_classic_beta_` flavor: if that folder or its `wow_classic_beta` row in `.build.info` is gone, `casc` raises `BuildRowError` first and the test errors rather than skips. They were not re-run on 70959. M15-07's derived fixture is the first under `macos-70959/`; it was first written with the client running (2026-11-17) and regenerated with it closed (2026-11-18) | `lab/core/tests/fixtures/macos-70959/derived/blp.json` (M15-07) | The 70450 fixtures stay as they are; new derived fixtures go under their own build's run folder |
| 2026-11-17 | local install | Character customization textures at 1.60.1.70959 are not all DXT: of 300 of `ChrCustomizationMaterial`'s textures, 86 are palette textures (alpha depth 0 or 8, alpha type 8), beside 127 DXT1 and 87 DXT5, from 128×32 and 64×128 up to 2048×1024. None of the 3,695 DXT headers with alpha type 0, 1 or 7 sampled falls where the wiki's and wow.export's DXT rules disagree; BC5 (alpha type 11), which the wiki's rule does not cover, is refused before either rule is applied. No stored mip 0 size differs from the computed one in the 4,127 textures decoded (the two samples may overlap). No DXT1 texture of alpha depth 0 met (2,053 + 126) has an index 3 in a three-colour block | `lab/core/tests/fixtures/macos-70959/derived/blp.json` (M15-07); `docs/LAB_FORMATS.md` §11, amendment of 2026-11-17 | `blp` decodes palette textures, so the compositor (M15-10) gets them as RGBA like the rest. The rule-conflict refusal stays. The index-3 question stays **[verify]**: it was not met in these samples, so M15-10 may not rely on either answer for a texture outside them |
| 2026-11-17 | local install | The launch client. The Forever client now lives in the flavor folder `_forever_` under product `wowf` (first captured at 1.60.1.70888, M16-05), and the Battle.net app has since patched it to 1.60.1.70938 and then **1.60.1.70959**. A read-only look at the owner's install on 2026-11-17 and 2026-11-18 found one row in `.build.info`, `us`, Active, `1.60.1.70959`, `wowf`. The `_classic_beta_` folder is still on disk beside it. It holds `.flavor.info` (`wow_classic_beta`), `Cache`, `Errors`, `Fonts`, `Interface`, `Logs`, `Utils` and two client apps, but no `WTF` folder, and `.build.info` has no row for its product. So the beta's folder stays, but its build is no longer installed. The §17.3 numbers measured on the beta at 70450 recur on `wowf` 70959 for `humanmale_hd`: version 272, 215 bones, 45 attachments, 113 sections, the eleven texture types, the 37,813-entry lookup and 147,966 indices | `lab/core/tests/fixtures/macos-70959/derived/m2-models.json` (M15-08); `docs/LAB_FORMATS.md` §12, amendment of 2026-11-17 | Nothing in the library changes: the flavor and product are discovered (L6). The derived fixture is filed under its own build, `macos-70959` |
| 2026-11-17 | wago.tools | A hand-run fetch through `gamedata` for M15-08 got `ChrRaceXChrModel`, `ChrModel`, `CreatureDisplayInfo` and `CreatureModelData` at build 1.60.1.70959, all four published. They resolve the human body models: race 1 → `ChrModel` 1 (sex 0, display 57899) and 2 (sex 1, display 56658) → `CreatureModelData` 7661 and 7599 → file data ids 1011653 and 1000764 (sidecars record the fetch at 2026-11-18T03:04Z UTC, 2026-11-17 local). The listing in the owner's cache dates from 2026-11-12 and does not yet have 70959. Its 1.60.1 builds 70840, 70862 and 70888 are under `wowf`, so whether wago lists 70959 under `wowf` is **[verify]**. The table endpoint is keyed by version alone and answered | gamedata's cache on the owner's machine (not committed); `lab/core/tests/fixtures/README.md`, row `macos-70959/derived/m2-models.json` | No change: `resolve_build` matches the exact version. M15-09 records its own CSV slices for its fixtures |
| 2026-11-18 | wago.tools | Item tables recorded for the gear chain (M15-13) at 1.60.1.70959 by a hand-run `GameData.table` fetch (18:41 UTC; recording only, never from a test): `Item`, `ItemBonus`, `ItemModifiedAppearance`, `ItemAppearance`, `ItemDisplayInfo`, `ItemDisplayInfoMaterialRes`, `ModelFileData` (10.5 MB, 121,133 rows), `ComponentModelFileData`, `TextureFileData` (byte-identical to M15-09's download), `ComponentTextureFileData` and `HelmetGeosetData`; `HelmetGeosetVisData` answered 400 again. Surprises: `ItemBonus` has no type-7 (appearance) row, and every `ItemModifiedAppearance` row has modifier 0, one per item, so no bonus id selects a different appearance on Forever's data; no `AttachmentGeosetGroup` cell is non-zero; `ComponentTextureFileData` marks a file for either body type with `GenderIndex` 3 (8,276 rows, all race 0; read as either body type, **[verify]**) and uses 2 only on 143 rows of named races, while every race-0 row of `ComponentModelFileData` has 2; `HelmetGeosetData` carries a WoWDBDefs placeholder column `Field_10_0_0_46047_003` (-1 or 32) | `lab/core/tests/fixtures/wago/*.1.60.1.70959*` (M15-13 rows: `ItemBonus` whole, the rest subsets) | `renderplan` reads `GenderIndex` 2 and 3 both as "either body type" (**[verify]**), keeps the type-7 rule graded on constructed rows only, and does not read the placeholder column (`docs/LAB_PLAN.md` §17.4, note of 2026-11-18) |
| 2026-11-23 | Local install (QuestieDB 1.1.4) | The ticket expected QuestieDB's tables as Lua strings compiled at run time. The installed copy for Forever (`QuestieDB_Forever.toc`, 18.8 MB, `## X-Mode: baked`, `## X-BUILD-TIME: 2023-11-14T22:13:20Z` as written) keeps every quest, NPC, object and item in 133,830 TOC metadata directives, base64 of CBOR (the id headers zlib-compressed, long values split into `~N~` chunks), decoded by the client's `C_EncodingUtil`; only the drop chances (`wowheadData`, `cmangosData`) are `[[return {...}]]` strings, and their text is a literal. Every row of the owner's copy decoded (23,978 items, 15,562 named NPCs, 6,079 quests, 7,459 objects) in under a second. The corrections Questie picks by the character's faction and class are Lua code (without them a quest given on both sides lists both factions' givers, and some quests show no zone); `itemDropCorrections.lua` is Lua code whose table pointers (`DropKeys.WOWHEAD`) a narrow recognizer reads as data (ruling of 2026-11-23). `layout`'s TOC cap (1 MiB) is below this TOC's size: on the owner's install `wowlab addons list` shows `QuestieDB  1 TOC files; which one loads depends on the game type` (no title, no interface), and `wowlab addons check` shows `interface   not read: larger than 1048576 bytes; not read` (a separate ticket raises the cap) | A read-only reading of the owner's install on 2026-11-23 (not committed: third-party data) | `questiedb` reads the store with a narrow CBOR reader and the drop strings with `luadata`; the code is left out and listed (`docs/LAB_FORMATS.md` §14); the TOC is read with its own 256 MiB cap |
| 2026-11-23 | Local install (Forever launch client, US) | The owner checked in game, with another player targeted. `UnitName("target")` returned two values, "Tay" and "Soulrend": on Forever its second value is the second name, where retail returns the realm. `GetPlayerInfoByGUID(UnitGUID("target"))` returned the name (6th value) "Tay Soulrend" and the realm (7th) ""; its 8th value was 22, probably the level **[verify]**. `GetCurrentRegionName()` returned "US", as the install's `Config.wtf` has `SET portal "US"`. So the lab-addon's crew key (`name .. "-" .. realm` from `UnitName`) is `<First>-<Second>` on Forever. The one combat-log fixture (beta, 1.60.1.70009) names the owner `Labchard-LabrealmbPartbPartcPartd-`: in that capture run its middle part is the pseudonym of the retail-style realm folder (`Labrealmb Partb Partc Partd`, spaces removed), not the second name (`Labrealmg`), so on the beta the log wrote the realm there; the launch client's log writes the second name (next row) | The owner's in-game results, reported in the session; no fixture | M21-02: a crew key's second part is read as the second name, with no realm, on a flavor whose account folders hold `<digits>/<First>-<Second>/` characters (found from the folders, L6); retail keeps `Name-Realm`; `cn` is a region (conductor ruling) |
| 2026-11-23 | Local install (Forever launch client, US) | The launch client's combat log (`WoWCombatLog-112326_112119.txt`, header `COMBAT_LOG_VERSION,22,ADVANCED_LOG_ENABLED,0,BUILD_VERSION,1.60.1,PROJECT_ID,18`) names other players in four parts, `"<First>-<Second>-<Realm>-<Region>"`: `"Kshatriya-Sune-Roleplay PvP-US"`, `"Mark-Buffâlo-Roleplay PvP-US"`, `"Aldric-Deathcrown-Roleplay PvP-US"`. The realm keeps its space and the region is set. The beta's log (build 1.60.1.70009) wrote three parts, `"<Name>-<Realm>-"`: the realm without its spaces, no second name, an empty region | `fixtures/macos-71037/forever/Logs/WoWCombatLog-010926_073444.txt` (first 120 lines, first names pseudonymised by the capture tool) and `fixtures/macos/forever/Logs/` (beta) | M21-02: on a flavor with second-name folders, `worldpvp` reads four or more parts as first, second, realm, region and keys `<First>-<Second>`, as the crew book does; three parts keep `<Name>-<Realm>`. The database (schema 5) keeps a four-part name's second name, realm and region in `log_names`; the schema-5 step queues a launch log read before this change for a fresh read under the new keys; a log no longer at its stored path, or no longer the same log, keeps its old keys |
| 2026-11-23 | Ketho/BlizzardInterfaceResources, Gethe/wow-ui-source | First reading for the addon's API list (M21-09). Ketho's `forever` branch is at commit `e230342a` (1.60.1.70938, 2026-11-14), behind Gethe's `forever` branch (1.60.1.71037, commit `734f0653`) and the owner's client of 2026-11-20. Its six files are Lua chunks of table literals (`local NAME = {...}` and a closing `return`); LuaEnum is plain assignments. FrameXML tables and constants (`TooltipDataProcessor`, `ItemLocation`, `SlashCmdList`, `INVSLOT_*`) and GlobalStrings (`UNKNOWNOBJECT`) are not in the six files, so "not in the list" does not mean the client lacks a name. `ACCOUNT_BINDINGS`, `CHARACTER_BINDINGS` and `MAX_ACCOUNT_MACROS` are defined nowhere in the 71037 UI source | `lab/addon/forever_api/` (generated); `lab/addon/README.md`, "API status" | The README's Baseline column says where a `no` name is defined; `make lint-lua` checks the column against the list |
| 2026-11-23 | wago.tools | Item stat tables recorded for the twink panel (M22-06) at 1.60.1.70888 by a hand-run `GameData.table` fetch (recording only, never from a test): `Item`, `ItemSparse`, `RandPropPoints`, `ItemXBonusTree`, `ItemBonusTreeNode`, `ItemBonus`, `ItemNameDescription` and `Faction`. Three surprises. (1) `ItemSparse` has a row for only 7848 of the 19079 weapons and armour pieces `Item` lists (11231 without; 11230 at 70959, 11158 at 71037), among them most of the owner's worn random-suffix items (Willow Boots 6537, Willow Bracers 6543, Willow Gloves 6541, Aboriginal Sash 14113) and Curved Dagger 2632. The server sends these items as hotfixes, which wago.tools' copy lacks; the client keeps them in its hotfix cache (GLOSSARY, "Hotfix cache"; the owner's finding of 2026-11-23: `_forever_/Cache/ADB/enUS/DBCache.bin`, XFTH v9, build 71037, 8,401 valid `Item` and `ItemSparse` rows, the five above among them). In the classic range 4,944 are missing, of which 4,108 are in that cache. wowlab will read them from it (M22-23). (2) `ItemSparse` has no stat amount column: each stat is a share per 10000 (`StatPercentEditor_<n>`) of the item budget in `RandPropPoints`, so amounts are worked out (WoWSims' Forever sim does the same). (3) 1101 uncommon to epic weapons and armour above item level 25 have `RequiredLevel` 0, and many more have 1 (A Lesson in Destruction 276897, item level 38): mostly quest rewards, whose quest's level is the real gate (M22-08), and some raid drops (Deathmist Mask 22074, item level 60) | `lab/core/tests/fixtures/README.md` (the 70888 `wago/` rows) | `twink` scores only items with an `ItemSparse` row and lists a worn item without one as not scored, with the count in a note; it works stat amounts out from the budget, **[verify]** against tooltips; it reads a required level of 0 or 1 as none and leaves out such an item whose item level is more than `no_level_margin` (5, editable) above the bracket, with the count in a note |
| 2026-11-23 | Local install (Forever launch client, US) | The launch client's combat log writes a dungeon's zone line with difficulty 0 (`ZONE_CHANGE,33,"Shadowfang Keep",0`), the value `worldpvp` took for the open world, so a dungeon fight was counted as world PvP. The same log (`WoWCombatLog-112326_133025.txt`) holds three `COMBAT_LOG_VERSION` headers: logging switched on again within one session appends to the same file | `lab/core/tests/fixtures/macos-71037/forever/Logs/WoWCombatLog-012427_232527.txt`, `lab/core/tests/fixtures/wago/Map.1.60.1.71037.csv` (M22-22) | Owner decision of 2026-11-23, "Use the map table": the open world is a map whose `InstanceType` in the build-keyed `Map` table is 0; the difficulty decides only when the table is not cached, and the text says so; a schema-6 re-check removes stored encounters that were in an instance |
| 2026-11-23 | Gethe/wow-ui-source, Ketho/BlizzardInterfaceResources | The client's slash-command, key-binding and `C_EncodingUtil` lists (M22-18), from wow-ui-source's `forever` branch at `734f0653` (1.60.1.71037) and Ketho's `GlobalStrings` at `e230342a` (1.60.1.70938). Surprises: the texts a player types (`SLASH_CAST1 = "/cast"`, the emotes' `EMOTE<n>_CMD<m>`) and the English binding names are not in wow-ui-source at all, only in the client's strings, which Ketho has and Gethe does not; Ketho's `itIT.lua` is byte-identical to `enUS.lua`; three commands the slash-command code registers (`PET_DEFENSIVEASSIST`, `UI_ERRORS_OFF`, `UI_ERRORS_ON`) have no text in the 70938 strings, and frFR's `SLASH_COUNTDOWN2` is two words; `Blizzard_SettingsDefinitions_Frame/Keybindings.xml` holds frame templates and no binding; `ChatConstantsDocumentation.lua` holds a bare name (`Value = FULLNAME_MAX_CHAR`), so it cannot be read as data whole; the `Camelot` talent frame hides the loadout import and export controls. | `lab/core/src/wowlab_core/client_lists/` (each file's `# not read:` lines); `docs/LAB_FORMATS.md` §13 and its amendment of the same date | A newer client may add a command the lists miss: each "not the game's" mark names the build. Moving the pins is `scripts/forever_api.py`'s docstring. |
| 2026-11-23 | Outside sites linked to (M21-03) | Address shapes read by the M21-03 agent, by hand, for `link_sites.toml`'s examples (never from a test; wowlab itself requests none of them): Blizzard's Forever armory answers `…/en-us/forever/us/armory/character/sandgraal-rizzen` and the capitalised form alike (its og:url only repeats the address asked for; the page's own `"slug"` field, `sandgraal-rizzen`, is what shows the address is lowercase), a guild as `…/armory/guild/booty-bay-cartel`, an unknown character or the wrong region with HTTP 500; the character page names the ruleset (`roleplay-pvp`). wofwforever.com's Journey Companion is a GET form at `/en/tools/one-shot-manager/` with fields `name`, `surname` (24 characters each) and `region` (`us`, `kr`, `eu`, `tw`), which its script reads back from the address and looks up. Raider.IO's own API gave the profile address `https://forever.raider.io/characters/us/pvp-rp/Sandgraal%20Rizzen` (name with a space); its pages refused an automated request (403). Keystone.guru's Forever maps are `/explore/forever/<slug>` (`the-deadmines`, `zulfarrak`; `zul-farrak` is a 404). Wowhead and Warcraft Logs refused an automated request (403), so their examples are the ticket's shapes, marked [verify]. ForeverTwinking was not opened (its terms); its two examples are the owner's. | `lab/core/src/wowlab_core/link_sites.toml` `[[example]]` | Templates and slug rules as data; a site's change is a change of that file |
| 2026-11-24 | Outside sites linked to (M21-03, review round 1) | Confirmed by the domain reviewer and the M21-03 agent, by hand: Blizzard's four armory locales serve (`en-gb/forever/eu`, `ko-kr/forever/kr`, `zh-tw/forever/tw`; the locale is only the page language, and `en-gb/forever/us/…` serves a US character), accented and Chinese names percent-encoded serve (`zo%C3%A9-abaddon`, the page's slug `zoé-abaddon`; `thrall-p%C3%A5-tr%C3%A5den`), an unknown guild or character answers HTTP 500. Blizzard's ruleset slugs map to Raider.IO's realm slugs as `normal`/`normal`, `pvp`/`pvp`, `roleplay`/`rp`, `roleplay-pvp`/`pvp-rp` (characters in US, EU and TW). Raider.IO's `profile_url` percent-encodes accents (`Zo%C3%A9%20Abaddon`). About 250 Forever characters and guild names from Raider.IO's search: every character name two parts of letters only, no guild name with an apostrophe. Keystone.guru: `scarlet-monastery`, `sunken-temple`, `blackrock-spire` and `dire-maul` are not pages; the wings (`scarlet-monastery-graveyard`, `-library`, `-armory`, `-cathedral`), `the-temple-of-atalhakkar`, `lower-`/`upper-blackrock-spire` and `dire-maul-east`/`-west`/`-north` are. | `link_sites.toml` `[locales]`, `[rulesets]`, `[slugs.*]`, `[[example]]` | [verify] resolved for those; Wowhead, Warcraft Logs' code shape and Raider.IO's letter case stay [verify] |
