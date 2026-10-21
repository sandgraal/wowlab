# Technical writer

## Verdict

The prose is better than most open-source tools. Sentences are plain and concrete, every promise is hedged honestly, and `doctor`'s first-run and no-install messages are the best writing in the project. I would recommend wowlab to a careful, technical player who reads the wiki rather than the repository. I would not hand the repository's own docs to a player. The single biggest problem is that **each fact is written by hand in five to eight places, and the copies have drifted**. The network promise, the list of commands that change the install, what the UI's Commands tab offers, and what `undo` reverses all say different things depending on where you read them. Some copies are flatly wrong about `main`: `wowlab ui --help`, `docs/INSTALL.md` and the wiki's Home page. The second problem is audience. Ticket ids, ADR numbers, `[verify]` markers, module names and spec cross-references reach players through `--help` and `wowlab explain`. The README also carries the project's governance and a 40-item idea menu next to the install line.

## What works

- `wowlab doctor` with a bad `WOWLAB_WOW_ROOT` says what is wrong, where to find the folder (the Battle.net "Show in Finder" step and Cmd-Shift-. for hidden files), and the exact command for each shell. That is model error copy.
- The first-run intro printed by `doctor --offline` matches `docs/INSTALL.md` "Your first run" step for step. The "To undo:" closing line is honest ("this run changed nothing … nothing to undo").
- The house style is short, mostly concrete words, with no marketing fluff below the README's emoji. Statements are scoped ("not yet verified on this client", "unconfirmed") instead of overclaimed.
- `docs/LAB_FILE_MAP.md` tables are generated from `filemap.toml`, with a drift test (`LAB_FILE_MAP.md:19-25`). The wiki's CLI Reference is generated from the code. This is the right mechanism, and it should be extended (see Top 3).
- The wiki (cloned, HEAD `0129e9e`, 2026-10-21) is a well-organised player guide: a sidebar split into Start, Use it and Reference, task-first tables on Home, and a player-pitched Glossary. On the UI it is more current than `docs/INSTALL.md`.

## Findings

### F1 — `wowlab ui --help` and `docs/INSTALL.md` say the UI does not offer write commands; it offers all six
- Severity: major
- Area: `wowlab ui --help`, `docs/INSTALL.md` "The explorer in your browser", wiki `CLI-Reference.md`
- Evidence:
  - `wowlab ui --help`: "Commands that change your game files, and the other commands that take --yes, are not offered in this version." (`cli.py:9899-9901`)
  - `docs/INSTALL.md:258-262`: "Only the commands that read, or that save to wowlab's own data folder … are offered. Nothing that changes a game file is, in this version." The same section contradicts this at lines 233-236 ("runs for real, through the same write gate, only when you press Confirm").
  - Running server, `GET /api/commands?token=…`: `undo | confirm: declined`, `sv merge | confirm: declined`, `addon install | confirm: dry_run`, `addon remove | confirm: dry_run`, `snap restore | confirm: dry_run`, `profile apply | confirm: dry_run`. Only `ui` has `excluded`.
  - `INSTALL.md`'s "What to look for" list has no Character tab and no Looks tab. The wiki's generated `CLI-Reference.md:85` repeats the stale help verbatim.
- Impact: A player deciding whether to trust the browser view is told it cannot write, then finds a Confirm button that does write. That is the worst direction for a safety doc to be wrong in.
- Proposal: Rewrite the `ui_command` docstring as M13-76 says. Also rewrite `INSTALL.md`'s UI section, or better, cut it to three lines and link the wiki's "The local UI" section, which is correct.
- Status: already-ticketed: M13-76 (help text and wiki CLI Reference only). The `docs/INSTALL.md` half is new.

