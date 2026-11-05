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

## GitHub Actions secrets (owner)

Claude does not run in CI (owner decision, 2026-11-04): no workflow uses the
Claude Code action, a Claude token or an Anthropic API key, and
`tests/repo/test_no_claude_in_workflows.py` holds every workflow to it. The
`@claude` responder, the automated PR review and the daily wiki pass were
deleted that day. What they left behind on GitHub, for the owner to remove.
Deleting a secret only makes GitHub forget its copy; it does not revoke the
token it held, so each credential is also revoked at its source.

1. `gh secret delete CLAUDE_CODE_OAUTH_TOKEN --repo sandgraal/wowlab`. A
   `claude setup-token` token stays valid until it expires (a year after it
   was minted) and has no self-service revocation; if it ever leaked, ask
   Anthropic support (support.claude.com) to revoke it.
2. `ANTHROPIC_API_KEY`, if it is still set: revoke the key in the Claude
   Console (API keys), then `gh secret delete ANTHROPIC_API_KEY --repo
   sandgraal/wowlab`.
3. `WIKI_PUSH_TOKEN`, if it was ever created: revoke the fine-grained token
   at GitHub, Settings, Developer settings, Personal access tokens, then
   `gh secret delete WIKI_PUSH_TOKEN --repo sandgraal/wowlab`.
4. The `WIKI_DAILY_PUBLISH` variable:
   `gh variable delete WIKI_DAILY_PUBLISH -R sandgraal/wowlab`.
5. The `wiki-daily` label and any open report issue: close the issue
   (`gh issue list -R sandgraal/wowlab --label wiki-daily`, then
   `gh issue close <n> -R sandgraal/wowlab`) and
   `gh label delete wiki-daily -R sandgraal/wowlab --yes`.
6. The Claude GitHub App: uninstall it, or remove this repository from it,
   at GitHub, Settings, Applications, Installed GitHub Apps.

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
