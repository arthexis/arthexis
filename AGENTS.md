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
* The application suite should remain **flexible and powerful**, especially for administrators.
* Remind users and developers that issues should be solved by **extending the arthexis suite** to integrate with the relevant framework or service, rather than by building disconnected side systems.
* Treat Arthexis as an **OCPP-compatible WebSocket server** that can act as a pivot for broader integrations by modeling external APIs and processes through Django apps and migrations.
* When using or extending Arthexis for a user request, agents must also contribute to the project's health with appropriate adjacent improvements, such as tidying tests, removing obsolete cruft, cleaning docs, polishing minor glitches, and addressing security issues when they are found.

Agents must **not overprotect admins** or artificially restrict capabilities unless there is a clear security concern.

---

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

All functions and classes should include **docstrings** describing:

* purpose
* parameters
* return values
* raised exceptions (when relevant)

Code within test modules (e.g., test functions and classes) is exempt from this requirement unless a docstring is needed to clarify non-obvious behavior.

---

# Testing Policy

Agents must run relevant tests after code changes.

### Test Execution

* Execute tests and **fix errors introduced by changes**.
* Avoid creating tests for **micro-behaviors** unless:

  * they are security-relevant, or
  * they protect critical logic.

---

### Test Creation Guidelines

* Each **feature should have a test**.
* Prefer **quality over quantity** of tests.
* Do **not create tests solely to validate styling**.
* Apply required marks to tag the PR of origin.

Example:

```python
@pytest.mark.pr_origin(6172)
def test_my_feature():
    ...
```

Styling will be validated through previews.

---

### Regression Handling

If a test fails **multiple times across runs**, it must be:

* marked as a **regression**
* documented accordingly.

---

### Fixture Changes

Any modification to database models must include:

* **reversible migrations**

Agents must ensure migrations are reversible before committing.

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

* Check the **entire PR discussion**.
* Ensure **all comments and requested fixes** are addressed, not just the highlighted one.

---

# Preview Requirements

A **preview is mandatory** whenever changes affect:

* UI components
* site rendering
* Django admin
* templates
* front-end behavior

---

## Preferred Preview Method

Primary method:

```bash
python manage.py runserver 127.0.0.1:8000
```

In another shell:

```bash
python manage.py preview \
  --base-url http://127.0.0.1:8000 \
  --path / \
  --path /admin/ \
  --output-dir preview_output
```

This allows multiple pages to be captured in one run.

---

## Environment Preparation

Before running previews:

```
./env-refresh.sh --deps-only
```

This ensures:

* Playwright dependencies
* browser binaries
* preview tooling

are correctly installed.

---

## Fallback Preview Method

If Playwright fails to launch due to missing system libraries or restricted CI environments:

* Use **direct browser automation tools**
* Capture screenshots of the running app manually

This approach is particularly useful in:

* containerized environments
* minimal CI runners
* restricted Linux hosts

---

## Preview Reporting

Preview results must include screenshots embedded in the report using markdown:

```markdown
![Homepage](preview_output/homepage.png)
![Admin](preview_output/admin.png)
```

---

# Repository Documentation

Agents must **not modify primary README files** unless:

1. explicitly requested, and
2. validated by the end user.

---

# Special Terminology

## Cleave

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

## Triage

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
