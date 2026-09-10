---
paths:
  - "api/src/bronze_api/services/simc_parser.py"
  - "api/src/bronze_api/services/profile_builder.py"
  - "api/src/bronze_api/services/talent_codec.py"
  - "api/src/bronze_api/services/gap_analysis.py"
---

# Load-bearing files

You are editing one of the four files everything else depends on. Extra rules apply.

- **Determinism.** `profile_builder` output is hashed for the sim cache (ADR-0004). No timestamps, no set/dict iteration without sorting, no locale-dependent formatting, `repr`-stable floats. The determinism test in `api/tests/parser/` must stay green and must not be weakened.
- **Lossless.** `simc_parser` preserves every unrecognised key verbatim in `parsed` and never drops a line it does not understand; `snapshots.simc_raw` always holds the original string. Unknown talent serialization versions raise; they do not guess.
- **No item math.** If you are about to compute stats from bonus IDs, upgrade tracks, or crafted quality, stop. SimC computes; we store and present (ADR-0002).
- **Evidence-only analysis.** `gap_analysis` attributions carry the numbers that produced them and leave the residual as `unattributed` (ADR-0010).
- **Gate.** `make test-parser` green before you push. A change here without a real fixture exercising it is incomplete.
- **Separation.** The tests that grade this file are written by `test-writer`, not by the session implementing it. Never edit a grader; if it is wrong, report it.
