import os
import re
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse


class AdminDocsModelGroupsTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser(
            username="docs", email="docs@example.com", password="password"
        )
        self.client.force_login(self.user)

    def test_model_groups_ordered(self):
        response = self.client.get(reverse("django-admindocs-models-index"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        group_names = re.findall(r'<li><a href="#app-[^>]+">([^<]+)</a></li>', content)
        expected_numbered = [
            "1. Power",
            "2. Business",
            "3. Protocol",
            "4. Infrastructure",
            "5. Horologia",
            "6. Workgroup",
        ]
        self.assertEqual(group_names[: len(expected_numbered)], expected_numbered)
        self.assertIn("User Manuals", group_names)
