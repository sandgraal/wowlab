# wowlab outside evaluation: shared brief

You are one seat on an outside evaluation panel. The owner of wowlab, a
local toolchain for World of Warcraft players, wants candid, evidence-backed
opinions of the whole tool from people who did not build it. Be specific
and blunt. Praise only what earns it. Your findings will be compiled into
proposed changes to the implementation plan.

Repository: /home/user/wowlab (branch `claude/sharp-gauss-gzr9eu`, same as main).
Eval kit: $EVAL
(below: `$EVAL`; write it out literally in commands, see "Shell gotchas").

## Order of reading (keeps your eyes fresh)

1. Start as a user: `README.md`, `docs/INSTALL.md`, the CLI's own `--help`.
2. Then the spec for your area: `docs/LAB_PLAN.md`, `docs/LAB_FILE_MAP.md`,
   `docs/LAB_FORMATS.md`, `docs/GLOSSARY.md`, `docs/DECISIONS.md` as needed.
   Do not read whole fixtures. Grep them or read slices.
3. Use the tool hands-on (sandbox below).
4. Only AFTER forming your findings: grep `docs/BACKLOG.md` and
   `docs/LAB_IDEAS.md` to tag each finding `already-ticketed: <id>`,
   `in-ideas: <entry>` or `new`.

## Hard limits on what you may recommend

These are the project's own permanent rules (AGENTS.md). You may argue
against one, but you must label the finding `conflicts: <L# or ADR-####>`.
Never present it as an easy fix.

- L1: reads never write into the install. L2: one write gate (`guard`), with a snapshot first.
- L3: no Lua execution, ever. L4: parsers are lossless. L5: game data is keyed by build.
- L6: no hard-coded flavor, build or interface numbers in the library.
- L7 / ADR-0023, permanently out of scope: process memory, injection, packet
  capture, input automation, editing `Data/` or executables, and bypassing integrity checks.
- Local only. Nothing is uploaded (ADR-0019/0031), apart from the planned opt-in `char advise --send` (ADR-0032).

## The sandbox (no real WoW install exists here)

Build your own synthetic install from the real, scrubbed fixtures:

```bash
eval "$(bash $EVAL/make_sandbox.sh <your-seat> [macos-70058|macos-70425|macos-70450])"
unset UV_NATIVE_TLS
W() { uv run -q --project /home/user/wowlab wowlab "$@"; }
W doctor --offline
```

- `make_sandbox.sh` sets `WOWLAB_WOW_ROOT`, `HOME` and the `XDG_*` variables
  to directories inside `$EVAL/sandboxes/<name>/`. Re-run the exports in
  every Bash call, because shell state does not persist. A second sandbox
  with an overlay (a later build of the same captures) lets you exercise
  `snap diff`, multiple builds, and so on. Use a new name for each sandbox.
- The flavor folder is `_classic_beta_` (the fixtures come from the Classic
  "Forever" beta on macOS, build 1.60.1.69913).
- Writes (`snap restore`, `profile apply`, `sv merge`, `addon install|remove lab`,
  `undo`) are fine against your sandbox, because they go through guard. The
  client check reports "not running" on this Linux box. Verified: snapshot,
  restore and undo all work.
- NO LIVE NETWORK to wago.tools (ADR-0012: community-run, unmetered).
  Always pass `--offline` where it exists. Do not run `db2 fetch`. If a
  command insists on the network, note that as a finding and move on.
  (`doctor` without `--offline` contacted wago.tools; that alone is worth a
  sentence from the relevant seats.)
- Never write into /home/user/wowlab. No edits, no new files, no commits,
  no `uv sync` changes to the lockfile. Probe files, scripts, screenshots
  and notes go under `$EVAL/work/<your-seat>/`. If you need to run the test
  suite, `make test` is fine (it only writes caches).
- Do not run `pkill` or `killall` (a hook blocks them). Stop a server you
  started with `kill <pid>`.

## Shell gotchas (a project hook inspects Bash)

- A redirect or `tee` to a path built from a variable is blocked
  (`> "$F"`). Use a literal path, or write with
  `python3 -c 'import sys; open(sys.argv[1],"w").write(...)' "$F"`, or use
  the Write tool.
- For the UI: Chromium is at `/opt/pw-browsers/chromium` (`chromium-1194`).
  Node 22 is available. Python Playwright is not installed; do not run
  `playwright install`. Options: use `npx -y playwright-core@1.47.2` in a
  script under `$EVAL/work/<seat>/` with
  `chromium.launch({executablePath: '/opt/pw-browsers/chromium-1194/chrome-linux/chrome'})`
  (find the exact binary with `ls`), or `chromium --headless --screenshot`,
  or `curl` of the API with the token. Start the UI with `W ui --help` first
  to learn the flags (no browser here; it prints the URL), and run it in
  the background.

## Output

Write your report to `$EVAL/reports/<your-seat>.md` with the Write tool,
using this structure:

```
# <Seat title>
## Verdict            (one paragraph: would you use or recommend it, for whom, the single biggest problem)
## What works         (short; only what is genuinely good)
## Findings
### F1 — <title>
- Severity: blocker | major | minor | idea
- Area: <cli command / ui tab / doc / module>
- Evidence: <exact command + trimmed output, or file:line, or screenshot path>
- Impact: <who is hurt and how>
- Proposal: <concrete change; for a plan-level change, name the LAB_PLAN section or wave>
- Status: new | already-ticketed: M13-xx | in-ideas: <entry> | conflicts: <rule>
## Top 3 changes      (your seat's priorities, ranked)
## Not tested         (what you could not exercise here, and why)
```

Aim for 8–20 findings. Quality matters more than count. Every blocker and
major finding must carry reproducible evidence, because a verifier will
re-run it. Your final message is the report's path and a three-line
summary.
