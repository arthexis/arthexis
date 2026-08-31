"""Tests for OCPP consumer action dispatch and extracted handler adapters."""

import importlib
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from channels.db import database_sync_to_async
from django.apps import apps as django_apps
from django.core.cache import cache
from django.utils import timezone

from apps.cards.models import RFID
from apps.energy.models import CustomerAccount
from apps.ocpp import auto_start, store
from apps.ocpp.consumers import CSMSConsumer
from apps.ocpp.consumers.base.connection_flow import ConnectionFlowMixin
from apps.ocpp.consumers.base.rfid import AuthorizationDecision
from apps.ocpp.consumers.base.routing import ActionRouter
from apps.ocpp.consumers.connection import RateLimitedConnectionMixin
from apps.ocpp.consumers.csms import consumer as csms_consumer
from apps.ocpp.consumers.csms.actions.authorization import AuthorizationActionHandler
from apps.ocpp.consumers.csms.connectors import CSMSConnectorAssignmentMixin
from apps.ocpp.consumers.csms.handlers import status as status_handlers
from apps.ocpp.consumers.path_metadata import bounded_last_path
from apps.ocpp.models import AutoStartAttempt, Charger, Transaction


@pytest.fixture(autouse=True)
def reset_store_state():
    """Reset in-memory store state used by dispatch tests."""

    cache.clear()
    store.connections.clear()
    store.logs["charger"].clear()
    yield
    cache.clear()
    store.connections.clear()
    store.logs["charger"].clear()


@pytest.mark.anyio
async def test_disconnect_does_not_remove_newer_store_connection(monkeypatch):
    old_connection = ConnectionFlowMixin()
    old_connection.charger_id = "CP-RACE"
    old_connection.connector_value = 1
    old_connection.client_ip = None
    old_connection.store_key = store.identity_key(
        old_connection.charger_id,
        old_connection.connector_value,
    )

    newer_connector = SimpleNamespace()
    newer_pending = SimpleNamespace()
    pending_key = store.pending_key(old_connection.charger_id)
    monkeypatch.setitem(store.connections, old_connection.store_key, newer_connector)
    monkeypatch.setitem(store.connections, pending_key, newer_pending)

    monkeypatch.setattr(store, "release_ip_connection", lambda *_args: None)
    monkeypatch.setattr(store, "get_transaction", lambda *_args: None)
    monkeypatch.setattr(store, "end_session_log", lambda *_args: None)
    monkeypatch.setattr(store, "stop_session_lock", lambda: None)
    monkeypatch.setattr(store, "clear_pending_calls", lambda *_args: None)
    monkeypatch.setattr(store, "add_log", lambda *_args, **_kwargs: None)

    await old_connection.disconnect(1000)

    assert store.connections[old_connection.store_key] is newer_connector
    assert store.connections[pending_key] is newer_pending


@pytest.mark.anyio
async def test_no_subprotocol_probe_does_not_replace_explicit_connection(monkeypatch):
    existing = SimpleNamespace(
        client_ip="198.51.100.10",
        close=AsyncMock(),
        _canonicalize_ocpp_subprotocol=Mock(return_value="ocpp1.6"),
        _get_offered_subprotocols=Mock(return_value=["ocpp1.6"]),
    )
    probe = RateLimitedConnectionMixin()
    probe.store_key = store.pending_key("CP-PROBE")
    probe.client_ip = "198.51.100.11"
    probe.close = AsyncMock()
    probe._get_offered_subprotocols = Mock(return_value=[])

    monkeypatch.setitem(store.connections, probe.store_key, existing)
    monkeypatch.setattr(store, "release_ip_connection", Mock())
    monkeypatch.setattr(store, "add_log", Mock())

    accepted = await probe._accept_connection(None)

    assert accepted is False
    assert store.connections[probe.store_key] is existing
    existing.close.assert_not_awaited()
    probe.close.assert_awaited_once_with(code=1008)
    store.release_ip_connection.assert_not_called()


@pytest.mark.anyio
async def test_no_rates_consumer_fallback_enforces_configured_limit(monkeypatch):
    """No-Rates profiles should still throttle repeated OCPP connects."""

    original_is_installed = django_apps.is_installed

    def fake_is_installed(app_label):
        if app_label == "apps.rates":
            return False
        return original_is_installed(app_label)

    monkeypatch.setattr(django_apps, "is_installed", fake_is_installed)
    reloaded = importlib.reload(csms_consumer)
    try:

        class ProbeConsumer(reloaded.RateLimitedConsumerMixin):
            rate_limit_scope = "ocpp-connect-test"
            rate_limit_fallback = 1
            rate_limit_window = 60
            rate_limit_close_code = 4321

            def __init__(self):
                self.scope = {"client": ("198.51.100.77", 5678)}
                self.closed_codes = []

            async def close(self, code=None):
                self.closed_codes.append(code)

        first = ProbeConsumer()
        second = ProbeConsumer()

        assert await first.enforce_rate_limit() is True
        assert await second.enforce_rate_limit() is False
    finally:
        monkeypatch.setattr(django_apps, "is_installed", original_is_installed)
        importlib.reload(csms_consumer)

    assert second.closed_codes == [4321]


