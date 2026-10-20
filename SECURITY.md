# Security

wowlab is a local-only tool in a public repository. Other players may
install it from a tagged GitHub release and run it on their own machine
(ADR-0031). There is still no service, no account system and no upload, so
the things that can go wrong are narrow: the tool damaging the install it
works on, the tool doing more than it says, and personal data reaching this
public repository, inside a fixture or inside something a player pastes.

## Reporting

Use GitHub's private vulnerability reporting on this repository
(Security → Report a vulnerability). Please do not open a public issue for a
security or privacy problem. You will get an acknowledgement within a few
days.

### If you are a player

Report a problem with your own install privately as above, or as an issue
if it is not a security or privacy problem. Paste only redacted output
(`wowlab doctor --redact`, once it ships), never your character, realm or
account folder names, your BattleTag or your home path. If you pasted one
by mistake, edit it out and tell us; a public issue keeps its edit history.

### A leaked identifier in a fixture

Fixtures under `lab/core/tests/fixtures/` are real captures that were
scrubbed by `scripts/lab_capture.py` (the capture tool, ticket M10-02).
Character and realm names are not identifiers there: they are visible to
anyone in the game, and since 2026-10-17 the capture tool keeps them as they
are (ADR-0019 amendment, M13-49). What identifies an account is an account
folder name that is not a pseudonym, an email address, a BattleTag, a real
name, a home path that carries a login, or a `Player-<n>-<hex>` GUID that is
not a pseudonym. If you find one of these in a fixture, do this:

1. Report it privately as above. **Do not quote the identifier in a public
   issue, PR or commit message**; that publishes it a second time. Give the
   file path and the line or byte offset.
2. The owner removes the file, rewrites history for it if the identifier is
   sensitive (a deleted file is still in the git history of a public repo),
   and adds the pattern to the scrub tool's refusal list with a test, so the
   same shape cannot pass again.
3. The fix is not complete until the tool refuses the original input.

## Scope and commitments

- **The install.** Every write into a game install goes through one gate
  (`wowlab_core.guard`, ADR-0021): client closed, allowlisted subtree,
  snapshot first, atomic replace, rollback on error. A write that bypasses
  it, a path that escapes the allowlist (traversal, symlink, case variant),
  or a loss of data the gate should have been able to undo is a
  vulnerability.
- **No Lua execution.** SavedVariables and other Lua-syntax files are parsed
  as data by a literal-only parser with bounds. Input that gets evaluated,
  or that crashes or exhausts the parser instead of raising, is a
  vulnerability.
- **The client.** No process memory access, injection, packet handling or
  input automation (ADR-0023). `psutil` lists processes and nothing else.
  Code that crosses that line is a vulnerability, whatever its intent.
- **Nothing leaves the machine.** The only network traffic is downloading
  public game tables, and a link you click opens in your own browser,
  carrying your character's race, body type, class, level and equipped item
  and bonus ids to that site. Anything that transmits data read from an
  install is a vulnerability, with one exception: that link. The Character
  tab's dressing-room address (ADR-0019 and ADR-0031, amendments of
  2026-10-19) holds those public game ids and nothing else (no name, realm,
  account, path or token); wowlab never requests it, and only the player's
  click sends it, from the player's own browser.
- **Secrets.** The project needs no credentials. gitleaks runs on every
  commit and on full history in CI; push protection is enabled.
- **CI.** Workflow permissions are least-privilege; secrets are unavailable
  to fork and Dependabot runs.

There is no bug bounty. Credit is given in the fix's commit unless you
prefer otherwise.
