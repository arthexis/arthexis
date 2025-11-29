import os
import sys
from pathlib import Path
from datetime import date, timedelta

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

from accounts.models import ClientReport, ClientReportSchedule, CustomerAccount
from core.models import RFID
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

    def test_calculate_period_supports_multi_month_intervals(self):
        monthly_schedule = ClientReportSchedule(
            periodicity=ClientReportSchedule.PERIODICITY_MONTHLY
        )
        start, end = monthly_schedule.calculate_period(reference=date(2024, 7, 15))
        self.assertEqual(start, date(2024, 6, 1))
        self.assertEqual(end, date(2024, 6, 30))

        bimonthly_schedule = ClientReportSchedule(
            periodicity=ClientReportSchedule.PERIODICITY_BIMONTHLY
        )
        start, end = bimonthly_schedule.calculate_period(reference=date(2024, 8, 15))
        self.assertEqual(start, date(2024, 5, 1))
        self.assertEqual(end, date(2024, 6, 30))

        quarterly_schedule = ClientReportSchedule(
            periodicity=ClientReportSchedule.PERIODICITY_QUARTERLY
        )
        start, end = quarterly_schedule.calculate_period(reference=date(2024, 7, 15))
        self.assertEqual(start, date(2024, 4, 1))
        self.assertEqual(end, date(2024, 6, 30))

        yearly_schedule = ClientReportSchedule(
            periodicity=ClientReportSchedule.PERIODICITY_YEARLY
        )
        start, end = yearly_schedule.calculate_period(reference=date(2025, 2, 1))
        self.assertEqual(start, date(2024, 1, 1))
        self.assertEqual(end, date(2024, 12, 31))

    def test_advance_period_moves_through_multi_month_cycles(self):
        schedule = ClientReportSchedule(
            periodicity=ClientReportSchedule.PERIODICITY_BIMONTHLY
        )
        next_start, next_end = schedule._advance_period(
            date(2024, 5, 1), date(2024, 6, 30)
        )
        self.assertEqual(next_start, date(2024, 7, 1))
        self.assertEqual(next_end, date(2024, 8, 31))

        quarterly_schedule = ClientReportSchedule(
            periodicity=ClientReportSchedule.PERIODICITY_QUARTERLY
        )
        q_start, q_end = quarterly_schedule._advance_period(
            date(2024, 4, 1), date(2024, 6, 30)
        )
        self.assertEqual(q_start, date(2024, 7, 1))
        self.assertEqual(q_end, date(2024, 9, 30))

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