@pytest.mark.anyio
async def test_action_router_resolves_transaction_and_notification_handlers():
    """Router exposes explicit registry entries for high-risk actions."""

    consumer = CSMSConsumer(scope={}, receive=None, send=None)
    router = ActionRouter(consumer)

    assert (
        router.resolve("TransactionEvent") == consumer._handle_transaction_event_action
    )
    assert router.resolve("MeterValues") == consumer._handle_meter_values_action
    assert (
        router.resolve("PublishFirmwareStatusNotification")
        == consumer._handle_publish_firmware_status_notification_action
    )


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_assign_connector_remains_consumer_entrypoint_after_extraction():
    """Connector assignment should preserve row creation and store identity routing."""

    consumer = CSMSConsumer(scope={"path": "/ocpp/CP-MIXIN"}, receive=None, send=None)
    consumer.scope = {"path": "/ocpp/CP-MIXIN"}
    consumer.charger_id = "CP-MIXIN"
    consumer.charger = None
    consumer.aggregate_charger = None
    consumer.charging_station = None
    consumer.client_ip = None
    consumer.connector_value = None
    consumer.store_key = store.pending_key(consumer.charger_id)

    await consumer._assign_connector("1")

    charger = await database_sync_to_async(Charger.objects.get)(
        charger_id="CP-MIXIN",
        connector_id=1,
    )
    assert isinstance(consumer, CSMSConnectorAssignmentMixin)
    assert consumer.charger.pk == charger.pk
    assert consumer.connector_value == 1
    assert consumer.charging_station.station_id == "CP-MIXIN"
    assert consumer.store_key == store.identity_key("CP-MIXIN", 1)
    assert store.connections[consumer.store_key] is consumer


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_assign_connector_refreshes_last_path_for_existing_connector():
    """Reconnects should refresh persisted path metadata for existing connectors."""

    await database_sync_to_async(Charger.objects.create)(
        charger_id="CP-MIXIN-EXISTING",
        connector_id=2,
        last_path="/old/path",
    )
    consumer = CSMSConsumer(
        scope={"path": "/ocpp/CP-MIXIN-EXISTING/new"},
        receive=None,
        send=None,
    )
    consumer.scope = {"path": "/ocpp/CP-MIXIN-EXISTING/new"}
    consumer.charger_id = "CP-MIXIN-EXISTING"
    consumer.charger = None
    consumer.aggregate_charger = None
    consumer.charging_station = None
    consumer.client_ip = None
    consumer.connector_value = None
    consumer.store_key = store.pending_key(consumer.charger_id)

    await consumer._assign_connector("2")

    charger = await database_sync_to_async(Charger.objects.get)(
        charger_id="CP-MIXIN-EXISTING",
        connector_id=2,
    )
    assert charger.last_path == "/ocpp/CP-MIXIN-EXISTING/new"
    assert consumer.charger.pk == charger.pk


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_assign_connector_bounds_last_path_for_existing_connector():
    """Persisted path metadata must fit the model field on strict databases."""

    await database_sync_to_async(Charger.objects.create)(
        charger_id="CP-MIXIN-BOUNDED",
        connector_id=2,
        last_path="/old/path",
    )
    overlong_path = "/" + "/".join(["prefix"] * 50) + "/ocpp/CP-MIXIN-BOUNDED"
    consumer = CSMSConsumer(scope={"path": overlong_path}, receive=None, send=None)
    consumer.scope = {"path": overlong_path}
    consumer.charger_id = "CP-MIXIN-BOUNDED"
    consumer.charger = None
    consumer.aggregate_charger = None
    consumer.charging_station = None
    consumer.client_ip = None
    consumer.connector_value = None
    consumer.store_key = store.pending_key(consumer.charger_id)

    await consumer._assign_connector("2")

    charger = await database_sync_to_async(Charger.objects.get)(
        charger_id="CP-MIXIN-BOUNDED",
        connector_id=2,
    )
    assert len(overlong_path) > Charger._meta.get_field("last_path").max_length
    assert (
        charger.last_path
        == overlong_path[: Charger._meta.get_field("last_path").max_length]
    )


def test_bounded_last_path_decodes_bytes_scope_path():
    assert bounded_last_path({"path": b"/ocpp/CP-BYTES"}, Charger) == "/ocpp/CP-BYTES"


@pytest.mark.anyio
async def test_dispatch_routes_via_registry_for_transaction_event():
    """Dispatch uses the explicit action registry from routing.py."""

    consumer = CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = "CP-ROUTE"
    consumer.charger_id = "CP-ROUTE"
    consumer._log_triggered_follow_up = lambda *_args, **_kwargs: None
    consumer._assign_connector = AsyncMock()
    consumer._handle_transaction_event_action = AsyncMock(
        return_value={"idTokenInfo": {}}
    )
    consumer.send = AsyncMock()

    msg = [2, "msg-1", "TransactionEvent", {"connectorId": 1}]
    await consumer._handle_call_message(msg, json.dumps(msg), json.dumps(msg))

    consumer._handle_transaction_event_action.assert_awaited_once()
    consumer.send.assert_awaited_once()


