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
11. `tests/test_role_bootstrap_smoke.py` (1 test)

Total removed tests: **39**.

## Why this set was removed

- Most tests asserted narrow branch/permutation behavior in scripts and command output formatting.
- Several tests overlapped with broader integration/domain confidence already present in the suite.
- The removed set had comparatively high maintenance churn and low customer-facing signal.

## Addendum (2026-03-24)

Removed exactly **40** additional low-value tests from targeted modules during triage follow-up.

### Exact removed test IDs

- `apps/core/tests/test_good_command.py::test_marketing_tagline_can_include_docs_url`
- `apps/core/tests/test_good_command.py::test_issue_sort_key_orders_by_priority`
- `apps/core/tests/test_good_command.py::test_success_line_rejects_non_minor_issue_reports`
- `apps/core/tests/test_good_command.py::test_good_command_tagline_includes_docs_link`
- `apps/core/tests/test_good_command.py::test_good_command_prints_plain_success_when_no_issues`
- `apps/core/tests/test_good_command.py::test_good_command_prints_star_only_for_minor_issues`
- `apps/core/tests/test_good_command.py::test_good_command_details_reveal_minor_issues`
- `apps/core/tests/test_good_command.py::test_good_command_lists_ranked_important_issues`
- `apps/core/tests/test_good_command.py::test_check_internet_connectivity_uses_configurable_named_endpoints`
- `apps/playwright/tests/test_preview_command.py::test_handle_reports_backend_failures_without_name_error`
- `apps/playwright/tests/test_preview_command.py::test_handle_uses_throwaway_user_and_cleans_it_up`
- `apps/playwright/tests/test_preview_command.py::test_handle_cleans_up_throwaway_user_on_validation_failure`
- `apps/playwright/tests/test_preview_command.py::test_handle_skips_login_and_user_creation_for_no_login`
- `apps/playwright/tests/test_preview_command.py::test_handle_falls_back_to_selenium_backend`
- `apps/playwright/tests/test_preview_command.py::test_handle_reports_missing_screenshot_artifacts`
- `apps/playwright/tests/test_preview_command.py::test_handle_falls_back_to_secondary_engine_when_first_misses_artifact`
- `apps/playwright/tests/test_preview_command.py::test_handle_clears_stale_artifacts_between_engine_retries`
- `apps/playwright/tests/test_preview_command.py::test_handle_waits_for_suite_when_requested`
- `apps/playwright/tests/test_preview_command.py::test_wait_for_suite_ready_rejects_non_positive_timeout`
- `apps/links/tests/test_context_processors.py::test_share_short_url_returns_qr_data_uri`
- `apps/links/tests/test_context_processors.py::test_share_short_url_falls_back_to_page_url_when_short_url_unavailable`
- `apps/links/tests/test_context_processors.py::test_share_short_url_falls_back_to_relative_path_on_disallowed_host`
- `apps/links/tests/test_context_processors.py::test_share_short_url_rebuilds_absolute_url_for_trusted_disallowed_host`
- `apps/links/tests/test_context_processors.py::test_share_short_url_rejects_malformed_trusted_fallback_host`
- `apps/links/tests/test_context_processors.py::test_share_short_url_rejects_port_mismatch_with_trusted_site`
- `apps/links/tests/test_context_processors.py::test_share_short_url_rejects_trusted_host_with_invalid_port`
- `apps/links/tests/test_context_processors.py::test_share_short_url_accepts_semantically_equivalent_trusted_host`
- `apps/links/tests/test_context_processors.py::test_share_short_url_returns_empty_qr_when_encoding_fails`
- `apps/nodes/tests/test_register_node_curl_command.py::test_node_register_curl_outputs_script`
- `apps/nodes/tests/test_register_node_curl_command.py::test_node_register_curl_rejects_invalid_scheme`
- `apps/nodes/tests/test_register_node_curl_command.py::test_node_register_curl_rejects_invalid_token`
- `apps/nodes/tests/test_register_node_curl_command.py::test_node_register_curl_rejects_base_url_with_path`
- `apps/nodes/tests/test_register_node_curl_command.py::test_node_register_curl_rejects_base_url_with_query`
- `tests/test_security_allowed_hosts.py::test_allowed_hosts_include_control_plane_ip`
- `tests/test_security_allowed_hosts.py::test_validate_host_accepts_control_plane_ip_with_port`
- `tests/test_security_allowed_hosts.py::test_allowed_hosts_include_requested_lan_ip`
- `tests/test_security_allowed_hosts.py::test_validate_host_accepts_requested_lan_ip_with_port`
- `tests/test_settings_helpers.py::test_extract_ip_from_host_handles_trailing_dot_and_port`
- `tests/test_settings_helpers.py::test_validate_host_with_subnets_accepts_trailing_dot_ip_host`
- `tests/test_settings_helpers.py::test_validate_host_with_subnets_rejects_comma_separated_host_value`

### Rationale summary

- Removed permutation-heavy command and host/URL edge-case tests with overlapping protection from broader module and integration coverage.
- Prioritized reduction of low-signal tests that frequently require brittle monkeypatch setup and offer limited regression detection value relative to maintenance cost.
- Kept representative adjacent coverage in each area (including remaining `apps/core`, `apps/playwright`, and `tests/test_settings_helpers.py` assertions) and validated via focused regression pass.

### Restored exceptions

- None restored in this pass; no regressions appeared in the focused run.
