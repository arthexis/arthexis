import json
import os
import sys
from pathlib import Path
from datetime import timedelta

import pytest

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import (
    RFID,
    ClientReport,
    EnergyAccount,
    ClientReportSchedule,
)
from ocpp.models import Charger, Transaction


pytestmark = [pytest.mark.django_db, pytest.mark.feature("rfid-scanner")]


class AdminClientReportTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser(
            username="admin", email="admin@example.com", password="pass"
        )
        self.client = Client()
        self.client.force_login(self.user)
        self.charger = Charger.objects.create(charger_id="C1")
        self.rfid1 = RFID.objects.create(rfid="A1B2C3")
        self.rfid2 = RFID.objects.create(rfid="D4E5F6")
        self.account = EnergyAccount.objects.create(name="ACCOUNT")
        self.account.rfids.add(self.rfid1)
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
            rfid=self.rfid2.rfid,
            start_time=start,
            stop_time=start + timedelta(hours=1),
            meter_start=0,
            meter_stop=500,
        )

    def test_generate_report_via_admin(self):
        day = timezone.now().date()
        url = reverse("admin:core_clientreport_generate")
        resp = self.client.post(
            url,
            {
                "period": "range",
                "start": day,
                "end": day,
                "recurrence": ClientReportSchedule.PERIODICITY_NONE,
            },
        )
        self.assertEqual(resp.status_code, 302)
        location = resp["Location"]
        self.assertIn("?download=", location)

        follow = self.client.get(location)
        self.assertContains(follow, "download it.")
        report = ClientReport.objects.get()
        self.assertEqual(report.start_date, day)
        self.assertEqual(report.end_date, day)
        self.assertEqual(report.owner, self.user)
        self.assertFalse(ClientReportSchedule.objects.exists())
        export = report.data.get("export")
        html_path = Path(settings.BASE_DIR) / export["html_path"]
        json_path = Path(settings.BASE_DIR) / export["json_path"]
        pdf_path = Path(settings.BASE_DIR) / export["pdf_path"]
        self.assertTrue(html_path.exists())
        self.assertTrue(json_path.exists())
        self.assertTrue(pdf_path.exists())
        self.assertEqual(report.data.get("schema"), "evcs-session/v1")
        evcs_entries = report.data.get("evcs", [])
        self.assertTrue(evcs_entries)
        transactions = [
            tx for evcs in evcs_entries for tx in evcs.get("transactions", [])
        ]
        self.assertTrue(
            any(tx.get("account_name") == self.account.name for tx in transactions)
        )
        self.assertTrue(
            any(tx.get("rfid_label") == str(self.rfid2.label_id) for tx in transactions)
        )

        pdf_response = self.client.get(
            reverse("admin:core_clientreport_download", args=[report.pk])
        )
        self.assertEqual(pdf_response.status_code, 200)
        self.assertEqual(pdf_response["Content-Type"], "application/pdf")
        pdf_bytes = b"".join(pdf_response.streaming_content)
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))

        with json_path.open(encoding="utf-8") as json_file:
            payload = json.load(json_file)

        self.assertEqual(payload.get("schema"), "evcs-session/v1")
        json_evcs = payload.get("evcs", [])
        flattened = [
            tx for evcs in json_evcs for tx in evcs.get("transactions", [])
        ]
        self.assertTrue(
            any(tx.get("rfid_label") == str(self.rfid2.label_id) for tx in flattened)
        )
        self.assertTrue(
            any(tx.get("account_name") == self.account.name for tx in flattened)
        )
        html_path.unlink()
        json_path.unlink()
        pdf_path.unlink()

    def test_generate_report_with_schedule(self):
        day = timezone.now().date()
        url = reverse("admin:core_clientreport_generate")
        resp = self.client.post(
            url,
            {
                "period": "range",
                "start": day,
                "end": day,
                "recurrence": ClientReportSchedule.PERIODICITY_WEEKLY,
                "destinations": "dest@example.com",
                "owner": self.user.pk,
                "enable_emails": "on",
            },
        )
        self.assertEqual(resp.status_code, 200)
        schedule = ClientReportSchedule.objects.get()
        self.assertEqual(schedule.periodicity, ClientReportSchedule.PERIODICITY_WEEKLY)
        self.assertIn("dest@example.com", schedule.email_recipients)
        report = ClientReport.objects.get()
        self.assertEqual(report.schedule, schedule)
        export = report.data.get("export")
        html_path = Path(settings.BASE_DIR) / export["html_path"]
        json_path = Path(settings.BASE_DIR) / export["json_path"]
        pdf_path = Path(settings.BASE_DIR) / export["pdf_path"]
        self.assertTrue(html_path.exists())
        self.assertTrue(json_path.exists())
        self.assertTrue(pdf_path.exists())
        html_path.unlink()
        json_path.unlink()
        pdf_path.unlink()