### F2 — The network promise is hand-written in eight places, and they disagree
- Severity: major
- Area: README, `docs/INSTALL.md`, `doctor` first-run intro, wiki Home, FAQ, Game-Data and Safety pages
- Evidence:
  - README:28 and :183: wago.tools for `doctor`, plus `db2` and `looks` sending the version string, which "have no `--offline`", plus the dressing-room link carrying race, body type, class, level and item and bonus ids.
  - `INSTALL.md:27-34`: says nothing about the dressing-room link.
  - `wowlab doctor --offline` intro: "The only network traffic is to wago.tools … With --offline, as now, this check sends no request at all." It says nothing about `db2`, `looks` or the link.
  - Wiki `Home.md`: "The only network access is downloading public game tables from wago.tools, and you can skip that with `--offline`." This is wrong: `db2` and `looks` have no `--offline`, and `doctor --offline` skips the version list, not tables.
  - Wiki `Game-Data.md:29`: "Downloading these tables is the *only* thing wowlab does on the network." The next sentence names a second network access (`doctor`).
  - Wiki `FAQ.md:45` and `Safety-and-Guarantees.md:50` omit the link.
- Impact: This is the project's headline trust claim ("Local only, uploads nothing" badge). A privacy-minded player who reads the wiki believes `--offline` makes everything local, and does not learn that clicking "See it dressed" sends their gear to Wowhead.
- Proposal: Write one canonical "What leaves your machine" block with three rows: `doctor` version list, `db2`/`looks` tables with the version string, and the dressing-room link with its payload. Put it between generated markers, as `LAB_FILE_MAP.md` already does, and include it in README, INSTALL, the wiki Safety page and the `doctor` intro. Add a test that fails when any copy differs. Other pages link to it instead of paraphrasing it. (LAB_PLAN §15, the ADR-0031 wording.)
- Status: new

### F3 — The README's worked examples fail when typed as written
- Severity: major
- Area: README "Why you might want it" and "See it in action"; `explain`; `sv dump`
- Evidence (sandbox `writer`):
  - `wowlab sv dump SavedVariables/DBM-StatusBarTimers.lua` prints `wowlab: [Errno 2] No such file or directory: '…/_classic_beta_/SavedVariables/DBM-StatusBarTimers.lua'` and exits 1. A bare `DBM-StatusBarTimers.lua` fails the same way, although `sv merge` accepts a bare name ("a name (WowLab.lua), looked up in …").
  - The README's lead question, "What is `config-cache.wtf` and can I delete it?", answered with `wowlab explain config-cache.wtf`, gives `config-cache.wtf: not found; nothing is at this path / no file-map row matches a name like this` and **exits 0**. Only the full `WTF/Account/90000001#6/config-cache.wtf` works, and even then the output has an `edit:` line but never says whether deleting the file is safe.
  - The README's `explain` sample uses the literal `<ACCOUNT>` placeholder and silently drops the `file-map row`, `on disk`, snapshot and `hint` lines that the real output prints.
- Impact: The first thing a curious player copies from the README fails. The pitch question ("can I delete it?") is not one the tool answers.
- Proposal: (a) Let `explain` and `sv dump` resolve a bare file name the way `sv merge` does, listing the candidates when several match. (b) Add a "delete:" line to `explain`, or change the README question to one `explain` answers. (c) Run every fenced README and INSTALL example against the fixture sandbox in CI, the way `tests/scripts/test_readme.py` already pins the first screen, and mark trimmed output with `…`.
- Status: new

### F4 — Internal vocabulary reaches players through `--help` and `wowlab explain`
- Severity: major
- Area: CLI help strings (`cli.py`), `filemap.toml` prose shown by `explain`
- Evidence:
  - From `--help` (all 61 help screens dumped to `$EVAL/work/writer/help.txt`):
    - `doctor`: "(M13-09)"
    - `ui`: "(ADR-0029)"
    - `undo`: "`guard.undo(expected_id=...)`"
    - `snap gc`: "(GC_GRACE_SECONDS, …)"
    - `looks options --sex`: "(ChrRaceXChrModel.Sex)"
    - `binds coach`, `macros check`, `char binds`, `char macros`: "(schema 3)"
    - `macros check`: "[verify]"
    - `addon install`: "lab/addon/WowLab/ in the wowlab checkout"
    - "JSON: snapshot.Manifest", "install.Install", "gaps.GapReport" next to unqualified "DoctorReport" (35 such lines)
  - `wowlab explain Data`: `what: … out of scope permanently (LAB_PLAN L7)` / `written by: Agent; client streams into it`.
  - `wowlab explain Cache`: `what: Other client caches; … [verify]. … deleting it is ordinary troubleshooting [verify]` / `file-map row: cache-other (… not in the two rows above)`. "The two rows above" refers to a table the player never sees.
  - Every `explain` prints `tier: A: ordinary addon-user behaviour` and `read by: \`wtfconfig\``. `filemap.toml` holds 22 `verify` markers that flow into this output.
