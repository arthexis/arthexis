import os
import sys
from pathlib import Path
from datetime import timedelta

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import RFID, EnergyReport, EnergyAccount
from ocpp.models import Charger, Transaction


class EnergyReportGenerationTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.charger = Charger.objects.create(charger_id="C1")
        self.rfid1 = RFID.objects.create(rfid="A1B2C3")
        self.rfid2 = RFID.objects.create(rfid="D4E5F6")
        self.account = EnergyAccount.objects.create(name="ACCOUNT")
        self.account.rfids.add(self.rfid1)
        start = timezone.now()
        Transaction.objects.create(
            charger=self.charger,
            rfid=self.rfid1.rfid,
            account=self.account,
            start_time=start,
            stop_time=start + timedelta(hours=1),
            meter_start=0,
            meter_stop=1000,
        )
        Transaction.objects.create(
            charger=self.charger,
            rfid=self.rfid2.rfid,
            start_time=start,
            stop_time=start + timedelta(hours=1),
            meter_start=0,
            meter_stop=500,
        )

    def test_generate_report(self):
        day = timezone.now().date()
        url = reverse("pages:energy-report")
        resp = self.client.post(url, {"start": day, "end": day})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, self.account.name)
        self.assertContains(resp, str(self.rfid2.label_id))
        self.assertNotContains(resp, self.rfid1.rfid)
        self.assertNotContains(resp, self.rfid2.rfid)
        report = EnergyReport.objects.get()
        self.assertEqual(report.start_date, day)
        self.assertEqual(report.end_date, day)
        subjects = {row["subject"] for row in report.data["rows"]}
        self.assertIn(self.account.name, subjects)
        self.assertIn(str(self.rfid2.label_id), subjects)
