# Visual and interaction designer

Seat: designer. Sandboxes `designer` (base fixtures plus the macos-70450 overlay, so two snapshots and two schema-4 characters) and `designer2` (fresh store, used for the first-run doctor confirm). The UI was driven in headless Chromium 1194 through playwright-core (scripts `shots.js`, `flow.js`, `flow2.js`, `flow3.js`). Screenshots are in `$EVAL/work/designer/shots/`, written below as `shots/<file>`.

## Verdict

I would recommend the CLI and the Explorer tab to a technical player who already knows what `WTF/` is and wants to know what is safe to touch. I would not yet hand `wowlab ui` to an ordinary player. The look is coherent and pleasant: a dark-and-gold "game UI" theme with a parchment detail pane, a proper light mode and no build step. But almost every screen past the Explorer talks to the developer, not the player. The Commands tab shows results as collapsed JSON trees. The confirm step hides the plan in a node called `"first_run": {11}`. The Character tab opens on "Spec id 1490, Average item level 3.3125", followed by six bullet points of `[verify]` caveats, and it runs to 9,000 px in one column. Error states are exit codes and raw Click or Python messages. The biggest problem is that the honesty of the text, which is the project's real strength, has never been edited for a player. Every caveat, ticket id, API name and `[verify]` marker reaches the screen at the same weight as the answer. Within 30 seconds a player understands the Explorer badges and little else. There is also a visible bug in the shipped Gaps tab: it prints "2 undefined".

## What works

- **Explorer detail pane** (`shots/desk-e2-config-preview.png`). This is the best screen in the product. It has a clear title and path, then a verdict badge with its plain sentence. "What it is" and "Who writes it, and when" follow, then the snapshot status ("the file on disk is the same"), and then a copyable command labelled with its shell. The hierarchy is right.
- **The confirm note itself.** "Confirm takes a first snapshot of _classic_beta_ into wowlab's store." is one plain sentence, and the "Show the plan" and Confirm wording is right (`shots/fresh-desk-f1-doctor-plan-full.png`).
- **The Keys table** says what a key does in game terms ("casts Immolate (Rank 2, spell 707)", "shows main-bar page 3") (`shots/desk-h2-character-crop-keys.png`). Gear names in item-quality colours on a dark backing read like the game.
- **Light scheme** is done properly with tokens, not inverted (`shots/light-explorer.png`, `shots/light-character.png`). The page is usable at 900 px wide (`shots/mid900-*.png`, no horizontal scroll).
- **CLI `explain`, `snap diff`, `doctor`.** The `explain` key/value block is aligned and scannable. `snap diff` drills down to the changed setting (`~ WowLabDB["schema"]: 1 -> 4`). The doctor first-run intro is candid and well ordered.
- **Loading states exist** for the main reads ("Reading the install…", "Reading the gear, keys and macros of …"), and an empty filter says "Nothing matches." (`shots/desk-07-explorer-nomatch.png`).

## Findings

### F1 — Gaps tab prints "2 undefined": a duplicate `plural` function breaks every count
- Severity: major
- Area: ui / Gaps tab (and the counts in `groupCounts` and `placeCounts`)
- Evidence: `shots/desk-08-gaps.png` and `shots/desk-g1-gaps-open.png` show "[addon] 2 undefined" under "Not entered". `lab/core/src/wowlab_core/ui/templates/page.js:863` declares `function plural(n, word)`, and `page.js:1462` declares `function plural(n, one, many)` in the same IIFE scope. The later declaration is hoisted over the first, so `plural(2, " folder")` becomes `count(2, " folder", undefined)` and prints "2 undefined". Every `plural(...)` call in the Gaps code (lines 871–893, 956, 1003) is affected: one file reads "1  file" and more than one reads "N undefined".
- Impact: The first thing a curious player clicks after the Explorer shows broken text. That costs trust in a tool whose pitch is precision.
- Proposal: Rename one of the two functions (for example `gapPlural`), and add a UI test that renders the Gaps tab on a fixture with more than one folder in a group and asserts that the text contains no "undefined". A lint step that fails on duplicate function declarations in `page.js` would catch the whole class (plain-JS, no build step: a grep test is enough).
- Status: new

