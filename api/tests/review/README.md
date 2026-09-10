# Review probes

Tests written by the `code-reviewer` agent that reproduce a defect found during
review. They live here, in new files, so a reviewer never edits a file the
implementer owns. Each file's header names the branch it originated from.
Delete a probe only when the behaviour it guards is removed on purpose.
