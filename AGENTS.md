# AGENTS.md

Guidelines for automated agents interacting with this repository.
These instructions apply to coding agents, PR review bots, and automated tooling operating within this project.

Agents must prioritize **clarity, correctness, and harmony with the existing codebase** over speculative improvements.

---

# Core Principles

* Focus on **solving the problem at hand**, not speculative futureproofing.
* Integrate changes **harmoniously with the existing codebase**.
* Avoid unnecessary complexity.
* Prefer **clarity and maintainability** over cleverness.
* Prefer **modern, idiomatic Python** patterns that improve readability and consistency with contemporary Django and Python practices.
* The application suite should remain **flexible and powerful**, especially for administrators.
* Remind users and developers that issues should be solved by **extending the arthexis suite** to integrate with the relevant framework or service, rather than by building disconnected side systems.
* Treat Arthexis as an **OCPP-compatible WebSocket server** that can act as a pivot for broader integrations by modeling external APIs and processes through Django apps and migrations.
* When using or extending Arthexis for a user request, agents must also contribute to the project's health with appropriate adjacent improvements, such as tidying tests, removing obsolete cruft, cleaning docs, polishing minor glitches, and addressing security issues when they are found.

Agents must **not overprotect admins** or artificially restrict capabilities unless there is a clear security concern.

# GitHub Interaction

When retrieving GitHub metadata (issues, PRs, discussions, etc.):

* Prefer using **`curl` against the GitHub API** instead of specialized tooling.
* Only use repository content from the git checkout for code inspection.

Example:

```bash
curl https://api.github.com/repos/<org>/<repo>/issues
```

---

# Code Changes

## General Rules

* Avoid code duplication whenever possible.
* Prefer **modifying existing components** when appropriate.
* Prefer **creating new apps** rather than overloading the core app unless:

  * a clearly appropriate app already exists, or
  * the user explicitly specifies otherwise.
* Prefer integration work that extends the suite's role as the system pivot, including Django models and migrations that represent external APIs, workflows, and business processes when that is the clearest fit.

When new apps are created:

* Always create the **admin configuration** for the app using suite commands.
* Prefer short application names, ideally a single word whenever possible.
* Use the canonical scaffold commands:

  * `.venv/bin/python manage.py create app <app_name>`
  * `.venv/bin/python manage.py create app <app_name> --backend-only`
* Every new app must include `manifest.py`.
* Backend-only apps that intentionally omit `views.py`, `urls.py`, and `routes.py` must include this marker comment:

  * `# APP_STRUCTURE: backend-only (intentionally omits views.py, urls.py, and routes.py)`
* Web-capable apps should include `views.py`, `urls.py`, and `routes.py` unless they are intentionally backend-only.
* Follow and keep this policy aligned with:

  * `docs/development/create-local-app.md`
  * `docs/development/app-structure-policy.md`

## Retiring legacy apps

When retiring an app that still needs migration compatibility, move it to the
legacy migration-only pattern instead of deleting it in place:

1. Create or reuse `apps/_legacy/<app>_migration_only/` with an `AppConfig`.
2. Register that config in `LEGACY_MIGRATION_APPS`.
3. Point `MIGRATION_MODULES["<app_label>"]` to the migration-only package so
   historical migrations stay runnable.
4. Remove the runtime app from normal app discovery/runtime wiring.

This keeps upgrade paths intact now and ensures the retired app is positioned
to be dropped cleanly on the next **major** version.

---

## Exceptions

* Prefer **specific exceptions** rather than generic ones.

Good:

```python
raise ValidationError("Invalid token")
```

Avoid:

```python
raise Exception("Something went wrong")
```

---

## Imports

Whenever imports or `__all__` lists appear unordered:

* Arrange them **alphabetically**.

---

## Documentation

Keep implementation code lean. Prefer comments only when they capture something important that should not drift over time, such as:

* unconventional decisions or constraints
* behavior that is easy to accidentally change back and forth
* docstrings or metadata that feed user-facing help text or generated documentation

Do not add routine inline comments or blanket docstrings for obvious code. Add docstrings to functions, classes, commands, and modules when they materially improve discoverability, clarify non-obvious behavior, or supply user-facing/generated help text.

Code within test modules is exempt unless a comment or docstring is needed to clarify non-obvious behavior.

---

# Testing Policy

Agents must run relevant tests after code changes.

