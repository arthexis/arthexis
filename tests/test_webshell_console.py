import os
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from django.test import TestCase
from django.contrib.auth import get_user_model


class AdminConsoleTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.user = User.objects.create_superuser(
            username="consoleadmin", email="consoleadmin@example.com", password="password"
        )
        self.client.force_login(self.user)

    def test_console_view_loads(self):
        response = self.client.get("/webshell/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "<textarea")

    def test_execute_endpoint(self):
        response = self.client.post("/webshell/execute/", {"source": "print(1+1)"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("2", response.content.decode())
