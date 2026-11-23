# Wiki pages waiting for an update

The wiki (`sandgraal/wowlab.wiki`) was brought up to date on 2026-11-21, in
wiki commit 8928d26 (after 575f1b0 on 2026-11-20): every row this list held,
up to and including #674, is done. That commit also corrected the wiki's
sentences that the owner's decisions of 2026-11-20 made false (what the crew
book and the lab-addon never record; what the capture tool replaces).
Everything on `main` and not in v0.3.0 is marked "On the development branch;
not in a release yet."; those marks come off at the next release.
On 2026-11-21 the owner approved that update in session. Not covered, because
they merged after it or were about to change: the tooltip line's wording and
M20-17 (#677).
On 2026-11-17 the owner approved an agent updating the wiki. On 2026-11-09
the owner ruled that a wiki item in a ticket's acceptance is met when the
merged PR names the pages it affects (`docs/AGENT_WORKFLOW.md`, "User-facing
changes"). The conductor keeps this list; each row is deleted once its pages
are done.

Page names are the wiki's own: CLI-Reference, Command-Reference,
Getting-Started, Lab-Addon-and-Characters, Snapshots-and-Undo, Profiles,
SavedVariables-and-Merging, Combat-Log, Safety-and-Guarantees, FAQ, Home.
Where a PR's wiki line was conditional ("only if the page says …"), the
condition is kept.

## Merged since 2026-11-21

| Merged | Ticket | What changed for the player | Wiki pages (from the PR) |
|---|---|---|---|
| 2026-11-21 | M20-17 (#677) | `char show` prints when each part of the record was read and which parts were read only at logout; a logout no longer empties the action bars or any other part; the crew book's tooltip line says "Battlegrounds: 3 with you, 1 against."; `/wowlab crew` says when it cannot find your home realm | Lab-Addon-and-Characters, CLI-Reference (regenerate) |
| 2026-11-21 | M20-18 (#680) | `wowlab crew scan-log` reads the game's combat log (written while `/combatlog` is on) and keeps the players who fought you in the open world, with each encounter: who was against you, who fought beside you, where, when and how it ended as far as the log shows; `crew list --world`, `crew matches --world`, open-world lines in `crew show`; the Crew tab's filter and its button to read the newest log; `doctor` counts encounters, opponents and logs. Duels count; players who only stunned, feared or missed you are not counted yet | Lab-Addon-and-Characters (the crew book), Combat-Log, Command-Reference, Browser-View, Getting-Started (the doctor lines), CLI-Reference (regenerate) |
| 2026-11-22 | M16-64, M16-55 (#683) | The addon no longer stores a ticket id in its appearances note (older files print the same note in new words); a debug session no longer counts one value the game withheld twice in the notes of `char show`, `char macros`, `binds coach` and `macros check` | none, unless a page quotes the old appearances note |
| 2026-11-22 | M20-19 (#686) | The addon turns the combat log on in the open world and off in instances, in a raid group and after 10 idle minutes (back on at the next fight); `/wowlab combatlog [on|off|idle MINUTES]`; `char show` has a Combat-log switch block; `crew scan-log` with no path reads every combat log not read before (newest 1,000) and names any it cannot read; the Crew tab button says so. First run on the real client not confirmed. | Lab-Addon-and-Characters, Combat-Log, Command-Reference, CLI-Reference (regenerate), Browser-View, char show section |
