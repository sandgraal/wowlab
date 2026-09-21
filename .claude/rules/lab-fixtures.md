---
paths:
  - "lab/core/tests/fixtures/**"
---

# Fixtures

The repository is public and these files come from a real install: an unscrubbed capture is a leak (ADR-0019, `SECURITY.md`).

- **Real captures only**, taken from a real install with `scripts/lab_capture.py`, which scrubs identity (account folder, character and realm names, identity CVars) with stable pseudonyms and refuses on a surviving email, BattleTag or unmapped player GUID. Nothing else in a fixture is edited: structure, order, number text, escapes and line endings stay byte for byte.
- Every file has a row in `lab/core/tests/fixtures/README.md` (kind, flavor, client version, platform, captured_by, consent, scrub, edge_cases). A fixture without a row fails the suite.
- Consent is `owner` unless the file came from someone else's install, which needs `explicit`.
- Raw captures live in `lab/core/tests/fixtures/incoming/` (gitignored) until reviewed. Never commit from there directly.
- Never edit a committed fixture. A new client version means a new file.
- Blizzard's exported interface code and art are not fixtures and are never committed.
- wago.tools recordings are small tables only, with the URL and capture date in the index row.
