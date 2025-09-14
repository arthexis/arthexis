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
from django.contrib.admin.models import LogEntry, ADDITION, CHANGE

from ocpp.models import Brand


class AdminObjectHistoryTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser(
            username="hist", email="hist@example.com", password="password"
        )
        self.client.force_login(self.user)

    def test_history_tracks_old_and_new_values(self):
        add_url = reverse("admin:ocpp_brand_add")
        self.client.post(
            add_url,
            {
                "name": "OldBrand",
                "wmi_codes-TOTAL_FORMS": "0",
                "wmi_codes-INITIAL_FORMS": "0",
                "wmi_codes-MIN_NUM_FORMS": "0",
                "wmi_codes-MAX_NUM_FORMS": "1000",
                "_save": "Save",
            },
        )
        brand = Brand.objects.get(name="OldBrand")
        log = LogEntry.objects.filter(
            object_id=str(brand.pk),
            content_type__model="brand",
            action_flag=ADDITION,
        ).last()
        self.assertIsNotNone(log)
        msg = log.get_change_message()
        self.assertIn("Added", msg)
        self.assertIn("name='OldBrand'", msg)

        change_url = reverse("admin:ocpp_brand_change", args=[brand.pk])
        self.client.post(
            change_url,
            {
                "name": "NewBrand",
                "wmi_codes-TOTAL_FORMS": "0",
                "wmi_codes-INITIAL_FORMS": "0",
                "wmi_codes-MIN_NUM_FORMS": "0",
                "wmi_codes-MAX_NUM_FORMS": "1000",
                "_save": "Save",
            },
        )
        log_change = LogEntry.objects.filter(
            object_id=str(brand.pk),
            content_type__model="brand",
            action_flag=CHANGE,
        ).last()
        self.assertIsNotNone(log_change)
        msg = log_change.get_change_message()
        self.assertIn("Changed", msg)
        self.assertIn("OldBrand", msg)
        self.assertIn("NewBrand", msg)
