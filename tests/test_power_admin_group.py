from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.contrib.admin.sites import site

from awg.models import EnergyTariff, PowerLead


class PowerAdminGroupTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username="biz-admin", password="pwd", email="admin@example.com"
        )
        self.client.force_login(self.admin)

    def test_powerlead_registered(self):
        registry = site._registry
        self.assertIn(PowerLead, registry)
        self.assertEqual(registry[PowerLead].model._meta.app_label, "awg")

    def test_energytariff_registered(self):
        registry = site._registry
        self.assertIn(EnergyTariff, registry)
        self.assertEqual(registry[EnergyTariff].model._meta.app_label, "awg")

    def test_admin_index_shows_powerlead(self):
        response = self.client.get(reverse("admin:index"))
        self.assertContains(response, "1. Power</a>")
        self.assertContains(response, "Power Leads")
        self.assertContains(response, "Energy Tariffs")
