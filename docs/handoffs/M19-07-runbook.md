# The crew book in game: the owner's check (M19-07)

Written by the conductor on 2026-11-19, from `docs/LAB_PLAN.md` §20.6 as it
stood after the addon merged (#634). It takes one play session with a
group. Nothing here writes to your install except the addon install in
step 1, which goes through the write gate and can be undone.

**What to send back.** Shapes and counts, never a name. Where a step asks
"what did the key look like", answer in the form `<Name>-<Realm>` or
`<Name>-<Realm with a space>`. No capture is committed; the conductor
writes your answers into §20.1 and §20.6 as dated notes.

## Before you start

1. Close the game. Install the addon from `main`:

   ```bash
   uv run wowlab addon install lab
   ```

2. Start the game and log in.

## In game

Each step says what it settles.

3. **Turn it on.** Type `/wowlab crew on`. Read the notice.
   - *Report:* did the notice appear, and was any line a Lua error?
   - Type `/wowlab crew`. *Report:* does it say on, and does it list
     anything as missing or `absent`? Copy the reasons (they hold no names).

4. **Exit the game fully, then start it again.** While it is closed, run
   these two at the desk:

   ```bash
   uv run wowlab crew list
   ```

   ```bash
   uv run wowlab snap create
   ```

   - *Report:* did `crew list` say the book is on (with no players yet)?
     That settles whether the book survives a logout at all.
   - The snapshot is your way back if a later login loses the book: nothing
     else takes one for you.
   - After logging back in, *report:* did a "crew book is on" line appear?
     (It waits up to 15 seconds.)

5. **Join a group in the open world**, with a player from your own realm
   and, if one is to hand, one from another realm. Wait a few seconds out
   of combat, then type `/wowlab crew who`.
   - *Report:* how many players it lists against how many are in the
     group.
   - *Report the key shapes:* for the same-realm player, does the realm
     part match your realm's name with or without spaces? For the
     cross-realm player, does the realm part have spaces or not?

6. **Rate one and note another.** Target a group member and type
   `/wowlab crew good`. Target another and type
   `/wowlab crew note solid healer`.
   - *Report:* the reply to each (no names needed: "rated good" or the
     refusal).
   - If a realm has a space in its name, try typing the name after the
     command (`/wowlab crew bad Name-Realm`). *Report:* did it work, or
     did it say the name was not found?

7. **`/reload` while still grouped.** Then `/wowlab crew who` again.
   - *Report:* did anyone's count go up? (It should not.)

8. **Fight something with the group**, and if someone joins or leaves
   during the fight, note it. After the fight, out of combat,
   `/wowlab crew who`.
   - *Report:* was a player who joined during the fight listed afterwards?
     (Nothing is read in combat; the addon catches up when the fight
     ends. The count of skipped reads is not shown in game: step 14 gives
     it once, at the desk.)

9. **Enter an instance with the group** (a dungeon is enough). Out of
   combat inside, `/wowlab crew who`.
   - *Report:* is everyone in the group listed, or is someone missing
     who joined inside the instance? This is the secret-value question: if
     names cannot be read in an instance, nobody new is listed there and
     step 14's skipped count is above zero.

10. **See the warning.** Type `/chatlog` to start the chat log. Leave the
    group and rejoin it, or have the rated player leave and rejoin.
    - *Report:* did one line appear for the player you rated or noted?
      Did it show the rating, the note, and "last seen in" a guild if they
      have one?

11. **If you get the chance: a battleground.** Queue for one with the book
    on. Inside, out of combat, `/wowlab crew who`.
    - *Report:* how many players it lists (your own side is at most the
      team size; it should never list the other team).

12. **Log out fully** (exit the game).

## At the desk, with the game closed

13. The list, as wowlab reads it:

    ```bash
    uv run wowlab crew list
    ```

    - *Report:* does the count match what you saw in game? Is each line's
      class, level and "last instance recorded" sensible? For the players
      met only in the open world, does it show no instance?
    - For one player, `uv run wowlab crew show NAME-REALM` (the name stays
      on your screen; do not paste it). *Report:* what role it shows
      (tank, healer, damage, or none).

14. The reader's own check, which prints counts and key shapes only:

    ```bash
    WOWLAB_WOW_ROOT="/path/to/World of Warcraft" uv run pytest -m live lab/core/tests/test_crewbook_live.py -s -p no:cacheprovider
    ```

    - *Report:* paste its output. It holds no names, and it gives the
      skipped count and how many players have each role.

15. Does the game's backup hold the book? This prints a number only:

    ```bash
    grep -c WowLabCrewDB "/path/to/World of Warcraft/<flavor folder>/WTF/Account/<your account folder>/SavedVariables/WowLab.lua.bak"
    ```

    - *Report:* the number (0 means the backup does not hold it), or "no
      such file".

16. Did the warning reach the chat log? This prints a number only:

    ```bash
    grep -c "crew book" "/path/to/World of Warcraft/<flavor folder>/Logs/WoWChatLog.txt"
    ```

    - *Report:* the number.

17. `uv run wowlab doctor` and read its crew book line.
    - *Report:* does it say on, with the right count?

## If something goes wrong

- **A Lua error popup:** copy its first two lines. They name a file and a
  line number, not a player.
- **The book is gone after a login:** do not play on. Close the game and
  tell the conductor: this is the loader bug §20.6 names. The snapshot
  from step 4 holds the book as it was then (`uv run wowlab snap list`
  shows it, and `uv run wowlab snap restore` puts it back, asking first).
  What was recorded after that snapshot is lost. `wowlab undo` does not
  help here: it reverses wowlab's own last change, which is the addon
  install.
- **To stop:** `/wowlab crew off` keeps what is stored and records nothing
  more. `uv run wowlab crew forget --all` (game closed) removes it all.

## What each answer decides

| Step | Settles (§20.6) |
|---|---|
| 3, 4 | whether the account file's probe and the book survive a logout |
| 5, 6 | the realm a same-realm and a cross-realm member come back with; whether one player gets two keys |
| 5, 7 | whether a roster update arrives before names are loaded; `met` across a `/reload` |
| 8, 9 | whether names are secret values in combat or in an instance |
| 9, 13 | what `GetInstanceInfo` gives in the open world and in an instance |
| 11 | whether a battleground team counts as a group (an arena is not checked) |
| 13 | what roles come back |
| 15, 16 | whether the backup holds the records; whether a printed line reaches the chat log |

Not reached by this check: the 5,000 drop order; whether an arena team
counts as a group; and whether `UNIT_NAME_UPDATE` follows a roster update
that came before names were loaded (step 5's count against the group size
shows only the result).
