"""Tests for conditional responses on version info endpoint."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


class VersionInfoConditionalResponseTests(TestCase):
    """Ensure version info endpoint supports conditional requests."""

    def setUp(self):
        self.staff_user = get_user_model().objects.create_user(
            username="version-staff",
            email="version-staff@example.com",
            password="Password123",
            is_staff=True,
        )
        self.client.force_login(self.staff_user)

    def test_version_info_returns_not_modified_for_matching_etag(self):
        response = self.client.get(reverse("version-info"))

        self.assertEqual(response.status_code, 200)
        self.assertIn("ETag", response.headers)
        self.assertIn("Last-Modified", response.headers)

        cached_response = self.client.get(
            reverse("version-info"),
            HTTP_IF_NONE_MATCH=response.headers["ETag"],
        )

        self.assertEqual(cached_response.status_code, 304)
