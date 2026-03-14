# Low-value test removal report (2026-03-14)

Removed exactly 50 tests as requested, focused on low-signal permutation-heavy checks.

## Removed test modules

1. `scripts/tests/test_check_import_resolution.py` (7 tests)
2. `scripts/tests/test_check_django_app_scaffold.py` (3 tests)
3. `scripts/tests/test_check_migration_conflicts.py` (4 tests)
4. `scripts/tests/test_build_migration_baseline.py` (3 tests)
5. `tests/test_runserver_preflight_policy.py` (6 tests)
6. `tests/test_site_and_node_context_processor.py` (4 tests)
7. `tests/test_installed_apps_manifests.py` (3 tests)
8. `apps/release/tests/test_git_status.py` (6 tests)
9. `apps/summary/tests/test_summary_command.py` (7 tests)
10. `tests/test_pr_origin_marker_policy.py` (1 test)
11. `tests/test_role_bootstrap_smoke.py` (1 test)
12. `scripts/tests/test_command_script.py` (2 tests)
13. `scripts/tests/test_nmcli_setup_script.py` (2 tests)
14. `apps/actions/tests/test_admin.py` (1 test)

Total removed tests: **50**.

## Why this set was removed

- Most tests asserted narrow branch/permutation behavior in scripts and command output formatting.
- Several tests overlapped with broader integration/domain confidence already present in the suite.
- The removed set had comparatively high maintenance churn and low customer-facing signal.
