from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from ocpp.models import Charger, ChargingProfile


class ChargingProfileModelTests(TestCase):
    def setUp(self):
        self.charger = Charger.objects.create(charger_id="TEST-CP-1")

    def test_profile_normalizes_schedule_and_builds_payload(self):
        now = timezone.now()
        valid_to = now + timedelta(hours=2)
        start_schedule = now + timedelta(minutes=15)

        profile = ChargingProfile(
            charger=self.charger,
            connector_id=1,
            charging_profile_id=7,
            stack_level=2,
            purpose=ChargingProfile.Purpose.TX_PROFILE,
            kind=ChargingProfile.Kind.ABSOLUTE,
            transaction_id=42,
            valid_from=now,
            valid_to=valid_to,
            start_schedule=start_schedule,
            duration_seconds=1800,
            charging_rate_unit=ChargingProfile.RateUnit.AMP,
            charging_schedule_periods=[
                {"startPeriod": 0, "limit": "16", "numberPhases": 3},
                {"start_period": 600, "limit": Decimal("20.5")},
            ],
            min_charging_rate=Decimal("6.0"),
            description="Example profile",
        )

        profile.save()
        profile.refresh_from_db()

        self.assertEqual(
            profile.charging_schedule_periods,
            [
                {"start_period": 0, "limit": 16.0, "number_phases": 3},
                {"start_period": 600, "limit": 20.5},
            ],
        )

        request_payload = profile.as_set_charging_profile_request()
        self.assertEqual(request_payload["connectorId"], 1)

        cs_profile = request_payload["csChargingProfiles"]
        self.assertEqual(cs_profile["chargingProfileId"], 7)
        self.assertEqual(
            cs_profile["chargingProfilePurpose"], ChargingProfile.Purpose.TX_PROFILE
        )
        self.assertEqual(
            cs_profile["chargingProfileKind"], ChargingProfile.Kind.ABSOLUTE
        )

        schedule = cs_profile["chargingSchedule"]
        periods = schedule["chargingSchedulePeriod"]
        self.assertEqual(periods[0]["startPeriod"], 0)
        self.assertEqual(periods[0]["numberPhases"], 3)
        self.assertEqual(schedule.get("duration"), 1800)
        self.assertIn("startSchedule", schedule)
        self.assertEqual(schedule["minChargingRate"], 6.0)

    def test_recurrency_validation_requires_recurring_kind(self):
        profile = ChargingProfile(
            charger=self.charger,
            connector_id=0,
            charging_profile_id=1,
            stack_level=0,
            purpose=ChargingProfile.Purpose.CHARGE_POINT_MAX_PROFILE,
            kind=ChargingProfile.Kind.ABSOLUTE,
            recurrency_kind=ChargingProfile.RecurrencyKind.DAILY,
            charging_rate_unit=ChargingProfile.RateUnit.WATT,
            charging_schedule_periods=[{"startPeriod": 0, "limit": 5}],
        )

        with self.assertRaises(ValidationError) as excinfo:
            profile.full_clean()

        self.assertIn("recurrency_kind", excinfo.exception.message_dict)

    def test_tx_profile_requires_transaction_id(self):
        profile = ChargingProfile(
            charger=self.charger,
            connector_id=1,
            charging_profile_id=8,
            stack_level=1,
            purpose=ChargingProfile.Purpose.TX_PROFILE,
            kind=ChargingProfile.Kind.RELATIVE,
            charging_rate_unit=ChargingProfile.RateUnit.WATT,
            charging_schedule_periods=[{"startPeriod": 0, "limit": 5}],
        )

        with self.assertRaises(ValidationError) as excinfo:
            profile.full_clean()

        self.assertIn("transaction_id", excinfo.exception.message_dict)

    def test_clear_filter_matches_expected_fields(self):
        profile = ChargingProfile.objects.create(
            charger=self.charger,
            connector_id=2,
            charging_profile_id=3,
            stack_level=1,
            purpose=ChargingProfile.Purpose.TX_DEFAULT_PROFILE,
            kind=ChargingProfile.Kind.RECURRING,
            recurrency_kind=ChargingProfile.RecurrencyKind.WEEKLY,
            charging_rate_unit=ChargingProfile.RateUnit.AMP,
            charging_schedule_periods=[{"startPeriod": 0, "limit": 16}],
        )

        self.assertTrue(profile.matches_clear_filter(profile_id=3))
        self.assertTrue(profile.matches_clear_filter(connector_id=2, purpose=profile.purpose))
        self.assertFalse(profile.matches_clear_filter(connector_id=1))
        self.assertFalse(profile.matches_clear_filter(profile_id=9))
        self.assertFalse(profile.matches_clear_filter(stack_level=5))
