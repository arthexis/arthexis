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
import re


class AdminIndexActionLinkTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser(
            username="indexadmin", email="indexadmin@example.com", password="password"
        )
        self.client.force_login(self.user)

    def test_custom_action_links_display(self):
        response = self.client.get(reverse("admin:index"))
        self.assertContains(response, "Scan RFIDs")
        self.assertContains(response, f'href="{reverse("admin:core_rfid_scan")}"')
        self.assertContains(response, "Register Visitor")
        self.assertContains(
            response, f'href="{reverse("admin:nodes_node_register_visitor")}"'
        )
        self.assertNotContains(response, "Build selected packages")
        self.assertNotContains(response, "Purge selected logs")
        content = response.content.decode()
        row_match = re.search(
            r'<tr class="model-packagerelease[^>]*>(.*?)</tr>', content, re.DOTALL
        )
        self.assertIsNotNone(row_match)
        row_html = row_match.group(1)
        # Actions column should still precede the Add and Change links
        pattern = re.compile(
            r'<td class="actions">.*?</td>\s*'
            r'<td>\s*<a[^>]*class="addlink"[^<]*</a>\s*</td>\s*'
            r'<td>\s*(?:<[^>]+>\s*)*<a[^>]*class="changelink"',
            re.DOTALL,
        )
        self.assertRegex(row_html, pattern)
