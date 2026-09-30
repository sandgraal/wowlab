# wowlab

**Your World of Warcraft install, understood.**

wowlab is a toolchain that runs on your own machine. It reads a World of Warcraft install directly, tells you what every file in it is and whether it is safe to touch, snapshots your setup, and lets you change the client's configurable state and change it back.

It is a Python library and a command-line program. It was built against a real install of **World of Warcraft: Forever** (the beta), but flavor folders, product codes and build numbers are *discovered* from your install, never assumed.

> [!IMPORTANT]
> **Local only.** wowlab has no accounts, no server and no uploads. The only network access is downloading public game tables from [wago.tools](https://wago.tools), and you can skip that with `--offline`. Everything it saves lives in your user data folder, never inside your game install.

## What you can do today

| I want to… | Use | Page |
|---|---|---|
| Find out what a file in my install is, who writes it and whether it is safe to edit | `wowlab explain` · `wowlab tree --explain` | [Command Reference](Command-Reference#inspect) |
| Check my install, flavors, versions and whether the game is running | `wowlab doctor` | [Getting Started](Getting-Started) |
| Save my whole setup, compare two saves, and put one back | `wowlab snap …` · `wowlab undo` | [Snapshots and Undo](Snapshots-and-Undo) |
| Switch between named UI setups (raid, clean, gamepad…) | `wowlab profile …` | [Profiles](Profiles) |
| Read addon settings, CVars, keybinds and macros as plain text | `wowlab sv dump` · `cvar` · `binds` · `macros` · `addons` | [SavedVariables and Merging](SavedVariables-and-Merging) |
| Copy one addon's settings between characters without clobbering the rest | `wowlab sv merge` | [SavedVariables and Merging](SavedVariables-and-Merging) |
| See what my character looks like to the addon: gear, talents, currencies, professions | `wowlab addon install lab`, then `wowlab char show` | [Lab-Addon and Characters](Lab-Addon-and-Characters) |
| Browse every race's customization options and keep a library of looks | `wowlab looks …` | [Customization Looks](Customization-Looks) |
| Look up any game table for my exact build | `wowlab db2 …` | [Game Data](Game-Data) |
| Read or follow the combat log | `wowlab log tail --follow` | [Combat Log](Combat-Log) |

New here? Start with **[Getting Started](Getting-Started)**, then read **[Safety and Guarantees](Safety-and-Guarantees)** before you let it change anything.

## The safety promises, in one breath

- **Reading never writes.** Looking at your install changes nothing.
- **One door for every change.** Anything wowlab writes into your install goes through a single *write gate*: the game must be closed, a snapshot is taken first, the change is recorded, and files are replaced atomically. There is no "force" that skips this.
- **No Lua is ever run.** Saved addon data is read as data by a strict parser.
- **Nothing is lost.** Parsers keep every byte they do not understand.
- **Permanently out of bounds:** memory reading, injection, packet tampering, input automation, and touching the game's `Data/` folder or executables.

Details: **[Safety and Guarantees](Safety-and-Guarantees)**.

## Where the project stands

wowlab grows in **waves**, and the owner picks each one after reviewing the last.

- **Wave 1** is done: explain, doctor, tree, snapshots, undo, SavedVariables / CVar / bindings / macros readers, game tables from wago.tools, the combat-log reader.
- **Wave 2** is done: the lab-addon and `char show`, the customization sandbox, profiles, and `sv merge`.
- **Wave 3** has not been picked yet.

See the **[Roadmap](Roadmap)**.

## More

- [Command Reference](Command-Reference) — every command and what it may change
- [Forever Beta Notes](Forever-Beta-Notes) — what real captures showed about the client
- [Glossary](Glossary) — WoW terms that are easy to get wrong
- [FAQ](FAQ)
- The repository: [sandgraal/wowlab](https://github.com/sandgraal/wowlab), with the spec ([`docs/LAB_PLAN.md`](https://github.com/sandgraal/wowlab/blob/main/docs/LAB_PLAN.md)), the file map ([`docs/LAB_FILE_MAP.md`](https://github.com/sandgraal/wowlab/blob/main/docs/LAB_FILE_MAP.md)) and the format reference ([`docs/LAB_FORMATS.md`](https://github.com/sandgraal/wowlab/blob/main/docs/LAB_FORMATS.md))

---

*World of Warcraft and Blizzard Entertainment are trademarks of Blizzard Entertainment, Inc. wowlab is an independent personal project, not affiliated with or endorsed by Blizzard. No game assets or Blizzard code are distributed with it.*
