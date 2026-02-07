import pytest
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


pytestmark = pytest.mark.django_db


class DashboardRFIDAttemptTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.superuser = user_model.objects.create_superuser(
            username="admin",
            email="admin@example.com",
            password=None,
        )
        self.client.force_login(self.superuser)

    @pytest.mark.slow
    def test_rfid_attempts_model_appears_on_admin_dashboard(self):
        response = self.client.get(reverse("admin:index"))
        changelist_url = reverse("admin:cards_rfidattempt_changelist")

        self.assertContains(response, "RFID Attempts")
        self.assertContains(response, f'href="{changelist_url}"')
