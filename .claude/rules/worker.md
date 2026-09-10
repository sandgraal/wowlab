---
paths:
  - "worker/**"
---

# Sim worker

- SimC is built from a **pinned commit SHA** on SimC's current expansion branch (`SIMC_REF` plus `SIMC_BRANCH` in the Dockerfile; upstream stopped tagging at `release-830-01`, ADR-0004 amendment 2026-09-10). The full SHA is `sim_jobs.simc_version` and part of the cache key. Re-pin when `CLIENT_DATA_WOW_VERSION` or `CLIENT_DATA_HOTFIX_DATE` in `engine/dbc/generated/client_data_version.inc` moves on that branch, or when SimC reports an unknown item id for a real snapshot; a re-pin is a deliberate change with an ADR amendment, never a side effect.
- `SC_NO_NETWORKING=ON` is required, not optional: with networking on, an item id missing from the compiled data is fetched at sim time and the cached result then depends on external state. With it off, a missing item fails loudly; "SimC unknown-item errors" is the queue to watch and the re-pin signal.
- The job loop refuses to start when the binary's compiled-in `git_revision` is not a prefix of `SIMC_REF`, so a stale image layer or a hand-built binary can never write results under the wrong cache key.
- Workers are stateless and idempotent: dequeue, regenerate the profile, assert its hash matches the job, run, write results, exit.
- Comparison sims (`vault`, `talent_compare`, `droptimizer`, `top_gear`) set `deterministic=1` so candidate runs share a seed and deltas are stable across re-runs. That is reproducibility, not precision: results still carry ±. Precision is governed by `target_error` (a quick tier around 0.1, a precise tier around 0.05) under an iteration ceiling per job kind. Never raise iterations to make a flaky comparison test pass.
- The vault candidate pool includes the catalysed variant of any tier-slot choice (bonus-id substitution from SimC's data, never stat arithmetic) so set-bonus boundaries are visible to the sim.
- Every result records the fight profile (style, length, target count) and the SimC version; the UI shows both.
- Golden-profile tests pin expected DPS within a tolerance at the pinned SimC version.
- SimC is GPL. It runs as a separate process; never link against it or vendor its source.
