import os
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from django.test import TestCase
from django.urls import reverse


class ReleaseChecklistTests(TestCase):
    def test_page_renders(self):
        response = self.client.get(reverse("pages:release-checklist"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Release Checklist")
