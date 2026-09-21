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

`gh secret set ANTHROPIC_API_KEY --repo sandgraal/wowlab` enables the
`@claude` responder and the automated PR review; `gh` prompts for the value,
so the key never goes through a chat or a shell history line. Without it
both workflows skip with a message. If the key is not scoped to a workspace,
also set the `ANTHROPIC_WORKSPACE_ID` repository variable
(`.github/workflows/claude-review.yml` explains why).

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
