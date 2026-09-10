#!/usr/bin/env bash
# One-time repository settings for sandgraal/wowlab. Idempotent; re-run safely.
# Run by the repository owner or the conductor after the harness PR merges.
# Everything here is reversible in the GitHub UI.
set -euo pipefail

REPO="${REPO:-sandgraal/wowlab}"
RULESET_FILE="$(dirname "$0")/../.github/rulesets/main.json"

echo "→ merge strategy: squash only, delete branch on merge, auto-merge allowed"
gh repo edit "$REPO" \
  --enable-squash-merge --enable-merge-commit=false --enable-rebase-merge=false \
  --delete-branch-on-merge --enable-auto-merge \
  --squash-merge-commit-title PR_TITLE --squash-merge-commit-message PR_BODY >/dev/null

echo "→ secret scanning + push protection, dependabot security updates"
gh api -X PATCH "repos/$REPO" --silent --input - <<'JSON'
{"security_and_analysis":{"secret_scanning":{"status":"enabled"},"secret_scanning_push_protection":{"status":"enabled"}}}
JSON
gh api -X PUT "repos/$REPO/vulnerability-alerts" --silent
gh api -X PUT "repos/$REPO/automated-security-fixes" --silent

echo "→ ruleset 'main' from $RULESET_FILE"
existing=$(gh api "repos/$REPO/rulesets" --jq '.[] | select(.name=="main") | .id' || true)
if [ -n "$existing" ]; then
  gh api -X PUT "repos/$REPO/rulesets/$existing" --silent --input "$RULESET_FILE"
  echo "   updated ruleset $existing"
else
  gh api -X POST "repos/$REPO/rulesets" --silent --input "$RULESET_FILE"
  echo "   created ruleset"
fi

echo "→ labels"
for spec in "ticket:0e8a16:agent-sized unit of work" "bug:d73a4a:something is wrong" "blocked:b60205:needs the owner" "harness:5319e7:agent harness or CI" "patch-day:fbca04:WoW patch maintenance"; do
  IFS=: read -r name color desc <<<"$spec"
  gh label create "$name" --repo "$REPO" --color "$color" --description "$desc" --force >/dev/null
done

echo "→ read-back"
gh api "repos/$REPO/rulesets" --jq '.[] | "   ruleset \(.name) enforcement=\(.enforcement)"'
gh repo view "$REPO" --json squashMergeAllowed,mergeCommitAllowed,rebaseMergeAllowed,deleteBranchOnMerge \
  --jq '"   squash=\(.squashMergeAllowed) merge=\(.mergeCommitAllowed) rebase=\(.rebaseMergeAllowed) deleteOnMerge=\(.deleteBranchOnMerge)"'
echo "done. Remaining owner steps: add the ANTHROPIC_API_KEY secret (gh secret set ANTHROPIC_API_KEY)."
