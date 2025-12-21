import pytest
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal

from django.utils import timezone

from apps.ocpp.models import Charger, Transaction, MeterValue
from apps.ocpp.transactions_io import export_transactions, import_transactions
from apps.ocpp.network import sync_transactions_payload


@pytest.fixture

def base_time():
    return timezone.make_aware(datetime(2024, 1, 1, 12, 0, 0))


@pytest.mark.django_db

def test_export_transactions_filters_and_serializes_decimals(base_time):
    charger = Charger.objects.create(charger_id="CP-1", connector_id=1, require_rfid=True)
    other = Charger.objects.create(charger_id="CP-2")

    tx = Transaction.objects.create(
        charger=charger,
        start_time=base_time,
        stop_time=base_time + timedelta(hours=1),
        meter_start=100,
        meter_stop=250,
        voltage_start=Decimal("230.500"),
        voltage_stop=Decimal("229.750"),
        current_import_start=Decimal("10.125"),
        current_import_stop=Decimal("11.000"),
    )
    MeterValue.objects.create(
        charger=charger,
        transaction=tx,
        connector_id=1,
        timestamp=base_time + timedelta(minutes=5),
        context="Sample.Periodic",
        energy=Decimal("100.500"),
        voltage=Decimal("230.500"),
        current_import=Decimal("10.125"),
    )

    # Transaction outside filter window should be excluded
    Transaction.objects.create(charger=other, start_time=base_time - timedelta(days=2))

    exported = export_transactions(
        start=base_time - timedelta(minutes=1),
        end=base_time + timedelta(minutes=1),
        chargers=["CP-1"],
    )

    assert exported["chargers"] == [
        {"charger_id": "CP-1", "connector_id": 1, "require_rfid": True}
    ]
    assert len(exported["transactions"]) == 1
    exported_tx = exported["transactions"][0]

    assert exported_tx["charger"] == "CP-1"
    assert exported_tx["start_time"] == base_time.astimezone(dt_timezone.utc).isoformat()
    assert exported_tx["meter_values"] == [
        {
            "connector_id": 1,
            "timestamp": (base_time + timedelta(minutes=5))
            .astimezone(dt_timezone.utc)
            .isoformat(),
            "context": "Sample.Periodic",
            "energy": "100.500",
            "voltage": "230.500",
            "current_import": "10.125",
            "current_offered": None,
            "temperature": None,
            "soc": None,
        }
    ]
    assert exported_tx["voltage_start"] == Decimal("230.500")
    assert exported_tx["voltage_stop"] == Decimal("229.750")
    assert exported_tx["current_import_start"] == Decimal("10.125")
    assert exported_tx["current_import_stop"] == Decimal("11.000")


@pytest.mark.django_db

def test_import_transactions_skips_invalid_entries_and_creates_meter_values(base_time):
    data = {
        "chargers": [
            {"charger_id": "<invalid>", "connector_id": "1"},
            {"charger_id": "CP-10", "connector_id": "2", "require_rfid": True},
        ],
        "transactions": [
            {  # invalid start time should be ignored
                "charger": "CP-10",
                "start_time": "not-a-date",
            },
            {
                "charger": "CP-10",
                "start_time": base_time.isoformat(),
                "stop_time": (base_time + timedelta(hours=1)).isoformat(),
                "connector_id": "2",
                "meter_values": [
                    {
                        "connector_id": "2",
                        "timestamp": (base_time + timedelta(minutes=10)).isoformat(),
                        "context": "Sample.Periodic",
                        "energy": "150.500",
                    }
                ],
            },
        ],
    }

    imported = import_transactions(data)

    assert imported == 1
    transaction = Transaction.objects.get()
    assert transaction.charger.connector_id == 2
    assert transaction.connector_id is None
    assert transaction.start_time == base_time
    assert transaction.stop_time == base_time + timedelta(hours=1)

    meter_value = MeterValue.objects.get()
    assert meter_value.connector_id == 2
    assert meter_value.energy == Decimal("150.500")
    assert meter_value.timestamp == base_time + timedelta(minutes=10)


@pytest.mark.django_db
def test_import_transactions_rejects_invalid_charger_ids(base_time):
    data = {
        "transactions": [
            {"charger": "", "start_time": base_time.isoformat()},
            {"charger": None, "start_time": base_time.isoformat()},
        ]
    }

    imported = import_transactions(data)

    assert imported == 0
    assert Transaction.objects.count() == 0


