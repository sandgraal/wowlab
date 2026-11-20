# Wiki pages waiting for an update

The wiki (`sandgraal/wowlab.wiki`) was brought up to date on 2026-11-18, in
wiki commits 819b89d and d9f3e16 (after e4bc840 and 155d160 on 2026-11-17):
every row this list held, up to and including #608, is done. 819b89d also
added `wowlab macros fix` (#582), which no row here had named, and the
roadmap's Wave 6 and Windows paragraphs; d9f3e16 added the `compositor`
module (#603) and the one CLI-Reference cell #606 changed. The wiki shows
v0.3.0 as the current release.
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

## Merged since 2026-11-18

| Merged | Ticket | What changed for the player | Wiki pages (from the PR) |
|---|---|---|---|
| 2026-11-19 | M15-11 (#611) | `wowlab char model` writes the character as a `.glb` file with its body, hair and eyes PNGs; the first run for a game version downloads the tables it needs | CLI-Reference (regenerate), Command-Reference, Lab-Addon-and-Characters, Safety-and-Guarantees (what leaves the machine), Home (roadmap: Wave 6 done) |
| 2026-11-19 | M15-25 (#622) | `char model` files sit in a folder per realm; an empty `--root` is a usage error | CLI-Reference (regenerate), Lab-Addon-and-Characters (the path) |
| 2026-11-19 | M15-22 (#621) | Commands start faster; nothing else changes | none |
| 2026-11-19 | M19-02 (#625) | `wowlab crew list` and `crew show` read the crew book, as of the last logout or /reload; nothing is changed. Not in a release until the addon's crew book (M19-10) is merged | CLI-Reference (regenerate), Command-Reference, Lab-Addon-and-Characters |
| 2026-11-19 | M19-03 (#630) | `wowlab crew forget`, `crew rate` and `crew note` change the crew book through the write gate, with `undo`; ten commands can now change the install. Not in a release until M19-10 is merged | CLI-Reference (regenerate), Command-Reference, Snapshots-and-Undo, Safety-and-Guarantees (seven commands becomes ten), Lab-Addon-and-Characters |
| 2026-11-19 | M19-05 (#627) | `doctor` says whether the crew book is on and how many players it keeps, never a record | CLI-Reference (regenerate), FAQ (what a bug report holds) |
| 2026-11-19 | M19-11 (#629) | `snap export` and the plans of `snap restore`, `undo` and `sv merge` say when they carry the crew book | Snapshots-and-Undo, SavedVariables-and-Merging |
| 2026-11-19 | M19-10 (#634) | The crew book in game: `/wowlab crew on`, `off`, `who`, `good`, `bad`, `clear`, `note`; off by default; it keeps the players in your party or raid, including your own side in a battleground or arena | Lab-Addon-and-Characters (a crew book section: what it keeps, how to turn it on and off, its limits), Safety-and-Guarantees, FAQ, Home (roadmap) |
| 2026-11-19 | M19-06 (#635) | A Crew tab in `wowlab ui`: the list with search, rating filter and sort, one player's record, and forget, rate and note through the confirm step | Browser-View (the Crew tab), Lab-Addon-and-Characters |
| 2026-11-20 | M19-09 (#639) | `wowlab snap rm` removes a snapshot from the command line (not in the browser page); `snap gc` says how many stored files it kept because they are under an hour old | CLI-Reference (regenerate), Command-Reference, Snapshots-and-Undo, Browser-View (the commands it does not run) |
| 2026-11-20 | M20-01 (#640) | `wowlab crew sync`; `crew list` and `crew show` answer from wowlab's own crew database, which keeps players the game's list has dropped; `crew forget` clears the database too | CLI-Reference (regenerate), Command-Reference, Lab-Addon-and-Characters (the crew book: two stores), Safety-and-Guarantees |
| 2026-11-20 | M15-27 (#643) | `wowlab char model` draws weapons, shields and off-hand items sheathed, labelled "placement approximate (not yet confirmed)"; `--no-held` leaves them out | CLI-Reference (regenerate), Lab-Addon-and-Characters (the 3D model: what is drawn and what is approximate) |
| 2026-11-20 | M19-12 (#642) | `snap rm` removes a snapshot wowlab cannot read when its full id is typed with `--break-undo`; `snap gc` says so in plain words | Snapshots-and-Undo |
