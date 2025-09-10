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
from man.models import UserManual
from nodes.models import Node, NodeRole
from pages.models import Application, Module


class ManTests(TestCase):
    def setUp(self):
        UserManual.objects.create(
            slug="test-manual",
            title="Test Manual",
            description="Test description",
            content_html="<p>hi</p>",
            content_pdf="UEZERg==",
        )
        role, _ = NodeRole.objects.get_or_create(name="Terminal")
        Node.objects.update_or_create(
            mac_address=Node.get_current_mac(),
            defaults={
                "hostname": "localhost",
                "address": "127.0.0.1",
                "role": role,
            },
        )
        app, _ = Application.objects.get_or_create(name="man")
        module = Module.objects.create(node_role=role, application=app, path="/man/")
        module.create_landings()

    def test_manuals_link_in_docs(self):
        User = get_user_model()
        user = User.objects.create_superuser(
            username="docs", email="docs@example.com", password="password"
        )
        self.client.force_login(user)
        response = self.client.get(reverse("django-admindocs-docroot"))
        self.assertContains(response, reverse("man:list"))

    def test_manual_pill_rendered(self):
        response = self.client.get(reverse("man:list"))
        pill = 'badge rounded-pill text-bg-secondary">MAN</span>'
        self.assertContains(response, pill, count=1)
        self.assertNotContains(
            response, 'badge rounded-pill text-bg-secondary">USER_MANUALS</span>'
        )
        self.assertContains(response, "Test Manual")
        self.assertContains(response, "Test description")
