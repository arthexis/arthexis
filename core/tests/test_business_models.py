"""Business model tests for core application."""

from datetime import time
from decimal import Decimal

from django.test import TestCase

from core.models import EnergyTariff


class EnergyTariffManagerTests(TestCase):
    """Validate EnergyTariff manager helpers."""

    def setUp(self):
        self.tariff = EnergyTariff.objects.create(
            year=2025,
            season=EnergyTariff.Season.ANNUAL,
            zone=EnergyTariff.Zone.ONE,
            contract_type=EnergyTariff.ContractType.DOMESTIC,
            period=EnergyTariff.Period.FLAT,
            unit=EnergyTariff.Unit.KWH,
            start_time=time(8, 0),
            end_time=time(17, 0),
            price_mxn=Decimal("1.2345"),
            cost_mxn=Decimal("0.9876"),
        )

    def test_get_by_natural_key_accepts_iso_strings(self):
        tariff = EnergyTariff.objects.get_by_natural_key(
            self.tariff.year,
            self.tariff.season,
            self.tariff.zone,
            self.tariff.contract_type,
            self.tariff.period,
            self.tariff.unit,
            self.tariff.start_time.isoformat(),
            self.tariff.end_time.isoformat(),
        )
        self.assertEqual(tariff, self.tariff)

    def test_get_by_natural_key_accepts_time_objects(self):
        tariff = EnergyTariff.objects.get_by_natural_key(
            self.tariff.year,
            self.tariff.season,
            self.tariff.zone,
            self.tariff.contract_type,
            self.tariff.period,
            self.tariff.unit,
            self.tariff.start_time,
            self.tariff.end_time,
        )
        self.assertEqual(tariff, self.tariff)
