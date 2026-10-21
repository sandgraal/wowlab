# Product strategist

## Verdict

I would recommend wowlab today to one narrow group: terminal-comfortable UI tinkerers and addon authors on macOS who play the Forever beta. Its safe-change core (snapshot, diff, journaled undo, profile apply, per-addon `sv merge`) is better than anything the incumbents offer, and its file map and format docs are the best public write-up of a modern client's `WTF/` folder I know of. As a product, though, it is aimed at the wrong moment and the wrong platform, and nobody knows it exists. Three days after v0.1.0, the release has **0 downloads and 0 stars**, and the only issue author is a bot. Forever's beta, the only flavor ever captured, ends around 2026-10-22. Launch is 2026-11-04 and will probably bring a new flavor folder. The 30-day gate closes on 2026-11-17, only 13 days after launch. Meanwhile the roadmap is putting its next two waves into a 3D character pipeline that wow.export and Wowhead already offer for free. **Biggest problem:** the plan keeps building features while nothing gets the tool to users. Distribution, Windows and the launch client are the bottleneck, not code.

## What works

- **The safe-change loop is real and pleasant.** `profile save raid --preset ui` → edit → `profile apply raid` (plan, `[y/N]`, "Nothing was changed") → `--yes` → `undo --yes` put `Config.wtf` back byte for byte in my sandbox in under a second per command. WowUp's "Interface Settings Backup" offers create, apply and delete of whole-WTF backups only. It has no diff, no per-addon merge, no journal and no undo of the restore itself ([WowUp guide](https://wowup.io/guide/my-addons/addon-settings)). CurseForge has only a "Sync Profile" of the addon set, with no settings backup ([CurseForge FAQ](https://support.curseforge.com/en/support/solutions/articles/9000198422-addons-faq)).
- **The trust story is concrete, not marketing.** The first run (`doctor --offline`) states the five rules before it does anything, sizes the first snapshot (30 files, 154 kB, about 1 s) and explains undo. For a tool that rewrites a player's UI files, that is the right first screen.
- **`binds coach` is novel.** No incumbent maps a player's own combat-log casts onto their keys and key costs. With the schema-3 character it gave sensible output: rank mismatches matched by name, plus eight bound-but-never-cast keys. Warcraft Logs, Raider.IO and SimC all judge performance or gear, not ergonomics.
- **Discovery really is flavor-agnostic.** I renamed the sandbox to `_retail_`, set product `wow` and version `12.0.5.66000`, and `doctor`, `char list` and `explain` all worked unchanged (constructed probe; see F12).

## Personas and jobs

