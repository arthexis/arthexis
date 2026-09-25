from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from asgiref.sync import async_to_sync
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.cards.models import AuthorizationAttempt
from apps.ocpp.domain.snapshots import snapshot_charger
from apps.ocpp.models import InboundProtocolRequest, MeterValue, OcppTransaction
from apps.ocpp.protocol.contracts import ProtocolVersion
from apps.ocpp.protocol.correlation import PendingCalls
from apps.ocpp.protocol.frames import Call, CallResult
from apps.ocpp.protocol.v16.inbound import InboundActions
from apps.ocpp.transport.dispatch import FrameDispatcher
from tests.apps.ocpp.builders import charger
from tests.apps.ocpp.builders import connection as charger_connection

pytestmark = pytest.mark.django_db(transaction=True, reset_sequences=True)


@pytest.fixture
def backlog_context():
    cutover = datetime(2026, 9, 22, 12, tzinfo=timezone.utc)
    selected = charger(
        "multi-year-backlog",
        authority_cutover_at=cutover,
    )
    charger_connection(selected, channel_name="backlog-channel")
    dispatcher = FrameDispatcher(
        charger=selected,
        version=ProtocolVersion.OCPP_16,
        pending_calls=PendingCalls(),
        handler_resolver=InboundActions(selected).resolve,
    )
    return cutover, selected, dispatcher