@pytest.mark.anyio
async def test_dispatch_registry_keeps_status_notification_binding():
    """StatusNotification should continue dispatching to the consumer handler."""

    consumer = CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = "CP-STATUS"
    consumer.charger_id = "CP-STATUS"
    consumer._log_triggered_follow_up = lambda *_args, **_kwargs: None
    consumer._assign_connector = AsyncMock()
    consumer._handle_status_notification_action = AsyncMock(return_value={})
    consumer.send = AsyncMock()

    msg = [
        2,
        "msg-status-1",
        "StatusNotification",
        {"connectorId": 1, "status": "Available"},
    ]
    await consumer._handle_call_message(msg, json.dumps(msg), json.dumps(msg))

    consumer._handle_status_notification_action.assert_awaited_once()
    consumer.send.assert_awaited_once()


@pytest.mark.anyio
async def test_status_notification_available_routes_through_availability_handlers(
    monkeypatch,
):
    """Availability transitions should be delegated to the dedicated mixin methods."""

    consumer = CSMSConsumer(scope={}, receive=None, send=None)
    consumer._assign_connector = AsyncMock()
    consumer._handle_available_status_transition = AsyncMock()
    consumer._sync_availability_state_from_status = AsyncMock()
    consumer.charger_id = "CP-AVAIL"
    consumer.store_key = "CP-AVAIL"
    consumer.connector_value = 7
    consumer.charger = SimpleNamespace()
    consumer.aggregate_charger = None

    monkeypatch.setattr(
        status_handlers.persistence,
        "update_status_notification_records",
        Mock(return_value=[]),
    )
    monkeypatch.setattr(
        status_handlers.persistence,
        "sync_charger_error_security_event",
        Mock(return_value=None),
    )

    await consumer._handle_status_notification_action(
        {"connectorId": 7, "status": "Available", "errorCode": "NoError"},
        "msg-status-2",
        "",
        "",
    )

    consumer._handle_available_status_transition.assert_awaited_once_with(7)
    consumer._sync_availability_state_from_status.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_preparing_sends_remote_start_for_configured_auto_start_id_tag(
    monkeypatch,
):
    """Preparing should trigger an OCPP 1.6 remote start using the configured tag."""

    charger = await database_sync_to_async(Charger.objects.create)(
        charger_id="CP-AUTO-START", connector_id=1, auto_start_id_tag="AUTO-START-003"
    )
    consumer = CSMSConsumer(scope={}, receive=None, send=None)
    consumer.charger_id = charger.charger_id
    consumer.charger = charger
    consumer.aggregate_charger = None
    consumer.connector_value = 1
    consumer.store_key = store.identity_key(charger.charger_id, 1)
    consumer._assign_connector = AsyncMock()
    consumer._sync_availability_state_from_status = AsyncMock()
    consumer.send = AsyncMock()
    monkeypatch.setattr(
        status_handlers.persistence,
        "update_status_notification_records",
        Mock(return_value=[]),
    )
    monkeypatch.setattr(
        status_handlers.persistence,
        "sync_charger_error_security_event",
        Mock(return_value=None),
    )

    await consumer._handle_status_notification_action(
        {"connectorId": 1, "status": "Preparing", "errorCode": "NoError"},
        "msg-auto-start",
        "",
        "",
    )
    await consumer._handle_status_notification_action(
        {"connectorId": 1, "status": "Preparing", "errorCode": "NoError"},
        "msg-auto-start-repeat",
        "",
        "",
    )
    await consumer._handle_status_notification_action(
        {"connectorId": 1, "status": "Charging", "errorCode": "NoError"},
        "msg-auto-start-charging",
        "",
        "",
    )
    await database_sync_to_async(Charger.objects.filter(pk=charger.pk).update)(
        auto_start_id_tag="AUTO-START-CHANGED"
    )
    await consumer._handle_status_notification_action(
        {"connectorId": 1, "status": "Preparing", "errorCode": "NoError"},
        "msg-auto-start-config-change",
        "",
        "",
    )

    consumer.send.assert_awaited_once()
    frame = json.loads(consumer.send.await_args.args[0])
    assert frame[2] == "RemoteStartTransaction"
    assert frame[3] == {"idTag": "AUTO-START-003", "connectorId": 1}
    attempt = await database_sync_to_async(AutoStartAttempt.objects.get)(
        message_id=frame[1]
    )
    assert attempt.state == AutoStartAttempt.State.STARTED
    metadata = store.pop_pending_call(frame[1])
    assert metadata is not None
    assert metadata["auto_start"] is True
    assert metadata["auto_start_attempt_id"]


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_ocpp2_occupied_uses_aggregate_auto_start_and_reported_evse(monkeypatch):
    """OCPP 2 uses Occupied and retains its EVSE id for RequestStartTransaction."""

    aggregate = await database_sync_to_async(Charger.objects.create)(
        charger_id="CP-AUTO-START-2X", auto_start_id_tag="AUTO-START-2X"
    )
    connector = await database_sync_to_async(Charger.objects.create)(
        charger_id=aggregate.charger_id, connector_id=2
    )
    consumer = CSMSConsumer(scope={}, receive=None, send=None)
    consumer.charger_id = aggregate.charger_id
    consumer.charger = connector
    consumer.aggregate_charger = aggregate
    consumer.connector_value = 2
    consumer.ocpp_version = "ocpp2.0.1"
    consumer.store_key = store.identity_key(aggregate.charger_id, 2)
    consumer._assign_connector = AsyncMock()
    consumer._sync_availability_state_from_status = AsyncMock()
    consumer.send = AsyncMock()
    monkeypatch.setattr(
        status_handlers.persistence,
        "update_status_notification_records",
        Mock(return_value=[]),
    )
    monkeypatch.setattr(
        status_handlers.persistence,
        "sync_charger_error_security_event",
        Mock(return_value=None),
    )

    await consumer._handle_status_notification_action(
        {"evseId": 7, "connectorId": 2, "connectorStatus": "Occupied"},
        "msg-auto-start-2x",
        "",
        "",
    )

    consumer.send.assert_awaited_once()
    frame = json.loads(consumer.send.await_args.args[0])
    assert frame[2] == "RequestStartTransaction"
    assert frame[3]["idToken"] == {"idToken": "AUTO-START-2X", "type": "Central"}
    assert frame[3]["evseId"] == 7

    await consumer._handle_status_notification_action(
        {"evseId": 8, "connectorId": 2, "connectorStatus": "Occupied"},
        "msg-auto-start-2x-second-evse",
        "",
        "",
    )

    assert consumer.send.await_count == 2


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_auto_start_timeout_releases_matching_reservation(monkeypatch):
    """A timed-out request permits one retry while the vehicle remains connected."""

    charger = await database_sync_to_async(Charger.objects.create)(
        charger_id="CP-AUTO-TIMEOUT", connector_id=1, auto_start_id_tag="AUTO-TIMEOUT"
    )
    consumer = CSMSConsumer(scope={}, receive=None, send=None)
    consumer.charger_id = charger.charger_id
    consumer.charger = charger
    consumer.aggregate_charger = None
    consumer.connector_value = 1
    consumer.store_key = store.identity_key(charger.charger_id, 1)
    consumer._assign_connector = AsyncMock()
    consumer._sync_availability_state_from_status = AsyncMock()
    consumer.send = AsyncMock()
    timeout_callbacks = []
    monkeypatch.setattr(
        status_handlers.store,
        "schedule_call_timeout",
        Mock(
            side_effect=lambda *_args, **kwargs: timeout_callbacks.append(
                kwargs["on_timeout"]
            )
        ),
    )
    monkeypatch.setattr(
        status_handlers.persistence,
        "update_status_notification_records",
        Mock(return_value=[]),
    )
    monkeypatch.setattr(
        status_handlers.persistence,
        "sync_charger_error_security_event",
        Mock(return_value=None),
    )

    await consumer._handle_status_notification_action(
        {"connectorId": 1, "status": "Preparing", "errorCode": "NoError"},
        "msg-auto-timeout",
        "",
        "",
    )
    attempt = await database_sync_to_async(AutoStartAttempt.objects.get)(
        charger=charger,
        reservation_scope="connector:1",
    )
    assert attempt.state == AutoStartAttempt.State.REQUESTED
    assert auto_start.REQUEST_TIMEOUT.total_seconds() == 90
    assert status_handlers.store.schedule_call_timeout.call_args.kwargs["timeout"] == 90

    await timeout_callbacks[0]({})

    await database_sync_to_async(attempt.refresh_from_db)()
    assert attempt.state == AutoStartAttempt.State.TIMED_OUT
    assert attempt.retry_after is not None


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_expired_auto_start_retries_on_the_first_status_after_reconnect():
    """A stale request does not require a second status notification to retry."""

    charger = await database_sync_to_async(Charger.objects.create)(
        charger_id="CP-AUTO-EXPIRED", connector_id=1
    )
    expired = await database_sync_to_async(AutoStartAttempt.objects.create)(
        charger=charger,
        reservation_scope="connector:1",
        id_tag="AUTO-EXPIRED",
        message_id="expired-auto-start",
        action="RemoteStartTransaction",
        expires_at=timezone.now() - timedelta(seconds=1),
    )

    replacement = await database_sync_to_async(auto_start.reserve_attempt)(
        charger_pk=charger.pk,
        reservation_scope="connector:1",
        id_tag="AUTO-EXPIRED",
        message_id="replacement-auto-start",
        action="RemoteStartTransaction",
    )

    await database_sync_to_async(expired.refresh_from_db)()
    assert expired.state == AutoStartAttempt.State.TIMED_OUT
    assert expired.retry_after is not None
    assert replacement is not None
    assert replacement.state == AutoStartAttempt.State.REQUESTED


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_auto_start_rejection_releases_only_its_attempt(monkeypatch):
    """A rejected remote start may retry after its bounded cooldown."""

    charger = await database_sync_to_async(Charger.objects.create)(
        charger_id="CP-AUTO-REJECT", connector_id=1, auto_start_id_tag="AUTO-REJECT"
    )
    consumer = CSMSConsumer(scope={}, receive=None, send=None)
    consumer.charger_id = charger.charger_id
    consumer.charger = charger
    consumer.aggregate_charger = None
    consumer.connector_value = 1
    consumer.store_key = store.identity_key(charger.charger_id, 1)
    consumer._assign_connector = AsyncMock()
    consumer._sync_availability_state_from_status = AsyncMock()
    consumer.send = AsyncMock()
    monkeypatch.setattr(
        status_handlers.persistence,
        "update_status_notification_records",
        Mock(return_value=[]),
    )
    monkeypatch.setattr(
        status_handlers.persistence,
        "sync_charger_error_security_event",
        Mock(return_value=None),
    )

    await consumer._handle_status_notification_action(
        {"connectorId": 1, "status": "Preparing", "errorCode": "NoError"},
        "msg-auto-reject",
        "",
        "",
    )
    frame = json.loads(consumer.send.await_args.args[0])
    await consumer._handle_call_result(frame[1], {"status": "Rejected"})

    attempt = await database_sync_to_async(AutoStartAttempt.objects.get)(
        message_id=frame[1]
    )
    assert attempt.state == AutoStartAttempt.State.REJECTED
    assert attempt.retry_after is not None

    await consumer._handle_status_notification_action(
        {"connectorId": 1, "status": "Preparing", "errorCode": "NoError"},
        "msg-auto-reject-cooldown",
        "",
        "",
    )
    consumer.send.assert_awaited_once()

    await database_sync_to_async(AutoStartAttempt.objects.filter(pk=attempt.pk).update)(
        retry_after=timezone.now() - timedelta(seconds=1)
    )
    await consumer._handle_status_notification_action(
        {"connectorId": 1, "status": "Preparing", "errorCode": "NoError"},
        "msg-auto-reject-retry",
        "",
        "",
    )
    assert consumer.send.await_count == 2


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_old_auto_start_timeout_cannot_release_a_new_attempt():
    """Attempt IDs keep a stale timeout from clearing a reconfigured request."""

    charger = await database_sync_to_async(Charger.objects.create)(
        charger_id="CP-AUTO-ATTEMPT", connector_id=1
    )
    old_attempt = await database_sync_to_async(auto_start.reserve_attempt)(
        charger_pk=charger.pk,
        reservation_scope="connector:1",
        id_tag="AUTO-ATTEMPT",
        message_id="old-auto-start",
        action="RemoteStartTransaction",
    )
    assert old_attempt is not None
    await database_sync_to_async(auto_start.release_chargers)(charger_ids=[charger.pk])
    new_attempt = await database_sync_to_async(auto_start.reserve_attempt)(
        charger_pk=charger.pk,
        reservation_scope="connector:1",
        id_tag="AUTO-ATTEMPT",
        message_id="new-auto-start",
        action="RemoteStartTransaction",
    )
    assert new_attempt is not None

    await CSMSConsumer._release_auto_start_reservation_on_timeout(
        str(old_attempt.attempt_id)
    )
    await database_sync_to_async(new_attempt.refresh_from_db)()
    assert new_attempt.state == AutoStartAttempt.State.REQUESTED


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_auto_start_call_error_releases_only_its_attempt():
    """A CALLERROR transitions only the pending auto-start attempt to failed."""

    charger = await database_sync_to_async(Charger.objects.create)(
        charger_id="CP-AUTO-ERROR", connector_id=1
    )
    attempt = await database_sync_to_async(auto_start.reserve_attempt)(
        charger_pk=charger.pk,
        reservation_scope="connector:1",
        id_tag="AUTO-ERROR",
        message_id="auto-start-error",
        action="RemoteStartTransaction",
    )
    assert attempt is not None
    consumer = CSMSConsumer(scope={}, receive=None, send=None)
    consumer.charger_id = charger.charger_id
    consumer.store_key = store.identity_key(charger.charger_id, 1)
    store.register_pending_call(
        "auto-start-error",
        {
            "action": "RemoteStartTransaction",
            "charger_id": charger.charger_id,
            "log_key": consumer.store_key,
            "auto_start_attempt_id": str(attempt.attempt_id),
        },
    )

    await consumer._handle_call_error(
        "auto-start-error",
        "InternalError",
        "charger rejected the request",
        {},
    )

    await database_sync_to_async(attempt.refresh_from_db)()
    assert attempt.state == AutoStartAttempt.State.FAILED
    assert attempt.retry_after is not None


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_ocpp_id_tag_account_is_found_without_an_rfid():
    """A direct OCPP idTag outranks a prefix-matched RFID without a card binding."""

    charger = await database_sync_to_async(Charger.objects.create)(
        charger_id="CP-AUTO-ACCOUNT"
    )
    account = await database_sync_to_async(CustomerAccount.objects.create)(
        name="AUTO-START AUTO-START-004",
        ocpp_id_tag="AUTO-START-004",
        service_account=False,
    )
    await database_sync_to_async(RFID.objects.create)(rfid="AUTO-START-003")
    consumer = CSMSConsumer(scope={}, receive=None, send=None)
    consumer.charger = charger

    resolved = await consumer._get_account("AUTO-START-004")

    assert resolved is not None
    assert resolved.pk == account.pk
    tag, resolved_account = await consumer._apply_rfid_authorization_side_effects(
        id_tag="AUTO-START-004",
        decision=AuthorizationDecision(
            status="Accepted",
            reason="account_authorized",
            policy="strict",
            should_mark_seen=True,
            should_auto_enroll=True,
        ),
        tag=None,
        tag_created=False,
        account=resolved,
    )
    assert tag is None
    assert resolved_account == account
    assert not await database_sync_to_async(
        RFID.objects.filter(rfid="AUTO-START-004").exists
    )()


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_auto_start_marks_evse_scope_started_across_connector_rows():
    """An OCPP 2 transaction event may omit connectorId after status selected it."""

    status_charger = await database_sync_to_async(Charger.objects.create)(
        charger_id="CP-AUTO-EVSE", connector_id=2
    )
    transaction_charger = await database_sync_to_async(Charger.objects.create)(
        charger_id="CP-AUTO-EVSE", connector_id=8
    )
    attempt = await database_sync_to_async(auto_start.reserve_attempt)(
        charger_pk=status_charger.pk,
        reservation_scope="evse:8",
        id_tag="AUTO-EVSE",
        message_id="auto-start-evse",
        action="RequestStartTransaction",
    )
    assert attempt is not None

    started = await database_sync_to_async(auto_start.mark_scope_started)(
        charger_pk=transaction_charger.pk,
        reservation_scope="evse:8",
    )

    await database_sync_to_async(attempt.refresh_from_db)()
    assert started is True
    assert attempt.state == AutoStartAttempt.State.STARTED
    assert attempt.expires_at is None


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_auto_start_reserves_an_ocpp2_evse_across_connector_rows():
    """Several connectors of one OCPP 2 EVSE share one remote-start attempt."""

    first_connector = await database_sync_to_async(Charger.objects.create)(
        charger_id="CP-AUTO-EVSE-RESERVE", connector_id=1
    )
    second_connector = await database_sync_to_async(Charger.objects.create)(
        charger_id="CP-AUTO-EVSE-RESERVE", connector_id=2
    )

    first_attempt = await database_sync_to_async(auto_start.reserve_attempt)(
        charger_pk=first_connector.pk,
        reservation_scope="evse:8",
        id_tag="AUTO-EVSE-RESERVE",
        message_id="auto-start-evse-first",
        action="RequestStartTransaction",
    )
    second_attempt = await database_sync_to_async(auto_start.reserve_attempt)(
        charger_pk=second_connector.pk,
        reservation_scope="evse:8",
        id_tag="AUTO-EVSE-RESERVE",
        message_id="auto-start-evse-second",
        action="RequestStartTransaction",
    )

    assert first_attempt is not None
    assert second_attempt is None


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_exact_rfid_ownership_outranks_a_direct_ocpp_id_tag():
    """An exact physical RFID remains authoritative over a direct idTag account."""

    direct_account = await database_sync_to_async(CustomerAccount.objects.create)(
        name="DIRECT RFID COLLISION",
        ocpp_id_tag="A1B2C3D4",
    )
    rfid_account = await database_sync_to_async(CustomerAccount.objects.create)(
        name="PHYSICAL RFID OWNER"
    )
    rfid = await database_sync_to_async(RFID.objects.create)(rfid="A1B2C3D4")
    await database_sync_to_async(rfid_account.rfids.add)(rfid)
    consumer = CSMSConsumer(scope={}, receive=None, send=None)

    resolved = await consumer._get_account("A1B2C3D4")

    assert resolved is not None
    assert resolved.pk == rfid_account.pk
    assert resolved.pk != direct_account.pk


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_auto_start_id_tag_outranks_a_later_matching_rfid():
    """An active auto-start idTag remains reserved from later RFID attribution."""

    id_tag = "A1B2C3D4"
    auto_start_account = await database_sync_to_async(CustomerAccount.objects.create)(
        name="AUTO-START RESERVED", ocpp_id_tag=id_tag, service_account=True
    )
    charger = await database_sync_to_async(Charger.objects.create)(
        charger_id="CP-AUTO-RESERVED", auto_start_id_tag=id_tag
    )
    rfid_account = await database_sync_to_async(CustomerAccount.objects.create)(
        name="PHYSICAL RFID OWNER"
    )
    rfid = await database_sync_to_async(RFID.objects.create)(rfid=id_tag)
    await database_sync_to_async(rfid_account.rfids.add)(rfid)
    consumer = CSMSConsumer(scope={}, receive=None, send=None)
    consumer.charger = charger

    resolved = await consumer._get_account(id_tag)

    assert resolved is not None
    assert resolved.pk == auto_start_account.pk
    assert resolved.pk != rfid_account.pk


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_auto_start_id_tag_does_not_override_rfid_on_another_charger():
    """An auto-start reservation only applies to its charger connection."""

    id_tag = "A1B2C3D4"
    await database_sync_to_async(CustomerAccount.objects.create)(
        name="AUTO-START RESERVED", ocpp_id_tag=id_tag, service_account=True
    )
    await database_sync_to_async(Charger.objects.create)(
        charger_id="CP-AUTO-RESERVED", auto_start_id_tag=id_tag
    )
    other_charger = await database_sync_to_async(Charger.objects.create)(
        charger_id="CP-OTHER"
    )
    rfid_account = await database_sync_to_async(CustomerAccount.objects.create)(
        name="PHYSICAL RFID OWNER"
    )
    rfid = await database_sync_to_async(RFID.objects.create)(rfid=id_tag)
    await database_sync_to_async(rfid_account.rfids.add)(rfid)
    consumer = CSMSConsumer(scope={}, receive=None, send=None)
    consumer.charger = other_charger

    resolved = await consumer._get_account(id_tag)

    assert resolved is not None
    assert resolved.pk == rfid_account.pk


