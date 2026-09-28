# Review probes

Tests written by the `code-reviewer` agent that reproduce a defect found during
review. They live here, in new files, so a reviewer never edits a file the
implementer owns. Each file's header names the branch it originated from.
Delete a probe only when the behaviour it guards is removed on purpose.

A timing probe (one that compares a measured time with a target such as the
`docs/LAB_PLAN.md` §6.4 one, which is calibrated on the owner's M1) is
named `*_time_target.py`. `conftest.py` here skips files with that suffix on
Windows, where the shared CI runner is slower than the calibration machine.
A probe that times a parse against a fixed target should measure the parse's
CPU time where the platform has a process clock, not wall clock, so a busy
machine does not fail it; the two M10-04 `*_at_budget_misses_time_target.py`
probes show how and say why in their docstrings (M11-16T).
Memory probes (`*_memory_target.py`) and all other probes run everywhere.
