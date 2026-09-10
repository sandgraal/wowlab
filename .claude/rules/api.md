---
paths:
  - "api/**/*.py"
---

# API conventions

- Python 3.12, FastAPI, SQLAlchemy 2.0 style (`Mapped[]`, `select()`), Pydantic v2. Full type annotations; `mypy --strict` is part of `make lint`.
- Ingest endpoints are idempotent by content hash: re-posting identical state returns the existing id with HTTP 200 (plan §9).
- There is no `UPDATE` on `snapshots`. New state is a new row. "Refresh" creates a snapshot.
- Every query against `game_*` tables carries a `game_version`.
- List endpoints are cursor-paginated; snapshots accumulate forever.
- Timestamps are timezone-aware (`DTZ` lint rules are on). `captured_at` is when the game state was true; `ingested_at` is when we received it.
- Structured logging via the project logger; parser failures capture the failing input to durable storage (M1-09).
- External clients live in `bronze_api/clients/` and read credentials from `Settings`, never from `os.environ` directly.