- Impact: Players meet ticket numbers, Python identifiers and an undefined "Agent" (the Battle.net agent, in a repository where "agent" otherwise means Claude). The honest "[verify]" becomes noise at the moment a player wants a clear answer.
- Proposal:
  - Split each `filemap.toml` row into a player `what` and a spec `notes`. `explain` prints `what`, renders `[verify]` as "(not yet confirmed)", and drops tier and module names unless `--verbose` is given.
  - Add a CI lint over every `help=` string and docstring that reaches `--help`. It rejects `M1\d-\d+`, `ADR-\d+`, `\[verify\]`, `LAB_PLAN`, `UPPER_SNAKE` constants and dotted module paths.
  - Spell "Battle.net app" where the map says "Agent".
- Status: new

### F5 — No separation between player docs and project docs
- Severity: major
- Area: `README.md`, `docs/`, `docs/SETUP.md`
- Evidence:
  - The README runs to 383 lines. After the first screen (pinned by M13-10) come wave governance, the "30 days … fewer than 50 downloads … freeze" rule (README:234), a ~40-entry idea menu ("The full vision"), "How it's built" with Claude Code agents, a source tree listing `.claude/`, and a Documents table that sends players to `AGENTS.md` "the constitution", `BACKLOG.md` and `DECISIONS.md`.
  - `INSTALL.md`, the one player page in `docs/`, cites `docs/handoffs/M13-03.md` (line 57) and "the `DoctorReport` model" (line 166). It sits beside `BACKLOG.md` and `AGENT_WORKFLOW.md`.
  - `INSTALL.md:5` sends developers to `SETUP.md`. That page is the owner's macOS-arm64 machine recipe, owner-only `CLAUDE_CODE_OAUTH_TOKEN` rotation (SETUP:78-133) and Claude Code notes. A contributor on Linux or Windows gets nothing usable from it.
- Impact: A player cannot tell which pages are for them, and the README's useful length is about one screen. Contributors get an owner runbook instead of a contributor guide.
- Proposal:
  - Make the player set README (first screen plus links), INSTALL and the wiki, and nothing else.
  - Move "What's planned next", "The full vision" and the wave diagram to the wiki Roadmap.
  - Move "How it's built", the developer commands and the source tree to `CONTRIBUTING.md`.
  - Split `SETUP.md` into contributor setup (cross-platform, in CONTRIBUTING) and an owner page under `docs/handoffs/`.
  - Change the README's Documents table so it lists player pages first and labels the rest "for contributors".
- Status: new (M13-10 shipped only the first screen)

### F6 — Two contradictory "run from source" recipes; the README's needs developer tooling
- Severity: major
- Area: README "Running it from source", `docs/INSTALL.md` "Getting wowlab", wiki Getting-Started
- Evidence:
  - README:328-331 says `make setup` and offers it to players who want the 🆕 features (README:48, "To try them now, run from source").
  - `Makefile:18-25`: `make setup` sets `core.hooksPath`, runs `pre-commit install-hooks` (which needs Go, per `SETUP.md:14`), creates `.env`, and fetches selene from the network.
  - `INSTALL.md:61-66` says `uv sync --frozen`, which is enough. Wiki Getting-Started repeats `make setup`.
  - `make` is not on a stock Windows machine.
- Impact: A Windows player following the README to try `main` gets `make: command not found`. A macOS player without Go gets a failing pre-commit step and git hooks wired into a checkout they only wanted to run.
- Proposal: Use `INSTALL.md`'s two lines (`uv sync --frozen`, `uv run wowlab doctor`) in the README and the wiki. Keep `make setup` in CONTRIBUTING only.
- Status: new

