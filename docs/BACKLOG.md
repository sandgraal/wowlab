# Backlog

Agent-sized tickets. Each is independently reviewable and has a verifiable acceptance criterion. Dependencies are explicit; anything with no unmet dependency can be worked in parallel.

Estimates assume one agent per ticket. `S` = under half a day, `M` = about a day, `L` = multiple days.

**Status lives in the heading:** `## [ ] M10-04 — …` is open, `## [x] …` is done. A ticket also counts as done when a merged PR title carries its id in parentheses. Implementers never edit this file; the conductor ticks headings in a batched `docs(backlog)` PR. A grader ticket carries a `T` suffix (`M10-04T`, **[TEST]**, test-writer) and lands before its implementation twin (`M10-04`, **[IMPL]**, implementer) — see ADR-0013. Tickets marked **owner** need the repository owner for a real capture or a decision.

The backlog holds the current wave only (ADR-0024). Milestone numbers start at `M10`; earlier numbers belonged to the retired project (ADR-0025) and are not reused.

---

# M11 — lab-addon, customization-sandbox, profiles, sv-merge (Wave 2)

Spec: `docs/LAB_PLAN.md` §13. Decisions: ADR-0026, ADR-0027 (accepted 2026-09-28). Owner pick 2026-09-28. L1–L8 apply to every ticket.

The wave is 15 tickets (10 planned plus the follow-ups M11-11T, M11-11, M11-12, M11-13 and M11-14 added 2026-09-28), four of them M-sized (M11-01, M11-05, M11-08, M11-09), not the four S-sized ideas listed in `docs/LAB_IDEAS.md`; the M11-10 review compares actual effort against that. Wave 1 closes before M11 dispatch (§13.5); the critical path M11-01 → M11-02 → M11-03 goes first.

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

## [ ] M11-04 — `wowlab_core.labaddon` reader, `wowlab char show` and `looks import-char`
**Size:** S · **Depends on:** M11-03, M10-14, M11-06

