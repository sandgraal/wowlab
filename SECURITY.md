# Security

## Reporting

Use GitHub's private vulnerability reporting on this repository
(Security → Report a vulnerability). Please do not open a public issue for a
security problem. You will get an acknowledgement within a few days.

## Scope and commitments

- **Companion agent (`agent/`).** Reads exactly one file path pattern in the
  WoW directory, never writes to the game directory, uploads only, parses
  SavedVariables with a literal-only parser (no Lua execution), ships as a
  reproducible single binary with published hashes. Any deviation from these
  is a vulnerability, not a feature request (ADR-0009).
- **Credentials.** No API client secrets, tokens, or user data in the
  repository, fixtures, logs, or CI output. gitleaks runs on every commit
  and on full history in CI; push protection is enabled.
- **Public character data.** Bronze exposes only what the Blizzard Armory
  already exposes about a character. Claiming a character is optional.
- **CI.** Workflow permissions are least-privilege; secrets are unavailable
  to fork and Dependabot runs.

There is no bug bounty. Credit is given in release notes unless you prefer otherwise.