### F7 — `main` and the release both report `wowlab 0.1.0`
- Severity: minor
- Area: `--version`, `lab/core/pyproject.toml:3`
- Evidence: `wowlab --version` on `main` prints `wowlab 0.1.0`. `main` has the Wave 5 confirm step, the Character tab, `char gear`, `char binds` and `char macros`, which the README says are not in v0.1.0.
- Impact: The README's 🆕 convention cannot be checked from the tool. A bug report or `doctor --redact` paste does not say which build the player ran.
- Proposal: After a release, bump to `0.2.0.dev0`, and have `--version` append "(source checkout, <short sha>)" when run from a checkout.
- Status: new

### F8 — `undo --help` understates what undo reverses
- Severity: minor
- Area: `wowlab undo --help`, wiki Command-Reference
- Evidence:
  - `undo --help`: "Undo the most recent change made through the write gate (a restore, or an earlier undo)".
  - In the sandbox, `wowlab addon install lab --yes` followed by `wowlab undo --yes` deleted the 10 created files: "Undone. This undo is journaled too".
  - `INSTALL.md:282-283` and wiki `Snapshots-and-Undo.md:59` list restore, profile apply, merge, and addon install or remove. Wiki `Command-Reference.md:71` copies the stale help.
- Impact: A player reading `--help` thinks a bad `profile apply` or `sv merge` cannot be undone. That is the safety net the whole product sells.
- Proposal: "Undo the most recent change made through the write gate (snap restore, profile apply, sv merge, addon install or remove, or an earlier undo)". Also drop the `guard.undo(...)` sentence (F4).
- Status: new

### F9 — The README repeats itself
- Severity: minor
- Area: README
- Evidence:
  - The "Exactly what the browser view runs" block (README:64-71) and "What the browser explorer runs, exactly" (README:113-120) are the same paragraph twice.
  - The six write commands are listed at README:27, :60, :69, :118 and :337, again in `INSTALL.md:16`, wiki Getting-Started and Command-Reference, and in the `doctor` intro.
  - The safety promises appear three times in the README (top bullets, the safety table, the "Where it is now" paragraph).
- Impact: The page is longer than it needs to be, and every copy is another place to drift (see F1, F2 and F8).
- Proposal: Keep one copy of each, in the place a reader first needs it, and have the others link to it. Generate the six-command list from the CLI (any leaf with `--yes` that goes through the write gate) the way the wiki CLI Reference is generated.
- Status: new

### F10 — The same thing goes by several names, and the glossary has no product terms
- Severity: minor
- Area: README, INSTALL, help, wiki, `docs/GLOSSARY.md`
- Evidence (counts, grep):
  - The UI is "browser view" (README ×6), "the explorer" (README ×3), "browser explorer" (README ×1), "local page", and "local UI" (help ×2, wiki ×6).
  - wowlab's folder is "user data directory" (help), "user data folder" (INSTALL), "wowlab's own data" (README), "data folder" and "store".
  - Prose says "body type" everywhere, but the option is `--sex` with "(ChrRaceXChrModel.Sex)".
  - `GLOSSARY.md`'s "Terms that mislead" table says **Key** "does not mean an auth credential; it is a dungeon item". The product uses "key" for keyboard keys (the Keys panel, `binds coach`'s "awkward keys", `char binds` "What each key does"), and the glossary itself defines content key, encoding key and TACT key.
  - Neither glossary defines write gate (docs), journal, profile, lab-addon, file map, tier, store or "Agent", though `explain` and `doctor` print all of them.
- Impact: Players cannot tell whether "browser view" and "local UI" are one thing, and the glossary contradicts the product's own usage of its most common word.
- Proposal:
  - Add a "wowlab's own terms" section to the glossary and pick one name each ("the browser view"; "wowlab's data folder").
  - Add `--body-type` as the documented name and keep `--sex` as an alias.
  - Rewrite the Key row to name all three senses: dungeon keystone, keyboard key, archive key.
- Status: new

### F11 — The README points players at the engineers' glossary; the two glossaries disagree
- Severity: minor
- Area: `docs/GLOSSARY.md`, wiki `Glossary.md`, README Documents table
- Evidence:
  - `GLOSSARY.md:3` says "For engineers who do not play World of Warcraft". It holds 20 `[verify]` markers and CASC internals (BLTE modes, bucket XOR).
  - README:373 lists it to players as "WoW and client terms".
  - The wiki Glossary is the right player page, but it states as fact what the source hedges. `C_Traits` is "Forever uses it too" in the wiki against "it is what Forever is reported to use" in `GLOSSARY.md:67`.
