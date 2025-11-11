import os
import sys
from pathlib import Path
from datetime import timedelta

import pytest

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from django.test import TestCase
from django.utils import timezone
from django.conf import settings
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

from core.models import ClientReport, ClientReportSchedule, CustomerAccount, RFID
from nodes.models import NetMessage, NodeRole
from ocpp.models import Charger, Transaction
from core.tasks import ensure_recurring_client_reports


pytestmark = [pytest.mark.django_db, pytest.mark.feature("rfid-scanner")]


class ClientReportScheduleRunTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(
            username="owner", email="owner@example.com", password="pwd"
        )
        self.charger = Charger.objects.create(charger_id="C1")
        self.rfid = RFID.objects.create(rfid="AA11BB22")
        self.account = CustomerAccount.objects.create(name="ACCOUNT")
        self.account.rfids.add(self.rfid)
        start = timezone.now() - timedelta(days=2)
        Transaction.objects.create(
            charger=self.charger,
            rfid=self.rfid.rfid,
            account=self.account,
            start_time=start,
            stop_time=start + timedelta(hours=1),
            meter_start=0,
            meter_stop=1000,
        )
        NodeRole.objects.get_or_create(name="Terminal")

    def test_schedule_run_generates_report_and_sends_email(self):
        schedule = ClientReportSchedule.objects.create(
            owner=self.owner,
            created_by=self.owner,
            periodicity=ClientReportSchedule.PERIODICITY_DAILY,
            email_recipients=["dest@example.com"],
        )

        schedule.chargers.add(self.charger)

        with patch(
            "core.models.ClientReport.send_delivery", return_value=["dest@example.com"]
        ) as mock_send:
            report = schedule.run()

        self.assertIsNotNone(report)
        self.assertEqual(report.schedule, schedule)
        self.assertEqual(report.recipients, ["dest@example.com"])
        self.assertEqual(list(report.chargers.all()), [self.charger])
        export = report.data.get("export")
        self.assertIsNotNone(export)
        html_path = Path(settings.BASE_DIR) / export["html_path"]
        json_path = Path(settings.BASE_DIR) / export["json_path"]
        self.assertTrue(html_path.exists())
        self.assertTrue(json_path.exists())
        mock_send.assert_called_once()
        html_path.unlink()
        json_path.unlink()

    def test_schedule_run_notifies_on_failure(self):
        schedule = ClientReportSchedule.objects.create(
            owner=self.owner,
            created_by=self.owner,
            periodicity=ClientReportSchedule.PERIODICITY_DAILY,
            email_recipients=["dest@example.com"],
        )

        schedule.chargers.add(self.charger)

        with patch(
            "core.models.ClientReport.send_delivery", side_effect=RuntimeError("boom")
        ):
            with self.assertRaises(RuntimeError):
                schedule.run()

        self.assertTrue(NetMessage.objects.exists())
        message = NetMessage.objects.latest("created")
        self.assertIn("Client report", message.subject)

    def test_schedule_rejects_control_characters_in_title(self):
        with self.assertRaises(ValidationError):
            ClientReportSchedule.objects.create(
                owner=self.owner,
                created_by=self.owner,
                periodicity=ClientReportSchedule.PERIODICITY_DAILY,
                title="Problematic\nTitle",
            )

    def test_daily_task_generates_missing_reports(self):
        schedule = ClientReportSchedule.objects.create(
            owner=self.owner,
            created_by=self.owner,
            periodicity=ClientReportSchedule.PERIODICITY_DAILY,
            disable_emails=True,
        )
        schedule.chargers.add(self.charger)

        ensure_recurring_client_reports()

        self.assertEqual(ClientReport.objects.count(), 1)
        report = ClientReport.objects.get()
        self.assertEqual(report.schedule, schedule)
        self.assertEqual(list(report.chargers.all()), [self.charger])
        export = report.data.get("export") or {}
        for key in ("html_path", "json_path", "pdf_path"):
            candidate = export.get(key)
            if candidate:
                path = Path(settings.BASE_DIR) / candidate
                if path.exists():
                    path.unlink()
