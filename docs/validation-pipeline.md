# Validation pipeline contract

Arthexis separates merge safety from deployment support and release safety. The names below are part of the CI/release contract and should be used consistently in workflow names, job names, release-readiness reports, documentation, and rulesets.

## Pull requests

**Clean Install** is the PR installation gate. It proves that the candidate can be installed from a clean checkout with its required runtime dependencies and migrations in a lightweight GitHub-hosted environment. A PR is not required to prove upgrade compatibility from a previously published Arthexis release.

The `main` ruleset requires exactly four pre-merge checks: **Clean Install**, `python / Package`, `python / Python compatibility`, and `python / Quality`. These are the merge-safety contract. Support Matrix, Live Integration, and Upgrade Health are deliberately not required PR checks; they validate supported deployments and release safety after the fast PR path has completed.

## Main branch

**Support Matrix** validates the current `main` revision on the supported software environments. Its intended matrix is:

- Debian 13 / Python 3.13 / SQLite: full `ocpp` shard.
- Debian 13 / Python 3.13 / SQLite: full `extra` shard (everything outside `apps/ocpp/tests`).
- Ubuntu 22.04 / Python 3.13 / SQLite: installation and smoke validation.
- Ubuntu 22.04 / Python 3.13 / PostgreSQL: installation and smoke validation.

The Debian container approximates the Raspberry Pi OS Debian userland; it does not establish ARM compatibility by itself.

**Live Integration** validates the same current `main` revision on the real integration target. The target is expected to become a disposable Raspberry Pi reached through WireGuard, so this check can exercise ARM64, Raspberry Pi OS, GWAY lifecycle operations, systemd, and actual Arthexis services without granting broad root privileges to the GitHub runner host.

Support Matrix and Live Integration are main-branch health signals. Release readiness must display the state of both for the exact candidate SHA.

## Release

**Upgrade Health** proves the production upgrade path. It starts from the latest published/versioned Arthexis release, creates representative released state, upgrades to the release candidate, and validates the resulting application and migrations. It belongs to release readiness rather than the PR critical path because production instances upgrade from published releases, not arbitrary `main` commits.

A release candidate should therefore present four distinct kinds of evidence:

1. Current code/security readiness.
2. Support Matrix state for the exact candidate SHA.
3. Live Integration state for the exact candidate SHA.
4. Upgrade Health from the latest published release to the exact candidate SHA.

The release report is the operator-facing place where these states are gathered. Missing, running, or failed required evidence is a release blocker; successful evidence should be shown with a link to its workflow run.

## Security boundary for Live Integration

The self-hosted GitHub runner is an orchestrator, not the privileged deployment target. The disposable Raspberry Pi integration target should be isolated with a dedicated WireGuard peer and SSH identity, contain no valuable production data or reusable secrets, and have no route to unrelated internal networks. Root-level integration operations may occur on that disposable target. Compromise of the target should be recoverable by reflashing it without compromising the runner host or other infrastructure.
