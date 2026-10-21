# Outside evaluation panel, October 2026

For the owner. The conductor commissioned this on 2026-10-21 at the owner's
request. A panel of ten outside-evaluator seats, each played by an agent
with a stated persona, read the docs and used the tool hands-on. The
seats were a veteran raider, an addon author, a designer, an accessibility
auditor, a first-time user, a technical writer, a Python architect, QA, a
security and privacy engineer, and a product strategist. The panel's job was
to say what to change in the plan.

Files:
- this document is the synthesis;
- each seat's full report is in `outside-evaluation-2026-10/<seat>.md`;
- the shared brief and the sandbox builder they all ran are in
  `outside-evaluation-2026-10/_brief.md` and `_make_sandbox.sh`;
- `$EVAL` in those files is the session's scratch directory, which is not kept.

Facts from outside the repository were read by the product seat from public
web pages and `gh api`. They are not checked against a second source: the
Forever launch date, the beta close, and the download and star counts.
Confirm them before acting on the dates.

## Owner decisions (2026-10-21, in session)

These are the owner's answers to the thirteen questions in §6 of the
synthesis below. The resulting wave is `docs/LAB_PLAN.md` §18 and
`docs/BACKLOG.md` M16. The ADR changes are amendments of 2026-10-21, which the
owner accepted the same day (M16-01).

| Q | Decision | Where it lands |
|---|---|---|
| 1 Wave order | Pause M15 after M15-05 and run M16, the launch edition, first | §17.9, §18, BACKLOG M16 |
| 2 The gate | Restart the 30 days at a v0.2 verified on the launch client; thresholds unchanged | ADR-0031 amendment; M13-13 follows |
| 3 Distribution | Publish to PyPI as well, and announce before the gate | ADR-0031 amendment; M16-32 |
| 4 Restore across folders | Yes, to an explicit target only. Accounts are auto-mapped only when each side has exactly one | §18.1 item 1; M16-03T, M16-03, M16-04 |
| 5 `doctor` and the network | Offline by default; `--check-builds` opts in | M16-07 |
| 6 Player voice | `[verify]` reads "(not yet confirmed)"; caveats go behind `--notes`; a copy rule for agents | `.claude/rules/player-copy.md`; M16-15, M16-16 |
| 7 Exit codes | `explain` of a missing path exits 1 | M16-18 |
| 8 sv merge rulings | Change both: an array part merges as one value, and `--key` merges inside, with `--replace` for the old behaviour | M16-12 (amends §13.4) |
| 9 The lab-addon | Schema 5 before launch; a salted macro hash is acceptable | ADR-0026 amendment; M16-45, M16-46 |
| 10 Ideas slices | All three: addons check and sv orphans, every character folder in `char list`, and `CLICK` binds | M16-34, M16-35, M16-45, M16-46 |
| 11 Captures | Neither a retail capture nor a Windows volunteer for now; the README states the limit | M16-05 (Forever only), M16-08 |
| 12 ADR-0032 | Replace other players' names in the packet; the proposal validator becomes load-bearing | ADR-0032 and ADR-0013 amendments |
| 13 Housekeeping | Correct the CLAUDE.md size figure; fold amendments at each wave review | CLAUDE.md; §11 item 5 |

Synthesis tickets not taken into M16:
- EV-38 (outside captures) was declined (Q11).
- EV-39 is done by the ADR-0032 and ADR-0013 amendments and the dated
  §16.5 amendment in `docs/LAB_PLAN.md`.
- EV-44 and EV-45 belong to M15 and are noted in §17.9 for when it
  resumes.

The rest of this document is the synthesis lead's text, unedited.

## Synthesis

Written by the synthesis lead on 2026-10-21, against `main` at 969141a
(`docs(backlog): file M13-81 …`, #313). Ten seat reports, one verifier pass
over every blocker and major finding (plus spot checks of minors), and the
project's own plan: `docs/LAB_PLAN.md`, `docs/BACKLOG.md` (open headings,
and the full entries a finding maps to), `docs/LAB_IDEAS.md`,
`docs/DECISIONS.md`, `AGENTS.md` and `docs/handoffs/M14-review.md`.

**Nothing in this document is a decision.** The owner picks waves
(ADR-0024); every ADR change named here would be `Proposed` until the owner
accepts it; candidate tickets carry `EV-` placeholders, not milestone ids,
and become tickets only through the conductor after an owner pick. No file
under `/home/user/wowlab` was changed.

## 1. Method and limits

- **Seats.** All ten reports are present: raider, addon-author, designer,
  a11y, newcomer, writer, architect, qa, security, product. None is
  missing.
- **Sandbox.** Every seat ran against a synthetic install assembled from
  the repository's own real fixtures: the macOS captures of the Forever
  (Classic) beta, `_classic_beta_`, builds 1.60.1.69913 to 70450
  (`lab/core/tests/fixtures/macos`, overlays `macos-70058`, `macos-70425`,
  `macos-70450`), on a Linux container running as uid 0 with 4 cores.
  Some seats added **constructed** inputs, labelled in their reports:
  scale trees (60 to 402 copied addons with filler files), a 10 to 120 MB
  SavedVariables file, hostile and BOM-prefixed files, renamed flavor
  folders, a `_retail_` rename. The newcomer installed a wheel built from
  HEAD with `uv tool install`; the others used `uv run`.
- **No real install, no client, no Windows.** Nothing ran against a live
  WoW install, so login-time behaviour (server sync, whether the client
  keeps an edited file, whether the lab-addon loads after a patch) is
  untested. The client check always reports "not running" on Linux, so no
  seat saw the running-client refusal. Windows and real macOS behaviour
  (case folding, junctions, OneDrive, Defender, High Contrast, VoiceOver,
  Narrator) were not exercised; a11y used Chromium's `forced-colors`
  emulation only.
- **No live wago.tools.** Live calls are off by ADR-0012. The proxy
  answered wago.tools with HTTP 503, so `doctor` without `--offline`,
  `db2` and `looks` were seen only in their failure states, except where a
  seat seeded the cache from the recorded 70009 CSVs. The product seat
  read public GitHub counts with `gh api` and launch dates from public
  web pages; those are the only outside reads.
- **UI.** Driven headless (Chromium headless shell 1194 through
  playwright-core 1.47.2; axe-core 4.10.2 for a11y). No real assistive
  technology was available; screen-reader effects are inferred from the
  Chromium accessibility tree and live-region text lengths.
- **Timings.** Seats ran concurrently on the same 4 cores (load 3 to 6
  while `make test` ran); treat every timing as an upper bound unless the
  report says "unloaded". The verifier re-timed the architect's two
  headline measurements in a fresh sandbox.
- **Verification.** 81 findings were checked: **60 confirmed, 20 partly
  confirmed, 1 misread-spec, 0 not reproduced.** "Partly" mostly means the
  observed behaviour is real but the spec chose it, so the finding is a
  request to change the plan rather than a defect; those are labelled as
  such below. Findings the verifier did not check are marked
  *unverified* and rank lower.

## 2. Panel verdict

The panel agrees that **the safety core is real and better than the
incumbents'**, and that **almost everything a player meets around it is not
ready for players yet.** L1 held across about 400 read runs in 14 sandboxes,
from the CLI and through the UI's `/api/run` (qa). A `kill -9` in the middle
of a restore left an install that `undo` returned to its exact bytes (qa).
Every UI injection, DNS-rebinding, origin and terminal-escape probe was
refused or escaped (security). `luadata` round-trips every SavedVariables
fixture and survived 80,000 seeded mutations without a crash or a round-trip
mismatch (addon-author, qa). `make lint` and `mypy --strict` are clean
(architect). What fails is the layer that turns that core into a product.
The flavor folder is about to be renamed, and every beta snapshot will then
be refused (raider, qa). The product says three different things about
whether the browser page can change game files (seven seats). Two write
plans and one restore hint can lose data without saying so (raider, qa,
addon-author). Answers are buried under maintainer caveats and ticket ids
(seven seats). The README's own first examples fail as typed (newcomer,
writer). The write gate slows down quadratically on a real addon folder,
and the UI kills a slow confirmed run at 120 s (architect). Nobody outside
the project has downloaded v0.1.0 (product). None of this needs an
invariant to move. Most of it is S-sized and fits in the fourteen days
before the Forever launch on 2026-11-04.

- **Raider.** Would install it today for one job: snapshot before patch day,
  then `snap diff` and `snap restore --paths`. It is not ready for the rest
  of a raider's week. Snapshots cannot move to a new PC or the launch folder,
  addon action bars are invisible to the keybind views, there is no alt
  workflow, and caveats outweigh answers. Blocker F1 is confirmed; the
  verifier calls it a missing feature, not a broken one.
- **Addon author.** Would use `luadata` and `sv dump` today; they are "the
  most careful SavedVariables reader I have seen outside the game". Would
  not yet recommend `sv merge` or the lab-addon to ordinary players.
  `--take theirs` and `--key` can drop data (the spec chose both; the help
  does not say so), and the lab-addon ships research scaffolding and goes
  stale silently after a patch.
- **Designer.** Recommends the CLI and the Explorer to technical players,
  and not `wowlab ui` to ordinary players yet. The look is coherent; the
  text is written for the developer. The confirm step shows a collapsed
  JSON root, errors are shown as exit codes, and the Gaps tab prints
  "2 undefined" (confirmed bug).
- **A11y.** The CLI is the accessible path today. The page passes axe but
  fails WCAG 2.2 AA where it matters. Toggling a folder drops focus to
  `<body>`, two whole panes are `aria-live`, the tree role has no tree
  keyboard behaviour, the Confirm button precedes its plan and has a bare
  name, the focus ring is about 1.2:1 on parchment, and the page does not
  reflow at 320 px. All of these are confirmed, and none touches an
  invariant.
- **Newcomer.** Got the first useful answer after six commands. The
  README's two headline examples fail as written. The page, `ui --help`
  and INSTALL.md contradict the Commands tab about safety, and the output
  "talks to its own developers". The safety loop itself worked first time.
- **Writer.** The prose is plain and honest. Each fact is hand-written in
  five to eight places and the copies have drifted (the UI's write
  commands, the network promise, what `undo` reverses). Player docs and
  project docs are not separated.
- **Architect.** Trusts the core on a small install. The write gate's path
  walk is O(files × siblings): a no-op whole restore took 37.6 s at 8,429
  files on the verifier's run, against the UI's 120 s kill. `sv dump --json`
  is 14 times slower than the parse, for want of a one-line fix. The
  10k-line `cli.py` and an unused `db2lake` are design risks for Wave 6.
