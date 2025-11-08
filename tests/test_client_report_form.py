import datetime
import os
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django

django.setup()

from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, TestCase

from core.models import ClientReportSchedule

from pages.views import ClientReportForm


class ClientReportFormTests(TestCase):
    def setUp(self):
        super().setUp()
        self.factory = RequestFactory()

    def test_invalid_week_string_raises_validation_error(self):
        form = ClientReportForm(
            data={
                "period": "week",
                "week": "invalid-week",
                "recurrence": ClientReportSchedule.PERIODICITY_NONE,
                "language": "en",
                "view_mode": "expanded",
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn("Please select a week.", form.non_field_errors())

    def test_month_period_accepts_html_month_value(self):
        form = ClientReportForm(
            data={
                "period": "month",
                "month": "2023-09",
                "recurrence": ClientReportSchedule.PERIODICITY_NONE,
                "language": "en",
                "view_mode": "expanded",
            }
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["start"], datetime.date(2023, 9, 1))
        self.assertEqual(form.cleaned_data["end"], datetime.date(2023, 9, 30))

    def test_title_rejects_control_characters(self):
        form = ClientReportForm(
            data={
                "period": "range",
                "start": "2024-01-01",
                "end": "2024-01-31",
                "recurrence": ClientReportSchedule.PERIODICITY_NONE,
                "language": "en",
                "title": "Malicious\nSubject",
                "view_mode": "expanded",
            }
        )

        self.assertFalse(form.is_valid())
        self.assertIn(
            "Report title cannot contain control characters.",
            form.errors.get("title", []),
        )

    def test_language_choices_match_supported_languages(self):
        form = ClientReportForm()
        expected_choices = list(settings.LANGUAGES)
        self.assertEqual(form.fields["language"].choices, expected_choices)

    def test_default_language_matches_request_language(self):
        request = self.factory.get("/client-report/")
        request.user = AnonymousUser()
        request.LANGUAGE_CODE = "de-de"

        form = ClientReportForm(request=request)

        self.assertEqual(form.fields["language"].initial, "de")

    def test_view_mode_defaults_to_expanded(self):
        form = ClientReportForm()
        self.assertEqual(form.fields["view_mode"].initial, "expanded")
