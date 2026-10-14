# Safety and Guarantees

These are hard rules in the code, checked by tests on every change. A violation is a bug even if every test passes.

## The rules, in plain words

| # | Rule | What it means for you |
|---|---|---|
| L1 | **Reads never write.** | Looking at your install changes nothing: no temp files, caches or lock files inside it. Caches and the snapshot store live in wowlab's own data folder. |
| L2 | **One write gate.** | Every byte written into an install goes through one module. The client must not be running, the path must be inside an allowed area, a snapshot is taken first, the operation is journaled, and the replace is atomic. There is no "force" that skips the snapshot. |
| L3 | **No Lua is executed.** | SavedVariables and every other Lua-syntax file are parsed as *data* by a strict literal parser. Functions, metatables, calls and operators are rejected with a position. |
| L4 | **Lossless.** | Parsers keep what they do not understand: key order, key style, the original text of every number, every unclassifiable line. An unmodified file comes back byte for byte. *One owner-granted exception:* a combat-log line over 1 MiB keeps only its first 1 MiB, flagged as truncated with its true length, so a crafted file cannot exhaust memory. |
| L5 | **Game data is keyed by build and never overwritten.** | A cached table for one game version is never replaced by another's. |
| L6 | **Nothing is hard-coded about a flavor.** | No `_retail_`, no product code, no interface number, no build number in library code. They are discovered from your install. |
| L7 | **Permanently out of scope.** | See below. |
| L8 | **Real fixtures.** | Every parser of an external format is graded against real captures, scrubbed of names and identifiers. |

## What the write gate does

Every change wowlab makes to your install (`snap restore`, `undo`, `profile apply`, `sv merge`, `addon install`, `addon remove`) follows the same path:

1. **Plan.** It works out exactly which files would be written or deleted and shows you.
2. **Ask.** You confirm (or you passed `--yes`). `--dry-run` stops here.
3. **Check the game is closed.** If the client is running, the change is refused (exit code 3).
4. **Check the path is allowed.** Only files under the client's own configurable areas may be written. Anything the file map marks as "leave alone" (client-written backups, `Blizzard_*` addon folders, file-browser metadata) is skipped unless you name it explicitly.
5. **Snapshot first.** Your current state is saved, so the change can be undone.
6. **Journal.** The operation is recorded before it happens.
7. **Atomic replace.** Each file is swapped in whole; you never end up with half a file.

`wowlab undo` reverses the most recent change from the snapshot taken in step 5.

## What wowlab will never do

Decided once and closed ([ADR-0023](https://github.com/sandgraal/wowlab/blob/main/docs/DECISIONS.md)). In this repository, permanently:

- read or write the game's process memory
- inject a DLL or dylib
- capture or modify network packets
- automate input (clicks, keypresses)
- edit anything under the game's `Data/` folder, or any executable
- bypass the client's integrity checks

wowlab only changes what the game itself treats as your settings and your saved data: config files, key bindings, macros, addon files and their saved variables. It is not affiliated with or endorsed by Blizzard, and this page cannot speak for Blizzard's policies.

## Privacy

- **The lab-addon never records names.** No character name, realm, GUID, guild, chat, text you typed, or Battle.net identity, and no wall-clock time. Every unit is recorded as `"player"`. A crafted item's link can embed the crafter's player id; wowlab blanks that before storing.
- **Test fixtures are scrubbed.** Real captures are used to test the parsers, but only after a byte-level scrub tool replaces account folders, character names, GUIDs and timestamps. The repository is public, so an unscrubbed capture would be a leak, and the process is built around not leaking one.
- **Client crash reports are never captured.** The Forever client writes them into the flavor folder's `Errors/` folder, and they contain the character name, GUID, BattleTag and guild roster. Do not paste one into an issue or a chat.
- **Nothing is uploaded.** There is no telemetry and no account.

## Why a change made while the game is running is refused

SavedVariables and many settings files are written by the client when you log out, `/reload` or exit cleanly, and the client overwrites whatever is on disk at that moment. An offline tool therefore always sees the *previous* session, and an edit made while the game runs would be silently lost, or worse, half-applied. The gate refuses instead of letting that happen.

## When something goes wrong

- `wowlab undo` puts back the last change.
- `wowlab snap list` and `wowlab snap show ID` show what you can go back to; `wowlab snap restore ID --dry-run` shows what a restore would do.
- `wowlab snap verify` re-hashes every stored object and checks every manifest, so you can trust the store.
- A hostile or corrupt file is refused with a reason and a position, not guessed at.
