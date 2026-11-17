# Release record: v0.3.0

Written by the conductor on 2026-11-17. v0.3.0 was tagged at e84bbf9 and published that day (GitHub release with the wheel, the sdist, `SHA256SUMS` and both apps; PyPI `wowlab` 0.3.0). The owner approved both `release` deployments.

## L1 sweep (M16-38)

Run by the conductor on the owner's instruction (2026-11-17, "Yes, run it"; "wow is closed"), on the owner's Mac, against the install, with the game closed and the Battle.net launcher open. The test was the one in PR #581 at 398f523, run after the release, so it covers v0.3.0 as installed from `main` at that time. It passed twice (81.6 s, then 77.6 s). The block below is the second run's, pasted as the checklist says: placeholders stand for the account folder, the characters, the install root, the home folder and the login.

```
--- L1 sweep, for the release record ---
wowlab 0.3.0; flavors: _classic_beta_ None, _forever_ 1.60.1.70959
install: 20526 paths, 17768 files hashed; unchanged
user data directory: unchanged
runs: 84, flagged: 0
exit   0  wowlab doctor --json
exit   0  wowlab install show --json
exit   0  wowlab filemap gaps --json
exit   0  wowlab tree --install --json
exit   0  wowlab char list --json
exit   0  wowlab char list --summary --json
exit   0  wowlab char kept --json
exit   0  wowlab char kept --flavor _classic_beta_ --account account#2 --character character#3 --json
exit   0  wowlab profile list --json
exit   0  wowlab snap list --json
exit   0  wowlab snap show latest --json
exit   0  wowlab snap diff --json -- ~1 latest
exit   0  wowlab snap verify --json
exit   0  wowlab names fetch --json
exit   0  wowlab explain --json -- <root>/.build.info
exit   0  wowlab explain --json -- <root>/Data
exit   0  wowlab tree --flavor _classic_beta_ --json
exit   0  wowlab tree --explain --flavor _classic_beta_ --json
exit   0  wowlab explain --flavor _classic_beta_ --verbose --json -- <root>/_classic_beta_/WTF/Config.wtf
exit   1  wowlab explain --flavor _classic_beta_ --verbose --json -- <root>/_classic_beta_/WTF/Account
exit   0  wowlab explain --flavor _classic_beta_ --verbose --json -- <root>/_classic_beta_/Interface/AddOns
exit   0  wowlab explain --flavor _classic_beta_ --verbose --json -- <root>/_classic_beta_/Logs
exit   0  wowlab cvar list --flavor _classic_beta_ --json
exit   0  wowlab addons list --flavor _classic_beta_ --json
exit   0  wowlab addons check --flavor _classic_beta_ --json
exit   0  wowlab char kept --flavor _classic_beta_ --json
exit   0  wowlab log tail -n 20 --flavor _classic_beta_ --json
exit   0  wowlab char list --flavor _classic_beta_ --brief --json
exit   0  wowlab char list --summary --flavor _classic_beta_ --json
exit   0  wowlab tree --flavor _forever_ --json
exit   0  wowlab tree --explain --flavor _forever_ --json
exit   0  wowlab explain --flavor _forever_ --verbose --json -- <root>/_forever_/WTF/Config.wtf
exit   0  wowlab explain --flavor _forever_ --verbose --json -- <root>/_forever_/WTF/Account
exit   0  wowlab explain --flavor _forever_ --verbose --json -- <root>/_forever_/Interface/AddOns
exit   0  wowlab explain --flavor _forever_ --verbose --json -- <root>/_forever_/Logs
exit   0  wowlab cvar list --flavor _forever_ --json
exit   0  wowlab cvar get --flavor _forever_ --json -- RAIDRenderFormat
exit   0  wowlab db2 head ChrClasses -n 3 --flavor _forever_ --json
exit   0  wowlab addons list --flavor _forever_ --json
exit   0  wowlab addons check --flavor _forever_ --json
exit   0  wowlab char kept --flavor _forever_ --json
exit   0  wowlab cvar list --scope account --flavor _forever_ --account account#1 --json
exit   0  wowlab binds list --flavor _forever_ --account account#1 --json
exit   0  wowlab macros list --flavor _forever_ --account account#1 --json
exit   0  wowlab macros check --flavor _forever_ --account account#1 --json
exit   0  wowlab sv list --flavor _forever_ --account account#1 --json
exit   0  wowlab sv list --orphans --flavor _forever_ --account account#1 --json
exit   0  wowlab sv list --unreadable --flavor _forever_ --account account#1 --json
exit   0  wowlab sv dump --install-only --flavor _forever_ --account account#1 --json -- <root>/_forever_/WTF/Account/account#1/SavedVariables/Blizzard_SettingsDefinitions_Shared.lua
exit   0  wowlab char list --flavor _forever_ --brief --json
exit   0  wowlab char list --summary --flavor _forever_ --json
exit   0  wowlab cvar list --scope character --flavor _forever_ --account account#1 --character character#1 --json
exit   0  wowlab binds list --flavor _forever_ --account account#1 --character character#1 --json
exit   0  wowlab macros list --flavor _forever_ --account account#1 --character character#1 --json
exit   0  wowlab macros check --flavor _forever_ --account account#1 --character character#1 --json
exit   0  wowlab sv list --flavor _forever_ --account account#1 --character character#1 --json
exit   0  wowlab char show --flavor _forever_ --account account#1 --character character#1 --json
exit   0  wowlab char gear --flavor _forever_ --account account#1 --character character#1 --json
exit   0  wowlab char binds --flavor _forever_ --account account#1 --character character#1 --json
exit   0  wowlab char macros --flavor _forever_ --account account#1 --character character#1 --json
exit   0  wowlab char history --flavor _forever_ --account account#1 --character character#1 --json
exit   0  ui GET /?boot= (HTTP 200)
exit   0  ui GET / (HTTP 200)
exit   0  ui GET /api/commands (HTTP 200)
exit   0  ui GET /api/looks-page (HTTP 200)
exit   0  ui POST /api/run doctor
exit   0  ui POST /api/run install show
exit   0  ui POST /api/run filemap gaps
exit   0  ui POST /api/run tree {'whole_install': True}
exit   0  ui POST /api/run snap list
exit   0  ui POST /api/run char list {'brief': True}
exit   0  ui POST /api/run char kept
exit   0  ui POST /api/run tree {'flavor': '_classic_beta_'}
exit   0  ui POST /api/run addons list {'flavor': '_classic_beta_'}
exit   0  ui POST /api/run addons check {'flavor': '_classic_beta_'}
exit   0  ui POST /api/run cvar list {'flavor': '_classic_beta_'}
exit   0  ui POST /api/run sv list {'flavor': '_classic_beta_'}
exit   0  ui POST /api/run char list {'flavor': '_classic_beta_', 'summary': True}
exit   0  ui POST /api/run tree {'flavor': '_forever_'}
exit   0  ui POST /api/run addons list {'flavor': '_forever_'}
exit   0  ui POST /api/run addons check {'flavor': '_forever_'}
exit   0  ui POST /api/run cvar list {'flavor': '_forever_'}
exit   0  ui POST /api/run sv list {'flavor': '_forever_'}
exit   0  ui POST /api/run char list {'flavor': '_forever_', 'summary': True}
skipped  profile show: there is no profile
skipped  cvar get: _classic_beta_ sets no CVar in Config.wtf
skipped  looks races: _classic_beta_'s customization tables are not all cached, so it would go online
skipped  looks options: _classic_beta_'s customization tables are not all cached, so it would go online
skipped  looks show: _classic_beta_'s customization tables are not all cached, so it would go online
skipped  looks compare: _classic_beta_'s customization tables are not all cached, so it would go online
skipped  db2 head: ChrClasses is not cached for _classic_beta_, so it would go online
skipped  account commands: _classic_beta_ has no account folder
skipped  character commands: _classic_beta_ has no character folder
skipped  looks races: _forever_'s customization tables are not all cached, so it would go online
skipped  looks options: _forever_'s customization tables are not all cached, so it would go online
skipped  looks show: _forever_'s customization tables are not all cached, so it would go online
skipped  looks compare: _forever_'s customization tables are not all cached, so it would go online
skipped  log tail: _forever_ has no combat log
skipped  binds coach: _forever_ has no combat log
excluded undo: writes into the install
excluded sv merge: writes into the install
excluded snap restore: writes into the install
excluded profile apply: writes into the install
excluded addon install: writes into the install
excluded addon remove: writes into the install
excluded snap create: writes into wowlab's store
excluded snap gc: writes into wowlab's store
excluded snap import: writes into wowlab's store
excluded profile save: writes into wowlab's store
excluded profile delete: writes into wowlab's store
excluded looks save: writes into wowlab's user data directory
excluded looks import-char: writes into wowlab's user data directory
excluded looks page: writes a page into wowlab's user data directory
excluded snap export: writes a file outside the install
excluded db2 fetch: downloads a table into wowlab's cache
excluded db2 builds: asks wago.tools for its list of versions
excluded ui: its server is started in this process instead, and its read requests made
--- end ---
```

Result: `1 passed, 27 deselected in 77.56s`.
