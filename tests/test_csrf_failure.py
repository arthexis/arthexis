import os
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from django.test import Client, TestCase


class CSRFFailureViewTests(TestCase):
    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)

    def test_custom_csrf_failure_view(self):
        with self.assertLogs("pages.views", level="WARNING") as cm:
            response = self.client.post("/login/", {})
        self.assertEqual(response.status_code, 403)
        content = response.content.decode()
        self.assertIn("You will be redirected", content)
        self.assertIn('window.location.href = "/"', content)
        self.assertIn("CSRF", cm.output[0])
