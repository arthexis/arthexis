# Linting improvements roadmap

This note proposes incremental linting improvements that increase signal without
creating large one-off cleanup projects.

## 1. Expand Ruff to include import and modernization checks

Current pre-commit Ruff usage is focused on parser/runtime failures
(`E9`, `F823`). Add low-noise rule sets in phases:

1. `I` (import sorting)
2. `UP` (safe Python upgrades)
3. `B` (selected bugbear checks)

Start in CI report mode first (`--exit-zero`) so maintainers can prioritize real
issues before enforcing.

## 2. Run Ruff and Black with shared line length settings

The repository already uses Black. Define Ruff formatting-aligned settings in
`pyproject.toml` to avoid conflicting recommendations and reduce churn.

## 3. Add `make lint` and `make lint-fix` commands

A consistent entry point reduces contributor friction and simplifies local + CI
lint flows.

Suggested targets:

- `make lint`: fast fail checks (`E9`, `F823`)
- `make lint-strict`: full Ruff rule set from `pyproject.toml`
- `make lint-imports`: import resolution checks
- `make lint-fix`: Ruff autofix for enabled safe rules + Black

## 4. Gate risky anti-patterns with selective rules

Introduce targeted Ruff rules that catch production defects while keeping noise
low:

- `B904`: raise exceptions with explicit cause when re-raising
- `F401`: unused imports
- `F841`: assigned but unused local variables

## 5. Track lint debt as a baseline

If full enablement surfaces too many findings, commit a temporary baseline file
and burn it down module-by-module. This keeps net-new code clean while allowing
safe incremental refactors.

## 6. Add CI-visible lint summary output

Expose lint category counts in CI logs so maintainers can see whether debt is
increasing or decreasing each sprint.
