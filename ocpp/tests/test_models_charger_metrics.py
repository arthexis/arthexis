from __future__ import annotations

import sys
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tests.conftest  # noqa: F401

import django

django.setup()

from django.db import connection
from django.utils import timezone
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from ocpp import store
from ocpp.models import Charger, MeterValue, Transaction


class ChargerStoreKeyTests(TestCase):
    def test_store_keys_and_target_chargers_scope_to_connector_and_aggregate(self):
        base_serial = "SERIAL-001"
        aggregate = Charger.objects.create(charger_id=base_serial)
        connector_one = Charger.objects.create(
            charger_id=base_serial,
            connector_id=1,
        )
        connector_two = Charger.objects.create(
            charger_id=base_serial,
            connector_id=2,
        )

        self.assertEqual(
            aggregate._store_keys(),
            [
                store.identity_key(base_serial, None),
                store.pending_key(base_serial),
                base_serial,
            ],
        )
        self.assertEqual(
            connector_one._store_keys(),
            [
                store.identity_key(base_serial, connector_one.connector_id),
                store.identity_key(base_serial, None),
                store.pending_key(base_serial),
                base_serial,
            ],
        )

        aggregate_targets = set(aggregate._target_chargers())
        self.assertEqual(
            aggregate_targets, {aggregate, connector_one, connector_two}
        )

        self.assertEqual(list(connector_one._target_chargers()), [connector_one])
        self.assertEqual(list(connector_two._target_chargers()), [connector_two])


class ChargerEnergyTotalsTests(TestCase):
    def setUp(self):
        super().setUp()
        store.transactions.clear()
        self.addCleanup(store.transactions.clear)

    def test_total_kw_includes_store_transaction_and_range_filters(self):
        base_serial = "SERIAL-ENERGY"
        aggregate = Charger.objects.create(charger_id=base_serial)
        connector = Charger.objects.create(
            charger_id=base_serial,
            connector_id=1,
        )

        now = timezone.now()
        range_start = now - timedelta(hours=1)
        range_end = now + timedelta(minutes=15)

        Transaction.objects.create(
            charger=connector,
            connector_id=connector.connector_id,
            meter_start=1000,
            meter_stop=4000,
            start_time=range_start + timedelta(minutes=5),
            stop_time=range_start + timedelta(minutes=35),
        )
        Transaction.objects.create(
            charger=connector,
            connector_id=connector.connector_id,
            meter_start=5000,
            meter_stop=6000,
            start_time=range_start - timedelta(hours=2),
            stop_time=range_start - timedelta(hours=2) + timedelta(minutes=30),
        )

        active = Transaction.objects.create(
            charger=connector,
            connector_id=connector.connector_id,
            meter_start=7000,
            start_time=range_start + timedelta(minutes=10),
        )
        MeterValue.objects.create(
            charger=connector,
            connector_id=connector.connector_id,
            transaction=active,
            timestamp=now,
            energy=Decimal("7.6"),
        )

        store.set_transaction(connector.charger_id, connector.connector_id, active)

        expected_total = 4.6
        self.assertAlmostEqual(connector.total_kw, expected_total, places=6)
        self.assertAlmostEqual(aggregate.total_kw, expected_total, places=6)

        expected_range_total = 3.6
        self.assertAlmostEqual(
            connector.total_kw_for_range(range_start, range_end),
            expected_range_total,
            places=6,
        )
        self.assertAlmostEqual(
            aggregate.total_kw_for_range(range_start, range_end),
            expected_range_total,
            places=6,
        )

    def test_total_kw_for_range_query_count_constant(self):
        base_serial = "SERIAL-QUERY"
        connector = Charger.objects.create(
            charger_id=base_serial,
            connector_id=1,
        )

        now = timezone.now()
        range_start = now - timedelta(hours=2)
        range_end = now + timedelta(hours=2)

        def add_transaction(offset_minutes: int, energy: str) -> None:
            start = range_start + timedelta(minutes=offset_minutes)
            tx = Transaction.objects.create(
                charger=connector,
                connector_id=connector.connector_id,
                meter_start=0,
                start_time=start,
                stop_time=start + timedelta(minutes=30),
            )
            MeterValue.objects.create(
                charger=connector,
                connector_id=connector.connector_id,
                transaction=tx,
                timestamp=start + timedelta(minutes=10),
                energy=Decimal(energy),
            )

        add_transaction(5, "1.0")

        with CaptureQueriesContext(connection) as ctx_single:
            connector.total_kw_for_range(range_start, range_end)

        add_transaction(25, "3.5")

        with CaptureQueriesContext(connection) as ctx_multiple:
            connector.total_kw_for_range(range_start, range_end)

        self.assertEqual(len(ctx_multiple), len(ctx_single))