### F2 — Commands tab shows results as collapsed JSON trees, not the CLI's readable text
- Severity: major
- Area: ui / Commands tab
- Evidence: Run `doctor` (with `--offline`) on a fresh store. The result is `{10} "install_root" … "first_run": {11}`, all collapsed, and the plan the player is asked to confirm (size, file count, estimated time) is inside the collapsed `first_run` node (`shots/fresh-desk-f1-doctor-plan-full.png`). The CLI prints the same plan as six readable lines ("a snapshot would hold WTF, Interface and Fonts: 29 files, 109.8 kB; about 1 second"). Explorer previews do the same thing: `cvar list` shows `"cvars": [130]` collapsed (`shots/desk-e2-config-preview.png`).
- Impact: The plan-then-Confirm flow (ADR-0029) exists so the player sees what will happen before agreeing. Right now the player sees the Confirm button and a JSON root. Confirming without reading is the default path.
- Proposal: Show each command's text rendering by default, and keep the JSON tree as a collapsed "Details (JSON)" disclosure. Two ways to get the text: (a) the server also runs the text form, which costs one extra run, but the read commands are cheap; or (b) the report models gain a `summary_lines` field that the CLI and the UI both print. Put the plan's lines directly above Confirm. This is plain JS and needs no build step (ADR-0027/0029). Plan: `docs/LAB_PLAN.md` §16.1.
- Status: new (M13-26 chose the generic tree deliberately; this asks to demote it)

### F3 — Confirm step: Confirm lands below the fold, and the outcome is "exit code 0", with "dry_run: true" after a real gc
- Severity: major
- Area: ui / Commands, confirm flow (snap gc, doctor)
- Evidence:
  - At 1440×900, after "Show the plan" on `doctor` the confirm box is at y=956, below the fold. Nothing scrolls to it, and the status line is the only visible change (`shots/fresh-desk-f1-doctor-plan.png`).
  - After Confirm, the plan's note disappears, and the outcome is "Confirmed: exit code 0" over a collapsed JSON root. There is no sentence such as "Snapshot 2026…-a1b2 taken; `wowlab undo` reverses nothing here" (`shots/fresh-desk-f2-doctor-confirmed.png`).
  - `snap gc`: the plan says "Nothing to remove. Confirm runs it anyway, and it changes nothing." Confirm is still offered, and the confirmed result shows `"dry_run": true` under "Confirmed" (`shots/desk-c2-snapgc-confirmed.png`). Reproduced on the CLI: `wowlab snap gc --yes --json` prints `"dry_run": true` on an empty store.
- Impact: The player gets no clear confirmation of what happened, and the gc result contradicts itself ("Confirmed" next to "dry run").
- Proposal:
  - `scrollIntoView({block: "nearest"})` on the confirm box, and move focus to Confirm.
  - Replace "exit code N" with an outcome sentence built from the report: snapshot id and size, objects and bytes removed, journal record.
  - When the plan changes nothing, show "Nothing to do" and no Confirm.
  - Make `GcReport.dry_run` false when `--yes` was given, or rename the field to what it actually means.
  - Give "Show the plan" a secondary style, so that Confirm is the only gold, high-emphasis button in the box.
- Status: new

### F4 — Errors are labelled as plans, reported as exit codes, and shown as raw Click or Python output
- Severity: major
- Area: ui / Commands; CLI error paths
- Evidence:
  - `snap restore` with an empty id: the status reads "Plan, nothing was changed: exit code 2", above red Click usage text "Error: Missing argument 'ID'." (`shots/desk-c7-snaprestore-plan.png`).
  - `undo` with an empty journal: "Plan, nothing was changed: exit code 1" and "wowlab: nothing to undo: the write gate's journal is empty" (`shots/desk-d4-undo-plan.png`).
  - `sv dump` with no file: "Exit code 2" plus `Usage: wowlab sv dump [OPTIONS] {file}` (`shots/desk-c6-svdump-empty.png`).
  - `explain Nope/does-not-exist.txt`: "Exit code 0", with `"absent": true` inside the tree (`shots/desk-c5-explain-error.png`). The CLI also exits 0 on that input.
  - CLI `wowlab sv dump nope.lua` prints `wowlab: [Errno 2] No such file or directory: '/…/nope.lua'`.
  - Required fields are not marked, and Run is enabled with them empty. Field labels are raw flags (`--root`, `snapshot_id`) (`shots/desk-c3-doctor-plan.png`).
