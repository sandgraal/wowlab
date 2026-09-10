# Machine setup

Bronze targets machines without a system package manager. Every tool below
installs into the user's home directory from a verified release artifact.
Contributors on Homebrew or apt can install the same versions their own way.

## Required

| Tool | Version | Why |
|---|---|---|
| uv | 0.12.12 | Python toolchain; provisions Python 3.12 itself |
| Python 3.12 | via `uv python install 3.12` | API, pipeline, tests |
| Python 3.9+ (system) | any | Claude Code hooks run on it |
| Docker + Compose plugin | 27+ | local Postgres, Redis, SimC worker (M0-03) |
| Node 24 + pnpm | `.nvmrc` | `web/` (M1-08) |
| Go | 1.27.1 | `agent/` (M4), pre-commit's gitleaks/actionlint builds |
| gh | 2.x | PR flow, bootstrap script |

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

Docker Compose plugin (when `docker compose` says "unknown command"): download
`docker-compose-darwin-aarch64` from https://github.com/docker/compose/releases,
verify its `.sha256`, and install to `~/.docker/cli-plugins/docker-compose`
(`chmod +x`). On colima, `colima start` first.

Then:

```bash
make setup    # uv sync --frozen, pre-commit install, .env
make ci
```

## Credentials (never committed)

- **Blizzard:** create a client at https://develop.battle.net/access/clients →
  `BNET_CLIENT_ID` / `BNET_CLIENT_SECRET` in `.env`. Needed for M0-01 and M0-05.
- **Warcraft Logs:** https://www.warcraftlogs.com/api/clients → `WCL_CLIENT_ID` /
  `WCL_CLIENT_SECRET`. Needed from M5.
- **GitHub Actions:** `gh secret set ANTHROPIC_API_KEY --repo sandgraal/wowlab`
  enables the `@claude` responder and the automated PR review; `gh` prompts
  for the value, so the key never goes through a chat or a shell history
  line. Without it both workflows skip with a message.

## Claude Code

`CLAUDE.md` imports `AGENTS.md`. Personal overrides go in
`.claude/settings.local.json`; machine notes in `CLAUDE.local.md`. Both are
gitignored. A `CLAUDE.local.md` template:

```markdown
# Local notes (not committed)
- Container runtime is colima: `colima start` before `make up`.
- No Homebrew; user binaries in ~/.local/bin (uv, go, docker, gh).
- This volume has dropped writes before: verify pushes with `git ls-remote`.
```