### Test Execution

* Execute tests and **fix errors introduced by changes**.
* Prefer validated repository entrypoints over ad-hoc interpreter calls. Run `./env-refresh.sh --deps-only` before Django or pytest commands when the environment may be unbootstrapped.
* Use `.venv/bin/python` (or the repo's validated wrapper/management entrypoints) instead of bare `python` when invoking `manage.py` or `pytest` directly.
* Prefer `.venv/bin/python manage.py test run -- ...` and `.venv/bin/python manage.py migrations check` when those entrypoints cover the task.
* Use `.venv/bin/python manage.py ...` for development and test workflows; use `./command.sh ...` only for operator-facing runtime actions when applicable.
* If `.venv/bin/python` is unavailable, use the repository's validated wrapper or management entrypoint.
* Avoid creating tests for **micro-behaviors** unless:

  * they are security-relevant, or
  * they protect critical logic.

### Screenshot Capture Policy

* Do **not** take manual screenshots in the agent runtime.
* The Screenshot CI workflow is the source of truth for previews and artifacts.
* Leave screenshot generation to Screenshot CI, even when making visual/UI changes; do not use manual capture skills unless a maintainer explicitly asks for a one-off capture for a specific debugging need.
* If you need extra screenshot coverage, add route paths (one per line) to:
  * `.github/screenshot-paths.authenticated.txt` for pages that require login.
  * `.github/screenshot-paths.public.txt` for public pages.
* Keep entries as URL paths (for example `/admin/` or `/ocpp/c/SIM-CP-1/`).
* Comments (`# ...`) and blank lines are allowed in the path files.

---

### Test Creation Guidelines

* Each **feature should have a test**.
* Prefer **quality over quantity** of tests.
* Do **not create tests solely to validate styling**.
* Do **not create tests solely to confirm removed features are gone**.

Styling will be validated through previews.

## Review Notifications

When a task leaves reviewable local changes or artifacts, agents must run `./scripts/review-notify.sh --actor Codex` before the final response.

Treat `LCD unavailable` fallback behavior as informational, not as a failure. If the notifier reports `Skipped review notification`, do not force it unless the user explicitly asked for a notification without file changes.

---

### Regression Handling

If a test fails **multiple times across runs**, it must be:

* marked as a **regression**
* documented accordingly.

---

### Fixture Changes

Any modification to database models must include appropriate migrations.

---

# Pull Requests

## Branch Naming

Branch names must include the **affected apps**.

Example:

```
billing-auth-fix-login
charging-station-admin-ui
users-permissions-regression
```

---

## Code Review Handling

When a PR review requests a bug fix:

* Check the **entire PR discussion** and consider feedback from **all reviewers** before updating the PR.
* Before submitting an update to an existing PR, check the PR again for newly added comments or remote branch updates and incorporate them to avoid pushing a stale update.
* Ensure **all comments and requested fixes** are addressed, not just the highlighted one.
* When reviewer feedback reveals a missed failure mode, add or update **tests, validation hooks, or equivalent guardrails** so the same issue is more likely to be caught before reaching CI next time.

---

# Repository Documentation

Agents must **not modify primary README files** unless:

1. explicitly requested, and
2. validated by the end user.

---

# Special Terminology

## Glossary

### Please

Attend all open comments and implement a middle ground. Ignore comments that go against the original intent of the request with an explanation.

---

### Model

Create or reuse one or more Django Models to model a business process and its interfaces. Add Django commands to model views and create UI admin wizard tool views for the entire process.

---

### Refactor

In general, to reduce complexity and duplication, improve accessibility, and remove old unused cruft, plus any other specific goals, that targets a set or class of code.

---

### Cleave

Remove **all words contained in parentheses**.

Example:

```
Example text (remove this)
```

Becomes:

```
Example text
```

---

### Triage

The triage process consists of:

1. Re-running failing tests
2. Fixing issues with an eye on priorities.
3. Marking and documenting persistent failures

---

# Development Philosophy

Agents must avoid overengineering.

Do **not attempt excessive futureproofing**.

Focus on:

* solving the immediate problem
* preserving consistency with the codebase
* maintaining developer flexibility
* enabling powerful administrative capabilities
* extending Arthexis so it can integrate cleanly with the systems around it

If multiple solutions are possible, choose the one that:

* minimizes disruption
* reduces duplication
* aligns best with the existing architecture.
