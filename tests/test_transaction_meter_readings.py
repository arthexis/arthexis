import os
import sys
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django

django.setup()

from django.test import TestCase
from django.utils import timezone

from ocpp.models import Charger, Transaction, MeterReading, MeterValue


pytestmark = pytest.mark.django_db


class TransactionKwTests(TestCase):
    def setUp(self):
        self.charger = Charger.objects.create(charger_id="TEST-CHARGER")
        start = timezone.now()
        self.transaction = Transaction.objects.create(
            charger=self.charger,
            start_time=start,
            stop_time=None,
            meter_start=5000,
            meter_stop=None,
        )

    def test_kw_uses_latest_reading_and_never_negative(self):
        base_time = self.transaction.start_time
        MeterValue.objects.create(
            charger=self.charger,
            transaction=self.transaction,
            timestamp=base_time + timedelta(minutes=1),
            energy=Decimal("6.500"),
        )
        self.assertEqual(self.transaction.kw, 1.5)

        MeterValue.objects.create(
            charger=self.charger,
            transaction=self.transaction,
            timestamp=base_time + timedelta(minutes=2),
            energy=Decimal("7.750"),
        )
        self.assertEqual(self.transaction.kw, 2.75)

        MeterValue.objects.create(
            charger=self.charger,
            transaction=self.transaction,
            timestamp=base_time + timedelta(minutes=3),
            energy=Decimal("4"),
        )
        self.assertEqual(self.transaction.kw, 0.0)

    def test_kw_returns_zero_without_meter_start_or_readings(self):
        start = timezone.now()
        transaction = Transaction.objects.create(
            charger=self.charger,
            start_time=start,
            stop_time=None,
        )
        self.assertEqual(transaction.kw, 0.0)

    def test_kw_prefetched_meter_values_used_without_meter_start_or_stop(self):
        start = timezone.now()
        transaction = Transaction.objects.create(
            charger=self.charger,
            start_time=start,
            stop_time=None,
        )

        first = MeterValue.objects.create(
            charger=self.charger,
            transaction=transaction,
            timestamp=start + timedelta(minutes=1),
            energy=Decimal("2.000"),
        )
        second = MeterValue.objects.create(
            charger=self.charger,
            transaction=transaction,
            timestamp=start + timedelta(minutes=2),
            energy=Decimal("5.500"),
        )

        transaction.prefetched_meter_values = [second, first]

        self.assertEqual(transaction.kw, 3.5)

    def test_kw_prefetched_objects_cache_used_when_available(self):
        start = timezone.now()
        transaction = Transaction.objects.create(
            charger=self.charger,
            start_time=start,
            stop_time=None,
        )

        first = MeterValue.objects.create(
            charger=self.charger,
            transaction=transaction,
            timestamp=start + timedelta(minutes=1),
            energy=Decimal("1.500"),
        )
        second = MeterValue.objects.create(
            charger=self.charger,
            transaction=transaction,
            timestamp=start + timedelta(minutes=5),
            energy=Decimal("4.000"),
        )

        prefetched = (
            Transaction.objects.filter(pk=transaction.pk)
            .prefetch_related("meter_values")
            .get()
        )

        self.assertEqual(prefetched.kw, 2.5)

    def test_kw_queryset_used_when_prefetched_data_missing(self):
        start = timezone.now()
        transaction = Transaction.objects.create(
            charger=self.charger,
            start_time=start,
            stop_time=None,
        )

        MeterValue.objects.create(
            charger=self.charger,
            transaction=transaction,
            timestamp=start + timedelta(minutes=3),
            energy=Decimal("10.000"),
        )
        MeterValue.objects.create(
            charger=self.charger,
            transaction=transaction,
            timestamp=start + timedelta(minutes=7),
            energy=Decimal("12.250"),
        )

        self.assertEqual(transaction.kw, 2.25)

    def test_kw_returns_zero_when_all_fallbacks_fail(self):
        start = timezone.now()
        transaction = Transaction.objects.create(
            charger=self.charger,
            start_time=start,
            stop_time=None,
        )

        transaction.prefetched_meter_values = []

        self.assertEqual(transaction.kw, 0.0)


class MeterReadingNormalizationTests(TestCase):
    def setUp(self):
        self.charger = Charger.objects.create(charger_id="NORMALIZE-CHARGER")
        start = timezone.now()
        self.transaction = Transaction.objects.create(
            charger=self.charger,
            start_time=start,
            stop_time=None,
        )

    def test_create_normalizes_value_and_strips_measurand(self):
        reading = MeterReading.objects.create(
            charger=self.charger,
            transaction=self.transaction,
            timestamp=self.transaction.start_time,
            value="1500",
            unit="Wh",
            measurand="Energy.Active.Import.Register",
        )
        reading.refresh_from_db()
        self.assertEqual(reading.energy, Decimal("1.5"))
        self.assertEqual(reading.value, Decimal("1.5"))
        self.assertNotIn("measurand", reading.__dict__)

    def test_get_or_create_normalizes_defaults(self):
        timestamp = self.transaction.start_time + timedelta(minutes=5)
        reading, created = MeterReading.objects.get_or_create(
            charger=self.charger,
            transaction=self.transaction,
            timestamp=timestamp,
            defaults={
                "value": Decimal("500"),
                "unit": "W",
                "measurand": "Power.Active.Import",
            },
        )
        self.assertTrue(created)
        reading.refresh_from_db()
        self.assertEqual(reading.energy, Decimal("0.5"))
        self.assertEqual(reading.value, Decimal("0.5"))
        self.assertNotIn("measurand", reading.__dict__)
