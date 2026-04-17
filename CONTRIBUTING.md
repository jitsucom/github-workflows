# Contributing

## Overview

This repo contains the shared AI review GitHub Actions workflow used across Jitsu repos.

## Development Branch

The default development branch is `main`.

## Common Principles

**Branch naming:** Use a type prefix — `feat/`, `fix/`, `chore/`.
Example: `feat/add-security-checks`.

**Merging policy:** We avoid merge commits. Always rebase onto the default branch —
never merge the default branch into a branch. For PRs, merge with full history preserved
— no squash merge. It's fine to squash overly granular commits within a branch locally
before opening a PR.

**Commit style:** [Conventional commits](https://www.conventionalcommits.org/) —
`type(scope): description`. Common types: `fix`, `feat`, `chore`, `refactor`, `ci`.

## PRs vs Direct Commits

Trivial changes, bug fixes, and config updates go directly to `main`. Larger or riskier
changes use pull requests. The engineer decides based on complexity and risk. AI review
runs on every push and PR.