@pytest.mark.anyio
@pytest.mark.django_db(transaction=True)
async def test_direct_ocpp_service_authorization_does_not_create_an_rfid():
    """A direct service idTag remains separate from physical RFID records."""

    charger = await database_sync_to_async(Charger.objects.create)(
        charger_id="CP-AUTO-DIRECT"
    )
    await database_sync_to_async(CustomerAccount.objects.create)(
        name="AUTO-START AUTO-DIRECT", ocpp_id_tag="AUTO-DIRECT", service_account=True
    )
    consumer = CSMSConsumer(scope={}, receive=None, send=None)
    consumer.charger = charger
    consumer._evaluate_authorization_policy = AsyncMock(
        return_value=AuthorizationDecision(
            status="Accepted",
            reason="account_authorized",
            policy="strict",
            should_mark_seen=True,
            should_auto_enroll=False,
        )
    )
    consumer._record_rfid_attempt = AsyncMock()

    reply = await AuthorizationActionHandler(consumer).handle(
        {"idTag": "AUTO-DIRECT"}, "msg-auto-direct", "", ""
    )

    assert reply["idTagInfo"]["status"] == "Accepted"
    assert not await database_sync_to_async(
        RFID.objects.filter(rfid="AUTO-DIRECT").exists
    )()


@pytest.mark.anyio
async def test_heartbeat_updates_last_heartbeat_without_name_error(monkeypatch):
    """Heartbeat updates persisted last_heartbeat using the Charger model."""

    consumer = CSMSConsumer(scope={}, receive=None, send=None)
    consumer.charger_id = "CP-HB"
    consumer.charger = SimpleNamespace(last_heartbeat=None)
    consumer.aggregate_charger = SimpleNamespace(last_heartbeat=None)

    update_mock = Mock(return_value=1)
    filter_mock = Mock(return_value=SimpleNamespace(update=update_mock))
    monkeypatch.setattr(
        status_handlers.Charger,
        "objects",
        SimpleNamespace(filter=filter_mock),
    )

    reply = await consumer._handle_heartbeat_action({}, "msg-hb-1", "", "")

    assert "currentTime" in reply
    filter_mock.assert_called_once_with(charger_id="CP-HB")
    update_mock.assert_called_once()
    assert consumer.charger.last_heartbeat is not None
    assert consumer.aggregate_charger.last_heartbeat is not None


