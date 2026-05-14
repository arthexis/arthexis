---
name: release-readiness-publish
description: Refresh Arthexis release readiness from live GitHub and repo state, verify human approval, checks, conflicts, publish workflow, tag target, and release assets before preparing or performing a gated tag-triggered release. Use for readiness reports, publish planning, GitHub releases, PyPI, or version tags.
---

# Release Readiness Publish

## Quick Start

Use this skill when release state can drift. Refresh live GitHub and local repository evidence before answering whether a release is ready.

Read-only:

```powershell
python "$env:CODEX_HOME\skills\release-readiness-publish\scripts\release_readiness.py" --repo arthexis/arthexis --checkout [CONF.BASE_DIR]
python "$env:CODEX_HOME\skills\release-readiness-publish\scripts\publish_workflow_check.py" --checkout [CONF.BASE_DIR]
python "$env:CODEX_HOME\skills\release-readiness-publish\scripts\release_preflight.py" --repo arthexis/arthexis --checkout [CONF.BASE_DIR] --fetch
```

Create and push a release tag only with `--write --push`:

```powershell
python "$env:CODEX_HOME\skills\release-readiness-publish\scripts\release_tag.py" --checkout [CONF.BASE_DIR] --version v0.0.0 --write --push
```

## Rules

- Treat bot comments as automation evidence, not human approval.
- Confirm required checks, review state, conflicts, workflow presence, and tag absence close to the release action.
- Do not push a tag from a dirty checkout unless the operator explicitly accepts that with `--allow-dirty`.
- Prefer tag-triggered publish when the repo workflow is configured that way.
- Report exact version, commit, release URL, workflow path, and blocked gates.

## Scripts

- `scripts/release_readiness.py`: collect live GitHub and local readiness evidence.
- `scripts/publish_workflow_check.py`: inspect publish workflow triggers and PyPI/GitHub release hints.
- `scripts/release_preflight.py`: summarize the next valid release action from live GitHub, PyPI, and checkout state.
- `scripts/release_tag.py`: validate, create, and optionally push a version tag.
