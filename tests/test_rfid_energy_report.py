import os
import sys
from pathlib import Path
from datetime import timedelta

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import RFID
from ocpp.models import Charger, Transaction


class RFIDEnergyReportTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser(
            username="report", email="report@example.com", password="pass"
        )
        self.client = Client()
        self.client.force_login(self.user)
        self.charger = Charger.objects.create(charger_id="C1")
        self.rfid1 = RFID.objects.create(rfid="AABBCCDD")
        self.rfid2 = RFID.objects.create(rfid="EEFF0011")
        start = timezone.now()
        Transaction.objects.create(
            charger=self.charger,
            rfid=self.rfid1.rfid,
            start_time=start,
            stop_time=start + timedelta(hours=1),
            meter_start=0,
            meter_stop=1000,
        )
        Transaction.objects.create(
            charger=self.charger,
            rfid=self.rfid1.rfid,
            start_time=start,
            stop_time=start + timedelta(hours=1),
            meter_start=1000,
            meter_stop=2500,
        )
        Transaction.objects.create(
            charger=self.charger,
            rfid=self.rfid2.rfid,
            start_time=start,
            stop_time=start + timedelta(hours=1),
            meter_start=0,
            meter_stop=500,
        )

    def test_report_view(self):
        url = reverse("admin:core_rfid_report")
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, self.rfid1.rfid)
        self.assertContains(resp, "2.50")
        self.assertContains(resp, self.rfid2.rfid)
        self.assertContains(resp, "0.50")