@pytest.mark.anyio
async def test_ocpp21_cp_to_csms_actions_resolve_to_concrete_handlers():
    """OCPP 2.1 CP->CSMS actions should resolve via router, not empty fallthrough."""

    consumer = CSMSConsumer(scope={}, receive=None, send=None)
    router = ActionRouter(consumer)

    expected_bindings = {
        "BootNotification": consumer._handle_boot_notification_action,
        "DataTransfer": consumer._handle_data_transfer_action,
        "Heartbeat": consumer._handle_heartbeat_action,
        "LogStatusNotification": consumer._handle_log_status_notification_action,
        "MeterValues": consumer._handle_meter_values_action,
        "NotifyChargingLimit": consumer._action_handler("NotifyChargingLimit").handle,
        "NotifyCustomerInformation": consumer._handle_notify_customer_information_action,
        "NotifyDisplayMessages": consumer._action_handler(
            "NotifyDisplayMessages"
        ).handle,
        "NotifyEVChargingNeeds": consumer._handle_notify_ev_charging_needs_action,
        "NotifyEVChargingSchedule": consumer._handle_notify_ev_charging_schedule_action,
        "PublishFirmwareStatusNotification": consumer._handle_publish_firmware_status_notification_action,
        "ReportChargingProfiles": consumer._handle_report_charging_profiles_action,
        "SecurityEventNotification": consumer._handle_security_event_notification_action,
        "StatusNotification": consumer._handle_status_notification_action,
    }

    for action, handler in expected_bindings.items():
        resolved = router.resolve(action)
        assert resolved is not None
        assert getattr(resolved, "__name__", "") == getattr(handler, "__name__", "")
        assert "stub" not in getattr(resolved, "__qualname__", "").casefold()


