# Review probes

Tests written by the `code-reviewer` agent that reproduce a defect found during
review. They live here, in new files, so a reviewer never edits a file the
implementer owns. Each file's header names the branch it originated from.
Delete a probe only when the behaviour it guards is removed on purpose.

A wall-clock timing probe (one that compares a measured time with the
`docs/LAB_PLAN.md` §6.4 target, which is calibrated on the owner's M1) is
named `*_time_target.py`. `conftest.py` here skips files with that suffix on
Windows, where the shared CI runner is slower than the calibration machine.
Memory probes (`*_memory_target.py`) and all other probes run everywhere.
