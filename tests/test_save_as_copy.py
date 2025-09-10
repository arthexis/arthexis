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
from core.models import Address


class SaveAsCopyTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser(
            username="copyadmin", email="copy@example.com", password="password"
        )

    def test_save_as_copy_creates_new_instance(self):
        address = Address.objects.create(
            street="Main",
            number="1",
            municipality="Saltillo",
            state="CO",
            postal_code="25000",
        )
        self.client.force_login(self.user)
        url = reverse("admin:core_address_change", args=[address.pk])
        data = {
            "street": address.street,
            "number": address.number,
            "municipality": address.municipality,
            "state": address.state,
            "postal_code": address.postal_code,
            "_saveacopy": "Save as a copy",
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Address.objects.count(), 2)
