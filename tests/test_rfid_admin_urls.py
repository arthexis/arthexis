import os
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from django.test import SimpleTestCase
from django.urls import reverse


class RFIDAdminURLTests(SimpleTestCase):
    def test_reverse_write_accepts_numeric_pk(self):
        url = reverse("admin:accounts_rfid_write", args=[1])
        self.assertEqual(url, "/admin/accounts/rfid/1/write/")