@pytest.mark.django_db
def test_import_transactions_skips_placeholder_serials(base_time):
    data = {
        "transactions": [
            {"charger": "<charger_id>", "start_time": base_time.isoformat()},
            {"charger": "<1234>", "start_time": base_time.isoformat()},
        ]
    }

    imported = import_transactions(data)

    assert imported == 0
    assert Transaction.objects.count() == 0


@pytest.mark.django_db
def test_import_transactions_skips_malformed_timestamps(base_time):
    data = {
        "transactions": [
            {"charger": "CP-1", "start_time": "bad"},
            {"charger": "CP-1", "start_time": base_time.isoformat(), "stop_time": "not-a-date"},
        ]
    }

    imported = import_transactions(data)

    assert imported == 0
    assert Transaction.objects.count() == 0


@pytest.mark.django_db
def test_import_transactions_persists_meter_values(base_time):
    data = {
        "chargers": [
            {"charger_id": "CP-20", "connector_id": "3", "require_rfid": True},
        ],
        "transactions": [
            {
                "charger": "CP-20",
                "start_time": base_time.isoformat(),
                "stop_time": (base_time + timedelta(minutes=30)).isoformat(),
                "meter_values": [
                    {
                        "connector_id": "3",
                        "timestamp": (base_time + timedelta(minutes=5)).isoformat(),
                        "context": "Sample.Periodic",
                        "energy": "12.5",
                        "current_import": "6.2",
                    },
                    {"connector_id": "x", "timestamp": "bad"},
                ],
            }
        ],
    }

    imported = import_transactions(data)

    assert imported == 1
    charger = Charger.objects.get(charger_id="CP-20")
    assert charger.charger_id == "CP-20"
    assert charger.connector_id == 3
    transaction = Transaction.objects.get()
    assert transaction.charger == charger
    assert transaction.start_time == base_time
    assert transaction.stop_time == base_time + timedelta(minutes=30)
    meter_value = MeterValue.objects.get()
    assert meter_value.connector_id == 3
    assert meter_value.timestamp == base_time + timedelta(minutes=5)
    assert meter_value.energy == Decimal("12.5")
    assert meter_value.current_import == Decimal("6.2")


@pytest.mark.django_db

def test_sync_transactions_payload_updates_and_skips_invalid_entries(base_time, monkeypatch):
    charger = Charger.objects.create(charger_id="SYNC-1", connector_id=1)

    payload = {
        "chargers": [
            {"charger_id": "SYNC-1", "connector_id": 1},
            {"charger_id": "UNKNOWN", "connector_id": 1},
        ],
        "transactions": [
            "not-a-mapping",
            {
                "charger": "SYNC-1",
                "connector_id": 1,
                "start_time": base_time.isoformat(),
                "stop_time": (base_time + timedelta(minutes=30)).isoformat(),
                "meter_values": [
                    {
                        "connector_id": 1,
                        "timestamp": (base_time + timedelta(minutes=5)).isoformat(),
                        "energy": "5",
                    }
                ],
            },
        ],
    }

    created = sync_transactions_payload(payload)

    assert created == 1
    transaction = Transaction.objects.get()
    assert transaction.charger == charger
    assert transaction.stop_time == base_time + timedelta(minutes=30)
    assert transaction.meter_values.count() == 1

    # Updating the same transaction should replace meter values
    updated_payload = {
        "chargers": payload["chargers"],
        "transactions": [
            {
                "charger": "SYNC-1",
                "connector_id": 1,
                "start_time": base_time.isoformat(),
                "stop_time": (base_time + timedelta(minutes=45)).isoformat(),
                "meter_values": [
                    {
                        "connector_id": "1",
                        "timestamp": (base_time + timedelta(minutes=15)).isoformat(),
                        "energy": "9",
                    },
                    {"malformed": True},
                ],
            }
        ],
    }

    updated = sync_transactions_payload(updated_payload)

    assert updated == 1
    transaction.refresh_from_db()
    assert transaction.stop_time == base_time + timedelta(minutes=45)
    meter_values = list(transaction.meter_values.order_by("timestamp"))
    assert len(meter_values) == 1
    assert meter_values[0].energy == Decimal("9")
    assert meter_values[0].connector_id == 1
