#!/usr/bin/env bash
# Apply branch rulesets + repo settings across jitsucom repos.
#
# Dry by default. Pass --apply to write.
#
# Required: gh auth status as a user with Admin on each target repo.
# Idempotent: deletes existing ruleset named "default-branch-protection" before recreating.
#
# Usage:
#   scripts/apply-rulesets.sh                   # dry, all repos
#   scripts/apply-rulesets.sh --apply           # write, all repos
#   scripts/apply-rulesets.sh --apply jitsu     # write, single repo

set -euo pipefail

ORG=jitsucom
RULESET_NAME=default-branch-protection
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TEMPLATE="$SCRIPT_DIR/../rulesets/template.json"

# Format: repo:policy:pipe-separated-check-contexts
# Policy: flexible | pr-required | settings-only
MATRIX=(
  "jitsu:flexible:✨ Lint & Test|🧪 Bulker Test|ai-review / ai-review"
  "jitsu-cloud-infra:flexible:ai-review / ai-review"
  "github-workflows:flexible:ai-review"
  "jitsu-bi:flexible:"
  "mongobetween:pr-required:tests|lint|salus"
  "websites:settings-only:"
)

DRY=1
TARGET=""
for arg in "$@"; do
  case "$arg" in
    --apply) DRY=0 ;;
    --help|-h)
      sed -n '2,/^set -e/p' "$0" | sed 's/^# \{0,1\}//' | sed '$d'
      exit 0
      ;;
    *) TARGET="$arg" ;;
  esac
done

run() {
  if [ "$DRY" = "1" ]; then
    printf '    [dry] '; printf '%q ' "$@"; echo
  else
    "$@"
  fi
}

apply_repo_settings() {
  local repo=$1
  echo "→ $repo: repo settings (auto-merge on, rebase only, delete branch on merge)"
  run gh api -X PATCH "/repos/$ORG/$repo" \
    -F allow_auto_merge=true \
    -F allow_rebase_merge=true \
    -F allow_squash_merge=false \
    -F allow_merge_commit=false \
    -F delete_branch_on_merge=true
}

apply_ruleset() {
  local repo=$1 policy=$2 checks_csv=$3
  echo "→ $repo: ruleset ($policy)"

  local reviews=0
  [ "$policy" = "pr-required" ] && reviews=1

  local checks_json
  if [ -n "$checks_csv" ]; then
    checks_json=$(echo "$checks_csv" | tr '|' '\n' | jq -R '{context:.}' | jq -s .)
  else
    checks_json="[]"
  fi

  local existing
  existing=$(gh api "/repos/$ORG/$repo/rulesets" --jq ".[] | select(.name==\"$RULESET_NAME\") | .id" 2>/dev/null || true)
  if [ -n "$existing" ]; then
    echo "    existing ruleset id=$existing — replacing"
    run gh api -X DELETE "/repos/$ORG/$repo/rulesets/$existing"
  fi

  local body
  body=$(jq --argjson reviews "$reviews" --argjson checks "$checks_json" '
    .rules |= map(
      if .type == "pull_request" then
        .parameters.required_approving_review_count = $reviews
      elif .type == "required_status_checks" then
        .parameters.required_status_checks = $checks
      else
        .
      end
    )
  ' "$TEMPLATE")

  if [ "$DRY" = "1" ]; then
    echo "    [dry] POST /repos/$ORG/$repo/rulesets with body:"
    echo "$body" | sed 's/^/    /'
  else
    echo "$body" | gh api -X POST "/repos/$ORG/$repo/rulesets" --input - > /dev/null
    echo "    ruleset created"
  fi
}

for line in "${MATRIX[@]}"; do
  repo="${line%%:*}"
  rest="${line#*:}"
  policy="${rest%%:*}"
  checks="${rest#*:}"

  if [ -n "$TARGET" ] && [ "$TARGET" != "$repo" ]; then
    continue
  fi

  apply_repo_settings "$repo"
  if [ "$policy" != "settings-only" ]; then
    apply_ruleset "$repo" "$policy" "$checks"
  fi
  echo
done

if [ "$DRY" = "1" ]; then
  echo "Dry run complete. Re-run with --apply to write."
fi
