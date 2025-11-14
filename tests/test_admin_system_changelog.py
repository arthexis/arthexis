import os
import sys
from datetime import datetime, timezone
from unittest import mock

sys.path.append(os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django

django.setup()

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.changelog import ChangelogCommit, ChangelogPage, ChangelogSection


class AdminChangelogReportTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.superuser = User.objects.create_superuser(
            username="admin", email="admin@example.com", password="password"
        )

    def _build_page(self, has_more=True, next_page=2):
        commit = ChangelogCommit(
            sha="abc1234deadbeef",
            summary="Fix telemetry pipeline",
            author="Alice",
            authored_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            commit_url="https://example.com/commit/abc1234",
        )
        section = ChangelogSection(
            slug="unreleased",
            title="Unreleased",
            commits=(commit,),
            is_unreleased=True,
        )
        return ChangelogPage(sections=(section,), next_page=next_page if has_more else None, has_more=has_more)

    @mock.patch("core.system.changelog.get_initial_page")
    def test_changelog_report_page_renders(self, get_initial_page):
        get_initial_page.return_value = self._build_page()
        self.client.force_login(self.superuser)

        response = self.client.get(reverse("admin:system-changelog-report"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Changelog Report")
        self.assertContains(response, "Fix telemetry pipeline")
        get_initial_page.assert_called_once_with()

    @mock.patch("core.system.render_to_string", return_value="<section>More</section>")
    @mock.patch("core.system.changelog.get_page")
    def test_changelog_data_endpoint_returns_html(self, get_page, render_to_string):
        get_page.return_value = self._build_page(has_more=False, next_page=None)
        self.client.force_login(self.superuser)

        url = reverse("admin:system-changelog-data")
        response = self.client.get(url, {"page": 1, "offset": 2})

        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(
            response.content,
            {"html": "<section>More</section>", "has_more": False, "next_page": None},
        )
        get_page.assert_called_once_with(1, per_page=1, offset=2)
        render_to_string.assert_called_once()

    def test_changelog_data_endpoint_requires_integer_page(self):
        self.client.force_login(self.superuser)
        response = self.client.get(reverse("admin:system-changelog-data"), {"page": "foo"})
        self.assertEqual(response.status_code, 400)
