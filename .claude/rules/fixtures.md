---
paths:
  - "api/tests/fixtures/**"
---

# Fixtures

- **Real captures only.** A `/simc` fixture is an addon export from a real character; an API fixture is a recorded live response. Constructed examples are rejected in review (ADR-0012).
- Add `/simc` exports with the `/fixture` skill; it validates, scans for secrets, and appends the provenance row that `make test-parser` requires.
- Consent is recorded per fixture (`owner` / `explicit` / `public-post`). Guildmates' exports need `explicit`. Names and realms may be rewritten before commit; nothing else may.
- API fixtures never contain access tokens, client secrets, or `Authorization` headers. Strip them at capture time; `gitleaks` is the backstop, not the process.
- Never edit a committed fixture. A new patch means a new file with the new `game_version`.
- Tests replay fixtures. Nothing under `api/tests/` calls a live API unless marked `@pytest.mark.live`, which CI excludes.
