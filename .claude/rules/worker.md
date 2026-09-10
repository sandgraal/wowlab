---
paths:
  - "worker/**"
---

# Sim worker

- SimC is built from a **pinned tag** in the Dockerfile; that tag is `sim_jobs.simc_version` and part of the cache key. Bumping it is a deliberate change with an ADR amendment, never a side effect.
- Workers are stateless and idempotent: dequeue, regenerate the profile, assert its hash matches the job, run, write results, exit.
- Comparison sims (`vault`, `talent_compare`, `droptimizer`, `top_gear`) set `deterministic=1` so candidate runs share a seed and deltas are stable across re-runs. That is reproducibility, not precision: results still carry ±. Precision is governed by `target_error` (a quick tier around 0.1, a precise tier around 0.05) under an iteration ceiling per job kind. Never raise iterations to make a flaky comparison test pass.
- The vault candidate pool includes the catalysed variant of any tier-slot choice (bonus-id substitution from SimC's data, never stat arithmetic) so set-bonus boundaries are visible to the sim.
- Every result records the fight profile (style, length, target count) and the SimC version; the UI shows both.
- Golden-profile tests pin expected DPS within a tolerance at the pinned SimC version.
- SimC is GPL. It runs as a separate process; never link against it or vendor its source.