- **QA.** "The safety core held up better than anything I usually test."
  The biggest gap is the flavor rename. After it come failure-path
  messages (an interrupted change, a full disk, a closed pipe that makes a
  committed write exit 1) and a real-fixture corpus that is narrow: one
  Mac, one owner, largest SavedVariables file 44 KB.
- **Security.** Nothing shipped comes close to a remote compromise. The
  one design-stage risk is §16.5 F3's proposal validator, which as
  specified passes `/run`, `/script` and `/console` macro lines. It should
  be fixed before F3 is ticketed. Several small local-exposure gaps
  (world-readable store, `sv dump` of any path from the UI, the token in
  argv) matter only on shared machines.
- **Product.** v0.1.0 has 0 downloads and 0 stars, and the only issue is
  from a bot. The beta, the only captured flavor, closes now. The 30-day
  gate (2026-11-17) falls 13 days after a launch that will likely rename the
  folder. The roadmap meanwhile spends two waves on a 3D pipeline that
  wow.export and Wowhead already provide. Both blockers are confirmed as
  facts. Their severity is strategic judgement, not a defect.

## 3. Ranked themes

Ranking is user impact × number of seats × verification. Each theme lists
the findings it merges (`seat:Fid`), the verifier's status, the best
evidence, and where it maps. "new" means no ticket, ADR or ideas row covers
it.

### 1. Launch day breaks the core promise (game correctness, roadmap)

- **Merges:** raider:F1, qa:F1 (rename/cross-install restore);
  addon-author:F5 (lab-addon goes stale after a patch); raider:F5,
  product:F6 (server sync and persistence untested); product:F2 (gate
  window), product:F12 (no retail capture), product:F3 (no human Windows
  run).
- **Verified:** raider:F1 confirmed (both cross-install and cross-flavor);
  qa:F1 confirmed with a renamed `_classic_` folder; addon-author:F5
  confirmed; raider:F5 partly (bindings test is already a runbook);
  product:F2, F3, F6, F12 confirmed.
- **Best evidence:** after renaming `_classic_beta_` to `_classic_` in
  verify-qa, `snap restore <id> --dry-run` exits 1 with "Flavor folders are
  renamed between beta and launch; wowlab restores only into the folder a
  snapshot was taken from." The refusal is `guard._refuse_foreign_snapshot`
  (guard.py:2161-2173), whose docstring says it "is not a thing the gate
  does". **No spec section or ADR requires it**, and LAB_PLAN §2
  (lines 49-53) predicts the rename. With `.build.info` moved to
  1.60.2.71000, `doctor`, `addons list` and `char list` said nothing about a
  lab-addon TOC that still declares 16001. Launch is 2026-11-04; the beta
  closes 2026-10-21 or 22; the gate reads on 2026-11-17.
- **Maps to:** new (portability, rename, staleness). M13-07 and
  `docs/handoffs/M14-binds-persist.md` cover the macro and binding tests;
  edit-mode cache and Config sync are new. M13-02 covers the human Windows
  run. M13-13 and ADR-0031 cover the gate. Addon-audit (LAB_IDEAS)
  partly covers the interface check.

### 2. The product contradicts itself about what can change your files (docs, UI)

- **Merges:** raider:F15, designer:F15, a11y:F14, newcomer:F2, writer:F1,
  qa:F18, security:F5 (UI write commands); writer:F2, designer:F5 (copy),
  security:F11, product:F11 (network promise and `doctor` online default);
  writer:F8, newcomer:F8 (what `undo` reverses); writer:F13 (README says
  Wave 6 code is not built; `casc.py` is on `main`); security:F12
  (SECURITY.md says `--redact` has not shipped).
- **Verified:** all UI-sentence findings confirmed (a11y:F14 is
  already-ticketed, not new); writer:F2 confirmed; writer:F8 confirmed;
  newcomer:F8 partly (the single-step undo is by design); writer:F13
  confirmed; security:F12 confirmed.
- **Best evidence:** `page.html:20` says "nothing on this page changes your
  game files". The Commands intro (`page.html:62`) says "none of them
  touches the game folder", then two sentences later describes the ones
  that do. `page.js:572` says "this page never runs them". `ui --help`
  (cli.py:9899-9902) and INSTALL.md:258-262 say the write commands are not
  offered. On a running server, `GET /api/commands` offers all six behind a
  confirm shape. Wiki `Home.md` says `--offline` makes everything local,
  which is false for `db2` and `looks`. The Looks tab says "no network
  requests" next to a button that downloads from wago.tools.
- **Maps to:** M13-76 (the `ui` docstring and wiki reference only). The
  page header, Commands intro, `page.js` hint, INSTALL.md half, network
  block, `undo --help` and SECURITY.md are new.

### 3. Plans and hints that lose data without saying so (game correctness, restore)

- **Merges:** raider:F2 (the restore hint points at guard's pre-write
  copy), raider:F9 (byte-level plans for bindings, macros, CVars), qa:F2
  (a whole restore leaves files added since, and the plan is silent),
  addon-author:F2 (`--key` replaces a subtree and its plan omits removed
  keys), addon-author:F1 (`--take theirs` merges lists index by index),
  addon-author:F13 (loader notes print after the prompt), qa:F5 (a closed
  pipe makes a committed write exit 1), qa:F3 (an interrupted change is
  invisible to `doctor`), newcomer:F12 (snapshot ids).
- **Verified:** raider:F2 and F9 confirmed; qa:F2 partly (deliberate and in
  INSTALL.md:146-149; the plan silence stands, severity minor); addon-author:F2
  partly (the wholesale copy is spec, the help and plan disclosure are the
  defect); addon-author:F1 partly (follows §13.4; a plan change);
  addon-author:F13 confirmed; qa:F5 confirmed; qa:F3 and newcomer:F12
  unverified.
- **Best evidence:** after a recovery, `explain` on the recovered file
  printed `hint: wowlab snap restore <guard pre-write id> --paths …`.
  Following it restores the wiped file. `profile apply raid --dry-run`
  printed `replace …/macros-cache.txt (91 bytes)` and never said that
  macro "New" would go. `--key BarCharDB.profile --yes` removed
  `outline = true` from the target, and the plan listed nothing to remove.
  `snap restore <id> --yes | head -1` exits 1 while the journal shows the
  restore committed.
- **Maps to:** new throughout. M13-23 and LAB_PLAN.md:3597 specify "the
  latest snapshot holding the path" without considering pre-write
  snapshots: a gap in the spec, not a misreading. §13.4 governs the merge
  rulings (see §5b).

### 4. Written for maintainers: answers buried under caveats, ids and module names (docs, first-run)

- **Merges:** raider:F12, designer:F6, designer:F13, newcomer:F3,
  newcomer:F6, writer:F4, writer:F16, product:F8, a11y:F13,
  addon-author:F14; writer:F10, F11, F12 (glossary and file-map
  meta-text); designer:F14, newcomer:F15, writer:F15 (help conventions,
  truncated short help).
- **Verified:** raider:F12, designer:F6, F13, newcomer:F3, writer:F4,
  a11y:F13 and addon-author:F14 confirmed; product:F8 partly (the
  default-character half rests on a sandbox mtime tie); the rest
  unverified.
- **Best evidence:** `char binds --character 1/Sangraal-Sett` prints 27
  `[verify]` markers. `doctor --help` carries "(M13-09)" and
  "JSON: DoctorReport". `undo --help` carries `guard.undo(expected_id=...)`.
  Every `explain` prints `tier: A: ordinary addon-user behaviour` and
  `read by: \`wtfconfig\``, and `written by: Agent` means the Battle.net
  app. `char gear` is 32 lines, about 10 of them notes, including an
  absolute path into the source tree. `binds coach` lists four spells with
  "no key", then prints "Spells cast with no bind: 0".
- **Maps to:** new. No spec section requires these references in player
  output; the project's honesty rule asks for hedging, not for the literal
  `[verify]` token.

### 5. First contact fails: README examples, bare names, raw errors (first-run)

- **Merges:** newcomer:F1, newcomer:F4, writer:F3 (README examples);
  qa:F17, writer:F14, designer:F4 (raw errno, errors shown as plans);
  raider:F16, newcomer:F5 (broken pipe; also theme 3); newcomer:F9 (exit
  codes); a11y:F15, architect:F5 (`--json` errors are text only);
  newcomer:F7, qa:F12 (wrong order when there is no install); newcomer:F10,
  F11, F14 (two flavors, `ui --root`, Linux prefixes).
- **Verified:** newcomer:F1, F4, F5, writer:F3, designer:F4, raider:F16
  confirmed; architect:F5 partly (the traceback risk is only prospective);
  the rest unverified.
- **Best evidence:** `wowlab explain config-cache.wtf`, the README's lead
  question (README:92), prints "not found; nothing is at this path / no
  file-map row matches a name like this" and **exits 0**. Two rows do
  match. The README's
  `wowlab sv dump SavedVariables/DBM-StatusBarTimers.lua` (README:154)
  prints `[Errno 2] No such file or directory` and exits 1. `| head` ends
  every command with `wowlab: [Errno 32] Broken pipe`, exit 1.
  `snap show zzz --json` prints nothing on stdout.
- **Maps to:** new. §6.2's M13-29 amendment defines `explain` by path, so
  bare-name lookup is a feature. `sv merge` already resolves a bare name.

### 6. The write path and the parsers at real-install scale (architecture)

- **Merges:** architect:F1 (gate walk O(files × siblings)); architect:F3
  (`sv dump --json` slow encoder); raider:F14 (every write snapshots all of
  `Interface/`); addon-author:F12 (`sv merge` on a 43 MB file: 88 s,
  2.6 GB); qa:F7 (WTF parsers have no budget); qa:F9, security:F9 (large
  SavedVariables, about 40× memory amplification); architect:F9 (targets
  are library-level and not regression-tested); architect:F10 (0.66 s
  import floor per UI action).
- **Verified:** architect:F1 confirmed (4.3 s at 2,129 files and 37.6 s at
  8,429; superlinear); architect:F3 confirmed (24.0 s against a 1.53 s
  parse; the fix gives byte-identical output in 8.83 s instead of 18.23 s);
  the rest unverified (the inputs are constructed and labelled).