| Persona | Rough size | Job they hire it for | What they use today | wowlab |
|---|---|---|---|---|
| **P1 UI tinkerer / addon-heavy raider** (ElvUI, WeakAuras, Plater, DBM) | The large majority of roughly 5–9M monthly players use addons (third-party estimates; Blizzard has published no figure since 2015: [ElectroIQ](https://electroiq.com/news/world-of-warcraft-statistics/), [ActivePlayer](https://activeplayer.io/?p=9643)). Those who would install a CLI with `uv`: my estimate is low tens of thousands worldwide. | "Never lose my UI. Let me experiment and get back. Move to a new PC. Copy one addon's settings to an alt." | Manual WTF copies, WowUp backup, Wago.io import strings for WeakAuras/ElvUI/Plater/Edit Mode ([warcraft.wiki.gg: Wago](https://warcraft.wiki.gg/wiki/Wago)), ElvUI profiles, server sync of binds/macros (`synchronize*` CVars, which forum users report as unreliable: [Blizzard forums](https://us.forums.blizzard.com/en/wow/t/stop-wow-from-server-syncing-macros/395250)) | **Wins** on diff, undo and per-addon merge. **Loses** on reach: no GUI installer, no addon-manager integration, and action bars are not covered (F6). |
| **P2 Forever launch player** (returning Classic player on a brand-new client) | Every current subscriber has access ([Digital Citizen](https://www.digitalcitizen.life/world-of-warcraft-forever-brings-classic-plus-to-azeroth-on-november-4/)); a few hundred thousand to low millions at launch (estimate). | "Which of my addons work on this client? Set up my UI once and copy it to alts. Don't lose it in launch-week chaos." | CurseForge (already documents the Forever beta path: [2upskill guide](https://2upskill.com/wow-forever-beta-curseforge-addon-directory-path-configuration-guide/)), WowUp, forums | **Could win** in launch week (F13). Today it only knows the beta. |
| **P3 Addon author / data-curious power user** | Thousands | "What is in these files, losslessly, from code. A sandbox to test against." | Text editor, BugSack, wago.tools, wow.export | **Wins outright**: `LAB_FILE_MAP.md`, `LAB_FORMATS.md`, the lossless `luadata`, `explain`. This is the persona most likely to file the 5 issues the gate needs. |
| **P4 Keybind/macro optimizer** | Small | "Fix my binds and macros." | Guides, Wowhead, in-game | **Novel** (`binds coach`, `macros check`), but read-only. The apply half waits on owner tests (F14). |
| **P5 Character showcase / RP / 3D** | Small | "See or export my character in its gear." | Wowhead Dressing Room, wow.export (MIT, retail and classic, OBJ/glTF export, 572★: [GitHub](https://github.com/Kruithne/wow.export)), WoW Model Viewer, in game | **Duplicates.** M15-02's dressing-room link already covers the "see it" job (F7). |
| **P6 The owner** | 1 | Personal lab | n/a | Fully served. Legitimate, but it should not be confused with the public product. |

## Findings

### F1 — Nobody can find it: v0.1.0 has zero downloads, and the plan has no channel to users
- Severity: blocker
- Area: release / distribution (LAB_PLAN §15, ADR-0031)
- Evidence: `gh api repos/sandgraal/wowlab/releases --jq '.[] | .tag_name as $t | .assets[] | "\($t) \(.name) \(.download_count)"'` gave `v0.1.0 … .whl 0`, `v0.1.0 … .tar.gz 0`, `v0.1.0 SHA256SUMS 0` (rc1: 5+3+1, almost certainly the owner's M13-10 run). `gh api repos/sandgraal/wowlab --jq .stargazers_count` gave `0`. The only issue on the repository is #240, by `github-actions[bot]`. A grep of LAB_PLAN and DECISIONS for `announce|reddit|discord|outreach|pypi|installer` finds no outreach step anywhere. ADR-0031 rules out PyPI and installers until the gate passes.
- Impact: The gate (≥50 downloads, ≥5 non-owner issue/discussion authors by 2026-11-17) will fail by construction, not on merit. The freeze would then be read as "nobody wanted it" when in fact nobody was told.
- Proposal: Add one M13/M16 ticket, "first 100 users". A 60-second terminal GIF of snapshot → break UI → undo. One post each to r/WowUI, r/wowaddons, r/classicwow and the Forever addon-dev Discord(s), framed for P1 and P3 (F5). Enable GitHub Discussions with a "tell us your setup" thread. Publish the same wheel to PyPI so that `uvx wowlab doctor` and `pipx install wowlab` work. That last step needs an ADR-0031 amendment ("No PyPI … until the gate") and a one-line change to the release workflow.
- Status: new (the PyPI part needs an ADR-0031 amendment)

### F2 — The gate window and the only captured flavor expire together
- Severity: blocker
- Area: plan sequencing (LAB_PLAN §15.4, M13-13; `docs/handoffs/M13-gate.md`)
- Evidence: Gate day is 2026-11-17 (`M13-gate.md`: "thirty days after the tag" of 2026-10-18). Blizzard's roadmap runs the beta from 2026-09-17 to 2026-10-22 and launches on 2026-11-04 ([Shacknews](https://shacknews.com/article/150712/world-of-warcraft-forever-classic-release-date-beta-sign-up), [PCGH](https://www.pcgameshardware.de/World-of-Warcraft-Spiel-42971/News/Forever-Classic-Plus-Release-Systemanforderungen-1553608/)). LAB_PLAN §2: "The beta wipes before launch and the flavor folder will probably change at launch." Every fixture root is `macos*/forever` at beta builds 69913–70450 (`ls lab/core/tests/fixtures/`).
- Impact: Half of the 30-day window falls in the gap between beta and launch, when the target audience has no client to use it on. The launch client is new: a new folder, a probably new interface number for the lab-addon's `@WOWLAB_INTERFACE@`, and a re-check of every `[verify]` against the beta. It will be met for the first time by strangers, not by the owner.
- Proposal: (a) Move the gate clock to start at a **v0.2 "launch" release cut within about 7 days of 2026-11-04**. The gate counts are unchanged; only the start date moves (an amendment to ADR-0031 and §15.4). (b) Add an owner capture ticket on the launch client in the first 48 hours (an M10-03-style rerun: discovery, `.flavor.info`, interface number, lab-addon load, one logout) before v0.2 is tagged. (c) Pause M15 dispatch until v0.2 ships (F7).
- Status: new (amends M13-13 / ADR-0031's gate timing)

### F3 — Windows, most likely the majority platform for Forever, has never been run by a human
- Severity: major
- Area: platforms (ADR-0031 platform rule, M13-02)
- Evidence: README platform table: "Windows | **Untested** … nobody has yet run the first-run flow on a real Windows install". M13-review: Windows took "at least eleven tickets … and are not finished". M14-review: "M13-02 Windows human run" is still listed under owner actions, with "no Windows machine or tester". Forever ships on Windows and macOS ([Shacknews](https://shacknews.com/article/150712/world-of-warcraft-forever-classic-release-date-beta-sign-up)). I found no published platform split; my estimate is that Windows is well over 80% of the player base.
- Impact: The supported platform is the minority one, so most of the P1 and P2 audience is told "untested" on the first screen. CI cannot catch the failures a real Windows install will produce: Defender or OneDrive on `Documents`, `Program Files (x86)` permissions, CurseForge-managed AddOns.
- Proposal: Treat "one human Windows first run on the launch client" as v0.2's single release blocker. Recruit the tester through the F1 post ("we need one Windows player for 15 minutes"). That person also counts toward the gate.
- Status: already-ticketed: M13-02 (human half)

### F4 — The install path filters out almost every player
- Severity: major
- Area: `docs/INSTALL.md`, README first screen, ADR-0031
- Evidence: README: "you need uv … `uv tool install --python 3.12 https://github.com/…whl`". ADR-0031 context: "The first users are expected to be comfortable with a terminal." Every interaction afterwards is a CLI command or `wowlab ui` started from a terminal.
- Impact: For P1 and P2 the incumbents are one-click GUI apps (CurseForge, WowUp). A terminal plus a Python tool manager limits the reachable audience to P3 and the most technical P1s. That group is still enough for 100 users, but only if the copy targets them (F5).
- Proposal: For v0.2, keep the CLI and accept the P3-first audience, but add PyPI (F1) so the line becomes `uvx wowlab doctor`. After the gate, write the further ADR that ADR-0031 already anticipates for a frozen, signed binary, a double-clickable `wowlab ui` launcher, or both. Do not spend on this before the gate.
- Status: new (frozen binary conflicts: ADR-0031 until the gate passes; PyPI needs an ADR-0031 amendment)

### F5 — The headline sells curiosity; the reason to adopt is fear of loss
- Severity: major
- Area: README first screen, wiki
- Evidence: README line 9: "Ever wondered what all those files in your WoW folder actually are?" and line 22: "Your WoW folder, finally explained, and every change undoable." The first command list is `doctor`, `ui` (explorer), then `snap create`. The README runs 383 lines, including a "full vision" of about 40 menu ideas (lines 255–318).
- Impact: "Explain my files" is a one-time curiosity job (P3). The recurring, painful job is "an addon update or reinstall wiped my UI" (P1/P2), and wowlab beats WowUp on exactly that (diff, per-addon merge, undo). A 40-idea vision on the page a stranger evaluates makes the project read as a personal sketchbook, not a tool.
- Proposal: Rewrite the first screen around "Time Machine for your WoW UI": snapshot before patch day or addon updates, `snap diff` to see what an update changed down to the addon setting, `undo`, and copying one addon's settings to an alt (`sv merge`). Keep the explorer as the second beat. Move "The full vision" to a wiki page. Name the personas: "for players who tinker with their UI, and for addon authors".
- Status: new

### F6 — "Switch UI profiles" overpromises: action bars are not in it, and the server may overwrite binds and macros
- Severity: major
- Area: `profile`, `snap restore`, README feature table
- Evidence: `wowlab profile --help`: "Action-bar contents and talents are not in these files (the server keeps them; not yet verified on this client), so no profile saves or restores them." `--preset macros`: "buttons may point at a macro by its place in the list: check your bars after applying". `doctor --offline` reports all three `synchronize*` settings as "client default; wowlab does not know whether that default is on". The persistence tests that decide whether an edited `bindings-cache.wtf` or `macros-cache.txt` survives login have not been run (M14-review, "Owner actions outstanding").
- Impact: A P1 player's mental model of a "raid profile" includes the bars and Edit Mode. Today wowlab can restore files the server may silently replace at login. That is the one failure that would turn an early adopter into a detractor ("undo didn't work").
- Proposal: (1) Pull the two owner persistence tests (M13-07 runbook, `M14-binds-persist.md`) forward to the launch-capture session (F2b). They validate the flagship restore promise, not only the future writers. (2) Until they are answered, the README feature row should say plainly that profiles cover "settings, Edit Mode layouts, keybinds and macros as files; action bars and talents live on the server". (3) Idea for later: the lab-addon already records action bars (schema 3). A lab-addon "restore my bars" button that uses the in-game API out of combat would close the gap legitimately.
- Status: already-ticketed: M13-07 (+ `docs/handoffs/M14-binds-persist.md`). The lab-addon restore idea is new and conflicts: ADR-0026 (the lab-addon is a recorder), so it needs a ruling.

### F7 — Waves 6 and 7 (character studio, 3D) spend the next month on a duplicated, low-reach feature
- Severity: major
- Area: plan sequencing, LAB_PLAN §17, M15-05 to M15-13, Wave 7
- Evidence: BACKLOG has M15-05 to M15-13 open (9 tickets: casc encoding/root, `casc stat/cat`, BLP, M2/skin, render plan ×2, compositor, glTF + `char model`, review), plus Wave 7, sketched in §17.8 as a vendored ~955 KB three.js and an optional "Stand pose … an M of its own". §17.7 lists about 30 `[verify]` items, including "whether the tables describe how Forever's playable races look" and "whether Forever has transmog in game at all". wow.export already exports models as OBJ and glTF for retail and Classic ([GitHub](https://github.com/Kruithne/wow.export)). M15-02's Wowhead dressing-room link, already on `main`, serves the "see my character in its gear" job. README line 245: "today's promise still holds: wowlab does not read `Data/`. The README's wording changes when the first reader ships."
- Impact: It costs about two waves of agent time and owner review during the only launch window. It serves P5 and P6, the smallest personas. It retires one of the simplest trust sentences on the first screen ("never touches `Data/`" becomes "reads `Data/`, read-only"), which strangers will have to re-evaluate. And a stranger gains nothing from it that they cannot get elsewhere.
- Proposal: Finish the in-flight M15-05 if it is close. Then defer M15-06 to M15-13 and all of Wave 7 to the post-gate, personal-lab track, moved word for word to a `docs/handoffs/M15-deferred.md` as §14.8 did for M12. If the owner wants it for themself (a legitimate P6 job), run it explicitly as personal-lab work that does not ship in v0.2 and does not change the README's promise.
- Status: already-ticketed: M15-05 … M15-13 (recommend defer); in-ideas: character-studio

### F8 — Output buries the answer under caveats, and the default character pick can be the useless one
- Severity: major
- Area: `binds coach`, `macros check`, `char gear` (CLI text output)
- Evidence: In sandbox `product` (overlay macos-70425), `wowlab binds coach` with no flags auto-picked `Labchard-Labrealmg … (schema 1) (written last)` and returned **"Cannot judge: 3"** for every rule, although a schema-3 record (`Labchard-Labrealme`) exists, with an equal mtime in the sandbox. With `--character Labchard-Labrealme`, the useful part is 8 lines; the rest is about 2.6 kB of fixed caveats ("Key costs: the shipped default table, an opinion that assumes a QWERTY keyboard…", three paragraphs about the combat log). `wowlab macros check` without `--character` gave "Findings: 0, Cannot judge: 2". `char gear` ends with five lines of "[verify]" and "community documentation" notes.
- Impact: A first-time P1/P4 user sees "Cannot judge" or a wall of hedging and concludes the tool does not work. The honesty is admirable, but it belongs in `--explain` and the docs, not on every run. `[verify]` is an internal project marker and should not appear in user output.
- Proposal: (1) When choosing by default, prefer the newest schema and then the newest mtime, and say why. For `binds coach`, prefer the record whose save time is closest to the log's. (2) Print at most two caveat lines by default and put the rest behind `--explain` or `--notes`. (3) Replace `[verify]` in user-facing text with "(not yet confirmed on this client)", once per report.
- Status: new

### F9 — The analysis features need three setup steps the first run never mentions
- Severity: minor
- Area: `doctor` first run, onboarding
- Evidence: The `doctor --offline` first-run output above ends with snapshot and undo instructions. It never mentions that `char *`, `macros check` (spellbook), `binds coach` and the Character tab need `wowlab addon install lab`, a login and logout per character, and `/combatlog` turned on. Those prerequisites only surface as "Cannot judge … install or update the lab-addon" inside individual reports.
- Impact: The novel features (P4) are effectively hidden. A user who runs the three README commands never reaches them.
- Proposal: Add a "Next steps" block to `doctor`: lab-addon installed? (yes/no), characters recorded (n), combat log present (yes/no), each with the one command or in-game action that fixes it. Mirror it as a checklist card on the `ui` home tab.
- Status: new

### F10 — The launch-week job is "which of my addons will break?", and wowlab is one comparison away from answering it
- Severity: idea (high value)
- Area: `addons list`; in-ideas: addon-audit
- Evidence: `wowlab addons list` printed `DBM-Brawlers … TOC declares Interface 120100` and `DBM-Challenges … TOC declares Interface 50504, 120100`. Discovery already knows the client's interface number (the lab-addon template is filled from it). The list does not compare the two, so it never says "out of date for this client (16001)".
- Impact: On 2026-11-04 every P2 player will hit addons that are "out of date" or broken. A local, read-only `wowlab addons check` (TOC interface against the client, missing dependencies, an addon folder with no TOC, SavedVariables that failed to parse) is the one feature with a news hook in launch week. It needs no network and no new invariant.
- Proposal: Pull the cheapest slice of **addon-audit** (TOC interface against the client, plus missing `## Dependencies`) into v0.2 as an S ticket. Leave the API-removal scan (it needs api-types) for later.
- Status: in-ideas: addon-audit (also sv-health)

### F11 — The first command a player runs talks to the network by default
- Severity: minor
- Area: `doctor`, README trust model
- Evidence: README line 28: "`wowlab doctor` downloads its list of game versions (`wowlab doctor --offline` skips that). `db2` and `looks` … have no `--offline`." The brief (verified by another seat) notes that `doctor` without `--offline` contacted wago.tools. The README's own first three commands use bare `wowlab doctor`.
- Impact: For a product whose pitch is "stays on your machine", the first command making a request is a trust papercut, even though what it sends is harmless. Privacy-minded P1/P3 users notice it.
- Proposal: Make `doctor` offline by default, with `--check-versions` to opt in. Give `db2` and `looks` an `--offline` that uses only the cache.
- Status: new

### F12 — Retail, the larger audience and the owner's "main characters", has never been captured
- Severity: major
- Area: target clients (LAB_PLAN §2), README line 206
- Evidence: README: "so wowlab works the same on retail and on *World of Warcraft: Forever*". LAB_PLAN §2 lists retail as a target ("The owner's main characters"). `ls lab/core/tests/fixtures/` gives only `macos`, `macos-70058`, `macos-70425`, `macos-70450` and `wago`. DATA_SOURCES line 190: the Forever account tree "is not the retail `<Realm>/<Character>/`". My constructed probe (sandbox `product-retail`, folder renamed to `_retail_`, product `wow`, version `12.0.5.66000`) ran `doctor`, `char list` and `explain` cleanly, but it proves only that discovery is not hard-coded. It does not prove retail's layout or formats.
- Impact: Retail is the largest single pool of P1 players, and the README claim is unverified. After Forever's launch the Forever audience is a subset of subscribers, and every one of them also has retail access.
- Proposal: Add one owner retail capture (M10-03 runbook) and a `macos-retail` fixture root in the same launch-capture session as F2b. Until then, the README should say "built and tested on Forever; retail expected to work, not yet tested".
- Status: new

### F13 — The gate measures the wrong thing for a CLI, and "5 issues" will be hard to read
- Severity: minor
- Area: M13-13, LAB_PLAN §15.4
- Evidence: §15.4 counts release-asset downloads and distinct non-owner issue/discussion authors. A source install (`git clone`, `uv run`) or a PyPI install (F1) adds nothing to `download_count`, and `gh api repos/sandgraal/wowlab/traffic/views` is not readable from here (HTTP 403).
- Impact: The gate can under-count real use (clones, PyPI) and over-count noise (rc downloads, bots). Five issues from a CLI audience that hits nothing broken is a high bar for a tool that works.
- Proposal: Keep the thresholds but add two inputs: the owner's GitHub Insights traffic and clones (owner-visible, still no telemetry) and PyPI downloads if F1 lands. Also count a Discussions "I use it" reply as a discussion author, which the current definition already allows. Record all of it in `M13-gate.md`.
- Status: already-ticketed: M13-13 (amend)

### F14 — Of the modify road, pull forward only the owner tests; the model-assisted step comes after users
- Severity: idea
- Area: LAB_PLAN §16.5 (F1 to F5), ADR-0032
- Evidence: M14-review recommended "the road to modify (§16.5 F1 to F3)" first. The owner picked character-studio instead (LAB_PLAN §17). F1 is two owner-run play-session tests, and nothing writes those files before their answers are in.
- Impact: The persistence tests are cheap and protect the shipped restore promise (F6). The writers, the proposal validator and `char advise` are genuinely novel for P4 (no incumbent does "fix my binds from my combat log, with undo"). But they serve a persona that does not exist yet in the user base.
- Proposal: Order after v0.2: (1) persistence tests, now; (2) `binds coach --apply` and `macros fix` through `guard` if the tests pass; (3) `char advise` stage one; (4) stage two (`--send`) only after the gate passes, since it changes the README's first-screen promise.
- Status: already-ticketed: M13-07; in-ideas: keybind-coach, macro-doctor

### F15 — Process weight is now far larger than the product it carries
- Severity: minor
- Area: repository and docs (an observation about cost, not code)
- Evidence: `cat docs/*.md docs/handoffs/*.md AGENTS.md README.md | wc -w` gave **167,032 words** of docs. `lab/core/src` has 43,400 Python lines and `lab/core/tests` has 95,996. Wave 4 grew from 13 tickets at the pick to 62 headings (M13-review, "Effort against the plan"). About 300 PRs since 2026-09-20.
- Impact: The machine builds fast. What is scarce is the owner's attention (reviews, captures, decisions), and every wave spends it on internal process and on the owner's own interests rather than on users. Outside contributors (P3) face a constitution, eight docs and ADR-0013's agent-only workflow before their first PR.
- Proposal: For v0.2, cap new process tickets at XS and set a "user-visible or user-requested" bar for anything else. Add a 1-page `CONTRIBUTING` fast path for human contributors (fixtures welcome, retail and Windows captures especially).
- Status: new

## Sequencing judgment

- **M13 (v0.1, gate):** The scope was right (trust model, first run, explorer, redaction). The release went out to a closing beta with no announcement. **Keep** the gate, but restart it from a launch-aligned v0.2 (F2) and add outreach (F1).
- **M14 (confirm flow, Character tab):** Done and solid. The Character tab is a nice-to-have for P4/P6. The confirm flow matters because it lets `ui` become the non-terminal front door later (F4).
- **M15/Wave 7 (character studio, 3D export):** **Defer** after M15-05 (F7). It duplicates wow.export and Wowhead, it is the largest unknown, and it lands exactly when the launch window needs the attention.

**Cut:** the README's "full vision" section from the README (move it to the wiki); M15-06 to M15-13 and Wave 7 for now; ADR-0032 stage two until after the gate.
**Keep:** snapshot, diff, undo, profiles, `sv merge`, explorer, `binds coach`, `macros check`, the trust model.
**Pull forward:** the launch-client capture plus the two persistence tests (one owner session); one human Windows run; `addons check` (addon-audit, cheapest slice); `doctor` "next steps"; a retail capture.
**Defer:** character studio, three.js viewer, `char advise --send`, db2lake, social ideas.

**The smallest release that wins 100 real users: "v0.2, launch edition", tagged within about 7 days of 2026-11-04:**
1. Verified on the launch client (new flavor folder, interface, lab-addon load) on macOS **and** one human Windows run.
2. A first screen rewritten around "snapshot before you break it, see what changed, undo" (F5), with `doctor` offline by default and a next-steps block (F9, F11).
3. `wowlab addons check`: out-of-date and missing-dependency addons for this client (F10).
4. Calmer defaults in `binds coach` and `macros check` (F8).
5. On PyPI (`uvx wowlab doctor`), announced in the three or four places where Forever UI tinkerers and addon authors gather, with a GIF (F1).

That needs no new invariant, no new ADR beyond amending ADR-0031's PyPI and gate-timing lines, and roughly one wave of S tickets.

## Top 3 changes

1. **Re-aim at the Forever launch:** freeze M15 dispatch, capture the launch client (plus the two persistence tests and a retail capture) in one owner session, and cut v0.2 within a week of 2026-11-04 with the gate clock restarted from it (F2, F6, F7, F12).
2. **Get it in front of people:** PyPI, a rewritten first screen for P1 and P3, and four targeted posts that also recruit the Windows tester (F1, F3, F5).
3. **Give launch week a reason to run it:** `wowlab addons check`, plus `doctor` next steps and quieter defaults, so that a first run ends with an answer rather than a caveat (F10, F9, F8).

## Not tested

- `wowlab ui` in a browser (other seats cover it). I reviewed it from the README and the handoffs only.
- Any real retail install. The `_retail_` probe was constructed by renaming the Forever fixtures and proves only that discovery is not hard-coded.
- `db2`, `looks` and `doctor` without `--offline` (no live network, ADR-0012).
- Windows and real macOS behaviour, server sync, and whether restored binds/macros persist after login (these need a client).
- Audience sizes are rough third-party estimates (Blizzard has published no figure since 2015). The Windows share is my estimate, not a sourced figure. GitHub traffic views returned HTTP 403 from this session.

Sources: [Shacknews](https://shacknews.com/article/150712/world-of-warcraft-forever-classic-release-date-beta-sign-up) · [Digital Citizen](https://www.digitalcitizen.life/world-of-warcraft-forever-brings-classic-plus-to-azeroth-on-november-4/) · [PCGH](https://www.pcgameshardware.de/World-of-Warcraft-Spiel-42971/News/Forever-Classic-Plus-Release-Systemanforderungen-1553608/) · [ElectroIQ](https://electroiq.com/news/world-of-warcraft-statistics/) · [ActivePlayer](https://activeplayer.io/?p=9643) · [WowUp settings backup](https://wowup.io/guide/my-addons/addon-settings) · [CurseForge addons FAQ](https://support.curseforge.com/en/support/solutions/articles/9000198422-addons-faq) · [CurseForge Forever beta path guide](https://2upskill.com/wow-forever-beta-curseforge-addon-directory-path-configuration-guide/) · [PC Gamer addon managers](https://www.pcgamer.com/best-wow-addon-client-manager/) · [warcraft.wiki.gg: Wago](https://warcraft.wiki.gg/wiki/Wago) · [Blizzard forums: server-synced macros](https://us.forums.blizzard.com/en/wow/t/stop-wow-from-server-syncing-macros/395250) · [wow.export](https://github.com/Kruithne/wow.export)
