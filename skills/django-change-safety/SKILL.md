---
name: django-change-safety
description: Standardize safe Django changes with required checks for reversible migrations, fixture/migration pairing, explicit exceptions, docstrings, app admin registration, focused testing, and regression labeling for failures.
---

# Django Change Safety

Use this skill for Django backend/model/app changes where consistency and release safety matter.

## When to use

- Schema/model/data changes.
- Fixture updates.
- New app creation.
- Refactors that can affect runtime behavior.

## Required workflow

1. **Classify the change**
   - Identify touched apps, models, fixtures, and management/admin surfaces.

2. **Apply implementation guardrails**
   - Add docstrings to new public functions/classes/modules when meaningful.
   - Prefer specific exceptions (`ValueError`, `ValidationError`, custom exceptions) over broad catches.
   - Avoid broad `except Exception` unless re-raising with context is required.

3. **Migration and fixture policy**
   - If fixtures change, add a **reversible migration** in the same app/release unit.
   - Ensure migration reverse operations restore prior state (or deterministically undo).
   - Verify migration graph is valid.

4. **New app policy**
   - Prefer creating a new app over overloading unrelated core areas.
   - When creating a new app, create/register its Django admin module.

5. **Run focused checks**
   - Run targeted tests for touched areas first, then broader checks as needed.
   - If a test fails after your change, mark it as a **regression** in your report and fix before finalize.

6. **Report format**
   - Summarize changed files and rationale.
   - List commands run and pass/fail status.
   - Explicitly mention whether any regressions were encountered/fixed.

## Output checklist

- [ ] Guardrails followed (docstrings + specific exceptions)
- [ ] Fixture changes paired with reversible migration (if applicable)
- [ ] New app has admin registration (if applicable)
- [ ] Relevant tests executed and green
- [ ] Any failed tests labeled as regressions and fixed