def test_status_notification_normalization_maps_ocpp21_fields():
    consumer = CSMSConsumer(scope={}, receive=None, send=None)

    payload = {
        "connectorStatus": "Occupied",
        "evse": {"id": 3},
        "statusInfo": {"reasonCode": "InternalError", "additionalInfo": "door-open"},
        "timestamp": "2026-01-01T00:00:00Z",
    }

    normalized = consumer._normalized_status_notification_payload(payload)

    assert normalized["status"] == "Occupied"
    assert normalized["connectorId"] == 3
    assert normalized["errorCode"] == "NoError"
    assert normalized["vendorId"] == "InternalError"
    assert normalized["info"] == "door-open"


def test_meter_values_normalization_maps_ocpp21_evse_to_connector_id():
    consumer = CSMSConsumer(scope={}, receive=None, send=None)

    normalized = consumer._normalized_meter_values_payload(
        {"evse": {"id": "4"}, "meterValue": []}
    )

    assert normalized["connectorId"] == "4"


def test_meter_values_normalization_preserves_zero_connector_id():
    consumer = CSMSConsumer(scope={}, receive=None, send=None)

    normalized = consumer._normalized_meter_values_payload(
        {"evse": {"connectorId": 0, "id": 9}, "evseId": 3, "meterValue": []}
    )

    assert normalized["connectorId"] == 0


