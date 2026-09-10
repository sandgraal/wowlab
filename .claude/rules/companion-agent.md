---
paths:
  - "agent/**"
---

# Companion agent (Go) and in-game addon

Security invariants from ADR-0009 are non-negotiable and reviewed by `security-reviewer`:

- Reads exactly one file path pattern: `<install>/_retail_/WTF/Account/<ACCOUNT>/SavedVariables/Bronze.lua`. No other filesystem access. Never writes to the game directory.
- The SavedVariables parser is a **constrained literal parser**: tables, strings, numbers, booleans, nil. It rejects function definitions, metatables, `load`, and any expression. No Lua interpreter, ever.
- Upload-only. The agent never receives instructions from the server beyond an HTTP status.
- Device token is obtained once through a pairing code shown in the web UI and stored with OS-appropriate permissions.
- Reproducible builds with published hashes for every release. Single static binary per platform.
- The addon writes on `PLAYER_LOGOUT` and on `/bronze`. It never does anything that could be read as automation of gameplay.
