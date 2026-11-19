# The PvP book: a proposal

Written by the conductor on 2026-11-19 for the owner, who said the same
day: "I am really interested in being able to track all of my PvP enemies
and friends across my journey. I do not want to have to limit that number
so we may need to reconsider the 5000 limit", and then: "if we need to
store to a better database internally, let's do that. It shouldn't be so
hard to read from a well indexed table."

This is a proposal, not a plan. Nothing here is built or ticketed. If the
owner accepts it, the conductor turns it into a plan section, the ADR
changes below (`Proposed` until the owner accepts them) and tickets, in one
docs PR, as `docs/LAB_PLAN.md` §11 asks.

## What you would get

1. **Everyone you fight with and against is remembered.** After each
   battleground or arena the book records both teams: who was on your side
   and who was against you, with the map, the date and whether you won.
2. **No limit on how many.** The history lives in a database on your
   machine. It can hold every player you ever meet.
3. **Fast answers at the desk.** "Everyone I have faced more than five
   times", "my record with this player on my team", "who did I meet in
   Warsong last week" come from indexed tables, not from re-reading a text
   file.
4. **The same warning in game.** When a player you rated joins your team,
   you get one line: your rating, your note, how often you met, and whether
   it was with or against you. A warning about a rated *opponent* at the
   start of a match depends on the game listing the other team before the
   match ends, which is not known (**[verify]**); if it does not, you learn
   who you faced when the match is over.

What stays as it is: off until you turn it on, nothing leaves your machine,
no GUIDs, no chat, and `crew forget` removes a player everywhere.

## Why the 5,000 limit exists, and why it can go

The game gives an addon one place to keep data: a SavedVariables file, a
single text file the client reads whole at login and rewrites whole at
logout. Two costs grow with its size:

- **In game:** the client parses the whole file at every login and
  `/reload`, and holds it in memory. How slow a large one is on the Forever
  client is not measured.
- **At the desk:** wowlab parses the whole file on every read. Measured on
  constructed books: 5,000 players is about 1.4 MB and reads in 0.2 s;
  50,000 players is about 13 MB and takes 2.5 s.

The limit was set to keep both small. The answer is not a bigger text file.
It is two stores with two jobs:

| | In the game (SavedVariables) | At the desk (a database) |
|---|---|---|
| Holds | What the game needs to warn you: every player you rated or noted, and the most recent others | Everything, for good |
| Size | Bounded (a number you pick; see "Decisions") | Unbounded |
| Written by | The addon, at logout | wowlab, when it reads the saved file |
| Read by | The addon, for warnings | `wowlab crew ...` and the Crew tab |

wowlab copies new records from the saved file into the database whenever a
`crew` command or the Crew tab runs (a "sync"). It is a read of the install
and a write of wowlab's own data, so it needs no confirmation and works
while the game runs.

**One honest catch.** The addon cannot see the database. When its own list
is full it drops the oldest player you have not rated or noted. If you have
not run wowlab since that player was recorded, that record is gone. Rated
and noted players are never dropped. Two ways to close the gap, for you to
choose between:

- **A. A generous in-game list and a warning.** The addon keeps, say,
  20,000 players. It says once per session when it is within 10% of full
  and you have not synced. Simple; relies on you opening wowlab now and
  then.
- **B. The addon only drops what wowlab has archived.** wowlab writes
  "archived up to this time" into the saved file, and the addon drops only
  players older than that mark. Nothing is ever lost. The price is a write
  to your install: through the write gate, with the game closed, with a
  plan, a confirmation and a snapshot each time, as every write has. That
  cannot ride along on `crew list`, `crew show` or the Crew tab, which
  promise to change nothing, so it would be a command of its own
  (`crew archive`, say) that you run now and then. The conductor recommends
  A first, and B only if A loses data in practice.

## Where the names come from

Three sources, in order of how sure they are. Every statement about the
Forever client here is from memory of how other versions of the game work
and is **[verify]** until an in-game check says so.

1. **Your own team (built on 2026-11-19, in review as M19-10).** The crew book already
   records your party or raid, and a battleground or arena team is a raid.
   The owner ruled in session on 2026-11-19 that these count; §20.5's
   sentence is amended with that date in the addon's PR.