def start(dispatcher, *, index: int, started_at: datetime, id_tag: str | None = None):
    response = async_to_sync(dispatcher.dispatch)(
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
    assert isinstance(response, CallResult)
    return response


def meter(
    dispatcher,
    *,
    index: int,
    transaction_id: int,
    sampled_at: datetime,
    value: int,
):
    response = async_to_sync(dispatcher.dispatch)(
        Call(
            unique_id=f"backlog-meter-{index}",
            action="MeterValues",
            payload={
                "transactionId": transaction_id,
                "meterValue": [
                    {
                        "timestamp": sampled_at.isoformat().replace("+00:00", "Z"),
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
    assert isinstance(response, CallResult)
    return response


def stop(
    dispatcher,
    *,
    index: int,
    transaction_id: int,
    stopped_at: datetime,
    meter_stop: int,
):
    response = async_to_sync(dispatcher.dispatch)(
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
    assert isinstance(response, CallResult)
    return response


def drain_one(dispatcher, *, index: int, started_at: datetime) -> int:
    started = start(dispatcher, index=index, started_at=started_at)
    transaction_id = started.payload["transactionId"]
    assert started.payload["idTagInfo"] == {"status": "Accepted"}

    metered = meter(
        dispatcher,
        index=index,
        transaction_id=transaction_id,
        sampled_at=started_at + timedelta(minutes=5),
        value=(index * 1000) + 125,
    )
    assert metered.payload == {}

    stopped = stop(
        dispatcher,
        index=index,
        transaction_id=transaction_id,
        stopped_at=started_at + timedelta(minutes=10),
        meter_stop=(index * 1000) + 150,
    )
    assert stopped.payload == {"idTagInfo": {"status": "Accepted"}}
    return transaction_id


def test_multi_year_backlog_drains_then_live_transaction_is_clean(backlog_context) -> None:
    cutover, selected, dispatcher = backlog_context
    selected.authorization_mode = selected.AuthorizationMode.RESTRICTED
    selected.save(update_fields=("authorization_mode",))

    first_started = datetime(2023, 1, 1, 8, tzinfo=timezone.utc)
    historical_ids = [
        drain_one(
            dispatcher,
            index=index,
            started_at=first_started + timedelta(days=index * 45),
        )
        for index in range(24)
    ]

    assert OcppTransaction.objects.historical().count() == 24
    assert OcppTransaction.objects.live().count() == 0
    assert MeterValue.objects.count() == 24
    assert not AuthorizationAttempt.objects.exists()

    first = OcppTransaction.objects.get(pk=historical_ids[0])
    last = OcppTransaction.objects.get(pk=historical_ids[-1])
    assert first.started_at == first_started
    assert last.stopped_at == first_started + timedelta(days=23 * 45, minutes=10)
    assert (
        OcppTransaction.objects.historical()
        .filter(pk__in=historical_ids, stopped_at__isnull=False)
        .count()
        == 24
    )

    backlog_snapshot = snapshot_charger(selected)
    assert backlog_snapshot.state == "idle"
    assert backlog_snapshot.active_transactions == 0
    assert backlog_snapshot.unresolved_sessions == 0
    assert backlog_snapshot.historical_sessions == 24
    assert backlog_snapshot.historical_open_sessions == 0

    at_cutover = async_to_sync(dispatcher.dispatch)(
        Call(
            unique_id="live-at-cutover",
            action="StartTransaction",
            payload={
                "connectorId": 1,
                "idTag": "not-currently-authorized",
                "meterStart": 50000,
                "timestamp": cutover.isoformat().replace("+00:00", "Z"),
            },
        )
    )
    assert at_cutover.payload == {"idTagInfo": {"status": "Invalid"}}
    assert AuthorizationAttempt.objects.count() == 1

    selected.authorization_mode = selected.AuthorizationMode.OPEN
    selected.save(update_fields=("authorization_mode",))
    live_started_at = cutover + timedelta(minutes=1)
    live = async_to_sync(dispatcher.dispatch)(
        Call(
            unique_id="post-cutover-live",
            action="StartTransaction",
            payload={
                "connectorId": 1,
                "idTag": "live-card",
                "meterStart": 60000,
                "timestamp": live_started_at.isoformat().replace("+00:00", "Z"),
            },
        )
    )

    assert live.payload["idTagInfo"] == {"status": "Accepted"}
    live_transaction = OcppTransaction.objects.get(pk=live.payload["transactionId"])
    assert not live_transaction.historical
    assert live_transaction.started_at == live_started_at

    final_snapshot = snapshot_charger(selected)
    assert final_snapshot.state == "charging"
    assert final_snapshot.active_transactions == 1
    assert final_snapshot.current_transaction_id == live_transaction.remote_id
    assert final_snapshot.historical_sessions == 24


def test_exact_backlog_replay_is_idempotent(backlog_context) -> None:
    _, selected, dispatcher = backlog_context
    started_at = datetime(2023, 4, 11, 10, tzinfo=timezone.utc)
    start_frame = Call(
        unique_id="historical-start-replay-acceptance",
        action="StartTransaction",
        payload={
            "connectorId": 1,
            "idTag": "legacy-card",
            "meterStart": 100,
            "timestamp": "2023-04-11T10:00:00Z",
        },
    )
    first_start = async_to_sync(dispatcher.dispatch)(start_frame)
    replay_start = async_to_sync(dispatcher.dispatch)(start_frame)
    assert replay_start == first_start
    transaction_id = first_start.payload["transactionId"]

    meter_frame = Call(
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
    first_meter = async_to_sync(dispatcher.dispatch)(meter_frame)
    replay_meter = async_to_sync(dispatcher.dispatch)(meter_frame)
    assert replay_meter == first_meter

    stop_frame = Call(
        unique_id="historical-stop-replay-acceptance",
        action="StopTransaction",
        payload={
            "transactionId": transaction_id,
            "meterStop": 150,
            "timestamp": "2023-04-11T10:10:00Z",
        },
    )
    first_stop = async_to_sync(dispatcher.dispatch)(stop_frame)
    replay_stop = async_to_sync(dispatcher.dispatch)(stop_frame)
    assert replay_stop == first_stop

    assert OcppTransaction.objects.count() == 1
    assert MeterValue.objects.count() == 1
    retained = OcppTransaction.objects.get()
    assert retained.historical
    assert retained.started_at == started_at
    assert retained.stopped_at == started_at + timedelta(minutes=10)
    assert (
        InboundProtocolRequest.objects.filter(
            charger=selected,
            action__in=("StartTransaction", "MeterValues", "StopTransaction"),
        ).count()
        == 3
    )


def test_secondary_meter_failure_does_not_stop_backlog_drain(backlog_context) -> None:
    _, _, dispatcher = backlog_context
    started = start(
        dispatcher,
        index=100,
        started_at=datetime(2024, 1, 1, 10, tzinfo=timezone.utc),
    )
    transaction_id = started.payload["transactionId"]

    with patch(
        "apps.ocpp.services.transactions.publish_safely",
        side_effect=RuntimeError("secondary processing unavailable"),
    ):
        metered = meter(
            dispatcher,
            index=100,
            transaction_id=transaction_id,
            sampled_at=datetime(2024, 1, 1, 10, 5, tzinfo=timezone.utc),
            value=100125,
        )

    assert metered.payload == {}
    retained = OcppTransaction.objects.get(pk=transaction_id)
    assert retained.historical
    assert retained.last_activity_at == datetime(
        2024, 1, 1, 10, 5, tzinfo=timezone.utc
    )
    assert MeterValue.objects.filter(transaction=retained).count() == 1

    stopped = stop(
        dispatcher,
        index=100,
        transaction_id=transaction_id,
        stopped_at=datetime(2024, 1, 1, 10, 10, tzinfo=timezone.utc),
        meter_stop=100150,
    )
    assert stopped.payload == {"idTagInfo": {"status": "Accepted"}}


def test_meter_database_work_stays_bounded_as_backlog_grows(backlog_context) -> None:
    _, _, dispatcher = backlog_context
    early_start = start(
        dispatcher,
        index=200,
        started_at=datetime(2023, 1, 1, 10, tzinfo=timezone.utc),
    )
    with CaptureQueriesContext(connection) as early_queries:
        meter(
            dispatcher,
            index=200,
            transaction_id=early_start.payload["transactionId"],
            sampled_at=datetime(2023, 1, 1, 10, 5, tzinfo=timezone.utc),
            value=200125,
        )

    for index in range(201, 221):
        drain_one(
            dispatcher,
            index=index,
            started_at=datetime(2023, 1, 2, 10, tzinfo=timezone.utc)
            + timedelta(days=index - 201),
        )

    late_start = start(
        dispatcher,
        index=221,
        started_at=datetime(2025, 1, 1, 10, tzinfo=timezone.utc),
    )
    with CaptureQueriesContext(connection) as late_queries:
        meter(
            dispatcher,
            index=221,
            transaction_id=late_start.payload["transactionId"],
            sampled_at=datetime(2025, 1, 1, 10, 5, tzinfo=timezone.utc),
            value=221125,
        )

    assert len(late_queries) <= len(early_queries) + 2
    assert len(late_queries) <= 20
