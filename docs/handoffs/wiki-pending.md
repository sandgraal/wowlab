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