- Impact: Players land on the hard page, and the easy page overstates verification.
- Proposal: Make the README link the wiki Glossary. Have the wiki job carry the hedges ("reported", "not yet confirmed") through, and add a `check_wiki` rule that flags a dropped hedge on a glossary term.
- Status: new

### F12 — `LAB_FILE_MAP.md` has stale and self-contradicting meta-text
- Severity: minor
- Area: `docs/LAB_FILE_MAP.md`
- Evidence:
  - Line 17 says "Entries marked **[verify]** are confirmed or corrected by M10-03". M10-03 is long done, and 38 `[verify]` markers remain.
  - Line 141 says "every `*-cache*` row below carries that caveat", but the rows are above, in the table at lines 61-115.
  - "Agent" in the Written-by column is never defined.
  - README:370 presents the page to players as "Every path in an install".
- Impact: Readers cannot tell what a `[verify]` means today or how one gets resolved.
- Proposal: Replace line 17 with the live rule ("a `[verify]` is resolved by a capture with a provenance row; see `.claude/rules/docs.md`"). Fix "below" to "above". Define the Battle.net agent in a one-line legend.
- Status: new

### F13 — README and wiki disagree on whether Wave 6 code is on `main`
- Severity: minor
- Area: README "See your character in its gear"; wiki `Home.md`
- Evidence:
  - README:245 says "Wave 6 … ticketed as M15-03 onward … None of this is built yet, so today's promise still holds: wowlab does not read `Data/`. The README's wording changes when the first reader ships."
  - On `main`, M15-03 and M15-04 are ticked, `lab/core/src/wowlab_core/casc.py` exists, and `LAB_FILE_MAP.md:50` lists `casc` as the reader of `Data/data/*.idx` and archive records.
  - Wiki `Home.md:54` says "Wave 6 is under way … a read-only reader of the game's data archives (`wowlab_core.casc`) that no command shows yet". The README's diagram says "Wave 6 · picked".
- Impact: The trigger the README set for itself has passed. No command reads `Data/` yet, so the user-visible promise holds, but the sentence "none of this is built" is false on `main`.
- Proposal: "A read-only archive reader is in the library on `main`; no command uses it yet, so no wowlab command reads `Data/`." Update the diagram to "under way".
- Status: new

### F14 — Error output is uneven: raw Python errors next to excellent guidance
- Severity: minor
- Area: CLI error handling
- Evidence:
  - `wowlab sv dump nope.lua` gives `wowlab: [Errno 2] No such file or directory: '/…/_classic_beta_/nope.lua'` with no hint (try `sv list`).
  - `wowlab tree | head -40` ends with `wowlab: [Errno 32] Broken pipe`.
  - With no install, `wowlab cvar list` gives "no WoW install at the locations searched by default (searched: nothing on this platform); pass the install folder (the one that holds .build.info) or set WOWLAB_WOW_ROOT". It never names `--root`, whereas `doctor` gives the full how-to.
  - `explain` of a missing path exits 0.
- Impact: A player hitting the most common mistakes (wrong path, piping output) sees Python internals.
- Proposal:
  - Map `FileNotFoundError` to a `CliError` that names the right lister (`sv list`, `snap list`).
  - Exit quietly on SIGPIPE.
  - Make every "no install" error reuse `doctor`'s how-to block, or end with "run `wowlab doctor` for how to point wowlab at your install".
  - Document the exit code of an `explain` on a missing path.
- Status: new

### F15 — Help-screen conventions vary from command to command
- Severity: minor
- Area: `--help` across 61 screens
- Evidence:
  - Argument metavars are `{path}`, `{file}`, `{table}`, `{ID}`, `{NAME}`, `{A} {B}` and `[NAME]`. The curly braces are non-standard.
  - The trailing "JSON: X" line is module-qualified on some commands (`snapshot.Manifest`, `install.Install`, `gaps.GapReport`, `snapshot.VerifyReport`) and bare on others.
  - Six help texts open with a fragment ("Install, flavors, versions…") and others with a sentence.
  - `snap restore --paths` puts a destructive rule in seven words: "A path the snapshot covers but does not hold is deleted."
