from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from asgiref.sync import async_to_sync

from apps.ocpp.models import Charger, OcppTransaction
from tests.apps.ocpp.builders import charger, connection, station_model, transaction

pytestmark = pytest.mark.django_db


@pytest.fixture
def charger_models():
    v16 = station_model(model="One", protocol="ocpp1.6")
    v201 = station_model(model="Two", protocol="ocpp2.0.1")
    selected = charger("charger-1", station=v16)
    return selected, v16, v201


def test_charger_uses_identity_as_its_natural_key(charger_models) -> None:
    selected, _, _ = charger_models

    assert selected.natural_key() == ("charger-1",)
    assert Charger.objects.get_by_natural_key("charger-1") == selected


def test_charger_queryset_separates_enabled_connected_and_charging_state(
    charger_models,
) -> None:
    selected, _, _ = charger_models
    disabled = charger("charger-disabled", active=False)
    connected_idle = charger("charger-idle")
    connected_charging = charger("charger-charging")
    connected_unresolved = charger("charger-unresolved")
    connection(connected_idle, channel_name="idle-channel", protocol="ocpp1.6")
    connection(
        connected_charging,
        channel_name="charging-channel",
        protocol="ocpp2.0.1",
    )
    connection(
        connected_unresolved,
        channel_name="unresolved-channel",
        protocol="ocpp1.6",
    )
    transaction(
        connected_charging,
        "active-transaction",
        started_at=datetime(2026, 9, 19, tzinfo=timezone.utc),
    )
    unresolved = transaction(
        connected_unresolved,
        "unresolved-transaction",
        started_at=datetime(2026, 9, 19, tzinfo=timezone.utc),
    )
    unresolved.recovery_state = unresolved.RecoveryState.UNRESOLVED
    unresolved.save(update_fields=("recovery_state",))

    assert list(Charger.objects.enabled().order_by("identity")) == [
        selected,
        connected_charging,
        connected_idle,
        connected_unresolved,
    ]
    assert list(Charger.objects.disabled()) == [disabled]
    assert list(Charger.objects.connected().order_by("identity")) == [
        connected_charging,
        connected_idle,
        connected_unresolved,
    ]
    assert list(Charger.objects.disconnected().order_by("identity")) == [
        selected,
        disabled,
    ]
    assert list(Charger.objects.charging()) == [connected_charging]
    assert list(Charger.objects.unresolved()) == [connected_unresolved]
    assert list(Charger.objects.idle()) == [connected_idle]


def test_historical_open_transactions_do_not_affect_fleet_state() -> None:
    historical_only = charger("historical-only")
    connection(historical_only, channel_name="historical-channel")
    historical = transaction(
        historical_only,
        "historical-open",
        started_at=datetime(2023, 1, 1, tzinfo=timezone.utc),
        historical=True,
    )
    historical.recovery_state = historical.RecoveryState.UNRESOLVED
    historical.save(update_fields=("recovery_state",))

    assert historical_only not in Charger.objects.charging()
    assert historical_only not in Charger.objects.unresolved()
    assert historical_only in Charger.objects.idle()


def test_charging_requires_an_actual_active_transaction(charger_models) -> None:
    selected, _, _ = charger_models
    connected_without_transactions = charger("charger-idle")
    connection(connected_without_transactions, channel_name="idle-channel")

    assert selected not in Charger.objects.charging()
    assert connected_without_transactions not in Charger.objects.charging()
    assert connected_without_transactions in Charger.objects.idle()


def test_reset_uses_the_selected_charger_protocol(charger_models) -> None:
    selected, _, _ = charger_models

    with patch(
        "apps.ocpp.transport.operations.request_explicit_operation",
        new_callable=AsyncMock,
    ) as request:
        request.return_value = SimpleNamespace(action="Reset", unique_id="operation-1")
        operation = async_to_sync(Charger.reset)(selected)

    assert operation.action == "Reset"
    assert request.await_args.kwargs["charger"] == selected
    assert request.await_args.kwargs["payload"] == {"type": "Soft"}


def test_start_uses_the_selected_charger_protocol(charger_models) -> None:
    _, _, v201 = charger_models
    selected = charger("charger-201", station=v201)

    with patch(
        "apps.ocpp.transport.operations.request_explicit_operation",
        new_callable=AsyncMock,
    ) as request:
        request.return_value = SimpleNamespace(
            action="RequestStartTransaction",
            unique_id="operation-2",
        )
        async_to_sync(Charger.start)(selected, id_token="member-2", evse=3)

    payload = request.await_args.kwargs["payload"]
    assert request.await_args.kwargs["action"] == "RequestStartTransaction"
    assert payload["idToken"] == {"idToken": "member-2"}
    assert payload["evseId"] == 3
    assert isinstance(payload["remoteStartId"], int)


def test_stop_derives_the_sole_active_transaction(charger_models) -> None:
    selected, _, _ = charger_models
    active = transaction(
        selected,
        "transaction-active",
        started_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )

    with patch(
        "apps.ocpp.transport.operations.request_explicit_operation",
        new_callable=AsyncMock,
    ) as request:
        request.return_value = SimpleNamespace(
            action="RemoteStopTransaction",
            unique_id="operation-3",
        )
        async_to_sync(Charger.stop)(selected)

    assert request.await_args.kwargs["action"] == "RemoteStopTransaction"
    assert request.await_args.kwargs["payload"] == {"transactionId": active.pk}


def test_v201_stop_uses_remote_transaction_identity(charger_models) -> None:
    _, _, v201 = charger_models
    selected = charger("charger-stop-201", station=v201)
    active = transaction(
        selected,
        "remote-transaction-201",
        started_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )

    with patch(
        "apps.ocpp.transport.operations.request_explicit_operation",
        new_callable=AsyncMock,
    ) as request:
        request.return_value = SimpleNamespace(
            action="RequestStopTransaction",
            unique_id="operation-201-stop",
        )
        async_to_sync(Charger.stop)(selected)

    assert request.await_args.kwargs["action"] == "RequestStopTransaction"
    assert request.await_args.kwargs["payload"] == {"transactionId": active.remote_id}


def test_stop_ignores_operator_cleared_open_transactions(charger_models) -> None:
    selected, _, _ = charger_models
    cleared = transaction(
        selected,
        "cleared-open",
        started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    cleared.recovery_state = OcppTransaction.RecoveryState.CLEARED
    cleared.save(update_fields=("recovery_state",))
    active = transaction(
        selected,
        "actual-active",
        started_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )

    with patch(
        "apps.ocpp.transport.operations.request_explicit_operation",
        new_callable=AsyncMock,
    ) as request:
        request.return_value = SimpleNamespace(
            action="RemoteStopTransaction",
            unique_id="operation-cleared",
        )
        async_to_sync(Charger.stop)(selected)

    assert request.await_args.kwargs["payload"] == {"transactionId": active.pk}


def test_stop_ignores_historical_open_transactions(charger_models) -> None:
    selected, _, _ = charger_models
    transaction(
        selected,
        "historical-open",
        started_at=datetime(2023, 1, 1, tzinfo=timezone.utc),
        historical=True,
    )

    with pytest.raises(ValueError, match="no active transaction"):
        async_to_sync(Charger.stop)(selected)


def test_start_and_stop_reject_invalid_model_state(charger_models) -> None:
    selected, _, _ = charger_models

    with pytest.raises(ValueError, match="non-empty id_token"):
        async_to_sync(Charger.start)(selected, id_token="")
    with pytest.raises(ValueError, match="no active transaction"):
        async_to_sync(Charger.stop)(selected)
