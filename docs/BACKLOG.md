# Backlog

Agent-sized tickets. Each is independently reviewable and has a verifiable acceptance criterion. Dependencies are explicit; anything with no unmet dependency can be worked in parallel.

Estimates assume one agent per ticket. `S` = under half a day, `M` = about a day, `L` = multiple days.

**Status lives in the heading:** `## [ ] M10-04 — …` is open, `## [x] …` is done. A ticket also counts as done when a merged PR title carries its id in parentheses. Implementers never edit this file; the conductor ticks headings in a batched `docs(backlog)` PR. A grader ticket carries a `T` suffix (`M10-04T`, **[TEST]**, test-writer) and lands before its implementation twin (`M10-04`, **[IMPL]**, implementer) — see ADR-0013. Tickets marked **owner** need the repository owner for a real capture or a decision.

The backlog holds the current wave only (ADR-0024). Milestone numbers start at `M10`; earlier numbers belonged to the retired project (ADR-0025) and are not reused.

---

# M10 — Core library (Wave 1)

Spec: `docs/LAB_PLAN.md`. Formats: `docs/LAB_FORMATS.md`. Paths: `docs/LAB_FILE_MAP.md`. Decisions: ADR-0019 to ADR-0025. Hard invariants L1–L8 (`AGENTS.md`) apply to every ticket below. This is the whole of Wave 1; nothing from `docs/LAB_IDEAS.md` is a ticket until the owner picks the next wave (ADR-0024).

## [x] M10-01 — Scaffold and gates
**Size:** S · **Depends on:** nothing

Create `lab/core/` as the uv workspace's only member, `wowlab-core` (import `wowlab_core`, hatchling, `src/` layout, a `wowlab` console-script entry pointing at a Typer app that only implements `--version`). Wire it into the root: workspace `members`, root `dependencies`, `uv.sources`, mypy `files`, pytest `testpaths`, isort `known-first-party`. `make test-parser` runs `lab/core/tests/parser` under the `parser` marker, with a day-one test that holds the fixture index to the files on disk so the required check is never an empty job. Add `lab/README.md`, `lab/core/tests/fixtures/README.md` (index table: `file | kind | flavor | client_version | platform | captured_by | consent | scrub | edge_cases`), `lab/core/tests/review/` for reviewer probes, and `.gitignore` entries for Lab scratch. Add a non-required `lab (windows)` CI job that runs `pytest lab/core/tests` on `windows-latest`.

**Acceptance:** `make ci` green; `uv run wowlab --version` prints a version; `uv lock --check` clean; `make test-parser` exits 0 on an empty corpus; the Windows job runs and is green.

*Done in the reset PR (ADR-0025), by the main session rather than an implementer, because the same change removed the previous project and rewrote the harness. The first run of the Windows job is that PR's CI.*

---

## [x] M10-02 — Fixture capture and scrub tool
**Size:** M · **Depends on:** M10-01

`scripts/lab_capture.py`, standard library only, runnable as `uv run python scripts/lab_capture.py --root <install> --out lab/core/tests/fixtures/incoming/`. Copies the capture set in `docs/handoffs/M10-03.md` from a real install and scrubs it per `docs/LAB_PLAN.md` §8: stable pseudonyms for account folder, character and realm names (in paths and in file contents), identity CVars blanked, and a hard refusal (non-zero exit, nothing written for that file) if an email address, a BattleTag or an unmapped `Player-<n>-<hex>` GUID survives. Byte-level targeted replacement only; it never parses and re-serializes. Prints a provenance row per file ready to paste into the index. Opens the install read-only and writes only under `--out` (L1).

**Acceptance:** run against a synthetic install tree built in the test (our own layout, so constructed input is legitimate): pseudonyms are stable across files; untouched bytes are identical (assert on a diff of offsets); the three refusal cases refuse; `--dry-run` writes nothing. `gitleaks` config extended if the scrubbed output trips it for a benign reason, with the reason in the PR. Reviewed by `security-reviewer`.

