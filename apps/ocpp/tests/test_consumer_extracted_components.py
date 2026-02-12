"""Unit tests for extracted OCPP consumer components."""

from types import SimpleNamespace

import pytest

from apps.ocpp.consumers.base.consumer.action_dispatch import ActionDispatchRegistry
from apps.ocpp.consumers.base.consumer.connection_flow import (
    AdmissionDecision,
    ConnectionAdmissionService,
)
from apps.ocpp.consumers.base.consumer.message_parsing import (
    normalize_raw_message,
    parse_ocpp_message,
)


class _DummyConsumer:
    """Small stand-in exposing action handlers required by the registry."""

    async def _handle_authorize_action(self, *_args, **_kwargs):
        return {}

    async def _handle_boot_notification_action(self, *_args, **_kwargs):
        return {}

    async def _handle_cleared_charging_limit_action(self, *_args, **_kwargs):
        return {}

    async def _handle_cost_updated_action(self, *_args, **_kwargs):
        return {}

    async def _handle_data_transfer_action(self, *_args, **_kwargs):
        return {}

    async def _handle_diagnostics_status_notification_action(self, *_args, **_kwargs):
        return {}

    async def _handle_firmware_status_notification_action(self, *_args, **_kwargs):
        return {}

    async def _handle_get_15118_ev_certificate_action(self, *_args, **_kwargs):
        return {}

    async def _handle_get_certificate_status_action(self, *_args, **_kwargs):
        return {}

    async def _handle_heartbeat_action(self, *_args, **_kwargs):
        return {}

    async def _handle_log_status_notification_action(self, *_args, **_kwargs):
        return {}

    async def _handle_meter_values_action(self, *_args, **_kwargs):
        return {}

    async def _handle_notify_charging_limit_action(self, *_args, **_kwargs):
        return {}

    async def _handle_notify_customer_information_action(self, *_args, **_kwargs):
        return {}

    async def _handle_notify_display_messages_action(self, *_args, **_kwargs):
        return {}

    async def _handle_notify_ev_charging_needs_action(self, *_args, **_kwargs):
        return {}

    async def _handle_notify_ev_charging_schedule_action(self, *_args, **_kwargs):
        return {}

    async def _handle_notify_event_action(self, *_args, **_kwargs):
        return {}

    async def _handle_notify_monitoring_report_action(self, *_args, **_kwargs):
        return {}

    async def _handle_notify_report_action(self, *_args, **_kwargs):
        return {}

    async def _handle_publish_firmware_status_notification_action(self, *_args, **_kwargs):
        return {}

    async def _handle_report_charging_profiles_action(self, *_args, **_kwargs):
        return {}

    async def _handle_reservation_status_update_action(self, *_args, **_kwargs):
        return {}

    async def _handle_security_event_notification_action(self, *_args, **_kwargs):
        return {}

    async def _handle_sign_certificate_action(self, *_args, **_kwargs):
        return {}

    async def _handle_start_transaction_action(self, *_args, **_kwargs):
        return {}

    async def _handle_status_notification_action(self, *_args, **_kwargs):
        return {}

    async def _handle_stop_transaction_action(self, *_args, **_kwargs):
        return {}

    async def _handle_transaction_event_action(self, *_args, **_kwargs):
        return {}


def test_parse_ocpp_message_supports_forwarding_envelopes():
    parsed = parse_ocpp_message('{"ocpp":[2,"m1","Heartbeat",{}],"meta":{"source":"peer"}}')

    assert parsed is not None
    assert parsed.ocpp_message[2] == "Heartbeat"
    assert parsed.forwarding_meta == {"source": "peer"}


def test_normalize_raw_message_prefers_text_and_encodes_binary():
    assert normalize_raw_message("hello", b"ignored") == "hello"
    assert normalize_raw_message(None, b"abc") == "YWJj"
    assert normalize_raw_message(None, None) is None


def test_parse_ocpp_message_rejects_short_call_frames():
    assert parse_ocpp_message('[2, "msg-only"]') is None


@pytest.mark.anyio
async def test_connection_admission_service_supports_db_wrapper_callables():
    def db_call(fn):
        async def runner():
            return fn()

        return runner

    service = ConnectionAdmissionService(
        feature_state_resolver=lambda existing: AdmissionDecision(existing is not None),
        db_call=db_call,
    )

    assert await service.allow_charge_point_connection(SimpleNamespace(), object()) is True


@pytest.mark.anyio
async def test_connection_admission_service_uses_explicit_resolver():
    service = ConnectionAdmissionService(
        feature_state_resolver=lambda existing: AdmissionDecision(existing is not None)
    )

    assert await service.allow_charge_point_connection(SimpleNamespace(), object()) is True
    assert await service.allow_charge_point_connection(SimpleNamespace(), None) is False


def test_action_dispatch_registry_resolves_core_actions():
    consumer = _DummyConsumer()
    registry = ActionDispatchRegistry(consumer)

    assert registry.resolve("MeterValues") == consumer._handle_meter_values_action
    assert registry.resolve("TransactionEvent") == consumer._handle_transaction_event_action
    assert registry.resolve("UnknownAction") is None