def test_status_notification_normalization_preserves_zero_connector_id():
    consumer = CSMSConsumer(scope={}, receive=None, send=None)

    normalized = consumer._normalized_status_notification_payload(
        {"evse": {"connectorId": 0, "id": 9}, "evseId": 3}
    )

    assert normalized["connectorId"] == 0


@pytest.mark.anyio
async def test_store_meter_values_resolves_non_numeric_transaction_id_from_db(
    monkeypatch,
):
    consumer = CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = "CP-TX"
    consumer.charger = SimpleNamespace(id=10)
    consumer._assign_connector = AsyncMock()
    consumer._ensure_ocpp_transaction_identifier = AsyncMock()
    consumer._process_meter_value_entries = AsyncMock()

    resolved = SimpleNamespace(pk=42, ocpp_transaction_id="tx-uuid-42")
    lookup = AsyncMock(return_value=resolved)
    monkeypatch.setattr(Transaction, "aget_by_ocpp_id", lookup)

    store.transactions.pop(consumer.store_key, None)
    payload = {"connectorId": 1, "transactionId": "tx-uuid-42", "meterValue": []}
    await consumer._store_meter_values(
        payload, raw_message='[2, "id", "MeterValues", {}]'
    )

    lookup.assert_awaited_once_with(consumer.charger, "tx-uuid-42")
    assert store.transactions[consumer.store_key] is resolved


