# Wiki pages waiting for an update

The wiki (`sandgraal/wowlab.wiki`) was brought up to date on 2026-11-20, in
wiki commit 575f1b0 (after 819b89d and d9f3e16 on 2026-11-18): every row this
list held, up to and including #643, is done. Everything that commit added
(`char model`, the crew book, `snap rm`, the crew database, the Crew tab) is
marked "On the development branch; not in a release yet."; those marks come
off at the next release. The wiki shows v0.3.0 as the current release.
On 2026-11-20 the owner approved that update in session.
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

## Merged since 2026-11-20

| Merged | Ticket | What changed for the player | Wiki pages (from the PR) |
|---|---|---|---|
| 2026-11-20 | M19-13 (#646) | `snap list`, `snap show`, `snap verify` and five more commands say a snapshot is damaged in one plain sentence and quote nothing from it | none, unless a page quotes the old message |
| 2026-11-20 | M19-14 (#649) | `sv merge --from` and `--base` say a snapshot is damaged in the same plain sentence; `wowlab ui --help` and the install guide say ten commands can change the install, not seven | CLI-Reference (regenerate), SavedVariables-and-Merging only if the page quotes the old message |
| 2026-11-20 | M20-02 (#645) | `crew rate` and `crew note` reach wowlab's crew database too; a rating or note given to a player the game's list had dropped is kept when the player returns, and `crew sync` says how many it kept; `doctor` has a crew database line (counts, size, path; never a record) | CLI-Reference (regenerate), Lab-Addon-and-Characters, Getting-Started (the doctor line), FAQ |
| 2026-11-20 | M19-15 (#654) | `undo`, plain `doctor` and the first-run check say a snapshot is damaged in the same plain sentence and quote nothing from it | Snapshots-and-Undo and Getting-Started only if a page quotes the old message |
| 2026-11-20 | M20-03 (#656) | The desk reads and changes a crew book the next addon version writes; `doctor` gains one "Battleground scoreboard check" line per check the addon ran (type names and counts, never a name); an unknown book version is refused naming "versions 1 and 2". Nothing a player sees changes until the addon change (#653) is released | CLI-Reference (regenerate), Getting-Started (the doctor lines) |
| 2026-11-20 | M20-09 (#659) | `wowlab crew matches [NAME-REALM]`; `crew list --faced`, `--allied`, `--map`, `--since` and two new sorts; `crew show` says how many matches were with you and against you; `crew forget` clears a player from every match; the Crew tab shows the match history. No match is recorded until the addon's later versions, so these lists are empty for now | CLI-Reference (regenerate), Command-Reference, Lab-Addon-and-Characters (the crew book), Browser-View (the Crew tab) |
