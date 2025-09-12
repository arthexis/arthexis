import os
import sys
from pathlib import Path
import socket

sys.path.append(str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from django.apps import apps
from django.conf import settings
from django.contrib.sites.models import Site
from django.core.management import call_command
from django.test import TestCase

from pages.models import Application, Module
from nodes.models import Node, NodeRole


class RegisterSiteAppsCommandTests(TestCase):
    def test_register_site_apps_creates_entries(self):
        Site.objects.all().delete()
        Site.objects.create(domain="zephyrus", name="Zephyrus")
        Application.objects.all().delete()
        Module.objects.all().delete()

        call_command("register_site_apps")

        site = Site.objects.get(domain="127.0.0.1")
        self.assertEqual(site.name, "Local")
        self.assertFalse(Site.objects.filter(domain="zephyrus").exists())

        node = Node.objects.get(hostname=socket.gethostname())
        self.assertFalse(node.enable_public_api)
        self.assertFalse(node.clipboard_polling)
        self.assertFalse(node.screenshot_polling)
        role = NodeRole.objects.get(name="Terminal")

        for label in settings.LOCAL_APPS:
            try:
                config = apps.get_app_config(label)
            except LookupError:
                continue
            self.assertTrue(Application.objects.filter(name=config.label).exists())
            app = Application.objects.get(name=config.label)
            self.assertTrue(
                Module.objects.filter(node_role=role, application=app).exists()
            )
