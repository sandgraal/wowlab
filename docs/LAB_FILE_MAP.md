# Lab — What is in a WoW install

Reference for `wowlab_core.layout.classify()` and `wowlab explain`. One row
per path pattern: what it is, who writes it and when, whether editing it is
sensible, and which module reads it. `<flavor>` is a flavor folder
(`_retail_`, `_classic_`, the Forever folder, …) found by discovery, never a
constant (LAB_PLAN L6).

**Edit column:** `gate` = writable through `guard` only, with the client
closed. `no` = the Lab never writes it. `n/a` = not a file the Lab touches.

**Tier column** (ADR-0023): `A` reading it or writing it as intended is
ordinary addon-user behaviour. `B` works, unsupported by Blizzard, can be
reset by a patch. Nothing in tier `C` appears in this table because the Lab
does not do it.

Entries marked **[verify]** are confirmed or corrected by M10-03.

## Install root

| Path | What | Written by / when | Edit | Tier | Module |
|---|---|---|---|---|---|
| `.build.info` | Installed products, versions, build keys | Battle.net agent on install/patch | no | A (read) | `install` |
| `.product.db`, `.patch.result`, `Launcher.db` | Agent state | Battle.net agent | no | — | none |
| `Data/config/`, `Data/data/`, `Data/indices/` | CASC storage: build/CDN configs, archive blobs (`data.NNN`), index files (`*.idx`) | Agent; client streams into it | no | A (read, later wave) | none in Wave 1 |
| `World of Warcraft Launcher.exe` / `.app`, `Battle.net` helpers | Launchers | Agent | no | — | none |

Everything under `Data/` is shared by all flavors. Modifying it is out of
scope permanently (LAB_PLAN L7).

## Flavor folder `<flavor>/`

| Path | What | Written by / when | Edit | Tier | Module |
|---|---|---|---|---|---|
| `.flavor.info` | Product code of this flavor | Agent | no | A (read) | `install` |
| `Wow.exe`, `WowClassic.exe`, `World of Warcraft.app`, … | Client executable | Agent | no | — | `process` (name only) |
| `WTF/Config.wtf` | Machine-wide CVars: graphics, sound, locale, last account | Client on exit and on some setting changes | gate | A | `wtfconfig` |
| `WTF/Account/<ACCOUNT>/` | One folder per Battle.net account that logged in on this machine. Folder name identifies the account; scrub in fixtures | Client | — | — | `layout` |
| `WTF/Account/<ACCOUNT>/SavedVariables.lua` | Blizzard UI's own account-wide saved state | Client on logout / `/reload` | gate | A | `luadata` |
| `WTF/Account/<ACCOUNT>/SavedVariables/<Addon>.lua` | Account-wide addon data (`## SavedVariables:`) | Client on logout / `/reload` | gate | A | `luadata` |
| `…/SavedVariables/<Addon>.lua.bak` | Previous write of the same file | Client | no | A (read) | `luadata` |
| `WTF/Account/<ACCOUNT>/config-cache.wtf` | Account-scoped CVars | Client | gate | A | `wtfconfig` |
| `WTF/Account/<ACCOUNT>/bindings-cache.wtf` | Account keybinds | Client | gate | A | `wtfconfig` |
| `WTF/Account/<ACCOUNT>/macros-cache.txt` | Account macros | Client | gate | A | `wtfconfig` |
| `WTF/Account/<ACCOUNT>/edit-mode-cache-account.txt` | Edit Mode HUD layouts: one line of space-separated tokens ending in a NUL byte; layout names are typed by the owner and length-prefixed; server may replace (see below) | Client | gate | A | none in Wave 1 (kept by `snapshot`) |
| `WTF/Account/<ACCOUNT>/chat-frontend-cache.txt` | Unknown; 0 bytes on the Forever beta (2026-09-22) **[verify]** | Client | gate | A | none (kept by `snapshot`; empty kept as empty) |
| `WTF/Account/<ACCOUNT>/flagged-cache-account.txt`, `…/<Character>/flagged-cache-character.txt` | Unknown; on the Forever beta exactly `2` then a NUL **[verify purpose]** | Client | gate | A | none (kept by `snapshot`) |
| `WTF/Account/<ACCOUNT>/tts-cache-account.txt`, `…/<Character>/tts-cache-character.txt` | Text-to-speech settings **[verify]**; mixed LF and CRLF within the file | Client | gate (server may replace) | A | none (kept by `snapshot`) |
| `WTF/Account/<ACCOUNT>/character-list-order.txt` | Character-select order **[verify]**; not captured (its lines refused by the scrub tool) | Client | gate | A | none (kept by `snapshot`) |
| `…/<Character>/edit-mode-cache-character.txt` | Character Edit Mode layouts, same encoding as the account file | Client | gate (server may replace) | A | none (kept by `snapshot`) |
| `…/<Character>/click-bindings-cache.txt` | Click Casting bindings on unit frames; LF, ends `END` | Client | gate (server may replace) | A | none (kept by `snapshot`) |
| `WTF/Account/<ACCOUNT>/edit-mode-cache-account.old` | Previous write of the same file | Client | no | A (read) | none (kept by `snapshot`) |
| `WTF/Account/<ACCOUNT>/<Realm>/<Character>/SavedVariables/<Addon>.lua` | Per-character addon data (`## SavedVariablesPerCharacter:`) | Client on logout / `/reload` | gate | A | `luadata` |
| `…/<Character>/config-cache.wtf`, `bindings-cache.wtf`, `macros-cache.txt` | Character-scoped CVars, binds, macros. `bindings-cache.wtf` exists only with character-specific key bindings on **[verify]**; the Forever capture had none | Client | gate | A | `wtfconfig` |
| `…/<Character>/AddOns.txt` | Which addons are enabled for this character | Client | gate | A | `layout` (lines) |
| `…/<Character>/layout-local.txt` | Legacy UI panel positions; on the Forever beta (2026-09-22) a stub, `Version: 1` and nothing else | Client | gate | A | none (kept by `snapshot`) |
| `…/<Character>/chat-cache.txt` | Chat window and channel configuration | Client | gate | A | none (kept by `snapshot`) |
| `Interface/AddOns/<Addon>/` | Third-party addon: `.toc`, `.lua`, `.xml`, media | You, or an addon manager | gate | A | `layout`, `toc` |
| `Interface/AddOns/Blizzard_*` | Present only after `ExportInterfaceFiles`; not loaded from disk by modern clients | Console export | no | A (read) | `layout` (flagged) |
| `BlizzardInterfaceCode/`, `BlizzardInterfaceArt/` | Output of the `ExportInterfaceFiles code` / `art` console commands: Blizzard's Lua/XML and UI textures, for reading | Client on command | no | A (read) | `layout` (flagged) |
| `Interface/<anything outside AddOns>` | Loose-file overrides of base-game UI textures. Works only for textures that exist in the base game; sound overrides and world/model overrides no longer work in modern clients | You | gate | B | `layout` (flagged) |
| `Fonts/` | Font overrides: `FRIZQT__.TTF` (main UI), `ARIALN.TTF` (chat, numbers), `skurri.ttf` (combat text), `MORPHEUS.ttf` (headers), plus locale variants | You | gate | B | `layout` |
| `Cache/ADB/<locale>/DBCache.bin` | Hotfixes the server pushed over the shipped DB2 tables | Client | no | A (read, later wave) | none in Wave 1 |
| `Cache/WDB/<locale>/*.wdb` | Cached server responses for creatures, items, quests, … | Client | no | A (read, later wave) | none |
| `Logs/WoWCombatLog*.txt` | Combat log while `/combatlog` is on | Client, batched | no | A (read) | `combatlog` |
| `Logs/*.log` (`Client.log`, `gx.log`, `Sound.log`, `FrameXML.log`, `taint.log` when enabled) | Diagnostics | Client | no | A (read) | none |
| `Screenshots/` | `WoWScrnShot_MMDDYY_HHMMSS.jpg` (or `.tga`/`.png` per CVar) | Client on Print Screen | n/a | A | `layout` |
| `Errors/` | Crash dumps and reports | Client | no | — | none |
| `Utils/`, `*.dll`, `*.dylib` | Client support binaries | Agent | no | — | none |

