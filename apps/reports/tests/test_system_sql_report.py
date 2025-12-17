from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.test import TestCase

from apps.reports.models import SQLReport
from apps.sigils.models import SigilRoot


class SQLReportViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="admin", email="admin@example.com", password="pass"
        )
        SigilRoot.objects.get_or_create(
            prefix="CONF", context_type=SigilRoot.Context.CONFIG
        )

    def test_create_and_run_sql_report(self):
        self.client.force_login(self.user)
        url = reverse("admin:system-sql-report")

        user_table = get_user_model()._meta.db_table

        response = self.client.post(
            url,
            {
                "name": "Check users",
                "database_alias": "default",
                "query": f"SELECT username, '[CONF.DEBUG]' as debug FROM {user_table} ORDER BY id LIMIT 1",
            },
        )

        self.assertEqual(response.status_code, 200)
        report = SQLReport.objects.get(name="Check users")
        result = response.context["query_result"]
        self.assertIsNotNone(result["executed_at"])
        self.assertIsNone(result["error"])
        self.assertIsNotNone(report.last_run_at)
        self.assertIsNotNone(report.last_run_duration)
        self.assertEqual(result["row_count"], 1)
        self.assertEqual(result["columns"], ["username", "debug"])
        row = result["rows"][0]
        self.assertEqual(row[0], self.user.username)
        self.assertEqual(row[1], str(settings.DEBUG))

    def test_sql_report_changelist_links_to_runner_tool(self):
        self.client.force_login(self.user)
        url = reverse("admin:reports_sqlreport_changelist")

        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse("admin:system-sql-report"))
        self.assertContains(response, "Open SQL runner")

    def test_loading_existing_report_prefills_form(self):
        report = SQLReport.objects.create(
            name="Existing report", database_alias="default", query="SELECT 1"
        )
        self.client.force_login(self.user)
        url = reverse("admin:system-sql-report")

        response = self.client.get(url, {"report": report.pk})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["selected_report"].pk, report.pk)
        form = response.context["sql_report_form"]
        self.assertEqual(form.initial.get("name"), report.name)
        self.assertEqual(form.initial.get("database_alias"), report.database_alias)
        self.assertEqual(form.initial.get("query"), report.query)
        self.assertIsNone(response.context.get("query_result"))

    def test_validate_sigils_without_running_query(self):
        self.client.force_login(self.user)
        url = reverse("admin:system-sql-report")

        response = self.client.post(
            url,
            {
                "name": "Preview report",
                "database_alias": "default",
                "query": "SELECT '[CONF.DEBUG]' as debug",
                "validate_sigils": "1",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(SQLReport.objects.count(), 0)
        flashed = list(messages.get_messages(response.wsgi_request))
        self.assertTrue(
            any("All sigils resolved successfully" in str(message) for message in flashed)
        )
        self.assertIsNone(response.context.get("query_result"))

    def test_validate_sigils_with_no_tokens(self):
        self.client.force_login(self.user)
        url = reverse("admin:system-sql-report")

        response = self.client.post(
            url,
            {
                "name": "Preview report",
                "database_alias": "default",
                "query": "SELECT 1",
                "validate_sigils": "1",
            },
        )

        flashed = list(messages.get_messages(response.wsgi_request))
        self.assertTrue(any("No sigils found" in str(message) for message in flashed))
        self.assertEqual(SQLReport.objects.count(), 0)

    def test_validate_sigils_with_invalid_token(self):
        self.client.force_login(self.user)
        url = reverse("admin:system-sql-report")

        response = self.client.post(
            url,
            {
                "name": "Preview report",
                "database_alias": "default",
                "query": "SELECT '[MISSING.VALUE]'",
                "validate_sigils": "1",
            },
        )

        flashed = list(messages.get_messages(response.wsgi_request))
        self.assertTrue(any("Unable to resolve sigils" in str(message) for message in flashed))
        self.assertEqual(SQLReport.objects.count(), 0)