*Follow-ups 4 and 5 merged (#63, 2026-09-23; #64, 2026-09-27): `--pseudonymise-other-players`; invented location parts of unit GUIDs in combat logs; each combat log's timestamps and file name moved by its own secret offset (owner decision 2026-09-24); `--combat-log NAME`; folded identity checks. Not scheduled: U+2212, U+2043, U+30FC, prime and backtick between letters are not treated as separators (the client does not write them); the byte-oracle test proves bytes outside spans, not what each span became; scrub time about 1.4× on large non-ASCII SavedVariables.*

*Follow-up 6 (owner-approved 2026-09-28): treat U+2212, U+2043, U+30FC, prime (U+2032) and backtick as separators between letters in every identity check (detect and refuse only), with constructed tests; the client does not write them, so this is defence in depth. Reviewed by `security-reviewer`.* Merged 2026-09-28 (#85), with `_`, LF, CR, VT and FF added to the loose byte check and U+30FC no longer hiding an adjacent short name.

---

## [x] M10-03 — Capture the fixture corpus
**Size:** S · **Depends on:** M10-02 · **owner** (needs the machine with the game installed)

Follow `docs/handoffs/M10-03.md`: log one character in and out on each installed flavor (retail; Forever beta if installed), run the capture tool, review the output, commit it with index rows. Record in `docs/DATA_SOURCES.md` (Local game install section and breakage log) what the capture shows for every **[verify]** item in `docs/LAB_FORMATS.md` and `docs/LAB_FILE_MAP.md`: Forever's flavor folder, product code, version string, interface number, executable name, preferred TOC suffix, SavedVariables line endings per platform, and whether the Forever client writes a combat log.

**Acceptance:** the corpus covers the coverage list in the handoff; every file has an index row; `gitleaks` clean; `docs/LAB_FORMATS.md` has a dated amendment per **[verify]** item resolved or still open.

*Start this as soon as M10-02 merges. Everything that parses a client format waits on it.*

*Amended 2026-09-22 (owner decision): the coverage list's multi-MB SavedVariables file is waived for now: no SavedVariables file on the Forever install is much over 14 KB (the largest, `Syndicator.lua`, is 14,484 bytes as committed). M10-04 measures its performance target on a constructed input and says so (§6.4 amendment item 5); a real large file is added when one exists.*


*Done 2026-09-28: parts 1–3 merged (#35, #51, #80). Coverage list met or waived: the multi-MB SavedVariables file waived (owner, 2026-09-22); no installed addon ships more than one TOC; the combat log is a solo open-world fight with shifted timestamps, so `COMBATANT_INFO` and groups stay open (M10-13 grades them on constructed lines; a boss-pull log is an optional owner capture). Every **[verify]** item has a dated amendment in `docs/LAB_FORMATS.md` or `docs/DATA_SOURCES.md`, resolved or recorded open.*
---

## [x] M10-04T — luadata parser graders [TEST]
**Size:** M · **Depends on:** M10-03

Graders for `wowlab_core.luadata` parsing, derived from `docs/LAB_PLAN.md` §6.4, `docs/LAB_FORMATS.md` §4 and the real corpus: every real SavedVariables fixture parses; key order, key style, number source text and trailing comments are preserved; duplicates kept and flagged; `to_python()` behaviour; every rejection in §4.3 raises the typed error with line and column (constructed, labelled); depth, size and string bounds raise rather than truncate or crash; a 10 000-deep table raises without a `RecursionError` escaping. Marked `parser`, `xfail(strict=True, reason="M10-04 not implemented")`.

**Acceptance:** graders fail today for the asserted reason; `make test-parser` reports them as xfail; no implementation code.

---

## [x] M10-04 — luadata parser [IMPL]
**Size:** L · **Depends on:** M10-04T

`lab/core/src/wowlab_core/luadata.py` per `docs/LAB_PLAN.md` §6.4 (parsing half). No Lua execution, no third-party parser (L3). Activate graders by deleting marker lines only.

**Acceptance:** `make test-parser` green with all M10-04T markers removed; parse time and peak RSS for the largest real fixture pasted in the PR, plus a generated 50 MB document (generator script in `lab/core/tests/`, labelled constructed) against the §6.4 target. If the target is missed, report numbers and stop (ADR-0020).

*Merged 2026-09-27 (#58), pure Python under the §6.4 cost budget (`MAX_COST`); owner decisions of 2026-09-23 (option 1), 2026-09-24 (a document over the budget is refused even under 50 MB) and third and fourth fix rounds (2026-09-24, 2026-09-27). Follow-ups recorded at merge: a `m10/04-luadata-followups` ticket is running for the Windows CI timing probes, the refusal speed on mixed line endings, the backslash-before-NUL message and the §6.4 "Slowest" row. Not scheduled: `to_python()` on one key of about 10M escaped line breaks takes about 6.6 s (bounded by the per-backslash charge); the review probe `test_m10_04_bounded_document_misses_memory_target.py` still imports `MAX_ENTRIES` as if it were the bound (passes; stale).*

---

## [x] M10-05 — Install discovery
**Size:** M · **Depends on:** M10-03

`wowlab_core.install` per `docs/LAB_PLAN.md` §6.1 and `docs/LAB_FORMATS.md` §1–§2. No flavor, product or version constants in library code (L6); a test greps the package for `_retail_`, `_classic` and `wow_classic` and fails on a hit outside comments.

**Acceptance:** real `.build.info` / `.flavor.info` fixtures from each captured platform resolve to the expected `Install`; unknown columns preserved; a flavor folder with no matching row is returned with `version=None`; `WOWLAB_WOW_ROOT` and explicit root override defaults; a root without `.build.info` raises the typed error; nothing is written anywhere (assert on a read-only temp tree).

---

## [x] M10-06 — Layout walker, file map and TOC parser
**Size:** L · **Depends on:** M10-05

`wowlab_core.layout`, `wowlab_core.toc`, `wowlab_core.filemap` per `docs/LAB_PLAN.md` §6.2–§6.3, `docs/LAB_FORMATS.md` §3, `docs/LAB_FILE_MAP.md`. The file map is one data source shared by the doc and `classify()`; state in the PR which way it is kept in sync and add a test that fails when they drift. Reviewed by `domain-reviewer` (file-map wording).

**Acceptance:** against the captured tree: accounts, realms, characters, SavedVariables (scope and owning addon), addons with all their TOCs, WTF files and loose overrides are inventoried; every path in the captured tree gets a `classify()` result or an explicit `Unclassified` (list them in the PR; each becomes a file-map row or a stated omission); real TOC fixtures round-trip directive order and unknown directives; symlinks pointing outside the install are reported and not followed; 400 synthetic addon folders inventory in under two seconds.

---

## [x] M10-07 — WTF text formats
**Size:** M · **Depends on:** M10-03

`wowlab_core.wtfconfig` per `docs/LAB_PLAN.md` §6.5 and `docs/LAB_FORMATS.md` §5–§7. Read-only, lossless.

**Acceptance:** for every real `Config.wtf`, `config-cache.wtf`, `bindings-cache.wtf` and `macros-cache.txt` fixture, joining `lines` reproduces the file byte for byte; case-insensitive CVar lookup with original case preserved; duplicate CVars reported with the last effective; multi-line macro bodies intact; unknown lines typed `Unknown` and preserved.

---

## [x] M10-08 — Game data client
**Size:** M · **Depends on:** M10-01

`wowlab_core.gamedata` per `docs/LAB_PLAN.md` §6.6 and ADR-0022. Record the builds endpoint and one small table's CSV from wago.tools as fixtures (manual `@pytest.mark.live` capture run by the implementer, responses committed, no credentials involved); add the source to `docs/DATA_SOURCES.md` with the URL shapes the recordings show.

**Acceptance:** replayed tests cover: cache miss then hit; an existing cached build is never overwritten (L5); interrupted download leaves no partial file under the final name; sidecar records URL, time, size, SHA-256; 429 and 5xx back off and retry; `BuildNotPublished` for an unknown build; cache lives under the user data directory, never in the repo or an install. No live call in CI.

---

## [x] M10-09 — Client process detection
**Size:** S · **Depends on:** M10-01

`wowlab_core.process` per `docs/LAB_PLAN.md` §6.7. `psutil` process listing only (ADR-0023).

**Acceptance:** with a fake process table injected: matches by executable path under an install root and by known names; access-denied yields `unknown`; returns pid, exe path and flavor folder when derivable. A test asserts the module calls nothing on `psutil.Process` beyond `pid`, `name`, `exe`, `cmdline`, `status`. Reviewed by `security-reviewer`.

*Amended 2026-09-21: the allowed surface is `pid`, `name`, `exe`, `status`, `cmdline` (non-Windows only, where it is not a read of the target's process memory), plus set and restore of the instance attribute `cmdline` on `psutil.Process` objects around each `exe()` call; "access-denied yields `unknown`" means the narrower rule in the `docs/LAB_PLAN.md` §6.7 amendment of the same date.*

---

## [x] M10-10 — Snapshot store
**Size:** L · **Depends on:** M10-01

`wowlab_core.snapshot` per `docs/LAB_PLAN.md` §6.9. Works on any directory tree with explicit subtrees; integration with `layout` defaults happens in M10-14.

**Acceptance:** identical trees produce byte-identical manifests apart from id and timestamp fields (assert with those fixed); unchanged files are stored once across snapshots; `diff` reports added, removed, changed; `verify` detects a corrupted object; `gc` dry-run lists exactly the unreferenced objects and a real run removes only those; manifests are immutable (no API mutates one except the guarded label write); store path is under the user data directory and creating a snapshot writes nothing inside the source tree (assert on a read-only source).

---

## [x] M10-11T — Write gate graders [TEST]
**Size:** M · **Depends on:** M10-09, M10-10

Graders for `wowlab_core.guard` from `docs/LAB_PLAN.md` §6.10 and ADR-0021, against a synthetic install in `tmp_path` with an injected process probe: refuses when the client is running and when its state is unknown; refuses every path outside the allowlist, including `Data/`, executables, `.build.info`, `.flavor.info`, the install root, `..` traversal, absolute paths, a symlink that escapes, and a case-variant of a forbidden path on a case-insensitive filesystem; takes a snapshot before the first write; journals before/after hashes; atomic replace (a simulated crash between temp write and rename leaves the original intact); an exception inside the transaction rolls every touched path back; `undo()` restores the pre-transaction bytes; dry-run touches nothing; no code path or flag skips the snapshot or the client check (assert on the public signature). `xfail(strict=True, reason="M10-11 not implemented")`.

**Acceptance:** graders fail today for the asserted reason; no implementation code.

---

## [x] M10-11 — Write gate and restore [IMPL]
**Size:** L · **Depends on:** M10-11T

`lab/core/src/wowlab_core/guard.py`. Activate graders by deleting marker lines only. A repository-wide test greps `lab/` for `open(` with a write mode, `write_text`, `write_bytes`, `os.replace`, `shutil.copy*`, `shutil.move`, `unlink` and `rmtree` outside `guard.py`, `snapshot.py`, `gamedata.py` and tests, and fails on a hit that is not allowlisted with a reason (L2).

**Acceptance:** all M10-11T graders green; the write-site test green; on Windows CI the locked-file case reports a typed error and rolls back. Reviewed by `security-reviewer`.

*Amended 2026-09-21 (owner decision after the M10-11T reviews): the non-required `lab (windows)` job must be green on the PR's final head (the `pull_request` run, which GitHub makes on the test merge commit), with the run link pasted in the PR before merge, because 32 of the write-gate graders (junctions, alternate data streams, reserved names, drive-letter and backslash escapes, the locked-file rollback) run only there. `guard` calls `wowlab_core.process` with its default probe only (no probe parameter on `transaction` or `undo`), passes the install root (`install_roots`), the flavor folder (`flavor_folders`) and the executable names it finds in the flavor folder (`extra_names`), and treats `unknown` or any probe exception as running (`docs/LAB_PLAN.md` §6.7 amendment). Snapshot manifests and the journal are untrusted at rollback, undo and restore time.*

*Follow-ups recorded 2026-09-22 at merge (#43), not part of this ticket's acceptance: (1) graders pinning behaviour the M10-11 reviews verified but no grader covers: the chain re-check after each rename/unlink forcing rollback, the restore-all skip rules for an unchanged executable-suffix file (mode, missing, symlink, reserved names), undo/restore refusing another install, the store-shard overlap check, the target-unchanged check and the part-way-restore rollback; (2) a lock against two concurrent transactions on one install or store (the spec is silent; the journal sequence number `_next_record_id` assigns can repeat, so `undo`'s choice of the most recent record becomes arbitrary); (3) cleanup of `.wowlab-*.tmp` files a hard kill leaves inside the install. (2) and (3) need a spec decision before a ticket.*

---

## [x] M10-12T — luadata serializer graders [TEST]
**Size:** S · **Depends on:** M10-04

Graders from `docs/LAB_PLAN.md` §6.4 (serializer half) and `docs/LAB_FORMATS.md` §4.2: `serialize(parse(x)) == x` byte for byte for every real SavedVariables fixture (parametrized over the index); after changing one leaf, every untouched sibling subtree's bytes are unchanged; a new positional entry, string key and number key follow the source document's own detected style (indentation or none, `-- [n]` comments or none, separators, line endings; owner decision 2026-09-22, §6.4), graded on an unindented Forever fixture and a constructed tab-indented, commented document (labelled `constructed`, L8: a stand-in until a real capture shows that style; replace it when one does); a new document with no source follows the sibling-file style, else §4.2; a property absent from the document falls back the same way; 100 runs produce identical bytes, including under a non-C locale. `xfail(strict=True, reason="M10-12 not implemented")`.

**Acceptance:** graders fail today for the asserted reason.


*Merged 2026-09-28 (#66): 142 graders of the §6.4 amendment of 2026-09-27, items 1–9 (owner decisions: fallback layout, pairing, newest sibling wins, data only; clarifications #70, #71). For M10-12: a refused `Entry.comment` is positioned at its `--` and a refused `nil` at `nil` (conductor ruling). Not graded yet: positions in CR-only or LF-CR documents; `lab_written` through a symlink.*
---

## [x] M10-12 — luadata serializer [IMPL]
**Size:** M · **Depends on:** M10-12T

**Acceptance:** `make test-parser` green with all M10-12T markers removed. Any real fixture that cannot round-trip is a finding against the parser's document model, reported rather than special-cased.


*Merged 2026-09-28 (#74). Follow-ups recorded at merge, not scheduled: skip a sibling folder with over 65,536 entries so a hostile folder is never read in full (today about 2.5 s and 450 MB per million entries; the owner's machine only); a Windows junction test via `_winapi.CreateJunction`; `_path_key` casefolds on case-sensitive Linux too (can only lose a style source); serialize at 50 MiB takes about 8.5 s since the output is re-parsed before return (§6.4 sets no serializer target).*
---

## [x] M10-13 — Combat log tokenizer
**Size:** M · **Depends on:** M10-03

`wowlab_core.combatlog` per `docs/LAB_PLAN.md` §6.8 and `docs/LAB_FORMATS.md` §8. If M10-03 found that the Forever client writes no usable combat log, ship against retail fixtures and say so in the module docstring and the PR.

**Acceptance:** every line of each real fixture tokenizes or is yielded as `Unparsed` (report the count and the distinct unparsed shapes); quoted commas and nested `[...]`/`(...)` groups handled (`COMBATANT_INFO` fixture line); `follow()` yields appended records, survives truncation and a rotated file name.


*Amended 2026-09-27 (owner, 2026-09-24): the Forever fixture is an open-world log with no `COMBATANT_INFO` and no `[...]`/`(...)` groups (`docs/LAB_FORMATS.md` §8 amendment). Until a real boss-pull log is captured (the owner will record one), quoted commas and nested groups are graded on constructed lines labelled `constructed` (L8), written from §8 and community documentation and marked **[verify]**; every line of the real fixture must still tokenize. When a real `COMBATANT_INFO` line is committed, a grader on it replaces the constructed one. Records carry shifted timestamps (M10-02 follow-up 5): the tokenizer must not assume the timestamp is the real time.*

*Amended 2026-09-28 from the M10-14 review: `wowlab log tail` (§6.11) is built with this ticket, on top of `combatlog.follow()`, since M10-14 shipped without it.*
---

## [ ] M10-14 — `wowlab` CLI
**Size:** M · **Depends on:** M10-04, M10-05, M10-06, M10-07, M10-08, M10-10, M10-11, M10-16

Commands, flags and exit codes per `docs/LAB_PLAN.md` §6.11. `snap create` uses `layout` to resolve the default subtrees. `snap restore` and `undo` go through `guard`, print the plan, and ask unless `--yes`. Reviewed by `domain-reviewer` (output wording).

**Acceptance:** each command has a test through Typer's runner against the captured tree or a synthetic install; every data command's `--json` output validates against its Pydantic model; exit code 3 when guard refuses; `wowlab explain <path>` returns the file-map entry for ten representative paths. A transcript of `wowlab doctor`, `tree --explain`, `sv dump`, `snap create`, a guarded edit and `undo` on a synthetic install is pasted in the PR.

*Amended 2026-09-21 from the M10-08, M10-09 and M10-10 reviews: `snap diff` includes the `luadata`-aware structural diff for `.lua` SavedVariables that `docs/LAB_PLAN.md` §6.9 specifies, falling back to the plain changed-path entry when either side does not parse (deferred from M10-10 because `luadata` did not exist; `SnapshotStore.read_file()` is the seam). Manifest `--json` output goes through `snapshot.manifest_bytes()`, not `model_dump_json()`, which is not the inverse of the manifest wire format. `doctor` passes each discovered install root and the executable names it finds in each flavor folder to `process` (`install_roots`, `extra_names`; discovery does not report executable names, reworded 2026-09-22 after the M10-05 review), and after a cross-product `gamedata.resolve_build` match prints only `.version` as a fact about the install, never the matched `product` or config hashes. `snap list` lists every manifest that loads and names each one that does not, exiting non-zero when any is damaged (`SnapshotStore.list()` raises on the first bad one today; a lenient public listing belongs in `snapshot`, not in private calls from the CLI).*

*Amended 2026-09-22 from the final M10-05 domain review (deferred there): when `doctor` or `install show` reports where it looked for an install, a default location that could not be checked (not a directory, or its check raised, including a drive that is not ready) is reported as "could not check", not as searched and empty; `InstallNotFoundError` gains that list, and the two `install.py` docstrings that still say "not a readable directory" are brought in line with the reworded §6.1 amendment ("not a directory, or cannot be checked").*

*Amended 2026-09-22 from the PR #49 security review: `snap gc` runs inside `guard.store_lock(store)` (§6.10 amendment item 4) for its whole run, because rollback and undo now rely only on objects in the store.*

---

## [x] M10-16T — Guard lock, temp cleanup and changed-file graders [TEST]
**Size:** M · **Depends on:** M10-11

Graders for the 2026-09-22 amendment to `docs/LAB_PLAN.md` §6.10 (owner decisions after the M10-11 reviews): (1) one writer at a time through two OS advisory locks, `GuardBusyError`; (2) cleanup of guard's own leftover temp files named in earlier journal records; (3) `ChangedSinceSnapshotError` for a file that differs from the pre-write snapshot at first touch or just before replace/unlink. (4) The public surface of item 4: `guard.store_lock` raises `GuardBusyError` while a transaction on that store is open, in another process and in the same one, and a transaction raises it while `store_lock` is held; `GuardBusyError` and `ChangedSinceSnapshotError` are exported; `HistoryRecord.temps_removed`/`temps_left`; a format-1 journal record still reads through `history()` and `undo()`. `guard.py` exists, so each grader is `xfail(strict=True)` with one marker line, fails today only because the behaviour is missing, and is activated by marker deletion. Derived from the spec, not from `guard.py` internals beyond its public API. Amends, per the amendment and only where it makes the old expectation wrong, the existing graders it contradicts: `test_constructed_journal_lives_with_the_store_and_leaves_the_store_healthy` (only `locks/` may appear under the user data directory), and the `…treat_the_pre_write_snapshot_as_untrusted` and `…undo_refuses_a_poisoned_entry…` graders (a refusal with nothing written is also accepted where item 3 applies); each amended grader is listed with its reason in the PR. `FORBIDDEN_IMPORTS` stands. The session that writes these never writes M10-16.

**Acceptance:** every grader fails today for the missing behaviour and no other reason; a scratch patch (not committed) shows they are satisfiable; each behaviour has a mutant that turns its graders red; Windows lock semantics are graded where the platform differs (skips carry a reason); `make ci` green.

---

## [x] M10-16 — Guard lock, temp cleanup and changed-file refusal [IMPL]
**Size:** M · **Depends on:** M10-16T

`lab/core/src/wowlab_core/guard.py` per the §6.10 amendment of 2026-09-22. Locks use the standard library only (`fcntl` on POSIX, `msvcrt` on Windows); no new runtime dependency. Activate the M10-16T graders by deleting marker lines only.

**Acceptance:** all M10-16T graders and every existing guard grader, as amended by M10-16T, green; the write-site test green; `lab (windows)` green on the PR's final head with the run link pasted in the PR; reviewed by `security-reviewer`.

*Follow-ups recorded 2026-09-23 at merge (#59), not part of this ticket's acceptance: graders (a test-writer ticket) for behaviour the M10-16 reviews verified only with scratch probes: the Windows lock at offset 2^30; store and lock places refused inside any install (including `store_lock`); lock files with more than one link refused; an unreadable journal refused before the snapshot; journal and manifest paths looked up only when local absolute; a forked child neither unlocking nor committing; locks handed to a transaction covering its store and install; an interrupted mutation breaking the transaction; and rules no grader pins yet: store-before-install lock order, the cleanup's chain re-check after the unlink, the link refusals on the user data directory and `locks/`, and a Windows junction at `locks/` or the user data directory.*

---

## [x] M10-17T — Guard store creation and undo-by-id graders [TEST]
**Size:** S · **Depends on:** M10-16, M10-14

Graders from the M10-14 reviews (2026-09-27/28), for `lab/core/src/wowlab_core/guard.py`'s public API: (1) `guard.store_lock(store, create=True)` refuses a store inside any install (a `.build.info`/`.flavor.info` marker on the place or an ancestor, through links and junctions) before anything at the path is created, then creates the store and takes the lock, so a first `snap create` holds the store lock like every later one; (2) `guard.undo(..., expected_id=...)` checks the journal's last record id under the store lock and refuses with nothing written when it differs, so an undo never acts on a record the user did not approve. Each grader `xfail(strict=True)` with one marker line, activated by marker deletion. The session that writes these never writes M10-17.

**Acceptance:** every grader fails today for the missing behaviour only; a scratch patch (not committed) shows they are satisfiable; a mutant per behaviour turns its graders red; `make ci` green.

---

## [x] M10-17 — Guard store creation and undo-by-id [IMPL]
**Size:** S · **Depends on:** M10-17T

`guard.store_lock(create=True)` and `guard.undo(expected_id=...)` per M10-17T; then the CLI takes the store lock for the first `snap create` (dropping the one-hour gc grace as the only protection, or keeping it as defence in depth; say which) and passes `expected_id` from `undo`, and `snapshot.py`'s copy of the inside-any-install rule is replaced by guard's. Activate graders by marker deletion only.

**Acceptance:** all M10-17T graders and every existing guard grader green; the write-site test green; `lab (windows)` green on the final head; reviewed by `security-reviewer`.


*Merged 2026-09-28 (#73 graders, #77 implementation). Follow-ups, not scheduled: a test-writer comment on the `dotdot-after-a-symlink` grader saying it intentionally requires refusing both readings (Windows collapses `..` as text before following links); optionally run the store/source overlap check before `store_lock` in `snap create`, so a store that is an ancestor of the install gets no stray empty lock file (outside the install).*
---

## [x] M10-18T — luadata sibling-listing follow-up graders [TEST]
**Size:** S · **Depends on:** M10-12

Owner-approved follow-ups (2026-09-28) from the M10-12 reviews, graded on `luadata.serialize`'s public behaviour: (1) a sibling folder holding more than 65,536 entries is skipped whole during style detection (never read or sorted in full), and whether a folder is skipped depends only on its entry count, never on listing order; (2) on Windows, a directory junction under `WTF/Account` (made with `_winapi.CreateJunction`, no privilege needed) is never walked into or read; (3) a missing target or `lab_written` path is compared case-insensitively only where the filesystem is case-insensitive, so on a case-sensitive volume a separate sibling `A.lua` still counts when the target is `a.lua`. Constructed trees, labelled per L8; `xfail(strict=True)` with one marker line each. The session that writes these never writes M10-18.

**Acceptance:** every grader fails today for the missing behaviour only; satisfiable by an uncommitted scratch patch; a mutant per behaviour; `make ci` green.

---

## [x] M10-18 — luadata sibling-listing follow-ups [IMPL]
**Size:** S · **Depends on:** M10-18T

`luadata.py` per M10-18T, with a dated line in `docs/LAB_PLAN.md` §6.4 for the per-folder cap. Activate graders by marker deletion only.

**Acceptance:** all M10-18T and every existing luadata grader green; `make test-parser` green; `lab (windows)` green on the final head; reviewed by `security-reviewer`.

---

## [x] M10-19T — Guard grader polish [TEST]
**Size:** S · **Depends on:** M10-17

From the M10-17 reviews (owner-approved 2026-09-28), test-only: a comment on the `dotdot-after-a-symlink` grader saying it intentionally requires refusing both readings (Windows collapses `..` as text before following links); the creating-call recorder in `test_guard_store_create_undo_id.py` also records builtin `open`/`io.open` in create or write modes, `os.link`, `os.symlink`, `os.mkfifo`, `os.mknod` and the destinations of `os.rename`/`os.replace`, so a create-and-remove is caught on filesystems with coarse timestamps. No grader is weakened; every existing assertion stays.

**Acceptance:** the guard suite green on the current `guard.py`; a mutant that creates a file with builtin `open` before the check turns a grader red; `make ci` green.

---

## [x] M10-19 — `snap create` checks overlap before taking the store lock
**Size:** S · **Depends on:** M10-17

From the M10-17 security review (owner-approved 2026-09-28): `wowlab snap create` runs the store/source overlap check before `guard.store_lock(create=True)`, so a store path that is an ancestor of the install is refused without leaving an empty `lock` file there. Tests through Typer's runner; `snapshot`/`guard` public APIs only.

**Acceptance:** a test shows the ancestor case refused with nothing created; the existing CLI tests green; `make ci` green.

---

## [x] M10-15 — Wave 1 review
**Size:** S · **Depends on:** M10-12, M10-13, M10-14, M10-16 · **owner**

The conductor writes `docs/handoffs/M10-review.md` per `docs/LAB_PLAN.md` §11 and stops dispatch. The owner runs the CLI against the real install (`wowlab doctor`, `wowlab snap create -m baseline`, one guarded change and `wowlab undo`), notes what was wrong or missing, and picks Wave 2.

**Acceptance:** the review file exists; the owner's pick is recorded in it; the next wave's plan, ADRs and tickets land in one docs PR.


*Written 2026-09-28: `docs/handoffs/M10-review.md`. The owner picked Wave 2 the same day (lab-addon, customization-sandbox, profiles, sv-merge).*
---

# Parallelization

Dependency shape: `docs/LAB_PLAN.md` §10.

- **Now** (M10-01 is done): M10-02, M10-08, M10-09 and M10-10 are independent of each other. Dispatch all four.
- **M10-03** needs the owner at the machine with the game installed, as soon as M10-02 merges. It is the long pole on wall-clock time; tell the owner the moment M10-02 lands.
- **After M10-03:** M10-04T, M10-05, M10-07 and M10-13 are independent. M10-06 follows M10-05. M10-04 follows M10-04T; M10-12T and then M10-12 follow M10-04.
- **M10-11T** opens when M10-09 and M10-10 are both merged and does not wait on the capture; M10-11 follows it.
- **M10-14** waits on every module; **M10-15** closes the wave and dispatch stops until the owner picks the next one (ADR-0024).

---

# Definition of done

A ticket is done when: CI is green on every required check; each acceptance criterion has pasted proof; `make lint` passes, `mypy --strict` included; new external-format parsing is covered by a real, scrubbed fixture with a provenance row; any change to a load-bearing file (`luadata.py`, `guard.py`) has `make test-parser` and the full suite green with graders activated by marker deletion only; nothing writes into an install outside `guard` and no test needs a real install; the independent review verdict is clean; every review thread was replied to and resolved; and anything contradicting an existing ADR ships with a superseding ADR (`Proposed`) rather than a silent deviation. Full lifecycle: `docs/AGENT_WORKFLOW.md`.

---

# M11 — lab-addon, customization-sandbox, profiles, sv-merge (Wave 2)

Spec: `docs/LAB_PLAN.md` §13. Decisions: ADR-0026, ADR-0027 (accepted 2026-09-28). Owner pick 2026-09-28. L1–L8 apply to every ticket.

The wave is 10 tickets, four of them M-sized (M11-01, M11-05, M11-08, M11-09), not the four S-sized ideas listed in `docs/LAB_IDEAS.md`; the M11-10 review compares actual effort against that. Wave 1 closes before M11 dispatch (§13.5); the critical path M11-01 → M11-02 → M11-03 goes first.

## [ ] M11-01 — lab-addon sources and Lua lint
**Size:** M · **Depends on:** —

`lab/addon/WowLab/` per §13.1 and ADR-0026: a TOC template (no interface number; filled at install), Lua 5.1 sources writing the versioned tables at `PLAYER_LOGOUT` and on `/wowlab save`; no names, realms, GUIDs, guild or chat. A static Lua linter on `lab/addon/` only, pinned, no network at test time (the CI workflow change is harness work: the implementer reports the exact step and the conductor lands it). No Lua is executed anywhere in the Lab.

Before writing the talents section, the implementer reads the trait walker of https://github.com/Thunderz96/forever-addon-kit as a reference only: copy no code (check its license first if anything is adapted). Each API it confirms is marked "confirmed by forever-addon-kit on 69893, re-verify in M11-03". `talents.class` and `talents.legacy` are separate sections, each absent with a reason when its API is missing; the spec field is optional and found by testing for the API, never by calling `GetSpecialization` unguarded. `WowLabCharDB.probe` per §13.1.

**Acceptance:** the linter passes; a test asserts the template has no interface number and that the sources call none of `UnitName`, `UnitFullName`, `GetUnitName`, `GetRealmName`, `GetNormalizedRealmName`, `UnitGUID`, `GetGuildInfo`, `BNGetInfo`, `C_BattleNet`, `C_ChatInfo`, `GetPlayerInfoByGUID`, and that every unit-token argument is the literal `"player"`; a reviewer checks every API used against community documentation, marking each **[verify]** for the owner capture.

---

## [ ] M11-02 — `wowlab addon install/remove lab`
**Size:** S · **Depends on:** M11-01, M10-14

Copies `lab/addon/WowLab/` into `Interface/AddOns/WowLab/` through one `guard` transaction, filling `## Interface:` from discovery (L6); `remove` deletes it through `guard`. Plan, prompt, `--yes`, `--json`, exit 3 on refusal, as §6.11.

**Acceptance:** tests on a synthetic install: install then undo restores the tree byte for byte; the written TOC carries the discovered interface; a running client refuses with exit 3; nothing written outside `Interface/AddOns/WowLab/`.

---

## [ ] M11-03 — Capture the lab-addon's output
**Size:** S · **Depends on:** M11-02 · **owner**

Install with `wowlab addon install lab`; on each character log in, then log out or `/reload` (a crash writes nothing); log in twice on one character, so the `probe.loads` counter is proven to increment; on at least one character open the barber shop and close it without changing anything, then log out. Capture `WowLab.lua` (account and per-character) and, for sv-merge, a `## SavedVariablesPerCharacter` file from two characters that holds no other player's names (or `WowLab.lua` itself), with `scripts/lab_capture.py` (extended with `--sv` patterns if needed, as a scrub-tool follow-up with `security-reviewer`). Index rows; `docs/LAB_FORMATS.md` amendment for what each API section actually returned on Forever.

**Acceptance:** fixtures committed with rows; security and domain reviews clean; `probe.loads` shown to increment across the two logins; a table in `docs/LAB_FORMATS.md` listing every **[verify]** from §13.1 as confirmed, contradicted (with what Forever actually returned) or still open.

---

## [ ] M11-04 — `wowlab_core.labaddon` reader and `wowlab char show`
**Size:** S · **Depends on:** M11-03, M10-14

Pydantic models per section and schema version; unknown keys kept; absent sections reported with the addon's reason. `wowlab char show [--json]`. The reader and `char show` handle `talents.legacy` present, absent (with its reason) and empty (below level 25), and an optional spec.

**Acceptance:** every M11-03 fixture reads; `--json` validates; a constructed schema-2 document (labelled) is refused with a clear message.

---

## [ ] M11-05 — Customization tables and the looks model
**Size:** M · **Depends on:** —

Record the `ChrCustomization*`, `ChrRaces` and model tables for the Forever build from wago.tools as fixtures (ADR-0012; table set checked against the build listing). `wowlab_core.looks`: races, body types, options, choices, requirements; a look validated against it.

**Acceptance:** the model loads from the recorded tables; every race the build's tables flag as playable has options (table flags, not a claim about what the Forever server allows); a look is refused only for what the data decides (wrong race or body type, a class mask that excludes the class, a missing choice it depends on); an unlock requirement is shown as "needs <unlock>", and an imported choice id the build lacks as "unknown to build <version> (possibly a hotfix)", never refused.

---

## [ ] M11-06 — `wowlab looks` CLI
**Size:** S · **Depends on:** M11-05, M10-14 (and M11-04 for `import-char`)

`races`, `options`, `save`, `show`, `compare`, `import-char`; looks as JSON under the user data directory; `--json` on every data command.

**Acceptance:** Typer-runner tests per command; `--json` validates; `import-char` reads the M11-03 capture.

---

## [ ] M11-07 — `wowlab looks page`
**Size:** S · **Depends on:** M11-06

One self-contained HTML file per ADR-0027 (inline CSS/JS, embedded JSON, CSP forbidding external requests), written under the user data directory or `--out`, never into an install.

**Acceptance:** the embedded JSON parses back to the model's data; the CSP and the absence of any external URL are asserted; a refusal when `--out` is inside an install.

---

## [ ] M11-08 — profiles
**Size:** M · **Depends on:** M10-14

`wowlab profile save | apply | list | show | delete` per §13.3 on `snapshot` and `guard`; presets as data; the add-since-save behaviour decided and written into §13.3.

**Acceptance:** tests on a synthetic install: save then change then apply returns the chosen subtrees to the saved bytes and leaves others alone; Edit `no` files skipped; every `*-cache*` file written is listed with the server-may-replace note; undo reverses an apply; exit 3 on refusal.

---

## [ ] M11-09T — sv-merge graders [TEST]
**Size:** S · **Depends on:** M11-03

Graders for §13.4 on the M11-03 two-character captures and constructed documents (labelled): with a common snapshot base, one-sided changes taken and the same change taken once; without one (two characters), a two-way merge where every differing key is a conflict and one-sided keys are listed; conflicts listed and nothing written; an account-wide file refused for character-to-character with the reason; `--key` copies within one file; a missing key reported as "absent"; output in the target document's style, written through `guard`; the loader check: refuse with exit 3 when `probe.lost` is true, refuse when two snapshots of `WowLab.lua` show `loads` not going up, warn and continue with no capture, and `--force-loader-check` overriding the refusal. `xfail(strict=True)` one marker line each.

**Acceptance:** fail today for the missing behaviour only; satisfiable by a scratch patch; `make ci` green.

---

## [ ] M11-09 — sv-merge [IMPL]
**Size:** M · **Depends on:** M11-09T

`wowlab sv merge` per §13.4. Activate graders by marker deletion only.

**Acceptance:** all M11-09T graders green; reviewed by `security-reviewer` (it rewrites user data).

---

## [ ] M11-10 — Wave 2 review
**Size:** S · **Depends on:** all M11 tickets · **owner**

`docs/handoffs/M11-review.md` per §11; stop dispatch until the owner picks.
