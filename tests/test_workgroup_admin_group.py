from django.test import TestCase
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.contrib.admin.sites import site

from teams.models import PowerLead


class WorkgroupAdminGroupTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser(
            username="biz-admin", password="pwd", email="admin@example.com"
        )
        self.client.force_login(self.admin)

    def test_powerlead_registered(self):
        registry = site._registry
        self.assertIn(PowerLead, registry)
        self.assertEqual(registry[PowerLead].model._meta.app_label, "teams")

    def test_admin_index_shows_powerlead(self):
        response = self.client.get(reverse("admin:index"))
        self.assertContains(response, "6. Workgroup MODELS")
        self.assertContains(response, "Power Leads")
