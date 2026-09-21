---
paths:
  - "lab/core/src/wowlab_core/luadata.py"
  - "lab/core/src/wowlab_core/guard.py"
---

# Lab load-bearing files

Every Lab tool stands on these two. The `[TEST]` / `[IMPL]` separation in `AGENTS.md` applies exactly as it does to Bronze's four.

- **Separation.** Graders are written by `test-writer` and land on `main` first as `xfail(strict=True)`. Activate them by deleting the marker line only. Never edit a grader; if one is wrong, report it.
- **`luadata.py`: data, never code.** Tables, strings, numbers, booleans, nil. Reject functions, calls, metatables, operators and bare identifiers with a line and column. Bounded depth, size and string length; over a bound raises, never truncates, never lets a `RecursionError` escape.
- **`luadata.py`: fidelity.** Key order, key style, raw number text and trailing comments survive a parse. The serializer reproduces an unmodified real fixture byte for byte and is deterministic across platforms and locales. A fixture that cannot round-trip is a document-model finding, not a special case.
- **`guard.py`: no bypass.** Client closed or the write is refused; unknown counts as running. Allowlisted subtrees only. Snapshot first. Journal before and after hashes. Atomic replace. Roll back on any exception. No parameter, environment variable or private helper skips any of these.
- **`guard.py`: path handling.** Resolve before checking. Refuse traversal, absolute paths, symlink escapes, case-variant matches of forbidden paths, anything at the install root, `Data/`, executables, `.build.info`, `.flavor.info`.
- **Gate.** `make test-parser` green before you push a `luadata.py` change; the full Lab suite green before you push a `guard.py` change. `security-reviewer` reviews every `guard.py` diff.
