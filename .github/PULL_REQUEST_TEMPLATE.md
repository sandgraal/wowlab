## Ticket

<!-- e.g. M10-08 — Game data client. Title format: type(scope): summary (M10-08) -->

## Acceptance — with proof

<!-- One line per criterion from docs/BACKLOG.md: criterion → command → result -->

- [ ] `make ci` green (paste tail)
- [ ] Ticket acceptance command(s) run (paste output)
- [ ] `luadata.py`, `guard.py` or another parser changed? `make test-parser` green and graders activated by marker deletion only
- [ ] New external-format parsing? Real, scrubbed fixture with a provenance row
- [ ] New runtime dependency? Named here with why the standard library does not do

## Invariants (AGENTS.md)

- [ ] **L1** reads never write: nothing but `guard` opens an install for writing; no temp, cache or lock file inside one
- [ ] **L2** one write gate: no write site outside `guard.py` (store: `snapshot.py`, cache: `gamedata.py`); nothing skips the client check, allowlist or snapshot
- [ ] **L3** no Lua execution: literal parser only; functions, calls, metatables, operators rejected with a position
- [ ] **L4** lossless: unknown lines, keys, directives and columns kept; `serialize(parse(x)) == x` on every real fixture
- [ ] **L5** game data keyed by build and never overwritten
- [ ] **L6** no flavor, product, interface or build constants in library code
- [ ] **L7** nothing ADR-0023 excludes: no process memory, injection, packets, input automation, `Data/` or executable edits
- [ ] **L8** real fixtures with index rows; constructed inputs labelled and limited to hostile and boundary cases; no live calls; no test needs a real install

## Judgment calls

<!-- Anything the spec did not literally decide, flagged as claims for the reviewer to test -->

## Reviews

<!-- code-reviewer: CLEAN | findings fixed in <sha>; domain-reviewer / security-reviewer if dispatched -->

---
session: <conductor session id>
🤖 Generated with [Claude Code](https://claude.com/claude-code)
