# Wave 5 (M14) review

Written by the conductor on 2026-10-19, following `docs/LAB_PLAN.md` §11,
ADR-0024 and ticket M14-10. Wave 5 was picked on 2026-10-18: finish the
twelve M13 follow-ups, then M14, the confirm flow in `wowlab ui` and the
player's character in it (§16, ADR-0029 amended, ADR-0032 accepted with
both stages). The plan section and the ten tickets M14-01 to M14-10 landed
in #282; the wave opened at dafd223 when the twelfth follow-up merged
(M14-01, 2026-10-18 late evening). The owner reordered the wave the same
evening ("want the gear view sooner"): `char gear` was dispatched before
the gate closed, and the Character tab came before the six install writes.

**Every build ticket is on `main`.** M14-02 to M14-09 merged between
2026-10-19 00:30 and <M14-09 merge time>; this review is M14-10, the last
ticket. Dispatch stops here until the owner picks the next wave (§11).

## What shipped

In merge order, with the squash commit on `main`:

- **M14-05 `wowlab char gear`** (#286, a52ad47). `wowlab_core.itemlink`,
  a lossless parser of the client's item links (38 links from the
  capture round-trip byte for byte), `gear.py` with the shipped slot
  table `gear_slots.toml` (slots 1 to 19, quality words and colours,
  **[verify]** on Forever) and a player override, and `CharGearReport`.
