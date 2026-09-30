# Backlog

Agent-sized tickets. Each is independently reviewable and has a verifiable acceptance criterion. Dependencies are explicit; anything with no unmet dependency can be worked in parallel.

Estimates assume one agent per ticket. `S` = under half a day, `M` = about a day, `L` = multiple days.

**Status lives in the heading:** `## [ ] M10-04 — …` is open, `## [x] …` is done. A ticket also counts as done when a merged PR title carries its id in parentheses. Implementers never edit this file; the conductor ticks headings in a batched `docs(backlog)` PR. A grader ticket carries a `T` suffix (`M10-04T`, **[TEST]**, test-writer) and lands before its implementation twin (`M10-04`, **[IMPL]**, implementer) — see ADR-0013. Tickets marked **owner** need the repository owner for a real capture or a decision.

The backlog holds the current wave only (ADR-0024). Milestone numbers start at `M10`; earlier numbers belonged to the retired project (ADR-0025) and are not reused. Wave 1 (M10) is archived in `docs/handoffs/M10-backlog.md`, Wave 2 (M11) in `docs/handoffs/M11-backlog.md`.

---

# M12 — db2lake, char-planner (talents first), alt-dashboard (Wave 3)

Spec: `docs/LAB_PLAN.md` §14. Decision: ADR-0028 (Proposed; M12-03 and later wait for the owner to accept or amend it). Owner pick 2026-09-29. L1–L8 apply to every ticket. Nothing in this wave writes into an install.

The wave is twelve tickets: ten development tickets, one optional owner capture (M12-11) and the review (M12-12). None is a `T` twin, because no ticket changes `luadata.py`, `guard.py`, `svmerge.py` or `profiles.py`; the tickets that run owner-typed SQL or put install-derived strings into HTML nonetheless get `security-reviewer`. The plan's estimate is ten development tickets; Wave 2's ten became twenty-five, so expect growth.

## [ ] M12-01 — Record the Trait, Spell, Currency and Skill tables
**Size:** S · **Depends on:** —

Record, by hand through `GameData.table` (ADR-0012: never from a test), the tables Wave 3 grades against, per `docs/LAB_PLAN.md` §14.1 and §14.6, at build 1.60.1.70058 (the build of the `macos-70058` capture). **Whole:** `TraitTree`, `TraitNode`, `TraitNodeEntry`, `TraitDefinition`, `TraitNodeXTraitNodeEntry`, `TraitEdge`, `TraitCond`, `TraitCurrency`, `TraitTreeXTraitCurrency`. **ID-filtered subsets:** `SpellName` (the ids named by `TraitDefinition.SpellID`, `OverridesSpellID` and `VisibleSpellID`), `CurrencyTypes` (the ids in the committed captures' `currencies.list`), `SkillLine` (the ids in their `professions.list`), `ChrSpecialization` (the ids in their `spec`). Also the nine Trait tables at 1.60.1.70009 when wago lists that build (the cross-build tests and the two 70009 captures). Also the body of one `HTTP 400` response for a table the build lacks (`TraitSubTree` at 70058; body only, no cookies), for M12-02.

New tool `scripts/wago_subset.py`: selects whole lines of a recorded CSV by the values in one column, keeps the header, never parses and re-serializes, refuses when the source's SHA-256 is not the one given, and prints the filter for the provenance row. Provenance rows per §14.6 (`wago-csv` for whole tables, `wago-csv-subset` for subsets, stating the full download's SHA-256 and size, the column, where the id set came from, and the counts). Update the "small tables only" rule in `lab/core/tests/fixtures/README.md` to add the subset rule.

**Acceptance:** every file has a provenance row (`make test-parser`); a subset holds only whole, byte-identical lines of its source in file order (a labelled constructed test on the tool, plus one on a committed whole table); every `SpellID`, `OverridesSpellID` and `VisibleSpellID` in the recorded `TraitDefinition` has its `SpellName` row in the subset; every tree id in the `macos-70058` capture exists in the recorded `TraitTree`, and the class tree's node ids all exist in the recorded `TraitNode`; no committed file is over 512 KB; `make ci` green; reviewed by `security-reviewer` (fixtures) and `code-reviewer`. The conductor adds the `docs/LAB_FORMATS.md` note.

---

## [ ] M12-02 — `gamedata`: HTTP 400 for a table the build lacks
**Size:** S · **Depends on:** M12-01