## Timing rules every tool must respect

- SavedVariables are written on logout, on `/reload`, and on clean exit. A
  crash writes nothing. While the client runs, the files on disk describe
  the previous session.
- The client reads SavedVariables, `Config.wtf`, binds and macros at login or
  `/reload` and overwrites them on the next write. Editing them while the
  client runs is lost work; that is one reason `guard` refuses.
- On Windows the client can hold files open; a read can fail with a sharing
  violation. Readers retry briefly and then report, they do not loop.
- Macros and binds may be synced server-side depending on the
  `synchronizeBindings` / `synchronizeMacros` / `synchronizeConfig` CVars; a
  local edit can be overwritten from the server at login when sync is on.
  `wowlab doctor` reports these CVars. The Forever beta capture (2026-09-22)
  writes none of them: absent means the client default, reported as "not set
  (client default)", never "off"; the default itself is **[verify]**. The
  server may replace any `*-cache*` file at login (edit-mode layouts and
  click bindings are believed to follow the account on retail); which ones,
  and under which setting, is **[verify]**, so every `*-cache*` row below
  carries that caveat.
- **Folder shape under `WTF/Account/<ACCOUNT>/` on the Forever beta
  (2026-09-22, observed; the name rule confirmed by the owner):**
  `<ACCOUNT>/<digits>/<First>-<Second>/`. Forever characters have a first
  and a second name, both chosen by the player, and the folder joins them
  with a hyphen (character names cannot contain one, **[verify]**). The digits folder is
  almost certainly the numeric id of the realm: every character in it also
  has a retail-style twin `<ACCOUNT>/<Realm>/<First>/` under one realm
  display name, holding only `AddOns.txt` (**[verify]** the id). Everything
  else per character — config, bindings, click bindings, macros, chat,
  edit-mode, flagged and text-to-speech caches, `SavedVariables/` — lives in
  the `<First>-<Second>` folder (owner's directory listing). `layout` must
  handle both shapes and must not read the second name as a realm. The same
  first name recurs with different second names in the owner's listing.
- A patch can reset `Interface/` overrides' effect and can change any format
  here. `snapshot` manifests record the flavor version so a diff across a
  patch says so.

## Useful console and slash commands (run by the owner, not by the Lab)

| Command | Where | Effect |
|---|---|---|
| `ExportInterfaceFiles code` / `art` | Login-screen console (launch with `-console`, press `` ` ``) | Dumps Blizzard UI source / art next to the flavor folder |
| `/reload` | In game | Flushes SavedVariables, reloads the UI |
| `/combatlog` | In game | Toggles combat logging |
| `/console <cvar> <value>` | In game | Sets a CVar |
| `/api` , `/fstack`, `/etrace`, `/tinspect` | In game | API browser, frame stack, event trace, table inspector |
| `/dump <expr>` | In game | Prints a Lua value |
