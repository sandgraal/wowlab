---
paths:
  - "lab/core/src/wowlab_core/cli.py"
  - "lab/core/src/wowlab_core/ui/**"
  - "lab/core/src/wowlab_core/filemap.toml"
  - "lab/core/src/wowlab_core/firstrun.py"
  - "README.md"
  - "docs/INSTALL.md"
---

# Player-facing copy

Owner decision, 2026-10-21 (`docs/handoffs/outside-evaluation-2026-10.md`,
"Owner decisions", Q6). These rules apply to `--help` text, text output and
page copy, meaning anything a player reads. `--json` is an interface and is
exempt.

- The answer comes first. After it comes at most one caveat that applies to
  this result. Standing caveats go behind `--notes`, and the default output
  ends with one footer line ("N notes: --notes shows them").
- Never print a ticket id (`M13-07`), an ADR id, `L1`..`L8`, a doc path, a
  module or function path, a constant name or a model class name. A test
  enforces this (M16-15).
- `[verify]` is shown to a player as "(not yet confirmed)", at most once per
  report. The spec keeps the marker; the output does not.
- Say what is unknown in the player's terms ("wowlab cannot tell whether the
  server replaces this at login"), not in the project's.
- Use the game's words for the game's things: "Battle.net app", not "Agent".
  Use the glossary's terms otherwise.
- One fact lives in one place. When a sentence about safety, the network or
  what `undo` reverses already exists in the generated block (M16-07), use
  the block rather than writing a new copy.