On 2026-09-29 wago answered `400` for `TraitSubTree`, `TraitTreeLoadout`, `TraitTreeLoadoutEntry` and a made-up table name at build 1.60.1.70058, a build its listing includes; `gamedata` reports the bare URL and `HTTP 400` (`docs/DATA_SOURCES.md`, breakage log). Map a `400` from the table endpoint, when the builds listing lists the build, to `TableNotPublished`, with a message that the table may not exist in this game version; an unlisted build stays `BuildNotPublished`; a `400` is never retried and never cached. The recording from M12-01 is the fixture.

**Acceptance:** tests replay the recording; `wowlab db2 head TraitSubTree --build 1.60.1.70058` (through the CLI test seam) exits 1 with the new message and no traceback; no other status code's handling changes; the `Source` protocol is unchanged; `make ci` green; reviewed by `code-reviewer`.

---

## [ ] M12-03 — `wowlab_core.db2lake`: typed tables in SQLite
**Size:** M · **Depends on:** M12-01, ADR-0028 accepted

`wowlab_core.db2lake` per `docs/LAB_PLAN.md` §14.2 and ADR-0028: one SQLite file per full build under `<user data>/wowlab/db2lake/`, tables loaded on demand and in one transaction from the cached CSV through `GameData.table`, column types inferred by the ADR's rule, `_lake_tables` recording name, source SHA-256 and row count, `Lake.rows(name)` and `Lake.schema(name)`, `Lake.attach(build)` read-only under `b<build digits>`. A loaded table is never replaced (L5). The database path is refused inside an install (as `gamedata` refuses its cache). A repeated header name is `MalformedTable`.

**Acceptance:** graded on the recorded tables: each recorded Trait table loads with the columns wago gives (`Field_10_0_0_45697_006` included) and a row count equal to the CSV's; labelled constructed tests for the inference rule (`0`, `-0`, `007` stays TEXT, 64-bit edges and overflow, an all-empty column, decimal fractions, a numeric-looking text column, empty cells as NULL and as the empty string); a second load is a no-op and a changed source hash is an error citing L5; a deleted database is rebuilt with the same content; two builds attach and join; a path inside an install is refused; a labelled constructed 200,000-row, 10-column CSV loads in under 10 CPU seconds (`_cpu_clock.py`) with memory bounded by the batch size, not the file; `mypy --strict`; reviewed by `security-reviewer` and `code-reviewer`.

---

## [ ] M12-04 — `wowlab db2 tables | schema | sql | repl`
**Size:** M · **Depends on:** M12-03, M11-30

The CLI of `docs/LAB_PLAN.md` §14.2: `db2 tables [--build V]`, `db2 schema TABLE`, `db2 sql "SELECT …" [--build V] [--attach V …] [--max-rows N] [--timeout S] [--offline] [--json]`, `db2 repl`. A table the query names but the lake lacks is fetched and loaded once (unless `--offline`), and the output says so. **SQL safety** as §14.2: one statement, beginning with `SELECT`, `WITH` or `VALUES` after comments; read-only connection; an authorizer as the second layer; extension loading off; `temp_store` in memory; an operation budget and a wall-clock deadline; a row cap with truncation flagged. Text output goes through `cli._safe` (M11-30).

**Acceptance:** labelled constructed hostile statements are each refused with exit 1 and create no file, change no database: `VACUUM INTO`, `ATTACH`, `DETACH`, `PRAGMA` outside the allowlist, `CREATE`, `INSERT`, `UPDATE`, `DELETE`, `DROP`, `WITH … INSERT`, a `load_extension()` call, two statements, a comment hiding the first token; a recursive-CTE bomb and a cross-join bomb both stop at the budget or the deadline with a message; the row cap flags truncation in text and `--json`; `--offline` makes no request (the transport fails the test if asked); a missing table is fetched once through a replayed transport; `--json` validates; `make ci` green; reviewed by `security-reviewer` (adversarial, on the real CLI) and `code-reviewer`.

---

## [ ] M12-05 — `wowlab_core.talents`: trees, plans and the check
**Size:** M · **Depends on:** M12-01, M12-03, ADR-0028 accepted