- Impact: A player cannot tell a refusal from a failure from a plan. "journal" and "write gate" are internal words.
- Proposal:
  - Mark required arguments from the command schema the server already sends, and block Run until they are filled ("Pick a snapshot").
  - Map exit codes to words: 0 Done, 1 Could not, 2 Missing or invalid input, 3 Refused by the write gate.
  - Never print "Plan" when the plan run failed.
  - For `snap restore`, offer a picker filled from `snap list --json`.
  - CLI: catch `FileNotFoundError` in `sv dump` and say "No SavedVariables file at …; try `wowlab sv list`". Give `explain` of a missing path a non-zero exit, or at least a clear "not found" line in the UI.
- Status: new

### F5 — Network actions are not labelled, have no progress, and fail with a raw URL; the Looks copy reads as "offline"
- Severity: major
- Area: ui / Commands (doctor), Looks tab
- Evidence:
  - In the UI, `doctor` defaults to online (the `--offline` box is unchecked). It shows "Planning wowlab doctor…" with no spinner, timer or cancel. The CLI run takes 35 s here: `time wowlab doctor --json` gives real 0m34.96s and ends with `"detail": "https://wago.tools/api/builds: gave up after 5 attempts (last: HTTP 503)"` (`shots/desk-c3-doctor-plan.png`).
  - Looks: the only control is "Write the page". After 35 s it shows `wowlab: https://wago.tools/db2/ChrRaces/csv?build=1.60.1.69913: gave up after 5 attempts (last: HTTP 503)` in red (`shots/desk-l2-looks-after-wait.png`).
  - The tab intro ends "It runs in a closed frame: only its own script, no network requests". That is true of the iframe, but a player reads it as "this tab is offline", and the button downloads from wago.tools.
- Impact: A 35-second silent wait reads as a hang. The project's own promise is clear disclosure of network use (README; ADR-0019/0031), and the tab's copy undercuts it.
- Proposal:
  - Label every action that fetches, at the button: "Downloads this build's race and customization tables from wago.tools (once; cached)".
  - Default the UI's doctor to `--offline`, with "Also check wago.tools" as an opt-in.
  - Show an elapsed-time counter and a Cancel button for any run longer than 2 s. The server already has a per-run timeout.
  - Turn network failures into one sentence: "wago.tools did not answer (HTTP 503) after 5 tries. Nothing was changed. Try again later." Keep the URL in Details.
  - Rename "Write the page" to "Show races and looks".
- Status: new

### F6 — Developer epistemics leak into player copy: `[verify]`, ticket ids, type names, API names, formulas
- Severity: major
- Area: CLI text output, `--help`, ui Character and Commands tabs
- Evidence:
  - `wowlab char binds --character 1/Sangraal-Sett | grep -o '\[verify\]' | wc -l` gives **27**. `char macros` has 9 and `char gear` has 3.
  - The doctor help shown in the UI ends "…are refused with it (M13-09)" and contains "JSON: DoctorReport". The `snap gc` help says "(GC_GRACE_SECONDS…) JSON: snapshot.GcReport" (`shots/desk-c2-snapgc-confirmed.png`).
  - The Character header notes cite `GetAverageItemLevel`, `C_SpecializationInfo`, "field 10 and the modifier pairs". The keys notes contain "slot (p - 1) x 12 + n [verify]". The Character tab shows "identity is recorded from schema 4 (the lab-addon from M15-01…)" (`shots/desk-12-character-full.png`).
  - `tree --explain` prints "written by: Agent", meaning the Battle.net Agent, and "Other client logs; not described here yet [verify]".
  - About 23 user-facing strings in `cli.py` carry ticket ids.