- **Best evidence:** `guard._entry` (guard.py:1188-1218) lists the parent
  directory for every path component of every file, with no memo. The UI
  kills a confirmed run at `DEFAULT_TIMEOUT_S = 120.0`
  (ui/__init__.py:153), and §16.1 tells the player to run `undo` after
  that. §13.3 already caps `profile apply` at 2,000 deletes "because the
  gate re-walks the install per delete; a gate-side listing cache is a
  follow-up". That follow-up was never ticketed, and `snap restore` has
  no cap.
- **Maps to:** new. The listing cache is named in §13.3 as an unticketed
  follow-up.

### 7. The UI's one consent moment, and accessibility (UI)

- **Merges:** designer:F1 (Gaps "2 undefined"); designer:F2, F3, F4,
  a11y:F4, a11y:F7 (the confirm flow: plan as collapsed JSON, Confirm
  before its plan and below the fold, outcome as "exit code N", errors
  labelled "Plan"); a11y:F1, F2, F3, F5, F6 (focus loss, pane-sized live
  regions, a tree role without tree keys, focus ring, reflow);
  a11y:F8 to F11, designer:F8, F11, F12 (tooltips only, forced colours,
  heading order, a 9,000 px Character column, narrow widths, an unlabelled
  command list); designer:F7, raider:F6 (the Character header shows ids);
  designer:F9, newcomer:F16 (badge colour and wording).
