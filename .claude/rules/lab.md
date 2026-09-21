---
paths:
  - "lab/**"
  - "scripts/lab_capture.py"
---

# Lab code

Spec: `docs/LAB_PLAN.md`. Formats: `docs/LAB_FORMATS.md`. Paths: `docs/LAB_FILE_MAP.md`. ADR-0019 to ADR-0025. This is the working summary of the hard invariants in `AGENTS.md`; that file wins.

- **Reads never write (L1).** Every module except `guard` opens the install read-only. No temp, cache or lock files inside an install. Caches and the snapshot store live under `platformdirs.user_data_path("wowlab")`.
- **One write gate (L2, ADR-0021).** Only `wowlab_core/guard.py` writes inside an install. If your ticket seems to need a write anywhere else, stop and report.
- **No Lua execution (L3).** Lua-syntax files are data. No interpreter, no `load`, no third-party evaluating parser.
- **Lossless (L4).** Keep order, raw number text, unknown lines, unknown directives, unknown columns. `serialize(parse(x)) == x` on every real fixture.
- **Build-keyed game data (L5).** Never overwrite a cached table for a build.
- **No flavor constants (L6).** `_retail_`, `_classic_beta_`, product codes, interface and build numbers come from discovery. Tests may name them; library code may not.
- **Scope (L7, ADR-0023).** No process memory, injection, packets, input automation, `Data/` or executable edits. `psutil` is for listing processes and nothing else. No `ctypes`/FFI aimed at the client.
- **Real fixtures (L8).** External-format parsers are graded on real captures with an index row. Constructed inputs only for hostile and boundary cases, labelled `constructed` in the test id. Tests never require a real install to be present and never write to one.
- **Local-only (ADR-0019).** Nothing read from an install leaves the machine. The only network client is `gamedata`. No telemetry, no upload, no packaging for distribution.
- **Layout.** `lab/core/` is `wowlab-core`; later apps are `lab/<app>/` and depend on it, never the reverse. Runtime dependencies stay on the `docs/LAB_PLAN.md` §6 list unless the PR body says why.
- **Waves (ADR-0024).** `docs/LAB_IDEAS.md` is a menu. Build tickets, not ideas.
- Items marked **[verify]** in the format reference are unconfirmed. A fixture that contradicts the reference wins: follow the fixture, add a dated amendment, and say so in your report.
