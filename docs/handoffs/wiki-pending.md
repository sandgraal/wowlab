# Wiki pages waiting for an update

The wiki (`sandgraal/wowlab.wiki`) was brought up to date on 2026-11-17, in
wiki commits e4bc840 and 155d160: every row this list held, up to and
including #575, is done, and the wiki shows v0.3.0 as the current release.
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

## Merged since 2026-11-17

| Merged | Ticket | What changed for the player | Wiki pages (from the PR) |
|---|---|---|---|
| #597 | M15-06 | Two new commands: `wowlab casc stat --fdid N` says whether one game file is on this computer, and `wowlab casc cat --fdid N` extracts it into wowlab's data folder (never into the install). Both are in the UI's Commands list; from the UI `casc cat` writes only to its default place | Command-Reference; CLI-Reference; the `wowlab ui` page |
