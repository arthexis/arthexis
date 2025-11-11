import os
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from core.models import AdminHistory


class AdminHistoryTests(TestCase):
    def test_admin_history_records_filters(self):
        User = get_user_model()
        user = User.objects.create_superuser(
            username="histadmin", email="histadmin@example.com", password="password"
        )
        self.client.force_login(user)
        url = reverse("admin:core_customeraccount_changelist") + "?q=test"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        history = AdminHistory.objects.get(user=user)
        self.assertEqual(history.url, url)
        index = self.client.get(reverse("admin:index"))
        self.assertContains(index, url)
