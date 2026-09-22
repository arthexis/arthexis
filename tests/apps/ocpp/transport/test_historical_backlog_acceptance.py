from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from asgiref.sync import async_to_sync
from django.db import connection
from django.test import TransactionTestCase
from django.test.utils import CaptureQueriesContext

from apps.cards.models import AuthorizationAttempt
from apps.ocpp.domain.snapshots import snapshot_charger
from apps.ocpp.models import InboundProtocolRequest, MeterValue, OcppTransaction
from apps.ocpp.protocol.contracts import ProtocolVersion
from apps.ocpp.protocol.correlation import PendingCalls
from apps.ocpp.protocol.frames import Call, CallResult
from apps.ocpp.protocol.v16.inbound import InboundActions
from apps.ocpp.transport.dispatch import FrameDispatcher
from tests.apps.ocpp.builders import charger, connection as charger_connection


class HistoricalBacklogDrainAcceptanceTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self) -> None:
        self.cutover = datetime(2026, 9, 22, 12, tzinfo=timezone.utc)
        self.charger = charger(
            "multi-year-backlog",
            authority_cutover_at=self.cutover,
        )
        charger_connection(self.charger, channel_name="backlog-channel")
        self.dispatcher = FrameDispatcher(
            charger=self.charger,
            version=ProtocolVersion.OCPP_16,
            pending_calls=PendingCalls(),
            handler_resolver=InboundActions(self.charger).resolve,
        )

    def _start(
        self,
        *,
        index: int,
        started_at: datetime,
        id_tag: str | None = None,
    ) -> CallResult:
        response = async_to_sync(self.dispatcher.dispatch)(
            Call(
                unique_id=f"backlog-start-{index}",
                action="StartTransaction",
                payload={
                    "connectorId": 1,
                    "idTag": id_tag or f"legacy-{index}",
                    "meterStart": index * 1000,
                    "timestamp": started_at.isoformat().replace("+00:00", "Z"),
                },
            )
        )
        self.assertIsInstance(response, CallResult)
        return response

    def _meter(
        self,
        *,
        index: int,
        transaction_id: int,
        sampled_at: datetime,
        value: int,
    ) -> CallResult:
        response = async_to_sync(self.dispatcher.dispatch)(
            Call(
                unique_id=f"backlog-meter-{index}",
                action="MeterValues",
                payload={
                    "transactionId": transaction_id,
                    "meterValue": [
                        {
                            "timestamp": sampled_at.isoformat().replace(
                                "+00:00", "Z"
                            ),
                            "sampledValue": [
                                {
                                    "value": str(value),
                                    "measurand": "Energy.Active.Import.Register",
                                    "unit": "Wh",
                                }
                            ],
                        }
                    ],
                },
            )
        )
        self.assertIsInstance(response, CallResult)
        return response

    def _stop(
        self,
        *,
        index: int,
        transaction_id: int,
        stopped_at: datetime,
        meter_stop: int,
    ) -> CallResult:
        response = async_to_sync(self.dispatcher.dispatch)(
            Call(
                unique_id=f"backlog-stop-{index}",
                action="StopTransaction",
                payload={
                    "transactionId": transaction_id,
                    "meterStop": meter_stop,
                    "timestamp": stopped_at.isoformat().replace("+00:00", "Z"),
                },
            )
        )
        self.assertIsInstance(response, CallResult)
        return response

    def _drain_one(self, *, index: int, started_at: datetime) -> int:
        started = self._start(index=index, started_at=started_at)
        transaction_id = started.payload["transactionId"]
        self.assertEqual(started.payload["idTagInfo"], {"status": "Accepted"})

        metered = self._meter(
            index=index,
            transaction_id=transaction_id,
            sampled_at=started_at + timedelta(minutes=5),
            value=(index * 1000) + 125,
        )
        self.assertEqual(metered.payload, {})

        stopped = self._stop(
            index=index,
            transaction_id=transaction_id,
            stopped_at=started_at + timedelta(minutes=10),
            meter_stop=(index * 1000) + 150,
        )
        self.assertEqual(
            stopped.payload,
            {"idTagInfo": {"status": "Accepted"}},
        )
        return transaction_id

    def test_multi_year_backlog_drains_then_live_transaction_is_clean(self) -> None:
        self.charger.authorization_mode = self.charger.AuthorizationMode.RESTRICTED
        self.charger.save(update_fields=("authorization_mode",))

        first_started = datetime(2023, 1, 1, 8, tzinfo=timezone.utc)
        historical_ids: list[int] = []
        for index in range(24):
            historical_ids.append(
                self._drain_one(
                    index=index,
                    started_at=first_started + timedelta(days=index * 45),
                )
            )

        self.assertEqual(OcppTransaction.objects.historical().count(), 24)
        self.assertEqual(OcppTransaction.objects.live().count(), 0)
        self.assertEqual(MeterValue.objects.count(), 24)
        self.assertFalse(AuthorizationAttempt.objects.exists())

        first = OcppTransaction.objects.get(pk=historical_ids[0])
        last = OcppTransaction.objects.get(pk=historical_ids[-1])
        self.assertEqual(first.started_at, first_started)
        self.assertEqual(
            last.stopped_at,
            first_started + timedelta(days=23 * 45, minutes=10),
        )
        self.assertTrue(
            OcppTransaction.objects.historical()
            .filter(pk__in=historical_ids, stopped_at__isnull=False)
            .count()
            == 24
        )

        backlog_snapshot = snapshot_charger(self.charger)
        self.assertEqual(backlog_snapshot.state, "idle")
        self.assertEqual(backlog_snapshot.active_transactions, 0)
        self.assertEqual(backlog_snapshot.unresolved_sessions, 0)
        self.assertEqual(backlog_snapshot.historical_sessions, 24)
        self.assertEqual(backlog_snapshot.historical_open_sessions, 0)

        at_cutover = async_to_sync(self.dispatcher.dispatch)(
            Call(
                unique_id="live-at-cutover",
                action="StartTransaction",
                payload={
                    "connectorId": 1,
                    "idTag": "not-currently-authorized",
                    "meterStart": 50000,
                    "timestamp": self.cutover.isoformat().replace("+00:00", "Z"),
                },
            )
        )
        self.assertEqual(
            at_cutover.payload,
            {"idTagInfo": {"status": "Invalid"}},
        )
        self.assertEqual(AuthorizationAttempt.objects.count(), 1)

        self.charger.authorization_mode = self.charger.AuthorizationMode.OPEN
        self.charger.save(update_fields=("authorization_mode",))
        live_started_at = self.cutover + timedelta(minutes=1)
        live = async_to_sync(self.dispatcher.dispatch)(
            Call(
                unique_id="post-cutover-live",
                action="StartTransaction",
                payload={
                    "connectorId": 1,
                    "idTag": "live-card",
                    "meterStart": 60000,
                    "timestamp": live_started_at.isoformat().replace(
                        "+00:00", "Z"
                    ),
                },
            )
        )

        self.assertEqual(live.payload["idTagInfo"], {"status": "Accepted"})
        live_transaction = OcppTransaction.objects.get(
            pk=live.payload["transactionId"]
        )
        self.assertFalse(live_transaction.historical)
        self.assertEqual(live_transaction.started_at, live_started_at)

        final_snapshot = snapshot_charger(self.charger)
        self.assertEqual(final_snapshot.state, "charging")
        self.assertEqual(final_snapshot.active_transactions, 1)
        self.assertEqual(
            final_snapshot.current_transaction_id,
            live_transaction.remote_id,
        )
        self.assertEqual(final_snapshot.historical_sessions, 24)

    def test_exact_backlog_replay_is_idempotent(self) -> None:
        started_at = datetime(2023, 4, 11, 10, tzinfo=timezone.utc)
        start = Call(
            unique_id="historical-start-replay-acceptance",
            action="StartTransaction",
            payload={
                "connectorId": 1,
                "idTag": "legacy-card",
                "meterStart": 100,
                "timestamp": "2023-04-11T10:00:00Z",
            },
        )
        first_start = async_to_sync(self.dispatcher.dispatch)(start)
        replay_start = async_to_sync(self.dispatcher.dispatch)(start)
        self.assertEqual(replay_start, first_start)
        transaction_id = first_start.payload["transactionId"]

        meter = Call(
            unique_id="historical-meter-replay-acceptance",
            action="MeterValues",
            payload={
                "transactionId": transaction_id,
                "meterValue": [
                    {
                        "timestamp": "2023-04-11T10:05:00Z",
                        "sampledValue": [{"value": "125"}],
                    }
                ],
            },
        )
        first_meter = async_to_sync(self.dispatcher.dispatch)(meter)
        replay_meter = async_to_sync(self.dispatcher.dispatch)(meter)
        self.assertEqual(replay_meter, first_meter)

        stop = Call(
            unique_id="historical-stop-replay-acceptance",
            action="StopTransaction",
            payload={
                "transactionId": transaction_id,
                "meterStop": 150,
                "timestamp": "2023-04-11T10:10:00Z",
            },
        )
        first_stop = async_to_sync(self.dispatcher.dispatch)(stop)
        replay_stop = async_to_sync(self.dispatcher.dispatch)(stop)
        self.assertEqual(replay_stop, first_stop)

        self.assertEqual(OcppTransaction.objects.count(), 1)
        self.assertEqual(MeterValue.objects.count(), 1)
        selected = OcppTransaction.objects.get()
        self.assertTrue(selected.historical)
        self.assertEqual(selected.started_at, started_at)
        self.assertEqual(
            selected.stopped_at,
            started_at + timedelta(minutes=10),
        )
        self.assertEqual(
            InboundProtocolRequest.objects.filter(
                charger=self.charger,
                action__in=("StartTransaction", "MeterValues", "StopTransaction"),
            ).count(),
            3,
        )

    def test_secondary_meter_failure_does_not_stop_backlog_drain(self) -> None:
        started = self._start(
            index=100,
            started_at=datetime(2024, 1, 1, 10, tzinfo=timezone.utc),
        )
        transaction_id = started.payload["transactionId"]

        with patch(
            "apps.ocpp.services.transactions.publish_safely",
            side_effect=RuntimeError("secondary processing unavailable"),
        ):
            metered = self._meter(
                index=100,
                transaction_id=transaction_id,
                sampled_at=datetime(2024, 1, 1, 10, 5, tzinfo=timezone.utc),
                value=100125,
            )

        self.assertEqual(metered.payload, {})
        selected = OcppTransaction.objects.get(pk=transaction_id)
        self.assertTrue(selected.historical)
        self.assertEqual(
            selected.last_activity_at,
            datetime(2024, 1, 1, 10, 5, tzinfo=timezone.utc),
        )
        self.assertEqual(MeterValue.objects.filter(transaction=selected).count(), 1)

        stopped = self._stop(
            index=100,
            transaction_id=transaction_id,
            stopped_at=datetime(2024, 1, 1, 10, 10, tzinfo=timezone.utc),
            meter_stop=100150,
        )
        self.assertEqual(
            stopped.payload,
            {"idTagInfo": {"status": "Accepted"}},
        )

    def test_meter_database_work_stays_bounded_as_backlog_grows(self) -> None:
        early_start = self._start(
            index=200,
            started_at=datetime(2023, 1, 1, 10, tzinfo=timezone.utc),
        )
        with CaptureQueriesContext(connection) as early_queries:
            self._meter(
                index=200,
                transaction_id=early_start.payload["transactionId"],
                sampled_at=datetime(2023, 1, 1, 10, 5, tzinfo=timezone.utc),
                value=200125,
            )

        for index in range(201, 221):
            self._drain_one(
                index=index,
                started_at=datetime(2023, 1, 2, 10, tzinfo=timezone.utc)
                + timedelta(days=index - 201),
            )

        late_start = self._start(
            index=221,
            started_at=datetime(2025, 1, 1, 10, tzinfo=timezone.utc),
        )
        with CaptureQueriesContext(connection) as late_queries:
            self._meter(
                index=221,
                transaction_id=late_start.payload["transactionId"],
                sampled_at=datetime(2025, 1, 1, 10, 5, tzinfo=timezone.utc),
                value=221125,
            )

        self.assertLessEqual(len(late_queries), len(early_queries) + 2)
        self.assertLessEqual(len(late_queries), 20)
