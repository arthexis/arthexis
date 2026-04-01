# Critical demotion candidates (validated in this change)

These 30 tests were previously selected by the `critical` marker through regression tagging and were moved to `integration` for slower, non-install/upgrade coverage.

1. `apps/actions/tests/test_admin.py::test_remote_action_token_admin_add_defaults_to_request_user`
2. `apps/actions/tests/test_admin.py::test_remote_action_token_admin_add_defaults_expiration_to_24h`
3. `apps/actions/tests/test_admin.py::test_remote_action_token_generate_tool_redirects_to_add_when_list_inaccessible`
4. `apps/actions/tests/test_admin.py::test_remote_action_openapi_download_requires_explicit_query_param`
5. `apps/actions/tests/test_admin.py::test_remote_action_openapi_forbidden_for_unprivileged_staff`
6. `apps/aws/tests/test_admin.py::test_credentials_tool_action_load_instances_redirects`
7. `apps/aws/tests/test_admin.py::test_credentials_tool_action_rejects_get`
8. `apps/aws/tests/test_admin.py::test_credentials_selected_action_loads_instances_for_each_selection`
9. `apps/aws/tests/test_admin.py::test_instance_tool_action_is_registered_and_redirects`
10. `apps/aws/tests/test_admin.py::test_instance_tool_action_rejects_get`
11. `apps/aws/tests/test_admin.py::test_credentials_load_records_discovery_for_loaded_items_only`
12. `apps/aws/tests/test_admin.py::test_credentials_load_handles_region_listing_failure`
13. `apps/cards/tests/test_scan_next_access.py::test_scan_next_anonymous_html_get_redirects_for_non_control_role`
14. `apps/cards/tests/test_scan_next_access.py::test_scan_next_anonymous_json_requests_unauthorized_for_non_control_role`
15. `apps/cards/tests/test_scan_next_access.py::test_scan_next_allows_anonymous_get_for_control_role`
16. `apps/cards/tests/test_scan_next_access.py::test_scan_next_blocks_anonymous_post_for_control_role`
17. `apps/core/tests/test_auto_upgrade_canaries.py::test_canary_gate_blocks_when_canary_offline`
18. `apps/core/tests/test_auto_upgrade_canaries.py::test_canary_gate_allows_when_canary_ready`
19. `apps/core/tests/test_auto_upgrade_canaries.py::test_canary_gate_blocks_when_canary_version_mismatch`
20. `apps/core/tests/test_odoo_product_admin.py::test_search_orders_for_selected_action_requires_odoo_link`
21. `apps/core/tests/test_odoo_product_admin.py::test_search_orders_view_accepts_post_selected_action`
22. `apps/core/tests/test_odoo_product_admin.py::test_load_employees_changelist_action_posts_to_import_endpoint`
23. `apps/core/tests/test_odoo_product_admin.py::test_load_employees_action_creates_missing_odoo_profiles`
24. `apps/core/tests/test_odoo_product_admin.py::test_load_employees_action_requires_verified_profile`
25. `apps/counters/tests/test_aws_credentials_dashboard_rules.py::test_watchtower_rule_requires_at_least_one_credential`
26. `apps/counters/tests/test_aws_credentials_dashboard_rules.py::test_watchtower_rule_succeeds_when_credentials_exist`
27. `apps/counters/tests/test_aws_credentials_dashboard_rules.py::test_non_watchtower_node_rule_succeeds`
28. `apps/evergo/tests/test_evergo_command.py::test_evergo_command_saves_credentials_and_tests_login`
29. `apps/evergo/tests/test_evergo_command.py::test_evergo_command_load_customers_with_inline_queries`
30. `apps/evergo/tests/test_evergo_command.py::test_evergo_command_load_customers_requires_query_source`

## Test suite maintenance note

Redundant UI, presentation, redirect, and default-formatting checks were trimmed in favor of representative coverage points so critical behavior paths stay covered without repetitive wiring assertions.
