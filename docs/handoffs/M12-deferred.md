# Wave 3 (M12): deferred tickets

Moved here word for word from `docs/BACKLOG.md` by the re-scope of 2026-10-14 (`docs/LAB_PLAN.md` §14.8), after the owner decided on 2026-10-13 that wowlab will be usable by other players (ADR-0031). They are not open work: they come back only through a later wave pick (ADR-0024), and are re-checked against `main` then. The spec for them is `docs/LAB_PLAN.md` §14.1 to §14.7. M12-03's implementation is on PR #158 (draft, branch `m12/03-db2lake`, kept).

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

## [ ] M12-10 — `wowlab char page`: the alt-dashboard
**Size:** M · **Depends on:** M12-09, M12-01

One static HTML file per ADR-0027 and §14.4: `wowlab char page [--out PATH] [--anonymize] [--offline]`. A card per character (when the client last saved the file, build, spec, gear names from the recorded links' bracketed text and the average item levels, class-talent points spent against the tree currency, whether a customization record exists, collection counts, currencies and professions with names from `CurrencyTypes` and `SkillLine` when cached and ids otherwise, every section's `absent` reason), a comparison table by profession and currency, and the note that each card is the last saved session. `--offline` never fetches; `--anonymize` replaces every label with `Character 1…`.

**Acceptance:** graded on the committed captures: the embedded JSON block equals what `read_all` returns plus the names; with the recorded tables present, currencies and professions carry names, and without them ids; after `--anonymize` no character folder name occurs anywhere in the file's bytes; a labelled constructed character folder named `</script><img onerror=…>` and a link text holding U+2028 are escaped in the HTML and in the JSON block; the Content-Security-Policy forbids every external request; `--out` inside an install is refused; `make ci` green; reviewed by `security-reviewer`, `domain-reviewer` and `code-reviewer`.

---

## [ ] M12-11 — Capture a talent build with points spent
**Size:** S · **Depends on:** M12-06 · **owner** (optional; does not hold the wave review)

The one class-talent capture so far has a single node ranked (a level-13 character). The rules §14.3 leaves at **[verify]** (reachability along edges, gates, staged versus committed ranks, the export string with more than one node set) need a character with several nodes purchased, ideally the same character before and after a respec. The conductor writes the runbook (`docs/handoffs/M12-11.md`) once M12-06 merges: play, spend points, log out, run `scripts/lab_capture.py` on a new branch.

**Acceptance:** a new `macos-<build>` fixture root with provenance rows; `talents import-char` and `talents decode` agree on it; every **[verify]** it settles is recorded as a dated amendment in `docs/LAB_FORMATS.md` and §14.3; reviewed by `security-reviewer` and `domain-reviewer`, as every capture.
