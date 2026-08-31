# Critical path test matrix

## Covered scenarios

- ✅ Invalid/expired/revoked token behavior:
  - `apps/nodes/tests/test_enrollment.py::test_submit_enrollment_public_key_rejects_duplicate_submission_regression`
- ✅ Duplicate requests and idempotency for state-changing operations:
  - `apps/nodes/tests/test_enrollment.py::test_submit_enrollment_public_key_rejects_duplicate_submission_regression`
- ✅ Out-of-order OCPP events and retry collisions:
  - `apps/ocpp/tests/test_charger_status_polling.py::test_dedupe_event_rows_keeps_newest_status_for_out_of_order_retry_collisions`
- ✅ Partial failures in external API integration boundaries:
  - `tests/test_nodes_registration.py::test_register_visitor_proxy_reports_partial_failure_on_visitor_confirmation`
- ✅ Permission boundary checks between admin/operator roles:
  - `apps/sites/tests/test_public_routes.py::test_require_site_operator_or_staff_enforces_admin_operator_boundary`

## Known test gaps

- No end-to-end multi-node concurrency test yet for simultaneous visitor registrations from separate workers.
- No long-running replay test yet for high-volume out-of-order OCPP event streams across process restarts.
- No scenario yet covering mixed operator-group + object-level permission conflicts in the same request lifecycle.
