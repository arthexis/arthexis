"""Business model tests for core application."""

from datetime import time, timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from django.core.exceptions import ValidationError

from accounts.models import EnergyTariff
from core.models import CountdownTimer
from pages.models import DeveloperArticle


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


class CountdownTimerManagerTests(TestCase):
    """Validate countdown timer publishing helpers."""

    def test_upcoming_excludes_unpublished(self):
        future_time = timezone.now() + timedelta(days=1)
        published = CountdownTimer.objects.create(
            title="Launch Party",
            scheduled_for=future_time,
            is_published=True,
        )
        CountdownTimer.objects.create(
            title="Soft Launch",
            scheduled_for=future_time + timedelta(hours=1),
            is_published=False,
        )

        upcoming = list(CountdownTimer.objects.upcoming())
        self.assertEqual(upcoming, [published])

    def test_visible_includes_completed(self):
        past_time = timezone.now() - timedelta(hours=1)
        timer = CountdownTimer.objects.create(
            title="Launch Party",
            scheduled_for=timezone.now() + timedelta(hours=1),
            is_published=True,
        )
        CountdownTimer.objects.filter(pk=timer.pk).update(
            scheduled_for=past_time
        )

        visible = list(CountdownTimer.objects.visible())
        self.assertEqual(visible, [CountdownTimer.objects.get(pk=timer.pk)])

    def test_rejects_unpublished_article_link(self):
        article = DeveloperArticle.objects.create(
            title="Sneak Peek",
            summary="Work in progress.",
            content="Details coming soon.",
            is_published=False,
        )

        with self.assertRaises(ValidationError):
            CountdownTimer.objects.create(
                title="Unpublished",
                scheduled_for=timezone.now() + timedelta(days=2),
                article=article,
                is_published=True,
            )

    def test_soft_delete_allows_past_schedules(self):
        timer = CountdownTimer.objects.create(
            title="Seeded",
            scheduled_for=timezone.now() + timedelta(hours=2),
            is_published=True,
            is_seed_data=True,
        )
        CountdownTimer.objects.filter(pk=timer.pk).update(
            scheduled_for=timezone.now() - timedelta(hours=2)
        )

        timer.delete()

        deleted = CountdownTimer.all_objects.get(pk=timer.pk)
        self.assertTrue(deleted.is_deleted)