The model of `docs/LAB_PLAN.md` §14.3: `TalentTree`, `Node`, `Entry`, `Edge`, `Plan` and `check(plan)`, built from the recorded tables through `Lake.rows`. **Refuse only what the tables decide** (unknown node or entry, rank above `MaxRanks`, an entry not on the node, more points spent than the tree currency's `SourcedMax`); **note the rest** (no purchased neighbour along an edge, an unmet `TraitCond` gate, `RequiredLevel`, more points than the character currently holds), each note saying what is unverified. Ids the recorded build lacks are "unknown to build V (possibly a hotfix)". Find whether a tree-to-class map exists in the recorded tables; if not, say so in the amendment and label trees by id, system and root names.

**Acceptance:** graded on the recorded tables and the `macos-70058` capture: every tree in `TraitTree` loads, with counts equal to the tables' (17 trees, 558 nodes, 96 edges); the captured class config (tree 1116) joins: its 52 recorded node ids equal the table's nodes for the tree, and every recorded `active_rank` is at most its entry's `MaxRanks`; `check` on a plan built from that config has no refusal; a labelled constructed plan per refusal and per note; models are Pydantic v2; `mypy --strict`; the dated amendment in §14.3 records what the tables settled and what stays **[verify]**; reviewed by `domain-reviewer` and `code-reviewer`.

---

## [ ] M12-06 — `wowlab talents trees | tree | import-char | save | show | compare`
**Size:** M · **Depends on:** M12-05

The CLI of §14.3 without `decode` and `page`, mirroring `looks` (M11-06): plans as `<user data>/wowlab/talents/<name>.json` (`SavedPlan`, format 1, the build and the plan), names by the profile rule, `save NAME --tree T --rank NODE=RANK … [--entry NODE=ENTRY …] [--replace]` (a plan the tables refuse is not written, exit 1; notes are shown), `import-char NAME [--character C]` from `talents.class` via the M11-04 reader (a character without class talents gets the addon's reason, exit 1), `show`, `compare A B`, `trees`, `tree ID` (the nodes as text, with names, ranks, entries and edges). Every data command takes `--json`; text output goes through `cli._safe`.

**Acceptance:** `import-char` on the `macos-70058` fixture reproduces the captured tree, ranks and entries and saves; `save` refuses an over-rank plan (exit 1, nothing written) and shows a note for an unreachable node; `compare` lists nodes set to the same rank and those that differ; a saved plan checked later against another build reports what changed; the store path is refused inside an install; `--json` validates; `make ci` green; reviewed by `domain-reviewer` and `code-reviewer`.

---

## [ ] M12-07 — `wowlab_core.talentstring`: decode a loadout string
**Size:** S · **Depends on:** M12-06

`wowlab_core.talentstring` and `wowlab talents decode STRING [--tree ID]` per §14.3: the header (serialization version, spec id, tree hash) and one field group per node in tree order, laid out as community documentation says and **[verify]** on Forever. **Decode only**; encoding needs the tree hash (§14.5 D6).

**Acceptance:** decoding the export strings in the committed captures (`macos` and `macos-70058`) yields ranks equal to the ranks recorded in the same file for the same nodes (a real cross-check), and the header's spec id equals the recorded `spec.id`; labelled constructed hostile inputs (empty, one character, a character outside the alphabet, truncated, padded, an unknown version, a string far over any real length) are each refused with a reason and never crash or allocate by the input's claimed size; `make ci` green; reviewed by `domain-reviewer`, `security-reviewer` (external-format parser) and `code-reviewer`.

---

## [ ] M12-08 — `wowlab talents page`
**Size:** S–M · **Depends on:** M12-06

One static HTML file per ADR-0027 and §14.3: a tree drawn from `PosX`/`PosY` and the edges, the character's recorded build and saved plans overlaid, a tooltip per node from the tables. Read-only (§14.5 D3). Written under the user data directory or `--out`, refused inside an install.

**Acceptance:** the embedded JSON block parses back to the model's data; the Content-Security-Policy meta tag forbids every external request and the file makes none; a labelled constructed plan name and node text holding `</script>`, `<img onerror=…>`, U+2028 and U+202E are escaped in the HTML and in the JSON block; `--out` inside an install is refused; `make ci` green; reviewed by `security-reviewer`, `domain-reviewer` and `code-reviewer`.

---

## [ ] M12-09 — Every character: `labaddon.read_all` and `wowlab char list`
**Size:** S · **Depends on:** —

`labaddon.read_all` per §14.4: walk every character folder the layout finds (the digits-folder shape and the retail-style twin, both), read each `WowLab.lua`, return per character its label (the folder name), the file's modification time and either the parsed record or the reason it could not be read. One unreadable file names itself, exits 1 and never blocks the others. `wowlab char list [--account A] [--json]` prints the table (text through `cli._safe`).

**Acceptance:** on the committed captures, each character with a `WowLab.lua` is listed once and the `<Realm>/<First>/` twin folders that hold only `AddOns.txt` are not; a labelled constructed damaged file is named and the rest still listed, exit 1; the order is stable; `--json` validates; `make ci` green; reviewed by `domain-reviewer` and `code-reviewer`.

---

## [ ] M12-10 — `wowlab char page`: the alt-dashboard
**Size:** M · **Depends on:** M12-09, M12-01

One static HTML file per ADR-0027 and §14.4: `wowlab char page [--out PATH] [--anonymize] [--offline]`. A card per character (when the client last saved the file, build, spec, gear names from the recorded links' bracketed text and the average item levels, class-talent points spent against the tree currency, whether a customization record exists, collection counts, currencies and professions with names from `CurrencyTypes` and `SkillLine` when cached and ids otherwise, every section's `absent` reason), a comparison table by profession and currency, and the note that each card is the last saved session. `--offline` never fetches; `--anonymize` replaces every label with `Character 1…`.

**Acceptance:** graded on the committed captures: the embedded JSON block equals what `read_all` returns plus the names; with the recorded tables present, currencies and professions carry names, and without them ids; after `--anonymize` no character folder name occurs anywhere in the file's bytes; a labelled constructed character folder named `</script><img onerror=…>` and a link text holding U+2028 are escaped in the HTML and in the JSON block; the Content-Security-Policy forbids every external request; `--out` inside an install is refused; `make ci` green; reviewed by `security-reviewer`, `domain-reviewer` and `code-reviewer`.

---

## [ ] M12-11 — Capture a talent build with points spent
**Size:** S · **Depends on:** M12-06 · **owner** (optional; does not hold the wave review)

The one class-talent capture so far has a single node ranked (a level-13 character). The rules §14.3 leaves at **[verify]** (reachability along edges, gates, staged versus committed ranks, the export string with more than one node set) need a character with several nodes purchased, ideally the same character before and after a respec. The conductor writes the runbook (`docs/handoffs/M12-11.md`) once M12-06 merges: play, spend points, log out, run `scripts/lab_capture.py` on a new branch.

**Acceptance:** a new `macos-<build>` fixture root with provenance rows; `talents import-char` and `talents decode` agree on it; every **[verify]** it settles is recorded as a dated amendment in `docs/LAB_FORMATS.md` and §14.3; reviewed by `security-reviewer` and `domain-reviewer`, as every capture.

---

## [ ] M12-12 — Wave 3 review
**Size:** S · **Depends on:** M12-01 to M12-10

The conductor writes `docs/handoffs/M12-review.md` per `docs/LAB_PLAN.md` §11 (what shipped, what was learned about the client, which ideas are unblocked, which estimates were wrong, a recommended next wave of at most three ideas), archives the wave's backlog, and stops dispatch until the owner picks Wave 4.

---

# Carried forward from Wave 2 (M11), still open

The five follow-ups filed after the Wave 2 review stay here until they merge; the Wave 3 plan PR (2026-09-29) decided they are independent of Wave 3 except that M12-04 needs M11-30 merged. Their milestone stays M11.

## [ ] M11-28 — the remaining wall-clock timing checks
**Size:** S · **Depends on:** — · **[TEST]** (test-writer only)

M11-26's review found three more wall-clock budgets that can fail under load: the review probes `lab/core/tests/review/test_m10_06_toc_parse_quadratic_time.py` (`LIMIT_SECONDS = 1.0`, `perf_counter`) and `lab/core/tests/review/test_m10_13_unclosed_quote_quadratic_time_target.py` (`BUDGET_S = 2.0`), and the luadata grader `lab/core/tests/parser/test_luadata_constructed.py` around line 1060 (a 60 s budget). Measure CPU time with `lab/core/tests/parser/_cpu_clock.py` (M11-26), keeping every assertion and budget. The first two are review probes a reviewer cannot edit and the third grades the load-bearing `luadata.py`, so all three belong to a test-writer.

**Acceptance:** each passes under a CPU-saturating load started and stopped by PID (not while the owner is playing) and still fails against a constructed slow parse (scratch patch, reverted); `make ci` green; reviewed by `code-reviewer`.

*Filed 2026-09-29 from the M11-28 report:* `lab/core/tests/review/test_review_m10_02_followup_blank_runs.py:70` still has an absolute wall-clock budget (`crlf < 5` s, `perf_counter`). It is a review probe, so its change is test-writer work; file it with the next batch.

---

## [ ] M11-29 — lab-addon: the barber-shop record after an applied change
**Size:** S · **Depends on:** M11-23 · **owner** (a short in-game check)

From the M11-23 capture (1.60.1.70058; `docs/LAB_FORMATS.md`, M11-23 follow-up table): the owner opened the barber shop twice, the second time changing the hair colour and accepting, yet the record reads `recorded_at = "open"` although `BARBER_SHOP_APPEARANCE_APPLIED` registered; and `chr_model_id` is absent. Find why the applied event does not reach the section's handler (or whether a different event fires on Forever) and why `C_BarberShop` gives no model id; change the addon only for what a capture can confirm. Per `docs/AGENT_WORKFLOW.md`, any new API call is **[verify]**, sits in a section `/wowlab skip` can switch off, and gets a runbook step.

**Acceptance:** static addon tests (labelled source scans) for the change; the README and §13.1 say what is confirmed and what stays [verify]; an owner capture after one applied change shows `recorded_at = "applied"` (or the reason it cannot); reviewed by `code-reviewer` and `domain-reviewer`.

---

## [ ] M11-30 — terminal-safe error text
**Size:** S · **Depends on:** —

From the M11-23 and M11-25 security reviews. `cli._safe` escapes C0 and C1 control characters only, and `cli._note` keeps line breaks from inserted values, so a character folder name holding a newline, U+202E or another Unicode format or separator character (Cf, Zl, Zp) reaches stderr raw and can spoof a line. Escape those in `_safe` the way M11-25 escapes printed keys (UTF-8 `\ddd` or `\xHH`, consistently with the surrounding output), and have `_note` escape line breaks that come from inserted values while keeping the ones the message itself contains. Real character names with accented letters must still print as they are.

**Acceptance:** labelled constructed tests with folder names holding a newline, U+202E, U+2028 and an accented letter; no raw Cf, Zl, Zp or control character reaches stdout or stderr; `make ci` green; reviewed by `security-reviewer` and `code-reviewer`.

---

## [ ] M11-31 — snapshot store follow-ups from M11-24
**Size:** S · **Depends on:** M11-24

From the M11-24 reviews. (a) `snap gc` now keeps every `after` a committed journal record names, including content a `snap restore` or profile apply wrote from snapshots the owner later deletes, so that space is never reclaimed; keep only what the loader check can need (the `after` of writes to a character's `WowLab.lua`), and say in `snap gc --help` what is kept. (b) A directory planted at an object path makes `put_object` raise a raw `IsADirectoryError`; wrap `OSError` as `SnapshotError` with a clear message. (c) The module docstring of `lab/core/tests/review/test_m11_24_snap_gc_deletes_the_kept_lab_write_object.py` still describes the removed "follow the advice once" scenario; a test-writer corrects it (reviewers cannot edit an existing probe). Part (a) changes what the loader check can rely on, so its graders come from a test-writer first (`M11-31T`).

**Acceptance:** (a) graded by a test-writer and green; (b) a labelled constructed test; (c) the docstring matches the file; `make ci` green; reviewed by `security-reviewer` and `code-reviewer`.

*Amended 2026-09-29 (from the M11-31T report):* (c) is done in `M11-31T`. The graders pin the account-wide `WowLab.lua` and `WowLab.lua.bak` as reclaimable and every write to a character's `WowLab.lua` as kept, not only the latest. The implementation must also update the "Nothing to collect … a committed write in the journal" message in `snap_gc`, the `put_object` and `gc` docstrings in `snapshot.py`, and add a dated §13.4 amendment; none of those is graded.

---

## [ ] M11-32 — `char show` Legacy wording, two tweaks
**Size:** S · **Depends on:** M11-27

From the M11-27 domain review (optional then, filed now). (1) The several-config second line should say the active class-talent config is also excluded, since on Forever it is type 4, not Combat. Replacement: `  the addon lists every trait config except the active class talents and the types below; which of these is the Legacy system is not recorded; panel opener ToggleLegacySystemUI present: yes` (the last clause as the code computes it). (2) When two or more configs are listed and all are absent, the second line still says "inferred by elimination"; use the line from (1) whenever more than one config is listed.

**Acceptance:** labelled constructed tests for both; real-fixture output unchanged (both captures have one config); reviewed by `domain-reviewer`.

*Amended 2026-09-29 (PR #138 reviews):* the replacement text in (1) can say something false: the addon excludes the active class config only when the client gave it the id, and a config found by system id is not type-checked. The built wording follows the domain review (`docs/LAB_PLAN.md` §13.1, M11-32 amendment), and a config list of zero no longer says "inferred by elimination". Follow-ups filed from that review: the reader notes when a listed candidate's id equals the `talents.class` config id; the addon records which id `talents.legacy` excluded (a schema change); the comments at `lab/addon/WowLab/Talents.lua` lines 8 and 279–280 describe the rule without those conditions.
