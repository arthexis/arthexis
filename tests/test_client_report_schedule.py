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

from core.models import ClientReportSchedule, EnergyAccount, RFID
from nodes.models import NetMessage, NodeRole
from ocpp.models import Charger, Transaction


pytestmark = [pytest.mark.django_db, pytest.mark.feature("rfid-scanner")]


class ClientReportScheduleRunTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.owner = User.objects.create_user(
            username="owner", email="owner@example.com", password="pwd"
        )
        self.charger = Charger.objects.create(charger_id="C1")
        self.rfid = RFID.objects.create(rfid="AA11BB22")
        self.account = EnergyAccount.objects.create(name="ACCOUNT")
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

        with patch("core.mailer.send") as mock_send:
            report = schedule.run()

        self.assertIsNotNone(report)
        self.assertEqual(report.schedule, schedule)
        export = report.data.get("export")
        self.assertIsNotNone(export)
        html_path = Path(settings.BASE_DIR) / export["html_path"]
        json_path = Path(settings.BASE_DIR) / export["json_path"]
        self.assertTrue(html_path.exists())
        self.assertTrue(json_path.exists())
        _args, kwargs = mock_send.call_args
        self.assertIn("attachments", kwargs)
        self.assertGreaterEqual(len(kwargs["attachments"]), 1)
        html_path.unlink()
        json_path.unlink()

    def test_schedule_run_notifies_on_failure(self):
        schedule = ClientReportSchedule.objects.create(
            owner=self.owner,
            created_by=self.owner,
            periodicity=ClientReportSchedule.PERIODICITY_DAILY,
            email_recipients=["dest@example.com"],
        )

        with patch("core.mailer.send", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                schedule.run()

        self.assertTrue(NetMessage.objects.exists())
        message = NetMessage.objects.latest("created")
        self.assertIn("Client report", message.subject)
