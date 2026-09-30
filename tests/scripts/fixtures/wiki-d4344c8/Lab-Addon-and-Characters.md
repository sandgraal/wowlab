# Lab-Addon and Characters

wowlab can only see files on disk, and a character's gear, talents and customization are not stored in any file the client writes for you. So wowlab ships a small **in-game addon** that records them into its own SavedVariables file, which `wowlab char show` then reads offline.

This is the one place Lua runs, and it runs **inside the game client**, never in wowlab ([ADR-0026](https://github.com/sandgraal/wowlab/blob/main/docs/DECISIONS.md)). wowlab itself never loads, runs or evaluates these files. They are checked by a static linter, by Python tests that read their tokens, and by review.

## Install and remove

```bash
uv run wowlab addon install lab --dry-run   # see the plan
uv run wowlab addon install lab             # copy it into Interface/AddOns/WowLab/ (game closed)
uv run wowlab addon remove lab              # delete its files; your recorded data stays
```

Both go through the [write gate](Safety-and-Guarantees), so `wowlab undo` reverses either.

- The addon's `## Interface:` number is filled in from your client's discovered version, so the repository never holds a hard-coded number. **After a client update that changes the first three parts of the version, run `addon install lab` again.**
- Over an existing copy, only changed files are written and files not in the sources are removed.
- `addon remove` never touches `WowLab.lua`: those are your captures.

## What it records

For the character you log in on, the addon writes one table (`WowLabCharDB`, per character) when the client saves: at **logout, `/reload` or a clean exit**. A crash writes nothing.

| Section | What is recorded |
|---|---|
| `client` | Version, build and interface number (not its date) |
| `spec` | Specialization index and id |
| `gear` | Each equipped slot's item link (with the crafter's id blanked) and item level; the average item levels |
| `talents.class` | The active class-talent config: trees, nodes, ranks, points, and the loadout export string |
| `talents.legacy` | Forever's Legacy talent trees, when the client lists them |
| `customization` | Your barber-shop choices as of your last visit (kept across sessions until the next one) |
| `collections` | Which mounts, toys and battle pets you have collected (and the journal filters in effect when it looked) |
| `currencies` | Your currency list with quantities, caps and weekly progress |
| `professions` | Profession skill lines with rank and maximum |

**Never recorded:** any name (character, realm, guild), GUID, chat, text you typed, Battle.net identity, or wall-clock time. Every unit is recorded as `"player"`.

**Honest gaps.** Some sections can be `absent`, always with a reason, and the reason is what `char show` prints:

- `collections.appearances` is **always absent**. Asking the Forever client for the appearance collection crashed it once, and a Lua `pcall` cannot catch a C++ assertion in the client, so the addon no longer calls anything in `C_TransmogCollection`.
- A section the client cannot provide is `absent` with the reason, never silently empty.
- The API a section uses stays marked **[verify]** in the project's docs until a real capture shows it ran.

## In-game commands

| Command | What it does |
|---|---|
| `/wowlab save` | Refreshes the recorded tables **in memory**. They are written to disk at the next logout, `/reload` or clean exit. |
| `/wowlab skip <section>` | Switches one section off at once. `/reload` or log out to save the switch. |
| `/wowlab unskip <section>` | Switches it back on from the next `/reload` or login. |
| `/wowlab skip` | Lists the sections switched off, then every valid section key. |

### The first-pass window

Because one unverified client call once took the game down, the addon tells you before it records: on login or `/reload` it waits **15 seconds** after entering the world and says so in chat (`WowLab: recording in 15 s. To switch a section off first: /wowlab skip <section>`). If a section ever crashes your client, log in again, type `/wowlab skip <section>` inside that window, and the crashing section is left out. Switching a section off is per character.

## Reading it back

```bash
uv run wowlab char show                    # the character whose WowLab.lua was written last
uv run wowlab char show --character NAME   # or pick one
uv run wowlab char show --json
```

`char show` prints what the addon recorded and, for every missing value, why it is missing. It reads the character's `WowLab.lua` only.

Because SavedVariables are written at logout, **`char show` always shows the state at the end of your last session**, not the live character.

Related: `wowlab looks import-char` turns the recorded barber-shop choices into a saved look ([Customization Looks](Customization-Looks)), and `wowlab sv merge` uses the addon's load counter as a safety check ([SavedVariables and Merging](SavedVariables-and-Merging#the-loader-check)).

## What real captures showed on Forever

- Talents, spec and professions record correctly. Class talents run on `C_Traits`.
- The Legacy talent trees are present even below level 25, with every node at rank 0.
- The character sheet shows no item level, so the recorded average item level has no on-screen number to compare against.
- The addon's own load counter rises across logins, which proves that the SavedVariables round-trip works on Forever.

More in [Forever Beta Notes](Forever-Beta-Notes).
