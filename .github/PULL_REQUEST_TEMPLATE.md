## Ticket

<!-- e.g. M1-04 — Snapshot ingest endpoint. Title format: type(scope): summary (M1-04) -->

## Acceptance — with proof

<!-- One line per criterion from docs/BACKLOG.md: criterion → command → result -->

- [ ] `make ci` green (paste tail)
- [ ] Ticket acceptance command(s) run (paste output)
- [ ] Parser/profile/codec/gap change? `make test-parser` green and graders activated by marker deletion only
- [ ] Migration? up/down/up output attached; `snapshots` change is additive
- [ ] New external-format parsing? Real fixture with provenance row
- [ ] No live API calls in tests

## Judgment calls

<!-- Anything the spec did not literally decide, flagged as claims for the reviewer to test -->

## Reviews

<!-- code-reviewer: CLEAN | findings fixed in <sha>; domain-reviewer / security-reviewer if dispatched -->

---
session: <conductor session id>
🤖 Generated with [Claude Code](https://claude.com/claude-code)