- Impact: The CLI feels assembled rather than designed, and the one sentence that deletes files is the easiest to miss.
- Proposal: Make one style pass: `PATH`/`FILE`/`ID`/`NAME` metavars, an unqualified model name, and a sentence-case first line. Give `--paths` deletion its own sentence: "If the snapshot has no file at a path you name, the file on disk is deleted."
- Status: new

### F16 — Sentences that need a second reading
- Severity: minor
- Area: README, INSTALL
- Evidence:
  - `INSTALL.md:128-132`: "With several flavors installed, if the game is running, or wowlab cannot tell, it says so first; then it lists the flavors … pressing Enter takes none. If the game is running, or wowlab cannot tell, it asks yes or no after that too." Three conditions and two orders are packed into two sentences.
  - README:58: "a link, a file the server may overwrite, and the one folder wowlab never uses get their own" (which folder?).
  - README:60: "Only two of them refuse when something changed after the plan … The others apply what is there when you confirm, after the snapshot that `wowlab undo` restores from."
  - The `doctor` report prints "0 snapshot(s) that load; none damaged" and the same "not set (client default; wowlab does not know whether that default is on)" clause six times in a row.
- Impact: These are small in isolation. Together they make the safety-relevant passages the hardest to read.
- Proposal:
  - Turn INSTALL step 4 into a three-row table (one flavor / several / game running).
  - Name the folder (`Screenshots/`?).
  - In `doctor`, print the sync caveat once under the heading and list only the setting names.
- Status: new

### F17 — Wiki examples assume a source checkout
- Severity: minor
- Area: wiki (11 pages)
- Evidence: `grep -c "uv run wowlab"` across the wiki gives 66 occurrences on 11 pages. Getting-Started tells release users "Then run commands as `wowlab …`" and two paragraphs later says "Every command below is run as `uv run wowlab …` from the checkout". `Command-Reference.md:3` opens with "Run every command as `uv run wowlab …` from the checkout."
- Impact: The release is the README's recommended install, yet every example implies a clone.
- Proposal: Write `wowlab …` throughout, with one note: "from a source checkout, prefix `uv run`". The wiki-daily prompt should carry that rule.
- Status: new

## Top 3 changes

1. **Write each user-facing fact once and generate or test the copies** (F1, F2, F8, F9, F13). Use generated blocks and drift tests, as `LAB_FILE_MAP.md` already does, for:
   - the network promise
   - the six write commands
   - what the UI offers
   - what `undo` reverses

   Docstrings feed the wiki and the UI. Fix them first, and add a test that pins README/INSTALL wording to the CLI contract.
2. **Separate the audiences and keep internal vocabulary out of player output** (F4, F5, F10, F11, F12):
   - The player set is README (first screen plus links), INSTALL and the wiki. Everything else is for contributors.
   - Split the file-map prose into player text and spec notes.
   - Lint help strings for ticket ids, ADR numbers, `[verify]` and module paths.
   - Add wowlab's own terms to the glossary.
3. **Make every documented example executable in CI** (F3, F6, F17). Run each fenced command in the README and INSTALL against the fixture sandbox, fail on non-zero exit or on output that no longer matches. Give `explain` and `sv dump` the bare-name lookup that `sv merge` already has, so the README's lead question works.

## Not tested

- **A real install, macOS or Windows:** the sandbox is Linux with scrubbed fixtures. Path wording on Windows (PowerShell hints, `%LOCALAPPDATA%`) was read, not run.
- **The UI visually:** I confirmed the command list through `/api/commands` only. Rendering, tab text and accessibility belong to the designer and a11y seats.
- **`db2`, `looks races` and `doctor` without `--offline`:** these need the network to wago.tools, which ADR-0012 and the brief forbid.
- **`make setup` (F6):** not run, because it writes git config and `.env` into the repository. The finding rests on `Makefile:18-25` and `SETUP.md:14`.
- **`uv tool install` of the v0.1.0 wheel:** not run, because it downloads from GitHub. I could not check whether `uv run wowlab` (F17) works outside a checkout for a tool install.
- **The wiki:** cloned and compared (HEAD `0129e9e`, 2026-10-21). I did not check how it renders on GitHub (Mermaid, alerts, anchors).
