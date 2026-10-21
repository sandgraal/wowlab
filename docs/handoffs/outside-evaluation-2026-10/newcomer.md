# First-time user

Seat: a fairly technical player who has never seen wowlab. I followed `README.md` and `docs/INSTALL.md` as written. I changed only two things: I built the wheel myself instead of downloading it, and I used a synthetic install. I installed it the release way, with `uv tool install` of a wheel built from HEAD (`969141a`) into the sandbox's own uv tool dir, so every command below is the installed `wowlab`, not `uv run`. Sandbox: `$EVAL/sandboxes/newcomer` (one flavor). I also made `$EVAL/sandboxes/newcomer-two` with a copied `_retail_` folder, for the two-flavor case. Work files are in `$EVAL/work/newcomer/` (`allhelp.txt` holds every `--help` page, `shots/` holds the UI screenshots, and `ui.js` is the headless pass).

## Timeline (UTC, 2026-10-21)

| Time | Step | Outcome |
|---|---|---|
| 17:28:09 | Read README (383 lines, 31.7 KB) and INSTALL.md (292 lines) | The first-run commands are at README line 39. The rest is vision, waves, the agent workflow and ADR links. A real reader spends about 15 min here. |
| 17:29:35 | Step 1: `uv tool install --python 3.12 <wheel>` | OK. uv warned that its bin dir is not on PATH, and neither doc mentions `uv tool update-shell`. |
| 17:29:49 | Step 2: `wowlab` | Help, exit 2. |
| 17:30:04 | Step 3: `wowlab doctor --offline` | A good intro, then 6 near-identical lines saying "wowlab does not know whether that default is on". |
| 17:30:10 | Step 4: `wowlab tree` | A useful list of my files. |
| 17:30:18 | Step 5: `wowlab explain config-cache.wtf` (the README's own example question) | "not found; nothing is at this path; no file-map row matches a name like this", exit 0. Dead end. |
| 17:30:18 | Step 6: `wowlab explain "WTF/Account/90000001#6/config-cache.wtf"` | **First useful answer about my own files.** I copied the path from step 4. |
| 17:34:01 | First snapshot (doctor in a pty, answered `y`) | OK, verified. |
| 17:34:46 | Changed Config.wtf, then `snap restore`, then `undo` | Works. Undo is a toggle, not a history. |
| 17:35:45 | `wowlab ui` (headless) | Works. The header, the copy-hints note and `ui --help` all contradict the Commands tab. |

**Steps to the first useful answer:** 6 commands on the CLI (install, a look at help, doctor, tree, one failed explain, explain with the full path). In the browser it is 4 (install, doctor, `ui`, then click a file). On Linux/Wine you also have to find your prefix path and export `WOWLAB_WOW_ROOT`, because nothing is searched by default.

## Verdict

I would recommend it to technical players on macOS who want to know what is in `WTF/`, and who want a real undo before they mess with addons or settings. The safety work shows: the snapshot, restore and undo cycle worked first time, refusals are clear, typo suggestions are good, and a wrong `--root` that points at a flavor folder says exactly which parent to pass. My single biggest problem: the tool talks to its own developers, not to players. The help, the explain output and the warnings are full of ticket ids, ADR numbers, internal model names, `[verify]` markers and Python module names. The README's two headline examples (`explain config-cache.wtf` and `sv dump SavedVariables/…`) fail exactly as written. The UI and its docs disagree about whether the page can change game files. A newcomer's first two tries fail, and then they are told three different things about safety. That is where I would give up.

## What works

- Typos are caught well: `doctr`, `snapshot`, `snap creat`, `--ofline` and `binds lst` all get "Did you mean …" and exit 2.
- Wrong-root help is excellent. `--root …/_classic_beta_` says "this looks like the flavor folder '_classic_beta_'; pass its parent …", and `--root …/.build.info` names the parent too. A missing flavor lists the flavors it found. A two-flavor install says "choose one with --flavor", exit 2.
- The first-run intro in `doctor` is plain and honest. The snapshot offer defaults to No, shows its size and time, and verifies the store afterwards.
- `snap restore` plans before it writes (with `--dry-run`, and a `[y/N]` without it). `undo` showed the exact file it would put back. Both worked on the first try.
- `wowlab tree` is the most useful command for a newcomer. It labels folders in words ("account folder", "character folder", "numeric folder (not a realm name)").

## Findings

### F1: The README's headline example (`explain config-cache.wtf`) fails, and so does every bare file name
- Severity: major
- Area: `explain`, README "Why you might want it"
- Evidence: README line 92 promises *"What is `config-cache.wtf` and can I delete it?" → `wowlab explain` answers that*. In practice:
  ```
  $ wowlab explain config-cache.wtf
  config-cache.wtf: not found; nothing is at this path
    no file-map row matches a name like this
  exit=0
  ```
  The same happens for `Config.wtf`, `bindings-cache.wtf`, `macros-cache.txt` and `SavedVariables/WowLab.lua`. Two rows match these names (`account-config-cache`, `config-wtf`), so "no file-map row matches a name like this" is false. The lookup is purely positional (`layout.py:1443`). Even when it succeeds, the answer never mentions deleting: it says `edit: yes, through wowlab's write gate only`.
- Impact: The first thing a curious player types is a file name, not a path relative to the flavor folder. This is the tool's main pitch, and it fails on the first try with a message that reads like "wowlab doesn't know this file".
- Proposal: When the path is not on disk, look the basename up in the file map and among the real files under the flavor. List the matches ("did you mean WTF/Account/90000001#6/config-cache.wtf?"), or explain them all when there are only a few. Rephrase NO_ROW_LIKE so it does not claim there is no such row. Add a "Can I delete it?" line, or change the README's question. `explain` should exit non-zero for "not found" (see F9).
- Status: new

### F2: The browser page, `ui --help` and INSTALL.md all contradict the Commands tab about changing game files
- Severity: major
- Area: `wowlab ui` (header, Explorer hints), `ui --help`, `docs/INSTALL.md`
- Evidence:
  - The page header (`page.html:20`) says "The explorer only reads, and nothing on this page changes your game files; the Commands tab can save to wowlab's own data". Yet the Commands tab lists `undo`, `snap restore`, `sv merge`, `profile apply`, `addon install` and `addon remove` (screenshot `$EVAL/work/newcomer/shots/04-commands.png`). The tab's own intro first says "none of them touches the game folder", then two sentences later "the commands that change your game files go through the same write gate".
  - The Explorer hint box (`page.js:572`) says "Run them yourself in a terminal; this page never runs them."
  - `wowlab ui --help` says "Commands that change your game files … are not offered in this version."
  - INSTALL.md:258–263 says "Nothing that changes a game file is, in this version."
  - The README (lines 60, 107) says they are offered behind Confirm.
- Impact: A newcomer deciding whether the page is safe to click around in reads four different answers. For a tool whose selling point is trust, that is the worst kind of inconsistency.
- Proposal: Use one sentence everywhere: "Reading never changes anything. Six commands can change game files; each shows a plan and runs only when you press Confirm, after a snapshot." Fix the header, the Commands intro, the hint note, `ui --help` and INSTALL.md together. Add a test that pins the sentence across all of them.
- Status: partly ticketed: M13-76 (`ui --help` only). The page header, the page.js note and INSTALL.md are new.

### F3: Developer jargon and internal references leak into player-facing output
- Severity: major
- Area: `--help` for many commands, `explain`, `macros check`, `doctor`
- Evidence (from `$EVAL/work/newcomer/allhelp.txt` and live runs):
  - Help: `(ADR-0029)` (ui), `(M13-09)` (doctor), `guard.undo(expected_id=...)` (undo), `JSON: the manifest (snapshot.Manifest)`. 47 help pages end in `JSON: TreeReport`, `JSON: DoctorReport` and so on.
  - `explain Data/`: "Editing anything under `Data/` is out of scope permanently (LAB_PLAN L7)".
  - `explain WTF/Config.wtf`: `tier: A: ordinary addon-user behaviour` and `read by: \`wtfconfig\`` (Python module names).
  - `written by: Agent`. The README spends a whole section on AI "agents" building wowlab, so I read this as an AI agent; it means the Battle.net agent.
  - `macros check`: "whether it counts characters or bytes is [verify]", "In a real capture (docs/LAB_FORMATS.md, 2026-10-16) …".
  - `char list`: "(and a deleted one [verify])". `binds coach`: "schema 1 has neither … schema 3 is needed".
  - Store wording: "transactions in the journal", "26 objects and 1 manifest", snapshot labels like `"guard pre-write: undo: restore 2026…"`.
- Impact: Players cannot tell what matters. `[verify]` reads as an unfinished program. It makes a careful tool look like a dev build.
- Proposal: Write a style rule for user-facing strings, with a lint test: no ADR, ticket, L# or doc-path references, no `[verify]` (say "not yet confirmed"), no module or model names in text output, and `--json` help in one shared footer. Rename `tier` to something a player understands, or drop it from the text output. Say "Battle.net launcher" instead of "Agent". Write "safety snapshot before restore" instead of "guard pre-write".
- Status: new

### F4: The README's `sv dump` example fails with a raw Python error
- Severity: major
- Area: `sv dump`, README "See it in action"
- Evidence:
  ```
  $ wowlab sv dump SavedVariables/DBM-StatusBarTimers.lua
  wowlab: [Errno 2] No such file or directory: '…/World of Warcraft/_classic_beta_/SavedVariables/DBM-StatusBarTimers.lua'
  exit=1
  $ wowlab sv dump DBM-StatusBarTimers
  wowlab: [Errno 2] No such file or directory: …
  ```
  It only works with the full `WTF/Account/90000001#6/SavedVariables/…` path. `sv list` already knows the addon name (`account  DBM-StatusBarTimers  3,680 bytes  WTF/Account/…`).
- Impact: Copying from the README fails, and the user sees an errno instead of advice.
- Proposal: Accept an addon name, or a path relative to the account folder, and resolve it via `sv list` (ask with `--account`/`--character` if it is ambiguous). Replace `[Errno 2]` with "no SavedVariables file there; `wowlab sv list` shows them". Run the README examples as a test.
- Status: new

### F5: Every command errors with "[Errno 32] Broken pipe" and exit 1 when piped to `head`
- Severity: minor
- Area: CLI (all text output)
- Evidence: `wowlab tree | head -2`, `wowlab cvar list | head -2`, `wowlab sv dump … | head -3` and `wowlab snap show 20261021T173401 | head -3` all print `wowlab: [Errno 32] Broken pipe`, and `${PIPESTATUS[0]}` is 1.
- Impact: `| head` and `| less` (quit early) are the first things a terminal user does with long output. A raw errno looks like a crash, and scripts see a false failure.
- Proposal: Handle BrokenPipeError and SIGPIPE in the CLI entry point: exit quietly, with 0 or 141 as conventional. Add one test.
- Status: new

### F6: `doctor` repeats six near-identical "does not know" sync lines on every run
- Severity: minor
- Area: `doctor`
- Evidence:
  ```
  Server sync (when on, the server can replace local binds, macros or settings at login; which other *-cache files it replaces is not known):
    WTF/Config.wtf: synchronizeBindings not set (client default; wowlab does not know whether that default is on)
    … ×6 per flavor (×12 with two flavors)
  ```
- Impact: This is the biggest block in the first command a newcomer runs, and all it says is "unknown". It is alarming ("the server can replace…") and gives no action to take.
- Proposal: Fold it into one line when nothing is set: "Server sync: not set in your files (the game's default applies; with it on, the server can replace binds, macros and settings at login)." Show per-file detail only with `--verbose`, or when values differ.
- Status: new

### F7: When no install is found, doctor still prints the "First run" intro and "To undo" around the error
- Severity: minor
- Area: `doctor` with a wrong `--root`, or no install
- Evidence: `wowlab doctor --offline --root /nope/World` prints "First run: wowlab has no snapshot of this install yet" and the full six-bullet intro. Only then comes "Install: /nope/World (from argument) does not exist", then the store and journal block, then "To undo: this run changed nothing…". Exit 1.
- Impact: The one line that matters is buried in the middle. "This install" does not exist.
- Proposal: When discovery fails, print the problem and the how-to-point-at-it block first and stop. Skip the first-run intro and the undo footer.
- Status: new

### F8: `undo` is a one-step toggle, not a history, and has no `--dry-run`
- Severity: minor
- Area: `undo`, README "Undo, for real"
- Evidence: After `snap restore` and then `undo`, the CLI says "This undo is journaled too: `wowlab undo` again reverses it". Running `undo` a second time would redo the restore. `wowlab undo --dry-run` gives "No such option: --dry-run", exit 2. The README tip says "most of them also take `--dry-run`". `undo --help` still says it undoes "(a restore, or an earlier undo)" and does not mention profile apply, sv merge or addon install.
- Impact: Anyone used to Ctrl-Z expects repeated undo to step further back. Here it ping-pongs, and you have to know `snap restore <older id>` to go back two changes.
- Proposal: Say plainly in the README and in `undo` output that undo reverses only the most recent change, and that undoing twice redoes it. Point to `snap list` and `snap restore` for older states. Add `--dry-run` for parity. Fix the help text.
- Status: new (help text: new)

### F9: Exit codes do not follow the README's contract in user-visible cases
- Severity: minor
- Area: CLI
- Evidence: The README says `0 ok, 1 error, 2 usage, 3 refused`.
  - `explain config-cache.wtf` (not found) exits 0, and so does `explain ../../etc/passwd` ("not inside the install root").
  - Answering N, or a closed stdin, at `snap restore` or `undo` exits 1 ("Nothing was changed.").
  - `wowlab` with no arguments exits 2.
  - `doctor` with closed stdin silently skips the question, while `snap restore` with closed stdin prompts and reads EOF.
- Impact: Scripts cannot tell "user said no" from "it broke". A not-found explain looks like a success.
- Proposal: Document "declined" as its own outcome (exit 0 with a message, or a dedicated code). Make `explain` exit 1 for not-found and outside-root. Treat a non-TTY stdin the same way everywhere: no prompt, print the `--yes` command.
- Status: new

### F10: `wowlab ui` takes no `--root` or `--flavor`, unlike every other command
- Severity: minor
- Area: `ui`
- Evidence: `wowlab ui --root "$WOWLAB_WOW_ROOT"` gives "No such option: --root", exit 2. The docs admit it (README:56, INSTALL.md).
- Impact: The second command the README tells you to run is the only one that can't take the flag `doctor` just taught you. A Windows user has to `setx` and open a new terminal.
- Proposal: Add `--root` (and `--flavor` to preselect).
- Status: new

### F11: Two-flavor installs (retail plus Classic, the common case) need `--flavor` on nearly every command, and `explain` gives a misleading answer
- Severity: minor
- Area: `--flavor` handling
- Evidence: In `newcomer-two`, `tree`, `cvar list` and `char list` each fail with "has 2 flavors (_classic_beta_, _retail_); choose one with --flavor", exit 2. `explain WTF/Config.wtf` gives "no file-map row: no file-map row matches (with several flavors, pass --flavor …)", exit 0. There is no `WOWLAB_FLAVOR` (only `WOWLAB_WOW_ROOT` and `WOWLAB_INTERFACE` exist in `src`). `--flavor _classic_beta_/` (what shell tab-completion produces) and `--flavor classic_beta` are both rejected.
- Impact: Every command gets longer for the most typical player setup, and `explain` again claims there is no row.
- Proposal: Add a `WOWLAB_FLAVOR` env var. Accept a trailing slash and an unambiguous name without underscores, or the product code. Have `explain` resolve the path in each flavor and show the rows, or at least not say "no file-map row".
- Status: new

### F12: Snapshot ids are hard to type and the readable part is not unique
- Severity: minor
- Area: `snap list`, `snap restore`, `snap show`
- Evidence: Ids look like `20261021T173401.947413Z-832e0860`. After one restore and one undo, two snapshots share the suffix `-832e0860`, and `snap show 832e0860` gives "no snapshot matches". `latest` and `last` are not accepted. `20261` became ambiguous after one restore.
- Impact: Restoring the first-run snapshot means copying a 32-character id.
- Proposal: Accept `latest`, `first-run` and `~1`-style relative refs, and match on the suffix as well as the prefix. Have `snap list` number its rows and accept the number.
- Status: new

### F13: The README is too long for its job, and the first-run path competes with roadmap material
- Severity: minor
- Area: README
- Evidence: 383 lines. The "what the browser explorer runs" details block appears twice, nearly word for word (lines 64–71 and 113–120). The README then covers waves 1–7, a 30-day "may freeze the public side" gate (line 234), a "Full vision" of about 30 ideas, how agents build it, and "This repository previously held a different project". The release banner says v0.1.0, but most rows say 🆕 "on main", and `wowlab --version` on main also prints `0.1.0`.
- Impact: A newcomer cannot tell what they will get from the release. The freeze note and "previously a different project" read as warning signs. A bug report from a main build says 0.1.0.
- Proposal: Cut the README to: what it does, the safety rules, install, the first three commands with real expected output, and links. Move waves, vision and process to the wiki or docs. Bump main to `0.2.0.dev0` so `--version` tells release and main apart.
- Status: new

### F14: Linux/Wine users get no help finding their install, and uv's PATH step is not documented
- Severity: minor
- Area: `doctor`, `docs/INSTALL.md`
- Evidence: With no `WOWLAB_WOW_ROOT`, `doctor` says "no install found at the default locations", and other commands say "(searched: nothing on this platform); pass the install folder" (pass it how? `--root` is not named). INSTALL.md says Linux means Wine, Lutris or Proton, but gives no typical prefix paths. After `uv tool install`, uv warned that its bin dir was not on PATH (`uv tool update-shell`), and the docs never mention this. My sandbox moved the dir, but the same warning shows up on stock systems where `~/.local/bin` is not on PATH.
- Impact: Linux users have to find their own way, and "wowlab: command not found" right after an install that reported success puts people off.
- Proposal: Name `--root` in the short error. List common Wine, Lutris and Steam-Proton prefix locations in INSTALL.md as places to look (a doc change only; a search order in code is optional and must not hard-code flavors, per L6). Add "if `wowlab` is not found, run `uv tool update-shell` and open a new terminal" next to the install line.
- Status: new

### F15: Command list descriptions are cut at 80 columns, and look-alike groups confuse
- Severity: minor
- Area: `wowlab --help`, the group helps, the UI Commands list
- Evidence: `COLUMNS=200 wowlab binds --help` still shows "coach  Coach your key bindings from a combat log (the newest, or...". The groups `addons` and `addon` sit next to each other, and `wowlab addon list` gives "No such command 'list'." with no "did you mean `addons list`". `binds list` and `char binds`, and `macros list` and `char macros`, overlap without saying how they differ. `install` sounds like it installs something; it only shows the install. `-h` is rejected. In the UI, the Commands tab is a bare list of 45 names with no descriptions.
- Impact: Users find commands by scanning help, and here help hides the half of each line that explains it.
- Proposal: Write a first help sentence that fits in about 60 characters for every command. Accept `-h`. Have `addon list` suggest `addons list`. Show the one-line description under each name in the UI list.
- Status: new

### F16: "won't touch" badges sit on things wowlab reads
- Severity: idea
- Area: `wowlab ui` Explorer badges
- Evidence: `Logs/` and `.build.info` carry the "wowlab won't touch" badge, but `binds coach` reads `Logs/` and `doctor` reads `.build.info`. The tooltip text reads "wowlab won't touch: no; wowlab never writes it".
- Impact: A newcomer reads "won't touch" as "doesn't read either", and then wonders why `binds coach` reads the logs.
- Proposal: Use "read-only to wowlab" or "wowlab never changes it", and drop the doubled "won't touch: no".
- Status: new

## Top 3 changes

1. **Make the first two things a newcomer types work** (F1, F4). `explain <bare name>` and `sv dump <addon>` should resolve by name, the README examples should run as tests, and no raw `[Errno …]` should reach a user.
2. **One true sentence about what the browser page can change** (F2), repeated the same way in the header, the Commands intro, the hint note, `ui --help`, INSTALL.md and the README.
3. **Run a "player voice" pass over all output** (F3, F6, F7, F15): no ADR or ticket ids, `[verify]`, module or model names, or "transactions", "objects" and "manifests" in text mode. Collapse the sync noise, and lead with the actual problem.

## Not tested

- The real release download, `SHA256SUMS` checking and Windows/PowerShell instructions: no network release and no Windows here. I built the wheel locally from HEAD instead.
- `doctor` without `--offline`, `db2` and `looks`: these use the network, which ADR-0012 and the brief forbid here.
- A real install at the default macOS or Windows location: Linux has no defaults, so I always used `WOWLAB_WOW_ROOT`.
- The client-running refusal: the process check always says "not running" here.
- The wiki: it is outside the repository and offline. The README and INSTALL.md send newcomers there for the step-by-step first run, so it may cover some of these gaps.
- Opening the browser automatically: there is no browser here. I used the printed URL with headless Chromium. I did not test the Confirm flow in the UI as a newcomer; other seats covered the UI in depth.
