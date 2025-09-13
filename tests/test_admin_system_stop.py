import os
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from unittest.mock import patch
import time


class AdminSystemStopTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.superuser = User.objects.create_superuser(
            username="admin", email="admin@example.com", password="password"
        )
        self.staff = User.objects.create_user(
            username="staff",
            email="staff@example.com",
            password="password",
            is_staff=True,
        )

    def test_stop_button_hidden_for_non_superuser(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("admin:system"))
        self.assertNotContains(response, "Stop Server")

    def test_stop_requires_password(self):
        self.client.force_login(self.superuser)
        url = reverse("admin:system")
        with patch("core.system.subprocess.Popen") as popen:
            response = self.client.post(url, {"action": "stop", "password": "wrong"})
        self.assertEqual(popen.call_count, 1)
        self.assertContains(response, "Incorrect password")

    def test_stop_with_correct_password(self):
        self.client.force_login(self.superuser)
        lock_dir = Path(__file__).resolve().parent.parent / "locks"
        lock_dir.mkdir(exist_ok=True)
        lock_file = lock_dir / "charging.lck"
        lock_file.touch()
        old = time.time() - 700
        os.utime(lock_file, (old, old))
        url = reverse("admin:system")
        with patch("core.system.subprocess.Popen") as popen:
            response = self.client.post(url, {"action": "stop", "password": "password"})
        self.assertEqual(popen.call_count, 2)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("admin:index"))
        lock_file.unlink()
        if not any(lock_dir.iterdir()):
            lock_dir.rmdir()

    def test_stop_blocked_by_recent_lock(self):
        self.client.force_login(self.superuser)
        lock_dir = Path(__file__).resolve().parent.parent / "locks"
        lock_dir.mkdir(exist_ok=True)
        lock_file = lock_dir / "charging.lck"
        lock_file.touch()
        url = reverse("admin:system")
        with patch("core.system.subprocess.Popen") as popen:
            response = self.client.post(url, {"action": "stop", "password": "password"})
        self.assertEqual(popen.call_count, 1)
        self.assertContains(response, "Charging session in progress")
        lock_file.unlink()
        if not any(lock_dir.iterdir()):
            lock_dir.rmdir()
