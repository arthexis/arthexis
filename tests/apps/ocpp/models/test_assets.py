from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from asgiref.sync import async_to_sync
from django.test import TestCase

from apps.ocpp.models import Charger
from tests.apps.ocpp.builders import charger, connection, station_model, transaction


class ChargerModelTests(TestCase):
    def setUp(self) -> None:
        self.v16 = station_model(model="One", protocol="ocpp1.6")
        self.v201 = station_model(model="Two", protocol="ocpp2.0.1")
        self.charger = charger("charger-1", station=self.v16)

    def test_charger_uses_identity_as_its_natural_key(self) -> None:
        self.assertEqual(self.charger.natural_key(), ("charger-1",))
        self.assertEqual(
            Charger.objects.get_by_natural_key("charger-1"),
            self.charger,
        )

    def test_charger_queryset_separates_enabled_connected_and_charging_state(
        self,
    ) -> None:
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

        self.assertQuerySetEqual(
            Charger.objects.enabled().order_by("identity"),
            [self.charger, connected_charging, connected_idle, connected_unresolved],
        )
        self.assertQuerySetEqual(Charger.objects.disabled(), [disabled])
        self.assertQuerySetEqual(
            Charger.objects.connected().order_by("identity"),
            [connected_charging, connected_idle, connected_unresolved],
        )
        self.assertQuerySetEqual(
            Charger.objects.disconnected().order_by("identity"),
            [self.charger, disabled],
        )
        self.assertQuerySetEqual(Charger.objects.charging(), [connected_charging])
        self.assertQuerySetEqual(Charger.objects.unresolved(), [connected_unresolved])
        self.assertQuerySetEqual(Charger.objects.idle(), [connected_idle])

    def test_historical_open_transactions_do_not_affect_fleet_state(self) -> None:
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

        self.assertNotIn(historical_only, Charger.objects.charging())
        self.assertNotIn(historical_only, Charger.objects.unresolved())
        self.assertIn(historical_only, Charger.objects.idle())

    def test_charging_requires_an_actual_active_transaction(self) -> None:
        connected_without_transactions = charger("charger-idle")
        connection(connected_without_transactions, channel_name="idle-channel")

        self.assertNotIn(self.charger, Charger.objects.charging())
        self.assertNotIn(connected_without_transactions, Charger.objects.charging())
        self.assertIn(connected_without_transactions, Charger.objects.idle())

    @patch(
        "apps.ocpp.transport.operations.request_explicit_operation",
        new_callable=AsyncMock,
    )
    def test_reset_uses_the_selected_charger_protocol(self, request) -> None:
        request.return_value = SimpleNamespace(action="Reset", unique_id="operation-1")

        operation = async_to_sync(Charger.reset)(self.charger)

        self.assertEqual(operation.action, "Reset")
        self.assertEqual(request.await_args.kwargs["charger"], self.charger)
        self.assertEqual(request.await_args.kwargs["payload"], {"type": "Soft"})

    @patch(
        "apps.ocpp.transport.operations.request_explicit_operation",
        new_callable=AsyncMock,
    )
    def test_start_uses_the_selected_charger_protocol(self, request) -> None:
        request.return_value = SimpleNamespace(
            action="RequestStartTransaction", unique_id="operation-2"
        )
        selected = charger("charger-201", station=self.v201)

        async_to_sync(Charger.start)(selected, id_token="member-2", evse=3)

        payload = request.await_args.kwargs["payload"]
        self.assertEqual(request.await_args.kwargs["action"], "RequestStartTransaction")
        self.assertEqual(payload["idToken"], {"idToken": "member-2"})
        self.assertEqual(payload["evseId"], 3)
        self.assertIsInstance(payload["remoteStartId"], int)

    @patch(
        "apps.ocpp.transport.operations.request_explicit_operation",
        new_callable=AsyncMock,
    )
    def test_stop_derives_the_sole_active_transaction(self, request) -> None:
        request.return_value = SimpleNamespace(
            action="RemoteStopTransaction", unique_id="operation-3"
        )
        active = transaction(
            self.charger,
            "transaction-active",
            started_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )

        async_to_sync(Charger.stop)(self.charger)

        self.assertEqual(request.await_args.kwargs["action"], "RemoteStopTransaction")
        self.assertEqual(
            request.await_args.kwargs["payload"], {"transactionId": active.pk}
        )

    @patch(
        "apps.ocpp.transport.operations.request_explicit_operation",
        new_callable=AsyncMock,
    )
    def test_v201_stop_uses_remote_transaction_identity(self, request) -> None:
        request.return_value = SimpleNamespace(
            action="RequestStopTransaction", unique_id="operation-201-stop"
        )
        selected = charger("charger-stop-201", station=self.v201)
        active = transaction(
            selected,
            "remote-transaction-201",
            started_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        )

        async_to_sync(Charger.stop)(selected)

        self.assertEqual(request.await_args.kwargs["action"], "RequestStopTransaction")
        self.assertEqual(
            request.await_args.kwargs["payload"],
            {"transactionId": active.remote_id},
        )

    def test_stop_ignores_historical_open_transactions(self) -> None:
        transaction(
            self.charger,
            "historical-open",
            started_at=datetime(2023, 1, 1, tzinfo=timezone.utc),
            historical=True,
        )

        with self.assertRaisesMessage(ValueError, "no active transaction"):
            async_to_sync(Charger.stop)(self.charger)

    def test_start_and_stop_reject_invalid_model_state(self) -> None:
        with self.assertRaisesMessage(ValueError, "non-empty id_token"):
            async_to_sync(Charger.start)(self.charger, id_token="")
        with self.assertRaisesMessage(ValueError, "no active transaction"):
            async_to_sync(Charger.stop)(self.charger)
