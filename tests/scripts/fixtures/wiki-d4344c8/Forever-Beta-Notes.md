# Forever Beta Notes

wowlab was built against real captures from **World of Warcraft: Forever** (the beta), on macOS. This page collects what those captures showed. The authoritative, dated record is [`docs/LAB_FORMATS.md`](https://github.com/sandgraal/wowlab/blob/main/docs/LAB_FORMATS.md), and real files always outrank documentation: when a capture contradicts the docs, the docs are amended.

> [!NOTE]
> The beta changes between builds. These are observations dated to the builds named, not promises. The beta's folder name will change at launch, which is why wowlab discovers it instead of assuming it.

## Layout

- The beta installs into its own flavor folder (`_classic_beta_` during the beta), with product code `wow_classic_beta`. wowlab discovers both; neither is hard-coded.
- The interface number for the beta's addons is `16001`. It is a compatibility label derived from the patch number, not an API level.
- **Characters have a first and a second name.** On disk a character is `WTF/Account/<ACCOUNT>/<digits>/<First>-<Second>/`, and the `<digits>` folder is the realm's numeric id. Beside it there is a retail-style twin, `<ACCOUNT>/<Realm>/<First>/`, that holds only `AddOns.txt`. The hyphen is a separator, not part of either name, and the second name is not a realm. Different addons spell the same character differently (with a space, with no realm), so don't match records to folders by realm.

## File formats

- **Line endings follow the file kind, not the platform.** In one macOS capture, `Config.wtf`, the `config-cache.wtf` files, `chat-cache.txt` and `layout-local.txt` were LF, while `bindings-cache.wtf`, the character `macros-cache.txt`, `AddOns.txt` and every SavedVariables file were CRLF.
- **SavedVariables:** CRLF, a leading blank line, no indentation, no `-- [n]` index comments. Keys are written in the client's table-iteration order, not the order an addon inserted them.
- **CVar names can contain a hyphen**, and values can contain raw control bytes, so a name is "any run of non-space bytes".
- **An empty `macros-cache.txt` is a zero-byte file** and must survive a round trip.
- **Floats are written with at most 16 significant digits**, which do not always read back as the same double. wowlab keeps every number's original text and never reformats a number it did not create.

## Behaviour of the client

- **The client's save moments are logout, `/reload` and a clean exit.** Everything an offline tool reads is from the *previous* session, and the client overwrites outside edits made while it runs.
- **Server sync.** A `synchronize*` CVar that is *absent* is not the same as *off*: the default is unknown. `wowlab doctor` says so instead of guessing.
- **The character sheet shows no item level.** The average item level API still returns values, but nothing on screen backs them.
- **Legacy talents are present below level 25**, with every node at rank 0. Class talents run on `C_Traits`. The old-style spec API globals are absent on Forever.
- **A client assertion is not a Lua error.** `pcall` cannot catch a C++ assertion inside the client. One call in the appearance-collection API crashed the client about four seconds after entering the world, which is why the [lab-addon](Lab-Addon-and-Characters) no longer asks for appearances and has an off switch per section.
- **The barber shop** opened and applied a change on build 1.60.1.70058. An earlier failure to open it matched a known Lua error in the barber-shop frame, which the client hides unless script errors are turned on.

## Crash reports

The client writes its crash reports into the flavor folder, as `Errors/<date>_<time>_Error_<pid>.txt` (seen on macOS). **They contain your character name, GUID, BattleTag and guild roster.** wowlab never captures them into its repository and neither should you: don't paste one into an issue or a chat.

## Combat log

The header names its own combat-log version (22), advanced logging (`ADVANCED_LOG_ENABLED,1`) and a project id for the beta. See [Combat Log](Combat-Log).
