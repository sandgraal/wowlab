# M13-13 — The 30-day gate for v0.1

Owner decision at the end; the conductor only records the counts
(`docs/LAB_PLAN.md` §15.4, `docs/BACKLOG.md` M13-13).

## The release

- Tag `v0.1.0` on `fd5aab4e5becce2c5f7fbc7f28d15576a9260a4f` (#246, M13-11),
  pushed by the owner on 2026-10-18 at 05:09 UTC (2026-10-17 evening, owner's
  local time). Release run: https://github.com/sandgraal/wowlab/actions/runs/39099567299.
- Published at: _(fill in when the owner approves the `release` environment and
  the run completes; the release page is https://github.com/sandgraal/wowlab/releases/tag/v0.1.0)_.
- Gate day: **2026-11-17** (thirty days after the tag).

## What to record on the gate day

Run each from the repository and paste the output under it, unedited.

Release-asset downloads (the three assets, summed):

```bash
gh api repos/sandgraal/wowlab/releases/tags/v0.1.0 --jq '.assets[] | "\(.name) \(.download_count)"'
```

Distinct non-owner authors of issues and discussions, bots excluded
(`sandgraal` is the owner; a login ending in `[bot]` is a bot):

```bash
gh issue list --repo sandgraal/wowlab --state all --limit 500 --json author,createdAt --jq '[.[] | select(.createdAt >= "2026-10-18") | .author.login] | unique | map(select(. != "sandgraal" and (endswith("[bot]") | not)))'
```

```bash
gh api graphql -f query='query{repository(owner:"sandgraal",name:"wowlab"){discussions(first:100){nodes{author{login} createdAt}}}}' --jq '[.data.repository.discussions.nodes[] | select(.createdAt >= "2026-10-18") | .author.login] | unique | map(select(. != "sandgraal" and (endswith("[bot]") | not)))'
```

Stars:

```bash
gh api repos/sandgraal/wowlab --jq .stargazers_count
```

## The counts

_(filled in on 2026-11-17)_

| Measure | Count | Threshold (§15.4) |
|---|---|---|
| Release-asset downloads | | at least 50 |
| Non-owner issue authors | | together at least 5, issues weighing more |
| Non-owner discussion authors | | |
| Stars | | (recorded, no threshold) |

## The decision

_(the owner's: continue public work, or freeze the public surface and return
to personal-lab mode; dated)_
