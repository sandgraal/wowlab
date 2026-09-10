---
paths:
  - "worker/**"
---

# Sim worker

- SimC is built from a **pinned tag** in the Dockerfile; that tag is `sim_jobs.simc_version` and part of the cache key. Bumping it is a deliberate change with an ADR amendment, never a side effect.
- Workers are stateless and idempotent: dequeue, regenerate the profile, assert its hash matches the job, run, write results, exit.
- Comparison sims (`vault`, `talent_compare`, `droptimizer`, `top_gear`) set `deterministic=1`. Never raise iteration counts to make a flaky comparison test pass.
- Golden-profile tests pin expected DPS within a tolerance at the pinned SimC version.
- SimC is GPL. It runs as a separate process; never link against it or vendor its source.
