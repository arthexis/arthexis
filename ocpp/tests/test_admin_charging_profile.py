from django.contrib import admin
from django.test import RequestFactory, TestCase

from ocpp.admin import ChargingProfileAdmin
from ocpp.models import Charger, ChargingProfile


class ChargingProfileAdminValidationTests(TestCase):
    def setUp(self):
        self.request = RequestFactory().get("/")
        self.admin = ChargingProfileAdmin(ChargingProfile, admin.site)

    def test_amp_schedules_allowed_for_watt_based_charger(self):
        charger = Charger.objects.create(
            charger_id="CP-AMP", energy_unit=Charger.EnergyUnit.W
        )

        self.assertTrue(
            self.admin._validate_units(
                self.request, charger, ChargingProfile.RateUnit.AMP
            )
        )