@pytest.mark.anyio
async def test_store_meter_values_creates_non_numeric_transaction_id_when_missing(
    monkeypatch,
):
    consumer = CSMSConsumer(scope={}, receive=None, send=None)
    consumer.store_key = "CP-TX-MISS"
    consumer.charger = SimpleNamespace(id=11)
    consumer._assign_connector = AsyncMock()
    consumer._ensure_ocpp_transaction_identifier = AsyncMock()
    consumer._process_meter_value_entries = AsyncMock()

    lookup = AsyncMock(return_value=None)
    monkeypatch.setattr(Transaction, "aget_by_ocpp_id", lookup)

    def fake_database_sync_to_async(sync_fn):
        async def wrapped(*args, **kwargs):
            return sync_fn(*args, **kwargs)

        return wrapped

    monkeypatch.setattr(
        csms_consumer, "database_sync_to_async", fake_database_sync_to_async
    )

    created = SimpleNamespace(pk=501, ocpp_transaction_id="tx-uuid-501")
    create_mock = Mock(return_value=created)
    monkeypatch.setattr(Transaction.objects, "create", create_mock)

    store.transactions.pop(consumer.store_key, None)
    payload = {"connectorId": 1, "transactionId": "tx-uuid-501", "meterValue": []}
    await consumer._store_meter_values(
        payload, raw_message='[2, "id", "MeterValues", {}]'
    )

    lookup.assert_awaited_once_with(consumer.charger, "tx-uuid-501")
    create_mock.assert_called_once()
    assert store.transactions[consumer.store_key] is created
