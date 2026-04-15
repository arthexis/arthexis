"""Tests for conditional responses on view history traffic data endpoint."""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from apps.sites.models import ViewHistory


class ViewHistoryTrafficDataConditionalResponseTests(TestCase):
    """Validate ETag and 304 behavior for the traffic JSON endpoint."""

    def setUp(self):
        self.staff_user = get_user_model().objects.create_user(
            username="traffic-staff",
            email="traffic-staff@example.com",
            password="Password123",
            is_staff=True,
        )
        self.client.force_login(self.staff_user)
        ViewHistory.objects.create(
            path="/status/",
            method="GET",
            status_code=200,
            status_text="OK",
            view_name="status",
        )

    def test_traffic_data_returns_not_modified_for_matching_etag(self):
        response = self.client.get(reverse("admin:pages_viewhistory_traffic_data"))

        self.assertEqual(response.status_code, 200)
        self.assertIn("ETag", response.headers)
        self.assertIn("Last-Modified", response.headers)

        cached_response = self.client.get(
            reverse("admin:pages_viewhistory_traffic_data"),
            HTTP_IF_MODIFIED_SINCE=response.headers["Last-Modified"],
        )

        self.assertEqual(cached_response.status_code, 304)
