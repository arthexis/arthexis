import os
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from django.apps import apps
from django.conf import settings
from django.contrib.sites.models import Site
from django.core.management import call_command
from django.test import TestCase

from website.models import Application, SiteApplication


class RegisterSiteAppsCommandTests(TestCase):
    def test_register_site_apps_creates_entries(self):
        Site.objects.all().delete()
        Application.objects.all().delete()
        SiteApplication.objects.all().delete()

        call_command("register_site_apps")

        site = Site.objects.get(domain="127.0.0.1")
        self.assertEqual(site.name, "localhost")

        for label in settings.LOCAL_APPS:
            try:
                config = apps.get_app_config(label)
            except LookupError:
                continue
            self.assertTrue(Application.objects.filter(name=config.label).exists())
            app = Application.objects.get(name=config.label)
            self.assertTrue(site.site_applications.filter(application=app).exists())