- Impact: This is the main reason a player does not understand the tool in 30 seconds. The answer (gear, keys, what a file is) is buried under caveats that matter to the maintainer. `[verify]` is a docs convention (AGENTS.md), not player vocabulary.
- Proposal: Write a copy rule into `.claude/rules/` and `docs/AGENT_WORKFLOW.md` (owner or conductor edit):
  - Player-facing text never shows ticket ids, model class names, constants or client API names.
  - `[verify]` renders as "(unconfirmed)", or in the UI as one small marker with a tooltip.
  - Caveat notes go into a single collapsed "About this data" disclosure per panel. In the CLI they print only with `--notes` or `-v`, with a one-line "N notes: --notes to show" footer.
  - Move "JSON: XReport" out of `--help` into the command reference.
- Status: new

### F7 — The Character header does not say who the character is
- Severity: major
- Area: ui / Character tab; CLI `char gear`
- Evidence:
  - Header for `Sangraal-Sett` (`shots/desk-h2-character-crop-gear.png`): "Spec id 1490", "Average item level, equipped 9.5", "overall 9.5". For `Labchard-Labrealmg` it reads "3.3125" (`shots/desk-12-character.png`).
  - The schema-4 record holds race 5, class 9 and `class_file "WARLOCK"`, level 15 (fixture README row 305), yet the only place the page says so is the dressing-room footnote "race 5, body type 0, class 9, level 15".
  - In the keys table, items read "uses item 3434", with no name.
- Impact: A player's first question ("is this my level-15 Undead Warlock?") is answered only in id numbers. The page's most prominent facts are an id and a four-decimal float.
- Proposal:
  - Header line: "Level 15 Warlock · Affliction" (or "spec 1490" until a name is known). `class_file` is already in the record, so no lookup is needed.
  - Race and spec names from the build's cached tables when present, falling back to the id. Names come from data, never constants, which respects L6.
  - Round the averages to one decimal, with the exact value in a `title`.
  - Item names in the keys table from the gear record when the item is equipped.
  - Plan: `docs/LAB_PLAN.md` §16.3 (amend the header spec).
- Status: new (M13-80 touches identity only for `looks import-char`)

### F8 — Character tab: one 9,000 px column, and an arbitrary default character
- Severity: minor
- Area: ui / Character tab
- Evidence:
  - The full page for a schema-4 character is 1440×9111 (`shots/desk-h2-character.png`): gear, then 130 key rows, then macros, then a block of notes, with no in-page navigation.
  - The picker lists `Labchard-Labrealmg` (schema 1, no identity, no action bars) first and selects it, because all four files share an mtime (log: `CHAR OPTIONS: 0=Labchard-Labrealmg … 2=Sangraal-Sett …`). The first view is therefore the emptiest record, with three "Cannot place / Cannot compare" lines (`shots/desk-12-character-full.png`).
- Impact: The player scrolls through a wall of keys to reach macros. The first impression shows the tab's weakest data.
- Proposal:
  - Sub-tabs or a sticky segmented control (Gear | Keys | Macros), plain JS.
  - Group keys by bar (Main bar 1–12, Bar 2, Movement, Targeting) with counts, collapsed after the first group.
  - Break mtime ties by the newest schema. Show the schema age in the picker ("old record: log in once with the new addon").
- Status: new

### F9 — Badge colours carry the wrong meaning; "Verdict" is jargon
- Severity: minor
- Area: ui / Explorer
- Evidence:
  - `shots/desk-02-explorer.png`: "wowlab won't touch" is drawn in red on maroon (`page.css:121`, `.badge-no`), which is the colour of danger and errors. "wowlab can edit" is gold, the brand highlight.
  - The CSS header comment says "badges coloured like item quality", but the mapping is not item quality either.
  - The filter label "Verdict" offers "All | wowlab won't touch | folder | wowlab can edit".
- Impact: Red on `.build.info` and `Logs/` makes them look broken or dangerous, when the badge is reassurance that wowlab leaves them alone. The real hazard ("the server may overwrite") gets only a dashed orange.
- Proposal:
  - "won't touch" in neutral slate.
  - "can edit" in gold, as now.
  - "server may overwrite" in amber with a warning glyph.
  - Red reserved for errors.
  - Rename "Verdict" to "Can wowlab change it?" and spell "folder" out as "Folders".
- Status: new

