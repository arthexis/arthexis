import json
from datetime import datetime, timezone
from io import StringIO

import pytest
from django.core.management import call_command

from apps.ocpp.models import OcppTransaction, ProtocolOperation
from tests.apps.ocpp.builders import charger, connection, connector, transaction

pytestmark = pytest.mark.django_db


@pytest.fixture
def recovery_context():
    selected = charger("field-recovery")
    connection(selected, channel_name="field-recovery-channel")
    connector(selected, number=1, status="Charging")
    session = transaction(
        selected,
        "stale-session",
        started_at=datetime(2026, 9, 23, 12, tzinfo=timezone.utc),
    )
    return selected, session


def test_inspection_is_charger_centric_and_read_only(recovery_context) -> None:
    selected, session = recovery_context
    ProtocolOperation.objects.create(
        charger=selected,
        version="ocpp1.6",
        direction=ProtocolOperation.Direction.CSMS_TO_CHARGE_POINT,
        action="RemoteStopTransaction",
        request_payload={"transactionId": "stale-session"},
        status=ProtocolOperation.Status.RECOVERY_REQUIRED,
        recovery_policy=ProtocolOperation.RecoveryPolicy.RECONCILE,
        attempt_count=1,
        last_delivery_error="connection lost before result",
    )
    output = StringIO()

    call_command("ocpp_recovery", charger=selected.identity, stdout=output)

    text = output.getvalue()
    for value in (
        "Charger: field-recovery",
        "State: charging",
        "Connectors: 1:Charging",
        "Current session: stale-session",
        "Ambiguous outbound operations: 1",
        "RemoteStopTransaction [reconcile] attempts=1",
        "connection lost before result",
        "Why: active session stale-session",
        "Waiting for: transaction end or newer charger evidence",
        "Last seen:",
        "Presence lease expires:",
        "Session last evidence:",
    ):
        assert value in text
    assert "transactionId" not in text

    session.refresh_from_db()
    assert session.recovery_state == OcppTransaction.RecoveryState.ACTIVE


def test_clear_stale_state_restores_idle_without_selecting_transaction(
    recovery_context,
) -> None:
    selected, session = recovery_context
    output = StringIO()

    call_command(
        "ocpp_recovery",
        charger=selected.identity,
        clear_stale_state=True,
        reason="verified idle at charger",
        stdout=output,
    )

    text = output.getvalue()
    for value in (
        "State: idle",
        "Connectors: 1:Charging",
        "Operator-cleared sessions: 1",
        "Last cleared session: stale-session",
        "Reason: verified idle at charger",
        "Cleared 1 stale live session(s)",
    ):
        assert value in text

    session.refresh_from_db()
    assert session.recovery_state == OcppTransaction.RecoveryState.CLEARED
    assert session.stopped_at is None


def test_clear_stale_state_is_safe_when_nothing_is_open(recovery_context) -> None:
    selected, session = recovery_context
    session.delete()
    output = StringIO()

    call_command(
        "ocpp_recovery",
        charger=selected.identity,
        clear_stale_state=True,
        stdout=output,
    )

    assert "Cleared 0 stale live session(s)" in output.getvalue()


def test_json_output_exposes_recovery_context(recovery_context) -> None:
    selected, _ = recovery_context
    output = StringIO()

    call_command(
        "ocpp_recovery",
        charger=selected.identity,
        clear_stale_state=True,
        reason="field reset",
        json_output=True,
        stdout=output,
    )

    payload = json.loads(output.getvalue())
    assert payload["identity"] == "field-recovery"
    assert payload["state"] == "idle"
    assert payload["cleared_sessions"] == 1
    assert payload["last_cleared_transaction_id"] == "stale-session"
    assert payload["last_recovery_clear_reason"] == "field reset"
    assert payload["cleared_session_count"] == 1
    assert (
        payload["state_reason"]
        == "live charger presence with no active or unresolved session"
    )
    assert payload["waiting_for"] is None
    assert payload["connection_last_seen_at"] is not None
    assert payload["connection_lease_expires_at"] is not None
