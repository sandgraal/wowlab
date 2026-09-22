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
  (directory listing; the process name is **[verify]**). Interface number
  `16001`, supported by two captured files (a TOC's `## Interface:` list and
  `engineSurveyPatch`) but not yet confirmed in game with
  `/dump (select(4, GetBuildInfo()))`. Line endings follow the file kind
  (`docs/LAB_FORMATS.md` amendments of 2026-09-22). Combat log, SavedVariables
  line endings, `WOW_PROJECT_ID` and the preferred TOC suffix: still open.

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
- **Fixtures:** `lab/core/tests/fixtures/wago/`, small tables only. Response
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
  once per five minutes. Known limits: a crash between publishing a table
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
| 2026-09-21 | wago.tools | First recording. The build parameter is `build=`; an unpublished build is a 404 HTML page, not a fall-back. All 13 product lists are sorted by version, descending, and a product code is reused across game versions: `wow_classic_beta` carries 1.13, 2.5, 3.4, 4.4, 5.5 and 1.60 builds, so its list opens with `5.5.0.x` and has the `1.60.1.x` builds (69876, 69893, 69913) further down; neither position nor the highest version means newest. One version string appears under several products (510 of them, each with a different `build_config` per product). The trailing build number is not unique: `10.0.0.46479` / `10.0.2.46479` under `wowlivetest` and `2.5.5.68575` / `2.5.6.68575` under `wow_anniversary` share a `build_config` | `lab/core/tests/fixtures/wago/` (M10-08) | `gamedata` never reads "latest" from the listing and keys on the full version string, never the trailing build number. `resolve_build` matches the exact version, preferring the flavor's own product. wago's table endpoint is keyed by version string alone, so a version listed only under another product still selects the same export and is accepted; from such a match only `.version` describes the installed flavor, `product` and the config hashes do not |
| 2026-09-22 | Local install (Forever beta) | First capture. The account's folder tree is not the retail `<Realm>/<Character>/`: a digits-only folder (almost certainly the realm's numeric id) holds `<First>-<Second>` character folders: Forever characters have a player-chosen first and second name (owner, 2026-09-22), and retail-style `<Realm>/<First>/` twins hold only AddOns.txt. The scrub tool treated the digits-only folder name as a realm name and replaced it inside ordinary numbers (chat colours, edit-mode offsets, an addon's map ids), and scrubbed `<First>-<Second>` only as a whole. Separately, a placeholder `--extra-name GUILD` rewrote the client's own `GUILD` chat-channel token | M10-03 stage-0 staging, never committed; four corrupted files dropped | Scrub-tool follow-up (#33): digits-only folder names get a path pseudonym only, `<First>-<Second>` folders are split into both names, and an `--extra-name` equal to a word the client writes is refused; stage 0 re-run after it merges |
