import os
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from ocpp.models import CPLocation


class SaveAsCopyTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser(
            username="copyadmin", email="copy@example.com", password="password"
        )

    def test_save_as_copy_creates_new_instance(self):
        location = CPLocation.objects.create(
            name="Loc1",
            street="Main",
            number="1",
            municipality="Saltillo",
            state="CO",
            postal_code="25000",
        )
        self.client.force_login(self.user)
        url = reverse("admin:ocpp_cplocation_change", args=[location.pk])
        data = {
            "name": location.name,
            "street": location.street,
            "number": location.number,
            "municipality": location.municipality,
            "state": location.state,
            "postal_code": location.postal_code,
            "latitude": "",
            "longitude": "",
            "_saveacopy": "Save as a copy",
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(CPLocation.objects.count(), 2)