Pydantic models per section and schema version; unknown keys kept, but a string value is accepted only in the fields the addon writes as strings (`link`, `export`, the enum-like fields such as `recorded_at`), so a hand-edited or tampered capture cannot pass free text through (#97 security review); exact numeric and boolean types; absent sections reported with the addon's reason. `wowlab char show [--json]`. The reader and `char show` handle `talents.legacy` present, absent (with its reason) and empty (below level 25), and an optional spec. The customization section is carried across sessions (§13.1): show it as "as of the last barber-shop visit with the addon enabled, N logins or reloads ago" from `recorded_load` and `probe.loads`, and say that a paid appearance change keeping the race is invisible to the addon. Also `wowlab looks import-char` (moved here from M11-06 on 2026-09-28 so the looks CLI need not wait for the capture): reads the capture's customization section into a look and checks it with the M11-05 model.

**Acceptance:** every M11-03 fixture reads; `--json` validates; a constructed schema-2 document (labelled) is refused with a clear message; `import-char` reads the M11-03 capture and a character without a recorded visit gets the addon's reason; a saved look records its origin (typed or imported) so `show`/`compare` keep the right unknown-id wording (#101 domain review).

---

## [x] M11-05 — Customization tables and the looks model
**Size:** M · **Depends on:** —

Record the `ChrCustomization*`, `ChrRaces` and model tables for the Forever build from wago.tools as fixtures (ADR-0012; table set checked against the build listing). `wowlab_core.looks`: races, body types, options, choices, requirements; a look validated against it.

**Acceptance:** the model loads from the recorded tables; every race the build's tables flag as playable has options (table flags, not a claim about what the Forever server allows); a look is refused only for what the data decides (wrong race or body type, a class mask that excludes the class, a missing choice it depends on); an unlock requirement is shown as "needs <unlock>", and an imported choice id the build lacks as "unknown to build <version> (possibly a hotfix)", never refused.

---

## [ ] M11-06 — `wowlab looks` CLI
**Size:** S · **Depends on:** M11-05, M10-14

`races`, `options`, `save`, `show`, `compare`; looks as JSON under the user data directory; `--json` on every data command. `import-char` moved to M11-04 (2026-09-28).

**Acceptance:** Typer-runner tests per command; `--json` validates.

---

## [ ] M11-07 — `wowlab looks page`
**Size:** S · **Depends on:** M11-06

One self-contained HTML file per ADR-0027 (inline CSS/JS, embedded JSON, CSP forbidding external requests), written under the user data directory or `--out`, never into an install.

**Acceptance:** the embedded JSON parses back to the model's data; the CSP and the absence of any external URL are asserted; a refusal when `--out` is inside an install.

---

## [x] M11-08 — profiles
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

## [ ] M11-11T — guard and store reads never block graders [TEST]
**Size:** S · **Depends on:** —

Follow-up from the M11-08 security review (#96). `guard._read` opens with `O_RDONLY | O_BINARY | O_NOFOLLOW` but no `O_NONBLOCK`, and checks `fstat` only after the open returns, so a file swapped for a FIFO during a read blocks forever while guard holds the store and install locks. Graders (constructed, labelled; POSIX-only, skipped where `os.mkfifo` is missing): a FIFO swapped in between the walk and the open (hooked the way the reviewer did) at every `guard._read` site is refused within a bounded time, and nothing is written; a FIFO already in place before the walk is refused today and is pinned as passing (amended 2026-09-28 after #99's review). Scope widened 2026-09-28 (#99 code and security reviews): the same blocking read in `snapshot.SnapshotStore._capture` (lstat then open without `O_NONBLOCK`, run inside guard's pre-write snapshot and by `snap create`/`profile save`), `SnapshotStore.read_object` (`read_bytes` with no type check, follows links, no size cap: a FIFO planted at a predictable object path blocks restore and undo with no race), and `guard._load_journal` (lstat then `read_bytes`, so a swapped symlink passes the size check). Also an empty-target case, so the `fstat` check is necessary. `xfail(strict=True)` one marker line each.

**Acceptance:** fail today for the missing behaviour only (with a test timeout, never a hang); satisfiable by a scratch patch; `make ci` green.

---

## [ ] M11-11 — guard and store reads never block [IMPL]
**Size:** S · **Depends on:** M11-11T

In `guard.py` and `snapshot.py` (widened 2026-09-28): open with `getattr(os, "O_NONBLOCK", 0)` (and `O_NOCTTY`) as guard's lock-file open already does, then `fstat` must show a regular file, in `guard._read`, `guard._load_journal`, `SnapshotStore._capture` and `SnapshotStore.read_object`; `read_object` and `_load_journal` also open with `O_NOFOLLOW` and read at most their size cap plus one byte; close the descriptor on every path (no `os.fdopen` on an unchecked descriptor, the leak fixed in `profiles._hash_fd` by #100). Sweep the other store reads with the same pattern (manifest `_load`, `_rehash`) and list what changed. Activate graders by marker deletion only, including review probe 74cfbde.

**Acceptance:** all M11-11T graders and 74cfbde green; full suite green (a `guard.py` change); reviewed by `security-reviewer`.

---

## [x] M11-12 — profiles hardening follow-ups
**Size:** S · **Depends on:** —

Follow-ups from the M11-08 reviews (#96), `profiles.py` only: (a) `_differs` walks parent folders with directory descriptors (`dir_fd`, `O_NOFOLLOW` per component where the platform allows) so a parent swapped for a link between the check and the open is never read through; (b) `profile save` notes when `Interface/AddOns` or `WTF` (or any saved subtree root) is itself a link: "<path> is a link; this profile holds the link, not what is behind it".

**Acceptance:** constructed, labelled tests for both on POSIX (a Windows junction case where it can be created); no read outside the install in (a); the note in human and `--json` output for (b); reviewed by `security-reviewer`.

---

## [ ] M11-13 — lab-addon static-check hardening
**Size:** S · **Depends on:** M11-01

Follow-ups from the #97 reviews (owner decision 2026-09-28: merge #97, defer these). In `tests/addon/test_lab_addon.py`: move the carry value check onto tokens so every chain starting `r.`, `r[`, `c.` or `c[` inside `carry` is allowed only as the argument of `type(...)`/`ipairs(...)`, one side of `== "<literal>"`, the `X` in `type(X) == "number" and X or nil`, or the whole right-hand side of an assignment directly inside `if type(X) == "number" then`; activate review probe f321c1a by marker deletion. In the addon: type-check each API return before storing it (the 12 sites the security review listed), so a whole API table can never be written. Deliberate obfuscation (aliasing a client table or `ns`, keys built with `string.format`/`string.lower`) stays review-only; say so in the test module docstring.

**Acceptance:** f321c1a green with its marker deleted; mutation proof for the new rule; selene and `make ci` green; reviewed by `security-reviewer`.

---

## [ ] M11-14 — small CLI follow-ups
**Size:** S · **Depends on:** M11-06

From the #101 reviews: `db2 fetch`/`db2 head` turn a malformed `--build` or table name into a usage error (exit 2), not an uncaught `ValueError`; `looks` `_choice_ref` gives `choice_name: null` when the choice is not in that option; `compare --json` states the tables-only remark once; the "no race" message prints the trimmed argument. From #100: a sentence in §13.3 that on POSIX a real file with `:` or `\` in its name always counts as changed in the profiles fallback path (harmless: guard rewrites the saved bytes).

**Acceptance:** a test per item; `make ci` green.

---

## [ ] M11-10 — Wave 2 review
**Size:** S · **Depends on:** all M11 tickets · **owner**

`docs/handoffs/M11-review.md` per §11; stop dispatch until the owner picks.
