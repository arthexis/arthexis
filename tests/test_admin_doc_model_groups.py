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

    def _get_group_section(self, content: str, group_name: str) -> str:
        pattern = re.compile(
            rf'<div class="module">\s*<h2 id="app-[^"]+">{re.escape(group_name)} \([^)]+\)</h2>(?P<body>.*?)</table>',
            re.S,
        )
        match = pattern.search(content)
        self.assertIsNotNone(
            match, f"{group_name} group should be present in admin docs"
        )
        return match.group("body")

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

    def test_selected_models_render_in_expected_groups(self):
        response = self.client.get(reverse("django-admindocs-models-index"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()

        business_section = self._get_group_section(content, "2. Business")
        protocol_section = self._get_group_section(content, "3. Protocol")
        workgroup_section = self._get_group_section(content, "6. Workgroup")

        location_link = reverse(
            "django-admindocs-models-detail",
            kwargs={"app_label": "core", "model_name": "location"},
        )
        rfid_link = reverse(
            "django-admindocs-models-detail",
            kwargs={"app_label": "core", "model_name": "rfid"},
        )
        package_link = reverse(
            "django-admindocs-models-detail",
            kwargs={"app_label": "core", "model_name": "package"},
        )
        package_release_link = reverse(
            "django-admindocs-models-detail",
            kwargs={"app_label": "core", "model_name": "packagerelease"},
        )
        todo_link = reverse(
            "django-admindocs-models-detail",
            kwargs={"app_label": "core", "model_name": "todo"},
        )

        self.assertIn(f'href="{location_link}"', business_section)
        self.assertNotIn(f'href="{location_link}"', protocol_section)

        self.assertIn(f'href="{rfid_link}"', protocol_section)
        self.assertNotIn(f'href="{rfid_link}"', business_section)

        for link in (package_link, package_release_link):
            self.assertIn(f'href="{link}"', workgroup_section)
            self.assertNotIn(f'href="{link}"', business_section)

        self.assertIn(f'href="{todo_link}"', workgroup_section)
        self.assertNotIn(f'href="{todo_link}"', business_section)
