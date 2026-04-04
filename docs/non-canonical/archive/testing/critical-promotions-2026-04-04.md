# Critical promotions (2026-04-04)

> **Non-canonical reference:** This document is retained for internal or historical context and is not part of the canonical Arthexis documentation set.

These tests were promoted to the `critical` marker to keep historically fragile auth, webhook, upgrade, and OCPP/netmesh regression paths in the fast gate.

1. `apps/netmesh/tests/test_api.py::test_netmesh_token_lifecycle_errors_are_stable`
2. `apps/ocpp/tests/test_charger_status_polling.py::test_dedupe_event_rows_keeps_newest_status_for_out_of_order_retry_collisions`
3. `tests/test_nodes_registration.py::test_register_visitor_proxy_reports_partial_failure_on_visitor_confirmation`
4. `apps/sites/tests/test_public_routes.py::test_require_site_operator_or_staff_enforces_admin_operator_boundary`
5. `apps/sites/tests/test_passkey_login.py::test_passkey_login_verify_rejects_missing_challenge`
6. `apps/sites/tests/test_passkey_login.py::test_passkey_login_verify_rejects_invalid_json_structure`
7. `apps/repos/tests/test_webhooks.py::test_github_webhook_form_payload_array_is_preserved`
8. `apps/repos/tests/test_webhooks.py::test_github_webhook_header_lookup_is_case_insensitive`
9. `apps/meta/tests/test_webhooks.py::test_whatsapp_webhook_rejects_invalid_signature`
10. `apps/meta/tests/test_webhooks.py::test_whatsapp_webhook_accepts_valid_signature`
11. `apps/core/tests/test_auto_upgrade_periodic_task.py::test_ensure_auto_upgrade_periodic_task_disables_task_when_feature_is_off`
12. `apps/core/tests/test_auto_upgrade_periodic_task.py::test_sync_auto_upgrade_periodic_task_for_feature_change_enables_task`
13. `apps/repos/tests/test_github_issue_reporting.py::test_request_exceptions_do_not_enqueue_github_reporting_when_feature_disabled`
14. `apps/repos/tests/test_github_issue_reporting.py::test_duplicate_exception_cooldown_still_blocks_repeated_reporting`
15. `apps/users/tests/test_rfid_auth_audit_suite.py::test_rfid_login_records_rejected_reason_for_blocked_tag`