- **Verified:** designer:F1 confirmed (page.js:863 and :1462 both declare
  `plural`); a11y:F1 to F6 confirmed; a11y:F4 confirmed (Confirm before
  `#result-tree` in DOM order); designer:F2, F3, F7 partly (each follows a
  ticket: M13-26's generic tree, M14-02's "Confirm is offered for a run
  that changes nothing", M14-08's header list); designer:F4 confirmed
  (`ui/__init__.py:1166-1176` sets stage "plan" whatever the exit code);
  designer:F9 confirmed.
- **Best evidence:** the a11y verifier probe shows focus going from
  "Close _classic_beta_" to `BODY` after Enter. `#detail` holds 12,670
  characters of polite live region after `cvar list`. Dark gold `#e3bb55`
  is 1.20:1 on `#e4d0a2`. At 320 px, scrollWidth is 483 and the Commands
  detail pane is 40 px wide. The `snap restore` status reads "Plan, nothing
  was changed: exit code 0", with Confirm above a collapsed `"plan"[1]`.
- **Maps to:** new, except that the gear panel's live region was already
  noted against M14-09 (BACKLOG.md:1249). The Explorer and Commands panes
  were never ticketed.

### 8. Nobody can find it, and the first screen sells the wrong job (roadmap, product)

- **Merges:** product:F1 (0 downloads, no channel), product:F4 (install
  path), product:F5 (headline), product:F13 (gate measures), product:F15
  (process weight); writer:F5, F6, F7, F9, F17, newcomer:F13 (README length,
  audience split, run-from-source recipe, `0.1.0` on `main`, wiki examples
  assume a checkout); product:F7 (defer Waves 6 and 7).
- **Verified:** product:F1, F5, F7 confirmed; product:F4 partly (an
  argument against accepted ADR-0031: §5); writer:F5, F6 confirmed
  (writer:F5's "nothing usable on Linux or Windows" is overstated);
  others unverified.
- **Best evidence:** every v0.1.0 asset has `download_count` 0, the
  repository has 0 stars and `has_discussions=false`, and the only issue
  (#240) is by `github-actions[bot]`. No step in LAB_PLAN, DECISIONS,
  BACKLOG or LAB_IDEAS announces the release anywhere. The README is 383
  lines, opens on "Ever wondered what all those files … are?" and carries
  45 idea slugs. `make setup` (README:328-331) needs Go and git hooks;
  INSTALL.md's `uv sync --frozen` is enough.
- **Maps to:** new (outreach, first screen, docs split). PyPI and the gate
  date amend ADR-0031 (§5). Deferring M15 is an owner wave decision
  (ADR-0024).

### 9. The jobs raiders, alt players and addon users bring (game correctness)

- **Merges:** raider:F8, addon-author:F15, product:F10 (`addons list`
  never says what will not load: missing dependencies, interface mismatch,
  orphaned SavedVariables); raider:F4 (addon action bars invisible);
  raider:F3 (no clone-to-alt); raider:F6 (no alt overview); raider:F10,
  product:F8 (the coach pairs the newest log with the newest record and
  judges from small samples); product:F9 (setup steps never mentioned);
  raider:F11, F13, F17, addon-author:F16 (looks offline, log encounters,
  `cvar where`, account-wide merge).
- **Verified:** raider:F8 confirmed; raider:F3 confirmed; raider:F4 partly
  (the drop is spec, LAB_PLAN.md:2111-2118, and the CLI does say "an
  addon's buttons are not followed"); raider:F6 partly (spec §14.4, and
  the alt-dashboard is deferred); product:F9 confirmed; the rest
  unverified.
- **Best evidence:** both DBM TOCs declare `## RequiredDeps: DBM-Core`,
  DBM-Core is absent, and `addons list` prints only the title and
  interface. Four account SavedVariables have no addon folder and are not
  flagged. Bartender's `CLICK BT4Button13:Keybind` fails the lab-addon's
  `^[%u%d_]+$` filter (Actions.lua:388-393).
- **Maps to:** addon-audit (interface only) and sv-health in LAB_IDEAS;
  dependencies and orphans are new. Alt-dashboard (deferred, §14.8).
  keybind-coach is built without bar addons. Clone-to-alt is new (profiles
  and sv-merge are built and do not cover it).

### 10. Real-world coverage and test strategy (test/QA)

- **Merges:** addon-author:F4, qa:F14, product:F12 (corpus: 16 small
  Forever/macOS SavedVariables files; no retail, Windows or heavy addon);
  addon-author:F3 (positional `nil` and non-finite numbers refused; a spec
  change that needs a fixture); qa:F15 (no fuzz test), qa:F16 (the
  marker-deletion rule is a checkbox), qa:F13 (`make ci` red as root and
  stops at the first red stage), architect:F9 (no performance regression
  guard).
- **Verified:** addon-author:F4 confirmed (largest file 44,218 bytes, no
  `-- [` index comment anywhere); qa:F14 partly (the casc `@live` tests do
  check `Data/data` state, so a narrow L1 check exists; no sweep covers
  WTF, Interface or the read commands); addon-author:F3 partly (the
  refusals are what LAB_FORMATS §4.1 and §4.2 require; the premise that
  Retail writes them is [verify]); the rest unverified. qa:F13's root-only
  failure was reproduced independently by the architect.
- **Maps to:** M13-02 (Windows CI) and M13-81 (local test run) are adjacent;
  the rest is new and permitted by L8.

### 11. Security: rules to write before ADR-0032 is built, and local exposure (security)

- **Merges:** security:F1 (validator passes `/run`, `/script`, `/console`,
  `/click`), security:F2 (the `--send` packet, key storage, the UI path),
  security:F3, qa:F6 (`sv dump` reads any absolute path, a FIFO, a link
  out), security:F4 (UI token in argv and URL), security:F7 (store is
  world-readable), security:F6, architect:F11 (release verification,
  floating dependencies), security:F8 (capture scrub and free text),
  security:F10 (`--redact` edge cases).
- **Verified:** security:F1 confirmed (design stage; nothing built);
  security:F2 partly (key storage and the UI path stand; the third-party
  names point runs into the ADR-0019 amendment of 2026-10-17: §5);
  security:F3 partly (deliberate on the CLI; a gap in the UI); security:F7
  confirmed; architect:F11 confirmed; the rest unverified.
- **Best evidence:** §16.5 F3 lists the validator's checks (key names,
  binding commands, spell names, the 255 limit, duplicates, brackets) and
  its hostile cases, none of which is code in a macro line.
  `macrocheck.py:146-147` already recognises `/run` and `/script` as
  slash commands. Under umask 0022 the store's objects and manifests are
  `-rw-r--r--`; only `lock` and the journal are 0600.
- **Maps to:** new. ADR-0032 is accepted; these are additions within it.

### 12. Contracts and structure ahead of Wave 6 (architecture)

- **Merges:** architect:F2 (two plan protocols: JSON and a stderr
  sentinel), architect:F5, a11y:F15 (no common error base),
  architect:F6 (unversioned `--json`; `addons list --json` is 8 MB at 402
  addons), architect:F4 (a 9,977-line `cli.py` that also holds policy),
  architect:F7 (no table-access layer for §17.4; `db2lake` imported by
  nothing), architect:F8 (compositor target at risk), architect:F12
  (gate rules copied by hand), architect:F13 (spec reads as a changelog;
  CLAUDE.md's "under 40K tokens" figure is stale).
- **Verified:** architect:F7 confirmed; architect:F2 partly (the as-built
  §16.1 design, so a proposal; the M13-75 cost is overstated); architect:F4
  partly (the dependency graph already serialises the remaining Wave 6 CLI
  tickets, so a split buys no Wave 6 parallelism); the rest unverified.
- **Maps to:** new. M13-72 is the same class of problem as F6, for
  `char list`.

### 13. The lab-addon as an addon players keep (game correctness)

- **Merges:** addon-author:F6 (a chat line on every login and `/reload`),
  F7 (the `Constants.*SYSTEM_ID*` sweep runs on every trait event), F8
  (probes and a made-up control event ship to players), F9 (whole-section
  rebuilds on high-frequency events), F10 (lockstep schema; an unknown
  schema blanks the character), F11 (no `## Version` in the TOC);
  raider:F7 (macro name discarded).
- **Verified:** addon-author:F7 partly (the code matches; the crash risk
  is conjecture, and the sweep has run on Forever without a recorded
  crash); addon-author:F11 confirmed; raider:F7 misread-spec (§13.1
  requires it: §5b); the rest unverified.
- **Maps to:** new. Any change is schema 5 under §13.1's rule and needs an
  owner capture (ADR-0026).

## 4. Proposed changes to the implementation plan

Every change below keeps L1 to L8 and ADR-0023. The tickets that touch a
load-bearing file (`guard.py`, `luadata.py`) are split into a `[TEST]`
ticket for `test-writer` and an `[IMPL]` ticket, per ADR-0013. Where a
change touches an accepted ADR or a spec ruling, it is listed again in §5.

### 4(a) Edits to `docs/LAB_PLAN.md` and to the wave order

1. **§2 Target clients.** After "That is why flavor, product and build
   always come from discovery and never from a constant.", add a dated
   amendment: *"The rename is also a product requirement. A snapshot or
   profile taken of one flavor folder can be restored into another flavor
   folder of the same install, or into another install root, but only when
   the player names the target (§6.10). The launch client is captured in its
   first 48 hours (M10-03's runbook), before any release claims it works."*
2. **§6.10 `guard`.** After "`guard.undo()` restores the pre-write snapshot
   of the most recent transaction.", add:
   - *"Restore into another target. `tx.restore(snapshot_id, paths=None,
     into=Target)` maps every path the snapshot holds by its path relative
     to the flavor folder. It does this only when the caller names the
     target, and the plan names both folders. Account folders are mapped
     only when the player passes the mapping, or when each side has exactly
     one account folder. The target gets its own pre-write snapshot, and
     the allowlist, link and client checks apply to the target as to any
     write. Without `into`, a foreign snapshot is refused as now."*
     Folder names come from the manifest and from discovery, never from a
     constant (L6).
   - *"Performance: a per-transaction directory-listing memo, keyed by the
     directory's identity (dev, ino, mtime_ns) and invalidated by the
     transaction's own writes in that directory. The re-check at commit is
     unchanged. Target: a whole-restore check of 20,000 files with nothing
     to change takes at most 5 s on the reference runner."*
   - In §13.3, replace "(the gate re-walks the install per delete; a
     gate-side listing cache is a follow-up)" with a pointer to that memo,
     and revisit the 2,000 cap once the target holds.
3. **§6.9 store.** Add three points:
   - The store tree is created 0700, and objects, manifests and temp files
     0600, whatever the umask.
   - Each manifest records a `kind`: `user`, `first-run`, `profile` or
     `pre-write`.
   - `latest`, `latest-user` and `~N` resolve to snapshot ids, and a
     unique suffix matches as a prefix does today.
4. **§6.2 `explain`.** Under the M13-29 amendment, add: *"A bare name that
   is not on disk is looked up by basename among file-map rows and the
   flavor's real files. The candidates are listed, and every candidate is
   explained when there are three or fewer. A hint never offers a
   `pre-write` snapshot as the copy to restore without saying in words that
   it is the state before the player's last wowlab change."*
5. **§6.4 `luadata`.** Next to "a 50 MB SavedVariables file … parses in
   under 10 s and under 1.5 GB", add command-level targets:
   - `sv dump --json` of that file end to end, and `sv merge` with a
     one-value change, each with a time and memory figure.
   - Measured on a reference CI runner, as a ratio to a calibration loop,
     by a nightly job that is not on the PR path.
   This changes no parser behaviour.
6. **§6.5 `wtfconfig`.** Add a byte budget and a line-count budget per
   file type, refused with a position (the same pattern as `luadata`). Also
   add a leading UTF-8 BOM, kept as its own prefix token so that
   `to_bytes()` returns the input byte for byte (L4). The BOM is reported,
   never silently counted as part of line 1.
7. **§6.11 CLI.** Add a short *player-output contract* section that later
   amendments extend:
   - (a) Text output and `--help` carry no ticket id, ADR id, `L<n>`, doc
     path, module path, constant name or model class name. A test enforces
     this.
   - (b) `[verify]` renders as "(not yet confirmed)", at most once per
     report.
   - (c) The answer comes first, followed by at most one caveat line that
     applies to this result. Standing caveats print with `--notes`,
     replaced by a one-line footer ("N notes: --notes shows them").
   - (d) `BrokenPipeError` is handled once at the entry point. Exit status
     reflects the work done, so a committed write never reports failure
     because stdout closed.
   - (e) Under `--json`, an error is an `ErrorReport` on stdout as well as
     the stderr line. This is additive, so ADR-0031's "`--json` is an
     interface" holds.
   - (f) Whether `explain` of a missing path exits 0 is an owner question
     (§6).

   `--json` output is unchanged by (a) to (c).
8. **§13.4 sv-merge.** No change to the rulings without the owner (§5b),
   but add disclosure rules. `--help` says that `--key` copies a subtree
   whole and may remove keys the target holds. Every plan lists each key
   it will remove. `report.notes` print before the prompt.
9. **§15.4 The gate** (with an ADR-0031 amendment, §5). Replace "Thirty
   days after v0.1" with *"Thirty days after the first release verified on
   the launch client (v0.2)"*. Add owner-visible GitHub traffic and clones
   to the inputs, recorded and not part of the threshold.
10. **§16.1 The confirm flow.** Under the M14-03 amendment, add a
    *presentation* rule:
    - The plan is shown above Confirm, in DOM order, as lines that Python
      computes (a `plan_lines` field on each plan report, so the page
      derives nothing, as §14.5 D3 requires). The JSON tree becomes a
      collapsed "Details".
    - Confirm's accessible name says what it does.
    - A plan run that did not end with its marker is never labelled
      "Plan". An exit code is shown as words.
    - After Confirm, the page shows one outcome sentence built from the
      report (snapshot id, journal id, files changed).
    - `GcReport.dry_run` is false under `--yes`.
11. **§15.6 and §16.3, a UI accessibility rule.** Add:
    - A live region holds one sentence, never a result.
    - Focus survives a redraw.
    - A declared ARIA role brings its keyboard model, or the role is
      dropped.
    - The focus ring is at least 3:1 against every background in both
      schemes.
    - The page reflows at 320 CSS px.

    In §16.3, amend the M14-08 header line to *"the character label, level
    and class from the identity record (schema 4), spec as a name when the
    build's cached tables hold it (else the id), averages to one decimal"*.
12. **§16.5.**
    - F1 adds the edit-mode cache and `Config.wtf` sync to the two tests
      and runs them on the launch client.
    - F3 adds: *"Every macro line is classified. `/run`, `/script`,
      `/console`, and `/click` on a frame the record does not list are
      code. A proposal that adds or changes a code line is refused unless
      the line is byte-identical to one in the player's current macro.
      Labelled hostile cases cover each verb. `wowlab_core.proposal` joins
      the graders-first set (ADR-0013 amendment, owner)."*
    - F4 stage two adds three rules. The key is stored in the OS keychain,
      or in a file created 0600 in a format `luadata` refuses. `char advise
      --send` is excluded from `/api/run` until a confirm shape shows the
      exact request body. Only `https:` endpoints are accepted, with no
      redirects. Whether other players' names in macro lines are replaced
      is an owner ruling (§5).
13. **§17.4 to §17.9 (Wave 6).**
    - Before M15-09, §17.4 names the one table-access path the render plan
      uses: either `db2lake` (ADR-0028), or a `gamedata.Table` helper with
      typed columns and `by(column)` indexes, with `db2lake` left out of the
      wheel until a wave uses it.
    - §17.4 also states that geometry and pixels cross module boundaries as
      `bytes` or `memoryview` inside a Pydantic metadata model.
    - §17.5 gains a per-blend-mode cost model and the pure-Python
      techniques it relies on.
    - §17.9 gains: *"After M15-05, dispatch pauses for the launch-edition
      wave (owner pick, 2026-10-2x); M15-06 to M15-13 resume after v0.2 if
      the owner says so."*
14. **Wave order, proposed (the owner picks; ADR-0024).**

    ```
    2026-10-21 ── M15-05 (finish only if in flight) ── M15-06..M15-13 paused
               └─ M16, proposed "v0.2, the launch edition", 14 days:
                    before 2026-11-04: M13-76 widened first; then every M16 code
                                       ticket of 4(b), [TEST] twins ahead of
                                       EV-01, EV-05 and EV-24
                    2026-11-04..06:    EV-03 owner launch capture (+ persistence tests)
                    by ~2026-11-11:    v0.2 tag (+ EV-30 outreach; gate clock per §15.4 amended)
               └─ then the owner picks: resume M15; §16.5 road to modify; the M13 cleanup half-wave
    ```

    §17.8's viewer wave moves after whichever the owner picks.
15. **Outside LAB_PLAN.** These are owner or conductor edits:
    - Add a player-copy rule to `.claude/rules/` (designer:F6), so agents
      keep 7(a) to 7(c).
    - Correct CLAUDE.md's "All of `docs/` is under 40K tokens"
      (architect:F13 measured about 119k words).
    - At each wave review, fold dated amendments into the body and move the
      history to `docs/handoffs/`.

### 4(b) Candidate tickets

Milestones: **M16** is the proposed launch-edition wave. **M13 follow-ups**
is the existing cleanup lane, for XS and S fixes to shipped behaviour.
**M15** means a change inside Wave 6. **owner** marks a ticket that needs
the owner's capture or decision.

#### Launch readiness (theme 1)

**EV-01T — [TEST] `guard`: restore into a named other flavor folder or install root**
**Size:** S · **Joins:** M16 · **Sources:** raider:F1, qa:F1
- [ ] Graders (strict xfail) cover: a renamed flavor folder; another install root; account-folder mapping given and not given; refusal without `--into`; a pre-write snapshot of the target; the allowlist and link refusals applied to the target; `undo` of a transplant.
- [ ] Every input constructed and labelled; no flavor name in library-facing assertions beyond test data (L6).
- [ ] `make test-parser` and `make ci` green, reviewed by `security-reviewer` and `code-reviewer`.

**EV-01 — [IMPL] `snap restore --into-flavor/--into-root`, `profile apply --into-flavor`**
**Size:** M · **Joins:** M16 · **Depends on:** EV-01T · **Sources:** raider:F1, qa:F1
- [ ] EV-01T's graders activated by marker deletion only.
- [ ] The plan names both folders and every mapped account folder. Without the option, a foreign snapshot is refused as today, and the refusal names the new option.
- [ ] Reachable from the UI through the existing dry-run confirm shape, with no UI change.
- [ ] README, INSTALL and the wiki's snapshot page say how to restore after the launch rename. Reviewed by `security-reviewer`, `domain-reviewer` and `code-reviewer`.

**EV-02 — `snap export` and `snap import`**
**Size:** S · **Joins:** M16 · **Sources:** raider:F1
- [ ] `snap export <id> FILE` writes one archive (manifest plus objects) to a path the player names. Nothing in the install is opened for writing (L1).
- [ ] `snap import FILE` verifies every object hash before the manifest is added. A corrupt archive is refused with nothing added.
- [ ] Constructed hostile archives are covered and labelled: path traversal, a duplicate id, a hash mismatch, an oversize entry.

**EV-03 — owner: the launch-client capture session**
**Size:** S · **Joins:** M16 · **owner** · **Sources:** product:F2, F6, F12; raider:F5; addon-author:F5
- [ ] M10-03's runbook rerun on the launch client within 48 hours of 2026-11-04. It records the flavor folder, product code, interface number, the lab-addon's load, and one logout capture through `lab_capture.py` with provenance rows (L8).
- [ ] The binding and macro persistence tests (`M13-07.md`, `M14-binds-persist.md`) are run, plus edit-mode cache and `Config.wtf` sync. The answers go into LAB_FORMATS §6 and §7 as dated amendments.
- [ ] `M14-macro-place.md` is run on the same session.
- [ ] One retail capture is taken the same day, into a new fixture root.

**EV-04 — The lab-addon's staleness is visible**
**Size:** S · **Joins:** M16 · **Sources:** addon-author:F5, F11
- [ ] `doctor` compares the installed `WowLab.toc` interface with the one derived from discovery, and names `wowlab addon install lab` when they differ.
- [ ] `char list` and `char show` flag a record saved by an older client build than the installed one.
- [ ] `addon install` writes `## Version:` from the wowlab version and drops the template comment from the installed copy.
- [ ] Tests on constructed `.build.info` versions, labelled.

#### What can change your files, said once (theme 2)

The UI sentence itself is M13-76, widened (4(c)).

**EV-06 — "What leaves your machine": one generated block**
**Size:** S · **Joins:** M16 · **Sources:** writer:F2, designer:F5, security:F11, product:F11
- [ ] One block with three rows (`doctor`'s version list; `db2` and `looks` tables with the version string; the dressing-room link and its payload). It sits between generated markers in README, INSTALL and the `doctor` first-run intro, and a drift test fails when a copy differs.
- [ ] The Looks tab copy no longer reads as "offline". The button is labelled with what it downloads.
- [ ] The wiki job is told to copy the block. Wiki `Home.md`'s `--offline` sentence is corrected.

**EV-07 — Help and docs drift: `undo`, `--paths`, README Wave 6, SECURITY.md**
**Size:** XS · **Joins:** M13 follow-ups · **Sources:** writer:F8, F13, F15; newcomer:F8; security:F12
- [ ] `undo --help` names every write it reverses, drops the `guard.undo(...)` sentence, and says that a second `undo` redoes the change.
- [ ] `snap restore --paths` gives the deletion rule its own sentence.
- [ ] The README's Wave 6 sentence matches `main` ("a read-only archive reader is in the library; no command reads `Data/`").
- [ ] SECURITY.md drops "once it ships". The README's duplicated browser block is removed.

#### Plans that say what they will lose (theme 3)

**EV-08 — Restore hints and snapshot references**
**Size:** S · **Joins:** M16 · **Sources:** raider:F2, newcomer:F12
- [ ] Manifests record `kind` (§6.9). `explain` and the explorer compare against the newest `user`/`first-run`/`profile` snapshot. When the newest snapshot is a pre-write copy, they say in words what restoring it means.
- [ ] `latest`, `latest-user`, `~N` and unique-suffix matching work in `snap show/diff/restore`.
- [ ] A regression test reproduces the verifier's wipe, recover, `explain` sequence and asserts that the hint is not the pre-write id.

**EV-09 — Semantic plan and diff lines for bindings, macros and CVars**
**Size:** M · **Joins:** M16 · **Sources:** raider:F9, a11y:F4, designer:F2
- [ ] `snap diff` and every write plan that touches `bindings-cache.wtf`, `macros-cache.txt`, `config-cache.wtf` or `Config.wtf` print key old→new, macro added/removed/changed by name, and CVar old→new, from the existing parsers.
- [ ] A SavedVariables diff gains one summary line (values removed, size before→after).
- [ ] The same lines are in the report models as `plan_lines` (used by EV-20). `--json` is additive only.

**EV-10 — A whole restore says what it leaves alone**
**Size:** S · **Joins:** M16 · **Sources:** qa:F2
- [ ] The plan lists files under the snapshot's subtrees that were added since, under a "left alone" heading.
- [ ] README, `snap restore --help` and the wiki say that a whole restore never deletes them. A `--mirror` option is left to a later ticket (it would delete through `guard`).

**EV-11 — `sv merge` discloses removals and notes before the prompt**
**Size:** S · **Joins:** M13 follow-ups · **Sources:** addon-author:F2, F13
- [ ] `--help` states that `--key` copies a subtree whole.
- [ ] The plan lists every key the merge removes from the target.
- [ ] `report.notes` (loader check) print before `Write the merge? [y/N]`.
- [ ] No change to §13.4's rulings.

**EV-12 — An interrupted change is visible and its debris named**
**Size:** S · **Joins:** M16 · **Sources:** qa:F3, F12
- [ ] An `open` journal record is a `doctor` problem (non-zero exit) that prints the exact `wowlab undo` line.
- [ ] `.wowlab-<32 hex>.tmp` gets a file-map row ("left by an interrupted change; the next write or `undo` removes it"). Leaving it out of snapshots needs `security-reviewer`'s confirmation that rollback never needs it.
- [ ] Without `.build.info`, `doctor` puts the not-an-install verdict before the first-run text and names Battle.net's Scan and Repair.

**EV-13 — The CLI's pipe handling**
**Size:** XS · **Joins:** M13 follow-ups · **Sources:** raider:F16, newcomer:F5, qa:F5, writer:F14
- [ ] `BrokenPipeError` and SIGPIPE are handled once at the entry point. Exit 0 when the work completed, and no errno line.
- [ ] A write never aborts mid-plan because stdout closed. A test pipes `snap restore --yes` and `tree` into a reader that closes after one line.

#### Player voice (theme 4)

**EV-14 — Help and text-output lint, and its hits fixed**
**Size:** S · **Joins:** M16 · **Sources:** designer:F6, newcomer:F3, writer:F4, F15, a11y:F13, addon-author:F14
- [ ] A test walks every `--help` screen and the text output of the fixture sandbox's read commands. It rejects `M1\d-\d+`, `ADR-\d+`, `\[verify\]`, `LAB_PLAN`, `L[1-8]\b`, dotted module paths and `UPPER_SNAKE` constants.
- [ ] Every hit is fixed. "JSON: X" moves to one shared footer naming the model unqualified.
- [ ] `luadata` refusal reasons are mapped to plain words in the CLI layer, with the rule id kept in `--json`. `luadata.py` is not changed.
- [ ] Every group and command has a `short_help` that fits at 80 columns. `-h` is accepted.

**EV-15 — Notes behind `--notes`; answers first**
**Size:** M · **Joins:** M16 · **Sources:** raider:F12, designer:F13, product:F8, newcomer:F6, writer:F16
- [ ] `char gear/binds/macros`, `macros check`, `binds coach` and `doctor` print data first, then at most one caveat line for this result, then a "N notes: --notes" footer.
- [ ] `doctor`'s six sync lines fold into one when nothing is set.
- [ ] "Cannot judge" replaces a misleading 0 ("Spells cast with no bind: cannot judge (no bindings record)").
- [ ] Prose wraps to the terminal width. No absolute source path is printed. `--json` is unchanged.

**EV-16 — File-map prose split into player text and spec notes**
**Size:** S · **Joins:** M16 · **Sources:** writer:F4, F12, newcomer:F3, F16, designer:F9
- [ ] Each `filemap.toml` row gets a player `what` and a spec `notes`. `explain` prints `what`, and prints tier and "read by" only with `--verbose`.
- [ ] "Agent" becomes "Battle.net app". The `LAB_FILE_MAP.md` drift test still passes, and its stale meta-text (line 17; "below" at line 141) is fixed.
- [ ] Badge words: "wowlab never changes it" instead of "won't touch". The red colour is reserved for errors.

#### First contact (theme 5)

**EV-17 — Bare names in `explain` and `sv dump`**
**Size:** S · **Joins:** M16 · **Sources:** newcomer:F1, F4, writer:F3, qa:F17
- [ ] A name not on disk is resolved by basename (file-map rows and real files under the flavor). Candidates are listed, and three or fewer are explained.
- [ ] `NO_ROW_LIKE` no longer claims that no row matches when one does.
- [ ] `sv dump` accepts an addon name or a path relative to the account folder, resolved as `sv list` lists them. When ambiguous, it asks for `--account` or `--character`.
- [ ] The README's lead question is one `explain` answers, or `explain` gains a "delete:" line from the row.

**EV-18 — Errors in words**
**Size:** S · **Joins:** M16 · **Sources:** qa:F17, writer:F14, designer:F4, newcomer:F14, qa:F4
- [ ] `FileNotFoundError` becomes a `CliError` that names the matching lister.
- [ ] Every "no install" error names `--root` and `WOWLAB_WOW_ROOT`.
- [ ] A full store is reported as the store being full, not as "cannot read" an install file.
- [ ] INSTALL.md names `uv tool update-shell` and the common Wine, Lutris and Proton prefix locations (docs only, L6).

**EV-19 — README and INSTALL examples run as tests**
**Size:** S · **Joins:** M16 · **Sources:** writer:F3, newcomer:F4
- [ ] Every fenced command in README and INSTALL runs against the fixture sandbox in CI. A non-zero exit, or output that no longer matches the shown lines, fails the test. Trimmed output is marked `…`.

**EV-20 — The confirm flow, presented**
**Size:** M · **Joins:** M16 · **Depends on:** EV-09 · **Sources:** designer:F2, F3, F4, a11y:F4, F7
- [ ] Plan lines from `plan_lines` are above Confirm in DOM order. The JSON tree is a collapsed "Details".
- [ ] Confirm's accessible name names the action. Focus moves to the plan heading, then after Confirm to the outcome sentence.
- [ ] A non-marker plan run is never labelled "Plan". Exit codes show as words (0 Done, 1 Could not, 2 Missing or invalid input, 3 Refused).
- [ ] Required fields are marked and checked before Run.
- [ ] `GcReport.dry_run` is false under `--yes`. Confirm stays offered on a no-op gc, as M14-02 rules.
- [ ] The JavaScript derives nothing (§14.5 D3). Reviewed by `security-reviewer`.

**EV-21 — `--json` errors and one error base**
**Size:** S · **Joins:** M16 · **Sources:** architect:F5, a11y:F15
- [ ] A `WowlabError` base with an `exit_code` attribute; module errors subclass it, and `_handled` catches the base.
- [ ] Under `--json`, an `ErrorReport {kind, message, exit_code}` is printed on stdout too. The stderr line and exit codes are unchanged.

#### Scale (theme 6)

**EV-05T — [TEST] `guard`: a listing memo that keeps every re-check**
**Size:** S · **Joins:** M16 · **Sources:** architect:F1, raider:F14
- [ ] Graders (strict xfail) cover: a constructed 8,000-file tree whose no-op whole-restore check meets §6.10's new target on the reference runner (`serial`); an identity change mid-transaction invalidates the memo; a commit-time re-check still refuses a changed path; an injected listing error is not cached.
- [ ] Constructed inputs are labelled. Reviewed by `security-reviewer`.

**EV-05 — [IMPL] The listing memo in `guard`**
**Size:** S · **Joins:** M16 · **Depends on:** EV-05T · **Sources:** architect:F1
- [ ] Graders activated by marker deletion only. `make ci` green, with the full suite as `guard.py` requires.
- [ ] Timings are reported for 2k, 8k and 20k files, unloaded. The §13.3 2,000-delete cap is restated or raised with numbers.

**EV-22 — `sv dump --json` on the C encoder**
**Size:** XS · **Joins:** M13 follow-ups · **Sources:** architect:F3, qa:F9
- [ ] `_stream_dump` encodes each row with `encoder.encode(...)`. Output is byte-identical to `main` on every fixture and on the constructed 10 MiB file, and the time is reported.

**EV-23 — WTF parser budgets and the BOM**
**Size:** S · **Joins:** M16 · **Sources:** qa:F7, F8
- [ ] Per-file-type byte and line budgets are refused with a position.
- [ ] A leading BOM is kept as a prefix token, and `to_bytes()` is byte-identical (L4). `cvar get` on a BOM-prefixed `Config.wtf` finds line 1. A serial RSS probe covers the budget.
- [ ] Constructed inputs are labelled. Whether the client writes a BOM is [verify] until a capture shows it (L8).

**EV-24T / EV-24 — `luadata.read` through `open_regular_file`**
**Size:** S + S · **Joins:** M16 · **Sources:** qa:F6, security:F3
- [ ] [TEST]: a FIFO is refused without blocking; a link out of the install is refused, as `tree` reports it; a regular file reads as before. Graders by `test-writer`.
- [ ] [IMPL]: `luadata.read` and `wtfconfig`'s `read_*` use the M11-11 helper. `make test-parser` and `make ci` green.

**EV-25 — Command-level performance budget, nightly**
**Size:** S · **Joins:** M16 · **Sources:** architect:F9, addon-author:F12, qa:F9
- [ ] A §6.12 table of command-level targets (whole restore, `sv dump --json`, `sv merge`, `addons list`, `wowlab --version`) as ratios to a calibration loop.
- [ ] A nightly job, not on the PR path, fails at 1.5× baseline. No live call (ADR-0012).

#### UI (theme 7)

**EV-26 — Gaps tab: one `plural`**
**Size:** XS · **Joins:** M13 follow-ups · **Sources:** designer:F1
- [ ] One of the two `plural` declarations (page.js:863, :1462) is renamed.
- [ ] A UI test renders Gaps with more than one folder in a group and asserts that no "undefined" appears.
- [ ] A grep test fails on duplicate function declarations in `page.js`.

**EV-27 — UI focus and announcements**
**Size:** S · **Joins:** M16 · **Sources:** a11y:F1, F2, F3, F11
- [ ] Focus is restored to the same `data-path` element after a tree redraw.
- [ ] `aria-live` is removed from `#explain` and `#detail`. `#status`, `#explain-status` and `#confirm-note` are `role="status"` with one sentence each, and `role="alert"` is used for refusals.
- [ ] `role=tree/treeitem/group` is dropped in favour of nested lists with `aria-expanded` on the twisty, or the APG tree is implemented. Either way, focus moves to the detail title after a pick.
- [ ] Copy results go to one shared status element and do not revert on a timer.

**EV-28 — UI visual accessibility**
**Size:** S · **Joins:** M16 · **Sources:** a11y:F5, F6, F8, F9, F10, designer:F11
- [ ] `.parchment :focus-visible` uses ink. A Python test parses `page.css` and asserts every focus-ring and text pair at 3:1 or 4.5:1 in both schemes.
- [ ] `.tabs` wraps. `.commands-body` stacks under 40rem, and `scrollWidth` equals `clientWidth` at 320 px on the owner's end-to-end list.
- [ ] A forced-colours rule covers selection. The quality word is visible next to the gear name. Each panel starts at H2.

**EV-29 — The Character header in player terms**
**Size:** S · **Joins:** M16 · **Sources:** designer:F7, F8, raider:F6
- [ ] The header shows level and class from the identity record, and spec and race names from cached build tables when present (else the id). Names come from data, never constants (L6).
- [ ] Averages are rounded to one decimal, with the exact value in a `title`. Keys show item names from the gear record when equipped.
- [ ] The picker breaks an mtime tie by the newest schema (a §16.3 amendment).

#### Distribution and first screen (theme 8)

**EV-30 — owner and conductor: the first hundred users**
**Size:** S · **Joins:** M16 · **owner** · **Sources:** product:F1, F3, F13
- [ ] GitHub Discussions are enabled, with a "tell us your setup" thread.
- [ ] A 60-second terminal recording of snapshot → break → `snap diff` → `undo`.
- [ ] One post each where Forever UI tinkerers and addon authors gather (owner's choice). The posts recruit one Windows player for M13-02's human run.
- [ ] `M13-gate.md` records traffic and clones beside the §15.4 counts.
- [ ] PyPI only after the ADR-0031 amendment (§5).

**EV-31 — First screen and the audience split**
**Size:** M · **Joins:** M16 · **owner** (the first-screen wording is an owner statement) · **Sources:** product:F5, writer:F5, F6, F7, F9, F17, newcomer:F13
- [ ] The README's first screen leads with "snapshot before you break it, see what changed, undo". The roadmap and "full vision" move to the wiki.
- [ ] `CONTRIBUTING.md` is added: cross-platform setup and `make setup`. The players' run-from-source recipe is `uv sync --frozen` / `uv run wowlab doctor`. SETUP.md's owner-only parts move under `docs/handoffs/`.
- [ ] `main` reports `0.2.0.dev0`. The wiki examples use `wowlab …`, with one note about `uv run`.
- [ ] `test_readme.py` pins the new first screen.

#### Players' jobs (theme 9)

**EV-32 — `wowlab addons check` and `sv list --orphans`** (needs the owner's pick of the addon-audit and sv-health slices, ADR-0024)
**Size:** S–M · **Joins:** M16 · **Sources:** raider:F8, addon-author:F5, F15, product:F10
- [ ] Per addon: interface against the client's discovered interface, missing `RequiredDeps`/`Dependencies`, `LoadOnDemand`, version, and which characters enable it.
- [ ] Orphans are SavedVariables that no installed TOC declares, with their size. Read-only, no network.
- [ ] Graded on the fixtures (the two DBM addons and the four orphan files are real).

**EV-33 — The coach's inputs and its defaults**
**Size:** S · **Joins:** M16 · **Sources:** raider:F4, F10, product:F8
- [ ] When the record counts non-plain binding commands, `binds coach` says before any judgement that N keys are bound to addon buttons or other non-plain commands and are not seen. Generic: no addon name is hard-coded.
- [ ] The log is chosen by the caster name matching the chosen character, with a warning on a mismatch.
- [ ] "Bound, never cast" is withheld below a stated minimum sample.
- [ ] Default character: newest schema, then newest mtime, and the reason is printed (a §16.2 amendment).

**EV-34 — `doctor`: next steps**
**Size:** S · **Joins:** M16 · **Sources:** product:F9, newcomer:F7
- [ ] A "Next steps" block covers: lab-addon installed and current (EV-04), characters recorded (n), combat log present. Each comes with the one command or in-game action that fixes it.
- [ ] With no install, the problem and the how-to come first, with no first-run intro.

#### Test strategy (theme 10)

**EV-35 — owner: an `@live` L1 sweep before each release**
**Size:** S · **Joins:** M16 · **owner** · **Sources:** qa:F14
- [ ] A `@live` test hashes the real install (contents, sizes, modes, mtimes, directory mtimes), runs every read command and the UI read endpoints, and compares. It is never run by CI (ADR-0012), and it is added to the release checklist.

**EV-36T — [TEST] Seeded mutation test for `luadata` and `wtfconfig`**
**Size:** S · **Joins:** M16 · **Sources:** qa:F15
- [ ] stdlib `random`, fixed seeds and iteration counts inside `make test-parser`.
- [ ] Invariants: only `LuaDataError` escapes; `serialize(parse(x)) == x` on success; `to_bytes()` round-trips for WTF. Written by `test-writer` (it grades `luadata.py`).

**EV-37 — CI enforces "graders activated by marker deletion only"**
**Size:** S · **Joins:** M16 · **Sources:** qa:F16
- [ ] On a PR touching `luadata.py` or `guard.py`, a job fails if a tracked grader file has any hunk other than the removal of an `xfail(strict=True)` block.
- [ ] A `protect_paths` rule for the implementer is proposed to the owner, as a harness edit.

**EV-38 — owner: real captures beyond one Mac**
**Size:** S · **Joins:** M16 · **owner** · **Sources:** addon-author:F3, F4, qa:F14, product:F3, F12
- [ ] Explicit-consent captures through `lab_capture.py`, with provenance rows: one Windows retail player with a heavy addon set (WeakAuras, Details, ElvUI or Auctionator), and one SavedVariables file of 10 MB or more.
- [ ] Until then, README says "built and tested on Forever on macOS; retail and Windows SavedVariables untested".

#### Security (theme 11)

**EV-39 — §16.5 and ADR-0032 rules written before F3 is ticketed** (docs; conductor XS lane, owner ack)
**Size:** XS · **Joins:** M16 · **Sources:** security:F1, F2
- [ ] The §16.5 F3 and F4 text of 4(a) item 12 is landed as a dated amendment.
- [ ] The ADR-0032 additions are drafted as a `Proposed` amendment for the owner.

**EV-40 — The store at rest**
**Size:** S · **Joins:** M16 · **Sources:** security:F7
- [ ] The store tree is created 0700 and its files 0600, whatever the umask. Existing stores are fixed on the next write.
- [ ] `doctor` and `snap verify` warn when the store is readable by others. Tests run under umask 0022.

**EV-41 — The UI's local exposure**
**Size:** S · **Joins:** M16 · **Sources:** security:F3, F4
- [ ] From `/api/run`, `sv dump` of a path outside the resolved install is refused, and a refusal never echoes bytes of an outside file.
- [ ] `page.js` drops the token from the URL with `history.replaceState`. A one-time bootstrap nonce replaces the token in the browser's argv.
- [ ] INSTALL.md's trust notes say "single-user machine" until both land. Reviewed by `security-reviewer`.

**EV-42 — Releases a player can verify**
**Size:** S · **Joins:** M16 · **Sources:** security:F6, architect:F11
- [ ] The README install line carries `#sha256=`.
- [ ] `release.yml` adds build-provenance attestation (pinned by SHA), and INSTALL shows `gh attestation verify`.
- [ ] Runtime dependencies get major upper bounds (`pydantic<3`, `httpx<1`, `psutil<8`, `platformdirs<5`).
- [ ] A weekly scheduled job installs the latest release fresh on 3.12 and 3.13 and runs `--version`, `doctor --offline` and `tree` on a fixture install. Otherwise `requires-python` declares `<3.13`.

**EV-43 — The capture tool and free text**
**Size:** S · **Joins:** M13 follow-ups · **Sources:** security:F8, F10
- [ ] Automatic SavedVariables picks skip a deny-list of chat-log, mail and note addons. Those can be picked only with `--sv` and an extra confirmation.
- [ ] String values of N or more words are flagged "free text, review by hand".
- [ ] `doctor --redact` replaces the login only inside path-shaped tokens and runs the home-path detector over every printed path.
- [ ] Reviewed by `security-reviewer`.

#### Wave 6 design (theme 12)

**EV-44 — §17.4 and §17.5 decisions before M15-09** (docs, XS)
**Size:** XS · **Joins:** M15 · **Sources:** architect:F7, F8
- [ ] The table-access path and the bytes-across-boundaries rule (4(a) item 13), and the blend-mode cost model, are written into §17.4 and §17.5. M15-09, M15-13 and M15-10 cite them.

**EV-45 — `cli.py` as a package** (only if M15 resumes; before M15-06)
**Size:** M · **Joins:** M15 · **Sources:** architect:F4, F10, F12
- [ ] One module per existing section, plus `_app.py` and `_target.py`. `cli/__init__.py` re-exports `app`, `CONFIRM_DECLINED` and `EXPECT_FIELDS`.
- [ ] Text and `--json` output are byte-identical on every command in the fixture sandbox.
- [ ] The restore skip policy moves to a library module in a later ticket.

#### Lab-addon (theme 13)

**EV-46 — A quieter lab-addon, schema 5** (needs an owner capture)
**Size:** M · **Joins:** M16 or later · **owner** (capture) · **Sources:** addon-author:F6, F7, F8, F9, F10
- [ ] The chat line prints once per addon version, or after an unclean session.
- [ ] Probes and the control event sit behind `/wowlab debug`, off by default.
- [ ] The `Constants` sweep runs once per session, or only where `ToggleLegacySystemUI` exists.
- [ ] Change events mark sections dirty, gathered out of combat at most once per 30 to 60 s and at logout.
- [ ] The reader accepts a newer minor schema's known sections and names the rest.
- [ ] Static Lua tests, a selene run, and one owner capture. No names stored (ADR-0026).

**Count: 46 numbered candidates, EV-01 to EV-46 (49 tickets with the
`[TEST]` twins).** Three are `[TEST]`/`[IMPL]` pairs on load-bearing files
(EV-01, EV-05, EV-24), and one is a grader-only ticket for `test-writer`
(EV-36T). Six are owner-run or owner-worded (EV-03, EV-30, EV-31, EV-35,
EV-38, EV-46), and two are docs amendments (EV-39, EV-44). Six join the
M13 follow-up lane (EV-07, EV-11, EV-13, EV-22, EV-26, EV-43), and two
join M15 (EV-44, EV-45). The other 38 join M16.

### 4(c) Changes to existing open tickets

- **M13-76** (`ui --help`): **widen it and move it to the front of M16.**
  It should cover `page.html:20`, the Commands intro (`page.html:62`), the
  `page.js:572` hint note, INSTALL.md's UI section (lines 233-262 and its
  "What to look for" list, which lacks Character and Looks), and the wiki
  `CLI-Reference.md`. One sentence everywhere: "Reading never changes
  anything. Six commands can change game files; each shows a plan and runs
  only when you press Confirm, after a snapshot." Add a text-pin test
  across all of them. Seven seats found this; it is the cheapest trust fix
  on the list.
- **M13-07** and `docs/handoffs/M14-binds-persist.md`: run on the launch
  client inside EV-03, and add the edit-mode cache and `Config.wtf` sync
  questions (raider:F5). Until the answers are in, the README's profile
  row says that action bars and talents live on the server and that
  binds and macros may be replaced at login (product:F6).
- **M13-13** (the gate): its date and inputs follow the §15.4 amendment, if
  the owner accepts the ADR-0031 amendment (§5).
- **M13-02** (Windows): the human run becomes the one release blocker of
  v0.2, with the tester recruited through EV-30.
- **M13-81** (lighter local run): add qa:F13. Add
  `skipif(os.geteuid() == 0)` to
  `test_wiki_daily_outside.py::…[read-only-as-user-fails]`, make `make ci`
  run every stage and report all of them, and make the selene requirement
  explicit in its message.
- **M13-72** (`char list --json` carries full records): extend the same
  decision to `addons list --json`, which embeds every TOC's lossless
  document (8 MB at 402 addons; architect:F6).
- **M13-74** (`sv merge --expect` pins merged bytes): keep it first among
  the follow-ups, and pair it with EV-11 so both `sv merge` disclosures
  land together.
- **M13-80** (`looks import-char` class): unchanged, and it can share a
  reviewer with EV-29, since both read the identity record.
- **M14-09's note at BACKLOG.md:1249** (the gear panel's live region) is
  absorbed by EV-27.
- **M15-05**: finish only if it is in flight. **M15-06 to M15-13: pause**
  until v0.2 ships (an owner decision, ADR-0024). If M15 resumes, M15-09
  and M15-13 depend on EV-44, M15-10 cites §17.5's cost model, and
  M15-06 waits for EV-45 if the owner wants the split.
- **No ticket should be closed** on the panel's word. M13-61 to M13-71,
  M13-73, M13-77 and M13-79 are untouched by the findings and stay in the
  cleanup half-wave.

## 5. Suggestions that conflict with an invariant or an accepted ADR

No seat proposed anything that crosses L1, L3, L5 or L7, or ADR-0023.
The panel checked every proposal below against the rule it touches.

### 5a. Invariants and accepted ADRs

| # | Suggestion | Rule it conflicts with | Panel's view | Revisit? |
|---|---|---|---|---|
| 1 | Publish the wheel to PyPI before the gate (product:F1, F4) | ADR-0031, Distribution: "No PyPI, no installer … until the gate has passed" | PyPI is a channel, not a trust change. The same wheel goes out with the same rules, and `uvx wowlab doctor` lowers the install bar for the P3 audience. | **Yes**, a narrow amendment (owner) |
| 2 | Start the 30-day gate at a launch-verified v0.2 (product:F2) | ADR-0031, The gate: "Thirty days after the first public release" | The window as it stands half-overlaps the beta-to-launch gap, when the audience has no client. The thresholds stay as they are; only the start moves. | **Yes**, owner |
| 3 | A frozen, signed binary or double-click launcher (product:F4) | ADR-0031 (not until the gate passes and a further ADR) | Agree with the ADR: not before the gate. | No |
| 4 | Lab-addon "restore my bars" in game (product:F6) | ADR-0026 (the lab-addon is data-producing code only), and ADR-0021 (writes go through `guard`; a server-side action placement is outside it) | Tier A in ADR-0023's terms, but it changes the addon's role and the write model. Record it as an idea only. | No, not now |
| 5 | Scope the pre-write snapshot to the touched subtrees (raider:F14) | L2 and ADR-0021 ("snapshot taken first"); §6.10 "of the default subtrees" | Undo after an unrelated change relies on the default subtrees. Measure first: EV-05's memo and an mtime/size hash cache in the store change no rule. | Only if EV-05 and the cache leave writes slow |
| 6 | `--allow-unreadable` for the pre-write snapshot (qa:F11) | L2 ("no `force` that skips the snapshot") | Weakens "the snapshot holds every file". Improve the message (EV-18) instead. | No |
| 7 | Accept a positional `nil` and non-finite spellings as opaque raw tokens (addon-author:F3) | L8 and LAB_FORMATS §4.1/§4.2 ("added with the fixture that shows it; an unlisted spelling raises") | L3 and L4 are not crossed, since the tokens are not evaluated and keep their raw text. The rule is right, though: get the Retail capture (EV-38) first, then amend §4.1/§4.2 with the fixture. | After a capture |
| 8 | axe-core or a browser harness in CI (a11y:F10) | ADR-0030 (any other runtime or build toolchain needs an ADR); ADR-0034 admits one vendored library, for the viewer | Static Python checks (EV-28) cover the contrast and structure rules now. A browser in CI is a new ADR with a measured benefit. | Later, by ADR |
| 9 | Redact other players' names in the `--send` packet (security:F2) | In tension with the ADR-0019 amendment of 2026-10-17 (character and realm names are public), which governs the repository and `--redact` | A packet sent to a model provider is not the repository. ADR-0032's text names only the player's own placeholders. Free text in macros is the stronger concern. | **Yes**, owner ruling within ADR-0032 before stage two |
| 10 | The proposal validator becomes load-bearing (security:F1; ADR-0032 already floats it) | ADR-0013's amendment of 2026-10-17 narrowed the load-bearing set to two files | The validator is the only path from a model's text to a `guard` plan. That is the same reason `luadata` and `guard` are in the set. | **Yes**, owner |
| 11 | DB2 tables read from the install over `casc` (architect:F14) | ADR-0022 (game data from wago.tools by build) and ADR-0033 (the listed asset formats) | Makes `char model` work offline, but it is a new format with three-layer grading. Not before users. | Later, by ADR |
| 12 | Leave `db2lake` out of the wheel (architect:F7, one option) | ADR-0028 (accepted design for db2lake) | An option, not a recommendation. The panel asks only that §17.4 name one path. | Owner's choice |

### 5b. Design challenges to spec rulings (not invariants)

These follow the plan. The panel's point is that the plan should change.
Each is an owner ruling.

- **raider:F7 (misread-spec).** §13.1 says a macro slot keeps an id only,
  never the macro's name or body, in line with ADR-0026's privacy posture
  (`MACRO <name>` is text the player typed). The point stands as a design
  challenge: placement stays [verify] forever without some join key. The
  M14-macro-place runbook may settle the numbering without names. If it
  does not, the panel suggests a per-install salted hash of name and body,
  matched offline against `macros-cache.txt`, which stores no text. Owner.
- **raider:F4.** LAB_PLAN.md:2111-2118 counts non-plain binding commands
  without storing them. `CLICK <frame>:<button>` names an addon's frame,
  not text the player typed. A narrow §13.1 amendment could keep `CLICK`
  commands, never `MACRO`, `SPELL` or `ITEM` text. EV-33's warning is
  spec-compatible today.
- **addon-author:F1 and F2.** §13.4 keys positional entries by index and
  copies a `--key` subtree whole (the M11-09T rulings). The panel finds
  that "`--take theirs` loses a list element" and "`--key` drops target
  keys" surprise the people the command is for. It proposes treating the
  array part as one value, and running a normal merge inside `--key X`
  with wholesale replacement as `--replace`. EV-11's disclosure lands
  either way.
- **designer:F2 (M13-26's generic tree), designer:F3 (M14-02's Confirm on a
  no-op gc).** EV-20 demotes the tree to "Details" and keeps Confirm on a
  no-op gc, so only the first needs a ruling.
- **architect:F2.** §16.1's three confirm shapes, including the stderr
  sentinel, are as built. The proposal is one plan model with a
  `plan_digest` and `--expect` on every write command. It would retire
  `CONFIRM_DECLINED` and pin the four commands that today "apply what is
  there". The panel thinks this is right before the writers of §16.5 F2
  add more shapes, but it is not urgent for v0.2.
- **newcomer:F8.** `undo` has no `--dry-run` by design (the third confirm
  shape). Only the documentation half is taken (EV-07).
- **raider:F6.** §14.4 defines `char list` over folders with a
  `WowLab.lua`. Listing every character folder, with "no lab record", is
  the deferred alt-dashboard's first slice. Owner pick.
- **product:F8 and EV-29/EV-33 tie-breaks.** §16.2 and §16.3 say "the
  latest-written first". Breaking an mtime tie by schema is a small
  amendment.

## 6. Open questions for the owner

1. **Wave order.** Pause M15 after M15-05 for a two-week launch-edition
   wave (M16), or carry on with Wave 6? (ADR-0024)
2. **The gate.** Keep 2026-11-17, or restart the 30 days at a v0.2 verified
   on the launch client? (ADR-0031 amendment, §5 item 2)
3. **PyPI and outreach.** May the wheel go to PyPI, and may the release be
   announced before the gate? Who posts, and where?
4. **Restore across folders.** Should `guard` restore into a named other
   flavor folder or install root at all? It currently refuses this by
   design, in code only. If yes: may account folders be mapped
   automatically when each side has exactly one?
5. **`doctor` and the network.** Make `doctor` offline by default with an
   opt-in version check (security:F11, product:F11, designer:F5), or keep
   the current default? The UI's `doctor` form defaults to online either
   way unless this changes.
6. **Player voice.** Are `[verify]` markers shown to players as "(not yet
   confirmed)", and are standing caveats moved behind `--notes` by
   default? Is a `.claude/rules/` copy rule wanted?
7. **Exit codes.** Should `explain` of a missing path exit 1 (newcomer:F9)?
   That changes a scripted contract (ADR-0031: `--json` is an interface).
8. **sv merge rulings.** Keep §13.4's index-keyed lists and wholesale
   `--key`, or change both (§5b)?
9. **The lab-addon.** Is a schema-5 change for a quieter addon (EV-46)
   wanted before launch, and is a hashed macro join key (§5b) acceptable
   under ADR-0026?
10. **Ideas slices.** Pick the cheapest addon-audit and sv-health slice
    (EV-32), and the alt-dashboard's first slice (raider:F6), or leave
    both on the menu?
11. **Captures.** One retail capture on launch day, and recruiting one
    Windows volunteer with explicit consent (EV-38)?
12. **ADR-0032 additions.** Rule on third-party names in the `--send`
    packet, and on making the proposal validator load-bearing (§5 items 9
    and 10).
13. **Housekeeping.** Correct CLAUDE.md's "under 40K tokens" figure, and
    fold LAB_PLAN amendments at wave reviews?

## 7. Appendix

### 7.1 Not reproduced

**No finding was marked not-reproduced.** One was misread-spec (raider:F7,
§5b). These sub-claims were not reproduced as stated, or were corrected:

- raider:F12: "50 lines for 4 macros" depended on the seat's own added
  macros. The verifier saw 19 lines for 1 macro, about 8 of them caveat
  paragraphs. The substance stands.
- raider:F6: `char show` is 104 lines, not 47. The left-out character is
  §14.4's choice.
- raider:F3: the quoted sentence is `PRESET_SCOPE_NOTE` (profiles.py:143-147),
  printed by `profile save` and `profile apply`, not `profile save --help`.
- qa:F2: the README pitch quoted ("puts every file back") is about `undo`,
  not `snap restore`. INSTALL.md:146-149 already says that a restore does
  not remove added files.
- qa:F14: the `@live` casc tests do take before/after state of
  `Data/data`. "Nothing exercises L1 against a real install" is too strong.
- architect:F2: M13-75's 25 failures were in tests' expectations; the
  mechanism was right.
- architect:F4: the Wave 6 parallelism gain is overstated (the dependency
  graph already serialises the CLI tickets), and 111k tokens does fit a
  200k window.
- writer:F5: SETUP.md's version table is platform-neutral, so "a
  contributor on Linux or Windows gets nothing usable" is too strong.
- product:F8: the schema-1 default pick came from copy-artefact mtimes a
  few microseconds apart. On a real install, the newest record is the
  last-played character.
- a11y:F5: the verdict cites WCAG 2.4.11, which is Focus Not Obscured.
  The contrast criterion is 1.4.11.
- a11y:F14: tagged new, but it is M13-76. a11y:F2 should cite the earlier
  gear-panel note (BACKLOG.md:1249).
- designer:F9: the rule is at page.css:131, not :121.
- newcomer:F4 and writer:F3: `sv dump nope.lua` exits 1, not 2.
- **Not re-checked by the verifier:**
  - addon-author:F1's three-way append case.
  - a11y:F3's 20-Tab count.
  - architect:F1's 91 s `--yes` run with 400 changed files.
  - designer:F3's y=956 Confirm position.
  - designer:F15's `BROWSER=true` token-URL sub-claim.
  - designer:F6's "~23 ticket ids in cli.py".
  - qa:F5's long-plan abort variant.
  - qa:F8's binds, macros and SavedVariables variants.

### 7.2 Findings the verifier did not check (seat-reported only)

These are ranked lower above. "Corroborated" means another seat's verified
finding covers the same behaviour.

- **raider:** F10, F11, F13, F14 (constructed load), F17.
- **addon-author:** F6, F8, F9, F10, F12 (constructed 43 MB), F15
  (corroborated by raider:F8), F16.
- **designer:** F8, F10, F11 (corroborated by a11y:F6), F12, F14.
- **a11y:** F7, F8, F9, F10, F11, F12, F15 (corroborated by architect:F5).
- **newcomer:** F6, F7, F9, F10, F11, F12, F13, F14, F15, F16.
- **writer:** F7, F9, F10, F11, F12, F14 (corroborated by newcomer:F4, F5),
  F15, F16, F17.
- **architect:** F6, F8 (micro-benchmark), F9, F10, F12, F13, F14.
- **qa:** F3, F4, F6, F7, F9, F10, F11, F12, F13 (corroborated by the
  architect's `make test` run), F15, F16, F17 (corroborated by
  newcomer:F4), F18 (corroborated, M13-76).
- **security:** F4, F5 (corroborated by newcomer:F2), F6 (dependency half
  corroborated by architect:F11), F8, F9, F10, F11 (corroborated by
  product:F11).
- **product:** F10 (corroborated by raider:F8), F13, F14, F15.

### 7.3 What each seat could not test

- **raider:** login-time behaviour (sync, merged SavedVariables in the
  client); Windows paths and the running-client refusal; network paths;
  `addon install lab` across real sessions; a real 1 to 2 GB addon tree;
  `log tail --follow` across a rotation; most of the UI.
- **addon-author:** anything in the client (addon load, per-event cost,
  secret values, the 15 s window, patch-time interface acceptance); Retail
  and Windows SavedVariables; the running-client refusal; the UI confirm
  for `sv merge`/`addon install`; a cross-machine three-way merge and real
  AceDB profile files.
- **designer:** the real network (Looks content, `db2`, online `doctor`);
  confirm flows that write the game folder; other browsers, Windows and
  macOS rendering, HiDPI, real fonts; screen readers and full keyboard
  navigation; the dressing-room destination; light mode beyond one
  screenshot per tab.
- **a11y:** real assistive technology; the Looks frame's content; a real
  install's tree size (Tab costs extrapolated); Windows High Contrast
  beyond emulation, Narrator, VoiceOver; pointer target size.
- **newcomer:** the real release download and checksum; Windows and
  PowerShell; `doctor` online, `db2`, `looks`; a default-location install;
  the running-client refusal; the wiki; the UI confirm flow as a newcomer.
- **writer:** a real install on macOS or Windows; the UI visually; network
  commands; `make setup` (it writes git config); `uv tool install` of the
  v0.1.0 wheel; how the wiki renders on GitHub.
- **architect:** the UI in a browser; a real large install (all scale
  inputs constructed); Windows and macOS timings; network paths; Wave 6
  code beyond M15-03 and M15-04.
- **qa:** any real install; network paths; the UI confirm flow and its 120 s
  kill; case-insensitive and normalising volumes, junctions, long paths;
  true unprivileged-user runs; timings under no load.
- **security:** Windows and macOS file-system and process behaviour; live
  network targets; the Looks frame with a planted page; TOCTOU races
  inside `guard` under a concurrent attacker; the workflows (read, not
  run) and GitHub rulesets; ADR-0032 (not built).
- **product:** the UI in a browser; any real retail install; network
  commands; Windows and real macOS; server sync and persistence; GitHub
  traffic views (HTTP 403). Audience sizes and the Windows share are
  estimates.