### F10 — First view: an empty parchment, no summary, and the install path as the loudest text
- Severity: minor
- Area: ui / Explorer first paint
- Evidence:
  - `shots/desk-02-explorer.png`: the right half is a parchment titled "Pick a file or folder" with nothing under it. The tree panel opens with the absolute install path in bold gold over two lines. The status says only "55 paths."
  - The banner paragraph is a two-line legal sentence ("…nothing on this page changes your game files; the Commands tab can save to wowlab's own data…"), and every tab adds its own four-to-six-line intro (`shots/desk-09-commands.png`).
- Impact: The player has no first action, no sense of their install's state, and a lot of text to read before anything.
- Proposal:
  - Fill the empty pane with an overview: counts by badge, "last snapshot: 3 days ago" or "no snapshot yet → Take one" (which goes to the Commands plan), and the newest character.
  - Show the install path in small muted mono text.
  - Cut tab intros to one line, with "More" disclosures.
  - Plan: `docs/LAB_PLAN.md` §15.6.
- Status: new

### F11 — No narrow-width layout: horizontal scroll, clipped tabs, mid-word breaks, detail off-screen
- Severity: minor
- Area: ui / all tabs at phone or half-screen width
- Evidence:
  - At 390×844 the `scrollWidth` is 483 on Explorer and 484 on Commands and Character (script log).
  - The tab bar overflows, with "Looks" off-screen. Tree names break inside words ("DBM-Braw / lers/", "addo / n;") (`shots/mobile-02-explorer-full.png`).
  - The Commands list and detail stay side by side, with the detail clipped (`shots/mobile-09-commands.png`). The Character picker overflows (`shots/mobile-12-character.png`).
  - The explain pane sits under the whole tree, and nothing scrolls to it when a file is picked. `page.css` has no width media query (its only `@media` is `prefers-color-scheme`), and `page.js` has no `scrollIntoView`. 900 px is fine.
- Impact: Low for a desktop tool, but a half-width window on a laptop is a common way to run this beside the game launcher.
- Proposal: One `@media (max-width: 700px)` block:
  - Tabs scroll or wrap.
  - `.commands-body { flex-direction: column }`.
  - `.name { overflow-wrap: normal; word-break: normal }`, with the note moved under the name.
  - `scrollIntoView` on the explain pane after a pick when panes are stacked.
- Status: new

