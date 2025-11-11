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
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext as _

from core.models import RFID, ClientReport, CustomerAccount, ClientReportSchedule
from ocpp.models import Charger, Transaction
from pages.views import ClientReportForm


pytestmark = [pytest.mark.django_db, pytest.mark.feature("rfid-scanner")]


class ClientReportGenerationTests(TestCase):
    def setUp(self):
        self.client = Client()
        cache.clear()
        User = get_user_model()
        self.user = User.objects.create_user(
            username="reporter", email="reporter@example.com", password="secret"
        )
        self.charger = Charger.objects.create(charger_id="C1")
        self.rfid1 = RFID.objects.create(rfid="A1B2C3")
        self.rfid2 = RFID.objects.create(rfid="D4E5F6")
        self.account = CustomerAccount.objects.create(name="ACCOUNT")
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

    def test_anonymous_post_rejected(self):
        day = timezone.now().date()
        url = reverse("pages:client-report")
        resp = self.client.post(
            url,
            {
                "period": "range",
                "start": day,
                "end": day,
                "recurrence": ClientReportSchedule.PERIODICITY_NONE,
                "chargers": [self.charger.pk],
                "language": "en",
                "view_mode": "expanded",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "log in</a> to generate consumer reports.")
        self.assertContains(resp, "You must log in to generate consumer reports.")
        self.assertFalse(ClientReport.objects.exists())

    def test_generate_report_authenticated(self):
        day = timezone.now().date()
        url = reverse("pages:client-report")
        self.client.force_login(self.user)
        resp = self.client.post(
            url,
            {
                "period": "range",
                "start": day,
                "end": day,
                "recurrence": ClientReportSchedule.PERIODICITY_NONE,
                "chargers": [self.charger.pk],
                "language": "en",
                "view_mode": "expanded",
            },
        )
        self.assertEqual(resp.status_code, 302)
        download_location = resp["Location"]
        self.assertIn("?download=", download_location)

        follow = self.client.get(download_location)
        self.assertContains(follow, "click here to download it.")
        report = ClientReport.objects.get()
        self.assertEqual(report.start_date, day)
        self.assertEqual(report.end_date, day)
        self.assertEqual(report.owner, self.user)
        self.assertEqual(list(report.chargers.all()), [self.charger])
        self.assertEqual(report.language, "en")
        self.assertEqual(report.title, "")
        export = report.data.get("export")
        self.assertIsNotNone(export)
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
        summary_rows = ClientReport.build_evcs_summary_rows(report.rows_for_display)
        self.assertTrue(summary_rows)
        self.assertTrue(all("transaction" in item for item in summary_rows))
        transaction_sessions = {
            tx.get("session_kwh") for tx in transactions if tx.get("session_kwh") is not None
        }
        summary_sessions = {
            item["transaction"].get("session_kwh") for item in summary_rows
        }
        self.assertTrue(summary_sessions.issubset(transaction_sessions))
        filters = report.data.get("filters", {})
        self.assertEqual(filters.get("chargers"), [self.charger.charger_id])
        pdf_response = self.client.get(
            reverse("pages:client-report-download", args=[report.pk])
        )
        self.assertEqual(pdf_response.status_code, 200)
        self.assertEqual(pdf_response["Content-Type"], "application/pdf")
        pdf_bytes = b"".join(pdf_response.streaming_content)
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))
        html_path.unlink()
        json_path.unlink()
        pdf_path.unlink()

    def test_repeated_generation_throttled(self):
        day = timezone.now().date()
        url = reverse("pages:client-report")
        payload = {
            "period": "range",
            "start": day,
            "end": day,
            "recurrence": ClientReportSchedule.PERIODICITY_NONE,
            "chargers": [self.charger.pk],
            "language": "en",
            "view_mode": "expanded",
        }
        self.client.force_login(self.user)

        first = self.client.post(url, payload)
        self.assertEqual(first.status_code, 302)
        report = ClientReport.objects.get()
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

        second = self.client.post(url, payload)
        self.assertEqual(second.status_code, 200)
        self.assertContains(
            second, "Consumer reports can only be generated periodically.", status_code=200
        )
        self.assertEqual(ClientReport.objects.count(), 1)

    def test_form_defaults_selects_all_chargers(self):
        extra = Charger.objects.create(charger_id="C2")
        form = ClientReportForm()
        initial_ids = list(form.fields["chargers"].initial or [])
        self.assertCountEqual(initial_ids, [self.charger.pk, extra.pk])

    def test_generate_rejects_control_characters_in_title(self):
        day = timezone.now().date()

        with self.assertRaises(ValidationError):
            ClientReport.generate(
                day,
                day,
                owner=self.user,
                chargers=[self.charger],
                language="en",
                title="Bad\rTitle",
            )

    def test_destinations_help_text_and_parser_alignment(self):
        form = ClientReportForm()
        self.assertEqual(
            form.fields["destinations"].help_text,
            _("Separate addresses with commas, whitespace, or new lines."),
        )

        today = timezone.now().date()
        bound_form = ClientReportForm(
            data={
                "period": "range",
                "start": today.isoformat(),
                "end": today.isoformat(),
                "recurrence": ClientReportSchedule.PERIODICITY_NONE,
                "destinations": (
                    "first@example.com second@example.com\n"
                    "third@example.com,\tfourth@example.com"
                ),
                "language": "en",
                "view_mode": "expanded",
            }
        )
        self.assertTrue(bound_form.is_valid())
        self.assertEqual(
            bound_form.cleaned_data["destinations"],
            [
                "first@example.com",
                "second@example.com",
                "third@example.com",
                "fourth@example.com",
            ],
        )
