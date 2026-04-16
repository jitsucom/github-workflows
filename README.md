# ai-review

Shared GitHub Actions workflow for AI-powered code review across jitsucom repos.
Uses [OpenAI Codex](https://openai.com/codex) to review pull requests and commits
for bugs, security issues, and correctness problems.

## What it does

- On **pull requests**: posts a native PR review with inline comments via a GitHub App
- On **push to main** (commits not part of any PR): posts a review as a commit comment
- Skips commits that already belong to an open PR (reviewed there instead)
- Reports token usage and estimated cost in the workflow summary

## Secrets required

All three secrets must be available to the workflow — either as org secrets or repo secrets.

| Secret | Required for | Description |
|--------|-------------|-------------|
| `OPENAI_API_KEY` | always | OpenAI API key with Codex access |
| `AI_CODE_REVIEW_APP_ID` | PR mode | GitHub App ID for posting PR reviews |
| `AI_CODE_REVIEW_PRIVATE_KEY` | PR mode | Private key (.pem) for the GitHub App |

### GitHub App setup

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

## Usage

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
    uses: jitsucom/ai-review/.github/workflows/ai-review.yml@main
    secrets: inherit
```

### Custom review instructions

Use the `review_instructions` input to focus the review on what matters for your repo:

```yaml
jobs:
  ai-review:
    uses: jitsucom/ai-review/.github/workflows/ai-review.yml@main
    secrets: inherit
    with:
      review_instructions: >-
        Focus on infrastructure safety, Terraform drift, and secret leaks.
        Skip style nitpicks.
```

## Updating

The reusable workflow lives in this repo. All consuming repos pick up changes on the next
run — no changes needed in each repo.
