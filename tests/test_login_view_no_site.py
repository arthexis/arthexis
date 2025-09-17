import os
import sys
from pathlib import Path

import django
from django.contrib.sites.models import Site
from django.test import TestCase
from django.urls import reverse

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

django.setup()


class LoginViewNoSiteTests(TestCase):
    def setUp(self):
        Site.objects.all().delete()

    def test_login_page_renders_without_site(self):
        response = self.client.get(reverse("pages:login"))
        self.assertEqual(response.status_code, 200)

    def test_admin_login_page_renders_without_site(self):
        response = self.client.get(reverse("admin:login"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="username"')
