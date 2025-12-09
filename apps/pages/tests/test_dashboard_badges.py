from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import NoReverseMatch, reverse


class DashboardBadgeTests(TestCase):
    def setUp(self):
        self.user_model = get_user_model()
        self.superuser = self.user_model.objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password="password",
        )
        self.client.force_login(self.superuser)

    def test_dashboard_rows_do_not_include_model_badges(self):
        response = self.client.get(reverse("admin:index"))

        self.assertNotContains(response, "data-model-status-loader")

    def test_dashboard_model_status_endpoint_is_unavailable(self):
        with self.assertRaises(NoReverseMatch):
            reverse("admin:dashboard_model_status")
