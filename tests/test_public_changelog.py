import os
import sys
from datetime import datetime, timezone
from unittest import mock

sys.path.append(os.path.dirname(os.path.dirname(__file__)))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django

django.setup()

from django.test import TestCase
from django.urls import reverse

from core.changelog import ChangelogCommit, ChangelogPage, ChangelogSection


class PublicChangelogViewTests(TestCase):
    def _build_page(self, has_more=True, next_page=2):
        commit = ChangelogCommit(
            sha="feedface1234567",
            summary="Add public changelog view",
            author="Bob",
            authored_at=datetime(2024, 2, 2, tzinfo=timezone.utc),
            commit_url=None,
        )
        section = ChangelogSection(
            slug="unreleased",
            title="Unreleased",
            commits=(commit,),
            is_unreleased=True,
        )
        return ChangelogPage(sections=(section,), next_page=next_page if has_more else None, has_more=has_more)

    @mock.patch("pages.views.changelog.get_initial_page")
    def test_public_changelog_renders(self, get_initial_page):
        get_initial_page.return_value = self._build_page()

        response = self.client.get(reverse("pages:changelog"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Add public changelog view")
        self.assertContains(response, "Changelog")
        get_initial_page.assert_called_once_with()

    @mock.patch("pages.views.loader.render_to_string", return_value="<section>Next</section>")
    @mock.patch("pages.views.changelog.get_page")
    def test_public_changelog_data(self, get_page, render_to_string):
        get_page.return_value = self._build_page(has_more=False, next_page=None)

        url = reverse("pages:changelog-data")
        response = self.client.get(url, {"page": 1, "offset": 2})

        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(
            response.content,
            {"html": "<section>Next</section>", "has_more": False, "next_page": None},
        )
        get_page.assert_called_once_with(1, per_page=1, offset=2)
        render_to_string.assert_called_once()

    def test_public_changelog_invalid_page(self):
        response = self.client.get(reverse("pages:changelog-data"), {"page": "bad"})
        self.assertEqual(response.status_code, 400)
