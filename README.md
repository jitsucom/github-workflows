# github-workflows

Shared GitHub Actions reusable workflows for jitsucom repos.

## Workflows

### `ai-review.yml` — AI Code Review

Reviews pull requests and commits for bugs, security issues, and correctness problems
using [OpenAI Codex](https://openai.com/codex).

- On **pull requests**: posts a native PR review with inline comments via a GitHub App
- On **push to main** (commits not part of any PR): posts a review as a commit comment
- Skips commits that already belong to an open PR (reviewed there instead)
- Reports reviewed SHAs, completion/publication status, token usage, and estimated cost

#### Secrets required

All three secrets must be available to the workflow — either as org secrets or repo secrets.

| Secret | Required for | Description |
|--------|-------------|-------------|
| `OPENAI_API_KEY` | always | OpenAI API key with Codex access |
| `AI_CODE_REVIEW_APP_ID` | PR mode | GitHub App ID for posting PR reviews |
| `AI_CODE_REVIEW_PRIVATE_KEY` | PR mode | Private key (.pem) for the GitHub App |

#### GitHub App setup

The GitHub App needs **Pull requests: Read & write** on the target repo.

1. Create app: `https://github.com/organizations/jitsucom/settings/apps/new`
2. Install the app on the target repo(s)
3. Generate a private key in the app settings
4. Store secrets (org-level example):

```sh
gh secret set AI_CODE_REVIEW_APP_ID --org jitsucom --repos my-repo --body "<app-id>"
gh secret set AI_CODE_REVIEW_PRIVATE_KEY --org jitsucom --repos my-repo < app-private-key.pem
gh secret set OPENAI_API_KEY --org jitsucom --repos my-repo --body "<key>"
```

#### Usage

Add a thin wrapper workflow to your repo:

```yaml
# .github/workflows/ai-review.yml
name: AI Review

on:
  pull_request:
    types: [opened, reopened, synchronize, edited, ready_for_review]
  push:
    branches: [main]
  workflow_dispatch:
    inputs:
      pr_number:
        description: PR number to review (leave blank to review a commit)
        required: false
      commit_sha:
        description: Commit SHA to review (leave blank when using PR number)
        required: false

jobs:
  ai-review:
    uses: jitsucom/github-workflows/.github/workflows/ai-review.yml@1.32.20260826
    secrets: inherit
    with:
      pr_number: ${{ inputs.pr_number }}
      commit_sha: ${{ inputs.commit_sha }}
```

Use the `review_instructions` input to add repository-specific focus. It supplements the
fixed scope and high-confidence evidence requirements; it does not replace them:

```yaml
    with:
      pr_number: ${{ inputs.pr_number }}
      commit_sha: ${{ inputs.commit_sha }}
      review_instructions: >-
        Focus on infrastructure safety, Terraform drift, and secret leaks.
        Skip style nitpicks.
```

#### Review context and decisions

PR reviews inspect the exact PR head, using its merge base with the base branch as the
start of the diff. Commit reviews inspect parent-to-commit changes. The workflow prepares
the PR title/body and paginated review threads/comments before analysis, so intentional
tradeoffs and previous dismissal explanations are available without rediscovery.

Include this optional section in the **caller repository's PR description or template**:

```markdown
## Intentional decisions and tradeoffs

- Decision, why it was made, and any accepted limitation.
- Known oddity the reviewer should understand.
```

The reviewer still checks whether the implementation achieves that intent safely.
It omits speculative findings and avoids repeating existing findings or dismissals,
unless new changes invalidate the dismissal. An empty review is valid.

To run the context regression checks locally (Python 3, Git, Bash, and jq required):

```sh
python3 -m unittest discover -s tests -v
```

#### Analysis and publication

Review runs use two separate jobs. `ai-review` has read-only GitHub permissions and
returns a schema-constrained `review.json`; it never receives the GitHub App key or a
posting token. Checkout credentials are not persisted. `publish-review` runs on a fresh
runner and validates the JSON before minting the App token or posting anything.

The publisher requires an explicit `complete` result for the exact reviewed SHA. Each
finding must have a P0/P1/P2 priority and a path/side/line range within the actual Git diff.
Deleted lines use `LEFT`; added lines use `RIGHT`. Invalid JSON, missing output, incomplete
analysis, invalid locations, and GitHub API failures fail the workflow. A completed empty
review is valid and is posted with `APPROVE`, matching the existing review convention.

Before posting, the publisher checks that the PR is still open, ready for review, and at
the reviewed head. Stale results are discarded and the publication job fails. Reviews
also specify `commit_id` so any change in the narrow interval between the check and the
POST cannot attach findings to a different commit; GitHub does not offer an atomic
head-check-and-post operation.

Per-subject concurrency covers both jobs so duplicate checks and posting are serialized.
Finding fingerprints prevent exact duplicate comments from this review bot, including
when lines move. Identical review results are idempotent on reruns. Existing legacy
comments and semantic duplicates are handled by the analysis prompt's thread history;
fingerprint checks do not attempt to infer whether differently worded findings describe
the same bug. Commit mode posts one consolidated comment, with rerun deduplication.

Artifacts are named with both run ID and run attempt. The publisher uses the artifact
name exported by analysis, including when only failed jobs are rerun:

- `ai-review-log-*`: `review.json` when available, diff files/statistics, and
  `analysis-status.json` with the analysis outcome, pinned model/effort/runtime, and usage.
- `ai-review-publication-*`: `status.json` recording validation/publication outcome and
  reviewed SHA, including failures. An unsuccessful API request leaves publication
  unconfirmed, since a failed POST may still have reached GitHub; reruns check markers
  before posting again. Publication is never inferred from an APPROVE badge.

Only data is read from the analysis artifact; publisher code comes from the workflow.
The model, reasoning effort, and CLI/action pins remain unchanged by this separation.

#### Incremental review and execution controls

The publisher records an `ai-review-coverage` marker only after a validated review is
posted. An APPROVE verdict without that marker does not count as coverage. A follow-up
PR review starts at the last compatible reviewed SHA when it is an ancestor of the
current head and the full PR merge base is unchanged. First reviews, rebases, force
pushes, base changes, and incompatible settings fall back to the full PR diff. Same-head
reruns also review the full diff. Commit reviews remain parent-to-commit.

Compatibility is a hash of the generated prompt, model, effort, CLI version, trusted bot
login, additional instructions, and PR title/body. Changes to these inputs require a
full review. Prior findings and dismissal explanations remain in the supplied context.
`range.json` in the context and artifact records both the selected range and full PR base.

Incremental deleted-line coordinates refer to the prior reviewed tree. Such findings,
and restored lines absent from GitHub's overall PR diff, are included in the review
summary with their path/range instead of being posted at an incorrect inline location.
These summary findings do not create resolvable inline threads. Oversized summaries
are split into bounded reviews; only the final accepted part records coverage. Retries
suppress findings from earlier accepted parts. A new head still receives
a completion marker when all findings were already reported, without duplicate comments.

These inputs are available through `workflow_call` and manual dispatch:

| Input | Default | Purpose |
|-------|---------|---------|
| `review_model` | `gpt-6-astra` | Explicit model identifier |
| `review_effort` | `high` | `low`, `medium`, `high`, `xhigh`, or `max`; model must support it |
| `analysis_timeout_minutes` | `12` | Per-engine timeout; integer from 1 to 30 |
| `codex_version` | `0.163.0` | Pinned stable CLI version; floating versions are rejected |
| `incremental` | `true` | Set false to force a full comparison |
| `review_bot_login` | `jitsu-code-review[bot]` | Trusted completion-marker author; must match the posting App |
| `dry_run` | `false` | Validate/archive without posting or advancing coverage |
| `benchmark_native` | `false` | Add a native-review comparison; automatically suppresses publication |

`benchmark_native` runs the structured prompt first, then `codex exec review` using a
clean worktree, the same selected range/model/effort/CLI, and a separate Codex home. The
native engine uses its built-in review guidance and repository AGENTS.md rather than the
custom prompt/PR discussion context. This compares the two execution setups; it does not
isolate prompt wording as the only variable. Both outputs are archived, including
`native-review.md` and `native-events.jsonl` (which includes native token usage events).
The structured result still passes the normal validator. Benchmark failure fails the run.

Example: compare a candidate CLI without adopting it as the production default:

```sh
gh workflow run ai-review.yml --repo jitsucom/github-workflows --ref <candidate-branch> \
  -f pr_number=<pr-number> -f codex_version=<candidate-version> \
  -f incremental=false -f dry_run=true -f benchmark_native=true
```

For reusable callers, pass the same inputs under `with:`. Primary token usage is collected
before the native benchmark. The workflow's `PRICES` dictionary contains standard text-token
rates for all 47 models in the official flagship, legacy text, cyber, life sciences, and
specialized text pricing tables, verified on 2026-10-16. This includes Astra, Sol 6.1,
Sol 6, Luna 6, the 5.6 family, and GPT-5.3-Codex. A price entry does not imply that a model
supports the Codex review command; model compatibility still needs to be checked separately.

Estimates use cumulative usage deltas per request, charge cached reads and cache writes
separately, and apply the published long-context premium above 272,000 input tokens per
request. Repeated token-count events do not add cost. Reasoning tokens are already included
in output tokens. Unknown model identifiers, inconsistent counters, or unpriced token
categories retain usage but show cost as unavailable with a reason. Exact identifiers are
required; arbitrary snapshot suffixes are not assigned alias prices.

Rates and their verification date come from [OpenAI pricing](https://developers.openai.com/api/docs/pricing).
Estimates exclude tool charges, regional/service-tier uplifts, and the optional native
benchmark. The source, date, basis, cache-write usage, and any unavailable reason are saved
in `analysis-status.json`. Update the inline dictionary when published pricing changes.
No runtime or model default changes are implied by a successful benchmark. Evaluate
confirmed findings, false positives, runtime and usage across representative PRs before
adopting a new default.

#### Versioning

Every merge to `main` is tagged `1.<commit-count>.<yyyymmdd>` by
[`release.yml`](.github/workflows/release.yml). Consuming repos pin a release tag and bump
it deliberately — there is no floating tag, so a github-workflows release never changes a
consumer's review behaviour until that repo opts in.

## Composite actions

Reusable composite actions live under `.github/actions/`. Consume them by path:

```yaml
- uses: jitsucom/github-workflows/.github/actions/<name>@<tag-or-main>
```

### `slack-notify` — Slack webhook notification

Sends a formatted notification to Slack with title + optional bullet blocks.
Used by the deploy workflows.

Inputs:

- `header` — required.
- `color` — optional, defaults to `good` (green). Accepts `good`, `warning`,
  `danger`, or a hex color like `#36a64f`.
- `blocks` — optional. YAML array of block objects with `title`, `value`,
  `url` (optional), `is_code` (optional). Omit to send the header alone.
- `slack_webhook_url` — optional override. Normally leave empty; the action
  reads `SLACK_WEBHOOK_URL` env first (see below) and only uses this input as
  a fallback, mainly for ad-hoc testing.

The composite action can't read org secrets directly. Standard pattern: set
`SLACK_WEBHOOK_URL` once at the job level from `secrets.CI_SLACK_WEBHOOK`.

```yaml
jobs:
  notify:
    env:
      SLACK_WEBHOOK_URL: ${{ secrets.CI_SLACK_WEBHOOK }}
    steps:
      - uses: jitsucom/github-workflows/.github/actions/slack-notify@main
        with:
          header: "Deploy started"
```

See [`action.yml`](.github/actions/slack-notify/action.yml).

#### Reusable-workflow wrapper

For callers that prefer `secrets: inherit` over wiring the env var, or for
testing the action directly from the GitHub UI (`workflow_dispatch`), there's
a thin wrapper at [`.github/workflows/slack-notify.yml`](.github/workflows/slack-notify.yml):

```yaml
jobs:
  notify:
    uses: jitsucom/github-workflows/.github/workflows/slack-notify.yml@main
    secrets: inherit
    with:
      header: "Deploy started"
```

Trade-off: each invocation runs as its own job on a fresh runner (~30–60s
startup) and can't share workspace state with sibling steps. For inline
notifications inside an existing deploy job, use the composite action directly.

### `install-yq` — Install the yq CLI

Installs `mikefarah/yq` to `/usr/local/bin` with a pinned version + sha256
checksum. Inputs: `version`, `sha256` (both have safe defaults). See
[`action.yml`](.github/actions/install-yq/action.yml).

### `install-kustomize` — Install the kustomize CLI

Installs the standalone kustomize CLI with a pinned version + sha256 checksum.
Inputs: `version`, `sha256` (both have safe defaults). See
[`action.yml`](.github/actions/install-kustomize/action.yml).

