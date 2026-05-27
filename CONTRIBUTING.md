# Contributing

## Overview

This repo contains the shared AI review GitHub Actions workflow used across Jitsu repos.

## Development Branch

The default development branch is `main`.

## Common Principles

**Branch naming:** Use a type prefix — `feat/`, `fix/`, `chore/`.
Example: `feat/add-security-checks`.

**Merging policy:** When working on a feature branch, never merge the default branch into
it — always rebase your branch onto the latest default branch. When merging a PR into the
default branch, either "Create a merge commit" (the default) or "Rebase and merge" is fine.
Squash merge stays off; if you want to squash overly granular commits, do it locally before
opening the PR.

**Commit style:** [Conventional commits](https://www.conventionalcommits.org/) —
`type(scope): description`. Common types: `fix`, `feat`, `chore`, `refactor`, `ci`.

## PRs vs Direct Commits

Trivial changes, bug fixes, and config updates go directly to `main`. Larger or riskier
changes use pull requests. The engineer decides based on complexity and risk. AI review
runs on every push and PR.