- **M14-02 the confirm flow, on `snap gc` and `doctor --yes`** (#288,
  c8d6f68; the Windows follow-up #291, 826ce3d). The mechanism of §16.1:
  the plan run without `--yes`, a one-time token minted only for a run
  that ended with its shape's marker, bound to the exact confirmed argv
  and spent on first use. The follow-up: on Windows the NUL device is a
  character device, so a child started with `stdin=DEVNULL` saw a
  terminal; the child now gets a pipe whose writer the server closes at
  once.
- **M14-06 `wowlab_core.keymap` and `wowlab char binds`** (#287,
  8b9c290). The join of the bindings files and the lab-addon's record,
  one row per key and command with an agreement word, the unreached
  slots, and the reasons a key could not be placed or compared.
- **M14-08 the Character tab: picker, header, gear** (#290, 9f47b7a).
  The picker lists the characters the lab-addon has recorded, latest
  first; the gear table's colours are CSS rules the server builds from the
  shipped table under the page's own CSP hash; the page derives nothing
  (§14.5 D3).
- **M14-07 `wowlab char macros`** (#292, 4a5b8db). Every record of both
  `macros-cache.txt` files with the checker's findings, and a *candidate*
  placement under a numbering that is **[verify]** on every line of output
  (`macroplace.CANDIDATE_RULE`); `docs/handoffs/M14-macro-place.md` is the
  owner's capture step that settles it.
- **M14-03 the six install writes, and `--expect`** (#294, 22b80ce).
  `snap restore`, `undo`, `profile apply`, `sv merge`, `addon install` and
  `addon remove` run from the Commands tab as plan, then Confirm. The
  server lists no confirm set: it reads each `--yes` leaf's shape from
  what the leaf declares, so a future `--yes --dry-run --json` command
  joins the step with no UI change. `undo --expect <journal id>` and
  `sv merge --expect <sha256>` pin the plan the page showed; the other
  four are held by their arguments and by snapshot-first.
- **M14-04 `looks page` in the UI** (#297, 9d48636). A fifth tab writes
  the page with `looks page` and shows it in a `srcdoc` frame with
  `sandbox="allow-scripts"`; the UI's CSP gains exactly the two hashes of
  `lookspage`'s own script and style. The implementer loaded it in
  chrome-headless-shell 153 and in WebKit (Safari 27's engine): the frame
  loads under `default-src 'none'` with no `frame-src`, the looks script
  runs, and the frame cannot reach its parent, storage or the network.
- **M14-09 the Character tab: keys and macros** (<#PR, sha>). The Keys
  table with its text and "disagreements only" filters and the unreached
  slots; the Macros cards with bodies as lines, candidate placement, the
  keys that reach each slot, the checker's findings and a "findings only"
  filter; both read-only, with the note §16.5 asks for.

Docs and harness PRs of the wave: #285, #289, #293, #295, #296, #299
(ticks, the dated amendments of §16.1 and §16.2 as built, the `ui` rule's
confirm step, M13-68's three measured runs and the review job's cap raised
to 40, nine review follow-ups filed). One test-only fix outside M14: M13-75
(#298, 1b4bdc6), the Windows red that M14-03's tests caused.

## What was learned about the client and the platforms

- **Macro numbering is still unknown.** The one capture (build 70425) had
  an empty account `macros-cache.txt` and two action slots holding macro
  ids 149 and 140 against a 29-record character file. That fits the
  remembered Retail rule (character record k is index 120 + k) but does not
  prove it, and nothing in the capture shows whether the file's order is
  the client's order. `char macros` therefore prints every placement as a
  candidate with **[verify]**, and the runbook names three macros so file
  order, window order and id order can be told apart. The runbook also
  asks for the cap: 29 character macros is more than Classic's 18, so
  Forever's cap is not Classic's.
- **A stale `macros-cache.txt` moves every candidate.** The file is written
  at logout; the lab-addon's record is written on `/reload` too. One record
  missing from the file shifts the position of every later one, which is
  exactly what the 70425 capture showed. The CLI says so.
- **`sv merge`'s source is live.** The client rewrites the played
  character's per-character files at logout, not the target's, so a merge
  planned before a logout and confirmed after can take values the page
  never showed. M13-74 pins the merged bytes too.
- **Windows: NUL is a character device**, so `isatty()` is true on a child
  whose stdin is `DEVNULL`; and a child's text-mode stderr arrives with
  CRLF. Both bit this wave (#291, M13-75), both are now pinned by tests
  that run on every platform.
- **A `srcdoc` frame inherits its parent's base URL.** Inside the Looks
  frame `document.baseURI` is the opened URL, token included, although
  `location` is `about:srcdoc` and the referrer is empty. The two scripts
  the CSP allows never read it, a test keeps it that way, and every request
  from the frame carries `Origin: null`, which the server refuses. The
  residual is named in the module docstring; see the owner actions.

## What was learned about running the wave

- **The review job's cap was the measured problem, not the model.** Two
  of three `lab/` runs ended with a verdict and still failed the job,
  because the action fails a run whose `num_turns` exceeds `--max-turns`
  even after a successful result. The conductor first misread that as a
  failure on findings; the #295 review caught it from the log's `##[error]`
  lines. Lesson recorded: read the action's error lines before
  interpreting a job conclusion. The cap is 40 now.
- **Bot findings were handled three ways.** A real docs defect (a runbook
  list that rendered with different numbers than its cross-references)
  was fixed by the conductor in the XS lane; a real spec contradiction in
  the conductor's own amendment was fixed the same way; a disputed test
  widening was tested on the branch (the original assertion fails, so the
  widening was needed) and answered "seen by the reviewers, not held". The
  2026-10-17 rule held: none of the three held a merge on its own.
- **Windows went red on `main` twice**, both times from new tests, both
  times green again within hours: M14-03's confirm tests expected LF stderr
  from the child (M13-75, #298), and a Looks test wrote its constructed
  page with `write_text`, which gave the header CRLF (M13-78, #301). Each
  ticket was filed and fixed the moment the red run was read, ahead of the
  frontier, and neither fix touched `lab/core/src/`. The second one slipped
  through because the shepherd merged M14-04 while its own Windows job was
  still running; the job is not required, and the rule stands, but a
  shepherd could wait for it on a `lab/**` PR when nothing else is queued.
- **Two usage-limit resets and several network resets** cut agents off
  mid-task; every one resumed by message with its worktree intact. The
  M13-60 rule (the shepherd never cleans up; the conductor does) worked
  without a single stranded worktree.
- **Review rounds.** M14-07 needed two (nine domain findings on wording
  and attribution, none wrong; two code findings); M14-03 one (six, all
  wording but one design question ruled a follow-up); M14-04 one (two
  low); M14-09 two (four code findings, one major: two quick picks started
  six runs against the server's four slots; then one trivial deletion). No
  ticket reached the third-round stop.

## Effort against the plan

Tickets were sized S or M and landed in a day: the wave opened late on
2026-10-18 and its last build ticket merged on 2026-10-19. The owner's
reorder cost nothing: `char gear` built in parallel with the confirm
mechanism, and the Character tab landed before the six writes without a
file collision. The one M-sized ticket that grew was M14-03, where the
reviewers' questions were about what the page promises, not what the code
does.

## Follow-ups filed, not yet picked

From this wave's reviews, open on `main` (M13-70 to M13-77; M13-75 and
M13-78, the two Windows test fixes, are already merged):
`labaddon.ActionBars` admits duplicate slots (M13-70); the shell guard's
`--input` bodies and substring allowlist (M13-71); `char list --json`
carries every full record under the Character tab (M13-72); `char binds`
with case-variant `WowLab.lua` files (M13-73); `sv merge --expect` pins the
merged bytes (M13-74); `wowlab ui --help` still describes the v0.1 UI
(M13-76); `CharBindsReport` carries the left-out sentence the Keys panel
copies from the CLI (M13-77). The earlier M13-61 to M13-69 follow-ups are
also still open; three of them (M13-62, M13-64, M13-66) wait on the owner.

## Owner actions outstanding

- **The README's first-screen sentence.** M14-03 changed "`wowlab ui` never
  changes game files." to "`wowlab ui` changes game files only when you
  confirm a plan it showed you." The sentence was an owner statement; its
  wording is the owner's to confirm or change.
- **The `baseURI` residual in the Looks frame.** Accept it as bounded (the
  default), or rule for the ticket's fallback (the file's path with a copy
  button, no frame). Nothing else removes it.
- **The Character tab and flavors.** The tab reads the default flavor
  folder only; a player with two flavor folders sees one roster. Whether it
  follows the Explorer's flavor picker is a small change that the M14-09
  implementer left to the owner.
- **Gear colours.** The colour drawn for a slot comes from the shipped
  table, never the player's own `gear_slots.toml`; the player's file
  changes the word, not the colour. The M14-08 ticket said "every name and
  colour comes from the model"; say whether the wording or the behaviour
  should change.
- **Two capture steps, both runbooks on `main`:** `docs/handoffs/
  M14-macro-place.md` (settles macro placement) and `docs/handoffs/
  M14-binds-persist.md` with `M13-07.md` (the persistence tests, §16.5 F1).
  Nothing that writes these files is built before the answers are in.
- Still from Wave 4: `gh cache delete --all`; the M13-62 `accountList`
  line; the M13-64 GUID decision; the M13-66 link spelling; the M13-02
  Windows human run; the 30-day gate on 2026-11-17 (M13-13); a real-browser
  look at the Character and Looks tabs on `main`.

## `docs/LAB_IDEAS.md`: what moved

The keybind-coach, macro-doctor and wow-as-code entries share the writers
§16.5 F2 names; nothing in the ideas file was built this wave, and no row
changed state. The looks page's "embedded view" is done (M14-04), which
closes the ADR-0029 amendment's open route.

## Recommended next wave (at most three; the owner picks)

1. **The road to modify (§16.5 F1 to F3).** The two persistence tests are
   the owner's and gate everything after them. Then the two lossless
   writers through `guard` (`binds coach --apply`, `macros fix`, each a
   `--yes` command that the confirm flow already carries with no UI work)
   and the proposal format with its validator, the only path from any
   suggestion to a `guard` plan. Every piece is a ticket shape already;
   the ADRs are accepted; §16.6's defaults were all taken. This is the
   wave the owner asked for on 2026-10-18.
2. **The advice packet, stage one (§16.5 F4, ADR-0032).** `wowlab char
   advise` writes one self-contained file for the player to hand to an
   assistant by hand, with the proposal grammar and worked examples; the
   UI shows it with a copy button and a drop target for the proposal. No
   network, no key, no ADR change. It can run beside item 1 once F3's
   grammar is fixed, and it is the first thing the owner can use with a
   model.
3. **A cleanup half-wave.** The sixteen open M13 follow-ups (M13-61 to
   M13-76 less the three with the owner), M13-74 first since it closes a
   gap in a shipped write command; each is XS or S and most are one
   review. Worth doing before the writers land, since M13-70 and M13-73
   touch the data the writers will read.
