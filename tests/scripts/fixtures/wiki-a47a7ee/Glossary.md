# Glossary

Several terms about the client are named misleadingly and will be modelled wrong by anyone reasoning from the names. These are the ones that matter most for wowlab. The full list is [`docs/GLOSSARY.md`](https://github.com/sandgraal/wowlab/blob/main/docs/GLOSSARY.md).

| Term | What it actually is |
|---|---|
| **Install** | The Battle.net folder that holds `.build.info`. One install can hold several *products* (retail, Classic, a beta), each in its own flavor folder, all sharing one `Data/` store. |
| **Flavor folder** | A directory name inside the install (`_retail_`, `_classic_beta_`…). It is a name that can change between beta and launch, **not an identifier**. wowlab discovers it and never hard-codes it. |
| **Product code** | What Battle.net and data sites key on (`wow`, `wow_classic`…). The flavor folder is what is on disk; neither is stable across a beta-to-launch move. |
| **Build** | The last component of a version (`1.60.1.70058` → `70058`). Game tables are valid for exactly one *full* version string, so wowlab never keys anything by the build number alone. |
| **Patch** | The first three components of the version. |
| **Interface version** | The integer an addon's manifest declares to say which client it supports. It is a compatibility label derived from the **patch** number, not an API level. A mismatch marks the addon "out of date"; it does not change what API exists. |
| **TOC** | An addon's manifest (`Foo.toc`): metadata and the ordered list of files to load. |
| **SavedVariables** | A Lua file per addon where it persists data. Written on logout or `/reload`, read on login or `/reload`. An offline tool always sees the *previous* session, and the client overwrites outside edits made while it runs. |
| **WTF folder** | Where configuration and SavedVariables live, one per flavor. |
| **CVar** | A client console variable: a named setting saved in `Config.wtf` or a `config-cache.wtf`. Scope is machine, account or character. |
| **Account folder** | `WTF/Account/<name>/`. It is personal data, so anything committed to the project has it replaced. |
| **Second name (Forever)** | Forever characters have a player-chosen first *and* second name. The second name is not a realm. See [Forever Beta Notes](Forever-Beta-Notes). |
| **GUID** | The client's string id for a unit. Only its NPC or object id is game data; the other fields record where and when that copy of the unit spawned, so they are scrubbed from any committed log. |
| **Secret value** | A number addon code may display but not compute with. It is **not** encrypted. |
| **Spec vs loadout** | A character's spec changes between activities and is not stable state. A *loadout* is one complete set of talent selections; a character has many and switches between them, so a loadout is a property of a moment, not of the character. |
| **`C_Traits`** | The modern client API for talent trees (configs, trees, nodes, entries, ranks). Forever uses it too. |
| **Item level / bonus IDs** | An item's power depends on its bonus ids, not just its item id. The same base item with different bonus ids is a materially different item, and item level is read from the client, never derived. |
| **Combat log** | The text file the client appends to under `Logs/` while `/combatlog` is on. |
| **DB2** | The game's database-table format. wowlab reads tables for your exact version from wago.tools. |
| **Write gate** | wowlab's single path for changing your install. See [Safety and Guarantees](Safety-and-Guarantees). |
| **Snapshot** | An immutable, content-addressed copy of your `WTF/`, `Interface/` and `Fonts/` state. See [Snapshots and Undo](Snapshots-and-Undo). |
| **Wave** | A group of tickets the owner picks to build next. See the [Roadmap](Roadmap). |
