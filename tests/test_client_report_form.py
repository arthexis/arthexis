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

    def test_valid_week_submission_populates_start_and_end(self):
        form = ClientReportForm(
            data={
                "period": "week",
                "week": "2024-W06",
                "recurrence": ClientReportSchedule.PERIODICITY_NONE,
            }
        )

        self.assertTrue(form.is_valid())
        self.assertEqual(
            form.cleaned_data["start"], datetime.date.fromisocalendar(2024, 6, 1)
        )
        self.assertEqual(
            form.cleaned_data["end"], datetime.date.fromisocalendar(2024, 6, 7)
        )

    def test_month_submission_sets_expected_range(self):
        form = ClientReportForm(
            data={
                "period": "month",
                "month": "2024-05",
                "recurrence": ClientReportSchedule.PERIODICITY_NONE,
            }
        )

        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["start"], datetime.date(2024, 5, 1))
        self.assertEqual(form.cleaned_data["end"], datetime.date(2024, 5, 31))
        self.assertEqual(form.cleaned_data["month"], datetime.date(2024, 5, 1))

    def test_range_submission_uses_provided_start_and_end(self):
        form = ClientReportForm(
            data={
                "period": "range",
                "start": "2024-03-01",
                "end": "2024-03-15",
                "recurrence": ClientReportSchedule.PERIODICITY_NONE,
            }
        )

        self.assertTrue(form.is_valid())
        self.assertEqual(form.cleaned_data["start"], datetime.date(2024, 3, 1))
        self.assertEqual(form.cleaned_data["end"], datetime.date(2024, 3, 15))
