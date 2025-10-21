import datetime
import os
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django

django.setup()

from django.test import SimpleTestCase

from core.models import ClientReportSchedule

from pages.views import ClientReportForm


class ClientReportFormTests(SimpleTestCase):
    def test_invalid_week_string_raises_validation_error(self):
        form = ClientReportForm(
            data={
                "period": "week",
                "week": "invalid-week",
                "recurrence": ClientReportSchedule.PERIODICITY_NONE,
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
            }
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["start"], datetime.date(2023, 9, 1))
        self.assertEqual(form.cleaned_data["end"], datetime.date(2023, 9, 30))
