# Low-value test removal report (2026-03-14)

Removed exactly 39 tests, focused on low-signal permutation-heavy checks.
Verified against test corpus at commit `31b6b6e3396c6b9138691f3364a7ce4baf1dd61b` by summing per-module counts below with:
`python - <<'PY'\ncounts=[7,3,4,3,6,7,2,2,3,1,1]; print(sum(counts))\nPY`
Selection criteria: script-focused permutation tests and narrowly scoped regression checks with overlapping integration coverage. Reproducibility sample removed test IDs: `scripts/tests/test_check_import_resolution.py::test_relative_from_package_init_exports_is_treated_as_resolvable`, `apps/release/tests/test_git_status.py::test_git_clean_ignores_branch_ahead`, `tests/test_role_bootstrap_smoke.py::test_role_bootstrap_imports_succeed_with_minimum_required_environment`.

## Removed test modules

1. `apps/release/tests/test_git_status.py` (6 tests)
2. `apps/summary/tests/test_summary_command.py` (7 tests)
3. `scripts/tests/test_build_migration_baseline.py` (3 tests)
4. `scripts/tests/test_check_django_app_scaffold.py` (3 tests)
5. `scripts/tests/test_check_import_resolution.py` (7 tests)
6. `scripts/tests/test_check_migration_conflicts.py` (4 tests)
7. `scripts/tests/test_command_script.py` (2 tests)
8. `scripts/tests/test_nmcli_setup_script.py` (2 tests)
9. `tests/test_installed_apps_manifests.py` (3 tests)
10. `tests/test_pr_origin_marker_policy.py` (1 test)
11. `tests/test_role_bootstrap_smoke.py` (1 test)

Total removed tests: **39**.

## Why this set was removed

- Most tests asserted narrow branch/permutation behavior in scripts and command output formatting.
- Several tests overlapped with broader integration/domain confidence already present in the suite.
- The removed set had comparatively high maintenance churn and low customer-facing signal.
