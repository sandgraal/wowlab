# Machine setup

wowlab targets machines without a system package manager. Every tool below
installs into the user's home directory from a verified release artifact.
On Homebrew, apt or winget, install the same versions your own way.

## Required

| Tool | Version | Why |
|---|---|---|
| uv | 0.12.12 | Python toolchain; provisions Python 3.12 itself |
| Python 3.12 | via `uv python install 3.12` | the library, the CLI, tests |
| Python 3.9+ (system) | any | Claude Code hooks run on it |
| Go | 1.27.1 | only so pre-commit can build its gitleaks and actionlint hooks |
| gh | 2.x | PR flow, bootstrap script |

A World of Warcraft install is **not** required to develop or to run the
test suite: tests use committed fixtures and synthetic trees, and none
touches a real install. It is required to use the tools, and for the
owner's fixture capture (`docs/handoffs/M10-03.md`).

## Install without a package manager (macOS arm64 shown)

```bash
mkdir -p ~/.local/bin
# Put it on PATH for interactive shells too (Claude Code's shell inherits the
# app's PATH, but a terminal you open yourself does not):
grep -q 'local/bin' ~/.zshrc || echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc

# uv — verify against the published checksum before installing
V=0.12.12; cd "$(mktemp -d)"
curl -sSfLO "https://github.com/astral-sh/uv/releases/download/$V/uv-aarch64-apple-darwin.tar.gz"
curl -sSfLO "https://github.com/astral-sh/uv/releases/download/$V/uv-aarch64-apple-darwin.tar.gz.sha256"
shasum -a 256 -c uv-aarch64-apple-darwin.tar.gz.sha256
tar -xzf uv-aarch64-apple-darwin.tar.gz && install -m 0755 uv-aarch64-apple-darwin/uv uv-aarch64-apple-darwin/uvx ~/.local/bin/
uv python install 3.12

# Go — checksum is on https://go.dev/dl/
V=1.27.1; cd "$(mktemp -d)"
curl -sSfLO "https://go.dev/dl/go$V.darwin-arm64.tar.gz"
echo "<sha256 from go.dev/dl>  go$V.darwin-arm64.tar.gz" | shasum -a 256 -c
tar -C ~/.local -xzf "go$V.darwin-arm64.tar.gz"
ln -sf ~/.local/go/bin/go ~/.local/bin/go && ln -sf ~/.local/go/bin/gofmt ~/.local/bin/gofmt
```

Then:

```bash
make setup    # uv sync --frozen, git hooks via core.hooksPath, pre-commit hook envs, .env
make ci       # lint, tests, parser suite, hook tests on 3.12 and 3.9
uv run wowlab --version
```

`make setup` never runs `pre-commit install`; see "Worktree hygiene" in
`docs/AGENT_WORKFLOW.md` for why.

## Pointing the tools at your install

Discovery looks in the platform default locations
(`/Applications/World of Warcraft`, `C:\Program Files (x86)\World of Warcraft`,
`C:\Program Files\World of Warcraft` and the same under each fixed drive on
Windows; `docs/LAB_PLAN.md` §6.1). For anything else, including Linux
installs under Wine, Lutris or Proton, set the install root, the directory
that holds `.build.info`:

```bash
export WOWLAB_WOW_ROOT="/path/to/World of Warcraft"
```

`.env.example` documents the variable; an explicit `--root` argument wins
over it. There are no credentials anywhere in this project: wowlab reads a
local install and public game tables, and uploads nothing.

Where wowlab keeps its own data (the snapshot store, the game-table cache):
the platform user data directory, `platformdirs.user_data_path("wowlab")`.
Never inside the install and never inside the repository.

## GitHub Actions secret (owner)

The three workflows that run Claude (the `@claude` responder in
`claude.yml`, the automated PR review in `claude-review.yml`, and the daily
wiki pass in `wiki-daily.yml`) authenticate with the owner's Claude
subscription through the `CLAUDE_CODE_OAUTH_TOKEN` repository secret (owner
decision, 2026-10-14). Their usage counts against that subscription's
limits, shared with the owner's own Claude Code sessions; there is no API
bill.

1. On your own machine, run `claude setup-token`. It opens the browser
   sign-in, then prints a long-lived token (valid for a year) to the
   terminal and saves it nowhere.
2. Run `gh secret set CLAUDE_CODE_OAUTH_TOKEN --repo sandgraal/wowlab` and
   paste the token at the prompt, so it never goes through a chat or a
   shell history line.
3. Retire the old API key. Deleting the secret does not invalidate the key
   it held, so do both: revoke the key in the Claude Console (API keys),
   then `gh secret delete ANTHROPIC_API_KEY --repo sandgraal/wowlab`. The
   `ANTHROPIC_WORKSPACE_ID` repository variable is no longer read either;
   delete it too (`gh variable delete ANTHROPIC_WORKSPACE_ID --repo
   sandgraal/wowlab`).

The token lasts one year. The token set on 2026-10-15 expires about
2027-10-14; when it does, the Claude steps fail (below), and steps 1 and 2
with a fresh token fix it.

Minting a new token does not cancel the old one, and deleting the secret
does not either: GitHub only forgets its copy, and the token stays valid
until it expires. Anthropic documents no self-service way to revoke a
`claude setup-token` token. If the token leaks (it appears in a log, a
comment, an artifact, or anywhere outside the secret):

1. Delete the secret at once:
   `gh secret delete CLAUDE_CODE_OAUTH_TOKEN --repo sandgraal/wowlab`.
   The Claude steps then skip cleanly.
2. Contact Anthropic support (support.claude.com) and ask them to revoke
   the token; until they do, whoever holds it can spend your subscription's
   usage.
3. Only then mint a new token and set the secret again (steps 1 and 2).

Each Claude job strips credentials, this token among them, from every
command the model runs (`CLAUDE_CODE_SUBPROCESS_ENV_SCRUB`, with bubblewrap
installed for it), and denies the model reads of `/proc`, `~/.claude` and
the runner's temp directory. That narrows a leak; it does not make one
impossible.

Without the token, every Claude step skips with a message and the rest of
each workflow still runs. There is no API preflight any more: a subscription
token has no documented check that spends no usage, so the workflows check
only that the secret is present. When the token is expired or revoked, or a
usage limit is reached, the Claude step itself fails: the PR review's check
goes red and its tracking comment reads "Claude encountered an error", with
no verdict unless the review had already finished, and a wiki pass on `main`,
scheduled or run by hand, pushes nothing and comments on the open
`wiki-daily` issue, or opens one if none is open.

## Claude Code

`CLAUDE.md` imports `AGENTS.md`. Personal overrides go in
`.claude/settings.local.json`; machine notes in `CLAUDE.local.md`. Both are
gitignored. A `CLAUDE.local.md` template:

```markdown
# Local notes (not committed)
- No Homebrew; user binaries in ~/.local/bin (uv, go, gh).
- WoW install: <path>; flavors present: <list>. WOWLAB_WOW_ROOT is exported in ~/.zshrc.
- This volume has dropped writes before: verify pushes with `git ls-remote`.
```