2. **The scoreboard, for opponents (new).** At the end of a match the game
   has a scoreboard listing both teams: name, realm, class, faction, and
   each player's totals. An addon can read it once the match is over, out
   of combat, with no GUID. This is the clean way to learn who you faced,
   and it also gives the result. **[verify]**: which scoreboard calls
   Forever has, and whether its names are ever secret values.
3. **World PvP (not proposed yet).** There is no scoreboard in the open
   world. Knowing who killed you, or whom you killed, means reading the
   combat log, which carries GUIDs and arrives during combat, when the
   client may hand addons secret values. The static checks (#626, #631)
   refuse the combat log outright. A lighter version records a hostile
   player only when you target them and type a command. The conductor
   proposes to leave world PvP out of the first build and decide it after
   the scoreboard is proven in game.

## What is recorded

For each player, as today: name and realm (the key), class, level, guild
last seen, first and last date, your rating and note. Added:

- how many matches **with** you and how many **against** you;
- of those, how many your side won;
- the last map and whether that last meeting was with or against.

In the database only, one row per meeting: the date, the map, with or
against, and the result. That is what makes "my journey" queries possible,
and it is too much to keep in the game's file.

Not recorded: GUIDs, chat, whispers, anything about a player you did not
share a match or a group with. Per-player kills and deaths *against you*
are believed not to be available from the scoreboard (on other versions of
the game it has each player's match totals only, **[verify]** on Forever)
and would need the combat log.

## What changes in the rules

- **ADR-0036** already allows other players' names "as the game shows
  them", local only, opt-in, deletable. Its plan section, not the ADR,
  limited the book to "the players you group with" (§20's aim; §20.8
  decision 2 says the same of the warning). Recording opponents
  is still a wider use than the owner had in view when accepting it, so the
  conductor proposes a dated amendment that says so in plain words:
  opponents from a match's scoreboard may be recorded, on the same five
  terms.
- **A new ADR for the store.** SQLite is already admitted for the game
  tables (ADR-0028). A database holding other players' names is a different
  kind of data and gets its own decision: where the file lives (wowlab's
  data directory, never the install, never the repository), that
  `crew forget` deletes from it, that `doctor --redact` and the capture
  tool never read it, and that nothing exports it without saying so.
- **The plan's limit** (§20.1 "Limits", §20.8 decision 5) is replaced by
  the two-store design above.
- **The static checks** gain a second, separately gated set of calls for
  the scoreboard, as `Crew.lua` has for the roster. `UnitGUID` and the
  combat log stay refused.

## What it costs

- The saved-file format changes (new counters), so the book goes to its
  schema 2. The reader, the write commands, the capture-tool rule and the
  repository scan (#625, #630, #624) all need a matching change. The addon's
  new rule that a book of another schema is left alone is what makes that
  safe.
- `crew forget` must now clear three places: the saved file, the game's
  backup of it, and the database. Older snapshots keep their copies, as
  today.
- A sync step, and a decision about what happens when the database and the
  saved file disagree (the saved file wins for rating and note, since you
  may have changed them in game; the database only ever gains meetings).
- One more in-game check for the owner, in a battleground: which scoreboard
  calls exist, what they return, and how long a login takes with a large
  list.

A rough size: the database and sync are about as large as the reader and
the write commands (#625, #630) together; the scoreboard half of the addon is
smaller than the roster half, but it needs the in-game check first, because
every fact it rests on is unconfirmed.

## A suggested order

1. **Finish the crew book as specified** (the wave in flight): the addon
   with your team recorded at 5,000, the Crew tab, your in-game check.
2. **The database and sync**, with the in-game list raised and the limit
   gone from the desk. This alone gives you unlimited history of teammates.
   It needs no new game API, so nothing in it waits on an in-game check.
3. **A scoreboard probe**: a few lines in the addon that record which
   scoreboard calls exist and the shape of what they return, with no names
   kept. You play one battleground and wowlab reads the answer.
4. **Opponents**, built on what the probe found.
5. **World PvP**, decided then.

## Decisions for the owner

1. Is the two-store design (a bounded list in game, everything in a
   database at the desk) what you want?
2. The catch: option A (a generous list and a warning) or option B (wowlab
   marks what it archived, with a write on sync)?
3. How large may the in-game list be? The conductor suggests starting at
   20,000 and measuring login time at the in-game check.
4. Opponents from the scoreboard: yes?
5. World PvP: leave out for now, or include the "target and type a command"
   version from the start?
6. Order: finish the current wave first (step 1), or fold the database into
   it?