### F12 — Commands list: 47 unlabelled commands, nothing marks the ones that change the install
- Severity: minor
- Area: ui / Commands
- Evidence: `shots/desk-09-commands.png`. The flat list runs "doctor, tree, explain, undo, ui(disabled), install show, filemap gaps, sv list, sv dump, sv merge, …". `undo`, `sv merge`, `snap restore`, `profile apply` and `addon install|remove` look exactly like `tree`. The difference is explained only in the six-line intro paragraph above.
- Impact: The most consequential commands are visually equal to read-only ones, so the player must read the intro to know which buttons touch the game folder.
- Proposal:
  - Group the list under headings: Look (read-only), Snapshots and profiles (wowlab's store), Change your install (write gate).
  - Reuse the Explorer badge vocabulary ("changes game files", "asks first") as a small marker per row.
  - Give each row a one-line plain description (short help) instead of the docstring.
- Status: new

### F13 — CLI output: the notes outweigh the data, and some lines contradict each other
- Severity: major
- Area: CLI `char gear`, `macros check`, `binds coach`, `char list`
- Evidence:
  - `wowlab char gear`: 19 slot lines followed by 11 lines of notes (paths into the venv, "[verify]", API names). `char gear --character 1/Sangraal-Sett | wc -l` gives 32.
  - `wowlab macros check` on 0 macros: "Findings: 0", then one paragraph-length line about how length is counted and two more paragraphs of caveats.
  - `wowlab binds coach` lists "Bane of Agony (980): no key", "Corruption (172): no key", … and two lines later says "Spells cast with no bind: 0". The real reason is a "Cannot judge" further down, but it reads as a contradiction.
  - `binds coach` and `char gear` print 200-character lines with absolute paths into the source tree (`/home/user/wowlab/lab/core/src/wowlab_core/binds_coach_costs.toml`). Nine `doctor --offline` lines are over 100 columns at COLUMNS=80.
- Impact: The CLI is the product's main surface. Its answers are hard to find, and the coach's headline numbers look wrong.
- Proposal:
  - Answer first, notes last and short: "N notes; run with --notes to read them".
  - When a count cannot be judged, print "Spells cast with no bind: cannot judge (no bindings record)" instead of 0.
  - Wrap prose to the terminal width (`click.wrap_text` or `textwrap` with `shutil.get_terminal_size`).
  - Say "copy the default key-cost table with `wowlab binds coach --write-costs`" instead of printing source paths.
- Status: new

### F14 — CLI polish: no colour, truncated top-level help, timestamp-shaped ids
- Severity: minor
- Area: CLI global
- Evidence:
  - No ANSI colour anywhere, even on a TTY (`script -qc 'wowlab doctor --offline' /dev/null | grep -c $'\e'` gives 0).
  - `wowlab --help` cuts summaries mid-phrase: "doctor  Install, flavors, versions, whether the client runs, store...", "sv  SavedVariables files: list and dump read them; merge writes...".
  - `snap list` prints `20261021T171436.093821Z-832e0860  _classic_beta_  1.60.1.69913  29 files` with no header or age.
  - `explain` prints `modified 2026-10-21T16:35:53.837668Z`, and `binds coach` prints `4/1/2026 02:16:37.862-4`.
- Impact: The output is dense and monochrome, so verdicts like "no" or "refused" and findings do not stand out. Players will quote ids from `snap list` and need short ones.
- Proposal:
  - Colour only the verdict words, the findings count and errors (red, yellow, green), honour `NO_COLOR` and non-TTY, and keep `--json` unchanged.
  - Set `short_help` on every group and command.
  - `snap list`: a header row, a relative age ("2 h ago") and a short id (8 characters, the unique-prefix rule already exists).
  - Print local time to the second in text mode, with the full ISO time in `--json`.
- Status: new

### F15 — `wowlab ui --help` says game-file commands are "not offered in this version"
- Severity: minor
- Area: CLI `ui --help`
- Evidence: `wowlab ui --help`: "Commands that change your game files, and the other commands that take --yes, are not offered in this version". The UI offers `snap restore`, `undo`, `profile apply` and the rest behind Confirm (`shots/desk-09-commands.png`). The README matches the UI, not the help.
- Impact: A safety-conscious player reads the help and gets a wrong model of what the browser can do.
- Proposal: Rewrite as ticketed. Also say how to get the URL when a browser "opens" but is not seen. With `BROWSER=true`, the token URL is never printed: "serving on http://127.0.0.1:35861/", with no token.
- Status: already-ticketed: M13-76 (the token-URL part is new)

## Top 3 changes

1. **Make the confirm flow readable (F2, F3, F4), and fix the Gaps "undefined" bug (F1).**
   - Render each command's text, with the plan above Confirm and Confirm scrolled into view.
   - End with an outcome sentence, not an exit code.
   - Never label a failure as a plan.
   - This is the one place the UI asks the player to agree to something, and today it asks with a JSON root.
2. **A player copy pass across CLI and UI (F6, F7, F13).**
   - Answer first, caveats collapsed.
   - No `[verify]`, ticket ids, type names or API names in player text.
   - Character header in player terms ("Level 15 Warlock").
   - Write the rule into the harness so agents keep it.
3. **Honest, visible network behaviour (F5).**
   - Label every button that reaches wago.tools.
   - Default the UI's doctor to offline.
   - Show progress and cancel.
   - Turn 503s into one plain sentence.

## Not tested

- **Real network.** wago.tools answered 503 through the proxy and live calls are off-limits, so the Looks page content, `db2` and the online doctor result were never seen. Only their failure states were.
- **Confirm flows that write the game folder** (`snap restore` with a real id, `profile apply`, `sv merge`, `addon install`). Only the plan screens with empty input were exercised; the brief scoped this seat to `snap gc` and doctor.
- **Other browsers, Windows and macOS rendering**, real system fonts (the serif stack fell back on Linux), and HiDPI.
- **Screen reader and full keyboard navigation.** Only the `:focus-visible` style and the ARIA roles in the templates were checked, by reading them.
- **The "See it dressed" destination** (the external site; not opened).
- **Light mode on the Commands, Gaps and Looks tabs** beyond one screenshot each, and the dark gear table inside light mode was not judged for contrast with a tool.
