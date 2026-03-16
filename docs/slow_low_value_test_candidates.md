# Slow low-value test removal candidates (30)

Selection criteria used:
- Already marked `@pytest.mark.slow`.
- Heavy websocket/live-server setup or repeated setup/teardown behavior.
- Overlapping assertions where nearby tests already cover the same contract.
- Focused on implementation detail variants rather than product-level behavior.

## Candidates

1. `apps.ocpp.tests.test_websocket_creation::test_charger_page_reverse_resolves_expected_path`
2. `apps.ocpp.tests.test_websocket_creation::test_select_subprotocol_prioritizes_preference_and_defaults`
3. `apps.ocpp.tests.test_websocket_creation::test_connect_prefers_stored_ocpp2_without_offered_subprotocol`
4. `apps.ocpp.tests.test_websocket_creation::test_connect_maps_ocpp16j_subprotocol_to_ocpp16_version`
5. `apps.ocpp.tests.test_websocket_creation::test_local_ip_bypasses_rate_limit_with_custom_scope_client`
6. `apps.ocpp.tests.test_websocket_creation::test_pending_connection_replaced_on_reconnect`
7. `apps.ocpp.tests.test_websocket_creation::test_assign_connector_rebinds_store_preserves_state`
8. `apps.ocpp.tests.test_websocket_creation::test_rejects_invalid_serial_from_path_logs_reason`
9. `apps.ocpp.tests.test_websocket_creation::test_rejects_invalid_query_serial_and_logs_details`
10. `apps.ocpp.tests.test_websocket_creation::test_basic_auth_rejects_when_missing_header`
11. `apps.ocpp.tests.test_websocket_creation::test_basic_auth_rejects_invalid_header_format`
12. `apps.ocpp.tests.test_websocket_creation::test_basic_auth_rejects_invalid_credentials`
13. `apps.ocpp.tests.test_websocket_creation::test_basic_auth_rejects_unauthorized_user`
14. `apps.ocpp.tests.test_websocket_creation::test_basic_auth_accepts_charge_station_manager_user`
15. `apps.ocpp.tests.test_websocket_creation::test_unknown_extension_action_replies_with_empty_call_result`
16. `apps.ocpp.tests.test_ocpp_reconnect::test_reconnect_resumes_pending_call`
17. `apps.ocpp.tests.test_ocpp_reconnect::test_reconnect_resumes_pending_call_case_insensitive`
18. `apps.ocpp.tests.test_ocpp_reconnect::test_replayed_result_keeps_pending_queue_intact`
19. `apps.ocpp.tests.test_ocpp_reconnect::test_unexpected_message_does_not_drop_restored_pending`
20. `apps.ocpp.tests.test_call_result_handler_domains::test_configuration_domain_tracks_status_and_resilience`
21. `apps.ocpp.tests.test_call_result_handler_domains::test_transactions_domain_updates_reservation_and_status_mapping`
22. `apps.ocpp.tests.test_call_result_handler_domains::test_authorization_domain_handles_unknown_payloads`
23. `apps.ocpp.tests.test_call_result_handler_domains::test_profiles_domain_updates_profile_and_ignores_malformed_variable_payload`
24. `apps.ocpp.tests.test_call_result_handler_domains::test_diagnostics_domain_updates_log_request_and_diagnostics_metadata`
25. `apps.ocpp.tests.test_status_resets::test_clear_stale_cached_statuses_resets_expected_fields`
26. `apps.ocpp.tests.test_status_resets::test_session_lock_cleanup_runs_for_expired_lock`
27. `apps.ocpp.tests.test_ocpp_handlers::test_cost_updated_rejects_invalid_payload`
28. `apps.ocpp.tests.test_ocpp_handlers::test_get_certificate_status_persists_check`
29. `apps.ocpp.tests.test_ocpp_handlers::test_notify_display_messages_updates_compliance_report`
30. `apps.locals.tests.test_user_data_persistence::test_user_data_applied_after_seed_fixture`

## Why these are low-value

- They are all in already-slow integration-heavy areas, mostly websocket lifecycle flows with near-duplicate setup.
- Several are variant tests that differ only by malformed input flavor or role variant.
- Multiple reconnect and handler-domain tests validate internal state transitions that are also exercised by broader happy-path and command handler suites.
- Removing these first typically yields meaningful runtime savings while preserving core end-to-end behavior tests.
