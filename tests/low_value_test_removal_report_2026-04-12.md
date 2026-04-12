# Low-value test removal report (2026-04-12)

Removed exactly **40** low-value tests based on the requested candidate set.

## Removed test IDs

1. `tests/test_manage_test_command.py::test_run_pytest_requires_pytest_module`
2. `tests/test_start_sh_systemd_fast_path.py::test_start_sh_skips_restart_when_main_service_is_active`
3. `tests/test_start_sh_systemd_fast_path.py::test_start_sh_restarts_when_main_service_is_inactive`
4. `tests/test_start_sh_systemd_fast_path.py::test_start_sh_reload_path_preserves_service_start_flow`
5. `tests/test_start_sh_systemd_fast_path.py::test_start_sh_restarts_failed_companion_when_main_is_active`
6. `tests/test_start_sh_systemd_fast_path.py::test_start_sh_starts_inactive_companion_when_main_is_active`
7. `tests/test_version_marker_shell_normalization.py::test_version_marker_shell_normalizes_legacy_suffixes`
8. `tests/test_version_marker_shell_normalization.py::test_version_marker_shell_ignores_missing_or_empty_version`
9. `tests/test_apps_registry_checks.py::test_apps_registry_check_reports_import_and_listing_errors`
10. `tests/test_apps_registry_checks.py::test_enforce_apps_registry_configuration_raises_for_misconfigured_apps`
11. `tests/test_apps_registry_checks.py::test_external_app_validation_errors`
12. `apps/core/tests/test_uptime_utils.py::test_availability_seconds_prefers_duration_locks`
13. `apps/core/tests/test_uptime_utils.py::test_availability_seconds_falls_back_to_boot_delay`
14. `apps/core/tests/test_review_notify.py::test_send_review_notification_uses_lcd_when_available`
15. `apps/core/tests/test_review_notify.py::test_send_review_notification_falls_back_cleanly_when_lcd_unavailable`
16. `apps/core/tests/test_review_notify.py::test_send_review_notification_skips_without_reviewable_changes`
17. `apps/core/tests/test_review_notify.py::test_send_review_notification_skips_when_git_status_unknown`
18. `apps/core/tests/test_review_notify.py::test_send_review_notification_uses_default_body_for_whitespace_summary`
19. `apps/core/tests/test_review_notify.py::test_send_review_notification_honors_zero_expiry_as_sticky`
20. `apps/core/tests/test_review_notify.py::test_review_notify_command_reports_fallback_transport`
21. `apps/core/tests/test_review_notify.py::test_review_notify_command_reports_skip`
22. `apps/core/tests/test_review_notify.py::test_review_notify_command_passes_force_flag`
23. `apps/core/tests/test_review_notify.py::test_review_notify_command_rejects_negative_expires_in`
24. `apps/core/tests/test_sqlite_wal.py::test_connect_sqlite_wal_executes_env_configured_pragmas`
25. `apps/core/tests/test_sqlite_wal.py::test_connect_sqlite_wal_runtime_pragma_failure_keeps_wal`
26. `apps/nginx/tests/test_renderers.py::test_generate_unified_config_includes_managed_sites`
27. `apps/nginx/tests/test_renderers.py::test_generate_unified_config_skips_primary_domain_from_managed_sites`
28. `apps/nginx/tests/test_renderers.py::test_generate_unified_config_does_not_exclude_allowed_hosts_domain`
29. `apps/nginx/tests/test_nginx_models.py::test_site_configuration_apply_records_state`
30. `apps/nginx/tests/test_nginx_models.py::test_site_configuration_validate_only`
31. `apps/nginx/tests/test_nginx_models.py::test_site_configuration_save_invalidates_dashboard_rule_cache`
32. `apps/screens/tests/test_lcd_bus_wrapper.py::test_bus_wrapper_raises_lcd_unavailable_when_i2c_device_missing`
33. `apps/screens/tests/test_lcd_bus_wrapper.py::test_bus_wrapper_raises_lcd_unavailable_when_i2c_bus_access_denied`
34. `apps/screens/tests/test_lcd_bus_wrapper.py::test_bus_wrapper_closes_bus_when_write_byte_raises`
35. `apps/docs/tests/test_library_cache.py::test_document_library_cache_reuses_path_scan_for_unique_prefixes`
36. `apps/docs/tests/test_library_cache.py::test_document_library_cache_scopes_paths_to_root_base`
37. `apps/sigils/tests/test_loader.py::test_load_fixture_sigil_roots_retries_on_locked`
38. `apps/sigils/tests/test_scanner.py::test_python_scanner_finds_expected_token_spans`
39. `apps/sigils/tests/test_scanner.py::test_get_scanner_returns_python_scanner`
40. `apps/screens/tests/test_lcd_runner.py::test_finalize_rotation_step_consumes_prefetched_channel_slot`

## File operations summary

- Deleted test modules where all selected tests lived.
- Kept `apps/screens/tests/test_lcd_runner.py` and removed only the requested single test.
