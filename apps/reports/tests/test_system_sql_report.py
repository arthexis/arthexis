from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
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

    def test_validate_sigils_reports_missing_tokens(self):
        self.client.force_login(self.user)
        url = reverse("admin:system-sql-report")

        response = self.client.post(
            url,
            {
                "name": "Missing sigils",
                "database_alias": "default",
                "query": "SELECT 1",
                "_validate_sigils": "1",
            },
        )

        messages = list(get_messages(response.wsgi_request))
        self.assertEqual(len(messages), 1)
        self.assertIn("No sigils were found", str(messages[0]))
        self.assertIsNone(response.context.get("query_result"))
        self.assertEqual(SQLReport.objects.count(), 0)

    def test_validate_sigils_reports_invalid_tokens(self):
        self.client.force_login(self.user)
        url = reverse("admin:system-sql-report")

        response = self.client.post(
            url,
            {
                "name": "Invalid sigils",
                "database_alias": "default",
                "query": "SELECT '[BAD.token]'",
                "_validate_sigils": "1",
            },
        )

        messages = list(get_messages(response.wsgi_request))
        self.assertEqual(len(messages), 1)
        self.assertIn("Invalid sigil", str(messages[0]))
        self.assertIsNone(response.context.get("query_result"))
        self.assertEqual(SQLReport.objects.count(), 0)

    def test_validate_sigils_reports_success(self):
        self.client.force_login(self.user)
        url = reverse("admin:system-sql-report")

        response = self.client.post(
            url,
            {
                "name": "Valid sigils",
                "database_alias": "default",
                "query": "SELECT '[CONF.DEBUG]'",
                "_validate_sigils": "1",
            },
        )

        messages = list(get_messages(response.wsgi_request))
        self.assertEqual(len(messages), 1)
        self.assertIn("All sigils are valid", str(messages[0]))
        self.assertIsNone(response.context.get("query_result"))
        self.assertEqual(SQLReport.objects.count(), 0)
