#!/usr/bin/env bash
# Repository settings for sandgraal/wowlab. Idempotent; re-run safely.
# Run by the repository owner (it needs repo admin, and the agents' shell guard
# refuses mutating `gh api` calls). Re-run it whenever a required CI job is
# renamed: the ruleset requires checks by exact name, so a PR that renames one
# cannot merge until the ruleset in .github/rulesets/main.json is re-applied.
# The two release-tag rulesets and the `release` environment guard
# .github/workflows/release.yml (M13-03): only an admin creates a `v*` tag, no
# tag moves or disappears, and every publish waits for the owner's approval.
# Everything here is reversible in the GitHub UI.
set -euo pipefail

REPO="${REPO:-sandgraal/wowlab}"
OWNER="${REPO%%/*}"
RULESETS="$(dirname "$0")/../.github/rulesets"

echo "→ merge strategy: squash only, delete branch on merge, auto-merge allowed"
gh api -X PATCH "repos/$REPO" --silent \
  -F allow_squash_merge=true -F allow_merge_commit=false -F allow_rebase_merge=false \
  -F delete_branch_on_merge=true -F allow_auto_merge=true \
  -f squash_merge_commit_title=PR_TITLE -f squash_merge_commit_message=PR_BODY

echo "→ Actions: default GITHUB_TOKEN read-only; workflows cannot approve PRs"
gh api -X PUT "repos/$REPO/actions/permissions/workflow" --silent \
  -f default_workflow_permissions=read -F can_approve_pull_request_reviews=false

echo "→ secret scanning + push protection, dependabot security updates"
gh api -X PATCH "repos/$REPO" --silent --input - <<'JSON'
{"security_and_analysis":{"secret_scanning":{"status":"enabled"},"secret_scanning_push_protection":{"status":"enabled"}}}
JSON
gh api -X PUT "repos/$REPO/vulnerability-alerts" --silent
gh api -X PUT "repos/$REPO/automated-security-fixes" --silent

for name in main release-tags-create release-tags-immutable; do
  file="$RULESETS/$name.json"
  echo "→ ruleset '$name' from $file"
  existing=$(gh api "repos/$REPO/rulesets" --jq ".[] | select(.name==\"$name\") | .id")  # a failed list call aborts (set -e): never create a duplicate
  if [ -n "$existing" ]; then
    gh api -X PUT "repos/$REPO/rulesets/$existing" --silent --input "$file"
    echo "   updated ruleset $existing"
  else
    gh api -X POST "repos/$REPO/rulesets" --silent --input "$file"
    echo "   created ruleset"
  fi
done

echo "→ environment 'release': $OWNER approves every publish; v* tags only"
owner_id=$(gh api "users/$OWNER" --jq .id)
case "$owner_id" in '' | *[!0-9]*) echo "no numeric user id for $OWNER" >&2; exit 1 ;; esac
gh api -X PUT "repos/$REPO/environments/release" --silent --input - <<JSON
{"wait_timer":0,"prevent_self_review":false,"reviewers":[{"type":"User","id":$owner_id}],"deployment_branch_policy":{"protected_branches":false,"custom_branch_policies":true}}
JSON
policy=$(gh api "repos/$REPO/environments/release/deployment-branch-policies" \
  --jq '.branch_policies[] | select(.name=="v*" and .type=="tag") | .id')
if [ -z "$policy" ]; then
  gh api -X POST "repos/$REPO/environments/release/deployment-branch-policies" --silent \
    -f name='v*' -f type=tag
  echo "   added deployment policy: tag v*"
fi

echo "→ labels"
for spec in "ticket:0e8a16:agent-sized unit of work" "bug:d73a4a:something is wrong" "blocked:b60205:needs the owner" "harness:5319e7:agent harness or CI" "wave:fbca04:wave review or next-wave planning"; do
  IFS=: read -r name color desc <<<"$spec"
  gh label create "$name" --repo "$REPO" --color "$color" --description "$desc" --force >/dev/null
done

echo "→ read-back"
gh api "repos/$REPO/rulesets" --jq '.[] | "   ruleset \(.name) target=\(.target) enforcement=\(.enforcement)"'
gh api "repos/$REPO/environments/release" \
  --jq '"   environment \(.name): reviewers=\([.protection_rules[] | select(.type=="required_reviewers") | .reviewers[].reviewer.login] | join(","))"'
gh repo view "$REPO" --json squashMergeAllowed,mergeCommitAllowed,rebaseMergeAllowed,deleteBranchOnMerge \
  --jq '"   squash=\(.squashMergeAllowed) merge=\(.mergeCommitAllowed) rebase=\(.rebaseMergeAllowed) deleteOnMerge=\(.deleteBranchOnMerge)"'
echo "done. Remaining owner steps:"
echo "  - run 'claude setup-token', then add the CLAUDE_CODE_OAUTH_TOKEN secret (gh secret set CLAUDE_CODE_OAUTH_TOKEN; see docs/SETUP.md)."
echo "  - make published releases immutable (assets and tag can no longer change):"
echo "      gh api -X PUT repos/$REPO/immutable-releases"
